import importlib.util
import os
import shutil
import socket
import sqlite3
import httpx
from fastapi import Depends
from pathlib import Path

from fastapi import APIRouter

from ..services.task_store import DB_PATH
from ..services.auth import Identity, optional_current_identity

router = APIRouter(prefix="/system", tags=["system"])
ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "data" / "douyin" / "media"


@router.get("/capabilities")
async def capabilities(
    identity: Identity | None = Depends(optional_current_identity),
):
    remote = os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}
    is_admin = bool(identity and identity.role == "admin" and not identity.must_change_password)
    return {
        "mode": "remote" if remote else "local",
        "current_role": "admin" if is_admin else "user",
        "features": {
            "remote_worker": remote,
            "local_crawl": not remote,
            "multiple_douyin_connections": True,
            "schedules": True,
            "media_stream": True,
            "asr": importlib.util.find_spec("faster_whisper") is not None,
            "admin": is_admin,
            "admin_console": is_admin,
        },
    }


def _directory_size(path: Path): return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


@router.get("/storage")
async def storage():
    MEDIA.mkdir(parents=True, exist_ok=True)
    usage=shutil.disk_usage(MEDIA)
    return {"media_bytes":_directory_size(MEDIA),"free_bytes":usage.free,"total_bytes":usage.total,"library_limit_bytes":20*1024**3,"min_free_bytes":10*1024**3}


@router.get("/health")
async def health():
    cdp=False; login_state="unknown"; risk_state=None
    try:
        with socket.create_connection(("127.0.0.1",9222),timeout=.5): cdp=True
    except OSError: pass
    if cdp:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                pages = (await client.get("http://127.0.0.1:9222/json")).json()
            urls = " ".join(str(page.get("url") or "") for page in pages).lower()
            if any(token in urls for token in ("captcha", "verify", "passport")):
                risk_state = "captcha_required"
            login_state = "login_page" if "login" in urls else "browser_available"
        except (httpx.HTTPError, ValueError):
            pass
    fts=False
    try:
        with sqlite3.connect(":memory:") as db: db.execute("CREATE VIRTUAL TABLE x USING fts5(value)"); fts=True
    except sqlite3.Error: pass
    whisper_installed = importlib.util.find_spec("faster_whisper") is not None
    cuda_devices = 0
    if importlib.util.find_spec("ctranslate2") is not None:
        try:
            import ctranslate2
            cuda_devices = int(ctranslate2.get_cuda_device_count())
        except Exception:
            cuda_devices = 0
    ffprobe_path = shutil.which("ffprobe")
    remote = os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}
    public_origin = os.getenv("FLOWLENS_PUBLIC_ORIGIN", "")
    auth_configuration = not remote or bool(
        os.getenv("FLOWLENS_AUTH_HASH_KEY") and public_origin and
        (not public_origin.startswith("https://") or os.getenv("FLOWLENS_COOKIE_SECURE", "false").lower() in {"1","true","yes"})
    )
    return {"status":"ok" if auth_configuration else "degraded","checks":{
        "cdp":{"ok":cdp,"detail":"127.0.0.1:9222","login_state":login_state,"risk_state":risk_state},
        "faster_whisper":{"ok":whisper_installed,"model":"small","device":"cuda" if cuda_devices else "cpu","compute_type":"float16" if cuda_devices else "int8","cuda_devices":cuda_devices},
        "ffprobe":{"ok":ffprobe_path is not None,"path":ffprobe_path,"fallback":"mime_header_sha256" if not ffprobe_path else None},
        "sqlite_fts5":{"ok":fts},
        "media_writable":{"ok":MEDIA.exists() and os.access(MEDIA,os.W_OK)},
        "task_database":{"ok":DB_PATH.parent.exists()},
        "remote_auth":{"ok":auth_configuration,"detail":"server session + CSRF" if auth_configuration else "配置 FLOWLENS_PUBLIC_ORIGIN、FLOWLENS_AUTH_HASH_KEY 和 HTTPS Secure Cookie"},
    }}
