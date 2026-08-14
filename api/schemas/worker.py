"""Versioned messages exchanged between a FlowLens control plane and worker."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = "1.0"
WorkerCommandType = Literal[
    "douyin.login.start", "douyin.login.refresh", "douyin.login.cancel",
    "douyin.session.check", "crawl.start", "crawl.pause", "crawl.resume",
    "crawl.cancel", "crawl.retry_failed", "media.open", "media.delete", "profile.close",
    "profile.delete",
]


class WorkerCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    type: WorkerCommandType
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def command_is_current(self):
        if self.expires_at:
            expiry = self.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                raise ValueError("worker command has expired")
        return self


class WorkerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    worker_id: str
    event_type: str
    sequence: int = Field(ge=1)
    command_id: str | None = None
    run_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class BrowserProfileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    connection_id: str = Field(min_length=1, max_length=128)
    tenant_hash: str = Field(pattern=r"^[a-f0-9]{16}$")


class WorkerRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_code: str = Field(min_length=16, max_length=256)
    name: str = Field(min_length=1, max_length=100)
    public_key: str = Field(min_length=32, max_length=4096)
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
