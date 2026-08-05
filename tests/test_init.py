from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myq.const import (
    CONF_EMAIL,
    CONF_MFA_METHOD,
    CONF_TOKENS,
    DOMAIN,
    MFA_METHOD_EMAIL,
)
from custom_components.myq.models import GarageDoor, OAuthTokens

EMAIL = "driver@example.com"
DOOR = GarageDoor(
    "account-1",
    "door-1",
    "Main garage",
    "Wi-Fi GDO",
    "closed",
    True,
    in_vacation_mode=False,
    attached_worklight_on=True,
    active_fault_codes=("1-2",),
    absolute_cycle_count=123,
    service_cycle_count=45,
    last_device_activation_source="myq_app",
)


async def test_setup_creates_cover_and_persists_refreshed_tokens(
    hass: HomeAssistant,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_get_garage_doors = AsyncMock(return_value=(DOOR,))
    token_listener: Callable[[OAuthTokens], None] | None = None

    def create_auth(
        _session: object,
        _tokens: OAuthTokens,
        listener: Callable[[OAuthTokens], None],
    ) -> MagicMock:
        nonlocal token_listener
        token_listener = listener
        return MagicMock()

    with (
        patch("custom_components.myq.MyQAuth", side_effect=create_auth),
        patch("custom_components.myq.MyQClient", return_value=client),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _entry_state(entry) is ConfigEntryState.LOADED
    assert entry.runtime_data.client is client
    entity_id = er.async_get(hass).async_get_entity_id("cover", DOMAIN, "door-1")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "closed"

    registry = er.async_get(hass)
    expected_states = {
        ("binary_sensor", "door-1_vacation_mode"): "off",
        ("binary_sensor", "door-1_work_light"): "on",
        ("binary_sensor", "door-1_active_fault"): "on",
        ("sensor", "door-1_absolute_cycle_count"): "123",
        ("sensor", "door-1_service_cycle_count"): "45",
        ("sensor", "door-1_last_activation_source"): "myq_app",
    }
    for (platform, unique_id), expected_state in expected_states.items():
        diagnostic_entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        assert diagnostic_entity_id is not None
        diagnostic_state = hass.states.get(diagnostic_entity_id)
        assert diagnostic_state is not None
        assert diagnostic_state.state == expected_state

    fault_entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, "door-1_active_fault")
    assert fault_entity_id is not None
    fault_state = hass.states.get(fault_entity_id)
    assert fault_state is not None
    assert fault_state.attributes["fault_codes"] == ("1-2",)

    battery_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "door-1_battery_backup_state"
    )
    assert battery_entity_id is None

    assert token_listener is not None
    token_listener(OAuthTokens("new-access", "new-refresh", 9876543210.0))
    assert entry.data[CONF_TOKENS] == {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": 9876543210.0,
    }

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _entry_state(entry) is ConfigEntryState.NOT_LOADED


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        title=EMAIL,
        data={
            CONF_EMAIL: EMAIL,
            CONF_MFA_METHOD: MFA_METHOD_EMAIL,
            CONF_TOKENS: {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": 9876543210.0,
            },
        },
    )


def _entry_state(entry: MockConfigEntry) -> ConfigEntryState:
    return cast(ConfigEntryState, entry.state)
