"""Database layer — SQLite async storage for sessions and triage history."""

import aiosqlite
import json
import os
from datetime import datetime

DB_PATH = os.environ.get("VOICEMED_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "..", "voicemed.db"
)

_initialized = False


async def _ensure_init():
    """Create the schema on first use.

    Startup (lifespan) calls init_db(), but serverless platforms like Vercel
    may not run lifespan events and use ephemeral filesystems — so every
    public function lazily ensures the schema exists (idempotent). Point
    VOICEMED_DB_PATH at a writable location (e.g. /tmp) on such platforms.
    """
    global _initialized
    if not _initialized:
        await init_db()
        _initialized = True


async def init_db():
    """Initialize the database schema (and migrate older databases)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT DEFAULT 'active',
                duration_seconds REAL DEFAULT 0,
                transcript TEXT DEFAULT '[]',
                triage_result TEXT,
                soap_note TEXT,
                tools_used TEXT DEFAULT '[]'
            )
        """)
        # Migration for databases created before duration_seconds existed.
        cursor = await db.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "duration_seconds" not in columns:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN duration_seconds REAL DEFAULT 0"
            )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS triage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                esi_level INTEGER,
                esi_label TEXT,
                chief_complaint TEXT,
                severity_score INTEGER,
                recommendation TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        await db.commit()


async def create_session(session_id: str):
    """Create a new session record."""
    await _ensure_init()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, datetime.now().isoformat())
        )
        await db.commit()


# Columns that may be written via update_session — anything else is rejected
# so callers can never interpolate arbitrary column names into SQL.
SESSION_COLUMNS = {
    "status", "ended_at", "duration_seconds",
    "transcript", "triage_result", "soap_note", "tools_used",
}


async def update_session(session_id: str, **kwargs):
    """Update session fields (allowlisted columns only)."""
    updates = {k: v for k, v in kwargs.items() if k in SESSION_COLUMNS}
    rejected = set(kwargs) - set(updates)
    if rejected:
        raise ValueError(f"Unknown session column(s): {', '.join(sorted(rejected))}")
    if not updates:
        return
    await _ensure_init()
    async with aiosqlite.connect(DB_PATH) as db:
        for key, value in updates.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await db.execute(
                f"UPDATE sessions SET {key} = ? WHERE id = ?",
                (value, session_id)
            )
        await db.commit()


async def end_session(session_id: str):
    """Mark session as ended."""
    await _ensure_init()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ?, status = 'completed' WHERE id = ?",
            (datetime.now().isoformat(), session_id)
        )
        await db.commit()


async def save_triage_result(session_id: str, result: dict):
    """Save a triage assessment result."""
    await _ensure_init()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO triage_history
               (session_id, timestamp, esi_level, esi_label, chief_complaint, severity_score, recommendation)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                datetime.now().isoformat(),
                result.get("esi_level"),
                result.get("esi_label"),
                result.get("chief_complaint"),
                result.get("patient_severity_score"),
                result.get("recommendation"),
            )
        )
        await db.commit()


async def get_sessions():
    """Get all sessions with their triage results."""
    await _ensure_init()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_session(session_id: str):
    """Get a single session by ID."""
    await _ensure_init()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
