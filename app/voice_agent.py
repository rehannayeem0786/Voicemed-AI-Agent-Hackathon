"""VoiceMed AI — AssemblyAI Voice Agent bridge.

Bridges the browser client to the AssemblyAI Voice Agent API over WebSocket:

    browser mic ── PCM16 ──▶ FastAPI /ws ── input.audio ──▶ wss://agents.assemblyai.com
    browser speakers ◀── reply.audio ◀── FastAPI /ws ◀── TTS audio ◀──

Tool calls emitted by the agent (tool.call) are intercepted here, executed
against the local clinical databases (app/tools/*), and returned with
tool.result following the API contract:
  - tool.result is sent only when reply.done is the latest event received
  - queued results are dropped if the reply was interrupted (barge-in)

Protocol reference: https://www.assemblyai.com/docs/voice-agents/voice-agent-api/
"""

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, Optional

import websockets
from starlette.websockets import WebSocketDisconnect

from app.agent_config import load_agent_config, to_inline_session
from app.database import create_session, end_session, save_triage_result, update_session
from app.tools import dispatch_tool

logger = logging.getLogger("voicemed.agent")

ASSEMBLYAI_WS_URL = "wss://agents.assemblyai.com/v1/ws"
WIRE_RATE = 24000  # Hz, audio/pcm 16-bit little-endian, mono

# websockets >= 14 moved the asyncio client and renamed the headers kwarg;
# support both so the project runs on either version.
try:  # websockets >= 14
    from websockets.asyncio.client import connect as _ws_connect

    _HEADERS_KWARG = "additional_headers"
except ImportError:  # websockets 13.x legacy client
    from websockets.legacy.client import connect as _ws_connect  # type: ignore

    _HEADERS_KWARG = "extra_headers"

BENIGN_EXCEPTIONS = (WebSocketDisconnect, asyncio.CancelledError)


