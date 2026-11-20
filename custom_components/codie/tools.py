"""Home Assistant side of the Codie external tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import difflib
import logging
import re
from typing import Any

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.translation import async_get_translations

from .executor import ToolResult

_LOGGER = logging.getLogger(__name__)

ASSISTANT = "conversation"

# domain -> (on service, off service, toggle service)
SWITCHABLE: dict[str, tuple[str, str, str | None]] = {
    "light": ("turn_on", "turn_off", "toggle"),
    "switch": ("turn_on", "turn_off", "toggle"),
    "fan": ("turn_on", "turn_off", "toggle"),
    "input_boolean": ("turn_on", "turn_off", "toggle"),
    "media_player": ("turn_on", "turn_off", "toggle"),
    "climate": ("turn_on", "turn_off", "toggle"),
    "humidifier": ("turn_on", "turn_off", "toggle"),
    "siren": ("turn_on", "turn_off", "toggle"),
    "remote": ("turn_on", "turn_off", "toggle"),
    "water_heater": ("turn_on", "turn_off", None),
    "automation": ("turn_on", "turn_off", "toggle"),
    "script": ("turn_on", "turn_off", "toggle"),
    "cover": ("open_cover", "close_cover", "toggle"),
    "valve": ("open_valve", "close_valve", "toggle"),
}
SCENE_DOMAINS = {"scene"}
_NEVER = {"zone", "persistent_notification", "update", "event"}
MAX_CANDIDATES = 8


@dataclass
class Match:
    """Entity resolution result."""

    state: State | None = None
    candidates: list[State] | None = None
    not_exposed: bool = False


# --------------------------------------------------------------------- texts
_TEXTS = {
    "de": {
        "not_found": "Ich finde kein freigegebenes Gerät namens „{query}“.",
        "not_exposed": "„{name}“ ist in Home Assistant nicht für Sprachassistenten freigegeben.",
        "ambiguous": "Ich habe mehrere passende Geräte gefunden: {names}. Welches meinst du?",
        "unsupported": "„{name}“ lässt sich nicht ein- oder ausschalten.",
        "state": "{name} ist {state}.",
        "state_value": "{name}: {state}.",
        "unavailable": "{name} ist gerade nicht erreichbar.",
        "on_done": "{name} ist jetzt {state}.",
        "sent": "Ich habe {name} den Befehl geschickt.",
        "scene_done": "Die Szene {name} ist aktiviert.",
        "error": "Das hat nicht geklappt: {error}",
        "and": " oder ",
    },
    "en": {
        "not_found": "I can't find an exposed device called “{query}”.",
        "not_exposed": "“{name}” is not exposed to voice assistants in Home Assistant.",
        "ambiguous": "I found several matching devices: {names}. Which one do you mean?",
        "unsupported": "“{name}” can't be switched on or off.",
        "state": "{name} is {state}.",
        "state_value": "{name}: {state}.",
        "unavailable": "{name} is currently unavailable.",
        "on_done": "{name} is now {state}.",
        "sent": "I sent the command to {name}.",
        "scene_done": "Scene {name} is activated.",
        "error": "That didn't work: {error}",
        "and": " or ",
    },
}


def _lang(hass: HomeAssistant) -> str:
    return "de" if (hass.config.language or "en").lower().startswith("de") else "en"


def _t(hass: HomeAssistant, key: str, **kwargs: Any) -> str:
    return _TEXTS[_lang(hass)][key].format(**kwargs)


# ---------------------------------------------------------------- resolution
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_FILLER = {"der", "die", "das", "den", "dem", "des", "the", "im", "in", "am", "an", "vom", "von", "bitte", "mal"}


def _norm(text: str) -> str:
    text = text.casefold().translate(_UMLAUTS).replace("_", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _tokens(text: str) -> set[str]:
    return {tok for tok in _norm(text).split() if tok not in _FILLER}


_GENERIC = {"licht", "light", "lampe", "lamp", "leuchte", "beleuchtung", "geraet", "device"}


def _tokens_match(query_tokens: set[str], name: str) -> bool:
    """Every query token appears in the name; German compounds count ("lampe" in "stehlampe")."""
    name_tokens = name.split()
    return all(
        any(tok == part or (len(tok) >= 4 and tok in part) for part in name_tokens) for tok in query_tokens
    )


def _names_for(hass: HomeAssistant, state: State) -> set[str]:
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    names = {state.name, state.entity_id, state.entity_id.split(".", 1)[1]}
    entry = ent_reg.async_get(state.entity_id)
    area_name = None
    if entry is not None:
        names.update(alias for alias in entry.aliases if isinstance(alias, str))
        area_id = entry.area_id
        if area_id is None and entry.device_id:
            device = dr.async_get(hass).async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id and (area := area_reg.async_get_area(area_id)):
            area_name = area.name
    if area_name:
        names.update({f"{area_name} {name}" for name in list(names) if "." not in name})
    return {_norm(name) for name in names if name}


def _resolve(hass: HomeAssistant, query: str, domains: set[str] | None) -> Match:
    """Find an entity by entity_id, name, alias or area + name. domains=None means all."""
    query_raw = query.strip()
    # Exact entity_id
    if "." in query_raw and (state := hass.states.get(query_raw.lower())):
        if domains is not None and state.domain not in domains:
            return Match()
        if not async_should_expose(hass, ASSISTANT, state.entity_id):
            return Match(state=state, not_exposed=True)
        return Match(state=state)

    query_norm = _norm(query_raw)
    query_tokens = _tokens(query_raw)
    exposed: list[tuple[State, set[str]]] = []
    hidden: list[tuple[State, set[str]]] = []
    for state in hass.states.async_all(domains):
        if state.domain in _NEVER:
            continue
        target = exposed if async_should_expose(hass, ASSISTANT, state.entity_id) else hidden
        target.append((state, _names_for(hass, state)))

    def find(pool: list[tuple[State, set[str]]]) -> list[State]:
        exact = [s for s, names in pool if query_norm in names]
        if exact:
            return exact
        for tokens in (query_tokens, query_tokens - _GENERIC):
            if not tokens:
                continue
            contains = [s for s, names in pool if any(_tokens_match(tokens, name) for name in names)]
            if contains:
                return contains
        all_names = {name: s for s, names in pool for name in names}
        close = difflib.get_close_matches(query_norm, list(all_names), n=MAX_CANDIDATES, cutoff=0.8)
        result: list[State] = []
        for name in close:
            if all_names[name] not in result:
                result.append(all_names[name])
        return result

    found = find(exposed)
    if len(found) == 1:
        return Match(state=found[0])
    if len(found) > 1:
        # Prefer a single "on/off"-capable or exact friendly-name hit
        named = [s for s in found if _norm(s.name) == query_norm]
        if len(named) == 1:
            return Match(state=named[0])
        return Match(candidates=found[:MAX_CANDIDATES])
    hidden_found = find(hidden)
    if hidden_found:
        return Match(state=hidden_found[0], not_exposed=True)
    return Match()


# ----------------------------------------------------------------- speaking
async def _state_text(hass: HomeAssistant, state: State) -> str:
    """Localized, speakable state value."""
    value = state.state
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit:
        number = value
        if _lang(hass) == "de":
            number = value.replace(".", ",")
        return f"{number} {unit}"
    if state.domain != "binary_sensor" and value in ("on", "off"):
        return {"de": {"on": "an", "off": "aus"}, "en": {"on": "on", "off": "off"}}[_lang(hass)][value]
    lang = hass.config.language or "en"
    translations = await async_get_translations(hass, lang, "entity_component", {state.domain})
    device_class = state.attributes.get("device_class") or "_"
    for key in (
        f"component.{state.domain}.entity_component.{device_class}.state.{value}",
        f"component.{state.domain}.entity_component._.state.{value}",
    ):
        if key in translations:
            return translations[key].lower()
    return value.replace("_", " ")


def _data(state: State | None, speech: str, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"speech": speech[:400]}
    if state is not None:
        data["entity_id"] = state.entity_id
        data["name"] = state.name[:255]
        data["state"] = state.state[:255]
    data.update(extra)
    return data


def _not_resolved(hass: HomeAssistant, query: str, match: Match) -> ToolResult:
    if match.candidates:
        names = [s.name for s in match.candidates]
        speech = _t(hass, "ambiguous", names=_t(hass, "and").join(names))
        return ToolResult("needs_clarification", _data(None, speech, candidates=names))
    if match.not_exposed and match.state is not None:
        return ToolResult("failed", _data(None, _t(hass, "not_exposed", name=match.state.name)), "NOT_EXPOSED")
    return ToolResult("failed", _data(None, _t(hass, "not_found", query=query)), "ENTITY_NOT_FOUND")


# ------------------------------------------------------------------- tools
async def _get_state(hass: HomeAssistant, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("entity", ""))
    match = _resolve(hass, query, None)
    if match.state is None or match.not_exposed:
        return _not_resolved(hass, query, match)
    state = match.state
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return ToolResult("succeeded", _data(state, _t(hass, "unavailable", name=state.name)))
    text = await _state_text(hass, state)
    extra = []
    attrs = state.attributes
    if state.domain == "climate":
        if (cur := attrs.get("current_temperature")) is not None:
            extra.append(f"{cur} °C" if _lang(hass) == "en" else f"aktuell {str(cur).replace('.', ',')} °C")
        if (target := attrs.get("temperature")) is not None:
            extra.append(f"target {target} °C" if _lang(hass) == "en" else f"Ziel {str(target).replace('.', ',')} °C")
    elif state.domain == "light" and state.state == "on" and attrs.get("brightness") is not None:
        pct = round(attrs["brightness"] / 255 * 100)
        extra.append(f"{pct} %")
    if extra:
        text = f"{text} ({', '.join(extra)})"
    key = "state_value" if attrs.get(ATTR_UNIT_OF_MEASUREMENT) else "state"
    return ToolResult("succeeded", _data(state, _t(hass, key, name=state.name, state=text)))


async def _wait_for_change(hass: HomeAssistant, entity_id: str, old: str, timeout: float = 2.0) -> State | None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        state = hass.states.get(entity_id)
        if state is not None and state.state != old:
            return state
        await asyncio.sleep(0.1)
    return hass.states.get(entity_id)


async def _switch(hass: HomeAssistant, args: dict[str, Any], action: int) -> ToolResult:
    query = str(args.get("entity", ""))
    match = _resolve(hass, query, set(SWITCHABLE))
    if match.state is None or match.not_exposed:
        return _not_resolved(hass, query, match)
    state = match.state
    service = SWITCHABLE[state.domain][action]
    if service is None:
        return ToolResult("failed", _data(state, _t(hass, "unsupported", name=state.name)), "NOT_SUPPORTED")
    try:
        await hass.services.async_call(
            state.domain, service, {"entity_id": state.entity_id}, blocking=True
        )
    except (HomeAssistantError, ValueError) as err:
        return ToolResult("failed", _data(state, _t(hass, "error", error=str(err))), "SERVICE_ERROR")
    new_state = await _wait_for_change(hass, state.entity_id, state.state)
    if new_state is None or new_state.state == state.state:
        if action != 2 and new_state is not None and new_state.state in ("on", "off", "open", "closed"):
            # Already in the requested state.
            text = await _state_text(hass, new_state)
            return ToolResult("succeeded", _data(new_state, _t(hass, "on_done", name=new_state.name, state=text)))
        return ToolResult("succeeded", _data(new_state or state, _t(hass, "sent", name=state.name)))
    text = await _state_text(hass, new_state)
    return ToolResult("succeeded", _data(new_state, _t(hass, "on_done", name=new_state.name, state=text)))


async def _scene(hass: HomeAssistant, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("scene", ""))
    match = _resolve(hass, query, SCENE_DOMAINS)
    if match.state is None or match.not_exposed:
        return _not_resolved(hass, query, match)
    state = match.state
    try:
        await hass.services.async_call("scene", "turn_on", {"entity_id": state.entity_id}, blocking=True)
    except (HomeAssistantError, ValueError) as err:
        return ToolResult("failed", _data(state, _t(hass, "error", error=str(err))), "SERVICE_ERROR")
    return ToolResult("succeeded", _data(state, _t(hass, "scene_done", name=state.name)))


async def async_handle_tool(hass: HomeAssistant, name: str, args: dict[str, Any]) -> ToolResult:
    """Dispatch a tool call from Codie."""
    if name == "ha_get_state":
        return await _get_state(hass, args)
    if name == "ha_turn_on":
        return await _switch(hass, args, 0)
    if name == "ha_turn_off":
        return await _switch(hass, args, 1)
    if name == "ha_toggle":
        return await _switch(hass, args, 2)
    if name == "ha_activate_scene":
        return await _scene(hass, args)
    return ToolResult("failed", {"speech": f"Unknown tool {name}"}, "UNKNOWN_TOOL")
