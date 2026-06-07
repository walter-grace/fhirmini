"""Management API + HTTP inbound for the integration engine.
Hosts the asyncio Engine inside the uvicorn event loop."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from . import core

CHANNELS = os.environ.get("ENGINE_CHANNELS", os.path.join(os.path.dirname(__file__), "channels.yaml"))
FHIR_BASE = os.environ.get("FHIR_BASE", "http://127.0.0.1:8080/fhir")
AI_URL = os.environ.get("AI_URL", "http://127.0.0.1:8090")

engine = core.Engine(CHANNELS, FHIR_BASE, AI_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.load()
    await engine.start()
    yield
    engine.stop()


app = FastAPI(title="Mac FHIR integration engine", lifespan=lifespan)


@app.get("/engine/health")
def health():
    return {"status": "up", "channels": list(engine.channels),
            "listeners": [{"channel": n, "bind": b} for n, _, b in engine.servers],
            "fhir_base": FHIR_BASE}


@app.get("/engine/channels")
def channels():
    return engine.channels


@app.get("/engine/messages")
def messages(limit: int = 50):
    return core.recent_messages(limit)


@app.get("/engine/messages/{mid}")
def message(mid: int):
    rec = core.get_message(mid)
    if not rec:
        raise HTTPException(404, "no such message")
    rec["raw"] = bytes(rec["raw"]).decode("utf-8", "ignore") if rec["raw"] else None
    return rec


@app.post("/engine/in/{channel}")
async def http_in(channel: str, request: Request):
    if channel not in engine.channels:
        raise HTTPException(404, f"unknown channel '{channel}'")
    raw = await request.body()
    mid, ok = await engine.handle_bytes(channel, "http", raw)
    return {"message_id": mid, "delivered": ok}


@app.post("/engine/replay/{mid}")
async def replay(mid: int):
    import asyncio
    if not core.get_message(mid):
        raise HTTPException(404, "no such message")
    ok = await asyncio.to_thread(engine.process_sync, mid)
    return {"message_id": mid, "delivered": ok}
