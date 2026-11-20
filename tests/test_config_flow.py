"""Config flow and setup tests with a mocked device."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.codie.api import CodieApiError, CodieAuthError
from custom_components.codie.const import DOMAIN

CLIENT = "custom_components.codie.config_flow.CodieClient"
CONFIRM = {
    "local_secret": "s3cret",
    "local_session_id": "",
    "local_session_expires_at_ms": 0,
    "auth_scope": "permanent",
    "device_id": "dev1",
    "version": "1.6.0",
}


def _mock(client):
    inst = client.return_value
    inst.bootstrap = AsyncMock(return_value={"user_id": "u1", "device_logged_in": True})
    inst.pair_request = AsyncMock(return_value={"challenge_id": "c1", "expires_at_ms": 0})
    inst.pair_confirm = AsyncMock(return_value=CONFIRM)
    inst.system = AsyncMock(return_value={"machine_id": "m1", "username": "Peter"})
    return inst


async def test_full_flow(hass: HomeAssistant) -> None:
    with patch(CLIENT) as client, patch("custom_components.codie.async_setup_entry", return_value=True):
        inst = _mock(client)
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "1.2.3.4", "port": 8081})
        assert result["step_id"] == "pair"

        inst.pair_confirm.side_effect = CodieAuthError(401, {"code": "PAIRING_CODE_EXPIRED"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pairing_code": "111111"})
        assert result["errors"] == {"base": "code_expired"}
        assert inst.pair_request.call_count == 2

        inst.pair_confirm.side_effect = CodieApiError(400, {"code": "PAIRING_CODE_INVALID"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pairing_code": "222222"})
        assert result["errors"] == {"base": "invalid_code"}

        inst.pair_confirm.side_effect = None
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pairing_code": "123 456"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Codie (Peter)"
        assert result["data"]["local_secret"] == "s3cret"
        assert result["data"]["user_id"] == "u1"
        assert result["result"].unique_id == "m1"
        inst.pair_confirm.assert_called_with("c1", "123456", "permanent")


async def test_setup_and_reauth(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="m1", data={"host": "1.2.3.4", "port": 8081, "user_id": "u1", **CONFIRM}
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.codie.api.CodieClient.ping", AsyncMock(return_value=True)),
        patch("custom_components.codie.api.CodieClient.slot", AsyncMock(return_value={"slot_number": 2})),
        patch("custom_components.codie.api.CodieClient.device_state", AsyncMock(return_value={"locked": False})),
        patch("custom_components.codie.executor.ToolExecutor.run", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    states = {s.entity_id: s.state for s in hass.states.async_all() if "codie" in s.entity_id}
    print(states)
    assert "2" in states.values()
    assert hass.services.has_service(DOMAIN, "say")

    say = AsyncMock(return_value={"utterance_id": "u-1", "state": "queued"})
    with patch("custom_components.codie.api.CodieClient.say", say):
        resp = await hass.services.async_call(DOMAIN, "say", {"text": " Hallo "}, blocking=True, return_response=True)
    assert resp == {"utterance_id": "u-1", "state": "queued"}
    say.assert_called_with("Hallo")

    with patch("custom_components.codie.api.CodieClient.notify", AsyncMock(side_effect=CodieAuthError(401, {"code": "LOCAL_SECRET_MISMATCH"}))):
        try:
            await hass.services.async_call(DOMAIN, "notify", {"text": "x"}, blocking=True)
        except Exception as err:  # noqa: BLE001
            print("notify error:", type(err).__name__, err)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)

    with patch("custom_components.codie.executor.ToolExecutor.stop", AsyncMock()):
        assert await hass.config_entries.async_unload(entry.entry_id)
