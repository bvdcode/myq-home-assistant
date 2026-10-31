import asyncio
import time

from aiohttp import ClientError
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MyQDataUpdateCoordinator
from .entity import MyQEntity
from .exceptions import MyQError
from .models import GarageDoor, MyQCamera
from .runtime import MyQConfigEntry
from .tend_stream import TendLiveStream

# Each snapshot is captured on demand and takes over a second, so MJPEG previews stay slow.
SNAPSHOT_FRAME_INTERVAL = 5.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyQConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    del hass
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MyQGarageCamera(coordinator, door)
        for door in coordinator.data.values()
        if door.camera is not None
    )


class MyQGarageCamera(MyQEntity, Camera):
    _attr_frame_interval = SNAPSHOT_FRAME_INTERVAL
    _attr_translation_key = "camera"

    def __init__(self, coordinator: MyQDataUpdateCoordinator, door: GarageDoor) -> None:
        super().__init__(coordinator, door)
        Camera.__init__(self)
        self._attr_unique_id = f"{door.serial_number}_camera"
        self._snapshot_lock = asyncio.Lock()
        self._snapshot: bytes | None = None
        self._snapshot_at = 0.0
        self._live_stream: TendLiveStream | None = None
        self._live_camera_serial: str | None = None
        if door.camera is not None and door.camera.serial_prefix == "TC":
            self._attr_supported_features = CameraEntityFeature.STREAM
            self.stream_options = {"use_wallclock_as_timestamps": True}
            self._live_camera_serial = door.camera.serial_number

    @property
    def camera(self) -> MyQCamera | None:
        door = self.door
        return None if door is None else door.camera

    @property
    def model(self) -> str | None:
        camera = self.camera
        return None if camera is None else camera.device_model

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        camera = self.camera
        if camera is None or camera.serial_prefix is None:
            return None
        return {"camera_serial_prefix": camera.serial_prefix}

    @property
    def available(self) -> bool:
        camera = self.camera
        return super().available and camera is not None and camera.online is not False

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        del width, height
        camera = self.camera
        if camera is None:
            return None
        requested = time.monotonic()
        async with self._snapshot_lock:
            # Requests that arrived while a capture was running share its image.
            if self._snapshot is not None and self._snapshot_at > requested:
                return self._snapshot
            try:
                image = await self.coordinator.client.async_get_camera_image(camera)
            except (ClientError, MyQError) as error:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="camera_image_failed",
                ) from error
            self._snapshot, self._snapshot_at = image, time.monotonic()
            return image

    async def stream_source(self) -> str | None:
        """Expose the TC camera's H.264 feed to Home Assistant's stream integration."""
        if not self.available or self._live_camera_serial is None:
            return None
        if self._live_stream is None:
            self._live_stream = TendLiveStream(
                self._live_camera_serial,
                self._serial_number,
                self.coordinator.client.async_access_token,
            )
        return await self._live_stream.source_url()

    async def async_will_remove_from_hass(self) -> None:
        """Release the stream and Tend session; an entity re-added after a rename starts fresh."""
        if self.stream is not None:
            await self.stream.stop()
            self.stream = None
        if self._live_stream is not None:
            await self._live_stream.close()
            self._live_stream = None
        await super().async_will_remove_from_hass()
