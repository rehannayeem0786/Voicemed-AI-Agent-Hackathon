#!/usr/bin/env python3
"""Publish the VoiceMed agent to AssemblyAI as a stored agent.

    python publish_agent.py [--lang en|es]

Reads agents/voicemed_triage[_es].jsonc (stored-agent shape), then:
  - POST  /v1/agents            when no agent id is stored yet for that language
  - PUT   /v1/agents/{agent_id} when AGENT_ID (en) or AGENT_ID_ES (es) is set

The returned id is written back to .env (AGENT_ID for English, AGENT_ID_ES for
Spanish) so the FastAPI bridge and the deployment tooling all bind to the same
stored agent per language. Standard library only.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.agent_config import load_agent_config  # noqa: E402

ENV_FILE = ROOT / ".env"
API_BASE = "https://agents.assemblyai.com/v1"


def stored_agent_id(lang: str = "en") -> str:
    if lang == "en":
        return os.environ.get("AGENT_ID") or os.environ.get("AGENT_ID_VOICEMED", "")
    return os.environ.get(f"AGENT_ID_{lang.upper()}", "")


def save_env(key: str, value: str) -> None:
    os.environ[key] = value
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        text = ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    try:
        ENV_FILE.write_text(text, encoding="utf-8")
    except OSError as err:
        print(f"  (could not write {ENV_FILE.name}: {err})")


def aai(path: str, method: str = "GET", body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['ASSEMBLYAI_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode()
        sys.exit(f"AssemblyAI API error {err.code} on {method} {path}: {detail}")


def load_env_file() -> None:
    """Minimal .env loader (env vars already set win, like the official starter)."""
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        match = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if match and match.group(1) not in os.environ:
            os.environ[match.group(1)] = match.group(2).strip("'\"")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish the VoiceMed triage agent as a stored agent.",
    )
    parser.add_argument(
        "--lang", choices=("en", "es"), default="en",
        help="Agent variant to publish (default: en)",
    )
    args = parser.parse_args()
    env_key = "AGENT_ID" if args.lang == "en" else "AGENT_ID_ES"

    load_env_file()

    if not os.environ.get("ASSEMBLYAI_API_KEY"):
        sys.exit("Missing ASSEMBLYAI_API_KEY — add it to .env")

    agent = load_agent_config(lang=args.lang)
    print(f"Agent definition ({args.lang}): {agent.get('name', '<unnamed>')}")
    print(f"Tools: {', '.join(t.get('name', '?') for t in agent.get('tools', []))}")

    existing = stored_agent_id(args.lang)
    if existing:
        # Verify the stored agent still exists before trying to update it.
        try:
            request = urllib.request.Request(
                API_BASE + f"/agents/{existing}",
                headers={"Authorization": f"Bearer {os.environ['ASSEMBLYAI_API_KEY']}"},
            )
            with urllib.request.urlopen(request) as response:
                response.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                print(f"Stored agent {existing} no longer exists; creating a new one")
                existing = ""
            else:
                sys.exit(f"AssemblyAI API error {err.code} on GET /agents/{existing}")
        except urllib.error.URLError as err:
            sys.exit(f"Could not reach AssemblyAI: {err}")

    if existing:
        aai(f"/agents/{existing}", method="PUT", body=agent)
        agent_id = existing
        print(f"Updated stored agent {agent_id}")
    else:
        created = aai("/agents", method="POST", body=agent)
        agent_id = created.get("id")
        print(f"Created stored agent {agent_id}")
        save_env(env_key, agent_id)
        print(f"Saved {env_key} to .env")

    print("\nDone. The FastAPI bridge binds to this agent automatically.")
    if args.lang == "es":
        print("Spanish sessions (?lang=es) bind to AGENT_ID_ES automatically.")
    print("Run:  python -m uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()

