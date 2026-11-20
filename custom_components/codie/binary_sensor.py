"""Binary sensors for Codie: device online and tool executor connected."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_EXECUTOR_STATE
from .entity import CodieEntity

if TYPE_CHECKING:
    from . import CodieConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: CodieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up binary sensors."""
    async_add_entities([CodieOnlineSensor(entry), CodieExecutorSensor(entry)])


class CodieOnlineSensor(CodieEntity, BinarySensorEntity):
    """Whether the device answers on the local network."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, entry: CodieConfigEntry) -> None:
        """Initialize."""
        super().__init__(entry, "online")

    @property
    def available(self) -> bool:
        """Always available; offline is reported as off."""
        return True

    @property
    def is_on(self) -> bool:
        """Return online state."""
        return bool(self.coordinator.data and self.coordinator.data.online)


class CodieExecutorSensor(CodieEntity, BinarySensorEntity):
    """Whether the external tool WebSocket is connected."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: CodieConfigEntry) -> None:
        """Initialize."""
        super().__init__(entry, "executor")
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        """Subscribe to executor state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_EXECUTOR_STATE.format(self._entry.entry_id), self._executor_changed
            )
        )

    @callback
    def _executor_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Always available."""
        return True

    @property
    def is_on(self) -> bool:
        """Return executor connection state."""
        return self._entry.runtime_data.executor.state.connected

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose diagnostics."""
        state = self._entry.runtime_data.executor.state
        return {"last_error": state.last_error, "invocations": state.invocations}
