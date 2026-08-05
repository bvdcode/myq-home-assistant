from collections.abc import Mapping
from typing import cast

from aiohttp import ClientError
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import MyQAuth, tokens_from_data, tokens_to_data
from .client import MyQClient
from .const import CONF_TOKENS, DOMAIN
from .coordinator import MyQDataUpdateCoordinator
from .exceptions import MyQApiError, MyQAuthenticationError
from .models import MyQConfigData, MyQConfigEntry, MyQRuntimeData, OAuthTokens

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.SENSOR,
)


async def async_setup_entry(hass: HomeAssistant, entry: MyQConfigEntry) -> bool:
    data = cast(MyQConfigData, entry.data)

    @callback
    def async_store_tokens(tokens: OAuthTokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_TOKENS: tokens_to_data(tokens)},
        )

    try:
        token_data = cast(Mapping[str, object], data[CONF_TOKENS])
        auth = MyQAuth(
            async_get_clientsession(hass),
            tokens_from_data(token_data),
            async_store_tokens,
        )
        client = MyQClient(async_get_clientsession(hass), auth)
        coordinator = MyQDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()
    except MyQAuthenticationError as error:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from error
    except ClientError as error:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from error
    except (MyQApiError, ValueError) as error:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="api_error",
        ) from error

    entry.runtime_data = MyQRuntimeData(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyQConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
