"""Protocol tests for the AssemblyAI Voice Agent bridge (app/voice_agent.py).

These validate the wire contract without network access:
  - tool.result goes out only when reply.done is the latest event received
  - interrupted replies (barge-in) drop queued tool results
  - clinical artifacts are mirrored to the browser as ui.* events
  - official event names/fields are forwarded untouched
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.voice_agent as va  # noqa: E402


class FakeClientWS:
    """Stand-in for the starlette WebSocket toward the browser."""

    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self):
        pass


class FakeAAIWS:
    """Stand-in for the AssemblyAI WebSocket."""

    def __init__(self):
        self.sent_text = []
        self.closed = False

    async def send(self, text):
        self.sent_text.append(text)

    async def close(self):
        self.closed = True


def make_session():
    client = FakeClientWS()
    session = va.VoiceAgentSession("test-session", "test-key", client)
    session.aai_ws = FakeAAIWS()
    session.started_at = 1234.0
    return session, client


async def no_op(*args, **kwargs):
    return None


def patch_db(monkeypatch):
    for name in ("create_session", "save_triage_result", "update_session", "end_session"):
        monkeypatch.setattr(va, name, no_op)


def patch_dispatch(monkeypatch, result=None, seen=None):
    async def fake_dispatch(name, arguments):
        if seen is not None:
            seen.append((name, arguments))
        return result if result is not None else {"ok": True}

    monkeypatch.setattr(va, "dispatch_tool", fake_dispatch)


class TestToolResultContract:
    def test_result_flushed_when_agent_idle(self, monkeypatch):
        seen = []
        patch_dispatch(monkeypatch, {"matches": []}, seen)
        patch_db(monkeypatch)
        session, client = make_session()
        session.last_event_type = "reply.done"  # agent is idle

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "symptom_lookup", "call_id": "call-1",
            "arguments": {"symptoms": ["fever"]},
        }))

        sent = [json.loads(t) for t in session.aai_ws.sent_text]
        results = [m for m in sent if m["type"] == "tool.result"]
        assert len(results) == 1
        assert results[0]["call_id"] == "call-1"
        # The API expects the result as a JSON string.
        assert json.loads(results[0]["result"]) == {"matches": []}
        # The browser saw the official event plus the ui.* mirror.
        types = [e["type"] for e in client.sent]
        assert "tool.call" in types
        assert "ui.tool_completed" in types

    def test_result_held_until_reply_done(self, monkeypatch):
        patch_dispatch(monkeypatch)
        patch_db(monkeypatch)
        session, _ = make_session()
        session.last_event_type = "reply.started"  # turn in flight

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "symptom_lookup",
            "call_id": "call-2", "arguments": {},
        }))

        assert session.aai_ws.sent_text == []  # nothing upstream yet
        assert len(session.pending_tool_results) == 1

        asyncio.run(session._handle_aai_event({"type": "reply.done"}))
        sent = [json.loads(t) for t in session.aai_ws.sent_text]
        assert [m["type"] for m in sent] == ["tool.result"]
        assert session.pending_tool_results == []

    def test_interrupted_reply_drops_stale_results(self, monkeypatch):
        patch_dispatch(monkeypatch)
        patch_db(monkeypatch)
        session, _ = make_session()
        session.last_event_type = "reply.started"

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "symptom_lookup",
            "call_id": "call-3", "arguments": {},
        }))
        # The user barges in; the interrupted reply makes results stale.
        asyncio.run(session._handle_aai_event({
            "type": "reply.done", "status": "interrupted",
        }))

        assert session.pending_tool_results == []
        assert session.aai_ws.sent_text == []

    def test_string_arguments_are_parsed(self, monkeypatch):
        seen = []
        patch_dispatch(monkeypatch, seen=seen)
        patch_db(monkeypatch)
        session, _ = make_session()
        session.last_event_type = "reply.done"

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "symptom_lookup", "call_id": "c9",
            "arguments": "{\"symptoms\": [\"fever\"]}",
        }))

        assert seen == [("symptom_lookup", {"symptoms": ["fever"]})]


class TestClinicalMirrors:
    def test_triage_result_mirrored_to_ui(self, monkeypatch):
        patch_dispatch(monkeypatch, {"esi_level": 2, "recommendation": "Emergency department now"})
        patch_db(monkeypatch)
        session, client = make_session()
        session.last_event_type = "reply.done"

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "triage_assessment",
            "call_id": "call-4", "arguments": {},
        }))

        ui_triage = [e for e in client.sent if e["type"] == "ui.triage"]
        assert ui_triage and ui_triage[0]["result"]["esi_level"] == 2
        assert session.triage_result == ui_triage[0]["result"]

    def test_emergency_alert_mirrored_to_ui(self, monkeypatch):
        patch_dispatch(monkeypatch, {"escalated": True, "instructions": "Call 911"})
        patch_db(monkeypatch)
        session, client = make_session()
        session.last_event_type = "reply.done"

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "emergency_alert",
            "call_id": "call-5", "arguments": {"emergency_type": "cardiac"},
        }))

        ui = [e for e in client.sent if e["type"] == "ui.emergency"]
        assert ui and ui[0]["alert"]["escalated"] is True
        assert session.emergency == ui[0]["alert"]

    def test_soap_note_mirrored_to_ui(self, monkeypatch):
        patch_dispatch(monkeypatch, {"soap": "S/A/P"})
        patch_db(monkeypatch)
        session, client = make_session()
        session.last_event_type = "reply.done"

        asyncio.run(session._handle_aai_event({
            "type": "tool.call", "name": "generate_soap_note",
            "call_id": "call-6", "arguments": {},
        }))

        ui = [e for e in client.sent if e["type"] == "ui.soap"]
        assert ui and ui[0]["result"] == {"soap": "S/A/P"}
        assert session.soap_note == {"soap": "S/A/P"}


class TestTranscript:
    def test_user_and_agent_transcripts_recorded(self):
        session, _ = make_session()
        asyncio.run(session._handle_aai_event({"type": "transcript.user", "text": "I have chest pain"}))
        asyncio.run(session._handle_aai_event({"type": "transcript.agent", "text": "Call 911 now."}))
        assert [(t["role"], t["text"]) for t in session.transcript] == [
            ("user", "I have chest pain"), ("agent", "Call 911 now.")]
        assert session.transcript[0]["at"] >= 0.0

    def test_empty_transcript_ignored(self):
        session, _ = make_session()
        asyncio.run(session._handle_aai_event({"type": "transcript.user", "text": ""}))
        assert session.transcript == []


class TestEventForwarding:
    def test_official_events_forwarded_untouched(self):
        session, client = make_session()
        events = [
            {"type": "session.ready", "session_id": "s1"},
            {"type": "reply.audio", "data": "QUJD"},
            {"type": "transcript.user.delta", "text": "ch"},
            {"type": "reply.started"},
        ]
        for event in events:
            asyncio.run(session._handle_aai_event(event))
        assert client.sent == events  # same names, same fields, same order

    def test_last_event_tracking_for_timing_contract(self):
        session, _ = make_session()
        for event, expected in (
            ({"type": "reply.started"}, "reply.started"),
            ({"type": "input.speech.started"}, "input.speech.started"),
            ({"type": "reply.audio", "data": "x"}, "input.speech.started"),  # untracked
            ({"type": "reply.done"}, "reply.done"),
        ):
            asyncio.run(session._handle_aai_event(event))
            assert session.last_event_type == expected
