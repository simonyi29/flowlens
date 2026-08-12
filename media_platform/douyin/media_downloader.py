"""Validated, resumable permanent media downloads for Douyin."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

import config


class MediaDownloadError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        super().__init__(f"{error_type}: {message}")


@dataclass
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str
    mime_type: str
    source_url: str
    resumed: bool


def video_candidates(aweme: dict[str, Any]) -> list[str]:
    video = aweme.get("video") or {}
    result: list[str] = []
    for key in ("play_addr_h264", "play_addr_256", "play_addr", "download_addr"):
        for url in (video.get(key) or {}).get("url_list") or []:
            value = str(url or "")
            if value and value not in result:
                result.append(value)
    return result


class MediaQuota:
    def __init__(self, root: Path, task_limit: int, library_limit: int, min_free: int):
        self.root, self.task_limit, self.library_limit, self.min_free = root, task_limit, library_limit, min_free
        self.task_bytes = 0

    def library_bytes(self) -> int:
        if not self.root.exists(): return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".part"))

    def ensure_available(self, expected: int = 0) -> None:
        free = shutil.disk_usage(self.root.parent if self.root.parent.exists() else Path.cwd()).free
        if free - expected < self.min_free:
            raise MediaDownloadError("disk_space_low", "minimum free disk space would be exceeded")
        if self.task_bytes + expected > self.task_limit or self.library_bytes() + expected > self.library_limit:
            raise MediaDownloadError("disk_quota_reached", "media byte quota would be exceeded")


class PermanentMediaDownloader:
    def __init__(self, root: Path = Path("data/douyin/media"), client: httpx.AsyncClient | None = None,
                 *, headers: dict[str, str] | None = None, proxy: str | None = None):
        self.root = root
        self.client = client or httpx.AsyncClient(follow_redirects=True, timeout=60, headers=headers, proxy=proxy)
        self._owns_client = client is None
        self.quota = MediaQuota(root, int(config.DY_MAX_MEDIA_TOTAL_BYTES), int(config.DY_MEDIA_LIBRARY_MAX_BYTES), int(config.DY_MIN_FREE_DISK_BYTES))

    async def close(self):
        if self._owns_client: await self.client.aclose()

    async def download(self, urls: list[str], destination: Path, *, verify: bool = True) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and config.DY_SKIP_EXISTING_MEDIA:
            return self._result(destination, "", "application/octet-stream", False)
        self.quota.ensure_available()
        errors = []
        for url in urls:
            for attempt in range(3):
                try:
                    return await self._download_one(url, destination, verify)
                except (httpx.HTTPError, MediaDownloadError) as exc:
                    # Signed media URLs must never flow into task logs.
                    if isinstance(exc, MediaDownloadError):
                        errors.append(f"{exc.error_type}: download attempt failed")
                    else:
                        errors.append(f"{type(exc).__name__}: network attempt failed")
                    if isinstance(exc, MediaDownloadError) and exc.error_type in {"disk_space_low", "disk_quota_reached"}:
                        raise
                    await asyncio.sleep(2 ** attempt)
        raise MediaDownloadError("media_url_expired", "; ".join(errors[-3:]) or "no media URL")

    async def _download_one(self, url: str, destination: Path, verify: bool) -> DownloadResult:
        part = destination.with_suffix(destination.suffix + ".part")
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        async with self.client.stream("GET", url, headers=headers) as response:
            if response.status_code >= 400:
                raise MediaDownloadError("media_url_expired", f"HTTP {response.status_code}")
            if offset and response.status_code != 206:
                offset = 0
                part.unlink(missing_ok=True)
            length = int(response.headers.get("content-length") or 0)
            self.quota.ensure_available(length)
            mime = response.headers.get("content-type", "application/octet-stream").split(";")[0]
            mode = "ab" if offset else "wb"
            with part.open(mode) as fh:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    self.quota.ensure_available(len(chunk))
                    fh.write(chunk)
                    self.quota.task_bytes += len(chunk)
        if verify: self._verify(part, mime)
        os.replace(part, destination)
        return self._result(destination, url, mime, bool(offset))

    def _verify(self, path: Path, mime: str):
        if path.stat().st_size == 0: raise MediaDownloadError("media_invalid", "empty media")
        with path.open("rb") as fh:
            head = fh.read(32).lstrip().lower()
        if mime in {"text/html", "application/json"} or head.startswith((b"<html", b"<!doctype", b"{")):
            raise MediaDownloadError("media_invalid", "response is not media")
        ffprobe = shutil.which("ffprobe")
        if ffprobe and path.suffix.lower() in {".mp4", ".mov", ".mkv"}:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode or "video" not in result.stdout:
                raise MediaDownloadError("media_invalid", "ffprobe found no video track")

    @staticmethod
    def _result(path: Path, url: str, mime: str, resumed: bool) -> DownloadResult:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""): digest.update(chunk)
        return DownloadResult(path, path.stat().st_size, digest.hexdigest(), mime, url, resumed)
