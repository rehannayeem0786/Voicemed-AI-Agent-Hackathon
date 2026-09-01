# VoiceMed AI — Voice-Powered Medical Triage Nurse

> 🏆 **Built for the AssemblyAI Voice Agent Hackathon** · Python · FastAPI · AssemblyAI Voice Agent API
>
> ✅ **Protocol verified live** against `wss://agents.assemblyai.com` — session.accepted → greeting audio → clean session end (`python smoke_test.py`)
> ✅ **44 automated tests passing** (`python -m pytest tests`)
> 🌍 **Bilingual triage — English & Español** — plus a live interview progress tracker and a built-in 🎬 Demo Copilot for flawless presenting
>
> ✨ **Redesigned clinical dashboard** — dark-glass theme, chat-style transcript, interview stepper, ESI gauge & tool timeline

**VoiceMed AI** is Aria — an empathetic AI triage nurse you talk to, not type at. She runs a structured clinical interview over a single real-time voice connection, scores urgency on the **Emergency Severity Index (ESI 1–5)**, catches **dangerous drug interactions**, fires **emergency escalation** on red-flag symptoms, and writes an **EMR-ready SOAP note** with ICD-10 codes — all while the patient simply speaks.

Voice-first is the point: triage works for elderly, low-vision, low-literacy, and hands-busy users who fail at forms and menus. One conversation, zero UI friction.

---

## 🎤 60-second demo script (what to say to Aria)

| Step | Say this | What happens on screen |
|---|---|---|
| 1 | *"I've had a crushing chest pain for the past hour, it goes into my left arm and I'm sweating."* | `emergency_alert` fires → red banner + ESI 1 (critical) → Aria tells you to call 911 |
| 2 | *"I also take warfarin and aspirin."* | `drug_interaction_check` → high-risk bleeding interaction flagged |
| 3 | *"It started around 9 this morning, pain is about 8 out of 10."* | `symptom_lookup` → red-flag follow-up questions woven into Aria's next questions |
| 4 | *"I think that's everything."* | `triage_assessment` + `generate_soap_note` → ESI gauge animates, SOAP note appears with ICD-10 codes, downloadable as Markdown |

Interrupt Aria anytime — barge-in is semantic and playback flushes instantly.

**Presenting?** Click **🎬 Demo Copilot** (or press `Alt+D`): it shows each line to *say* and what to *watch for*, and auto-advances the moment Aria hears you — while the **progress tracker** lights up Complaint → Symptoms → Medications → Red flags → ESI → SOAP → Booking as the interview actually happens. Noisy room? *Skip* keeps the story moving.

**¿En español?** Switch the 🌐 selector to **Español** before starting — Aria becomes your *enfermera de triaje* with a native Spanish voice (the Demo Copilot recognizes both languages).

---

## 🖥 The dashboard (redesigned)

A clinical dark-glass theme — deep navy, teal→cyan accent, red reserved strictly for alerts, Inter + Space Grotesk type:

- **Sticky glass header** — live session status pill, ⚡ speech→reply latency chip, ⏱ session timer, total triage count
- **Chat-style transcript** — Aria left, patient right, system notices centered; interim (streaming) words appear live
- **Interview stepper** — Complaint → Symptoms → Medications → Red flags → ESI → SOAP → Booking lights up with ✓ as milestones *actually* happen in the session
- **ESI gauge** — green→amber→red track with tick marks and a severity-colored animated fill; the big ESI number is colored by acuity
- **Tool timeline** — every server-side tool call surfaces with its arguments and a running ✓/✗ status
- **Medication safety cards & SOAP document** — severity chips for interactions; downloadable Markdown SOAP note
- **Emergency banner** — pulsing red alert with 911/988 guidance when `emergency_alert` fires
- Accessible (`:focus-visible`, `prefers-reduced-motion`) and responsive down to phone widths

---

## 🛠 How it works

```
 Browser (mic + speakers)                FastAPI bridge                    AssemblyAI Voice Agent API
┌──────────────────────────┐   WS /ws   ┌─────────────────────┐   WSS    ┌────────────────────────────┐
│ AudioWorklet capture     │──────────▶│ tool.call → run tool │─────────▶│ Speech-to-Text (Universal) │
│ 24 kHz PCM16 + resample  │           │ against data/*.json  │          │        + LLM orchestration │
│ AudioWorklet playback    │◀──────────│ tool.result (queued, │◀─────────│        + TTS (alba / lola) │
│ ring buffer + barge-in   │  reply.   │  flushed on reply.   │  reply.  │  turn detection + barge-in │
│ echo cancellation (AEC)  │  audio    │  done — per API spec)│  audio   │  all on ONE WebSocket      │
└──────────────────────────┘           └─────────────────────┘          └────────────────────────────┘
```

