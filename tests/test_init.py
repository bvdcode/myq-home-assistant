from collections.abc import Callable
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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
from custom_components.myq.runtime import MyQRuntimeData

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
        ("binary_sensor", "door-1_door_open"): "off",
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

    door_sensor_id = registry.async_get_entity_id("binary_sensor", DOMAIN, "door-1_door_open")
    assert door_sensor_id is not None
    door_sensor = registry.async_get(door_sensor_id)
    assert door_sensor is not None
    assert door_sensor.disabled_by is None
    assert door_sensor.entity_category is None
    door_state = hass.states.get(door_sensor_id)
    assert door_state is not None
    assert door_state.attributes["device_class"] == "garage_door"

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


async def test_door_open_sensor_tracks_cover_without_additional_polling(
    hass: HomeAssistant,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_get_garage_doors = AsyncMock(return_value=(DOOR,))

    with patch("custom_components.myq.MyQClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    sensor_id = registry.async_get_entity_id("binary_sensor", DOMAIN, "door-1_door_open")
    cover_id = registry.async_get_entity_id("cover", DOMAIN, "door-1")
    assert sensor_id is not None
    assert cover_id is not None
    assert client.async_get_garage_doors.await_count == 1
    runtime = cast(MyQRuntimeData, entry.runtime_data)
    states: tuple[tuple[str | None, bool, str, str, bool, bool], ...] = (
        ("closed", True, "off", "closed", False, False),
        ("open", True, "on", "open", False, False),
        ("opening", True, "on", "opening", True, False),
        ("closing", True, "on", "closing", False, True),
        ("moving", True, "on", "open", False, False),
        ("stopped", True, "on", "open", False, False),
        ("unknown", True, STATE_UNKNOWN, STATE_UNKNOWN, False, False),
        (None, True, STATE_UNKNOWN, STATE_UNKNOWN, False, False),
        ("unrecognized", True, STATE_UNKNOWN, STATE_UNKNOWN, False, False),
        ("open", False, STATE_UNAVAILABLE, STATE_UNAVAILABLE, False, False),
        ("opening", False, STATE_UNAVAILABLE, STATE_UNAVAILABLE, True, False),
        ("closing", False, STATE_UNAVAILABLE, STATE_UNAVAILABLE, False, True),
    )

    for poll_count, (
        door_state,
        online,
        expected_sensor,
        expected_cover,
        expected_opening,
        expected_closing,
    ) in enumerate(states, 2):
        door = replace(DOOR, door_state=door_state, online=online)
        assert door.is_opening is expected_opening
        assert door.is_closing is expected_closing
        client.async_get_garage_doors.return_value = (door,)
        await runtime.coordinator.async_refresh()
        await hass.async_block_till_done()

        sensor_state = hass.states.get(sensor_id)
        cover_state = hass.states.get(cover_id)
        assert sensor_state is not None
        assert cover_state is not None
        assert sensor_state.state == expected_sensor
        assert cover_state.state == expected_cover
        assert sensor_state.attributes["device_class"] == "garage_door"
        assert client.async_get_garage_doors.await_count == poll_count

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_missing_door_recovers_all_entities_without_losing_customization(
    hass: HomeAssistant,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    door = replace(DOOR, battery_backup_state="charged")
    other_door = replace(door, serial_number="door-2", name="Side garage")
    client = MagicMock()
    client.async_get_garage_doors = AsyncMock(return_value=(door, other_door))

    with patch("custom_components.myq.MyQClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    cover_id = registry.async_get_entity_id("cover", DOMAIN, door.serial_number)
    assert cover_id is not None
    registry.async_update_entity(cover_id, new_entity_id="cover.driveway", name="Driveway")
    await hass.async_block_till_done()
    registered = er.async_entries_for_config_entry(registry, entry.entry_id)
    original_entities = {entity.entity_id: entity for entity in registered}
    assert len(original_entities) == 18
    original_states = {}
    for entity in registered:
        state = hass.states.get(entity.entity_id)
        assert state is not None
        original_states[entity.entity_id] = state.state

    runtime = cast(MyQRuntimeData, entry.runtime_data)
    client.async_get_garage_doors.return_value = (other_door,)
    await runtime.coordinator.async_refresh()
    await hass.async_block_till_done()

    for entity in registered:
        state = hass.states.get(entity.entity_id)
        assert state is not None
        if entity.unique_id.startswith(door.serial_number):
            assert state.state == STATE_UNAVAILABLE
        else:
            assert state.state == original_states[entity.entity_id]
    assert {
        entity.entity_id: entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    } == original_entities

    client.async_get_garage_doors.return_value = (
        replace(door, door_state="open", absolute_cycle_count=124),
        other_door,
    )
    await runtime.coordinator.async_refresh()
    await hass.async_block_till_done()

    cycle_id = registry.async_get_entity_id("sensor", DOMAIN, "door-1_absolute_cycle_count")
    assert cycle_id is not None
    door_sensor_id = registry.async_get_entity_id("binary_sensor", DOMAIN, "door-1_door_open")
    assert door_sensor_id is not None
    expected_states = {
        **original_states,
        "cover.driveway": "open",
        cycle_id: "124",
        door_sensor_id: "on",
    }
    for entity_id, expected in expected_states.items():
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == expected
    assert {
        entity.entity_id: entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    } == original_entities
    assert _entry_state(entry) is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


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
