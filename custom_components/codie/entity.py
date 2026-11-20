"""Base entity for Codie."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CodieCoordinator

if TYPE_CHECKING:
    from . import CodieConfigEntry


class CodieEntity(CoordinatorEntity[CodieCoordinator]):
    """Common device info."""

    _attr_has_entity_name = True

    def __init__(self, entry: CodieConfigEntry, key: str) -> None:
        """Initialize."""
        super().__init__(entry.runtime_data.coordinator)
        unique = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{unique}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique)},
            name="Codie",
            manufacturer="CODE27",
            model="Codie",
            sw_version=entry.data.get("firmware") or None,
            configuration_url=None,
        )
