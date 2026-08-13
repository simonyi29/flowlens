import csv
import io
import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/library", tags=["library"])
DB_PATH = Path(__file__).resolve().parents[2] / "database" / "sqlite_tables.db"
TASK_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "flowlens" / "tasks.sqlite"


def _db():
    if not DB_PATH.exists(): raise HTTPException(404, "SQLite content database not found")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; return conn


def _ensure_fts(db: sqlite3.Connection) -> bool:
    try:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='douyin_search_fts'"
        ).fetchone()
        if exists:
            return True
        db.executescript("""
        CREATE VIRTUAL TABLE douyin_search_fts USING fts5(entity_type, entity_id, text);
        INSERT INTO douyin_search_fts SELECT 'aweme',aweme_id,COALESCE(title,'')||' '||COALESCE(desc,'') FROM douyin_aweme;
        INSERT INTO douyin_search_fts SELECT 'transcript',aweme_id,COALESCE(full_text,'') FROM douyin_transcript;
        INSERT INTO douyin_search_fts SELECT 'comment',comment_id,COALESCE(content,'') FROM douyin_aweme_comment;
        CREATE TRIGGER IF NOT EXISTS flowlens_fts_aweme_insert AFTER INSERT ON douyin_aweme BEGIN
          INSERT INTO douyin_search_fts VALUES('aweme',new.aweme_id,COALESCE(new.title,'')||' '||COALESCE(new.desc,'')); END;
        CREATE TRIGGER IF NOT EXISTS flowlens_fts_aweme_update AFTER UPDATE ON douyin_aweme BEGIN
          DELETE FROM douyin_search_fts WHERE entity_type='aweme' AND entity_id=old.aweme_id;
          INSERT INTO douyin_search_fts VALUES('aweme',new.aweme_id,COALESCE(new.title,'')||' '||COALESCE(new.desc,'')); END;
        CREATE TRIGGER IF NOT EXISTS flowlens_fts_transcript_insert AFTER INSERT ON douyin_transcript BEGIN
          INSERT INTO douyin_search_fts VALUES('transcript',new.aweme_id,COALESCE(new.full_text,'')); END;
        CREATE TRIGGER IF NOT EXISTS flowlens_fts_transcript_update AFTER UPDATE ON douyin_transcript BEGIN
          DELETE FROM douyin_search_fts WHERE entity_type='transcript' AND entity_id=old.aweme_id;
          INSERT INTO douyin_search_fts VALUES('transcript',new.aweme_id,COALESCE(new.full_text,'')); END;
        CREATE TRIGGER IF NOT EXISTS flowlens_fts_comment_insert AFTER INSERT ON douyin_aweme_comment BEGIN
          INSERT INTO douyin_search_fts VALUES('comment',new.comment_id,COALESCE(new.content,'')); END;
        CREATE TRIGGER IF NOT EXISTS flowlens_fts_comment_update AFTER UPDATE ON douyin_aweme_comment BEGIN
          DELETE FROM douyin_search_fts WHERE entity_type='comment' AND entity_id=old.comment_id;
          INSERT INTO douyin_search_fts VALUES('comment',new.comment_id,COALESCE(new.content,'')); END;
        """)
        return True
    except sqlite3.Error:
        return False


def _paged_table(table: str, order: str, where: list[str], args: list, limit: int, offset: int):
    clause = " WHERE " + " AND ".join(where) if where else ""
    with _db() as db:
        total = db.execute(f"SELECT COUNT(*) FROM {table}{clause}", args).fetchone()[0]
        rows = [dict(r) for r in db.execute(
            f"SELECT * FROM {table}{clause} ORDER BY {order} LIMIT ? OFFSET ?", args + [limit, offset]
        )]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/awemes")
