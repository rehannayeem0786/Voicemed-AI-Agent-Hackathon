"""Tests for app/agent_config.py — JSONC parsing and inline session conversion."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent_config import load_agent_config, parse_jsonc, to_inline_session  # noqa: E402


class TestParseJsonc:
    def test_plain_json(self):
        assert parse_jsonc('{"a": 1}') == {"a": 1}

    def test_line_comments(self):
        assert parse_jsonc('{\n  // a comment\n  "a": 1\n}') == {"a": 1}

    def test_block_comments(self):
        assert parse_jsonc('{\n  /* block\n   comment */\n  "a": 2\n}') == {"a": 2}

    def test_trailing_commas(self):
        assert parse_jsonc('{"a": [1, 2, 3,], "b": {"c": 1,},}') == {"a": [1, 2, 3], "b": {"c": 1}}

    def test_comment_markers_inside_strings_are_kept(self):
        doc = '{"url": "https://example.com/x", "text": "a // b"}'
        assert parse_jsonc(doc) == {"url": "https://example.com/x", "text": "a // b"}

    def test_quotes_inside_strings(self):
        assert parse_jsonc('{"say": "he said \\"hi\\" // really"}')["say"] == 'he said "hi" // really'


class TestLoadAgentConfig:
    def test_loads_real_config(self):
        config = load_agent_config()
        assert config["name"] == "VoiceMed AI Triage Nurse"
        assert "Aria" in config["system_prompt"]
        assert config["greeting"]
        assert config["voice"] == {"voice_id": "alba"}
        assert len(config["tools"]) == 6

    def test_tool_names_match_registry(self):
        from app.tools import TOOL_HANDLERS

        config = load_agent_config()
        for tool in config["tools"]:
            assert tool["name"] in TOOL_HANDLERS, f"{tool['name']} has no handler"

    def test_tool_schemas_are_valid_json_schema(self):
        config = load_agent_config()
        for tool in config["tools"]:
            params = tool.get("parameters", {})
            assert params.get("type") == "object", tool["name"]
            assert isinstance(params.get("properties", {}), dict)
            for req in params.get("required", []):
                assert req in params["properties"], f"{tool['name']}: required '{req}' not in properties"


class TestToInlineSession:
    def setup_method(self):
        self.config = load_agent_config()
        self.session = to_inline_session(self.config)

    def test_inline_keys_are_api_valid(self):
        allowed = {"system_prompt", "greeting", "tools", "input", "output"}
        assert set(self.session.keys()) <= allowed

    def test_no_stored_agent_fields_leak(self):
        # name / voice / language / llm are stored-agent fields; sending them
        # inline would be rejected by the API.
        for bad in ("name", "voice", "language", "llm", "agent_id"):
            assert bad not in self.session

    def test_voice_mapped_to_output_string(self):
        assert self.session["output"]["voice"] == "alba"

    def test_tools_are_function_tools(self):
        for tool in self.session["tools"]:
            assert tool["type"] == "function"
            assert "_" in tool["name"]  # snake_case, verb-noun

    def test_input_settings_preserved(self):
        assert self.session["input"]["format"]["encoding"] == "audio/pcm"
        assert self.session["input"]["turn_detection"]["interrupt_response"] is True
        assert "warfarin" in self.session["input"]["keyterms"]

    def test_is_json_serializable(self):
        # The bridge sends this straight over the WebSocket.
        json.dumps(self.session)

    def test_empty_config_yields_empty_session(self):
        assert to_inline_session({}) == {}
