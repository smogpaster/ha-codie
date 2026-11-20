"""Async client for the Codie (CODE27) local HTTP API, firmware 1.6.0.

This module depends only on aiohttp so it can be used and tested outside Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import unquote
import uuid

import aiohttp

API_BASE = "/v1/api"
EMPTY_BODY_HASH = hashlib.sha256(b"").hexdigest()

# 401 codes that are transient and must not trigger re-authentication.
_TRANSIENT_401 = {"REQUEST_EXPIRED", "NONCE_REUSED"}


class CodieError(Exception):
    """Base error for the Codie API."""


class CodieConnectionError(CodieError):
    """The device could not be reached."""


class CodieApiError(CodieError):
    """The device answered with an error."""

    def __init__(self, status: int, payload: Any) -> None:
        """Initialize."""
        self.status = status
        self.payload = payload
        self.code = payload.get("code") if isinstance(payload, dict) else None
        self.reason = payload.get("reason") if isinstance(payload, dict) else None
        message = (payload.get("message") or "") if isinstance(payload, dict) else str(payload)
        super().__init__(" ".join(part for part in (f"HTTP {status}", self.code, message) if part))


class CodieAuthError(CodieApiError):
    """Credentials are invalid or expired; pairing must be repeated."""


@dataclass
class CodieCredentials:
    """Pairing credentials."""

    local_secret: str
    user_id: str
    local_session_id: str = ""
    local_session_expires_at_ms: int = 0
    auth_scope: str = "permanent"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodieCredentials:
        """Build from stored config entry data."""
        return cls(
            local_secret=data["local_secret"],
            user_id=data.get("user_id", ""),
            local_session_id=data.get("local_session_id") or "",
            local_session_expires_at_ms=int(data.get("local_session_expires_at_ms") or 0),
            auth_scope=data.get("auth_scope") or "permanent",
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize for config entry storage."""
        return {
            "local_secret": self.local_secret,
            "user_id": self.user_id,
            "local_session_id": self.local_session_id,
            "local_session_expires_at_ms": self.local_session_expires_at_ms,
            "auth_scope": self.auth_scope,
        }


