from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from aiohttp import ClientSession

from .const import (
    ANDROID_CERT_SHA1,
    ANDROID_PACKAGE,
    APP_VERSION,
    BRAND_ID,
    FIREBASE_API_KEY,
    FIREBASE_APP_ID,
    FIREBASE_DEBUG_TOKEN,
    FIREBASE_PROJECT_ID,
    IDENTITY_BASE_URL,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
    TOKEN_EXPIRY_MARGIN,
    USER_AGENT,
)
from .exceptions import (
    MyQApiError,
    MyQAuthenticationError,
    MyQCloudflareChallengeError,
    MyQInvalidCredentialsError,
    MyQInvalidMfaError,
)
from .models import OAuthTokens, StoredTokens

TokenListener = Callable[[OAuthTokens], None]


@dataclass(frozen=True, slots=True)
class ParsedForm:
    action: str
    fields: dict[str, str]
    email_field: str | None
    password_field: str | None
    otp_field: str | None


@dataclass(frozen=True, slots=True)
class HttpPage:
    url: str
    status: int
    location: str | None
    body: str


@dataclass(frozen=True, slots=True)
class MfaForm:
    page_url: str
    action: str
    fields: dict[str, str]
    otp_field: str


def tokens_to_data(tokens: OAuthTokens) -> StoredTokens:
    return StoredTokens(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.expires_at,
    )


def tokens_from_data(data: Mapping[str, object]) -> OAuthTokens:
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_at = data.get("expires_at")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise ValueError("Stored OAuth tokens are invalid")
    if not isinstance(expires_at, int | float):
        raise ValueError("Stored OAuth expiry is invalid")
    return OAuthTokens(access_token, refresh_token, float(expires_at))


