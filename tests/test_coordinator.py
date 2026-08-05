from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientConnectionError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myq.coordinator import MyQDataUpdateCoordinator
from custom_components.myq.exceptions import MyQAuthenticationError
from custom_components.myq.models import GarageDoor

DOOR = GarageDoor("account-1", "door-1", "Garage", None, "closed", True)


async def test_coordinator_indexes_every_door(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.async_get_garage_doors = AsyncMock(return_value=(DOOR,))
    coordinator = MyQDataUpdateCoordinator(hass, MockConfigEntry(), client)

    assert await coordinator._async_update_data() == {"door-1": DOOR}


async def test_coordinator_requests_reauthentication(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.async_get_garage_doors = AsyncMock(side_effect=MyQAuthenticationError)
    coordinator = MyQDataUpdateCoordinator(hass, MockConfigEntry(), client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_translates_network_failure(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.async_get_garage_doors = AsyncMock(side_effect=ClientConnectionError)
    coordinator = MyQDataUpdateCoordinator(hass, MockConfigEntry(), client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
