"""Authenticated control-plane gateway for outbound FlowLens workers."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import secrets
import time
import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..schemas.worker import WorkerRegisterRequest
from ..services.task_store import task_store
from ..services.worker_identity import verify_worker_signature
from ..services import douyin_session_manager
from ..services.media_relay import MediaRelayOpenError, media_relay_broker
from ..services.remote_events import remote_event_hub

router = APIRouter(tags=["flowlens-worker-gateway"])


def remote_enabled() -> bool:
    return os.getenv("FLOWLENS_REMOTE_WORKER", "false").lower() in {"1","true","yes"}


@router.post("/internal/flowlens/workers/register")
async def register_worker(request: WorkerRegisterRequest):
    if not remote_enabled(): raise HTTPException(404, "remote worker mode is disabled")
    if not await task_store.consume_worker_enrollment(request.enrollment_code):
        raise HTTPException(401, "invalid or expired enrollment code")
    try:
        if len(base64.urlsafe_b64decode(request.public_key.encode())) != 32: raise ValueError
    except (ValueError, binascii.Error):
        raise HTTPException(422, "invalid Ed25519 public key")
    worker_id = f"worker_{uuid.uuid4().hex}"
    await task_store.upsert_worker({
        "worker_id":worker_id, "name":request.name, "public_key":request.public_key,
        "status":"offline", "protocol_version":request.protocol_version,
    })
    return {"worker_id":worker_id, "protocol_version":"1.0"}


async def _set_worker_status(item: dict, status: str, capabilities: dict | None = None) -> None:
    await task_store.upsert_worker({
        "worker_id":item["worker_id"], "name":item["name"], "public_key":item["public_key"],
        "status":status, "version":item.get("version"), "protocol_version":item.get("protocol_version","1.0"),
        "browser_slots":item.get("browser_slots",1),
        "capabilities":capabilities if capabilities is not None else json.loads(item.get("capabilities_json") or "{}"),
    })


async def _receive_worker_message(worker: dict, message: dict) -> dict | None:
    kind = message.get("type")
    if kind == "heartbeat":
        await _set_worker_status(worker, "online", message.get("capabilities") or {})
    elif kind == "ack":
        event_id = str(message.get("event_id") or "")
        if event_id: await task_store.ack_outbox_event(event_id, worker["worker_id"])
    elif kind == "qr.image":
        session_id = str(message.get("login_session_id") or "")
        session = await task_store.get_login_session(session_id) if session_id else None
        if not session or session.get("worker_id") != worker["worker_id"]:
            return
        try: png = base64.b64decode(message.get("image_base64") or "", validate=True)
        except (ValueError, binascii.Error): return
        if session_id and png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) <= 1024 * 1024:
            douyin_session_manager.qr_store.put(session_id, png)
            await task_store.update_login_session(session_id, "qr_ready")
    elif kind == "login.status":
        session_id, status = str(message.get("login_session_id") or ""), str(message.get("status") or "")
        owned = await task_store.get_login_session(session_id) if session_id else None
        if not owned or owned.get("worker_id") != worker["worker_id"]:
            return
        if status in {"qr_ready","qr_scanned","phone_confirmation_required","logged_in","captcha_required","risk_controlled","expired","cancelled","failed"}:
            await task_store.update_login_session(session_id, status, error_type=message.get("error_type"), error_message=message.get("message"))
            item = await task_store.get_login_session(session_id)
            if item and status == "logged_in":
                await task_store.update_connection(item["connection_id"], "connected", creator_hash=message.get("creator_hash"), masked_nickname=message.get("masked_nickname"))
                douyin_session_manager.qr_store.delete(session_id)
            elif item and status in {"captcha_required","risk_controlled"}:
                await task_store.update_connection(item["connection_id"], "verification_required" if status == "captcha_required" else status)
            if item and item.get("user_id"):
                remote_event_hub.publish(item["user_id"], {
                    "event":"login.status", "login_session_id":session_id, "status":status,
                    "error_type":message.get("error_type"),
                })
    elif kind == "crawl.status":
        run_id, status = str(message.get("run_id") or ""), str(message.get("status") or "")
        if run_id and status:
            await task_store.update_remote_run(run_id,status,stage=message.get("stage"),error_type=message.get("error_type"),error_message=message.get("error_message"))
            run = await task_store.get_remote_run(run_id)
            if run:
                if status == "waiting_for_login":
                    await task_store.update_connection(run["connection_id"], "session_expired")
                remote_event_hub.publish(run["user_id"], {
                    "event":"crawl.status", "run_id":run_id, "status":status,
                    "stage":message.get("stage"), "error_type":message.get("error_type"),
                })
    elif kind == "command.result":
        run_id = str(message.get("run_id") or "")
        result = message.get("result") or {}
        run = await task_store.get_remote_run(run_id) if run_id else None
        if run and run["worker_id"] == worker["worker_id"]:
            worker_run_id = result.get("worker_run_id")
            if worker_run_id:
                await task_store.update_remote_run(
                    run_id, "running", worker_run_id=str(worker_run_id)
                )
        if message.get("outbox_event_id"):
            return {"type":"outbox.ack", "event_id":message["outbox_event_id"]}
    elif kind == "result.event":
        event_id, run_id = str(message.get("event_id") or ""), str(message.get("run_id") or "")
        run = await task_store.get_remote_run(run_id)
        if not event_id or not run or run["worker_id"] != worker["worker_id"]:
            return {"type":"result.reject","event_id":event_id,"reason":"invalid_run_ownership"}
        stored = await task_store.store_remote_result({
            "source_event_id":event_id,"user_id":run["user_id"],"connection_id":run["connection_id"],
            "run_id":run_id,"worker_id":worker["worker_id"],"entity_type":message.get("entity_type") or "unknown",
            "entity_id":str(message.get("entity_id") or ""),"payload":message.get("payload") or {},
            "observed_at":message.get("observed_at"),
        })
        return {"type":"result.ack","event_id":event_id,"stored":stored}
    elif kind == "media.status":
        session = media_relay_broker.get(str(message.get("stream_id") or ""), worker["worker_id"])
        if session and not session.ready.done():
            session.ready.set_exception(MediaRelayOpenError(str(message.get("status") or "failed")))
    return None


@router.websocket("/internal/flowlens/workers/connect")
async def worker_connect(websocket: WebSocket):
    if not remote_enabled():
        await websocket.close(code=1008); return
    worker_id = websocket.query_params.get("worker_id") or ""
    worker = await task_store.get_worker(worker_id)
    if not worker:
        await websocket.close(code=1008); return
    await websocket.accept()
    challenge = secrets.token_bytes(32)
    await websocket.send_json({"type":"challenge", "nonce":base64.urlsafe_b64encode(challenge).decode(), "protocol_version":"1.0"})
    try:
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if auth.get("type") != "authenticate" or not verify_worker_signature(worker["public_key"], challenge, str(auth.get("signature") or "")):
            await websocket.close(code=1008); return
        await _set_worker_status(worker, "online")
        await websocket.send_json({"type":"authenticated", "worker_id":worker_id})
        sent: set[str] = set()
        last_seen = time.monotonic()
        while True:
            if time.monotonic() - last_seen > 45:
                await websocket.close(code=1011, reason="heartbeat timeout")
                break
            for row in await task_store.pending_outbox_for_worker(worker_id):
                if row["event_id"] in sent: continue
                command_payload = json.loads(row["payload_json"])
                command_payload.pop("worker_id", None)
                await websocket.send_json({
                    "type":"command", "event_id":row["event_id"], "sequence":row["sequence"],
                    "payload":command_payload,
                })
                sent.add(row["event_id"])
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=1)
                last_seen = time.monotonic()
                response = await _receive_worker_message(worker, message)
                if response: await websocket.send_json(response)
            except asyncio.TimeoutError:
                continue
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        await _set_worker_status(worker, "offline")


@router.websocket("/internal/flowlens/workers/media/{stream_id}")
async def worker_media(websocket: WebSocket, stream_id: str):
    """Dedicated binary channel, isolated from commands and heartbeats."""
    if not remote_enabled():
        await websocket.close(code=1008); return
    worker_id = websocket.query_params.get("worker_id") or ""
    worker = await task_store.get_worker(worker_id)
    session = media_relay_broker.get(stream_id, worker_id)
    if not worker or not session:
        await websocket.close(code=1008); return
    await websocket.accept()
    challenge = secrets.token_bytes(32)
    await websocket.send_json({"type":"challenge", "nonce":base64.urlsafe_b64encode(challenge).decode()})
    try:
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if not verify_worker_signature(worker["public_key"], challenge, str(auth.get("signature") or "")):
            await websocket.close(code=1008); return
        metadata = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if metadata.get("type") != "media.metadata":
            raise ValueError("media metadata was not provided")
        session.metadata = metadata
        if not session.ready.done(): session.ready.set_result(metadata)
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                await session.queue.put(message["bytes"])
            elif message.get("text"):
                control = json.loads(message["text"])
                if control.get("type") == "media.complete": break
            else:
                break
    except Exception as exc:
        if not session.ready.done(): session.ready.set_exception(exc)
    finally:
        await session.queue.put(None)
