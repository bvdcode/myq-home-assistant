from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import MyQDataUpdateCoordinator
from .models import GarageDoor


class MyQEntity(CoordinatorEntity[MyQDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        door: GarageDoor,
    ) -> None:
        super().__init__(coordinator)
        self._serial_number = door.serial_number
        self._attr_unique_id = door.serial_number
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, door.serial_number)},
            manufacturer=MANUFACTURER,
            model=door.device_model,
            name=door.name,
            serial_number=door.serial_number,
        )

    @property
    def door(self) -> GarageDoor:
        return self.coordinator.data[self._serial_number]

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self.door.online is not False