class MyQAuth:
    def __init__(
        self,
        session: ClientSession,
        tokens: OAuthTokens,
        token_listener: TokenListener,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._token_listener = token_listener
        self._refresh_lock = asyncio.Lock()

    async def async_access_token(self) -> str:
        if self._tokens.expires_at - TOKEN_EXPIRY_MARGIN.total_seconds() > time.time():
            return self._tokens.access_token
        async with self._refresh_lock:
            if self._tokens.expires_at - TOKEN_EXPIRY_MARGIN.total_seconds() > time.time():
                return self._tokens.access_token
            return (await self.async_refresh()).access_token

    async def async_refresh(self) -> OAuthTokens:
        payload = await _post_json(
            self._session,
            f"{IDENTITY_BASE_URL}/connect/token",
            data={
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_SCOPE,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
            },
            headers=_token_headers(),
        )
        tokens = _oauth_tokens(payload, self._tokens.refresh_token)
        self._tokens = tokens
        self._token_listener(tokens)
        return tokens


class MyQLoginSession:
    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._verifier: str | None = None
        self._mfa_form: MfaForm | None = None

    async def async_start(
        self,
        email_address: str,
        password: str,
        mfa_method: str,
    ) -> OAuthTokens | None:
        authorization_url, verifier = _authorization_url()
        self._verifier = verifier
        page = await self._request_page(
            "GET",
            authorization_url,
            headers=_login_headers(),
        )
        authorization_code, page = await self._follow_redirects(page)
        if authorization_code is not None:
            return await self._async_exchange_code(authorization_code)
        _raise_for_challenge(page.body)

        form = _login_form(page)
        fields = dict(form.fields)
        fields[cast(str, form.email_field)] = email_address
        fields[cast(str, form.password_field)] = password
        submitted = await self._request_page(
            "POST",
            urllib.parse.urljoin(page.url, form.action),
            data=fields,
            headers=_login_headers(referer=page.url, form_post=True),
        )
        authorization_code, result = await self._follow_redirects(submitted)
        if authorization_code is not None:
            return await self._async_exchange_code(authorization_code)
        _raise_for_challenge(result.body)

        message = _validation_error(result.body)
        if message is not None:
            raise MyQInvalidCredentialsError(message)
        authorization_code, result = await self._select_mfa_method(result, mfa_method)
        if authorization_code is not None:
            return await self._async_exchange_code(authorization_code)
        _raise_for_challenge(result.body)
        self._set_mfa_form(result)
        return None

    async def async_submit_mfa(self, code: str) -> OAuthTokens:
        if self._mfa_form is None:
            raise MyQApiError("No active MyQ MFA challenge")
        fields = dict(self._mfa_form.fields)
        fields[self._mfa_form.otp_field] = code
        submitted = await self._request_page(
            "POST",
            self._mfa_form.action,
            data=fields,
            headers=_login_headers(
                referer=self._mfa_form.page_url,
                form_post=True,
            ),
        )
        authorization_code, result = await self._follow_redirects(submitted)
        authorization_code, result = await self._follow_consent(
            authorization_code,
            result,
        )
        if authorization_code is None:
            message = _validation_error(result.body)
            with suppress(MyQApiError):
                self._set_mfa_form(result)
            raise MyQInvalidMfaError(message or "MyQ rejected the MFA code")
        return await self._async_exchange_code(authorization_code)

    async def _async_exchange_code(self, code: str) -> OAuthTokens:
        if self._verifier is None:
            raise MyQApiError("The PKCE verifier is missing")
        app_check_token = await _mint_app_check_token(self._session)
        payload = await _post_json(
            self._session,
            f"{IDENTITY_BASE_URL}/connect/token",
            data={
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_SCOPE,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "code_verifier": self._verifier,
            },
            headers={
                **_token_headers(),
                "Firebase-AppCheck-Token": app_check_token,
            },
        )
        return _oauth_tokens(payload)

    async def _follow_redirects(self, page: HttpPage) -> tuple[str | None, HttpPage]:
        current = page
        for _ in range(12):
            if current.location is None:
                return None, current
            target = urllib.parse.urljoin(current.url, current.location)
            if target.startswith(OAUTH_REDIRECT_URI):
                return _redirect_code(target), current
            current = await self._request_page(
                "GET",
                target,
                headers=_login_headers(),
            )
        raise MyQApiError("Too many redirects while completing MyQ sign-in")

    async def _select_mfa_method(
        self,
        page: HttpPage,
        mfa_method: str,
    ) -> tuple[str | None, HttpPage]:
        server_method = {
            MFA_METHOD_EMAIL: "Email",
            MFA_METHOD_SMS: "Sms",
        }.get(mfa_method)
        if server_method is None:
            raise MyQApiError("Unsupported MyQ MFA method")

        form = _otp_form(page.body)
        selected_method = next(
            (
                value
                for name, value in form.fields.items()
                if name.casefold() == "selectedmfamethod"
            ),
            None,
        )
        if selected_method is not None and selected_method.casefold() == server_method.casefold():
            return None, page

        split_url = urllib.parse.urlsplit(page.url)
        query = [
            (name, value)
            for name, value in urllib.parse.parse_qsl(
                split_url.query,
                keep_blank_values=True,
            )
            if name.casefold() != "selectedmfamethod"
        ]
        query.append(("selectedMfaMethod", server_method))
        switch_url = urllib.parse.urlunsplit(
            (
                split_url.scheme,
                split_url.netloc,
                split_url.path,
                urllib.parse.urlencode(query),
                split_url.fragment,
            )
        )
        switched = await self._request_page(
            "GET",
            switch_url,
            headers=_login_headers(referer=page.url),
        )
        return await self._follow_redirects(switched)

    async def _follow_consent(
        self,
        authorization_code: str | None,
        page: HttpPage,
    ) -> tuple[str | None, HttpPage]:
        if authorization_code is not None:
            return authorization_code, page
        if urllib.parse.urlsplit(page.url).path.lower() != "/consent":
            return None, page

        form = _consent_form(page.body)
        post_url = urllib.parse.urljoin(page.url, form.action)
        consented = await self._request_page(
            "POST",
            post_url,
            data=form.fields,
            headers=_login_headers(referer=page.url, form_post=True),
        )
        if consented.status == 200:
            return_url = urllib.parse.parse_qs(urllib.parse.urlsplit(post_url).query).get(
                "returnUrl", [""]
            )[0]
            resumed_url = urllib.parse.urljoin(IDENTITY_BASE_URL, return_url)
            resumed = urllib.parse.urlsplit(resumed_url)
            identity = urllib.parse.urlsplit(IDENTITY_BASE_URL)
            if (
                resumed.scheme == identity.scheme
                and resumed.netloc == identity.netloc
                and resumed.path == "/connect/authorize/callback"
            ):
                consented = await self._request_page(
                    "GET",
                    resumed_url,
                    headers=_login_headers(referer=page.url),
                )
        return await self._follow_redirects(consented)

    async def _request_page(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpPage:
        async with self._session.request(
            method,
            url,
            data=data,
            headers=headers,
            allow_redirects=False,
        ) as response:
            return HttpPage(
                url=str(response.url),
                status=response.status,
                location=response.headers.get("Location"),
                body=await response.text(),
            )

    def _set_mfa_form(self, page: HttpPage) -> None:
        form = _otp_form(page.body)
        self._mfa_form = MfaForm(
            page_url=page.url,
            action=urllib.parse.urljoin(page.url, form.action),
            fields=form.fields,
            otp_field=cast(str, form.otp_field),
        )


async def _mint_app_check_token(session: ClientSession) -> str:
    endpoint = (
        "https://firebaseappcheck.googleapis.com/v1/projects/"
        f"{FIREBASE_PROJECT_ID}/apps/{FIREBASE_APP_ID}:exchangeDebugToken"
    )
    payload = await _post_json(
        session,
        endpoint,
        params={"key": FIREBASE_API_KEY},
        json_body={"debugToken": FIREBASE_DEBUG_TOKEN},
        headers={
            "X-Android-Package": ANDROID_PACKAGE,
            "X-Android-Cert": ANDROID_CERT_SHA1,
        },
    )
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise MyQApiError("Firebase App Check did not return a token")
    return token


async def _post_json(
    session: ClientSession,
    url: str,
    *,
    data: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    json_body: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    async with session.post(
        url,
        data=data,
        params=params,
        json=json_body,
        headers=headers,
    ) as response:
        body = await response.text()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise MyQApiError(
                f"MyQ returned HTTP {response.status} with an invalid JSON body"
            ) from error
        if not isinstance(parsed, dict):
            raise MyQApiError("MyQ returned an unexpected JSON response")
        payload = cast(dict[str, object], parsed)
        if response.status < 400:
            return payload
        error_code = payload.get("code") or payload.get("error")
        if response.status in {400, 401, 403}:
            raise MyQAuthenticationError(str(error_code or response.status))
        raise MyQApiError(f"MyQ request failed with HTTP {response.status}")


def _oauth_tokens(
    payload: Mapping[str, object],
    existing_refresh_token: str | None = None,
) -> OAuthTokens:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token", existing_refresh_token)
    expires_in = payload.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise MyQApiError("MyQ returned an incomplete OAuth token response")
    if not isinstance(expires_in, int | float):
        raise MyQApiError("MyQ returned an invalid OAuth expiry")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=time.time() + float(expires_in),
    )


def _authorization_url() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    query = urllib.parse.urlencode(
        {
            "acr_values": "unified_flow:v1 brand:myq",
            "client_id": OAUTH_CLIENT_ID,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "ui_locales": "en-US",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": OAUTH_SCOPE,
            "prompt": "login",
        }
    )
    return f"{IDENTITY_BASE_URL}/connect/authorize?{query}", verifier


def _token_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "App-Version": APP_VERSION,
        "BrandId": BRAND_ID,
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
    }


def _login_headers(
    *,
    referer: str | None = None,
    form_post: bool = False,
) -> dict[str, str]:
    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 11; sdk_gphone_x86) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/83.0.4103.106 Mobile Safari/537.36"
        ),
        "Upgrade-Insecure-Requests": "1",
    }
    if referer is not None:
        headers["Referer"] = referer
    if form_post:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Origin"] = IDENTITY_BASE_URL
    return headers