def _dumps(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


class CodieClient:
    """Codie local API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        credentials: CodieCredentials | None = None,
        *,
        timeout: float = 15,
    ) -> None:
        """Initialize."""
        self._session = session
        self.host = host
        self.port = port
        self.credentials = credentials
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def base_url(self) -> str:
        """HTTP base URL without API path."""
        return f"http://{self.host}:{self.port}"

    # ------------------------------------------------------------------ signing
    def sign(self, method: str, path: str, body: bytes = b"", mode: str = "empty") -> dict[str, str]:
        """Return signature headers (GUIDE section 4). A new nonce is used on every call."""
        if self.credentials is None:
            raise CodieAuthError(401, {"code": "NOT_PAIRED", "message": "Device is not paired"})
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())
        if mode == "multipart":
            body_hash = "UNSIGNED-PAYLOAD"
        else:
            body_hash = hashlib.sha256(body).hexdigest()
        user_id = self.credentials.user_id
        canonical = "|".join(
            (method.upper(), unquote(path.split("?", 1)[0]), timestamp, nonce, mode, body_hash, user_id)
        )
        signature = hmac.new(
            self.credentials.local_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers = {
            "X-Local-Sig": signature,
            "X-Local-Timestamp": timestamp,
            "X-Local-Nonce": nonce,
            "X-Local-Payload-Mode": mode,
            "X-Local-Body-Hash": body_hash,
            "X-Local-User-Id": user_id,
        }
        if self.credentials.local_session_id:
            headers["X-Local-Session-Id"] = self.credentials.local_session_id
        return headers

    def ws_url_and_params(self, path: str) -> tuple[str, dict[str, str]]:
        """Return the WebSocket URL and signed query parameters for a signed GET handshake."""
        headers = self.sign("GET", path)
        params = {
            "sig": headers["X-Local-Sig"],
            "timestamp": headers["X-Local-Timestamp"],
            "nonce": headers["X-Local-Nonce"],
            "payload_mode": headers["X-Local-Payload-Mode"],
            "body_hash": headers["X-Local-Body-Hash"],
            "user_id": headers["X-Local-User-Id"],
        }
        if "X-Local-Session-Id" in headers:
            params["session_id"] = headers["X-Local-Session-Id"]
        return f"ws://{self.host}:{self.port}{path}", params

    # ----------------------------------------------------------------- requests
    async def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        public: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a (signed) JSON request. ``path`` is relative to /v1/api."""
        full_path = API_BASE + path
        body = b"" if payload is None else _dumps(payload)
        mode = "empty" if payload is None else "json"
        headers: dict[str, str] = {} if public else self.sign(method, full_path, body, mode)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return await self._send(
            method,
            full_path,
            data=body if payload is not None else None,
            headers=headers,
            timeout=timeout,
        )

    async def _send(
        self,
        method: str,
        full_path: str,
        *,
        data: Any,
        headers: dict[str, str],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        client_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else self._timeout
        try:
            async with self._session.request(
                method,
                self.base_url + full_path,
                data=data,
                headers=headers,
                timeout=client_timeout,
            ) as response:
                raw = await response.read()
                status = response.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CodieConnectionError(f"Cannot reach Codie at {self.host}:{self.port}: {err}") from err

        result: Any
        if not raw:
            result = {}
        else:
            try:
                result = json.loads(raw)
            except ValueError:
                result = {"message": raw.decode(errors="replace")}
        if status >= 400:
            error = result if isinstance(result, dict) else {"message": str(result)}
            # Some endpoints nest the error object.
            if isinstance(error.get("error"), dict) and "code" not in error:
                error = {**error, **error["error"]}
            if status == 401 and error.get("code") not in _TRANSIENT_401:
                raise CodieAuthError(status, error)
            raise CodieApiError(status, error)
        return result if isinstance(result, dict) else {"data": result}

    # ------------------------------------------------------------------ public
    async def ping(self) -> bool:
        """Return True if the device answers the public ping."""
        result = await self.request("GET", "/auth/ping", public=True, timeout=5)
        return bool(result.get("success"))

    async def bootstrap(self) -> dict[str, Any]:
        """Return bootstrap data (user_id, login state, languages)."""
        return (await self.request("GET", "/auth/bootstrap", public=True))["data"]

    async def pair_request(self) -> dict[str, Any]:
        """Show a pairing code on the device; returns challenge_id and expires_at_ms."""
        return (await self.request("POST", "/auth/pair/request", public=True))["data"]

    async def pair_confirm(
        self, challenge_id: str, pairing_code: str, auth_scope: str = "permanent"
    ) -> dict[str, Any]:
        """Confirm the pairing code and return raw credential data."""
        payload = {"challenge_id": challenge_id, "pairing_code": pairing_code, "auth_scope": auth_scope}
        return (await self.request("POST", "/auth/pair/confirm", payload, public=True))["data"]

    async def pair_cancel(self, challenge_id: str) -> None:
        """Cancel a pending pairing request."""
        await self.request("POST", "/auth/pair/cancel", {"challenge_id": challenge_id}, public=True)

    # ------------------------------------------------------------------ signed
    async def system(self) -> dict[str, Any]:
        """Return device and user information."""
        return (await self.request("GET", "/data/user/system"))["data"]

    async def slot(self) -> dict[str, Any]:
        """Return the current character slot."""
        return (await self.request("GET", "/data/user/slot"))["data"]

    async def device_state(self) -> dict[str, Any]:
        """Return screen/lock/conversation state."""
        return (await self.request("GET", "/data/user/device/state"))["data"]

    async def say(self, text: str, slot: int | None = None) -> dict[str, Any]:
        """Read text aloud with the current character voice (TTS utterance)."""
        if slot is None:
            slot = int((await self.slot())["slot_number"])
        payload = {"request_id": str(uuid.uuid4()), "slot": slot, "text": text}
        return (await self.request("POST", "/data/user/tts/utterances", payload))["data"]

    async def utterance(self, utterance_id: str) -> dict[str, Any]:
        """Return utterance task state."""
        return (await self.request("GET", f"/data/user/tts/utterances/{utterance_id}"))["data"]

    async def notify(self, text: str) -> None:
        """Queue a short asynchronous result to be spoken when the device is idle."""
        await self.request("POST", "/data/user/agent/results", {"text": text})

    async def create_media_session(
        self,
        content: bytes,
        content_type: str,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Upload media and create a temporary media session (multipart, unsigned payload)."""
        full_path = API_BASE + "/data/user/media/sessions"
        form = aiohttp.FormData()
        form.add_field("request", _dumps(options).decode(), content_type="application/json")
        form.add_field("file", content, filename="media", content_type=content_type)
        headers = self.sign("POST", full_path, mode="multipart")
        result = await self._send("POST", full_path, data=form, headers=headers, timeout=60)
        return result.get("data", {})

    async def get_tool_integration(self, integration_id: str) -> dict[str, Any] | None:
        """Return a registered tool integration or None."""
        try:
            return (await self.request("GET", f"/data/user/tool-integrations/{integration_id}"))["data"]
        except CodieApiError as err:
            if err.status == 404 and not isinstance(err, CodieAuthError):
                return None
            raise

    async def put_tool_integration(self, integration_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
        """Register or replace a tool catalog, handling the optimistic revision lock once."""
        path = f"/data/user/tool-integrations/{integration_id}"
        for attempt in range(2):
            current = await self.get_tool_integration(integration_id)
            body = {**catalog, "expected_revision": current["revision"] if current else 0}
            try:
                return (await self.request("PUT", path, body))["data"]
            except CodieApiError as err:
                if err.status != 409 or attempt:
                    raise
        raise CodieError("unreachable")

    async def delete_tool_integration(self, integration_id: str) -> None:
        """Delete a tool integration (idempotent)."""
        await self.request("DELETE", f"/data/user/tool-integrations/{integration_id}")
