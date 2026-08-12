"""Small, versioned SQLite migrations for Douyin-only schema additions."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


MIGRATION_VERSION = "20260812_douyin_enrichment_v1"
INDEX_MIGRATION_VERSION = "20260812_douyin_indexes_v2"

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
    if applied.scalar_one_or_none() is None:
        await _add_missing_columns(conn, "douyin_aweme", AWEME_COLUMNS)
        await _add_missing_columns(conn, "douyin_aweme_comment", COMMENT_COLUMNS)
        await conn.execute(
            text(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (:version, CAST(strftime('%s','now') AS INTEGER))"
            ),
            {"version": MIGRATION_VERSION},
        )

    index_applied = await conn.execute(
        text("SELECT 1 FROM schema_migrations WHERE version=:version"),
        {"version": INDEX_MIGRATION_VERSION},
    )
    if index_applied.scalar_one_or_none() is not None:
        return
    indexes = {
        "douyin_aweme": {
            "ix_douyin_aweme_aweme_id": "aweme_id",
            "ix_douyin_aweme_creator_hash": "creator_hash",
            "ix_douyin_aweme_crawl_run_id": "crawl_run_id",
        },
        "douyin_aweme_comment": {
            "ix_douyin_aweme_comment_comment_id": "comment_id",
            "ix_douyin_aweme_comment_aweme_id": "aweme_id",
            "ix_douyin_aweme_comment_root_comment_id": "root_comment_id",
        },
        "douyin_creator": {"ix_douyin_creator_creator_hash": "creator_hash"},
        "douyin_topic": {"ix_douyin_topic_topic_id": "topic_id"},
        "douyin_aweme_metric_snapshot": {
            "ix_douyin_aweme_metric_entity_run": "aweme_id, crawl_run_id",
            "ix_douyin_aweme_metric_observed": "observed_at",
        },
        "douyin_creator_metric_snapshot": {
            "ix_douyin_creator_metric_entity_run": "creator_hash, crawl_run_id",
            "ix_douyin_creator_metric_observed": "observed_at",
        },
    }
    for table, table_indexes in indexes.items():
        if not await _table_exists(conn, table):
            continue
        columns_result = await conn.execute(text(f'PRAGMA table_info("{table}")'))
        existing_columns = {row[1] for row in columns_result.fetchall()}
        for index_name, column_sql in table_indexes.items():
            required_columns = {value.strip() for value in column_sql.split(",")}
            if required_columns.issubset(existing_columns):
                await conn.execute(
                    text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ({column_sql})')
                )
    await conn.execute(
        text(
            "INSERT INTO schema_migrations(version, applied_at) "
            "VALUES (:version, CAST(strftime('%s','now') AS INTEGER))"
        ),
        {"version": INDEX_MIGRATION_VERSION},
    )
