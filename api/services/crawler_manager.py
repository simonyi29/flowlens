# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/services/crawler_manager.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import subprocess
import signal
import os
from typing import Optional, List
from datetime import datetime
from pathlib import Path
import json
import sys

from ..schemas import CrawlerStartRequest, LogEntry
from .task_store import task_store


class CrawlerManager:
    """Crawler process manager"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.status = "idle"
        self.started_at: Optional[datetime] = None
        self.current_config: Optional[CrawlerStartRequest] = None
        self.current_run_id: Optional[str] = None
        self._detected_error_type: Optional[str] = None
        self._log_id = 0
        self._logs: List[LogEntry] = []
        self._read_task: Optional[asyncio.Task] = None
        self._queued_configs: dict[str, CrawlerStartRequest] = {}
        # Project root directory
        self._project_root = Path(__file__).parent.parent.parent
        # Log queue - for pushing to WebSocket
        self._log_queue: Optional[asyncio.Queue] = None

    @property
    def logs(self) -> List[LogEntry]:
        return self._logs

    def get_log_queue(self) -> asyncio.Queue:
        """Get or create log queue"""
        if self._log_queue is None:
            self._log_queue = asyncio.Queue()
        return self._log_queue

    def _create_log_entry(self, message: str, level: str = "info") -> LogEntry:
        """Create log entry"""
        self._log_id += 1
        entry = LogEntry(
            id=self._log_id,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            message=message
        )
        self._logs.append(entry)
        # Keep last 500 logs
        if len(self._logs) > 500:
            self._logs = self._logs[-500:]
        return entry

    async def _push_log(self, entry: LogEntry):
        if self.current_run_id:
            await task_store.add_log(self.current_run_id, entry.level, entry.message)
        """Push log to queue"""
        if self._log_queue is not None:
            try:
                self._log_queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def _parse_log_level(self, line: str) -> str:
        """Parse log level"""
        line_upper = line.upper()
        if "ERROR" in line_upper or "FAILED" in line_upper:
            return "error"
        elif "WARNING" in line_upper or "WARN" in line_upper:
            return "warning"
        elif "SUCCESS" in line_upper or "完成" in line or "成功" in line:
            return "success"
        elif "DEBUG" in line_upper:
            return "debug"
        return "info"

    async def start(self, config: CrawlerStartRequest, run_id: str | None = None) -> str | None:
        """Start crawler process"""
        async with self._lock:
            if self.process and self.process.poll() is None:
                return None

            await task_store.initialize()
            if run_id is None:
                payload = config.model_dump(mode="json")
                payload["platform"] = config.platform.value
                payload["crawler_type"] = config.crawler_type.value
                run_id = await task_store.create_run(payload)

            # Clear old logs
            self._logs = []
            self._detected_error_type = None
            self._log_id = 0
            self.current_run_id = run_id

            # Clear pending queue (don't replace object to avoid WebSocket broadcast coroutine holding old queue reference)
            if self._log_queue is None:
                self._log_queue = asyncio.Queue()
            else:
                try:
                    while True:
                        self._log_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            # Build command line arguments
            cmd = self._build_command(config)

            # Log start information
            safe_cmd = list(cmd)
            for sensitive_flag in ("--cookies", "--static_proxy_url"):
                if sensitive_flag in safe_cmd:
                    value_index = safe_cmd.index(sensitive_flag) + 1
                    if value_index < len(safe_cmd):
                        safe_cmd[value_index] = "***"
            entry = self._create_log_entry(f"Starting crawler: {' '.join(safe_cmd)}", "info")
            await self._push_log(entry)

            try:
                # Start subprocess
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                )
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    bufsize=1,
                    cwd=str(self._project_root),
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "FLOWLENS_RUN_ID": run_id},
                    creationflags=creationflags,
                )

                self.status = "running"
                self.started_at = datetime.now()
                self.current_config = config
                await task_store.update_run(run_id, "running", stage="discover")

                entry = self._create_log_entry(
                    f"Crawler started on platform: {config.platform.value}, type: {config.crawler_type.value}",
                    "success"
                )
                await self._push_log(entry)

                # Start log reading task
                self._read_task = asyncio.create_task(self._read_output())

                return run_id
            except Exception as e:
                self.status = "error"
                await task_store.update_run(run_id, "failed", error_type="unknown", error_message=str(e))
                entry = self._create_log_entry(f"Failed to start crawler: {str(e)}", "error")
                await self._push_log(entry)
                return None

    async def enqueue(self, config: CrawlerStartRequest) -> str:
        """Persist a run first, then launch it when the exclusive CDP slot is free."""
        await task_store.initialize()
        payload = config.model_dump(mode="json")
        payload["platform"] = config.platform.value
        payload["crawler_type"] = config.crawler_type.value
        run_id = await task_store.create_run(payload)
        # Keep credentials only in memory. They are deliberately absent from tasks.sqlite.
        self._queued_configs[run_id] = config
        if not self.process or self.process.poll() is not None:
            await self.start_next_queued()
        return run_id

    async def start_next_queued(self) -> str | None:
        if self.process and self.process.poll() is None:
            return None
        item = await task_store.next_queued_run()
        if not item:
            return None
        run_id = item["run_id"]
        config = self._queued_configs.pop(run_id, None)
        if config is None:
            config = CrawlerStartRequest.model_validate(json.loads(item["config_json"]))
        return await self.start(config, run_id=run_id)

    async def stop(self, final_status: str = "partial") -> bool:
        """Stop crawler process"""
        async with self._lock:
            if not self.process or self.process.poll() is not None:
                return False

            self.status = "stopping"
            graceful_signal = (
                signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM
            )
            signal_name = "CTRL_BREAK" if os.name == "nt" else "SIGTERM"
            entry = self._create_log_entry(
                f"Sending {signal_name} to crawler process...", "warning"
            )
            await self._push_log(entry)

            try:
                self.process.send_signal(graceful_signal)

                # Wait for graceful exit (up to 15 seconds)
                for _ in range(30):
                    if self.process.poll() is not None:
                        break
                    await asyncio.sleep(0.5)

                # If still not exited, force kill
                if self.process.poll() is None:
                    entry = self._create_log_entry("Process not responding, sending SIGKILL...", "warning")
                    await self._push_log(entry)
                    self.process.kill()

                entry = self._create_log_entry("Crawler process terminated", "info")
                await self._push_log(entry)

            except Exception as e:
                entry = self._create_log_entry(f"Error stopping crawler: {str(e)}", "error")
                await self._push_log(entry)

            self.status = "idle"
            self.current_config = None
            if self.current_run_id:
                await task_store.update_run(self.current_run_id, final_status, stage="finalize")

            # Cancel log reading task
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None

            asyncio.create_task(self.start_next_queued())

            return True

    async def pause(self, run_id: str) -> bool:
        if run_id != self.current_run_id or not self.process or self.process.poll() is not None:
            return False
        await task_store.update_run(run_id, "pausing")
        stopped = await self.stop(final_status="paused")
        if stopped:
            await task_store.update_run(run_id, "paused")
        return stopped

    async def continue_after_login(self, run_id: str) -> bool:
        item = await task_store.get_run(run_id)
        if not item or item["status"] != "waiting_for_login":
            return False
        return await self.resume(run_id)

    async def cancel(self, run_id: str) -> bool:
        item = await task_store.get_run(run_id)
        if not item or item["status"] in {"completed", "cancelled"}:
            return False
        if run_id == self.current_run_id and self.process and self.process.poll() is None:
            await self.stop(final_status="cancelled")
        await task_store.update_run(run_id, "cancelled", error_type="cancelled")
        self._queued_configs.pop(run_id, None)
        return True

    async def resume(self, run_id: str) -> bool:
        item = await task_store.get_run(run_id)
        if not item or item["status"] not in {"paused", "partial", "failed", "waiting_for_login", "waiting_for_space"}:
            return False
        config = CrawlerStartRequest.model_validate(json.loads(item["config_json"]))
        await task_store.update_run(run_id, "queued")
        self._queued_configs[run_id] = config
        if not self.process or self.process.poll() is not None:
            await self.start_next_queued()
        return True

    async def retry(self, run_id: str) -> str | None:
        item = await task_store.get_run(run_id)
        if not item or item["status"] not in {"failed", "partial", "cancelled"}:
            return None
        await task_store.retry_failed_items(run_id)
        config = CrawlerStartRequest.model_validate(json.loads(item["config_json"]))
        await task_store.update_run(run_id, "queued")
        self._queued_configs[run_id] = config
        if not self.process or self.process.poll() is not None:
            await self.start_next_queued()
        return run_id

    def get_status(self) -> dict:
        """Get current status"""
        return {
            "status": self.status,
            "platform": self.current_config.platform.value if self.current_config else None,
            "crawler_type": self.current_config.crawler_type.value if self.current_config else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "run_id": self.current_run_id,
            "error_message": None
        }

    def _build_command(self, config: CrawlerStartRequest) -> list:
        """Build main.py command line arguments"""
        # Reuse the interpreter that runs the API. This works for uv, venv and
        # packaged launches and avoids assuming the `uv` executable is on PATH.
        cmd = [sys.executable, "main.py"]

        cmd.extend(["--platform", config.platform.value])
        cmd.extend(["--lt", config.login_type.value])
        cmd.extend(["--type", config.crawler_type.value])
        cmd.extend(["--save_data_option", config.save_option.value])

        # Pass different arguments based on crawler type
        if config.crawler_type.value == "search" and config.keywords:
            cmd.extend(["--keywords", config.keywords])
        elif config.crawler_type.value == "detail" and config.specified_ids:
            cmd.extend(["--specified_id", config.specified_ids])
        elif config.crawler_type.value == "creator" and config.creator_ids:
            cmd.extend(["--creator_id", config.creator_ids])
        elif config.crawler_type.value == "topic" and config.topics:
            cmd.extend(["--topics", config.topics])

        if config.start_page != 1:
            cmd.extend(["--start", str(config.start_page)])

        cmd.extend(["--get_comment", "true" if config.enable_comments else "false"])
        cmd.extend(["--get_sub_comment", "true" if config.enable_sub_comments else "false"])

        if config.max_notes_count is not None:
            cmd.extend(["--crawler_max_notes_count", str(config.max_notes_count)])

        if config.max_comments_count is not None:
            cmd.extend(["--max_comments_count_singlenotes", str(config.max_comments_count)])

        if config.cookies:
            cmd.extend(["--cookies", config.cookies])

        if config.platform.value == "dy":
            douyin_flags = {
                "--enable_creator_profile": config.enable_creator_profile,
                "--force_creator_refresh": config.force_creator_refresh,
                "--enable_native_subtitle": config.enable_native_subtitle,
                "--enable_asr": config.enable_asr,
                "--save_raw_payload": config.save_raw_payload,
                "--keep_media": config.keep_media,
                "--download_media": config.download_media,
                "--download_video": config.download_video,
                "--download_images": config.download_images,
                "--download_cover": config.download_cover,
                "--download_music": config.download_music,
                "--skip_existing_media": config.skip_existing_media,
                "--verify_media": config.verify_media,
                "--keep_asr_source_media": config.keep_asr_source_media,
                "--incremental": config.incremental,
                "--refresh_existing_metrics": config.refresh_existing_metrics,
                "--refresh_existing_comments": config.refresh_existing_comments,
            }
            for flag, value in douyin_flags.items():
                if value is not None:
                    cmd.extend([flag, "true" if value else "false"])
            if config.asr_model is not None:
                cmd.extend(["--asr_model", config.asr_model])
            if config.asr_language is not None:
                cmd.extend(["--asr_language", config.asr_language])
            for flag, value in {
                "--media_quality": config.media_quality,
                "--max_media_downloads": config.max_media_downloads,
                "--max_media_total_bytes": config.max_media_total_bytes,
                "--media_library_max_bytes": config.media_library_max_bytes,
                "--min_free_disk_bytes": config.min_free_disk_bytes,
                "--stop_after_existing": config.stop_after_existing,
            }.items():
                if value is not None:
                    cmd.extend([flag, str(value)])

        cmd.extend(["--headless", "true" if config.headless else "false"])
        cmd.extend(["--enable_ip_proxy", "true" if config.enable_ip_proxy else "false"])
        if config.enable_ip_proxy and config.static_proxy_url:
            cmd.extend(["--ip_proxy_provider_name", "static"])
            cmd.extend(["--static_proxy_url", config.static_proxy_url])

        return cmd

    async def _read_output(self):
        """Asynchronously read process output"""
        loop = asyncio.get_event_loop()

        try:
            while self.process and self.process.poll() is None:
                # Read a line in thread pool
                line = await loop.run_in_executor(
                    None, self.process.stdout.readline
                )
                if line:
                    line = line.strip()
                    if line:
                        level = self._parse_log_level(line)
                        lower = line.lower()
                        for token in ("disk_space_low", "disk_quota_reached", "login_required", "captcha_required", "risk_controlled", "media_invalid", "api_schema_changed"):
                            if token in lower: self._detected_error_type = token
                        entry = self._create_log_entry(line, level)
                        await self._push_log(entry)

            # Read remaining output
            if self.process and self.process.stdout:
                remaining = await loop.run_in_executor(
                    None, self.process.stdout.read
                )
                if remaining:
                    for line in remaining.strip().split('\n'):
                        if line.strip():
                            level = self._parse_log_level(line)
                            entry = self._create_log_entry(line.strip(), level)
                            await self._push_log(entry)

            # Process ended
            if self.status == "running":
                exit_code = self.process.returncode if self.process else -1
                if exit_code == 0:
                    entry = self._create_log_entry("Crawler completed successfully", "success")
                else:
                    entry = self._create_log_entry(f"Crawler exited with code: {exit_code}", "warning")
                await self._push_log(entry)
                self.status = "idle"
                if self.current_run_id:
                    final_status = "completed" if exit_code == 0 else (
                        "waiting_for_space" if self._detected_error_type in {"disk_space_low", "disk_quota_reached"}
                        else "waiting_for_login" if self._detected_error_type in {"login_required", "captcha_required", "risk_controlled"}
                        else "partial"
                    )
                    await task_store.update_run(
                        self.current_run_id,
                        final_status,
                        stage="finalize",
                        error_type=None if exit_code == 0 else (self._detected_error_type or "unknown"),
                        error_message=None if exit_code == 0 else f"exit code {exit_code}",
                    )
                    await task_store.update_stage(
                        self.current_run_id, "finalize",
                        "completed" if final_status == "completed" else final_status,
                        total=1, completed=1 if final_status == "completed" else 0,
                        failed=0 if final_status == "completed" else 1,
                    )
                    self.current_config = None
                    await self.start_next_queued()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            entry = self._create_log_entry(f"Error reading output: {str(e)}", "error")
            await self._push_log(entry)


# Global singleton
crawler_manager = CrawlerManager()
