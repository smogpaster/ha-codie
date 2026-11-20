"""Config flow for Codie: pair with the code shown on the device display."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CodieApiError, CodieClient, CodieConnectionError, CodieCredentials, CodieError
from .const import CONF_DEVICE_ID, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_PAIRING_CODE = "pairing_code"


class CodieConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Codie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._challenge_id: str | None = None
        self._user_id: str = ""

    def _client(self, credentials: CodieCredentials | None = None) -> CodieClient:
        return CodieClient(async_get_clientsession(self.hass), self._host, self._port, credentials)

    async def _start_pairing(self) -> str | None:
        """Ask the device to display a pairing code. Return an error key or None."""
        client = self._client()
        try:
            bootstrap = await client.bootstrap()
            if not bootstrap.get("device_logged_in", True):
                return "not_logged_in"
            self._user_id = bootstrap.get("user_id", "")
            challenge = await client.pair_request()
        except CodieConnectionError:
            return "cannot_connect"
        except CodieApiError as err:
            _LOGGER.warning("Pairing request rejected by Codie: %s", err)
            return "pairing_rejected"
        self._challenge_id = challenge["challenge_id"]
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for host and port."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = int(user_input[CONF_PORT])
            if not (error := await self._start_pairing()):
                return await self.async_step_pair()
            errors["base"] = error
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_PORT, default=self._port): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the pairing code shown on the device and confirm it."""
        errors: dict[str, str] = {}
        if user_input is not None and self._challenge_id:
            code = "".join(ch for ch in str(user_input[CONF_PAIRING_CODE]) if ch.isdigit())
            client = self._client()
            try:
                data = await client.pair_confirm(self._challenge_id, code, "permanent")
            except CodieConnectionError:
                errors["base"] = "cannot_connect"
            except CodieApiError as err:
                _LOGGER.debug("Pairing confirm failed: %s", err)
                if err.code and "EXPIRED" in err.code:
                    errors["base"] = "code_expired"
                    # Display a fresh code right away.
                    if new_error := await self._start_pairing():
                        errors["base"] = new_error
                else:
                    errors["base"] = "invalid_code"
            else:
                return await self._finish(data)
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required(CONF_PAIRING_CODE): str}),
            errors=errors,
            description_placeholders={"host": self._host},
        )

    async def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        credentials = CodieCredentials(
            local_secret=data["local_secret"],
            user_id=self._user_id,
            local_session_id=data.get("local_session_id") or "",
            local_session_expires_at_ms=int(data.get("local_session_expires_at_ms") or 0),
            auth_scope=data.get("auth_scope") or "permanent",
        )
        client = self._client(credentials)
        try:
            system = await client.system()
        except CodieError as err:
            _LOGGER.warning("Paired, but signed request failed: %s", err)
            return self.async_abort(reason="signature_failed")
        unique_id = system.get("machine_id") or data.get(CONF_DEVICE_ID) or self._host
        entry_data = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_DEVICE_ID: data.get(CONF_DEVICE_ID, ""),
            "firmware": data.get("version", ""),
            **credentials.as_dict(),
        }
        await self.async_set_unique_id(unique_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=entry_data)
        self._abort_if_unique_id_configured(updates=entry_data)
        name = system.get("username")
        title = f"Codie ({name})" if name else "Codie"
        return self.async_create_entry(title=title, data=entry_data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Credentials were rejected; pair again."""
        self._host = entry_data[CONF_HOST]
        self._port = entry_data[CONF_PORT]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm re-pairing, then show a new code on the device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (error := await self._start_pairing()):
                return await self.async_step_pair()
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            errors=errors,
            description_placeholders={"host": self._host},
        )
