"""Persistent executor WebSocket for Codie external tools.

Depends only on aiohttp and the API client; the actual tool logic is injected as a handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
import logging
import random
from typing import Any

import aiohttp

from .api import CodieApiError, CodieAuthError, CodieClient, CodieError

_LOGGER = logging.getLogger(__name__)

# From openapi.json x-websockets.tool-executor.limits
PING_SECONDS = 20
PONG_TIMEOUT_SECONDS = 60
MESSAGE_BYTES = 65536
RESULT_DATA_BYTES = 16384
PENDING_PER_INTEGRATION = 3

RECONNECT_MIN = 2.0
RECONNECT_MAX = 300.0


@dataclass
class ToolResult:
    """Result returned by a tool handler."""

    status: str  # succeeded | failed | needs_clarification
    data: dict[str, Any]
    code: str | None = None


ToolHandler = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


@dataclass
class ExecutorState:
    """Observable executor state."""

    connected: bool = False
    registered: bool = False
    last_error: str | None = None
    invocations: int = 0
    pending: set[asyncio.Task] = field(default_factory=set)


class ToolExecutor:
    """Registers a tool catalog and keeps the executor WebSocket online."""

    def __init__(
        self,
        client: CodieClient,
        session: aiohttp.ClientSession,
        integration_id: str,
        catalog: dict[str, Any],
        handler: ToolHandler,
        *,
        on_state_change: Callable[[], None] | None = None,
        on_auth_error: Callable[[], None] | None = None,
    ) -> None:
        """Initialize."""
        self._client = client
        self._session = session
        self.integration_id = integration_id
        self._catalog = catalog
        self._handler = handler
        self._on_state_change = on_state_change
        self._on_auth_error = on_auth_error
        self.state = ExecutorState()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._stopping = False
        self._wakeup = asyncio.Event()

    @property
    def path(self) -> str:
        """Signed WebSocket path."""
        return f"/v1/ws/tool-integrations/{self.integration_id}/executor"

    def _set(self, **changes: Any) -> None:
        changed = False
        for key, value in changes.items():
            if getattr(self.state, key) != value:
                setattr(self.state, key, value)
                changed = True
        if changed and self._on_state_change:
            self._on_state_change()

    async def register(self) -> None:
        """PUT the tool catalog (replaces it on the device)."""
        data = await self._client.put_tool_integration(self.integration_id, self._catalog)
        _LOGGER.debug("Registered Codie tool catalog revision %s", data.get("revision"))
        self._set(registered=True)

    def reconnect_now(self) -> None:
        """Skip the current backoff delay (e.g. when the device came back online)."""
        self._wakeup.set()

    async def stop(self) -> None:
        """Close the WebSocket and stop reconnecting."""
        self._stopping = True
        self._wakeup.set()
        for task in list(self.state.pending):
            task.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._set(connected=False)

    async def run(self) -> None:
        """Run forever: register, connect, serve, reconnect with exponential backoff."""
        delay = RECONNECT_MIN
        need_register = True
        while not self._stopping:
            try:
                if need_register:
                    await self.register()
                    need_register = False
                await self._connect_and_serve()
                delay = RECONNECT_MIN  # clean session end -> quick reconnect
            except CodieAuthError as err:
                self._set(connected=False, last_error=str(err))
                _LOGGER.warning("Codie rejected credentials for tool executor: %s", err)
                if self._on_auth_error:
                    self._on_auth_error()
                return
            except aiohttp.WSServerHandshakeError as err:
                self._set(connected=False, last_error=f"handshake {err.status}")
                if err.status == 401:
                    # Could be a stale timestamp; verify credentials with a signed request.
                    try:
                        await self._client.slot()
                    except CodieAuthError:
                        if self._on_auth_error:
                            self._on_auth_error()
                        return
                    except CodieError:
                        pass
                if err.status in (404, 409, 410):
                    need_register = True
                _LOGGER.debug("Codie executor handshake failed: %s", err)
            except (CodieError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                self._set(connected=False, last_error=str(err))
                _LOGGER.debug("Codie executor disconnected: %s", err)
                if isinstance(err, CodieApiError) and err.status == 404:
                    need_register = True
            if self._stopping:
                break
            sleep_for = delay * (0.8 + random.random() * 0.4)
            _LOGGER.debug("Reconnecting Codie executor in %.1fs", sleep_for)
            self._wakeup.clear()
            try:
                await asyncio.wait_for(self._wakeup.wait(), sleep_for)
            except (TimeoutError, asyncio.TimeoutError):
                pass
            delay = min(delay * 2, RECONNECT_MAX)

    async def _connect_and_serve(self) -> None:
        url, params = self._client.ws_url_and_params(self.path)
        # The server pings every 20 s; aiohttp answers pings automatically (autoping).
        # If nothing (ping or data) arrives for PONG_TIMEOUT_SECONDS + margin, treat it as dead.
        # Our own heartbeat additionally detects half-open TCP connections.
        async with self._session.ws_connect(
            url,
            params=params,
            heartbeat=PING_SECONDS,
            autoping=True,
            max_msg_size=MESSAGE_BYTES * 2,
            timeout=aiohttp.ClientWSTimeout(ws_close=10),
        ) as ws:
            self._ws = ws
            first = await ws.receive(timeout=30)
            if first.type != aiohttp.WSMsgType.TEXT:
                raise CodieError(f"Unexpected first executor frame: {first.type}")
            ready = json.loads(first.data)
            if ready.get("type") != "ready":
                raise CodieError(f"Executor did not send ready: {ready}")
            _LOGGER.info("Codie tool executor online")
            self._set(connected=True, last_error=None)
            try:
                while not ws.closed:
                    msg = await ws.receive(timeout=PONG_TIMEOUT_SECONDS + 30)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._on_message(msg.data)
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        _LOGGER.debug("Executor closed by device: %s %s", ws.close_code, msg.extra)
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise CodieError(f"Executor WebSocket error: {ws.exception()}")
            finally:
                self._ws = None
                self._set(connected=False)

    async def _on_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            _LOGGER.warning("Invalid JSON from Codie executor")
            return
        kind = message.get("type")
        if kind == "invoke":
            call = message.get("data") or {}
            if len(self.state.pending) >= PENDING_PER_INTEGRATION * 2:
                _LOGGER.warning("Too many pending Codie tool calls; dropping %s", call.get("invocation_id"))
                return
            task = asyncio.create_task(self._invoke(call))
            self.state.pending.add(task)
            task.add_done_callback(self.state.pending.discard)
        elif kind == "error":
            data = message.get("data") or {}
            _LOGGER.warning("Codie executor protocol error: %s %s", data.get("code"), data.get("message", ""))
        else:
            _LOGGER.debug("Ignoring executor message type %s", kind)

    async def _invoke(self, call: dict[str, Any]) -> None:
        invocation_id = call.get("invocation_id")
        name = call.get("name", "")
        arguments = call.get("arguments") or {}
        timeout_s = max(0.1, (call.get("timeout_ms") or 5000) / 1000 - 0.3)
        self.state.invocations += 1
        _LOGGER.debug("Codie tool call %s(%s) [%s]", name, arguments, invocation_id)
        try:
            result = await asyncio.wait_for(self._handler(name, arguments), timeout_s)
        except (TimeoutError, asyncio.TimeoutError):
            result = ToolResult("failed", {"speech": "Home Assistant hat nicht rechtzeitig geantwortet."}, "TIMEOUT")
        except Exception:  # noqa: BLE001 - never leave the device waiting
            _LOGGER.exception("Codie tool %s failed", name)
            result = ToolResult("failed", {"speech": "Bei der Ausführung ist ein Fehler aufgetreten."}, "INTERNAL_ERROR")

        payload: dict[str, Any] = {"invocation_id": invocation_id, "status": result.status, "data": result.data}
        if result.code:
            payload["code"] = result.code
        encoded = json.dumps({"type": "result", "data": payload}, ensure_ascii=False)
        if len(json.dumps(result.data, ensure_ascii=False).encode()) > RESULT_DATA_BYTES:
            payload["data"] = {"speech": result.data.get("speech", "")[:200]}
            encoded = json.dumps({"type": "result", "data": payload}, ensure_ascii=False)
        ws = self._ws
        if ws is None or ws.closed:
            _LOGGER.debug("Executor disconnected before result for %s could be sent", invocation_id)
            return
        async with self._send_lock:
            await ws.send_str(encoded)
        _LOGGER.debug("Codie tool result %s: %s", invocation_id, result.status)
