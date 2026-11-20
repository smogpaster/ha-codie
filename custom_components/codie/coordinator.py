"""Polling coordinator for Codie."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import CodieApiError, CodieAuthError, CodieClient, CodieConnectionError
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

if TYPE_CHECKING:
    from . import CodieConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass
class CodieData:
    """Snapshot of the device."""

    online: bool
    slot: int | None = None
    device_state: dict[str, Any] | None = None


class CodieCoordinator(DataUpdateCoordinator[CodieData]):
    """Polls reachability, current slot and device state."""

    config_entry: CodieConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CodieConfigEntry, client: CodieClient) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.client = client

    async def _async_update_data(self) -> CodieData:
        try:
            await self.client.ping()
        except (CodieConnectionError, CodieApiError):
            return CodieData(online=False)
        try:
            slot = await self.client.slot()
            state = await self.client.device_state()
        except CodieAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CodieConnectionError:
            return CodieData(online=False)
        except CodieApiError as err:
            _LOGGER.debug("Codie status query failed: %s", err)
            return CodieData(online=True)
        return CodieData(online=True, slot=slot.get("slot_number"), device_state=state)
