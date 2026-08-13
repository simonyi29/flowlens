"""Back-pressured, in-memory bridge between an HTTP Range response and a worker WSS."""
from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_ROOT = (Path(__file__).resolve().parents[2] / "data" / "douyin" / "media").resolve()
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class MediaRelayOpenError(RuntimeError):
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


def safe_media_path(value: str | None) -> Path:
    if not value:
        raise FileNotFoundError("media file is unavailable")
    path = Path(value).resolve()
    if path == MEDIA_ROOT or MEDIA_ROOT not in path.parents:
        raise PermissionError("media path is outside the FlowLens library")
    if not path.is_file():
        raise FileNotFoundError("media file does not exist")
    return path


def parse_range(value: str | None, size: int) -> tuple[int, int]:
    if size <= 0:
        raise ValueError("empty media")
    if not value:
        return 0, size - 1
    match = RANGE_RE.fullmatch(value.strip())
    if not match or (not match.group(1) and not match.group(2)):
        raise ValueError("invalid byte range")
    start_text, end_text = match.groups()
    if not start_text:
        length = int(end_text)
        if length <= 0:
            raise ValueError("invalid byte range")
        start, end = max(size - length, 0), size - 1
    else:
        start = int(start_text)
        end = min(int(end_text) if end_text else size - 1, size - 1)
    if start < 0 or start >= size or start > end:
        raise ValueError("invalid byte range")
    return start, end


@dataclass
class RelaySession:
    stream_id: str
    worker_id: str
    asset_id: str
    range_header: str | None
    expires_at: float
    ready: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    queue: asyncio.Queue[bytes | None] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    metadata: dict | None = None


class MediaRelayBroker:
    def __init__(self, ttl_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self.sessions: dict[str, RelaySession] = {}
        self._worker_counts: dict[str, int] = {}

    def create(self, worker_id: str, asset_id: str, range_header: str | None) -> RelaySession:
        if self._worker_counts.get(worker_id, 0) >= 2:
            raise RuntimeError("worker media stream limit reached")
        stream_id = secrets.token_urlsafe(24)
        session = RelaySession(stream_id, worker_id, asset_id, range_header, time.monotonic() + self.ttl_seconds)
        self.sessions[stream_id] = session
        self._worker_counts[worker_id] = self._worker_counts.get(worker_id, 0) + 1
        return session

    def get(self, stream_id: str, worker_id: str) -> RelaySession | None:
        item = self.sessions.get(stream_id)
        if not item or item.worker_id != worker_id or time.monotonic() >= item.expires_at:
            return None
        return item

    def close(self, stream_id: str) -> None:
        item = self.sessions.pop(stream_id, None)
        if item:
            self._worker_counts[item.worker_id] = max(self._worker_counts.get(item.worker_id, 1) - 1, 0)


media_relay_broker = MediaRelayBroker()
