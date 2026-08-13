"""Outbound, authenticated command processor for a FlowLens worker."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..schemas.worker import WorkerCommand
from .task_store import TaskStore, task_store
from .worker_security import sanitize_worker_payload

CommandHandler = Callable[[WorkerCommand], Awaitable[dict[str, Any]]]


class WorkerAgent:
    def __init__(self, worker_id: str, *, store: TaskStore = task_store):
        self.worker_id = worker_id
        self.store = store
        self.handlers: dict[str, CommandHandler] = {}
        self._stop = asyncio.Event()

    def register_handler(self, command_type: str, handler: CommandHandler) -> None:
        self.handlers[command_type] = handler

    async def process_command(self, raw: dict[str, Any]) -> dict[str, Any]:
        command = WorkerCommand.model_validate(raw)
        claimed = await self.store.claim_worker_command(command.command_id, command.type)
        if not claimed:
            return {"command_id": command.command_id, "status": "duplicate"}
        handler = self.handlers.get(command.type)
        if handler is None:
            result = {"command_id": command.command_id, "status": "failed", "error_type": "unsupported_command"}
        else:
            try:
                payload = sanitize_worker_payload(await handler(command))
                result = {"command_id": command.command_id, "status": "completed", "result": payload}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = {
                    "command_id": command.command_id, "status": "failed",
                    "error_type": getattr(exc, "error_type", "unknown"),
                    "error_message": str(exc)[:500],
                }
        await self.store.enqueue_outbox("command.result", result)
        return result

    async def heartbeat(self) -> str:
        event = {"worker_id": self.worker_id, "status": "online", "browser_slots": 1}
        return await self.store.enqueue_outbox("worker.heartbeat", event)

    async def pending_messages(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = await self.store.pending_outbox(limit)
        return [{
            "sequence": row["sequence"], "event_id": row["event_id"],
            "event_type": row["event_type"], "payload": json.loads(row["payload_json"]),
        } for row in rows]

    def stop(self) -> None:
        self._stop.set()

