from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import MyQDataUpdateCoordinator
from .entity import MyQEntity
from .models import GarageDoor, MyQConfigEntry

type ValueFn = Callable[[GarageDoor], StateType]
type ExistsFn = Callable[[GarageDoor], bool]


@dataclass(frozen=True, kw_only=True)
class MyQSensorEntityDescription(SensorEntityDescription):
    value_fn: ValueFn
    exists_fn: ExistsFn


SENSOR_DESCRIPTIONS: tuple[MyQSensorEntityDescription, ...] = (
    MyQSensorEntityDescription(
        key="battery_backup_state",
        translation_key="battery_backup_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda door: door.battery_backup_state,
        exists_fn=lambda door: door.battery_backup_state not in {None, "none"},
    ),
    MyQSensorEntityDescription(
        key="absolute_cycle_count",
        translation_key="absolute_cycle_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda door: door.absolute_cycle_count,
        exists_fn=lambda door: door.absolute_cycle_count is not None,
    ),
    MyQSensorEntityDescription(
        key="service_cycle_count",
        translation_key="service_cycle_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda door: door.service_cycle_count,
        exists_fn=lambda door: door.service_cycle_count is not None,
    ),
    MyQSensorEntityDescription(
        key="last_activation_source",
        translation_key="last_activation_source",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda door: door.last_device_activation_source,
        exists_fn=lambda door: door.last_device_activation_source is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyQConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    del hass
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MyQSensor(coordinator, door, description)
        for door in coordinator.data.values()
        for description in SENSOR_DESCRIPTIONS
        if description.exists_fn(door)
    )


class MyQSensor(MyQEntity, SensorEntity):
    entity_description: MyQSensorEntityDescription

    def __init__(
        self,
        coordinator: MyQDataUpdateCoordinator,
        door: GarageDoor,
        description: MyQSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, door)
        self.entity_description = description
        self._attr_unique_id = f"{door.serial_number}_{description.key}"

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.door)
