"""Constants for the Codie integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "codie"

DEFAULT_PORT: Final = 8081
API_BASE: Final = "/v1/api"

CONF_LOCAL_SECRET: Final = "local_secret"
CONF_LOCAL_SESSION_ID: Final = "local_session_id"
CONF_LOCAL_SESSION_EXPIRES: Final = "local_session_expires_at_ms"
CONF_AUTH_SCOPE: Final = "auth_scope"
CONF_USER_ID: Final = "user_id"
CONF_DEVICE_ID: Final = "device_id"

# Fixed, lowercase UUID identifying this Home Assistant tool integration on the device.
TOOL_INTEGRATION_ID: Final = "29b3f267-7fe0-5fcd-b2b4-8f04524501d0"
TOOL_INTEGRATION_NAME: Final = "Home Assistant"

SCAN_INTERVAL_SECONDS: Final = 30

# Executor WebSocket limits from openapi.json x-websockets.tool-executor.
WS_PING_SECONDS: Final = 20
WS_PONG_TIMEOUT_SECONDS: Final = 60
WS_MESSAGE_BYTES: Final = 65536
WS_RESULT_DATA_BYTES: Final = 16384
TOOL_TIMEOUT_MS: Final = 10000

RECONNECT_MIN_SECONDS: Final = 2
RECONNECT_MAX_SECONDS: Final = 300

TTS_MAX_CHARS: Final = 16000
NOTIFY_MAX_CHARS: Final = 200
IMAGE_MAX_BYTES: Final = 8 * 1024 * 1024

SIGNAL_EXECUTOR_STATE: Final = f"{DOMAIN}_executor_state_{{}}"
