from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict, cast

import voluptuous as vol
from aiohttp import ClientError, CookieJar
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

from .auth import MyQAuth, MyQLoginSession, tokens_to_data
from .client import MyQClient
from .const import (
    CONF_EMAIL,
    CONF_MFA_METHOD,
    DEFAULT_MFA_METHOD,
    DOMAIN,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
)
from .exceptions import (
    MyQCloudflareChallengeError,
    MyQError,
    MyQInvalidCredentialsError,
    MyQInvalidMfaError,
)
from .models import MyQConfigData, MyQConfigEntry, OAuthTokens

_LOGGER = logging.getLogger(__name__)


class CredentialsInput(TypedDict):
    email: str
    mfa_method: str
    password: str


class PasswordInput(TypedDict):
    mfa_method: str
    password: str


class MfaInput(TypedDict):
    code: str


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    tokens: OAuthTokens | None = None
    error: str | None = None


EMAIL_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.EMAIL,
        autocomplete="email",
    )
)
PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD,
        autocomplete="current-password",
    )
)
MFA_SELECTOR = selector.TextSelector(selector.TextSelectorConfig(autocomplete="one-time-code"))
MFA_METHOD_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[MFA_METHOD_EMAIL, MFA_METHOD_SMS],
        translation_key="mfa_method",
    )
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required("email"): EMAIL_SELECTOR,
        vol.Required("password"): PASSWORD_SELECTOR,
        vol.Required(CONF_MFA_METHOD, default=DEFAULT_MFA_METHOD): MFA_METHOD_SELECTOR,
    }
)
REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required("password"): PASSWORD_SELECTOR,
        vol.Required(CONF_MFA_METHOD, default=DEFAULT_MFA_METHOD): MFA_METHOD_SELECTOR,
    }
)
MFA_SCHEMA = vol.Schema({vol.Required("code"): MFA_SELECTOR})


class MyQConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._mfa_method: str | None = None
        self._login: MyQLoginSession | None = None
        self._reauth_entry: MyQConfigEntry | None = None

    async def async_step_user(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = cast(CredentialsInput, user_input)
            self._email = credentials["email"].strip().casefold()
            self._mfa_method = credentials[CONF_MFA_METHOD]
            self._async_abort_entries_match({CONF_EMAIL: self._email})
            attempt = await self._async_start_login(credentials["password"])
            if attempt.tokens is not None:
                return await self._async_finish(attempt.tokens)
            if attempt.error is None:
                return await self.async_step_mfa()
            errors["base"] = attempt.error

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def async_step_mfa(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        return await self._async_mfa_step("mfa", user_input)

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, object],
    ) -> ConfigFlowResult:
        del entry_data
        self._reauth_entry = cast(MyQConfigEntry, self._get_reauth_entry())
        data = cast(MyQConfigData, self._reauth_entry.data)
        self._email = data[CONF_EMAIL]
        self._mfa_method = data[CONF_MFA_METHOD]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            password_input = cast(PasswordInput, user_input)
            self._mfa_method = password_input[CONF_MFA_METHOD]
            attempt = await self._async_start_login(password_input["password"])
            if attempt.tokens is not None:
                return await self._async_finish(attempt.tokens)
            if attempt.error is None:
                return await self.async_step_reauth_mfa()
            errors["base"] = attempt.error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"email": self._required_email()},
        )

    async def async_step_reauth_mfa(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        return await self._async_mfa_step("reauth_mfa", user_input)

    async def _async_mfa_step(
        self,
        step_id: str,
        user_input: dict[str, object] | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            mfa_input = cast(MfaInput, user_input)
            code = mfa_input["code"].strip()
            if not re.fullmatch(r"\d{6}", code):
                errors["base"] = "invalid_mfa"
            else:
                attempt = await self._async_submit_mfa(code)
                if attempt.tokens is not None:
                    return await self._async_finish(attempt.tokens)
                errors["base"] = attempt.error or "unknown"

        return self.async_show_form(
            step_id=step_id,
            data_schema=MFA_SCHEMA,
            errors=errors,
            description_placeholders={"email": self._required_email()},
        )

    async def _async_start_login(self, password: str) -> LoginAttempt:
        await self._async_close_login()
        session = async_create_clientsession(
            self.hass,
            auto_cleanup=False,
            cookie_jar=CookieJar(),
        )
        self._login = MyQLoginSession(session)
        try:
            tokens = await self._login.async_start(
                self._required_email(),
                password,
                self._required_mfa_method(),
            )
        except MyQInvalidCredentialsError:
            await self._async_close_login()
            return LoginAttempt(error="invalid_auth")
        except MyQCloudflareChallengeError:
            await self._async_close_login()
            return LoginAttempt(error="cloudflare_challenge")
        except ClientError:
            await self._async_close_login()
            return LoginAttempt(error="cannot_connect")
        except MyQError:
            _LOGGER.exception("Unexpected MyQ error while starting authentication")
            await self._async_close_login()
            return LoginAttempt(error="unknown")
        return LoginAttempt(tokens=tokens)

    async def _async_submit_mfa(self, code: str) -> LoginAttempt:
        if self._login is None:
            return LoginAttempt(error="authentication_expired")
        try:
            tokens = await self._login.async_submit_mfa(code)
        except MyQInvalidMfaError:
            return LoginAttempt(error="invalid_mfa")
        except ClientError:
            return LoginAttempt(error="cannot_connect")
        except MyQError:
            _LOGGER.exception("Unexpected MyQ error while submitting MFA")
            await self._async_close_login()
            return LoginAttempt(error="unknown")
        return LoginAttempt(tokens=tokens)

    async def _async_finish(self, tokens: OAuthTokens) -> ConfigFlowResult:
        email_address = self._required_email()
        auth = MyQAuth(async_get_clientsession(self.hass), tokens, lambda _: None)
        client = MyQClient(async_get_clientsession(self.hass), auth)
        try:
            doors = await client.async_get_garage_doors()
        except ClientError:
            await self._async_close_login()
            return self.async_abort(reason="cannot_connect")
        except MyQError:
            _LOGGER.exception("Unexpected MyQ API error during account validation")
            await self._async_close_login()
            return self.async_abort(reason="unknown")
        await self._async_close_login()
        if not doors:
            return self.async_abort(reason="no_devices")

        data = MyQConfigData(
            email=email_address,
            mfa_method=self._required_mfa_method(),
            tokens=tokens_to_data(tokens),
        )
        if self._reauth_entry is not None:
            await self.async_set_unique_id(email_address)
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data=data,
            )

        await self.async_set_unique_id(email_address)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=email_address, data=data)

    async def _async_close_login(self) -> None:
        if self._login is None:
            return
        await self._login.async_close()
        self._login = None

    def _required_email(self) -> str:
        if self._email is None:
            raise RuntimeError("The MyQ config flow has no email address")
        return self._email

    def _required_mfa_method(self) -> str:
        if self._mfa_method is None:
            raise RuntimeError("The MyQ config flow has no MFA method")
        return self._mfa_method
