"""Security helpers shared by the remote worker and its local control API."""
from __future__ import annotations

from typing import Any

SENSITIVE_KEYS = {
    "authorization", "cookie", "cookies", "set-cookie", "token", "access_token",
    "refresh_token", "signature", "proxy_password", "static_proxy_url", "sec_uid",
    "user_id", "uid", "cdp_ws_url", "websocketdebuggerurl", "raw_payload",
}


def sanitize_worker_payload(value: Any) -> Any:
    """Recursively remove credentials and original account identifiers."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_worker_payload(item)
            for key, item in value.items()
            if str(key).lower().replace("-", "_") not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [sanitize_worker_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_worker_payload(item) for item in value]
    return value

