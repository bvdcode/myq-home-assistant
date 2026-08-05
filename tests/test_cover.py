from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.myq.cover import MyQGarageDoor
from custom_components.myq.exceptions import MyQApiError
from custom_components.myq.models import GarageDoor


@pytest.mark.parametrize(
    ("state", "closed", "opening", "closing"),
    [
        ("closed", True, False, False),
        ("open", False, False, False),
        ("opening", False, True, False),
        ("closing", False, False, True),
        ("moving", False, False, False),
        ("unknown", None, False, False),
    ],
)
def test_cover_maps_door_state(
    state: str,
    closed: bool | None,
    opening: bool,
    closing: bool,
) -> None:
    entity, _ = _entity(state)

    assert entity.is_closed is closed
    assert entity.is_opening is opening
    assert entity.is_closing is closing


async def test_cover_commands_client_and_refreshes() -> None:
    entity, coordinator = _entity("closed")

    await entity.async_open_cover()
    await entity.async_close_cover()

    coordinator.client.async_open_door.assert_awaited_once_with(entity.door)
    coordinator.client.async_close_door.assert_awaited_once_with(entity.door)
    assert coordinator.async_request_refresh.await_count == 2


async def test_cover_translates_command_failure() -> None:
    entity, coordinator = _entity("closed")
    coordinator.client.async_open_door.side_effect = MyQApiError

    with pytest.raises(HomeAssistantError):
        await entity.async_open_cover()

    coordinator.async_request_refresh.assert_not_awaited()


def _entity(state: str) -> tuple[MyQGarageDoor, MagicMock]:
    door = GarageDoor("account-1", "door-1", "Garage", None, state, True)
    coordinator = MagicMock()
    coordinator.data = {door.serial_number: door}
    coordinator.client.async_open_door = AsyncMock()
    coordinator.client.async_close_door = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return MyQGarageDoor(coordinator, door), coordinator