def _attribute(tag: str, name: str) -> str | None:
    pattern = re.compile(
        rf"\b{re.escape(name)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        re.IGNORECASE,
    )
    match = pattern.search(tag)
    if match is None:
        return None
    value = next(group for group in match.groups() if group is not None)
    return html.unescape(value)


def _parse_forms(page_html: str) -> list[ParsedForm]:
    forms: list[ParsedForm] = []
    for form_match in re.finditer(
        r"(<form\b[^>]*>)(.*?)</form>",
        page_html,
        re.IGNORECASE | re.DOTALL,
    ):
        form_tag = form_match.group(1)
        form_body = form_match.group(2)
        action = _attribute(form_tag, "action") or ""
        fields: dict[str, str] = {}
        email_field: str | None = None
        password_field: str | None = None
        otp_field: str | None = None
        visible_fields: list[str] = []

        for input_match in re.finditer(r"<input\b[^>]*>", form_body, re.IGNORECASE):
            input_tag = input_match.group(0)
            name = _attribute(input_tag, "name")
            if name is None or re.search(r"\bdisabled\b", input_tag, re.IGNORECASE):
                continue
            field_type = (_attribute(input_tag, "type") or "text").lower()
            if field_type in {"button", "image", "reset", "submit"}:
                continue
            fields[name] = _attribute(input_tag, "value") or ""
            identity = " ".join(
                (
                    name,
                    _attribute(input_tag, "id") or "",
                    _attribute(input_tag, "autocomplete") or "",
                )
            ).lower()
            if field_type == "email" or "email" in identity:
                email_field = name
            if field_type == "password":
                password_field = name
            if (
                "otp" in identity
                or "one-time-code" in identity
                or re.search(
                    r"(^|\W)(verification|security)[_-]?code($|\W)",
                    identity,
                )
            ):
                otp_field = name
            if field_type in {"number", "tel", "text"}:
                visible_fields.append(name)

        if otp_field is None and "verifyotp" in action.lower():
            otp_field = next(
                (name for name in fields if "otp" in name.lower() or name.lower().endswith("code")),
                visible_fields[0] if len(visible_fields) == 1 else None,
            )
        forms.append(ParsedForm(action, fields, email_field, password_field, otp_field))
    return forms


