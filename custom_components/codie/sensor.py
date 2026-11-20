"""Sensors for Codie: current character slot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CodieEntity

if TYPE_CHECKING:
    from . import CodieConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: CodieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up sensors."""
    async_add_entities([CodieSlotSensor(entry)])


class CodieSlotSensor(CodieEntity, SensorEntity):
    """Current character slot (1-5)."""

    def __init__(self, entry: CodieConfigEntry) -> None:
        """Initialize."""
        super().__init__(entry, "character_slot")

    @property
    def available(self) -> bool:
        """Available while the slot is known."""
        data = self.coordinator.data
        return super().available and bool(data and data.online and data.slot is not None)

    @property
    def native_value(self) -> int | None:
        """Return the slot number."""
        return self.coordinator.data.slot if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose the device interaction state."""
        data = self.coordinator.data
        if not data or not data.device_state:
            return None
        state = data.device_state
        return {
            "screen_on": state.get("screen_on"),
            "locked": state.get("locked"),
            "conversation_blocked": state.get("conversation_blocked"),
            "mode": state.get("game_mode"),
        }
