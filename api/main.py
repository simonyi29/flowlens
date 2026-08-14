# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
FlowLens WebUI API Server
Start command: uvicorn api.main:app --port 8080 --reload
Or: python -m api.main
"""
import asyncio
import os
import sys
import subprocess
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .routers import crawler_router, data_router, websocket_router, tasks_router, media_router, schedules_router, library_router, system_router, remote_router, worker_gateway_router, dashboard_router, auth_router, admin_users_router
from .services.task_store import task_store
from .services.schedule_runner import schedule_runner
from .services.crawler_manager import crawler_manager
from .services.auth import remote_mode
from .services.auth import identity_from_token, SESSION_COOKIE, validate_origin, csrf_token_for_session
import hmac

# Project root directory (used for running subprocesses like uv run main.py)
PROJECT_ROOT = Path(__file__).parent.parent

app = FastAPI(
    title="FlowLens WebUI API",
    description="API for controlling FlowLens from WebUI",
    version="1.3.0"
)

# Get webui static files directory
WEBUI_DIR = os.path.join(os.path.dirname(__file__), "webui")

# CORS configuration - allow the configured website plus local Vite servers.
public_origin = os.getenv("FLOWLENS_PUBLIC_ORIGIN", "").rstrip("/")
cors_origins = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",  # Backup port
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]
if public_origin and public_origin not in cors_origins:
    cors_origins.append(public_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def remote_browser_auth_boundary(request: Request, call_next):
    """Prevent legacy local APIs from bypassing website authentication.

    Worker enrollment/control remains on the separate ``/internal`` device
    authentication surface. Capabilities and login are the only public website
    endpoints in remote mode.
    """
    if not remote_mode() or not request.url.path.startswith("/api/"):
        return await call_next(request)
    compat = os.getenv("FLOWLENS_TRUSTED_HEADER_COMPAT", "false").lower() in {"1", "true", "yes"}
    compat_token = request.headers.get("x-flowlens-proxy-token", "")
    expected_compat_token = os.getenv("FLOWLENS_TRUSTED_PROXY_TOKEN", "")
    if compat and expected_compat_token and compat_token and hmac.compare_digest(expected_compat_token, compat_token):
        return await call_next(request)
    blocked_local_prefixes = ("/api/crawler", "/api/tasks", "/api/library", "/api/media", "/api/data")
    if request.url.path.startswith(blocked_local_prefixes):
        return JSONResponse(status_code=404, content={"detail": "local-only API is disabled in remote mode"})
    public = {
        "/api/health", "/api/system/capabilities", "/api/auth/login", "/api/auth/register",
    }
    if request.url.path in public:
        return await call_next(request)
    identity = await identity_from_token(request.cookies.get(SESSION_COOKIE))
    if identity is None:
        return JSONResponse(status_code=401, content={"detail": {
            "error_type": "authentication_required", "user_message": "请先登录 FlowLens。",
            "recoverable": True, "recommended_action": "login",
        }})
    auth_allowed = {"/api/auth/me", "/api/auth/change-password", "/api/auth/logout"}
    if (identity.must_change_password or identity.status == "pending_activation") and request.url.path not in auth_allowed:
        return JSONResponse(status_code=403, content={"detail": {
            "error_type": "password_change_required", "user_message": "首次登录需要先设置正式密码。",
            "recoverable": True, "recommended_action": "change_password",
        }})
    if identity.status == "suspended":
        return JSONResponse(status_code=423, content={"detail": {
            "error_type": "account_suspended", "user_message": "账号已暂停，请联系管理员。",
        }})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/api/auth/login":
        try:
            validate_origin(request)
        except Exception:
            return JSONResponse(status_code=403, content={"detail": {"error_type": "invalid_origin", "user_message": "请求来源未被允许。"}})
        provided = request.headers.get("x-csrf-token", "")
        expected = csrf_token_for_session(identity.session_token or "")
        if not provided or not hmac.compare_digest(provided, expected):
            return JSONResponse(status_code=403, content={"detail": {
                "error_type": "csrf_failed", "user_message": "安全令牌已失效，请刷新页面后重试。",
            }})
    request.state.identity = identity
    return await call_next(request)

# Register routers
app.include_router(crawler_router, prefix="/api")
app.include_router(data_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(media_router, prefix="/api")
app.include_router(schedules_router, prefix="/api")
app.include_router(library_router, prefix="/api")
app.include_router(system_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(admin_users_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(remote_router, prefix="/api")
app.include_router(worker_gateway_router)


@app.on_event("startup")
async def initialize_task_store():
    await task_store.initialize()
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    await task_store.cleanup_auth_records((now - timedelta(days=30)).isoformat())
    await task_store.cleanup_expired_sessions(now.isoformat())
    await crawler_manager.start_next_queued()
    schedule_runner.start()
    if remote_mode():
        problems = []
        if not os.getenv("FLOWLENS_AUTH_HASH_KEY"):
            problems.append("FLOWLENS_AUTH_HASH_KEY is not configured")
        if not os.getenv("FLOWLENS_PUBLIC_ORIGIN"):
            problems.append("FLOWLENS_PUBLIC_ORIGIN is not configured")
        if os.getenv("FLOWLENS_PUBLIC_ORIGIN", "").startswith("https://") and os.getenv("FLOWLENS_COOKIE_SECURE", "false").lower() not in {"1", "true", "yes"}:
            problems.append("FLOWLENS_COOKIE_SECURE must be true for HTTPS")
        if problems:
            print("[FlowLens auth health] " + "; ".join(problems))


@app.on_event("shutdown")
async def stop_schedule_runner():
    await schedule_runner.stop()


@app.get("/")
async def serve_frontend():
    """Return frontend page"""
    index_path = os.path.join(WEBUI_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": "FlowLens WebUI API",
        "version": "1.3.0",
        "docs": "/docs",
        "note": "WebUI not found, please build it first: cd webui && npm run build"
    }


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/env/check")
async def check_environment():
    """Check if the FlowLens environment is configured correctly."""
    try:
        # Run uv run main.py --help command to check environment
        # Use PROJECT_ROOT so it works regardless of where uvicorn was started
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            process = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [sys.executable, "main.py", "--help"],
                    capture_output=True,
                    timeout=30.0,
                    cwd=str(PROJECT_ROOT)
                )
            )
            stdout, stderr = process.stdout, process.stderr  # bytes
        else:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "main.py", "--help",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(PROJECT_ROOT)  # Project root directory
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0  # 30 seconds timeout
            )
        if process.returncode == 0:
            return {
                "success": True,
                "message": "FlowLens environment configured correctly",
                "output": stdout.decode("utf-8", errors="ignore")[:500]  # Truncate to first 500 characters
            }
        else:
            error_msg = stderr.decode("utf-8", errors="ignore") or stdout.decode("utf-8", errors="ignore")
            return {
                "success": False,
                "message": "Environment check failed",
                "error": error_msg[:500]
            }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "message": "Environment check timeout",
            "error": "Command execution exceeded 30 seconds"
        }
    except FileNotFoundError:
        return {
            "success": False,
            "message": "uv command not found",
            "error": "Please ensure uv is installed and configured in system PATH"
        }
    except Exception as e:
        return {
            "success": False,
            "message": "Environment check error",
            "error": f"{type(e).__name__}: {str(e) or 'Unknown'}"
        }


@app.get("/api/config/platforms")
async def get_platforms():
    """Get list of supported platforms"""
    return {
        "platforms": [
            {"value": "xhs", "label": "Xiaohongshu", "icon": "book-open"},
            {"value": "dy", "label": "Douyin", "icon": "music"},
            {"value": "ks", "label": "Kuaishou", "icon": "video"},
            {"value": "bili", "label": "Bilibili", "icon": "tv"},
            {"value": "wb", "label": "Weibo", "icon": "message-circle"},
            {"value": "tieba", "label": "Baidu Tieba", "icon": "messages-square"},
            {"value": "zhihu", "label": "Zhihu", "icon": "help-circle"},
        ]
    }


@app.get("/api/config/options")
async def get_config_options():
    """Get all configuration options"""
    return {
        "login_types": [
            {"value": "qrcode", "label": "QR Code Login"},
            {"value": "cookie", "label": "Cookie Login"},
        ],
        "crawler_types": [
            {"value": "search", "label": "Search Mode"},
            {"value": "detail", "label": "Detail Mode"},
            {"value": "creator", "label": "Creator Mode"},
            {"value": "topic", "label": "Douyin Topic Mode"},
        ],
        "save_options": [
            {"value": "jsonl", "label": "JSONL File"},
            {"value": "json", "label": "JSON File"},
            {"value": "csv", "label": "CSV File"},
            {"value": "excel", "label": "Excel File"},
            {"value": "sqlite", "label": "SQLite Database"},
            {"value": "db", "label": "MySQL Database"},
            {"value": "mongodb", "label": "MongoDB Database"},
        ],
    }


# Mount static resources - must be placed after all routes
if os.path.exists(WEBUI_DIR):
    assets_dir = os.path.join(WEBUI_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    # Mount logos directory
    logos_dir = os.path.join(WEBUI_DIR, "logos")
    if os.path.exists(logos_dir):
        app.mount("/logos", StaticFiles(directory=logos_dir), name="logos")
    # Mount other static files (e.g., vite.svg)
    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="webui-static")


if __name__ == "__main__":
    # In remote-worker mode a reverse proxy is the only public control surface.
    host = "127.0.0.1" if os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1","true","yes"} else "0.0.0.0"
    uvicorn.run(app, host=host, port=8080)
