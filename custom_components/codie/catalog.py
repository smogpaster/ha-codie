"""External tool catalog registered on the Codie device."""

from __future__ import annotations

from typing import Any

TOOL_VERSION = "v1"
TOOL_TIMEOUT_MS = 10000

_ENTITY_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "description": (
                "Name of the device or entity as the user said it (e.g. 'Wohnzimmerlampe'), "
                "or a Home Assistant entity_id such as light.wohnzimmer."
            ),
        }
    },
    "required": ["entity"],
    "additionalProperties": False,
}

_SCENE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scene": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "description": "Name of the scene (e.g. 'Filmabend') or its entity_id such as scene.filmabend.",
        }
    },
    "required": ["scene"],
    "additionalProperties": False,
}

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speech": {
            "type": "string",
            "maxLength": 400,
            "description": "Short sentence describing the outcome, suitable to be spoken to the user.",
        },
        "entity_id": {"type": "string", "maxLength": 255},
        "name": {"type": "string", "maxLength": 255},
        "state": {"type": "string", "maxLength": 255},
        "candidates": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 255},
            "description": "Possible matches when the request was ambiguous.",
        },
    },
    "required": ["speech"],
    "additionalProperties": False,
}


def _tool(name: str, description: str, params: dict[str, Any], risk: str, idempotent: bool) -> dict[str, Any]:
    return {
        "name": name,
        "version": TOOL_VERSION,
        "description": description,
        "parameters": params,
        "result_schema": RESULT_SCHEMA,
        "risk": risk,
        "idempotent": idempotent,
        "confirmation_required": False,
        "timeout_ms": TOOL_TIMEOUT_MS,
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "ha_get_state",
        "Read the current state of a smart-home device or sensor in Home Assistant "
        "(e.g. whether a light is on, a temperature, a door contact). "
        "Use the 'speech' field of the result as the answer.",
        _ENTITY_PARAMS,
        "read",
        True,
    ),
    _tool(
        "ha_turn_on",
        "Turn on a smart-home device in Home Assistant (light, switch, fan, media player, ...).",
        _ENTITY_PARAMS,
        "reversible",
        True,
    ),
    _tool(
        "ha_turn_off",
        "Turn off a smart-home device in Home Assistant (light, switch, fan, media player, ...).",
        _ENTITY_PARAMS,
        "reversible",
        True,
    ),
    _tool(
        "ha_toggle",
        "Toggle a smart-home device in Home Assistant: on becomes off and off becomes on.",
        _ENTITY_PARAMS,
        "reversible",
        False,
    ),
    _tool(
        "ha_activate_scene",
        "Activate a Home Assistant scene, e.g. 'Filmabend' or 'Gute Nacht'.",
        _SCENE_PARAMS,
        "reversible",
        True,
    ),
]


def build_catalog(name: str) -> dict[str, Any]:
    """Return the PUT body without expected_revision."""
    return {"protocol_version": "v1", "name": name, "enabled": True, "tools": TOOLS}
