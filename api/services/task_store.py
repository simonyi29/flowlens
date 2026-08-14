"""Persistent local task, schedule, log, and media catalog for FlowLens."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_configured_db_path = os.getenv("FLOWLENS_TASK_DB_PATH", "").strip()
DB_PATH = Path(_configured_db_path or ROOT / "data" / "flowlens" / "tasks.sqlite").resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._lock = asyncio.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    def _initialize_sync(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS crawl_run (
              run_id TEXT PRIMARY KEY, platform TEXT NOT NULL, crawler_type TEXT NOT NULL,
              status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'discover', config_json TEXT NOT NULL,
              created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_type TEXT, error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS crawl_task_item (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, entity_id TEXT NOT NULL,
              stage TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
              error_type TEXT, error_message TEXT, updated_at TEXT NOT NULL,
              UNIQUE(run_id, entity_id, stage), FOREIGN KEY(run_id) REFERENCES crawl_run(run_id)
            );
            CREATE TABLE IF NOT EXISTS crawl_task (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, stage TEXT NOT NULL,
              status TEXT NOT NULL, total_count INTEGER NOT NULL DEFAULT 0,
              completed_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL, UNIQUE(run_id, stage),
              FOREIGN KEY(run_id) REFERENCES crawl_run(run_id)
            );
            CREATE TABLE IF NOT EXISTS task_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, created_at TEXT NOT NULL,
              level TEXT NOT NULL, message TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES crawl_run(run_id)
            );
            CREATE TABLE IF NOT EXISTS media_asset (
              asset_id TEXT PRIMARY KEY, run_id TEXT, aweme_id TEXT NOT NULL, creator_hash TEXT,
              kind TEXT NOT NULL, status TEXT NOT NULL, path TEXT, part_path TEXT, source_url TEXT,
              mime_type TEXT, size_bytes INTEGER NOT NULL DEFAULT 0, sha256 TEXT, quality TEXT,
              codec TEXT, duration_ms INTEGER, retry_count INTEGER NOT NULL DEFAULT 0,
              error_type TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_media_aweme ON media_asset(aweme_id, kind);
            CREATE TABLE IF NOT EXISTS schedule (
              schedule_id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL,
              platform TEXT NOT NULL, crawler_type TEXT NOT NULL, source TEXT NOT NULL,
              interval_type TEXT NOT NULL, interval_value INTEGER NOT NULL DEFAULT 1,
              run_at TEXT, timezone TEXT NOT NULL, config_json TEXT NOT NULL,
              last_run_at TEXT, next_run_at TEXT, misfire_policy TEXT NOT NULL DEFAULT 'run_once',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entity_state (
              platform TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_run_id TEXT,
              PRIMARY KEY(platform, entity_type, entity_id)
            );
            CREATE TABLE IF NOT EXISTS worker_identity (
              worker_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL,
              private_key_path TEXT NOT NULL, protocol_version TEXT NOT NULL,
              created_at TEXT NOT NULL, revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS browser_profile (
              profile_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL UNIQUE,
              tenant_hash TEXT NOT NULL, status TEXT NOT NULL, profile_path TEXT NOT NULL,
              pid INTEGER, cdp_port INTEGER, last_checked_at TEXT, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_session (
              login_session_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
              profile_id TEXT NOT NULL, status TEXT NOT NULL, expires_at TEXT NOT NULL,
              error_type TEXT, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              FOREIGN KEY(profile_id) REFERENCES browser_profile(profile_id)
            );
            CREATE TABLE IF NOT EXISTS worker_command (
              command_id TEXT PRIMARY KEY, command_type TEXT NOT NULL, status TEXT NOT NULL,
              result_json TEXT, received_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sync_outbox (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
              event_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL,
              retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              acknowledged_at TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_outbox_pending ON sync_outbox(status, sequence);
            CREATE TABLE IF NOT EXISTS media_stream_session (
              stream_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, status TEXT NOT NULL,
              range_start INTEGER, range_end INTEGER, expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL, completed_at TEXT,
              FOREIGN KEY(asset_id) REFERENCES media_asset(asset_id)
            );
            CREATE TABLE IF NOT EXISTS flowlens_worker (
              worker_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL,
              status TEXT NOT NULL, version TEXT, protocol_version TEXT NOT NULL DEFAULT '1.0',
              capabilities_json TEXT NOT NULL DEFAULT '{}', browser_slots INTEGER NOT NULL DEFAULT 1,
              last_heartbeat_at TEXT, registered_at TEXT NOT NULL, revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS flowlens_user (
              user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
              normalized_username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
              password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
              status TEXT NOT NULL CHECK(status IN ('pending_activation','active','suspended')),
              must_change_password INTEGER NOT NULL DEFAULT 1,
              temporary_password_expires_at TEXT,
              max_douyin_connections INTEGER NOT NULL DEFAULT 3,
              max_queued_tasks INTEGER NOT NULL DEFAULT 10,
              media_quota_bytes INTEGER NOT NULL DEFAULT 21474836480,
              created_by_user_id TEXT, created_at TEXT NOT NULL, activated_at TEXT,
              last_login_at TEXT, suspended_at TEXT, updated_at TEXT NOT NULL,
              FOREIGN KEY(created_by_user_id) REFERENCES flowlens_user(user_id)
            );
            CREATE INDEX IF NOT EXISTS ix_flowlens_user_status ON flowlens_user(status, created_at);
            CREATE TABLE IF NOT EXISTS user_session (
              session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              session_token_hash TEXT NOT NULL UNIQUE, csrf_token_hash TEXT NOT NULL,
              created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              idle_expires_at TEXT NOT NULL, absolute_expires_at TEXT NOT NULL,
              revoked_at TEXT, revoked_reason TEXT,
              FOREIGN KEY(user_id) REFERENCES flowlens_user(user_id)
            );
            CREATE INDEX IF NOT EXISTS ix_user_session_user ON user_session(user_id, revoked_at);
            CREATE TABLE IF NOT EXISTS auth_login_attempt (
              attempt_id TEXT PRIMARY KEY, username_hash TEXT NOT NULL,
              source_ip_hash TEXT NOT NULL, success INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_auth_attempt_username ON auth_login_attempt(username_hash, created_at);
            CREATE INDEX IF NOT EXISTS ix_auth_attempt_ip ON auth_login_attempt(source_ip_hash, created_at);
            CREATE TABLE IF NOT EXISTS audit_event (
              audit_id TEXT PRIMARY KEY, actor_user_id TEXT, action TEXT NOT NULL,
              target_type TEXT NOT NULL, target_id TEXT, result TEXT NOT NULL,
              sanitized_context_json TEXT NOT NULL DEFAULT '{}', request_id TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(actor_user_id) REFERENCES flowlens_user(user_id)
            );
            CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_event(actor_user_id, created_at);
            CREATE TABLE IF NOT EXISTS douyin_connection (
              connection_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, worker_id TEXT NOT NULL,
              profile_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, creator_hash TEXT,
              masked_nickname TEXT, last_verified_at TEXT, created_at TEXT NOT NULL,
              disconnected_at TEXT, display_name TEXT, remark TEXT, updated_at TEXT,
              FOREIGN KEY(worker_id) REFERENCES flowlens_worker(worker_id)
            );
            CREATE INDEX IF NOT EXISTS ix_connection_user ON douyin_connection(user_id, created_at);
            CREATE TABLE IF NOT EXISTS remote_crawl_run (
              run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, connection_id TEXT NOT NULL,
              worker_id TEXT NOT NULL, worker_run_id TEXT, status TEXT NOT NULL,
              stage TEXT NOT NULL DEFAULT 'discover', sanitized_config_json TEXT NOT NULL,
              created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
              error_type TEXT, error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_remote_run_user ON remote_crawl_run(user_id, created_at);
            CREATE TABLE IF NOT EXISTS worker_event (
              event_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, event_type TEXT NOT NULL,
              run_id TEXT, sequence INTEGER NOT NULL, received_at TEXT NOT NULL, processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS worker_enrollment (
              code_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
              used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS remote_result (
              id INTEGER PRIMARY KEY AUTOINCREMENT, source_event_id TEXT NOT NULL UNIQUE,
              user_id TEXT NOT NULL, connection_id TEXT NOT NULL, run_id TEXT NOT NULL,
              worker_id TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
              payload_json TEXT NOT NULL, observed_at TEXT, synced_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_remote_result_user ON remote_result(user_id, entity_type, synced_at);
            CREATE INDEX IF NOT EXISTS ix_remote_result_entity ON remote_result(user_id, entity_type, entity_id);
            CREATE TABLE IF NOT EXISTS remote_entity (
              user_id TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
              connection_id TEXT NOT NULL, run_id TEXT NOT NULL, worker_id TEXT NOT NULL,
              payload_json TEXT NOT NULL, source_event_id TEXT NOT NULL,
              observed_at TEXT, synced_at TEXT NOT NULL,
              PRIMARY KEY(user_id,entity_type,entity_id)
            );
            CREATE INDEX IF NOT EXISTS ix_remote_entity_type ON remote_entity(user_id,entity_type,synced_at);
            CREATE TABLE IF NOT EXISTS task_schema_migrations (
              version TEXT PRIMARY KEY, applied_at TEXT NOT NULL
            );
            """)
            self._ensure_columns(db, "login_session", {"user_id":"TEXT", "worker_id":"TEXT"})
            self._ensure_columns(db, "sync_outbox", {"worker_id":"TEXT"})
            self._ensure_columns(db, "douyin_connection", {
                "display_name": "TEXT", "remark": "TEXT", "updated_at": "TEXT"
            })
            self._ensure_columns(db, "schedule", {"user_id": "TEXT", "connection_id": "TEXT"})
            db.execute("UPDATE crawl_run SET status='partial', finished_at=? WHERE status IN ('running','pausing')", (_now(),))
            # Older direct CLI runs recorded an interrupted process as both
            # ``partial`` and ``error_type=cancelled``.  That made the product
            # UI offer a meaningless "retry failed items" action even though
            # there were no failed items.  Preserve the run and its collected
            # data, but repair the user-facing lifecycle state in place.
            db.execute("UPDATE crawl_run SET status='cancelled' WHERE status='partial' AND error_type='cancelled'")
            # A few older downloader versions could create two asset rows for
            # the same physical file. Keep the best metadata row and retire
            # only the duplicate database records; the shared file remains.
            db.execute("""
                WITH ranked AS (
                  SELECT asset_id,
                         ROW_NUMBER() OVER (
                           PARTITION BY path
                           ORDER BY CASE WHEN mime_type LIKE 'video/%' THEN 0 ELSE 1 END,
                                    updated_at DESC,
                                    asset_id
                         ) AS position
                  FROM media_asset
                  WHERE path IS NOT NULL AND status <> 'deleted'
                )
                UPDATE media_asset
                SET status='deleted',path=NULL,size_bytes=0,updated_at=?
                WHERE asset_id IN (SELECT asset_id FROM ranked WHERE position > 1)
            """, (_now(),))
            db.execute("UPDATE browser_profile SET status='idle',pid=NULL,cdp_port=NULL,updated_at=? WHERE status='running'", (_now(),))
            db.execute("UPDATE login_session SET status='failed',error_type='worker_restarted',error_message='Worker restarted before login completed',updated_at=? WHERE status IN ('starting_browser','opening_login_page','generating_qr','qr_ready','qr_scanned','checking_login')", (_now(),))
            db.execute("INSERT OR IGNORE INTO task_schema_migrations(version,applied_at) VALUES('remote_worker_v1',?)", (_now(),))
            db.execute("INSERT OR IGNORE INTO task_schema_migrations(version,applied_at) VALUES('auth_accounts_v1',?)", (_now(),))
            self._migrate_legacy_users_sync(db)

    @staticmethod
    def _ensure_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    @staticmethod
    def _migrate_legacy_users_sync(db: sqlite3.Connection) -> None:
        """Keep old tenant ownership intact without making legacy IDs loginable."""
        user_ids: set[str] = set()
        for table in ("douyin_connection", "remote_crawl_run", "remote_result"):
            user_ids.update(
                str(row[0]) for row in db.execute(
                    f"SELECT DISTINCT user_id FROM {table} WHERE user_id IS NOT NULL AND user_id<>''"
                )
            )
        now = _now()
        for user_id in user_ids:
            if db.execute("SELECT 1 FROM flowlens_user WHERE user_id=?", (user_id,)).fetchone():
                continue
            suffix = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
            username = f"legacy_{suffix}"
            db.execute(
                """INSERT INTO flowlens_user(
                   user_id,username,normalized_username,display_name,password_hash,role,status,
                   must_change_password,created_at,suspended_at,updated_at
                   ) VALUES(?,?,?,?,?,'user','suspended',1,?,?,?)""",
                (user_id, username, username, "旧版迁移用户", "!disabled!", now, now, now),
            )

    async def create_run(self, config: dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex
        config = dict(config)
        config.pop("cookies", None)
        config.pop("static_proxy_url", None)
        async with self._lock:
            await asyncio.to_thread(self._create_run_sync, run_id, config)
        return run_id

    async def ensure_run(self, run_id: str, config: dict[str, Any]) -> None:
        """Register direct CLI runs without replacing API-created config snapshots."""
        safe = dict(config)
        safe.pop("cookies", None)
        safe.pop("static_proxy_url", None)
        async with self._lock:
            await asyncio.to_thread(self._ensure_run_sync, run_id, safe)

    def _ensure_run_sync(self, run_id: str, config: dict[str, Any]) -> None:
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM crawl_run WHERE run_id=?", (run_id,)).fetchone()
            if exists:
                return
            db.execute(
                "INSERT INTO crawl_run(run_id,platform,crawler_type,status,stage,config_json,created_at,started_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, config.get("platform", "dy"), config.get("crawler_type", "detail"), "running", "discover", json.dumps(config, ensure_ascii=False), _now(), _now()),
            )
            db.executemany("INSERT INTO crawl_task(run_id,stage,status,updated_at) VALUES(?,?,?,?)", [
                (run_id, stage, "queued", _now()) for stage in
                ("discover","detail","creator","comments","native_transcript","media_download","asr","finalize")
            ])

    def _create_run_sync(self, run_id: str, config: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO crawl_run(run_id,platform,crawler_type,status,config_json,created_at) VALUES(?,?,?,?,?,?)",
                       (run_id, config['platform'], config['crawler_type'], 'queued', json.dumps(config, ensure_ascii=False), _now()))
            db.executemany("INSERT INTO crawl_task(run_id,stage,status,updated_at) VALUES(?,?,?,?)", [
                (run_id, stage, "queued", _now()) for stage in
                ("discover","detail","creator","comments","native_transcript","media_download","asr","finalize")
            ])

    async def update_run(self, run_id: str, status: str, *, stage: str | None = None,
                         error_type: str | None = None, error_message: str | None = None) -> None:
        async with self._lock:
            await asyncio.to_thread(self._update_run_sync, run_id, status, stage, error_type, error_message)

    def _update_run_sync(self, run_id, status, stage, error_type, error_message):
        fields, values = ["status=?", "error_type=?", "error_message=?"], [status, error_type, error_message]
        if stage: fields.append("stage=?"); values.append(stage)
        if status in {'queued','running'}: fields.append("finished_at=NULL")
        if status == 'running': fields.append("started_at=COALESCE(started_at,?)"); values.append(_now())
        if status in {'completed','failed','cancelled','partial'}: fields.append("finished_at=?"); values.append(_now())
        values.append(run_id)
        with self._connect() as db: db.execute(f"UPDATE crawl_run SET {','.join(fields)} WHERE run_id=?", values)

    async def add_log(self, run_id: str, level: str, message: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute, "INSERT INTO task_log(run_id,created_at,level,message) VALUES(?,?,?,?)", (run_id,_now(),level,message))

    def _execute(self, sql, args=()):
        with self._connect() as db: db.execute(sql, args)

    async def list_runs(self, limit=100, offset=0, status: str | None = None):
        if status:
            return await asyncio.to_thread(
                self._query,
                "SELECT * FROM crawl_run WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        return await asyncio.to_thread(
            self._query,
            "SELECT * FROM crawl_run ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    async def count_runs(self, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS total FROM crawl_run"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        rows = await asyncio.to_thread(self._query, sql, args)
        return int(rows[0]["total"] if rows else 0)

    async def run_status_counts(self) -> dict[str, int]:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT status,COUNT(*) AS total FROM crawl_run GROUP BY status",
        )
        return {str(row["status"]): int(row["total"]) for row in rows}

    async def get_run(self, run_id):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM crawl_run WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    async def delete_run_history(self, run_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_run_history_sync, run_id)

    def _delete_run_history_sync(self, run_id: str) -> bool:
        terminal = {"completed", "cancelled", "partial", "failed"}
        with self._connect() as db:
            row = db.execute("SELECT status FROM crawl_run WHERE run_id=?", (run_id,)).fetchone()
            if not row or row["status"] not in terminal:
                return False
            db.execute("DELETE FROM task_log WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM crawl_task_item WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM crawl_task WHERE run_id=?", (run_id,))
            cursor = db.execute("DELETE FROM crawl_run WHERE run_id=?", (run_id,))
            return bool(cursor.rowcount)

    async def next_queued_run(self):
        rows = await asyncio.to_thread(
            self._query,
            "SELECT * FROM crawl_run WHERE status='queued' ORDER BY created_at LIMIT 1",
        )
        return rows[0] if rows else None

    async def retry_failed_items(self, run_id: str) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._retry_failed_items_sync, run_id)

    def _retry_failed_items_sync(self, run_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE crawl_task_item SET status='queued',progress=0,error_type=NULL,error_message=NULL,updated_at=? "
                "WHERE run_id=? AND status IN ('failed','partial')", (_now(), run_id),
            )
            db.execute(
                "UPDATE crawl_task SET status='queued',failed_count=0,updated_at=? WHERE run_id=? AND status IN ('failed','partial')",
                (_now(), run_id),
            )
            return int(cursor.rowcount or 0)

    async def list_items(self, run_id):
        return await asyncio.to_thread(self._query, "SELECT * FROM crawl_task_item WHERE run_id=? ORDER BY id", (run_id,))

    async def list_stages(self, run_id):
        sql = """SELECT t.*,
        COALESCE((SELECT COUNT(*) FROM crawl_task_item i WHERE i.run_id=t.run_id AND i.stage=t.stage),t.total_count) AS total_count,
        COALESCE((SELECT COUNT(*) FROM crawl_task_item i WHERE i.run_id=t.run_id AND i.stage=t.stage AND i.status='completed'),t.completed_count) AS completed_count,
        COALESCE((SELECT COUNT(*) FROM crawl_task_item i WHERE i.run_id=t.run_id AND i.stage=t.stage AND i.status IN ('failed','partial')),t.failed_count) AS failed_count
        FROM crawl_task t WHERE t.run_id=? ORDER BY t.id"""
        return await asyncio.to_thread(self._query, sql, (run_id,))

    async def update_stage(self, run_id: str, stage: str, status: str, *, total: int | None = None,
                           completed: int | None = None, failed: int | None = None):
        fields, values = ["status=?", "updated_at=?"], [status, _now()]
        for name, value in (("total_count",total),("completed_count",completed),("failed_count",failed)):
            if value is not None: fields.append(f"{name}=?"); values.append(value)
        values += [run_id,stage]
        async with self._lock: await asyncio.to_thread(self._execute, f"UPDATE crawl_task SET {','.join(fields)} WHERE run_id=? AND stage=?", values)

    async def upsert_task_item(self, run_id: str, entity_id: str, stage: str, status: str,
                               progress: float = 0, error_type: str | None = None, error_message: str | None = None):
        sql = """INSERT INTO crawl_task_item(run_id,entity_id,stage,status,progress,error_type,error_message,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,entity_id,stage) DO UPDATE SET status=excluded.status,
        progress=excluded.progress,error_type=excluded.error_type,error_message=excluded.error_message,updated_at=excluded.updated_at"""
        args = (run_id,entity_id,stage,status,progress,error_type,error_message,_now())
        async with self._lock:
            await asyncio.to_thread(self._upsert_task_item_sync, sql, args, run_id)

    def _upsert_task_item_sync(self, sql: str, args: tuple, run_id: str) -> None:
        # Unit-level services and direct CLI calls can enter a stage without the
        # API scheduler. Create a minimal parent atomically so stage telemetry
        # never breaks otherwise successful collection work.
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM crawl_run WHERE run_id=?", (run_id,)).fetchone()
            if not exists:
                now = _now()
                db.execute(
                    "INSERT INTO crawl_run(run_id,platform,crawler_type,status,stage,config_json,created_at,started_at) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, "dy", "detail", "running", "discover", "{}", now, now),
                )
                db.executemany(
                    "INSERT INTO crawl_task(run_id,stage,status,updated_at) VALUES(?,?,?,?)",
                    [(run_id, value, "queued", now) for value in
                     ("discover","detail","creator","comments","native_transcript","media_download","asr","finalize")],
                )
            db.execute(sql, args)
            entity_stage, entity_status = args[2], args[3]
            counts = db.execute(
                "SELECT COUNT(*),SUM(status='completed'),SUM(status IN ('failed','partial')) "
                "FROM crawl_task_item WHERE run_id=? AND stage=?", (run_id, entity_stage),
            ).fetchone()
            stage_status = (
                "running" if entity_status in {"running", "queued"}
                else "partial" if int(counts[2] or 0) else "completed"
            )
            db.execute(
                "UPDATE crawl_task SET status=?,total_count=?,completed_count=?,failed_count=?,updated_at=? "
                "WHERE run_id=? AND stage=?",
                (stage_status, int(counts[0] or 0), int(counts[1] or 0), int(counts[2] or 0), _now(), run_id, entity_stage),
            )
            if entity_status == "running":
                db.execute("UPDATE crawl_run SET stage=? WHERE run_id=?", (entity_stage, run_id))

    async def list_logs(self, run_id, limit=500):
        return await asyncio.to_thread(self._query, "SELECT * FROM task_log WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id,limit))

    async def upsert_media(self, item: dict[str, Any]) -> str:
        asset_id = item.get("asset_id")
        if not asset_id:
            asset_path = str(item.get("path") or item.get("part_path") or "")
            if asset_path.endswith(".part"):
                asset_path = asset_path[:-5]
            identity = f"{item['aweme_id']}|{item['kind']}|{asset_path}"
            asset_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        now = _now()
        values = (
            asset_id, item.get("run_id"), item["aweme_id"], item.get("creator_hash"), item["kind"],
            item.get("status", "completed"), item.get("path"), item.get("part_path"), item.get("source_url"),
            item.get("mime_type"), item.get("size_bytes", 0), item.get("sha256"), item.get("quality"),
            item.get("codec"), item.get("duration_ms"), item.get("retry_count", 0), item.get("error_type"),
            item.get("error_message"), now, now,
        )
        sql = """INSERT INTO media_asset(asset_id,run_id,aweme_id,creator_hash,kind,status,path,part_path,
        source_url,mime_type,size_bytes,sha256,quality,codec,duration_ms,retry_count,error_type,error_message,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET
        status=excluded.status,path=excluded.path,part_path=excluded.part_path,source_url=excluded.source_url,
        mime_type=excluded.mime_type,size_bytes=excluded.size_bytes,sha256=excluded.sha256,retry_count=excluded.retry_count,
        error_type=excluded.error_type,error_message=excluded.error_message,updated_at=excluded.updated_at"""
        async with self._lock: await asyncio.to_thread(self._execute, sql, values)
        remote_run_id = os.getenv("FLOWLENS_WORKER_RUN_ID", "")
        if remote_run_id and item.get("status") in {"completed", "deleted"}:
            await self.enqueue_outbox("result.media", {
                "worker_id":os.getenv("FLOWLENS_WORKER_ID", ""),
                "run_id":remote_run_id, "entity_type":"media", "entity_id":asset_id,
                "payload":{
                    "asset_id":asset_id, "aweme_id":item["aweme_id"], "kind":item["kind"],
                    "status":item.get("status", "completed"), "size_bytes":item.get("size_bytes", 0),
                    "mime_type":item.get("mime_type"), "sha256":item.get("sha256"),
                    "duration_ms":item.get("duration_ms"),
                },
            })
        return asset_id

    @staticmethod
    def _media_filters(
        *,
        aweme_id: str | None = None,
        query: str | None = None,
        kind: str | None = None,
        status: str | None = "active",
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if aweme_id:
            clauses.append("aweme_id=?")
            values.append(aweme_id)
        if query:
            clauses.append("(aweme_id LIKE ? OR creator_hash LIKE ? OR quality LIKE ? OR mime_type LIKE ?)")
            pattern = f"%{query.strip()}%"
            values.extend([pattern, pattern, pattern, pattern])
        if kind:
            clauses.append("kind=?")
            values.append(kind)
        if status == "active":
            clauses.append("status<>'deleted'")
        elif status:
            clauses.append("status=?")
            values.append(status)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    async def list_media(
        self,
        limit: int = 24,
        offset: int = 0,
        aweme_id: str | None = None,
        *,
        query: str | None = None,
        kind: str | None = None,
        status: str | None = "active",
        sort: str = "newest",
    ):
        where, values = self._media_filters(
            aweme_id=aweme_id, query=query, kind=kind, status=status,
        )
        order_by = {
            "newest": "updated_at DESC,asset_id",
            "oldest": "updated_at ASC,asset_id",
            "largest": "size_bytes DESC,updated_at DESC",
        }.get(sort, "updated_at DESC,asset_id")
        return await asyncio.to_thread(
            self._query,
            f"SELECT * FROM media_asset{where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )

    async def media_catalog_summary(
        self,
        *,
        aweme_id: str | None = None,
        query: str | None = None,
        kind: str | None = None,
        status: str | None = "active",
    ) -> dict[str, Any]:
        where, values = self._media_filters(
            aweme_id=aweme_id, query=query, kind=kind, status=status,
        )
        total_rows, status_rows, kind_rows = await asyncio.gather(
            asyncio.to_thread(
                self._query,
                f"SELECT COUNT(*) AS total,COALESCE(SUM(size_bytes),0) AS bytes FROM media_asset{where}",
                tuple(values),
            ),
            asyncio.to_thread(
                self._query,
                "SELECT status,COUNT(*) AS total FROM media_asset GROUP BY status",
            ),
            asyncio.to_thread(
                self._query,
                "SELECT kind,COUNT(*) AS total FROM media_asset WHERE status<>'deleted' GROUP BY kind",
            ),
        )
        current = total_rows[0] if total_rows else {"total": 0, "bytes": 0}
        return {
            "total": int(current["total"] or 0),
            "filtered_bytes": int(current["bytes"] or 0),
            "active_total": sum(int(row["total"]) for row in status_rows if row["status"] != "deleted"),
            "status_counts": {str(row["status"]): int(row["total"]) for row in status_rows},
            "kind_counts": {str(row["kind"]): int(row["total"]) for row in kind_rows},
        }

    async def media_count(self) -> int:
        rows = await asyncio.to_thread(self._query, "SELECT COUNT(*) AS total FROM media_asset WHERE status<>'deleted'")
        return int(rows[0]["total"] if rows else 0)

    async def run_summary(self, run_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._run_summary_sync, run_id)

    def _run_summary_sync(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            run = db.execute("SELECT * FROM crawl_run WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return {}
            media = db.execute(
                "SELECT COALESCE(SUM(size_bytes),0),COUNT(*),MIN(created_at),MAX(updated_at) "
                "FROM media_asset WHERE run_id=? AND status='completed'", (run_id,),
            ).fetchone()
        start_text = run["started_at"] or run["created_at"]
        end_text = run["finished_at"] or _now()
        try:
            elapsed = max((datetime.fromisoformat(end_text) - datetime.fromisoformat(start_text)).total_seconds(), 0.0)
        except (TypeError, ValueError):
            elapsed = 0.0
        downloaded = int(media[0] or 0)
        try:
            config = json.loads(run["config_json"] or "{}")
        except json.JSONDecodeError:
            config = {}
        quota = int(config.get("max_media_total_bytes") or 0)
        remaining = max(quota - downloaded, 0) if quota else 0
        speed = downloaded / elapsed if elapsed else 0
        return {
            "elapsed_seconds": round(elapsed, 3),
            "downloaded_bytes": downloaded,
            "completed_media_assets": int(media[1] or 0),
            "average_download_bytes_per_second": round(speed, 2),
            "task_media_quota_bytes": quota,
            "remaining_quota_bytes": remaining,
            "estimated_remaining_seconds": round(remaining / speed, 1) if remaining and speed else None,
        }

    async def get_media(self, asset_id):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM media_asset WHERE asset_id=?", (asset_id,))
        return rows[0] if rows else None

    async def enqueue_outbox(self, event_type: str, payload: dict[str, Any], event_id: str | None = None) -> str:
        from .worker_security import sanitize_worker_payload
        event_id = event_id or uuid.uuid4().hex
        safe_payload = sanitize_worker_payload(payload)
        worker_id = payload.get("worker_id")
        sql = "INSERT OR IGNORE INTO sync_outbox(event_id,event_type,payload_json,status,created_at,worker_id) VALUES(?,?,?,'pending',?,?)"
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, (event_id, event_type, json.dumps(safe_payload, ensure_ascii=False), _now(), worker_id))
        return event_id

    async def pending_outbox(self, limit: int = 500):
        return await asyncio.to_thread(
            self._query,
            "SELECT * FROM sync_outbox WHERE status IN ('pending','sending','failed') ORDER BY sequence LIMIT ?",
            (limit,),
        )

    async def ack_outbox(self, through_sequence: int) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE sync_outbox SET status='acknowledged',acknowledged_at=? WHERE sequence<=? AND status<>'acknowledged'",
                (_now(), through_sequence),
            )

    async def pending_outbox_for_worker(self, worker_id: str, limit: int = 100):
        return await asyncio.to_thread(
            self._query,
            "SELECT * FROM sync_outbox WHERE worker_id=? AND status IN ('pending','sending','failed') ORDER BY sequence LIMIT ?",
            (worker_id, limit),
        )

    async def ack_outbox_event(self, event_id: str, worker_id: str | None = None) -> None:
        async with self._lock:
            if worker_id:
                await asyncio.to_thread(self._execute, "UPDATE sync_outbox SET status='acknowledged',acknowledged_at=? WHERE event_id=? AND worker_id=?", (_now(),event_id,worker_id))
            else:
                await asyncio.to_thread(self._execute, "UPDATE sync_outbox SET status='acknowledged',acknowledged_at=? WHERE event_id=?", (_now(),event_id))

    async def create_worker_enrollment(self, ttl_seconds: int = 600) -> str:
        import secrets
        from datetime import timedelta
        code = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        async with self._lock:
            await asyncio.to_thread(self._execute, "INSERT INTO worker_enrollment(code_hash,expires_at,created_at) VALUES(?,?,?)", (digest,expires,_now()))
        return code

    async def consume_worker_enrollment(self, code: str) -> bool:
        digest = hashlib.sha256(code.encode()).hexdigest()
        async with self._lock:
            return await asyncio.to_thread(self._consume_worker_enrollment_sync, digest)

    def _consume_worker_enrollment_sync(self, digest: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT expires_at,used_at FROM worker_enrollment WHERE code_hash=?", (digest,)).fetchone()
            if not row or row["used_at"] or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                return False
            db.execute("UPDATE worker_enrollment SET used_at=? WHERE code_hash=? AND used_at IS NULL", (_now(),digest))
            return True

    async def save_browser_profile(self, item: dict[str, Any]) -> None:
        now = _now()
        values = (
            item["profile_id"], item["connection_id"], item["tenant_hash"],
            item.get("status", "creating"), item["profile_path"], item.get("pid"),
            item.get("cdp_port"), item.get("last_checked_at"), now, now,
        )
        sql = """INSERT INTO browser_profile(profile_id,connection_id,tenant_hash,status,profile_path,pid,cdp_port,last_checked_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET
        status=excluded.status,pid=excluded.pid,cdp_port=excluded.cdp_port,
        last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at"""
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, values)

    async def get_browser_profile(self, profile_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM browser_profile WHERE profile_id=?", (profile_id,))
        return rows[0] if rows else None

    async def get_profile_by_connection(self, connection_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM browser_profile WHERE connection_id=?", (connection_id,))
        return rows[0] if rows else None

    async def list_browser_profile_status(self):
        return await asyncio.to_thread(
            self._query,
            "SELECT profile_id,connection_id,status,pid,cdp_port,last_checked_at,updated_at FROM browser_profile ORDER BY updated_at DESC",
            (),
        )

    async def save_login_session(self, item: dict[str, Any]) -> None:
        now = _now()
        values = (
            item["login_session_id"], item["connection_id"], item["profile_id"],
            item.get("status", "queued"), item["expires_at"], item.get("error_type"),
            item.get("error_message"), now, now, item.get("user_id"), item.get("worker_id"),
        )
        sql = """INSERT INTO login_session(login_session_id,connection_id,profile_id,status,expires_at,error_type,error_message,created_at,updated_at,user_id,worker_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(login_session_id) DO UPDATE SET
        status=excluded.status,expires_at=excluded.expires_at,error_type=excluded.error_type,
        error_message=excluded.error_message,updated_at=excluded.updated_at"""
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, values)

    async def get_login_session(self, login_session_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM login_session WHERE login_session_id=?", (login_session_id,))
        return rows[0] if rows else None

    async def get_user_login_session(self, login_session_id: str, user_id: str):
        rows = await asyncio.to_thread(
            self._query, "SELECT * FROM login_session WHERE login_session_id=? AND user_id=?", (login_session_id, user_id)
        )
        return rows[0] if rows else None

    async def update_login_session(self, login_session_id: str, status: str, *, error_type: str | None = None,
                                   error_message: str | None = None) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE login_session SET status=?,error_type=?,error_message=?,updated_at=? WHERE login_session_id=?",
                (status, error_type, error_message, _now(), login_session_id),
            )

    async def claim_worker_command(self, command_id: str, command_type: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._claim_worker_command_sync, command_id, command_type)

    def _claim_worker_command_sync(self, command_id: str, command_type: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO worker_command(command_id,command_type,status,received_at) VALUES(?,?,'running',?)",
                (command_id, command_type, _now()),
            )
            return bool(cursor.rowcount)

    async def upsert_worker(self, item: dict[str, Any]) -> None:
        await self.initialize()
        now = _now()
        values = (
            item["worker_id"], item.get("name", item["worker_id"]), item["public_key"],
            item.get("status", "offline"), item.get("version"), item.get("protocol_version", "1.0"),
            json.dumps(item.get("capabilities", {}), ensure_ascii=False), int(item.get("browser_slots", 1)),
            item.get("last_heartbeat_at", now), now,
        )
        sql = """INSERT INTO flowlens_worker(worker_id,name,public_key,status,version,protocol_version,capabilities_json,browser_slots,last_heartbeat_at,registered_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
        name=excluded.name,status=excluded.status,version=excluded.version,
        capabilities_json=excluded.capabilities_json,last_heartbeat_at=excluded.last_heartbeat_at"""
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, values)

    async def get_worker(self, worker_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM flowlens_worker WHERE worker_id=? AND revoked_at IS NULL", (worker_id,))
        return rows[0] if rows else None

    async def list_workers(self):
        return await asyncio.to_thread(self._query, "SELECT worker_id,name,status,version,browser_slots,last_heartbeat_at,capabilities_json FROM flowlens_worker WHERE revoked_at IS NULL ORDER BY registered_at")

    async def revoke_worker(self, worker_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._revoke_worker_sync, worker_id)

    def _revoke_worker_sync(self, worker_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE flowlens_worker SET status='revoked',revoked_at=? WHERE worker_id=? AND revoked_at IS NULL",
                (_now(), worker_id),
            )
            return bool(cursor.rowcount)

    # Website account and server-side session persistence -----------------

    async def admin_exists(self) -> bool:
        rows = await asyncio.to_thread(
            self._query, "SELECT 1 FROM flowlens_user WHERE role='admin' LIMIT 1"
        )
        return bool(rows)

    async def create_user(self, item: dict[str, Any]) -> dict[str, Any]:
        user_id = item.get("user_id") or uuid.uuid4().hex
        now = _now()
        values = (
            user_id, item["username"], item["normalized_username"], item["display_name"],
            item["password_hash"], item.get("role", "user"),
            item.get("status", "pending_activation"), int(item.get("must_change_password", True)),
            item.get("temporary_password_expires_at"), int(item.get("max_douyin_connections", 3)),
            int(item.get("max_queued_tasks", 10)), int(item.get("media_quota_bytes", 21474836480)),
            item.get("created_by_user_id"), now, now,
        )
        sql = """INSERT INTO flowlens_user(
        user_id,username,normalized_username,display_name,password_hash,role,status,
        must_change_password,temporary_password_expires_at,max_douyin_connections,
        max_queued_tasks,media_quota_bytes,created_by_user_id,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, values)
        return await self.get_user(user_id)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._query, "SELECT * FROM flowlens_user WHERE user_id=?", (user_id,)
        )
        return rows[0] if rows else None

    async def get_user_by_username(self, normalized_username: str) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT * FROM flowlens_user WHERE normalized_username=?",
            (normalized_username,),
        )
        return rows[0] if rows else None

    async def list_users(
        self, *, search: str | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if search:
            clauses.append("(normalized_username LIKE ? OR display_name LIKE ?)")
            term = f"%{search.lower()}%"
            args.extend((term, term))
        if status:
            clauses.append("status=?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""SELECT u.user_id,u.username,u.display_name,u.role,u.status,
        u.must_change_password,u.max_douyin_connections,u.max_queued_tasks,u.media_quota_bytes,
        u.created_at,u.last_login_at,u.suspended_at,
        (SELECT COUNT(*) FROM douyin_connection c WHERE c.user_id=u.user_id AND c.status<>'disconnected') AS douyin_connection_count,
        (SELECT COUNT(*) FROM remote_crawl_run r WHERE r.user_id=u.user_id AND r.status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space')) AS active_task_count,
        COALESCE((SELECT SUM(CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.size_bytes') ELSE 0 END)
          FROM remote_entity e WHERE e.user_id=u.user_id AND e.entity_type='media'
          AND COALESCE(json_extract(e.payload_json,'$.status'),'')<>'deleted'),0) AS media_usage_bytes
        FROM flowlens_user u {where} ORDER BY u.created_at DESC LIMIT ? OFFSET ?"""
        args.extend((limit, offset))
        return await asyncio.to_thread(self._query, sql, tuple(args))

    async def get_user_resource_summary(self, user_id: str) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._query,
            """SELECT u.user_id,u.username,u.display_name,u.role,u.status,
            u.must_change_password,u.max_douyin_connections,u.max_queued_tasks,u.media_quota_bytes,
            u.created_at,u.activated_at,u.last_login_at,u.suspended_at,u.updated_at,
            (SELECT COUNT(*) FROM douyin_connection c WHERE c.user_id=u.user_id AND c.status<>'disconnected') AS douyin_connection_count,
            (SELECT COUNT(*) FROM remote_crawl_run r WHERE r.user_id=u.user_id AND r.status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space')) AS active_task_count,
            COALESCE((SELECT SUM(CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.size_bytes') ELSE 0 END)
              FROM remote_entity e WHERE e.user_id=u.user_id AND e.entity_type='media'
              AND COALESCE(json_extract(e.payload_json,'$.status'),'')<>'deleted'),0) AS media_usage_bytes
            FROM flowlens_user u WHERE u.user_id=? LIMIT 1""",
            (user_id,),
        )
        return rows[0] if rows else None

    async def count_users(self, *, search: str | None = None, status: str | None = None) -> int:
        clauses: list[str] = []
        args: list[Any] = []
        if search:
            clauses.append("(normalized_username LIKE ? OR display_name LIKE ?)")
            term = f"%{search.lower()}%"
            args.extend((term, term))
        if status:
            clauses.append("status=?")
            args.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await asyncio.to_thread(
            self._query, f"SELECT COUNT(*) AS total FROM flowlens_user{where}", tuple(args)
        )
        return int(rows[0]["total"] if rows else 0)

    async def update_user_profile(
        self, user_id: str, *, display_name: str | None = None,
        username: str | None = None, normalized_username: str | None = None,
        max_douyin_connections: int | None = None, max_queued_tasks: int | None = None,
        media_quota_bytes: int | None = None,
    ) -> dict[str, Any] | None:
        fields = ["updated_at=?"]
        values: list[Any] = [_now()]
        for field, value in (
            ("display_name", display_name), ("username", username),
            ("normalized_username", normalized_username),
            ("max_douyin_connections", max_douyin_connections),
            ("max_queued_tasks", max_queued_tasks), ("media_quota_bytes", media_quota_bytes),
        ):
            if value is not None:
                fields.append(f"{field}=?")
                values.append(value)
        values.append(user_id)
        async with self._lock:
            await asyncio.to_thread(
                self._execute, f"UPDATE flowlens_user SET {','.join(fields)} WHERE user_id=?", values
            )
        return await self.get_user(user_id)

    async def set_user_password(
        self, user_id: str, password_hash: str, *, temporary_expires_at: str | None,
        must_change_password: bool, activate: bool = False,
    ) -> None:
        now = _now()
        status_sql = ",status='active',activated_at=COALESCE(activated_at,?)" if activate else ""
        args: list[Any] = [
            password_hash, int(must_change_password), temporary_expires_at, now,
        ]
        if activate:
            args.append(now)
        args.append(user_id)
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE flowlens_user SET password_hash=?,must_change_password=?,"
                f"temporary_password_expires_at=?,updated_at=?{status_sql} WHERE user_id=?",
                args,
            )

    async def set_user_status(self, user_id: str, status: str) -> None:
        now = _now()
        suspended_at = now if status == "suspended" else None
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE flowlens_user SET status=?,suspended_at=?,updated_at=? WHERE user_id=?",
                (status, suspended_at, now, user_id),
            )

    async def record_login(self, user_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute, "UPDATE flowlens_user SET last_login_at=?,updated_at=? WHERE user_id=?",
                (_now(), _now(), user_id),
            )

    async def consume_temporary_password(self, user_id: str) -> None:
        """Make the temporary credential unusable after its first successful login."""
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE flowlens_user SET temporary_password_expires_at=NULL,updated_at=? WHERE user_id=? AND must_change_password=1",
                (_now(), user_id),
            )

    async def create_user_session(self, item: dict[str, Any]) -> None:
        sql = """INSERT INTO user_session(
        session_id,user_id,session_token_hash,csrf_token_hash,created_at,last_seen_at,
        idle_expires_at,absolute_expires_at) VALUES(?,?,?,?,?,?,?,?)"""
        values = (
            item["session_id"], item["user_id"], item["session_token_hash"],
            item["csrf_token_hash"], item["created_at"], item["last_seen_at"],
            item["idle_expires_at"], item["absolute_expires_at"],
        )
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, values)

    async def get_user_session(self, token_hash: str) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._query,
            """SELECT s.*,u.username,u.normalized_username,u.display_name,u.role,u.status,
            u.must_change_password,u.temporary_password_expires_at,u.max_douyin_connections,
            u.max_queued_tasks,u.media_quota_bytes
            FROM user_session s JOIN flowlens_user u ON u.user_id=s.user_id
            WHERE s.session_token_hash=?""",
            (token_hash,),
        )
        return rows[0] if rows else None

    async def touch_user_session(self, session_id: str, last_seen_at: str, idle_expires_at: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE user_session SET last_seen_at=?,idle_expires_at=? WHERE session_id=? AND revoked_at IS NULL",
                (last_seen_at, idle_expires_at, session_id),
            )

    async def revoke_user_sessions(
        self, user_id: str, reason: str, *, except_session_id: str | None = None,
    ) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                self._revoke_user_sessions_sync, user_id, reason, except_session_id
            )

    def _revoke_user_sessions_sync(
        self, user_id: str, reason: str, except_session_id: str | None,
    ) -> int:
        with self._connect() as db:
            sql = "UPDATE user_session SET revoked_at=?,revoked_reason=? WHERE user_id=? AND revoked_at IS NULL"
            args: list[Any] = [_now(), reason, user_id]
            if except_session_id:
                sql += " AND session_id<>?"
                args.append(except_session_id)
            cursor = db.execute(sql, args)
            return int(cursor.rowcount or 0)

    async def revoke_session(self, session_id: str, reason: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE user_session SET revoked_at=?,revoked_reason=? WHERE session_id=? AND revoked_at IS NULL",
                (_now(), reason, session_id),
            )

    async def record_login_attempt(
        self, username_hash: str, source_ip_hash: str, success: bool,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "INSERT INTO auth_login_attempt(attempt_id,username_hash,source_ip_hash,success,created_at) VALUES(?,?,?,?,?)",
                (uuid.uuid4().hex, username_hash, source_ip_hash, int(success), _now()),
            )

    async def recent_login_attempts(self, username_hash: str, source_ip_hash: str, since: str):
        return await asyncio.to_thread(
            self._query,
            """SELECT username_hash,source_ip_hash,success,created_at FROM auth_login_attempt
            WHERE created_at>=? AND (username_hash=? OR source_ip_hash=?) ORDER BY created_at DESC""",
            (since, username_hash, source_ip_hash),
        )

    async def cleanup_auth_records(self, before: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute, "DELETE FROM auth_login_attempt WHERE created_at<?", (before,)
            )

    async def cleanup_expired_sessions(self, now: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "DELETE FROM user_session WHERE (revoked_at IS NOT NULL OR absolute_expires_at<?) AND created_at<?",
                (now, now),
            )

    async def add_audit_event(
        self, *, actor_user_id: str | None, action: str, target_type: str,
        target_id: str | None, result: str = "success",
        context: dict[str, Any] | None = None, request_id: str | None = None,
    ) -> None:
        safe_context = context or {}
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                """INSERT INTO audit_event(audit_id,actor_user_id,action,target_type,target_id,
                result,sanitized_context_json,request_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, actor_user_id, action, target_type, target_id, result,
                 json.dumps(safe_context, ensure_ascii=False), request_id, _now()),
            )

    async def count_active_connections(self, user_id: str) -> int:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT COUNT(*) AS total FROM douyin_connection WHERE user_id=? AND status<>'disconnected'",
            (user_id,),
        )
        return int(rows[0]["total"] if rows else 0)

    async def count_active_remote_runs(self, user_id: str) -> int:
        rows = await asyncio.to_thread(
            self._query,
            """SELECT COUNT(*) AS total FROM remote_crawl_run WHERE user_id=?
            AND status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space')""",
            (user_id,),
        )
        return int(rows[0]["total"] if rows else 0)

    async def reserved_user_media_bytes(self, user_id: str) -> int:
        """Return media bytes promised to unfinished remote runs.

        The reservation prevents a user from queuing several downloads that each
        individually fit the quota but exceed it in aggregate. Actual completed
        media usage is accounted separately from remote entities.
        """
        rows = await asyncio.to_thread(
            self._query,
            """SELECT sanitized_config_json FROM remote_crawl_run WHERE user_id=?
            AND status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space')""",
            (user_id,),
        )
        reserved = 0
        for row in rows:
            try:
                config = json.loads(row.get("sanitized_config_json") or "{}")
            except (TypeError, ValueError):
                continue
            if config.get("download_media"):
                reserved += max(0, int(config.get("max_media_total_bytes") or 0))
        return reserved

    async def pause_user_remote_runs(self, user_id: str) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._pause_user_remote_runs_sync, user_id)

    def _pause_user_remote_runs_sync(self, user_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE remote_crawl_run SET status='paused',error_type='account_suspended',
                error_message='账号已被管理员暂停' WHERE user_id=?
                AND status IN ('queued','running','pausing','waiting_for_login','waiting_for_space')""",
                (user_id,),
            )
            return int(cursor.rowcount or 0)

    async def save_connection(self, item: dict[str, Any]) -> None:
        sql = """INSERT INTO douyin_connection(connection_id,user_id,worker_id,profile_id,status,creator_hash,masked_nickname,last_verified_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET
        status=excluded.status,creator_hash=excluded.creator_hash,masked_nickname=excluded.masked_nickname,
        last_verified_at=excluded.last_verified_at,updated_at=excluded.updated_at"""
        values = (
            item["connection_id"], item["user_id"], item["worker_id"], item["profile_id"],
            item.get("status", "creating"), item.get("creator_hash"), item.get("masked_nickname"),
            item.get("last_verified_at"), item.get("created_at", _now()), item.get("updated_at", _now()),
        )
        async with self._lock:
            await asyncio.to_thread(self._execute, sql, values)

    async def list_user_connections(self, user_id: str):
        return await asyncio.to_thread(
            self._query,
            "SELECT connection_id,worker_id,status,creator_hash,masked_nickname,display_name,remark,last_verified_at,created_at,updated_at FROM douyin_connection WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )

    async def list_connections_requiring_verification(self, limit: int = 100):
        return await asyncio.to_thread(
            self._query,
            """SELECT c.connection_id,c.worker_id,c.status,c.masked_nickname,c.display_name,
            c.remark,c.last_verified_at,c.updated_at,u.display_name AS user_display_name,
            w.name AS worker_name,w.status AS worker_status
            FROM douyin_connection c
            LEFT JOIN flowlens_user u ON u.user_id=c.user_id
            LEFT JOIN flowlens_worker w ON w.worker_id=c.worker_id
            WHERE c.status IN ('verification_required','risk_controlled')
            ORDER BY c.updated_at ASC LIMIT ?""",
            (min(max(limit, 1), 200),),
        )

    async def get_connection(self, connection_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM douyin_connection WHERE connection_id=?", (connection_id,))
        return rows[0] if rows else None

    async def get_user_connection(self, connection_id: str, user_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM douyin_connection WHERE connection_id=? AND user_id=?", (connection_id, user_id))
        return rows[0] if rows else None

    async def update_connection(self, connection_id: str, status: str, *, creator_hash: str | None = None,
                                masked_nickname: str | None = None) -> None:
        verified = _now() if status == "connected" else None
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "UPDATE douyin_connection SET status=?,creator_hash=COALESCE(?,creator_hash),masked_nickname=COALESCE(?,masked_nickname),last_verified_at=COALESCE(?,last_verified_at),updated_at=? WHERE connection_id=?",
                (status, creator_hash, masked_nickname, verified, _now(), connection_id),
            )

    async def update_connection_labels(
        self, connection_id: str, *, display_name: str | None, remark: str | None,
    ) -> None:
        fields = ["updated_at=?"]
        values: list[Any] = [_now()]
        if display_name is not None:
            fields.append("display_name=?")
            values.append(display_name)
        if remark is not None:
            fields.append("remark=?")
            values.append(remark)
        values.append(connection_id)
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                f"UPDATE douyin_connection SET {','.join(fields)} WHERE connection_id=?",
                values,
            )

    async def create_remote_run(self, item: dict[str, Any]) -> str:
        run_id = item.get("run_id") or uuid.uuid4().hex
        safe = dict(item["config"])
        for key in ("cookies", "static_proxy_url", "browser_profile_id"):
            safe.pop(key, None)
        values = (
            run_id, item["user_id"], item["connection_id"], item["worker_id"],
            item.get("worker_run_id"), item.get("status", "queued"), item.get("stage", "discover"),
            json.dumps(safe, ensure_ascii=False), _now(),
        )
        async with self._lock:
            await asyncio.to_thread(
                self._execute,
                "INSERT INTO remote_crawl_run(run_id,user_id,connection_id,worker_id,worker_run_id,status,stage,sanitized_config_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                values,
            )
        return run_id

    async def get_user_remote_run(self, run_id: str, user_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM remote_crawl_run WHERE run_id=? AND user_id=?", (run_id, user_id))
        return rows[0] if rows else None

    async def get_remote_run(self, run_id: str):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM remote_crawl_run WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    async def delete_user_remote_run_history(self, run_id: str, user_id: str) -> bool:
        terminal = {"completed", "cancelled", "partial", "failed"}
        async with self._lock:
            return await asyncio.to_thread(
                self._delete_user_remote_run_history_sync, run_id, user_id, terminal,
            )

    def _delete_user_remote_run_history_sync(
        self, run_id: str, user_id: str, terminal: set[str],
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT status FROM remote_crawl_run WHERE run_id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if not row or row["status"] not in terminal:
                return False
            cursor = db.execute(
                "DELETE FROM remote_crawl_run WHERE run_id=? AND user_id=?",
                (run_id, user_id),
            )
            return bool(cursor.rowcount)

    async def list_user_remote_runs(
        self,
        user_id: str,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        connection_id: str | None = None,
    ):
        clauses = ["user_id=?"]
        args: list[Any] = [user_id]
        if status:
            clauses.append("status=?")
            args.append(status)
        if connection_id:
            clauses.append("connection_id=?")
            args.append(connection_id)
        args.extend((limit, offset))
        return await asyncio.to_thread(
            self._query,
            f"SELECT * FROM remote_crawl_run WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(args),
        )

    async def count_user_remote_runs(
        self, user_id: str, status: str | None = None, connection_id: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) AS total FROM remote_crawl_run WHERE user_id=?"
        args: list[Any] = [user_id]
        if status:
            sql += " AND status=?"
            args.append(status)
        if connection_id:
            sql += " AND connection_id=?"
            args.append(connection_id)
        rows = await asyncio.to_thread(self._query, sql, tuple(args))
        return int(rows[0]["total"] if rows else 0)

    async def user_remote_run_status_counts(self, user_id: str) -> dict[str, int]:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT status,COUNT(*) AS total FROM remote_crawl_run WHERE user_id=? GROUP BY status",
            (user_id,),
        )
        return {str(row["status"]): int(row["total"]) for row in rows}

    async def list_remote_runs_global(self, limit: int = 100, offset: int = 0):
        return await asyncio.to_thread(
            self._query,
            """SELECT r.run_id,r.stage,r.status,r.created_at,r.sanitized_config_json,
            u.display_name AS user_display_name,c.display_name,c.remark,c.masked_nickname,
            w.name AS worker_name,
            COALESCE(json_extract(r.sanitized_config_json,'$.crawler_type'),'unknown') AS crawler_type
            FROM remote_crawl_run r
            LEFT JOIN flowlens_user u ON u.user_id=r.user_id
            LEFT JOIN douyin_connection c ON c.connection_id=r.connection_id
            LEFT JOIN flowlens_worker w ON w.worker_id=r.worker_id
            ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )

    async def count_remote_runs_global(self) -> int:
        rows = await asyncio.to_thread(self._query, "SELECT COUNT(*) AS total FROM remote_crawl_run")
        return int(rows[0]["total"] if rows else 0)

    async def update_remote_run(self, run_id: str, status: str, *, stage: str | None = None,
                                error_type: str | None = None, error_message: str | None = None,
                                worker_run_id: str | None = None) -> None:
        fields, values = ["status=?", "error_type=?", "error_message=?"], [status,error_type,error_message]
        if stage: fields.append("stage=?"); values.append(stage)
        if worker_run_id: fields.append("worker_run_id=?"); values.append(worker_run_id)
        if status == "running": fields.append("started_at=COALESCE(started_at,?)"); values.append(_now())
        if status in {"completed","failed","partial","cancelled"}: fields.append("finished_at=?"); values.append(_now())
        values.append(run_id)
        async with self._lock:
            await asyncio.to_thread(self._execute, f"UPDATE remote_crawl_run SET {','.join(fields)} WHERE run_id=?", values)

    async def store_remote_result(self, item: dict[str, Any]) -> bool:
        from .worker_security import sanitize_worker_payload
        payload = sanitize_worker_payload(item["payload"])
        values = (
            item["source_event_id"],item["user_id"],item["connection_id"],item["run_id"],
            item["worker_id"],item["entity_type"],item["entity_id"],json.dumps(payload,ensure_ascii=False),
            item.get("observed_at"),_now(),
        )
        async with self._lock:
            return await asyncio.to_thread(self._store_remote_result_sync, values)

    def _store_remote_result_sync(self, values: tuple) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO remote_result(source_event_id,user_id,connection_id,run_id,worker_id,entity_type,entity_id,payload_json,observed_at,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            if cursor.rowcount and values[5] not in {"aweme_metric", "creator_metric", "log"}:
                db.execute(
                    """INSERT INTO remote_entity(user_id,entity_type,entity_id,connection_id,run_id,worker_id,payload_json,source_event_id,observed_at,synced_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,entity_type,entity_id) DO UPDATE SET
                    connection_id=excluded.connection_id,run_id=excluded.run_id,worker_id=excluded.worker_id,
                    payload_json=excluded.payload_json,source_event_id=excluded.source_event_id,
                    observed_at=excluded.observed_at,synced_at=excluded.synced_at""",
                    (values[1],values[5],values[6],values[2],values[3],values[4],values[7],values[0],values[8],values[9]),
                )
            return bool(cursor.rowcount)

    async def list_user_remote_results(
        self, user_id: str, entity_type: str, limit: int = 50, offset: int = 0,
        connection_id: str | None = None,
    ):
        table = "remote_result" if entity_type in {"aweme_metric", "creator_metric", "log"} else "remote_entity"
        connection_clause = " AND connection_id=?" if connection_id else ""
        args: tuple[Any, ...] = (
            (user_id, entity_type, connection_id, limit, offset) if connection_id
            else (user_id, entity_type, limit, offset)
        )
        return await asyncio.to_thread(
            self._query,
            f"SELECT source_event_id,connection_id,run_id,entity_type,entity_id,payload_json,observed_at,synced_at FROM {table} WHERE user_id=? AND entity_type=?{connection_clause} ORDER BY synced_at DESC LIMIT ? OFFSET ?",
            args,
        )

    async def count_user_remote_results(
        self, user_id: str, entity_type: str, connection_id: str | None = None,
    ) -> int:
        table = "remote_result" if entity_type in {"aweme_metric", "creator_metric", "log"} else "remote_entity"
        connection_clause = " AND connection_id=?" if connection_id else ""
        args = (user_id, entity_type, connection_id) if connection_id else (user_id, entity_type)
        rows = await asyncio.to_thread(
            self._query,
            f"SELECT COUNT(*) AS total FROM {table} WHERE user_id=? AND entity_type=?{connection_clause}",
            args,
        )
        return int(rows[0]["total"] if rows else 0)

    async def remote_result_counts(self, user_id: str) -> dict[str, int]:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT entity_type,COUNT(*) AS total FROM remote_entity WHERE user_id=? GROUP BY entity_type",
            (user_id,),
        )
        values = {str(row["entity_type"]): int(row["total"]) for row in rows}
        comments = await asyncio.to_thread(
            self._query,
            "SELECT payload_json FROM remote_entity WHERE user_id=? AND entity_type='comment'",
            (user_id,),
        )
        replies = 0
        for row in comments:
            try:
                replies += int(json.loads(row["payload_json"] or "{}").get("level") or 1) == 2
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return {
            "awemes": values.get("aweme", 0),
            "creators": values.get("creator", 0),
            "topics": values.get("topic", 0),
            "comments": values.get("comment", 0),
            "replies": replies,
            "transcripts": values.get("transcript", 0),
            "media": values.get("media", 0),
        }

    async def get_user_remote_result(self, user_id: str, entity_type: str, entity_id: str):
        table = "remote_result" if entity_type in {"aweme_metric", "creator_metric", "log"} else "remote_entity"
        rows = await asyncio.to_thread(
            self._query,
            f"SELECT * FROM {table} WHERE user_id=? AND entity_type=? AND entity_id=? ORDER BY synced_at DESC LIMIT 1",
            (user_id,entity_type,entity_id),
        )
        return rows[0] if rows else None

    async def update_remote_media_status(
        self, asset_id: str, worker_id: str, status: str, *,
        user_id: str | None = None, deleted: bool = False,
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_remote_media_status_sync, asset_id, worker_id, status, user_id, deleted
            )

    def _update_remote_media_status_sync(
        self, asset_id: str, worker_id: str, status: str,
        user_id: str | None, deleted: bool,
    ) -> bool:
        with self._connect() as db:
            user_clause = " AND user_id=?" if user_id else ""
            args: tuple[Any, ...] = (asset_id, worker_id, user_id) if user_id else (asset_id, worker_id)
            row = db.execute(
                f"SELECT * FROM remote_entity WHERE entity_type='media' AND entity_id=? AND worker_id=?{user_clause}",
                args,
            ).fetchone()
            if not row:
                return False
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            payload["status"] = status
            if deleted:
                payload.update({"size_bytes":0,"deleted_at":_now()})
            rendered = json.dumps(payload, ensure_ascii=False)
            db.execute(
                "UPDATE remote_entity SET payload_json=?,synced_at=? WHERE user_id=? AND entity_type='media' AND entity_id=?",
                (rendered, _now(), row["user_id"], asset_id),
            )
            return True

    async def save_schedule(self, item: dict[str, Any], schedule_id: str | None = None) -> str:
        schedule_id = schedule_id or uuid.uuid4().hex
        now = _now()
        safe_config = dict(item["config"])
        safe_config.pop("cookies", None)
        safe_config.pop("static_proxy_url", None)
        values = (schedule_id, item["name"], int(item.get("enabled", True)), item["platform"],
                  item["crawler_type"], item["source"], item["interval_type"], int(item.get("interval_value", 1)),
                  item.get("run_at"), item.get("timezone", "Asia/Shanghai"), json.dumps(safe_config, ensure_ascii=False),
                  item.get("last_run_at"), item.get("next_run_at"), "run_once", now, now,
                  item.get("user_id"), item.get("connection_id"))
        sql = """INSERT INTO schedule(schedule_id,name,enabled,platform,crawler_type,source,interval_type,interval_value,
        run_at,timezone,config_json,last_run_at,next_run_at,misfire_policy,created_at,updated_at,user_id,connection_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(schedule_id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,source=excluded.source,
        interval_type=excluded.interval_type,interval_value=excluded.interval_value,run_at=excluded.run_at,
        timezone=excluded.timezone,config_json=excluded.config_json,next_run_at=excluded.next_run_at,
        user_id=COALESCE(excluded.user_id,schedule.user_id),connection_id=COALESCE(excluded.connection_id,schedule.connection_id),updated_at=excluded.updated_at"""
        async with self._lock: await asyncio.to_thread(self._execute, sql, values)
        return schedule_id

    async def list_schedules(self):
        return await asyncio.to_thread(self._query, "SELECT * FROM schedule ORDER BY created_at DESC")

    async def list_user_schedules(self, user_id: str):
        return await asyncio.to_thread(
            self._query, "SELECT * FROM schedule WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )

    async def get_schedule(self, schedule_id):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM schedule WHERE schedule_id=?", (schedule_id,))
        return rows[0] if rows else None

    async def get_user_schedule(self, schedule_id: str, user_id: str):
        rows = await asyncio.to_thread(
            self._query, "SELECT * FROM schedule WHERE schedule_id=? AND user_id=?", (schedule_id, user_id)
        )
        return rows[0] if rows else None

    async def delete_schedule(self, schedule_id):
        async with self._lock: await asyncio.to_thread(self._execute, "DELETE FROM schedule WHERE schedule_id=?", (schedule_id,))

    async def delete_user_schedule(self, schedule_id: str, user_id: str):
        async with self._lock:
            await asyncio.to_thread(
                self._execute, "DELETE FROM schedule WHERE schedule_id=? AND user_id=?", (schedule_id, user_id)
            )

    async def due_schedules(self, now: str):
        return await asyncio.to_thread(self._query, "SELECT * FROM schedule WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?", (now,))

    async def mark_schedule_run(self, schedule_id: str, last_run: str, next_run: str | None):
        async with self._lock: await asyncio.to_thread(self._execute,
            "UPDATE schedule SET last_run_at=?,next_run_at=?,updated_at=? WHERE schedule_id=?", (last_run,next_run,_now(),schedule_id))

    async def schedule_has_active_run(self, schedule_id: str) -> bool:
        local_rows = await asyncio.to_thread(self._query,
            "SELECT 1 FROM crawl_run WHERE status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space') AND config_json LIKE ? LIMIT 1",
            (f'%"schedule_id": "{schedule_id}"%',))
        remote_rows = await asyncio.to_thread(self._query,
            "SELECT 1 FROM remote_crawl_run WHERE status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space') AND sanitized_config_json LIKE ? LIMIT 1",
            (f'%"schedule_id": "{schedule_id}"%',))
        return bool(local_rows or remote_rows)

    async def entity_exists(self, platform: str, entity_type: str, entity_id: str) -> bool:
        rows = await asyncio.to_thread(self._query, "SELECT 1 FROM entity_state WHERE platform=? AND entity_type=? AND entity_id=?", (platform,entity_type,entity_id))
        return bool(rows)

    async def touch_entity(self, platform: str, entity_type: str, entity_id: str, run_id: str):
        now = _now()
        sql = """INSERT INTO entity_state(platform,entity_type,entity_id,first_seen_at,last_seen_at,last_run_id)
        VALUES(?,?,?,?,?,?) ON CONFLICT(platform,entity_type,entity_id) DO UPDATE SET
        last_seen_at=excluded.last_seen_at,last_run_id=excluded.last_run_id"""
        async with self._lock: await asyncio.to_thread(self._execute, sql, (platform,entity_type,entity_id,now,now,run_id))

    def _query(self, sql, args=()):
        with self._connect() as db: return [dict(row) for row in db.execute(sql,args).fetchall()]


task_store = TaskStore()