- **One WebSocket, full duplex**: STT, LLM tool-calling and TTS run server-side at AssemblyAI; we stream mic audio up and play `reply.audio` (base64, 24 kHz PCM16) down.
- **Browser token flow**: the API key never leaves the server — `GET /token` mints single-use tokens (2-minute redemption window, sessions capped at 30 min); the page connects with `?token=…`, fetches its config from `GET /session-config?lang=en|es` and configures the session inline (`session.update` with `system_prompt`, `greeting`, 6 tools, `input`/`output`).
- **Spec-exact tool loop**: `tool.call` results are queued and flushed only when `reply.done` is the latest event, and dropped on `status:"interrupted"` — exactly per the client-side-tools contract, which keeps turn-taking unbroken.
- **Barge-in that works**: `input.speech.started` flushes the playback ring buffer mid-frame (no stale audio), and the capture worklet is never routed to the speakers (no echo loop).

### The six clinical tools (all server-executed, local JSON databases)

| Tool | What it does | Data source |
|---|---|---|
| `symptom_lookup` | Matches symptoms → conditions, severity base, red flags, follow-up questions | `data/symptoms.json` (20 symptoms) |
| `drug_interaction_check` | Detects dangerous combinations with severity + guidance | `data/drug_interactions.json` (20 pairs, alias-normalized) |
| `triage_assessment` | Scores ESI 1–5 from complaint, severity, vitals, red flags | `app/tools/triage_assessment.py` |
| `generate_soap_note` | Structured SOAP note + ICD-10 mapping | `data/icd10_codes.json` |
| `book_appointment` | Books telehealth / urgent care / primary care slots | `app/tools/appointment_scheduler.py` (mock provider calendar) |
| `emergency_alert` | Critical escalation: banner, alarm, 911/988 guidance | `app/tools/emergency_alert.py` |

---

## 🌍 Bilingual triage (English · Español)

Flip the **🌐 language selector** to **Español** and the next session runs the Spanish variant: Aria speaks with a native Spanish accent (voice `lola`), STT runs in Spanish with medical keyterms (*dolor en el pecho*, *warfarina*, *cara caída*…), and red-flag escalation still drives 911 / 988. The six tools keep English names and argument names, so one server-side dispatcher and one set of clinical databases serve both languages.

- **Inline mode (default):** `GET /session-config?lang=es` builds the session from `agents/voicemed_triage_es.jsonc` — the same single source of truth as English.
- **Stored-agent mode:** `python publish_agent.py --lang es` publishes the Spanish agent and saves `AGENT_ID_ES` to `.env`; `?lang=es` sessions then bind to it, while `AGENT_ID` remains the English agent. Leave `AGENT_ID_ES` empty and Spanish runs inline.

---

## 🛡 Safety & clinical design

- **Never diagnoses.** The system prompt hard-codes "suggest, don't conclude" language and forbids dosage advice.
- **Escalation-first red flags**: chest pain with autonomic symptoms, stroke signs (FAST), severe bleeding, anaphylaxis, suicidal ideation → `emergency_alert` immediately (tool runs in `"execution_mode": "hold"` so the caller stays with Aria), with explicit **call 911 / 988** instructions.
- **Transcription bias for medical speech**: `keyterms` + `transcription_prompt` bias STT toward drug names and clinical vocabulary; `interrupt_response: true` keeps the conversation natural.
- **Clean billing & sessions**: every path (Stop button, page close, server shutdown) sends `session.end` and waits for `session.ended` — no 30-second billable grace windows.
- **Privacy**: audio is streamed, not stored; sessions recorded in SQLite hold transcripts/metadata only, and the demo runs entirely on local JSON — no PHI leaves the stack except the live audio session.
- **Not a medical device** — demonstration only.

---

## 🚀 Run it

```bash
# 1. Python 3.10+ and an AssemblyAI API key (https://www.assemblyai.com/dashboard/api-keys)
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

# 2. Configure
copy .env.example .env            # then paste your ASSEMBLYAI_API_KEY into .env

# 3. Start
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. Talk to Aria
#    open http://localhost:8000  →  Start Consultation  →  allow microphone
#    (headphones recommended; the browser's echo cancellation handles speakers)
```

