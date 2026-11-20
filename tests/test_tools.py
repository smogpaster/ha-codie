"""Tool handler tests: resolution, exposure, switching."""
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.codie.tools import async_handle_tool


async def _setup(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"stehlampe": {"name": "Stehlampe"}, "deckenlampe": {"name": "Deckenlampe"}, "tresor": {"name": "Tresor"}}},
    )
    assert await async_setup_component(
        hass, "scene", {"scene": [{"name": "Filmabend", "entities": {"input_boolean.stehlampe": "on"}}]}
    )
    hass.states.async_set("sensor.bad_temp", "21.5", {"friendly_name": "Badtemperatur", "unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    for entity_id in ("input_boolean.stehlampe", "input_boolean.deckenlampe", "scene.filmabend", "sensor.bad_temp"):
        async_expose_entity(hass, "conversation", entity_id, True)
    async_expose_entity(hass, "conversation", "input_boolean.tresor", False)


async def test_tools(hass: HomeAssistant) -> None:
    await _setup(hass)

    r = await async_handle_tool(hass, "ha_turn_on", {"entity": "Stehlampe"})
    assert r.status == "succeeded", r
    assert hass.states.get("input_boolean.stehlampe").state == "on"
    print(r.data["speech"])

    r = await async_handle_tool(hass, "ha_get_state", {"entity": "stehlampe"})
    assert r.status == "succeeded" and r.data["entity_id"] == "input_boolean.stehlampe"
    print(r.data["speech"])

    r = await async_handle_tool(hass, "ha_toggle", {"entity": "input_boolean.stehlampe"})
    assert r.status == "succeeded"
    assert hass.states.get("input_boolean.stehlampe").state == "off"

    r = await async_handle_tool(hass, "ha_get_state", {"entity": "Lampe"})
    assert r.status == "needs_clarification"
    assert set(r.data["candidates"]) == {"Stehlampe", "Deckenlampe"}
    print(r.data["speech"])

    r = await async_handle_tool(hass, "ha_turn_on", {"entity": "Tresor"})
    assert r.status == "failed" and r.code == "NOT_EXPOSED"
    assert hass.states.get("input_boolean.tresor").state == "off"

    r = await async_handle_tool(hass, "ha_turn_on", {"entity": "input_boolean.tresor"})
    assert r.status == "failed" and r.code == "NOT_EXPOSED"

    r = await async_handle_tool(hass, "ha_turn_on", {"entity": "Blubber"})
    assert r.status == "failed" and r.code == "ENTITY_NOT_FOUND"

    r = await async_handle_tool(hass, "ha_activate_scene", {"scene": "filmabend"})
    assert r.status == "succeeded"
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.stehlampe").state == "on"

    r = await async_handle_tool(hass, "ha_get_state", {"entity": "Badtemperatur"})
    assert r.status == "succeeded" and "21,5 °C" in r.data["speech"]
    print(r.data["speech"])

    r = await async_handle_tool(hass, "ha_turn_on", {"entity": "Badtemperatur"})
    assert r.status == "failed"


async def test_generic_words(hass: HomeAssistant) -> None:
    await _setup(hass)
    hass.states.async_set("light.wohnzimmer", "off", {"friendly_name": "Wohnzimmer"})
    async_expose_entity(hass, "conversation", "light.wohnzimmer", True)
    r = await async_handle_tool(hass, "ha_get_state", {"entity": "Licht im Wohnzimmer"})
    assert r.status == "succeeded" and r.data["entity_id"] == "light.wohnzimmer", r
    print(r.data["speech"])
