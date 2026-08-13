"""FlowLens remote worker registration and long-running agent CLI."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

from .services.task_store import task_store
from .services.worker_agent import WorkerAgent
from .services.worker_identity import WorkerIdentityManager

ROOT = Path(__file__).resolve().parents[1]
WORKER_CONFIG = ROOT / "data" / "flowlens" / "worker" / "worker.json"


def _control_websocket_url(control_url: str) -> str:
    parsed = urlparse(control_url.rstrip("/"))
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("control URL must be an absolute HTTPS/WSS URL")
    if parsed.scheme in {"http", "ws"} and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("remote control URL must use TLS")
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    path = parsed.path.rstrip("/") + "/internal/flowlens/workers/connect"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _save_config(config: dict) -> None:
    WORKER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    WORKER_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(WORKER_CONFIG, 0o600)
    except OSError:
        pass


def register(control_url: str, enrollment_code: str, name: str) -> dict:
    _control_websocket_url(control_url)
    identity = WorkerIdentityManager()
    public_key = identity.load_or_create()
    endpoint = control_url.rstrip("/") + "/internal/flowlens/workers/register"
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post(endpoint, json={
            "enrollment_code":enrollment_code,
            "name":name,
            "public_key":public_key,
            "protocol_version":"1.0",
        })
        response.raise_for_status()
        result = response.json()
    config = {
        "worker_id":result["worker_id"],
        "control_url":control_url.rstrip("/"),
        "protocol_version":result.get("protocol_version", "1.0"),
        "name":name,
    }
    _save_config(config)
    return config


async def run_agent(config_path: Path = WORKER_CONFIG) -> None:
    if not config_path.is_file():
        raise RuntimeError("worker is not registered; run `python -m api.worker register` first")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    os.environ["FLOWLENS_WORKER_ID"] = str(config["worker_id"])
    await task_store.initialize()
    identity = WorkerIdentityManager()
    public_key = identity.load_or_create()
    await task_store.upsert_worker({
        "worker_id":str(config["worker_id"]), "name":str(config.get("name") or "flowlens-worker"),
        "public_key":public_key, "status":"online", "version":"1.2.0",
        "protocol_version":str(config.get("protocol_version") or "1.0"),
    })
    agent = WorkerAgent(str(config["worker_id"]), identity=identity)
    agent.configure_default_handlers()
    try:
        await agent.run_forever(_control_websocket_url(str(config["control_url"])))
    finally:
        from .services.douyin_session_manager import session_manager
        await session_manager.close_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowLens outbound remote worker")
    commands = parser.add_subparsers(dest="command", required=True)
    registration = commands.add_parser("register", help="register with a one-time enrollment code")
    registration.add_argument("--control-url", required=True)
    registration.add_argument("--enrollment-code", required=True)
    registration.add_argument("--name", default="flowlens-worker")
    run = commands.add_parser("run", help="connect and process commands")
    run.add_argument("--config", type=Path, default=WORKER_CONFIG)
    args = parser.parse_args()
    if args.command == "register":
        config = register(args.control_url, args.enrollment_code, args.name)
        print(f"registered worker {config['worker_id']}")
    else:
        asyncio.run(run_agent(args.config))


if __name__ == "__main__":
    main()
