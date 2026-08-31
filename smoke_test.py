#!/usr/bin/env python3
"""Live end-to-end smoke test for the VoiceMed AI agent protocol.

Validates the full AssemblyAI Voice Agent API flow against the real service:

  1. Mint a short-lived browser token        GET /v1/token
  2. Open the WebSocket                      wss://agents.assemblyai.com/v1/ws?token=...
  3. Send inline session.update              (system_prompt, greeting, tools, input, output)
  4. Expect session.ready                    session accepted
  5. Expect the greeting reply               reply.started / reply.audio / reply.done
  6. End cleanly                             session.end -> session.ended

Run:  .venv/Scripts/python.exe smoke_test.py

Exit code 0 = protocol verified. Any step prints a precise failure reason.
"""

import asyncio
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252 and crash on unicode symbols; force UTF-8
# with a safe fallback so the run never dies on its own output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

from app.agent_config import load_agent_config, to_inline_session  # noqa: E402

API_BASE = "https://agents.assemblyai.com/v1"
WS_URL = "wss://agents.assemblyai.com/v1/ws"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ASSEMBLYAI_API_KEY="):
            return line.split("=", 1)[1].strip()
    return os.environ.get("ASSEMBLYAI_API_KEY", "")


def load_env_file() -> None:
    """Mirror KEY=value lines from .env into os.environ (existing env wins),
    so checks like AGENT_ID behave exactly like the FastAPI app does."""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def mint_token(api_key: str) -> str:
    req = urllib.request.Request(
        f"{API_BASE}/token?product=voice_agent&expires_in_seconds=120",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        body = json.loads(res.read().decode())
    token = body.get("token") if isinstance(body, dict) else body
    if not token:
        raise RuntimeError(f"unexpected token response: {body!r}")
    return token


class Step:
    def __init__(self, name: str) -> None:
        self.name = name
        self.t0 = time.perf_counter()

    def ok(self, detail: str = "") -> None:
        print(f"  PASS  {self.name}  ({time.perf_counter() - self.t0:.2f}s)"
              + (f" -- {detail}" if detail else ""))

    def fail(self, detail: str) -> "AssertionError":
        print(f"  FAIL  {self.name}  ({time.perf_counter() - self.t0:.2f}s) -- {detail}")
        return AssertionError(f"{self.name}: {detail}")


async def run() -> int:
    api_key = load_key()
    if not api_key:
        print("FAIL  no ASSEMBLYAI_API_KEY found in .env")
        return 1

    print("VoiceMed AI — live protocol smoke test")
    print("=" * 62)

    load_env_file()

    # 1. Mint a short-lived browser token, exactly like app/main.py does.
    step = Step("1. mint browser token (GET /v1/token)")
    try:
        token = mint_token(api_key)
    except Exception as err:
        raise step.fail(str(err))
    step.ok(f"{len(token)} chars")

    # 2. Open the session WebSocket with the token in the query string.
    step = Step("2. open WebSocket (wss://agents.assemblyai.com/v1/ws)")
    try:
        ws = await websockets.connect(f"{WS_URL}?token={token}")
    except Exception as err:
        raise step.fail(str(err))
    step.ok("connected")

    session = to_inline_session(load_agent_config())
    audio_bytes = 0
    greeting_text = ""
    reply_done = False
    ended = False

    try:
        # 3. Inline configuration (mutually exclusive with agent_id).
        step = Step("3. send inline session.update (prompt + 6 tools)")
        await ws.send(json.dumps({"type": "session.update", "session": session}))
        step.ok(f"{len(json.dumps(session))} bytes")

        # 4. Wait for the server to accept the configuration.
        step = Step("4. session.ready")
        deadline = time.perf_counter() + 30
        event: dict = {}
        while time.perf_counter() < deadline:
            event = json.loads(await asyncio.wait_for(
                ws.recv(), timeout=deadline - time.perf_counter()))
            t = event.get("type", "")
            if t == "session.ready":
                break
            if t == "session.error":
                raise step.fail(f"{event.get('code')}: {event.get('message')}")
        else:
            raise step.fail("timed out waiting for session.ready")
        step.ok(f"session {str(event.get('session_id', ''))[:8]}…")

        # 5. The agent greets immediately: audio arrives as base64 in
        #    reply.audio events, text as transcript.agent.
        step = Step("5. greeting reply (reply.audio + transcript.agent)")
        deadline = time.perf_counter() + 45
        while time.perf_counter() < deadline and not reply_done:
            event = json.loads(await asyncio.wait_for(
                ws.recv(), timeout=deadline - time.perf_counter()))
            t = event.get("type", "")
            if t == "reply.audio":
                audio_bytes += len(base64.b64decode(event.get("data", "")))
            elif t == "transcript.agent" and event.get("text"):
                greeting_text = event["text"]
            elif t == "session.error":
                raise step.fail(f"{event.get('code')}: {event.get('message')}")
            elif t == "reply.done":
                reply_done = True
        if not audio_bytes:
            raise step.fail("no reply.audio received")
        step.ok(f"{audio_bytes} bytes of 24 kHz PCM"
                + (f' — agent said: "{greeting_text[:70]}"' if greeting_text else ""))

        # 6. End cleanly: a bare close leaves a 30 s billable grace window.
        step = Step("6. session.end -> session.ended")
        await ws.send(json.dumps({"type": "session.end"}))
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline and not ended:
            event = json.loads(await asyncio.wait_for(
                ws.recv(), timeout=deadline - time.perf_counter()))
            if event.get("type") == "session.ended":
                ended = True
        if not ended:
            raise step.fail("no session.ended confirmation")
        step.ok("clean shutdown, no billable grace window")
    finally:
        await ws.close()

    # 7. Stored-agent binding — the path app/main.py and app/voice_agent.py
    #    take once publish_agent.py has run (AGENT_ID in .env). agent_id is
    #    mutually exclusive with the inline fields, so this validates the
    #    published agent loads server-side with its prompt, voice, and tools.
    agent_id = os.environ.get("AGENT_ID", "").strip()
    if not agent_id:
        print("  SKIP  7. stored-agent binding (no AGENT_ID in .env — run publish_agent.py)")
    else:
        step = Step("7. stored-agent binding (session.update {agent_id})")
        try:
            token2 = mint_token(api_key)
            ws2 = await websockets.connect(f"{WS_URL}?token={token2}")
        except Exception as err:
            raise step.fail(str(err))
        try:
            await ws2.send(json.dumps({"type": "session.update", "session": {"agent_id": agent_id}}))
            deadline = time.perf_counter() + 30
            while True:
                if time.perf_counter() >= deadline:
                    raise step.fail("timed out waiting for session.ready")
                event = json.loads(await asyncio.wait_for(
                    ws2.recv(), timeout=deadline - time.perf_counter()))
                t = event.get("type", "")
                if t == "session.ready":
                    break
                if t == "session.error":
                    raise step.fail(f"{event.get('code')}: {event.get('message')}")
            step.ok(f"bound to agent {agent_id[:8]}…")
        finally:
            try:
                await ws2.send(json.dumps({"type": "session.end"}))
                deadline = time.perf_counter() + 10
                while time.perf_counter() < deadline:
                    event = json.loads(await asyncio.wait_for(ws2.recv(), timeout=2))
                    if event.get("type") == "session.ended":
                        break
            except Exception:
                pass
            await ws2.close()

    print("=" * 62)
    print("PROTOCOL VERIFIED END-TO-END ✔")
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except AssertionError:
        return 1


if __name__ == "__main__":
    sys.exit(main())

