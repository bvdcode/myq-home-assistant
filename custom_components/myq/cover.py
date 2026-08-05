from typing import Any

from aiohttp import ClientError
from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import MyQEntity
from .exceptions import MyQError
from .models import MyQConfigEntry

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyQConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    del hass
    coordinator = entry.runtime_data.coordinator
    async_add_entities(MyQGarageDoor(coordinator, door) for door in coordinator.data.values())


class MyQGarageDoor(MyQEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.GARAGE
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_translation_key = "garage_door"

    @property
    def is_closed(self) -> bool | None:
        match self.door.door_state:
            case "closed":
                return True
            case "open" | "opening" | "closing" | "moving" | "stopped":
                return False
            case None | "unknown":
                return None
            case _:
                return None

    @property
    def is_opening(self) -> bool:
        return self.door.door_state == "opening"

    @property
    def is_closing(self) -> bool:
        return self.door.door_state == "closing"

    async def async_open_cover(self, **kwargs: Any) -> None:
        del kwargs
        try:
            await self.coordinator.client.async_open_door(self.door)
        except (ClientError, MyQError) as error:
            raise HomeAssistantError(
                translation_domain="myq",
                translation_key="command_failed",
            ) from error
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        del kwargs
        try:
            await self.coordinator.client.async_close_door(self.door)
        except (ClientError, MyQError) as error:
            raise HomeAssistantError(
                translation_domain="myq",
                translation_key="command_failed",
            ) from error
        await self.coordinator.async_request_refresh()
