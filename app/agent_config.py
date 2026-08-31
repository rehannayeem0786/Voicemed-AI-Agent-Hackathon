"""VoiceMed AI — Agent configuration loader.

Loads the agent definition from agents/voicemed_triage.jsonc and converts it
into the inline `session` payload accepted by the AssemblyAI Voice Agent API
(`session.update`). The JSONC file is kept in "stored agent" shape (the same
body accepted by POST /v1/agents) so it can be published via publish_agent.py
or configured inline — one source of truth, two deployment paths.
"""

import json
import os
from typing import Any

AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "agents")
AGENT_CONFIG_PATH = os.path.join(AGENTS_DIR, "voicemed_triage.jsonc")

# Languages shipped with the project: agents/voicemed_triage[_<lang>].jsonc
SUPPORTED_LANGUAGES = {"en": "English", "es": "Español"}


def config_path_for_lang(lang: str) -> str:
    """Path to the agent definition for a language (falls back to English)."""
    lang = (lang or "en").lower()
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    if lang == "en":
        return AGENT_CONFIG_PATH
    return os.path.join(AGENTS_DIR, f"voicemed_triage_{lang}.jsonc")


def resolve_lang(lang: str | None) -> str:
    """Normalize a requested language to one we ship, defaulting to English."""
    lang = (lang or "en").lower()
    return lang if lang in SUPPORTED_LANGUAGES else "en"


def parse_jsonc(text: str) -> Any:
    """Parse JSON-with-comments: strips // and /* */ comments and trailing
    commas while respecting string literals (port of the official starter's
    parser, so agents/*.jsonc files stay fully commented)."""
    out: list[str] = []
    in_string = escaped = in_line_comment = in_block_comment = False
    i = 0
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                out.append(char)
            i += 1
            continue
        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                i += 1
            i += 1
            continue
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if char == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if char in "}]" and not in_string:
            # Drop a trailing comma left before this closing bracket.
            while out and out[-1] in " \t\r\n":
                out.pop()
            if out and out[-1] == ",":
                out.pop()
        out.append(char)
        i += 1
    return json.loads("".join(out))


def load_agent_config(path: str | None = None, lang: str = "en") -> dict:
    """Load a VoiceMed agent definition (stored-agent shape).

    Pass `lang` to load a translated variant (e.g. "es" →
    agents/voicemed_triage_es.jsonc); unknown languages fall back to English.
    """
    if path is None:
        path = config_path_for_lang(lang)
    with open(path, "r", encoding="utf-8") as f:
        config = parse_jsonc(f.read())
    if not isinstance(config, dict):
        raise ValueError(f"Agent config at {path} is not a JSON object")
    return config


def _normalize_tools(tools: Any) -> list[dict]:
    """Ensure every tool is a valid client-side function tool.

    The Voice Agent API requires `"type": "function"` on function tools and
    snake_case, verb-noun names. Anything else is passed through untouched so
    HTTP tools keep working if we add them later.
    """
    normalized = []
    for tool in tools or []:
        if not isinstance(tool, dict) or "name" not in tool:
            continue
        tool = dict(tool)
        is_http = bool(tool.get("http"))
        if not is_http and not tool.get("type"):
            tool["type"] = "function"
        normalized.append(tool)
    return normalized


def to_inline_session(config: dict) -> dict:
    """Convert a stored-agent shaped config into the inline `session` payload.

    Inline configuration (mutually exclusive with agent_id) accepts:
      system_prompt, greeting, tools, input, output
    `voice: {voice_id: X}` on the stored agent maps to `output.voice: "X"`.
    Fields that only exist on stored agents (`name`, `language`, `llm`) are
    dropped — the Voice Agent API rejects unknown top-level fields.
    """
    session: dict[str, Any] = {}

    if config.get("system_prompt"):
        session["system_prompt"] = config["system_prompt"]
    if config.get("greeting"):
        session["greeting"] = config["greeting"]

    tools = _normalize_tools(config.get("tools"))
    if tools:
        session["tools"] = tools

    # input: pass through (turn_detection, keyterms, transcription_mode, ...)
    if isinstance(config.get("input"), dict):
        session["input"] = config["input"]

    # output: map stored-agent voice object → inline voice string
    output = dict(config.get("output") or {})
    voice = config.get("voice")
    if isinstance(voice, dict) and voice.get("voice_id"):
        output.setdefault("voice", voice["voice_id"])
    elif isinstance(voice, str):
        output.setdefault("voice", voice)
    if output:
        session["output"] = output

    return session