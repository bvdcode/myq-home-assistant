import logging

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MyQClient
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .exceptions import MyQApiError, MyQAuthenticationError
from .models import MyQConfigEntry, MyQCoordinatorData

_LOGGER = logging.getLogger(__name__)


class MyQDataUpdateCoordinator(DataUpdateCoordinator[MyQCoordinatorData]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: MyQConfigEntry,
        client: MyQClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=DEFAULT_UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> MyQCoordinatorData:
        try:
            doors = await self.client.async_get_garage_doors()
        except MyQAuthenticationError as error:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from error
        except (ClientError, MyQApiError) as error:
            raise UpdateFailed("Unable to update MyQ garage doors") from error
        return {door.serial_number: door for door in doors}
