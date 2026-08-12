"""Persistent local task, schedule, log, and media catalog for FlowLens."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "flowlens" / "tasks.sqlite"


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
            """)
            db.execute("UPDATE crawl_run SET status='partial', finished_at=? WHERE status IN ('running','pausing')", (_now(),))

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

    async def list_runs(self, limit=100, offset=0):
        return await asyncio.to_thread(self._query, "SELECT * FROM crawl_run ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit,offset))

    async def get_run(self, run_id):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM crawl_run WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

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
        return asset_id

    async def list_media(self, limit=100, offset=0, aweme_id: str | None = None):
        if aweme_id:
            return await asyncio.to_thread(self._query, "SELECT * FROM media_asset WHERE aweme_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?", (aweme_id,limit,offset))
        return await asyncio.to_thread(self._query, "SELECT * FROM media_asset ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit,offset))

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

    async def save_schedule(self, item: dict[str, Any], schedule_id: str | None = None) -> str:
        schedule_id = schedule_id or uuid.uuid4().hex
        now = _now()
        safe_config = dict(item["config"])
        safe_config.pop("cookies", None)
        safe_config.pop("static_proxy_url", None)
        values = (schedule_id, item["name"], int(item.get("enabled", True)), item["platform"],
                  item["crawler_type"], item["source"], item["interval_type"], int(item.get("interval_value", 1)),
                  item.get("run_at"), item.get("timezone", "Asia/Shanghai"), json.dumps(safe_config, ensure_ascii=False),
                  item.get("last_run_at"), item.get("next_run_at"), "run_once", now, now)
        sql = """INSERT INTO schedule(schedule_id,name,enabled,platform,crawler_type,source,interval_type,interval_value,
        run_at,timezone,config_json,last_run_at,next_run_at,misfire_policy,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(schedule_id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,source=excluded.source,
        interval_type=excluded.interval_type,interval_value=excluded.interval_value,run_at=excluded.run_at,
        timezone=excluded.timezone,config_json=excluded.config_json,next_run_at=excluded.next_run_at,updated_at=excluded.updated_at"""
        async with self._lock: await asyncio.to_thread(self._execute, sql, values)
        return schedule_id

    async def list_schedules(self):
        return await asyncio.to_thread(self._query, "SELECT * FROM schedule ORDER BY created_at DESC")

    async def get_schedule(self, schedule_id):
        rows = await asyncio.to_thread(self._query, "SELECT * FROM schedule WHERE schedule_id=?", (schedule_id,))
        return rows[0] if rows else None

    async def delete_schedule(self, schedule_id):
        async with self._lock: await asyncio.to_thread(self._execute, "DELETE FROM schedule WHERE schedule_id=?", (schedule_id,))

    async def due_schedules(self, now: str):
        return await asyncio.to_thread(self._query, "SELECT * FROM schedule WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?", (now,))

    async def mark_schedule_run(self, schedule_id: str, last_run: str, next_run: str | None):
        async with self._lock: await asyncio.to_thread(self._execute,
            "UPDATE schedule SET last_run_at=?,next_run_at=?,updated_at=? WHERE schedule_id=?", (last_run,next_run,_now(),schedule_id))

    async def schedule_has_active_run(self, schedule_id: str) -> bool:
        rows = await asyncio.to_thread(self._query,
            "SELECT 1 FROM crawl_run WHERE status IN ('queued','running','pausing','paused','waiting_for_login','waiting_for_space') AND config_json LIKE ? LIMIT 1",
            (f'%"schedule_id": "{schedule_id}"%',))
        return bool(rows)

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
