"""Output-independent crawl checkpoints for the Douyin crawler."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import json

import aiosqlite

from model.m_douyin import DouyinCrawlCheckpoint


STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "douyin" / "crawl_state.sqlite"


async def initialize_state_db() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(STATE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_checkpoint (
                scope TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                cursor TEXT NOT NULL DEFAULT '0',
                sub_cursor TEXT NOT NULL DEFAULT '0',
                status TEXT NOT NULL DEFAULT 'running',
                expected_count INTEGER,
                collected_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                pending_items TEXT NOT NULL DEFAULT '[]',
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (scope, scope_id)
            )
            """
        )
        cursor = await db.execute("PRAGMA table_info(crawl_checkpoint)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "pending_items" not in columns:
            await db.execute(
                "ALTER TABLE crawl_checkpoint ADD COLUMN pending_items TEXT NOT NULL DEFAULT '[]'"
            )
        await db.commit()


async def load_checkpoint(scope: str, scope_id: str) -> Optional[DouyinCrawlCheckpoint]:
    await initialize_state_db()
    async with aiosqlite.connect(STATE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM crawl_checkpoint WHERE scope=? AND scope_id=?",
            (scope, scope_id),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    values = dict(row)
    try:
        values["pending_items"] = json.loads(values.get("pending_items") or "[]")
    except json.JSONDecodeError:
        values["pending_items"] = []
    return DouyinCrawlCheckpoint(**values)


async def save_checkpoint(checkpoint: DouyinCrawlCheckpoint) -> None:
    await initialize_state_db()
    values = checkpoint.model_dump()
    values["pending_items"] = json.dumps(values["pending_items"], ensure_ascii=False)
    async with aiosqlite.connect(STATE_PATH) as db:
        await db.execute(
            """
            INSERT INTO crawl_checkpoint (
                scope, scope_id, cursor, sub_cursor, status, expected_count,
                collected_count, last_error, pending_items, updated_at
            ) VALUES (
                :scope, :scope_id, :cursor, :sub_cursor, :status, :expected_count,
                :collected_count, :last_error, :pending_items, :updated_at
            )
            ON CONFLICT(scope, scope_id) DO UPDATE SET
                cursor=excluded.cursor,
                sub_cursor=excluded.sub_cursor,
                status=excluded.status,
                expected_count=excluded.expected_count,
                collected_count=excluded.collected_count,
                last_error=excluded.last_error,
                pending_items=excluded.pending_items,
                updated_at=excluded.updated_at
            """,
            values,
        )
        await db.commit()