async def list_awemes(q: str = "", creator_hash: str = "", source_topic: str = "",
                      source_keyword: str = "", min_likes: int | None = None,
                      max_likes: int | None = None, min_comments: int | None = None,
                      max_comments: int | None = None, min_plays: int | None = None,
                      max_plays: int | None = None, published_from: int | None = None,
                      published_to: int | None = None, transcript_status: str = "",
                      comment_status: str = "", download_status: str = "",
                      limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = [], []
    if q: where.append("(title LIKE ? OR desc LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
    if creator_hash: where.append("creator_hash=?"); args.append(creator_hash)
    if source_topic: where.append("source_topic=?"); args.append(source_topic)
    if source_keyword: where.append("source_keyword=?"); args.append(source_keyword)
    if min_likes is not None: where.append("liked_count>=?"); args.append(min_likes)
    if max_likes is not None: where.append("liked_count<=?"); args.append(max_likes)
    if min_comments is not None: where.append("comment_count>=?"); args.append(min_comments)
    if max_comments is not None: where.append("comment_count<=?"); args.append(max_comments)
    if min_plays is not None: where.append("play_count>=?"); args.append(min_plays)
    if max_plays is not None: where.append("play_count<=?"); args.append(max_plays)
    if published_from is not None: where.append("create_time>=?"); args.append(published_from)
    if published_to is not None: where.append("create_time<=?"); args.append(published_to)
    if transcript_status:
        where.append("EXISTS (SELECT 1 FROM douyin_transcript t WHERE t.aweme_id=douyin_aweme.aweme_id AND t.status=?)")
        args.append(transcript_status)
    if comment_status == "completed":
        where.append("EXISTS (SELECT 1 FROM douyin_aweme_comment c WHERE c.aweme_id=douyin_aweme.aweme_id)")
    elif comment_status == "empty":
        where.append("NOT EXISTS (SELECT 1 FROM douyin_aweme_comment c WHERE c.aweme_id=douyin_aweme.aweme_id)")
    media_db = TASK_DB_PATH
    if download_status:
        if media_db.exists():
            where.append("EXISTS (SELECT 1 FROM taskdb.media_asset m WHERE m.aweme_id=douyin_aweme.aweme_id AND m.status=?)")
            args.append(download_status)
        else:
            where.append("1=0")
    clause = " WHERE " + " AND ".join(where) if where else ""
    with _db() as db:
        if download_status and media_db.exists():
            db.execute("ATTACH DATABASE ? AS taskdb", (str(media_db),))
        total = db.execute("SELECT COUNT(*) FROM douyin_aweme" + clause, args).fetchone()[0]
        rows = [dict(r) for r in db.execute("SELECT * FROM douyin_aweme" + clause + " ORDER BY collected_at DESC LIMIT ? OFFSET ?", args+[limit,offset])]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/creators")
async def list_creators(q: str = "", min_followers: int | None = None,
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = [], []
    if q: where.append("(nickname LIKE ? OR signature LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
    if min_followers is not None: where.append("follower_count>=?"); args.append(min_followers)
    return _paged_table("douyin_creator", "collected_at DESC", where, args, limit, offset)


@router.get("/topics")
async def list_topics(q: str = "", limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = (["name LIKE ?"], [f"%{q}%"]) if q else ([], [])
    return _paged_table("douyin_topic", "collected_at DESC", where, args, limit, offset)


@router.get("/comments")
async def list_comments(aweme_id: str = "", q: str = "", level: int | None = Query(None, ge=1, le=2),
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = [], []
    if aweme_id: where.append("aweme_id=?"); args.append(aweme_id)
    if q: where.append("content LIKE ?"); args.append(f"%{q}%")
    if level is not None: where.append("level=?"); args.append(level)
    return _paged_table("douyin_aweme_comment", "create_time DESC", where, args, limit, offset)


@router.get("/transcripts")
async def list_transcripts(q: str = "", status: str = "", limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = [], []
    if q: where.append("full_text LIKE ?"); args.append(f"%{q}%")
    if status: where.append("status=?"); args.append(status)
    return _paged_table("douyin_transcript", "processed_at DESC", where, args, limit, offset)


@router.get("/creators/{creator_hash}/metrics")
async def creator_metrics(creator_hash: str):
    return _paged_table("douyin_creator_metric_snapshot", "observed_at", ["creator_hash=?"], [creator_hash], 500, 0)


@router.get("/awemes/{aweme_id}")
async def aweme_detail(aweme_id: str):
    with _db() as db:
        row = db.execute("SELECT * FROM douyin_aweme WHERE aweme_id=?", (aweme_id,)).fetchone()
        if not row: raise HTTPException(404, "Aweme not found")
        transcript = db.execute("SELECT * FROM douyin_transcript WHERE aweme_id=? ORDER BY processed_at DESC LIMIT 1", (aweme_id,)).fetchone()
        metrics = [dict(r) for r in db.execute("SELECT * FROM douyin_aweme_metric_snapshot WHERE aweme_id=? ORDER BY observed_at", (aweme_id,))]
        comments = [dict(r) for r in db.execute("SELECT * FROM douyin_aweme_comment WHERE aweme_id=? ORDER BY level,create_time LIMIT 5000", (aweme_id,))]
    roots, children = [], {}
    for item in comments:
        if int(item.get("level") or 1) == 1: roots.append(item)
        else: children.setdefault(item.get("root_comment_id") or item.get("parent_comment_id"), []).append(item)
    for root in roots: root["replies"] = children.get(root.get("comment_id"), [])
    return {"aweme":dict(row), "transcript":dict(transcript) if transcript else None, "metrics":metrics, "comments":roots}


@router.get("/search")
async def full_text_search(q: str = Query(min_length=1), limit: int = Query(50, ge=1, le=200)):
    term = f"%{q}%"
    with _db() as db:
        if _ensure_fts(db):
            try:
                hits=[dict(r) for r in db.execute("SELECT entity_type,entity_id,text FROM douyin_search_fts WHERE douyin_search_fts MATCH ? LIMIT ?",(q,limit))]
                return {"items":hits,"engine":"fts5"}
            except sqlite3.Error:
                pass
        awemes = [dict(r) for r in db.execute("SELECT aweme_id,title,desc FROM douyin_aweme WHERE title LIKE ? OR desc LIKE ? LIMIT ?", (term,term,limit))]
        transcripts = [dict(r) for r in db.execute("SELECT aweme_id,full_text,status FROM douyin_transcript WHERE full_text LIKE ? LIMIT ?", (term,limit))]
        comments = [dict(r) for r in db.execute("SELECT comment_id,aweme_id,content FROM douyin_aweme_comment WHERE content LIKE ? LIMIT ?", (term,limit))]
    return {"awemes":awemes,"transcripts":transcripts,"comments":comments,"engine":"like"}


@router.get("/stats")
async def library_stats():
    """Small local-only aggregates used by the content dashboard."""
    with _db() as db:
        counts = {
            "awemes": db.execute("SELECT COUNT(*) FROM douyin_aweme").fetchone()[0],
            "creators": db.execute("SELECT COUNT(*) FROM douyin_creator").fetchone()[0],
            "topics": db.execute("SELECT COUNT(*) FROM douyin_topic").fetchone()[0],
            "comments": db.execute("SELECT COUNT(*) FROM douyin_aweme_comment").fetchone()[0],
            "replies": db.execute("SELECT COUNT(*) FROM douyin_aweme_comment WHERE level=2").fetchone()[0],
            "transcripts": db.execute("SELECT COUNT(*) FROM douyin_transcript WHERE status IN ('native_completed','asr_completed')").fetchone()[0],
        }
        high_liked_comments = [dict(row) for row in db.execute(
            "SELECT comment_id,aweme_id,content,like_count FROM douyin_aweme_comment "
            "ORDER BY COALESCE(like_count,0) DESC LIMIT 10"
        )]
        topic_engagement = [dict(row) for row in db.execute(
            "SELECT source_topic AS topic,COUNT(*) AS aweme_count,"
            "AVG(COALESCE(liked_count,0)+COALESCE(comment_count,0)+COALESCE(share_count,0)) AS avg_engagement "
            "FROM douyin_aweme WHERE source_topic IS NOT NULL AND source_topic<>'' "
            "GROUP BY source_topic ORDER BY aweme_count DESC LIMIT 20"
        )]
    return {"counts": counts, "high_liked_comments": high_liked_comments, "topic_engagement": topic_engagement}


@router.get("/export")
async def export_awemes(
    format: str = "jsonl", q: str = "", creator_hash: str = "", source_topic: str = "",
    source_keyword: str = "", min_likes: int | None = None, max_likes: int | None = None,
    min_comments: int | None = None, max_comments: int | None = None,
    min_plays: int | None = None, max_plays: int | None = None,
    published_from: int | None = None, published_to: int | None = None,
    transcript_status: str = "", comment_status: str = "", download_status: str = "",
):
    data = await list_awemes(
        q=q, creator_hash=creator_hash, source_topic=source_topic, source_keyword=source_keyword,
        min_likes=min_likes, max_likes=max_likes, min_comments=min_comments,
        max_comments=max_comments, min_plays=min_plays, max_plays=max_plays,
        published_from=published_from, published_to=published_to,
        transcript_status=transcript_status, comment_status=comment_status,
        download_status=download_status, limit=500, offset=0,
    )
    if format == "jsonl":
        body = "".join(json.dumps(x,ensure_ascii=False)+"\n" for x in data["items"])
        return StreamingResponse(iter([body]), media_type="application/x-ndjson", headers={"Content-Disposition":"attachment; filename=awemes.jsonl"})
    if format == "csv":
        out=io.StringIO(); rows=data["items"]
        if rows:
            writer=csv.DictWriter(out, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=awemes.csv"})
    raise HTTPException(422, "format must be jsonl or csv")