def _login_form(page: HttpPage) -> ParsedForm:
    form = next(
        (candidate for candidate in _parse_forms(page.body) if candidate.password_field),
        None,
    )
    if form is None or form.email_field is None or not form.action:
        raise MyQApiError(f"The MyQ sign-in form was not found ({_page_summary(page)})")
    return form


def _page_summary(page: HttpPage) -> str:
    path = urllib.parse.urlsplit(page.url).path or "/"
    title_match = re.search(
        r"<title\b[^>]*>(.*?)</title>",
        page.body,
        re.IGNORECASE | re.DOTALL,
    )
    title = _plain_text(title_match.group(1)) if title_match else ""
    content = _plain_text(page.body)
    parts = [f"HTTP {page.status} at {path}"]
    if title:
        parts.append(f"title={title[:120]!r}")
    if content:
        parts.append(f"content={content[:240]!r}")
    return ", ".join(parts)


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _otp_form(page_html: str) -> ParsedForm:
    form = next(
        (candidate for candidate in _parse_forms(page_html) if candidate.otp_field),
        None,
    )
    if form is None or form.otp_field is None or not form.action:
        raise MyQApiError("The MyQ MFA form was not recognized")
    return form


def _consent_form(page_html: str) -> ParsedForm:
    form = next(
        (
            candidate
            for candidate in _parse_forms(page_html)
            if "consent" in candidate.action.lower()
        ),
        None,
    )
    if form is None or not form.action:
        raise MyQApiError("The MyQ consent form was not recognized")
    fields = {**form.fields, "button": "yes"}
    return ParsedForm(form.action, fields, None, None, None)


def _validation_error(page_html: str) -> str | None:
    flattened = re.sub(r"\s+", " ", page_html)
    match = re.search(
        r"validation-summary-errors.*?<ul>(.*?)</ul>|"
        r"field-validation-error[^>]*>(.*?)<",
        flattened,
        re.IGNORECASE,
    )
    if match is None:
        return None
    raw = match.group(1) or match.group(2) or ""
    message = html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
    return re.sub(r"\s+", " ", message) or None


def _raise_for_challenge(page_html: str) -> None:
    if any(marker in page_html for marker in ("Just a moment", "Verify you are human")):
        raise MyQCloudflareChallengeError


def _redirect_code(redirect_url: str) -> str:
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(redirect_url).query).get("code", [""])[0]
    if not code:
        raise MyQApiError("The MyQ callback did not contain an authorization code")
    return code
