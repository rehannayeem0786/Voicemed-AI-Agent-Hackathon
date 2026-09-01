"""Vercel entrypoint.

Exposes the FastAPI app (UI page, /static, /token, /session-config,
/tools/call, /sessions/*) as a single serverless ASGI function.

Note: the legacy /ws bridge is not usable on serverless (no WebSockets),
but the app's browser-direct flow — the browser opens its own WebSocket
straight to AssemblyAI — is unaffected, so the full voice demo works.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.main import app  # noqa: E402  (ASGI callable expected by Vercel)
