"""Actions (services) for Codie."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CodieApiError, CodieAuthError, CodieError
from .const import DOMAIN, IMAGE_MAX_BYTES, NOTIFY_MAX_CHARS, TTS_MAX_CHARS

if TYPE_CHECKING:
    from . import CodieConfigEntry

ATTR_ENTRY = "config_entry_id"
ATTR_TEXT = "text"
ATTR_WAIT = "wait"
ATTR_URL = "url"
ATTR_PATH = "path"
ATTR_DURATION = "duration"
ATTR_FIT = "fit"

SERVICE_SAY = "say"
SERVICE_NOTIFY = "notify"
SERVICE_SHOW_IMAGE = "show_image"

TERMINAL = {"completed", "failed", "cancelled"}


def _text(max_len: int):
    def validate(value: Any) -> str:
        text = cv.string(value).strip()
        if not 1 <= len(text) <= max_len:
            raise vol.Invalid(f"text must contain 1-{max_len} characters (after trimming)")
        return text

    return validate


SAY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_TEXT): _text(TTS_MAX_CHARS),
        vol.Optional(ATTR_WAIT, default=False): cv.boolean,
    }
)
NOTIFY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required(ATTR_TEXT): _text(NOTIFY_MAX_CHARS),
    }
)
SHOW_IMAGE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_ENTRY): cv.string,
            vol.Exclusive(ATTR_URL, "source"): cv.url,
            vol.Exclusive(ATTR_PATH, "source"): cv.string,
            vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=86400)),
            vol.Optional(ATTR_FIT, default="contain"): vol.In(["contain", "cover", "stretch"]),
        }
    ),
    cv.has_at_least_one_key(ATTR_URL, ATTR_PATH),
)


def _entry(hass: HomeAssistant, call: ServiceCall) -> CodieConfigEntry:
    entry_id = call.data.get(ATTR_ENTRY)
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found", translation_placeholders={"entry": entry_id}
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
        return entry
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")


def _raise(err: CodieError) -> None:
    if isinstance(err, CodieAuthError):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="auth_failed", translation_placeholders={"error": str(err)}
        ) from err
    raise HomeAssistantError(
        translation_domain=DOMAIN, translation_key="device_error", translation_placeholders={"error": str(err)}
    ) from err


async def _say(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call)
    client = entry.runtime_data.client
    try:
        task = await client.say(call.data[ATTR_TEXT])
        if call.data[ATTR_WAIT]:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 190
            while task["state"] not in TERMINAL and loop.time() < deadline:
                await asyncio.sleep(1)
                task = await client.utterance(task["utterance_id"])
            if task["state"] == "failed":
                error = task.get("error") or {}
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="say_failed",
                    translation_placeholders={"error": f"{error.get('code')}: {error.get('message', '')}"},
                )
    except CodieError as err:
        if isinstance(err, CodieAuthError):
            entry.async_start_reauth(hass)
        _raise(err)
    return {"utterance_id": task["utterance_id"], "state": task["state"]}


async def _notify(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry(hass, call)
    try:
        await entry.runtime_data.client.notify(call.data[ATTR_TEXT])
    except CodieError as err:
        if isinstance(err, CodieAuthError):
            entry.async_start_reauth(hass)
        _raise(err)


def _image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_image")


async def _load_image(hass: HomeAssistant, call: ServiceCall) -> bytes:
    if url := call.data.get(ATTR_URL):
        session = async_get_clientsession(hass)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                content = await resp.content.read(IMAGE_MAX_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="image_download_failed", translation_placeholders={"error": str(err)}
            ) from err
    else:
        path = call.data[ATTR_PATH]
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="path_not_allowed", translation_placeholders={"path": path}
            )

        def _read() -> bytes:
            with Path(path).open("rb") as file:
                return file.read(IMAGE_MAX_BYTES + 1)

        try:
            content = await hass.async_add_executor_job(_read)
        except OSError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="path_not_readable", translation_placeholders={"path": path}
            ) from err
    if len(content) > IMAGE_MAX_BYTES:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="image_too_large")
    if not content:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unsupported_image")
    return content


async def _show_image(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _entry(hass, call)
    content = await _load_image(hass, call)
    content_type = _image_type(content)
    options: dict[str, Any] = {
        "request_id": str(uuid.uuid4()),
        "conflict_policy": "replace",
        "layout": {
            "x_percent": 0,
            "y_percent": 0,
            "width_percent": 100,
            "height_percent": 100,
            "fit": call.data[ATTR_FIT],
        },
    }
    if (duration := call.data.get(ATTR_DURATION)) is not None:
        options["max_duration_ms"] = max(1, int(duration * 1000))
    try:
        session = await entry.runtime_data.client.create_media_session(content, content_type, options)
    except CodieError as err:
        if isinstance(err, CodieAuthError):
            entry.async_start_reauth(hass)
        if isinstance(err, CodieApiError) and err.status == 409:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="media_busy",
                translation_placeholders={"error": str(err)},
            ) from err
        _raise(err)
    return {"session_id": session.get("session_id"), "state": session.get("state")}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Codie actions."""

    async def say(call: ServiceCall) -> ServiceResponse:
        result = await _say(hass, call)
        return result if call.return_response else None

    async def notify(call: ServiceCall) -> None:
        await _notify(hass, call)

    async def show_image(call: ServiceCall) -> ServiceResponse:
        result = await _show_image(hass, call)
        return result if call.return_response else None

    hass.services.async_register(DOMAIN, SERVICE_SAY, say, schema=SAY_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_NOTIFY, notify, schema=NOTIFY_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SHOW_IMAGE, show_image, schema=SHOW_IMAGE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
