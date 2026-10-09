from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class StoredTokens(TypedDict):
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class MyQAccount:
    account_id: str
    name: str


@dataclass(frozen=True, slots=True)
class GarageDoor:
    account_id: str
    serial_number: str
    name: str
    device_model: str | None
    door_state: str | None
    online: bool | None
    battery_backup_state: str | None = None
    in_vacation_mode: bool | None = None
    attached_worklight_on: bool | None = None
    active_fault_codes: tuple[str, ...] = ()
    absolute_cycle_count: int | None = None
    service_cycle_count: int | None = None
    last_device_activation_source: str | None = None

    @property
    def is_closed(self) -> bool | None:
        """Return whether the door is closed, or None when its state is unknown."""
        match self.door_state:
            case "closed":
                return True
            case "open" | "opening" | "closing" | "moving" | "stopped":
                return False
            case None | "unknown":
                return None
            case _:
                return None

    @property
    def is_open(self) -> bool | None:
        """Return whether the door is open, or None when its state is unknown."""
        is_closed = self.is_closed
        if is_closed is None:
            return None
        return not is_closed

    @property
    def is_opening(self) -> bool:
        """Return whether the door is opening."""
        return self.door_state == "opening"

    @property
    def is_closing(self) -> bool:
        """Return whether the door is closing."""
        return self.door_state == "closing"
