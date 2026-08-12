"""Douyin native-caption and optional local-ASR processing service."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import config
from model.m_douyin import DouyinTranscript, DouyinTranscriptSegment
from model.m_douyin import DouyinCrawlCheckpoint
from database.douyin_state import load_checkpoint, save_checkpoint
from store import douyin as douyin_store
from tools import utils


MediaDownloader = Callable[[str], Awaitable[bytes | None]]


def _timestamp(ms: int) -> str:
    hours, remainder = divmod(max(ms, 0), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def segments_to_srt(segments: Iterable[DouyinTranscriptSegment]) -> str:
    return "\n\n".join(
        f"{index}\n{_timestamp(segment.start_ms)} --> {_timestamp(segment.end_ms)}\n{segment.text.strip()}"
        for index, segment in enumerate(segments, 1)
        if segment.text.strip()
    )


def parse_caption_payload(payload: bytes | str | dict | list) -> list[DouyinTranscriptSegment]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig", errors="replace")
    if isinstance(payload, str):
        stripped = payload.strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            blocks = re.split(r"\n\s*\n", stripped.replace("\r\n", "\n"))
            parsed = []
            pattern = re.compile(
                r"(?:(?:\d+)\n)?"
                r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{3})\s*-->\s*"
                r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{3})[^\n]*\n(.+)",
                re.S,
            )
            for block in blocks:
                match = pattern.search(block.strip())
                if not match:
                    continue
                values = [int(value or 0) for value in match.groups()[:8]]
                start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
                end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
                parsed.append(DouyinTranscriptSegment(start_ms=start, end_ms=end, text=match.group(9).strip()))
            return parsed

    if isinstance(payload, dict):
        payload = (
            payload.get("utterances") or payload.get("segments") or payload.get("captions")
            or payload.get("data") or []
        )
        if isinstance(payload, dict):
            payload = payload.get("list") or payload.get("utterances") or []
    if not isinstance(payload, list):
        return []

    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or item.get("utterance") or "").strip()
        if not text:
            continue
        start = item.get("start_time") or item.get("start_ms") or item.get("start") or 0
        end = item.get("end_time") or item.get("end_ms") or item.get("end") or start
        try:
            start_ms, end_ms = int(float(start)), int(float(end))
        except (TypeError, ValueError):
            continue
        result.append(DouyinTranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text))
    return result


def find_native_caption(aweme: dict[str, Any]) -> tuple[Any, str]:
    """Return an inline caption payload or URL and its language."""
    candidates = []
    video = aweme.get("video") or {}
    for value in (
        aweme.get("caption_infos"), aweme.get("subtitle_list"), aweme.get("captions"),
        video.get("caption_infos"), video.get("subtitle_list"), video.get("cla_info"),
    ):
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        language = str(candidate.get("language") or candidate.get("language_code") or "zh")
        inline = candidate.get("utterances") or candidate.get("segments") or candidate.get("captions")
        if inline:
            return inline, language
        url = candidate.get("url") or candidate.get("caption_url") or candidate.get("subtitle_url")
        if isinstance(url, dict):
            urls = url.get("url_list") or []
            url = urls[-1] if urls else ""
        if url:
            return str(url), language
    return None, "zh"


class DouyinTranscriptService:
    def __init__(self, downloader: MediaDownloader):
        self.downloader = downloader
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=20)
        self.worker_task: asyncio.Task | None = None
        self.model = None
        self._close_lock = asyncio.Lock()
        self._closing = False

    async def start(self) -> None:
        if self.worker_task is None:
            self.worker_task = asyncio.create_task(self._worker())

    async def enqueue(self, aweme: dict[str, Any]) -> None:
        if self._closing:
            raise RuntimeError("transcript service is closing")
        aweme_id = str(aweme.get("aweme_id") or "")
        checkpoint = await load_checkpoint("transcript", aweme_id)
        await self.start()
        await douyin_store.save_transcript(
            DouyinTranscript(
                aweme_id=aweme_id, status="pending",
                retry_count=checkpoint.collected_count if checkpoint else 0,
            )
        )
        await self.queue.put(aweme)

    async def drain_and_close(self) -> None:
        async with self._close_lock:
            if self.worker_task is None:
                return
            self._closing = True
            await self.queue.join()
            await self.queue.put(None)
            await self.worker_task
            self.worker_task = None

    async def cancel_and_close(self) -> None:
        """Cancel active ASR and persist queued transcript jobs as retryable failures."""
        async with self._close_lock:
            if self.worker_task is None:
                return
            self._closing = True
            while True:
                try:
                    item = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    if item is not None:
                        await self._save_failure(item, "task cancelled during shutdown")
                finally:
                    self.queue.task_done()
            self.worker_task.cancel()
            await asyncio.gather(self.worker_task, return_exceptions=True)
            self.worker_task = None

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                await self._process(item)
            except asyncio.CancelledError:
                if item:
                    await self._save_failure(item, "task cancelled")
                raise
            except Exception as exc:
                if item:
                    await self._save_failure(item, f"{type(exc).__name__}: {exc}")
            finally:
                self.queue.task_done()

    async def _process(self, aweme: dict[str, Any]) -> None:
        aweme_id = str(aweme.get("aweme_id") or "")
        native, language = find_native_caption(aweme)
        segments: list[DouyinTranscriptSegment] = []
        if native is not None and getattr(config, "DY_ENABLE_NATIVE_SUBTITLE", True):
            payload = await self.downloader(native) if isinstance(native, str) else native
            if payload:
                segments = parse_caption_payload(payload)
            if segments:
                await self._save_completed(aweme_id, segments, "native", language, "")
                return

        if not getattr(config, "DY_ENABLE_ASR", True):
            await douyin_store.save_transcript(
                DouyinTranscript(
                    aweme_id=aweme_id, status="not_available",
                    processed_at=utils.get_current_timestamp(),
                )
            )
            await save_checkpoint(
                DouyinCrawlCheckpoint(
                    scope="transcript", scope_id=aweme_id, status="complete",
                    collected_count=0, updated_at=utils.get_current_timestamp(),
                )
            )
            return
        video_url = self._video_url(aweme)
        if not video_url:
            raise RuntimeError("video download URL is unavailable")
        media = await self.downloader(video_url)
        if not media:
            raise RuntimeError("video download failed")

        temp_path = None
        try:
            temp_dir = Path("data/douyin/tmp")
            temp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".mp4", delete=False) as file:
                file.write(media)
                temp_path = Path(file.name)
            segments = await asyncio.to_thread(self._transcribe, temp_path)
            if not segments:
                raise RuntimeError("ASR returned no speech segments")
            await self._save_completed(
                aweme_id, segments, "asr", getattr(config, "DY_ASR_LANGUAGE", "zh"),
                getattr(config, "DY_ASR_MODEL", "small"),
            )
        finally:
            if temp_path and temp_path.exists() and not getattr(config, "DY_KEEP_MEDIA", False):
                temp_path.unlink(missing_ok=True)

    def _transcribe(self, media_path: Path) -> list[DouyinTranscriptSegment]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("ASR is enabled but faster-whisper is not installed; install with `uv sync --extra asr`") from exc
        if self.model is None:
            device = "auto"
            compute_type = "float16"
            try:
                import ctranslate2
                if not ctranslate2.get_cuda_device_count():
                    device, compute_type = "cpu", "int8"
            except ImportError:
                compute_type = "default"
            self.model = WhisperModel(
                getattr(config, "DY_ASR_MODEL", "small"), device=device, compute_type=compute_type
            )
        result, _ = self.model.transcribe(
            str(media_path), language=getattr(config, "DY_ASR_LANGUAGE", "zh") or None
        )
        return [
            DouyinTranscriptSegment(
                start_ms=round(segment.start * 1000), end_ms=round(segment.end * 1000),
                text=segment.text.strip(),
            )
            for segment in result if segment.text.strip()
        ]

    async def _save_completed(self, aweme_id, segments, source, language, model_name) -> None:
        output_dir = Path("data/douyin/transcripts")
        output_dir.mkdir(parents=True, exist_ok=True)
        srt_path = output_dir / f"{aweme_id}.srt"
        srt_path.write_text(segments_to_srt(segments), encoding="utf-8")
        await douyin_store.save_transcript(
            DouyinTranscript(
                aweme_id=aweme_id, source=source, language=language,
                full_text="\n".join(segment.text for segment in segments), segments=segments,
                srt_path=srt_path.as_posix(), model_name=model_name,
                status="native_completed" if source == "native" else "asr_completed",
                processed_at=utils.get_current_timestamp(),
            )
        )
        await save_checkpoint(
            DouyinCrawlCheckpoint(
                scope="transcript", scope_id=aweme_id, status="complete",
                collected_count=0, updated_at=utils.get_current_timestamp(),
            )
        )

    async def _save_failure(self, aweme: dict[str, Any], error: str) -> None:
        aweme_id = str(aweme.get("aweme_id") or "")
        checkpoint = await load_checkpoint("transcript", aweme_id)
        retries = (checkpoint.collected_count if checkpoint else 0) + 1
        await douyin_store.save_transcript(
            DouyinTranscript(
                aweme_id=aweme_id, status="failed",
                error_message=error, retry_count=retries,
                processed_at=utils.get_current_timestamp(),
            )
        )
        await save_checkpoint(
            DouyinCrawlCheckpoint(
                scope="transcript", scope_id=aweme_id, status="partial",
                collected_count=retries, last_error=error,
                updated_at=utils.get_current_timestamp(),
            )
        )

    @staticmethod
    def _video_url(aweme: dict[str, Any]) -> str:
        video = aweme.get("video") or {}
        for key in ("play_addr_h264", "play_addr_256", "play_addr"):
            urls = (video.get(key) or {}).get("url_list") or []
            if urls:
                return str(urls[-1])
        return ""
