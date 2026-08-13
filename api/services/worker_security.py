"""Security helpers shared by the remote worker and its local control API."""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {
    "authorization", "cookie", "cookies", "set-cookie", "token", "access_token",
    "refresh_token", "signature", "proxy_password", "static_proxy_url", "sec_uid",
    "sec_user_id", "user_id", "uid", "author_id", "cdp_ws_url", "websocketdebuggerurl",
    "raw_payload", "path", "local_path", "profile_path", "part_path", "srt_path",
    "avatar", "avatar_url", "ip_location", "gender", "homepage_url",
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
    if isinstance(value, str):
        value = re.sub(r"(?i)(cookie|authorization|token|signature)\s*[:=]\s*[^\s,;]+", r"\1=***", value)
        value = re.sub(r"(?i)wss?://[^\s]+", "[redacted-websocket]", value)
        value = re.sub(r"(?i)https?://(?:127\.0\.0\.1|localhost):\d+[^\s]*", "[redacted-local-endpoint]", value)
        return value
    return value