Optional — publish the agent as a **stored agent** (e.g. so a Twilio number can use it too):

```bash
python publish_agent.py            # POST/PUT /v1/agents, saves AGENT_ID to .env
python publish_agent.py --lang es  # Spanish variant → saves AGENT_ID_ES
```

## ✅ Verify it

```bash
python -m pytest tests -q         # 44 tests: config schema, tools, protocol loop (mocked WS)
python smoke_test.py              # live end-to-end: token → session.update → session.ready
                                  # → greeting audio → session.end/session.ended, real API

# UI checks
node --check static/app.js && node --check static/audio-processor.js   # JS syntax valid
python scripts/check_ids.py       # every DOM id referenced by app.js exists in index.html
```

Expected smoke-test output (recorded run):

```
PASS  1. mint browser token (GET /v1/token)        (0.97s)
PASS  2. open WebSocket (wss://agents.assemblyai.com/v1/ws)  (0.95s)
PASS  3. send inline session.update (prompt + 6 tools)       (8129 bytes)
PASS  4. session.ready                             (0.57s)
PASS  5. greeting reply (reply.audio + transcript.agent)     (5.63s, 269280 bytes PCM)
PASS  6. session.end -> session.ended              (0.26s)
PASS  7. stored-agent binding (session.update {agent_id})    (2.13s)
PROTOCOL VERIFIED END-TO-END ✔
```

Step 7 validates the published stored agent (`publish_agent.py` → `AGENT_ID`
in `.env`), which is the exact binding path the running app takes. Without an
`AGENT_ID` the step prints SKIP and the app configures inline — both paths work.

---

## ☁️ Deploy to Render

The repo ships with a **`render.yaml` Blueprint**, so deployment is nearly one click:

1. Go to [dashboard.render.com](https://dashboard.render.com) → sign in with GitHub → **New → Blueprint** → select this repo. Render reads `render.yaml` and pre-fills everything.
   *(Or manually: **New → Web Service** → pick the repo → Runtime **Python** → Build: `pip install -r requirements.txt` → Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.)*
2. When prompted, set the secret **`ASSEMBLYAI_API_KEY`** (your key).
3. Click **Apply** / **Create Service** — you get an always-on HTTPS URL like `https://voicemed-ai.onrender.com`.

**Why Render fits this app better than serverless (Vercel):**
- Always-on process — no cold starts, no function-bundle file limits
- **WebSockets supported** — the `/ws` bridge works too, not just the browser-direct flow
- Persistent disk option — attach a disk and set `VOICEMED_DB_PATH=/data/voicemed.db` so session history survives redeploys

**Free-plan note:** the service sleeps after ~15 min without traffic; the first request then takes ~30–60 s to wake up. Keep the tab open (or ping `/api/health` on a schedule) during your demo.

---

## 📁 Project structure

```
├── agents/voicemed_triage.jsonc     # agent definition (stored-agent shape; single source of truth)
├── agents/voicemed_triage_es.jsonc  # Spanish variant (same shape, native-Spanish voice)
├── app/
│   ├── main.py                    # FastAPI: page, /token, /session-config, /tools/call, /ws bridge
│   ├── voice_agent.py             # Voice Agent API client: inline session, tool queue/flush, barge-in
│   ├── agent_config.py            # JSONC parser + inline-session converter (spec-exact)
│   ├── database.py                # aiosqlite session persistence + stats
│   └── tools/                     # the six clinical tools
├── data/                          # symptom / interaction / ICD-10 / appointment knowledge bases
├── static/                        # redesigned dashboard: index.html · style.css (dark-glass theme) · app.js · audio-processor.js (AudioWorklets)
├── tests/                         # 44 pytest tests (config, tools, WS protocol loop)
├── publish_agent.py               # publish the JSONC as a stored agent (browser + phone)
└── smoke_test.py                  # live protocol verification against the real API
```

## 🧰 Built with

- **AssemblyAI Voice Agent API** — STT + LLM + TTS over a single WebSocket, server-side tool calling, semantic turn detection & barge-in
- **FastAPI + websockets** — the bridge that runs the clinical tools and proxies audio to the browser
- **Web Audio API / AudioWorklets** — 24 kHz capture with in-worklet resampling, ring-buffer playback with instant barge-in flush
- **aiosqlite** — session history and live stats on the dashboard

---

*VoiceMed AI is a hackathon demonstration, not a medical device, and does not provide medical advice. In a real emergency, call 911 (or 988 for crisis support).*

