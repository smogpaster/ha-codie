"""The Codie (CODE27) integration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .api import CodieClient, CodieCredentials, CodieError
from .catalog import build_catalog
from .const import DOMAIN, SIGNAL_EXECUTOR_STATE, TOOL_INTEGRATION_ID, TOOL_INTEGRATION_NAME
from .coordinator import CodieCoordinator
from .executor import ToolExecutor
from .services import async_setup_services
from .tools import async_handle_tool

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class CodieRuntime:
    """Runtime objects of a config entry."""

    client: CodieClient
    coordinator: CodieCoordinator
    executor: ToolExecutor


type CodieConfigEntry = ConfigEntry[CodieRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CodieConfigEntry) -> bool:
    """Set up Codie from a config entry."""
    session = async_get_clientsession(hass)
    client = CodieClient(
        session, entry.data[CONF_HOST], entry.data[CONF_PORT], CodieCredentials.from_dict(dict(entry.data))
    )
    coordinator = CodieCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    @callback
    def _executor_changed() -> None:
        async_dispatcher_send(hass, SIGNAL_EXECUTOR_STATE.format(entry.entry_id))

    @callback
    def _auth_failed() -> None:
        entry.async_start_reauth(hass)

    executor = ToolExecutor(
        client,
        session,
        TOOL_INTEGRATION_ID,
        build_catalog(TOOL_INTEGRATION_NAME),
        partial(async_handle_tool, hass),
        on_state_change=_executor_changed,
        on_auth_error=_auth_failed,
    )
    entry.runtime_data = CodieRuntime(client, coordinator, executor)

    @callback
    def _device_back_online() -> None:
        data = coordinator.data
        if data and data.online and not executor.state.connected:
            executor.reconnect_now()

    entry.async_on_unload(coordinator.async_add_listener(_device_back_online))
    entry.async_create_background_task(hass, executor.run(), f"{DOMAIN}_executor_{entry.entry_id}")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CodieConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.executor.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the tool catalog from the device when the entry is deleted (best effort)."""
    client = CodieClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        CodieCredentials.from_dict(dict(entry.data)),
    )
    try:
        await client.delete_tool_integration(TOOL_INTEGRATION_ID)
    except CodieError as err:
        _LOGGER.debug("Could not remove Codie tool integration: %s", err)
