import importlib.util
import hmac
import os
import shutil
import socket
import sqlite3
import httpx
from fastapi import Header
from pathlib import Path

from fastapi import APIRouter

from ..services.task_store import DB_PATH

router = APIRouter(prefix="/system", tags=["system"])
ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "data" / "douyin" / "media"


@router.get("/capabilities")
async def capabilities(
    x_flowlens_role: str | None = Header(None),
    x_flowlens_proxy_token: str | None = Header(None),
):
    remote = os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1", "true", "yes"}
    expected = os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    trusted = bool(
        remote
        and expected
        and x_flowlens_proxy_token
        and hmac.compare_digest(expected, x_flowlens_proxy_token)
    )
    is_admin = not remote or (trusted and x_flowlens_role == "admin")
    return {
        "mode": "remote" if remote else "local",
        "current_role": "admin" if is_admin else "user",
        "features": {
            "remote_worker": remote,
            "local_crawl": not remote,
            "schedules": True,
            "media_stream": True,
            "asr": importlib.util.find_spec("faster_whisper") is not None,
            "admin": is_admin,
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
    return {"status":"ok","checks":{
        "cdp":{"ok":cdp,"detail":"127.0.0.1:9222","login_state":login_state,"risk_state":risk_state},
        "faster_whisper":{"ok":whisper_installed,"model":"small","device":"cuda" if cuda_devices else "cpu","compute_type":"float16" if cuda_devices else "int8","cuda_devices":cuda_devices},
        "ffprobe":{"ok":ffprobe_path is not None,"path":ffprobe_path,"fallback":"mime_header_sha256" if not ffprobe_path else None},
        "sqlite_fts5":{"ok":fts},
        "media_writable":{"ok":MEDIA.exists() and os.access(MEDIA,os.W_OK)},
        "task_database":{"ok":DB_PATH.parent.exists()},
    }}
