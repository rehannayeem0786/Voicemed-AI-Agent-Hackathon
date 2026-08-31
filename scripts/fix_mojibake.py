#!/usr/bin/env python3
"""Repair double-encoded UTF-8 ("mojibake") in project source files.

Files written through mixed-encoding editors can end up with sequences like
"â€”" (which is the em dash — encoded to UTF-8 and then reinterpreted as
Latin-1). This script finds every maximal run of Latin-1-range characters,
tries to round-trip it back through UTF-8, and rewrites the file when any
run repairs cleanly. Files that are already clean are left untouched.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "static/app.js",
    "static/audio-processor.js",
    "static/index.html",
    "static/style.css",
    "app/main.py",
    "app/voice_agent.py",
    "app/agent_config.py",
    "app/database.py",
    "app/__init__.py",
    "app/tools/__init__.py",
    "app/tools/symptom_lookup.py",
    "app/tools/triage_assessment.py",
    "app/tools/drug_interaction.py",
    "app/tools/soap_generator.py",
    "app/tools/appointment_scheduler.py",
    "app/tools/emergency_alert.py",
    "agents/voicemed_triage.jsonc",
    "tests/test_agent_config.py",
    "tests/test_tools.py",
    "tests/test_voice_agent.py",
    "publish_agent.py",
    "README.md",
]

MIN_RUN = 2  # a single stray non-ASCII char is likely intentional (e.g. —)


def repair(text: str) -> tuple[str, int]:
    out = []
    fixes = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if 0x80 <= ord(ch) <= 0xFF:
            j = i
            while j < n and 0x80 <= ord(text[j]) <= 0xFF:
                j += 1
            run = text[i:j]
            if len(run) >= MIN_RUN:
                try:
                    fixed = run.encode("latin-1").decode("utf-8")
                    out.append(fixed)
                    fixes += 1
                    i = j
                    continue
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            out.append(run)
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out), fixes


def main() -> int:
    changed = []
    for rel in TARGETS:
        path = ROOT / rel
        if not path.exists():
            print(f"skip (missing): {rel}")
            continue
        original = path.read_text(encoding="utf-8")
        repaired, fixes = repair(original)
        if fixes and repaired != original:
            path.write_text(repaired, encoding="utf-8", newline="")
            changed.append((rel, fixes))
            print(f"fixed {fixes:3d} runs: {rel}")
        else:
            print(f"clean:          {rel}")
    print(f"\n{len(changed)} file(s) repaired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
