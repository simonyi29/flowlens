"""Small, versioned SQLite migrations for Douyin-only schema additions."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


MIGRATION_VERSION = "20260812_douyin_enrichment_v1"

AWEME_COLUMNS = {
    "source_topic": "TEXT DEFAULT ''",
    "play_count": "BIGINT",
    "duration_ms": "BIGINT",
    "width": "INTEGER",
    "height": "INTEGER",
    "hashtags": "TEXT DEFAULT '[]'",
    "mentions": "TEXT DEFAULT '[]'",
    "music_id": "VARCHAR(255) DEFAULT ''",
    "music_title": "TEXT DEFAULT ''",
    "music_author": "TEXT DEFAULT ''",
    "crawl_run_id": "VARCHAR(64) DEFAULT ''",
    "collected_at": "BIGINT",
    "raw_payload": "TEXT",
}

COMMENT_COLUMNS = {
    "root_comment_id": "VARCHAR(255) DEFAULT ''",
    "level": "INTEGER DEFAULT 1",
    "crawl_run_id": "VARCHAR(64) DEFAULT ''",
}


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    )
    return result.scalar_one_or_none() is not None


async def _add_missing_columns(
    conn: AsyncConnection, table: str, columns: dict[str, str]
) -> None:
    if not await _table_exists(conn, table):
        return
    result = await conn.execute(text(f'PRAGMA table_info("{table}")'))
    existing = {row[1] for row in result.fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            await conn.execute(
                text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
            )


async def migrate_douyin_sqlite(conn: AsyncConnection) -> None:
    """Upgrade old SQLite databases without touching other platform tables."""
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(128) PRIMARY KEY, applied_at BIGINT NOT NULL)"
        )
    )
    applied = await conn.execute(
        text("SELECT 1 FROM schema_migrations WHERE version=:version"),
        {"version": MIGRATION_VERSION},
    )
    if applied.scalar_one_or_none() is not None:
        return

    await _add_missing_columns(conn, "douyin_aweme", AWEME_COLUMNS)
    await _add_missing_columns(conn, "douyin_aweme_comment", COMMENT_COLUMNS)
    await conn.execute(
        text(
            "INSERT INTO schema_migrations(version, applied_at) "
            "VALUES (:version, CAST(strftime('%s','now') AS INTEGER))"
        ),
        {"version": MIGRATION_VERSION},
    )
