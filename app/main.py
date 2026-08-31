"""
VoiceMed AI — FastAPI Application Server

Main entry point for the VoiceMed AI medical triage voice agent.
Serves the web UI and provides the WebSocket bridge to AssemblyAI.
"""

import json
import logging
import os
import uuid

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from contextlib import asynccontextmanager

from app.agent_config import load_agent_config, resolve_lang, to_inline_session
from app.database import init_db, get_sessions, get_session
from app.tools import dispatch_tool
from app.voice_agent import VoiceAgentSession

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("voicemed")

# Create FastAPI app
app = FastAPI(
    title="VoiceMed AI",
    description="Voice-Powered Medical Triage & Care Navigator",
    version="1.0.0",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize the database on startup, log readiness."""
    await init_db()
    logger.info("✅ VoiceMed AI server started")
    logger.info(
        "   API Key configured: %s",
        "Yes" if os.getenv("ASSEMBLYAI_API_KEY") else "⚠️  No — set ASSEMBLYAI_API_KEY in .env",
    )
    yield


app.router.lifespan_context = lifespan

# Mount static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Track active sessions
active_sessions: dict[str, VoiceAgentSession] = {}


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the main web UI."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint that bridges the browser client to AssemblyAI Voice Agent API.

    Flow:
    1. Browser connects to /ws
    2. Server creates a VoiceAgentSession
    3. Session connects to AssemblyAI wss://agents.assemblyai.com/v1/ws
    4. Audio and events are forwarded bidirectionally
    5. Tool calls are intercepted and executed server-side
    """
    await websocket.accept()
    session_id = str(uuid.uuid4())[:8]

    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "ASSEMBLYAI_API_KEY not configured. Please add it to your .env file.",
        }))
        await websocket.close()
        return

    logger.info(f"🎙️  New session: {session_id}")

    session = VoiceAgentSession(
        session_id=session_id,
        api_key=api_key,
        client_ws=websocket,
    )
    active_sessions[session_id] = session

    try:
        await session.start()
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Session error: {e}")
    finally:
        active_sessions.pop(session_id, None)
        await session.stop()


# ─── Browser-direct endpoints (official browser integration flow) ────
#
# The page connects straight to wss://agents.assemblyai.com/v1/ws with a
# short-lived token, so the API key never reaches the browser and audio
# takes the lowest-latency path. Tool calls come back to this server over
# REST so the clinical logic stays server-side.

ASSEMBLYAI_TOKEN_URL = "https://agents.assemblyai.com/v1/token"


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


class SessionRecordRequest(BaseModel):
    transcript: list = []
    soap_note: dict | None = None
    triage_result: dict | None = None
    duration_seconds: float = 0.0


@app.get("/token")
async def mint_token():
    """Mint a short-lived Voice Agent token. Each token is single-use and
    starts exactly one session; usage is billed to our API key. The browser
    never sees the key itself."""
    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        return JSONResponse(content={"error": "ASSEMBLYAI_API_KEY not configured"}, status_code=503)
    params = {
        "product": "voice_agent",
        "expires_in_seconds": "120",       # redemption window for opening the WS
        "max_session_duration_seconds": "1800",  # cap each triage call at 30 min
    }
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                ASSEMBLYAI_TOKEN_URL, params=params,
                headers={"Authorization": api_key}, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return JSONResponse(content={"error": data or "token request failed"}, status_code=502)
        return JSONResponse(content=data)
    except Exception as exc:
        logger.error(f"Token minting failed: {exc}")
        return JSONResponse(content={"error": "token request failed"}, status_code=502)


@app.get("/session-config")
async def session_config(lang: str = "en"):
    """Session payload for the browser's session.update.

    Binds to the published stored agent when AGENT_ID is set (agent_id is
    mutually exclusive with the inline fields per the Voice Agent API);
    otherwise returns the inline config generated from
    agents/voicemed_triage[_<lang>].jsonc — the single source of truth shared
    with publish_agent.py. `?lang=es` serves the Spanish triage variant.
    """
    chosen = resolve_lang(lang)
    try:
        # Language-aware stored-agent binding: AGENT_ID (English) and
        # AGENT_ID_ES (Spanish) are published separately by publish_agent.py.
        env_key = "AGENT_ID" if chosen == "en" else "AGENT_ID_ES"
        stored_agent_id = os.environ.get(env_key, "").strip()
        if stored_agent_id:
            return JSONResponse(content={"agent_id": stored_agent_id})
        return JSONResponse(content=to_inline_session(load_agent_config(lang=chosen)))
    except Exception as exc:
        logger.error(f"Could not build session config: {exc}")
        return JSONResponse(content={"error": str(exc)}, status_code=500)


@app.post("/tools/call")
async def call_tool(request: ToolCallRequest):
    """Execute a clinical tool server-side and return its result. The browser
    relays this into the agent as tool.result (queued per the API contract)."""
    result = await dispatch_tool(request.name, request.arguments)
    return JSONResponse(content=result)


@app.get("/sessions/stats")
async def sessions_stats():
    """Dashboard counters."""
    sessions = await get_sessions()
    triaged = [s for s in sessions if s.get("triage_result")]
    return JSONResponse(content={
        "total_sessions": len(sessions),
        "triaged": len(triaged),
    })


@app.post("/sessions/record")
async def record_session(request: SessionRecordRequest):
    """Persist a browser-direct session (transcript + SOAP) for the audit log."""
    session_id = str(uuid.uuid4())[:8]
    await create_session_record(
        session_id,
        transcript=request.transcript,
        soap_note=request.soap_note,
        triage_result=request.triage_result,
        duration_seconds=request.duration_seconds,
    )
    return JSONResponse(content={"session_id": session_id})


async def create_session_record(session_id: str, **kwargs) -> None:
    from app.database import create_session, update_session, end_session
    await create_session(session_id)
    payload = {
        "transcript": kwargs.get("transcript") or [],
        "duration_seconds": kwargs.get("duration_seconds") or 0.0,
    }
    if kwargs.get("soap_note"):
        payload["soap_note"] = kwargs["soap_note"]
    if kwargs.get("triage_result"):
        payload["triage_result"] = kwargs["triage_result"]
    await update_session(session_id, **payload)
    await end_session(session_id)


# ─── REST API Endpoints ──────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    """Get all past sessions."""
    sessions = await get_sessions()
    # Parse JSON strings back to objects
    for s in sessions:
        for field in ("transcript", "triage_result", "soap_note", "tools_used"):
            if s.get(field) and isinstance(s[field], str):
                try:
                    s[field] = json.loads(s[field])
                except (json.JSONDecodeError, TypeError):
                    pass
    return JSONResponse(content=sessions)


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Get a single session's details."""
    session = await get_session(session_id)
    if not session:
        return JSONResponse(content={"error": "Session not found"}, status_code=404)

    for field in ("transcript", "triage_result", "soap_note", "tools_used"):
        if session.get(field) and isinstance(session[field], str):
            try:
                session[field] = json.loads(session[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return JSONResponse(content=session)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return JSONResponse(content={
        "status": "healthy",
        "service": "VoiceMed AI",
        "active_sessions": len(active_sessions),
        "api_key_configured": bool(os.getenv("ASSEMBLYAI_API_KEY")),
    })


# ─── App Init ────────────────────────────────────────────────────────

# Create __init__.py for the app package
_init_path = os.path.join(os.path.dirname(__file__), "__init__.py")
if not os.path.exists(_init_path):
    with open(_init_path, "w") as f:
        f.write('"""VoiceMed AI — Voice-Powered Medical Triage."""\n')