class VoiceAgentSession:
    """One browser session ↔ one AssemblyAI Voice Agent session."""

    def __init__(self, session_id: str, api_key: str, client_ws) -> None:
        self.session_id = session_id
        self.api_key = api_key
        self.client_ws = client_ws
        self.aai_ws = None

        # Per the client-side tools contract: tool.result goes out only when
        # reply.done is the latest event we have received.
        self.pending_tool_results: list[dict] = []
        self.last_event_type: Optional[str] = None

        self.transcript: list[dict] = []
        self.tools_used: list[dict] = []
        self.triage_result: Optional[dict] = None
        self.soap_note: Optional[dict] = None
        self.emergency: Optional[dict] = None

        self.active = False
        self.started_at = 0.0
        self._tasks: list[asyncio.Task] = []

    # ── lifecycle ────────────────────────────────────────────────────
    async def start(self) -> None:
        """Connect to AssemblyAI, configure the session inline, and pump
        events in both directions until either side disconnects."""
        self.active = True
        self.started_at = time.time()
        await create_session(self.session_id)

        # Bind to the published stored agent when AGENT_ID is set — agent_id
        # is mutually exclusive with the inline fields per the Voice Agent
        # API. Otherwise configure inline from agents/voicemed_triage.jsonc,
        # so the project also runs with just an API key.
        stored_agent_id = os.environ.get("AGENT_ID", "").strip()
        if stored_agent_id:
            session_config = {"agent_id": stored_agent_id}
        else:
            session_config = to_inline_session(load_agent_config())
        headers = {"Authorization": f"Bearer {self.api_key}"}
        logger.info(f"[{self.session_id}] connecting to {ASSEMBLYAI_WS_URL}")
        self.aai_ws = await _ws_connect(
            ASSEMBLYAI_WS_URL,
            **{_HEADERS_KWARG: headers},
            max_size=2**23,  # 8 MiB — TTS audio chunks can be large
            ping_interval=20,
            ping_timeout=20,
        )
        await self.aai_ws.send(json.dumps({"type": "session.update", "session": session_config}))
        if "agent_id" in session_config:
            logger.info(f"[{self.session_id}] bound to stored agent {stored_agent_id}")
        else:
            logger.info(
                f"[{self.session_id}] inline session.update sent "
                f"({len(session_config['tools'])} tools)"
            )

        upstream = asyncio.create_task(self._pump_upstream(), name="upstream")
        downstream = asyncio.create_task(self._pump_downstream(), name="downstream")
        self._tasks = [upstream, downstream]
        done, pending = await asyncio.wait({upstream, downstream}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, BENIGN_EXCEPTIONS + (RuntimeError,)):
                raise exc

    # ── pumping ──────────────────────────────────────────────────────
    async def _pump_upstream(self) -> None:
        """Browser → AssemblyAI. Audio arrives as raw binary PCM16 frames
        (preferred) or JSON {"type": "input.audio", "audio": "<b64>"}."""
        while True:
            message = await self.client_ws.receive()
            if message.get("type") == "websocket.disconnect":
                logger.info(f"[{self.session_id}] browser disconnected")
                break

            data, text = message.get("bytes"), message.get("text")
            if data:
                await self._send_input_audio(data)
            elif text:
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype == "input.audio":
                    b64 = msg.get("audio") or msg.get("data") or ""
                    if b64:
                        await self._send_input_audio(base64.b64decode(b64))
                elif mtype == "session.end":
                    logger.info(f"[{self.session_id}] session.end from browser")
                    if self.aai_ws is not None:
                        await self.aai_ws.send(json.dumps({"type": "session.end"}))
                    break
                # anything else from the browser is ignored

    async def _send_input_audio(self, pcm: bytes) -> None:
        if self.aai_ws is None or not pcm:
            return
        try:
            await self.aai_ws.send(json.dumps({
                "type": "input.audio",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }))
        except Exception as exc:  # socket closed mid-chunk — benign
            logger.debug(f"[{self.session_id}] input.audio send failed: {exc}")

    async def _pump_downstream(self) -> None:
        """AssemblyAI → browser, with tool-call interception."""
        try:
            async for raw in self.aai_ws:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"[{self.session_id}] non-JSON frame: {raw[:120]!r}")
                    continue
                await self._handle_aai_event(event)
        finally:
            await self._finalize()
            await self._send_ui({"type": "session.ended"})

    async def _send_ui(self, payload: dict) -> None:
        """Forward an event to the browser. Events use the official AssemblyAI
        names/fields; VoiceMed-specific UI events are prefixed with `ui.`."""
        try:
            await self.client_ws.send_text(json.dumps(payload))
        except Exception:
            pass  # browser may already be gone; the loop keeps finalizing

    # ── event handling ───────────────────────────────────────────────
    async def _handle_aai_event(self, event: dict) -> None:
        etype = event.get("type", "")

        # Track the latest event for the tool.result timing contract.
        if etype in ("reply.started", "input.speech.started", "reply.done"):
            self.last_event_type = etype

        if etype == "session.ready":
            logger.info(f"[{self.session_id}] session.ready: {event.get('session_id', '')}")
        elif etype == "session.error":
            logger.error(f"[{self.session_id}] session.error: {event}")
        elif etype == "session.ended":
            logger.info(f"[{self.session_id}] session.ended")
        elif etype == "transcript.user":
            await self._on_transcript_final("user", event.get("text", ""))
        elif etype == "transcript.agent":
            await self._on_transcript_final("agent", event.get("text", ""))
        elif etype == "tool.call":
            await self._handle_tool_call(event)

        # Everything (audio included) is forwarded with its official name and
        # fields so the browser client mirrors the documented protocol.
        await self._send_ui(event)

        if etype == "reply.done":
            if event.get("status") == "interrupted":
                # Barge-in: the agent moved on, queued results are stale.
                self.pending_tool_results.clear()
            else:
                await self._flush_tool_results()

    async def _on_transcript_final(self, role: str, text: str) -> None:
        if not text:
            return
        self.transcript.append({
            "role": role,
            "text": text,
            "at": round(time.time() - self.started_at, 1) if self.started_at else 0.0,
        })
        logger.info(f"[{self.session_id}] {role}: {text}")

    # ── tools ────────────────────────────────────────────────────────
    async def _handle_tool_call(self, event: dict) -> None:
        name = event.get("name", "")
        call_id = event.get("call_id", "")
        arguments = event.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        logger.info(f"[{self.session_id}] tool.call {name}({arguments})")
        self.tools_used.append({"name": name, "arguments": arguments})

        result = await dispatch_tool(name, arguments)

        # Capture clinical artifacts for the session record and the UI.
        if name == "triage_assessment":
            self.triage_result = result
            await save_triage_result(self.session_id, result)
            await self._send_ui({"type": "ui.triage", "result": result})
        elif name == "generate_soap_note":
            self.soap_note = result
            await self._send_ui({"type": "ui.soap", "result": result})
        elif name == "emergency_alert":
            self.emergency = result
            await self._send_ui({"type": "ui.emergency", "alert": result})

        await self._send_ui({"type": "ui.tool_completed", "name": name, "result": result})

        # Queue per the contract; _flush_tool_results sends it when idle.
        self.pending_tool_results.append({"call_id": call_id, "result": result})
        await self._flush_tool_results()

    async def _flush_tool_results(self) -> None:
        """Send queued tool.result messages only when reply.done is the latest
        event received — not earlier (agent mid-transition) and dropped on
        interruption (stale)."""
        if self.last_event_type != "reply.done" or not self.pending_tool_results:
            return
        if self.aai_ws is None:
            return
        pending, self.pending_tool_results = self.pending_tool_results, []
        for item in pending:
            payload = json.dumps({
                "type": "tool.result",
                "call_id": item["call_id"],
                # The API expects the result as a JSON string.
                "result": json.dumps(item["result"]),
            })
            try:
                await self.aai_ws.send(payload)
                logger.info(f"[{self.session_id}] tool.result sent for {item['call_id']}")
            except Exception as exc:
                logger.warning(f"[{self.session_id}] tool.result send failed: {exc}")

    # ── teardown ─────────────────────────────────────────────────────
    async def stop(self) -> None:
        """Idempotent shutdown: end the AssemblyAI session cleanly (a bare
        close leaves a billable 30-second grace window), then release the
        browser connection."""
        self.active = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self.aai_ws is not None:
            try:
                # Best effort: a clean session.end avoids the billable 30s
                # grace window that a bare close leaves behind.
                await asyncio.wait_for(
                    self.aai_ws.send(json.dumps({"type": "session.end"})), timeout=2.0
                )
            except Exception:
                pass
            try:
                await self.aai_ws.close()
            except Exception:
                pass
            self.aai_ws = None
        try:
            await self.client_ws.close()
        except Exception:
            pass

    async def _finalize(self) -> None:
        """Persist the session record once the AssemblyAI stream ends."""
        duration = round(time.time() - self.started_at, 1) if self.started_at else 0.0
        kwargs: dict[str, Any] = {
            "duration_seconds": duration,
            "transcript": json.dumps(self.transcript),
            "tools_used": json.dumps(self.tools_used),
        }
        if self.triage_result is not None:
            kwargs["triage_result"] = json.dumps(self.triage_result)
        if self.soap_note is not None:
            kwargs["soap_note"] = json.dumps(self.soap_note)
        try:
            await update_session(self.session_id, **kwargs)
            await end_session(self.session_id)
        except Exception as exc:
            logger.warning(f"[{self.session_id}] could not persist session: {exc}")
        logger.info(
            f"[{self.session_id}] finished in {duration}s — "
            f"{len(self.transcript)} turns, {len(self.tools_used)} tool calls"
        )
