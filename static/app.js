// VoiceMed AI — browser client for the AssemblyAI Voice Agent API.
//
// Protocol (per https://www.assemblyai.com/docs/voice-agents/):
//   ws = new WebSocket(`wss://agents.assemblyai.com/v1/ws?token=${token}`)
//   → { type: "session.update", session: {...} }   inline config (no agent_id)
//   → wait for session.ready before streaming input.audio
//   → input.audio { audio: base64 PCM16 @ 24 kHz }, sent in real time
//   ← reply.audio { data: base64 } + transcript.agent { text }
//   ← tool.call { call_id, name, arguments } → run tool server-side, queue
//     tool.result until reply.done is the latest event (drop on interrupt)
//   → finish with session.end (a bare close leaves a billable grace window)

"use strict";

const $ = (id) => document.getElementById(id);

const elements = {
  startBtn: $("start-btn"),
  stopBtn: $("stop-btn"),
  statusDot: $("status-indicator"),
  statusText: $("status-text"),
  transcript: $("transcript-container"),
  toolActivity: $("tool-activity"),
  waveform: $("waveform-canvas"),
  speakerHint: $("speaker-hint"),
  triageLevel: $("triage-level"),
  triageLevelText: $("triage-level-text"),
  triageBar: $("triage-bar"),
  triageRec: $("triage-recommendation"),
  medPanel: $("med-panel"),
  medContent: $("med-content"),
  emergencyBanner: $("emergency-banner"),
  emergencyContent: $("emergency-content"),
  emergencySub: $("emergency-sub"),
  soapContent: $("soap-content"),
  downloadSoap: $("download-soap"),
  latencyValue: $("latency-value"),
  timerChip: $("timer-chip"),
  sessionTimer: $("session-timer"),
  sessionCounter: $("session-counter"),
  langSelect: $("lang-select"),
  progressTracker: $("progress-tracker"),
  demoToggle: $("demo-toggle"),
  demoCopilot: $("demo-copilot"),
  dcSteps: $("dc-steps"),
  dcStepCount: $("dc-step-count"),
  dcPrev: $("dc-prev"),
  dcNext: $("dc-next"),
  dcClose: $("dc-close"),
};

let ws = null;
let audioContext = null;
let mediaStream = null;
let captureNode = null;
let playbackNode = null;
let sessionActive = false;
let sessionReady = false;
let sessionStart = 0;
let timerInterval = null;
let micLevel = 0;
let agentSpeaking = false;

// tool.result flow-control, per the client-side tools contract: results are
// held until reply.done is the latest event received, and dropped entirely if
// the turn was interrupted (the agent has moved on to a new reply).
let lastEventType = null;
const pendingToolResults = [];

// Speech-end → first-audio latency measurement.
let speechEndAt = 0;

const interimEls = {};

const TOOL_ICONS = {
  symptom_lookup: "🔍",
  drug_interaction_check: "💊",
  triage_assessment: "🚨",
  generate_soap_note: "📋",
  book_appointment: "📅",
  emergency_alert: "🆘",
};

const TOOL_LABELS = {
  symptom_lookup: "Searching symptom database",
  drug_interaction_check: "Checking drug interactions",
  triage_assessment: "Scoring ESI triage level",
  generate_soap_note: "Writing SOAP note",
  book_appointment: "Booking appointment",
  emergency_alert: "Triggering emergency escalation",
};

// Which steps of the structured triage interview each tool completes.
const TOOL_PROGRESS = {
  symptom_lookup: ["symptoms"],
  drug_interaction_check: ["meds"],
  emergency_alert: ["redflags"],
  triage_assessment: ["redflags", "esi"],
  generate_soap_note: ["soap"],
  book_appointment: ["booking"],
};

// ─── Audio pipeline ────────────────────────────────────────────
// Capture worklet resamples the mic to 24 kHz PCM16 and posts chunks; the
// playback worklet drains a ring buffer into the speakers. Both live in
// audio-processor.js.

async function setupAudio() {
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  if (audioContext.state === "suspended") await audioContext.resume();
  await audioContext.audioWorklet.addModule("/static/audio-processor.js");

  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true, // without this, Aria hears herself and self-interrupts
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });

  const source = audioContext.createMediaStreamSource(mediaStream);
  captureNode = new AudioWorkletNode(audioContext, "capture-processor");
  captureNode.port.onmessage = (e) => {
    const msg = e.data;
    if (msg.type === "level") {
      micLevel = msg.rms;
      return;
    }
    if (msg.type === "audio" && ws && ws.readyState === WebSocket.OPEN && sessionReady) {
      // The worklet fires in real time, so pacing is implicit — the API
      // drops audio sent faster than 1x wall clock.
      const bytes = new Uint8Array(msg.pcm.buffer);
      let binary = "";
      const CHUNK = 8192;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
      }
      ws.send(JSON.stringify({ type: "input.audio", audio: btoa(binary) }));
    }
  };
  // IMPORTANT: the capture chain is deliberately NOT connected to
  // audioContext.destination — that would create a mic→speaker feedback loop.
  source.connect(captureNode);

  playbackNode = new AudioWorkletNode(audioContext, "playback-processor");
  playbackNode.connect(audioContext.destination);
}

function flushPlayback() {
  // Barge-in: empty the ring buffer so stale TTS audio stops instantly.
  if (playbackNode) playbackNode.port.postMessage("stop");
}

function playReplyAudio(base64) {
  if (!playbackNode || !base64) return;
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  playbackNode.port.postMessage(bytes.buffer, [bytes.buffer]);
}

// ─── Session control ───────────────────────────────────────────

async function startSession() {
  if (sessionActive) return;
  sessionActive = true;
  sessionReady = false;
  sessionRecorded = false;
  pendingToolResults.length = 0;
  lastEventType = null;
  resetProgress();
  demoReset();
  if (elements.langSelect) elements.langSelect.disabled = true;
  elements.startBtn.classList.add("hidden");
  elements.stopBtn.classList.remove("hidden");
  updateStatus("connecting", "Requesting secure token…");

  try {
    const tokenRes = await fetch("/token");
    if (!tokenRes.ok) throw new Error("Token endpoint failed — is the server running with ASSEMBLYAI_API_KEY set?");
    const tokenData = await tokenRes.json();
    const token = tokenData.token || tokenData.access_token;
    if (!token) throw new Error("No token in response");

    await loadSessionConfig(); // window.VOICEMED_SESSION ← /session-config
    await setupAudio();

    updateStatus("connecting", "Connecting to Aria…");
    ws = new WebSocket(`wss://agents.assemblyai.com/v1/ws?token=${encodeURIComponent(token)}`);
    ws.onopen = onWSOpen;
    ws.onmessage = onWSMessage;
    ws.onclose = onWSClose;
    ws.onerror = onWSError;
  } catch (err) {
    console.error(err);
    updateStatus("error", err.message || "Could not start session");
    addMessage("system", `⚠️ ${err.message || "Could not start session"}`);
    cleanup();
  }
}

function onWSOpen() {
  updateStatus("connecting", "Waking Aria…");
  // Inline configuration: the session object comes from
  // agents/voicemed_triage.jsonc via the server (window.VOICEMED_SESSION).
  // agent_id is mutually exclusive with these inline fields.
  ws.send(JSON.stringify({ type: "session.update", session: window.VOICEMED_SESSION || {} }));
}

function stopSession() {
  // Send session.end and wait for session.ended. A bare ws.close() leaves the
  // session in a 30-second billable grace window.
  if (ws && ws.readyState === WebSocket.OPEN) {
    updateStatus("ending", "Ending session…");
    ws.send(JSON.stringify({ type: "session.end" }));
    setTimeout(() => { if (ws) cleanup(); }, 1500); // safety net
  } else {
    cleanup();
  }
}

function onWSClose() {
  if (sessionActive) {
    cleanup();
    updateStatus("idle", "Session ended");
  }
}

function onWSError(err) {
  console.error("WebSocket error:", err);
  updateStatus("error", "Connection error — check the server logs");
}

function cleanup() {
  void recordSessionOnce(); // persist transcript + SOAP for the audit log (safe: never throws)
  sessionActive = false;
  sessionReady = false;
  agentSpeaking = false;
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  if (captureNode) { captureNode.disconnect(); captureNode = null; }
  if (playbackNode) { playbackNode.disconnect(); playbackNode = null; }
  if (mediaStream) { mediaStream.getTracks().forEach((t) => t.stop()); mediaStream = null; }
  if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    try { ws.close(); } catch { /* already closing */ }
    ws = null;
  }
  micLevel = 0;
  if (elements.langSelect) elements.langSelect.disabled = false;
  elements.startBtn.classList.remove("hidden");
  elements.stopBtn.classList.add("hidden");
  setSpeakerHint("Aria is listening");
}

// ─── Server event handling ─────────────────────────────────────

function onWSMessage(event) {
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch {
    return; // no binary frames are expected from the agent
  }
  handleEvent(msg);
}

function handleEvent(msg) {
  const type = msg.type || "";
  lastEventType = type;

  switch (type) {
    case "session.ready":
      sessionReady = true;
      sessionStart = Date.now();
      startTimer();
      updateStatus("active", "Listening — tell Aria what's wrong");
      setSpeakerHint("Aria is listening");
      break;

    case "session.error": {
      const detail = `${msg.code || "error"}${msg.message ? ": " + msg.message : ""}`;
      updateStatus("error", detail);
      addMessage("system", `⚠️ ${detail}`);
      console.error("session.error", msg);
      break;
    }

    case "input.speech.started":
      speechEndAt = 0;
      setSpeakerHint("You're speaking…");
      break;

    case "input.speech.stopped":
      speechEndAt = performance.now();
      setSpeakerHint("Aria is thinking…");
      break;

    case "transcript.user.delta":
      updateInterim("user", msg.text || msg.delta || "");
      break;

    case "transcript.user":
      finalizeInterim("user", msg.text || "");
      markProgress("complaint");
      demoOnUtterance(msg.text || "");
      break;

    case "reply.started":
      agentSpeaking = true;
      setSpeakerHint("Aria is speaking…");
      break;

    case "reply.audio":
      if (msg.data) {
        if (speechEndAt) {
          const ms = Math.round(performance.now() - speechEndAt);
          elements.latencyValue.textContent = ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
          speechEndAt = 0;
        }
        playReplyAudio(msg.data);
      }
      break;

    case "transcript.agent.delta":
      updateInterim("agent", msg.text || "");
      break;

    case "transcript.agent":
      finalizeInterim("agent", msg.text || "");
      break;

    case "reply.done":
      agentSpeaking = false;
      flushPlayback(); // anything still queued is stale — stop it
      setSpeakerHint(sessionActive ? "Aria is listening" : "");
      // Per the tools contract: interrupted turns invalidate queued results.
      if (msg.status === "interrupted") pendingToolResults.length = 0;
      flushToolResults();
      break;

    case "tool.call":
      void handleToolCall(msg);
      break;

    case "session.ended":
      cleanup();
      updateStatus("idle", "Session complete — SOAP note saved below");
      break;

    default:
      console.log("[voicemed event]", type, msg);
  }
}

// ─── Tool round-trip ───────────────────────────────────────────

async function handleToolCall(msg) {
  const callId = msg.call_id;
  const name = msg.name || "unknown_tool";
  let args = msg.arguments !== undefined ? msg.arguments : (msg.args !== undefined ? msg.args : {});
  if (typeof args === "string") {
    try { args = JSON.parse(args); } catch { args = { raw: args }; }
  }

  showToolActivity(name, "running", args);

  let result;
  try {
    const res = await fetch("/tools/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, arguments: args }),
    });
    if (!res.ok) throw new Error(`tool endpoint returned ${res.status}`);
    result = await res.json();
  } catch (err) {
    // A specific error lets the agent recover cleanly (e.g. re-ask one field)
    result = { error: `${name} failed: ${err.message}` };
  }

  showToolActivity(name, "done", args, result);
  renderToolSideEffects(name, result);
  for (const step of TOOL_PROGRESS[name] || []) markProgress(step);
  pendingToolResults.push({ call_id: callId, result });
  flushToolResults();
}

function flushToolResults() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  // Hold results mid-turn; send only when reply.done is the latest event.
  if (lastEventType !== "reply.done") return;
  while (pendingToolResults.length) {
    const t = pendingToolResults.shift();
    ws.send(JSON.stringify({
      type: "tool.result",
      call_id: t.call_id,
      result: JSON.stringify(t.result), // the API expects a JSON string
    }));
  }
}

// ─── Tool side effects on the dashboard ────────────────────────

// Colors tuned for the dark UI: bright enough to read on navy, ordered
// red (critical) → amber → green/teal (non-urgent) to match the gauge,
// which runs green on the left (ESI 5) to red on the right (ESI 1).
const ESI = {
  1: { text: "Resuscitation — immediate, life-saving intervention", color: "#f87171", pct: 100 },
  2: { text: "Emergent — high risk, do not wait", color: "#ef4444", pct: 84 },
  3: { text: "Urgent — several resources needed", color: "#f59e0b", pct: 62 },
  4: { text: "Less urgent — one resource expected", color: "#34d399", pct: 36 },
  5: { text: "Non-urgent — safe to manage at home", color: "#2dd4bf", pct: 14 },
};

function renderToolSideEffects(name, result) {
  if (!result || typeof result !== "object") return;
  if (name === "triage_assessment") renderTriage(result);
  else if (name === "drug_interaction_check") renderMeds(result);
  else if (name === "emergency_alert") showEmergency(result);
  else if (name === "generate_soap_note") renderSoap(result);
  else if (name === "symptom_lookup") renderSymptomLookup(result);
  else if (name === "book_appointment") renderAppointment(result);
}

function renderTriage(r) {
  const level = parseInt(r.esi_level ?? r.level ?? r.triage_level ?? 0, 10);
  if (level >= 1 && level <= 5) {
    const info = ESI[level];
    elements.triageLevel.textContent = `ESI ${level}`;
    elements.triageLevel.style.color = info.color;
    elements.triageLevelText.textContent = info.text;
    elements.triageBar.style.width = `${info.pct}%`;
    elements.triageBar.style.background = info.color;
  }
  const rec = r.recommendation ?? r.disposition ?? r.summary;
  if (rec) elements.triageRec.textContent = typeof rec === "string" ? rec : JSON.stringify(rec);
  if (r.rationale) addMessage("system", `📊 Triage rationale: ${r.rationale}`);
}

function severityChip(sev) {
  const s = String(sev || "info").toLowerCase();
  const cls = s.includes("high") || s.includes("severe") ? "sev-high" : s.includes("moderate") ? "sev-mod" : "sev-low";
  return `<span class="sev-chip ${cls}">${escapeHtml(s)}</span>`;
}

function renderMeds(r) {
  elements.medPanel.classList.remove("hidden");
  const box = elements.medContent;
  box.textContent = "";
  const checked = r.medications_checked || r.medications || [];
  const interactions = r.interactions || [];

  if (checked.length) {
    const head = document.createElement("p");
    head.className = "med-checked";
    head.textContent = `Checked: ${checked.join(", ")}`;
    box.appendChild(head);
  }

  if (!interactions.length) {
    const ok = document.createElement("div");
    ok.className = "med-allclear";
    ok.innerHTML = `<span class="sev-chip sev-low">all clear</span> No known interactions in the database.`;
    box.appendChild(ok);
    return;
  }

  for (const ix of interactions) {
    const row = document.createElement("div");
    row.className = "med-interaction";
    const pair = ix.pair ? ix.pair.join(" + ") : `${ix.drug_a ?? "?"} + ${ix.drug_b ?? "?"}`;
    row.innerHTML = `
      <div class="med-head">${severityChip(ix.severity)} <strong>${escapeHtml(pair)}</strong></div>
      <p class="med-effect">${escapeHtml(ix.effect || "")}</p>
      <p class="med-rec">➜ ${escapeHtml(ix.recommendation || "Discuss with your doctor.")}</p>`;
    box.appendChild(row);
  }
}

function showEmergency(r) {
  elements.emergencyBanner.classList.remove("hidden");
  elements.emergencySub.textContent = r.title || (r.emergency_type
    ? `Type detected: ${r.emergency_type}`
    : "Escalation protocol activated");
  elements.emergencyContent.textContent = "";
  if (r.description) {
    const d = document.createElement("p");
    d.className = "emergency-desc";
    d.textContent = r.description;
    elements.emergencyContent.appendChild(d);
  }
  const actions = r.immediate_actions || [];
  if (actions.length) {
    const ul = document.createElement("ul");
    ul.className = "emergency-actions";
    for (const a of actions.slice(0, 4)) {
      const li = document.createElement("li");
      li.textContent = a;
      ul.appendChild(li);
    }
    elements.emergencyContent.appendChild(ul);
  }
  const nums = r.emergency_numbers || {};
  const lines = [nums.emergency ? `Emergency: ${nums.emergency}` : "Call 911 now. Stay on the line with Aria."];
  if (nums.suicide_crisis) lines.push(`Crisis line: ${nums.suicide_crisis}`);
  const p = document.createElement("p");
  p.className = "emergency-nums";
  p.textContent = lines.join(" · ");
  elements.emergencyContent.appendChild(p);
  if (r.alert_id) {
    const id = document.createElement("span");
    id.className = "emergency-id";
    id.textContent = `Alert ID: ${r.alert_id}`;
    elements.emergencyContent.appendChild(id);
  }
}

let lastSoap = null;

function renderSoap(r) {
  lastSoap = r;
  const md = r.soap_note ?? r.note ?? r.formatted_text ?? (typeof r === "string" ? r : null);
  elements.soapContent.textContent = md || JSON.stringify(r, null, 2);
  elements.soapContent.classList.remove("soap-empty");
  elements.downloadSoap.classList.remove("hidden");
}

function downloadSoap() {
  if (!lastSoap) return;
  const text = lastSoap.soap_note ?? lastSoap.note ?? lastSoap.formatted_text ?? JSON.stringify(lastSoap, null, 2);
  const blob = new Blob([String(text)], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `voicemed-soap-${new Date().toISOString().slice(0, 10)}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function renderSymptomLookup(r) {
  const matched = r.matched_symptoms || r.matches || [];
  if (matched.length) {
    addMessage("system", `🔍 Symptom database matched: ${matched.map((m) => (typeof m === "string" ? m : m.name)).join(", ")}`);
  }
}

function renderAppointment(r) {
  if (r.error) return;
  const detail = r.appointment_id
    ? `📅 Appointment confirmed (${r.appointment_id}) — ${r.appointment_type || ""} ${r.scheduled_for || ""}`.trim()
    : `📅 Appointment request recorded: ${JSON.stringify(r).slice(0, 140)}`;
  addMessage("system", detail);
}

// ─── Transcript UI ─────────────────────────────────────────────

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

const ROLE_META = {
  user: { label: "You", icon: "🗣️" },
  agent: { label: "Aria · triage nurse", icon: "🩺" },
  system: { label: "System", icon: "ℹ️" },
};

function makeMessage(role, text, extraClass) {
  const meta = ROLE_META[role] || ROLE_META.system;
  const div = document.createElement("div");
  div.className = `transcript-msg transcript-${role}${extraClass ? " " + extraClass : ""}`;
  const icon = document.createElement("span");
  icon.className = "msg-icon";
  icon.textContent = meta.icon;
  const content = document.createElement("div");
  content.className = "msg-content";
  const label = document.createElement("span");
  label.className = "msg-label";
  label.textContent = meta.label;
  const body = document.createElement("p");
  body.className = "msg-text";
  body.textContent = text; // textContent — never inject transcript HTML
  content.append(label, body);
  div.append(icon, content);
  return div;
}

function addMessage(role, text) {
  const el = makeMessage(role, text);
  elements.transcript.appendChild(el);
  elements.transcript.scrollTop = elements.transcript.scrollHeight;
  return el;
}

function updateInterim(role, text) {
  if (!text) return;
  if (!interimEls[role]) {
    interimEls[role] = makeMessage(role, text, "interim");
    elements.transcript.appendChild(interimEls[role]);
  } else {
    interimEls[role].querySelector(".msg-text").textContent = text;
  }
  elements.transcript.scrollTop = elements.transcript.scrollHeight;
}

function finalizeInterim(role, text) {
  const interim = interimEls[role];
  if (interim) {
    interim.remove();
    interimEls[role] = null;
  }
  if (text) addMessage(role, text);
}

// ─── Tool activity UI ──────────────────────────────────────────

function summarizeArgs(args) {
  try {
    const parts = [];
    for (const [k, v] of Object.entries(args || {})) {
      const val = Array.isArray(v) ? v.join(", ") : String(v);
      parts.push(`${k}: ${val.length > 60 ? val.slice(0, 60) + "…" : val}`);
    }
    return parts.join(" · ");
  } catch {
    return "";
  }
}

function showToolActivity(name, status, args, result) {
  const empty = elements.toolActivity.querySelector(".tool-empty-state");
  if (empty) empty.remove();

  let row;
  if (status === "running") {
    row = document.createElement("div");
    row.className = "tool-item tool-running";
    row.dataset.tool = name;
    row.innerHTML = `
      <span class="tool-icon">${TOOL_ICONS[name] || "⚙️"}</span>
      <div class="tool-body">
        <span class="tool-label">${TOOL_LABELS[name] || escapeHtml(name)}</span>
        <span class="tool-args">${escapeHtml(summarizeArgs(args))}</span>
      </div>
      <span class="tool-status">running…</span>`;
    elements.toolActivity.prepend(row);
  } else {
    row = elements.toolActivity.querySelector(`.tool-item[data-tool="${CSS.escape(name)}"].tool-running`);
    if (!row) {
      row = document.createElement("div");
      row.className = "tool-item";
      row.dataset.tool = name;
      row.innerHTML = `
        <span class="tool-icon">${TOOL_ICONS[name] || "⚙️"}</span>
        <div class="tool-body">
          <span class="tool-label">${TOOL_LABELS[name] || escapeHtml(name)}</span>
          <span class="tool-args"></span>
        </div>
        <span class="tool-status"></span>`;
    }
    row.classList.remove("tool-running");
    const failed = result && result.error;
    row.querySelector(".tool-status").textContent = failed ? "✗ failed" : "✓ done";
    row.classList.add(failed ? "tool-failed" : "tool-done");
    elements.toolActivity.prepend(row);
  }

  while (elements.toolActivity.children.length > 12) {
    elements.toolActivity.removeChild(elements.toolActivity.lastChild);
  }
}

// ─── Status, timer, config, counter ────────────────────────────

function updateStatus(state, text) {
  elements.statusDot.className = `status-dot status-${state}`;
  elements.statusText.textContent = text;
}

function setSpeakerHint(text) {
  if (elements.speakerHint) elements.speakerHint.textContent = text;
}

function startTimer() {
  elements.timerChip.classList.remove("hidden");
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - sessionStart) / 1000);
    elements.sessionTimer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);
}

async function loadSessionCount() {
  try {
    const res = await fetch("/sessions/stats");
    elements.sessionCounter.textContent = res.ok ? String((await res.json()).total_sessions ?? 0) : "0";
  } catch {
    elements.sessionCounter.textContent = "0";
  }
}

// ─── Session audit log ─────────────────────────────────────────
// Persist the finished session (transcript + SOAP + triage) via
// POST /sessions/record so it counts in the header stats. Runs at most
// once per session and never throws — cleanup() depends on it, so any
// failure here is logged, not propagated.
let sessionRecorded = false;

async function recordSessionOnce() {
  if (sessionRecorded || !sessionStart) return;
  sessionRecorded = true;
  try {
    const transcript = [...elements.transcript.querySelectorAll(".transcript-msg")]
      .filter((el) => !el.classList.contains("interim"))
      .map((el) => ({
        role: el.classList.contains("transcript-user") ? "user"
          : el.classList.contains("transcript-agent") ? "agent" : "system",
        text: el.querySelector(".msg-text")?.textContent ?? "",
      }));

    const payload = {
      transcript,
      soap_note: lastSoap || null,
      duration_seconds: (Date.now() - sessionStart) / 1000,
    };

    // Reconstruct the triage result from what the dashboard is showing.
    const esiMatch = elements.triageLevel.textContent.match(/\d/);
    if (esiMatch) {
      payload.triage_result = {
        esi_level: parseInt(esiMatch[0], 10),
        text: elements.triageLevelText.textContent,
        recommendation: elements.triageRec.textContent,
      };
    }

    const res = await fetch("/sessions/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`/sessions/record returned ${res.status}`);
    loadSessionCount(); // refresh the header counter with the new total
  } catch (err) {
    console.error("Could not record session:", err);
  }
}

async function loadSessionConfig() {
  const lang = elements.langSelect ? elements.langSelect.value : "en";
  const res = await fetch(`/session-config?lang=${encodeURIComponent(lang)}`);
  if (!res.ok) throw new Error("Could not load session configuration");
  window.VOICEMED_SESSION = await res.json();
}

// ─── Waveform ──────────────────────────────────────────────────

function drawWaveform() {
  requestAnimationFrame(drawWaveform);
  const canvas = elements.waveform;
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const t = performance.now() / 1000;
  if (agentSpeaking) {
    // Aria is talking — animated voice wave in the brand teal→cyan gradient
    const wave = ctx.createLinearGradient(0, 0, w, 0);
    wave.addColorStop(0, "#2dd4bf");
    wave.addColorStop(1, "#38bdf8");
    ctx.strokeStyle = wave;
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.beginPath();
    for (let x = 0; x <= w; x += 2) {
      const env = Math.sin((x / w) * Math.PI);
      const y = h / 2 + Math.sin(x * 0.045 + t * 7) * 14 * env * (0.65 + 0.35 * Math.sin(t * 2.6));
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  } else {
    // Listening — bars driven by the live mic level
    const level = Math.min(1, micLevel * 6);
    const bars = 56;
    const bw = w / bars;
    for (let i = 0; i < bars; i++) {
      const wiggle = 0.35 + 0.65 * Math.abs(Math.sin(i * 1.7 + t * 3));
      const bh = Math.max(3, level * (h / 2 - 8) * wiggle + 3);
      ctx.fillStyle = level > 0.03 ? "#2dd4bf" : "#223049";
      const x = i * bw + 1.5;
      const y = (h - bh) / 2;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, y, Math.max(2, bw - 3), bh, 2);
      else ctx.rect(x, y, Math.max(2, bw - 3), bh);
      ctx.fill();
    }
  }
}

// ─── Triage progress tracker ───────────────────────────────────
// Visualizes the structured ESI interview: each step lights up when the
// corresponding clinical milestone actually happens in the session.

function resetProgress() {
  if (!elements.progressTracker) return;
  elements.progressTracker.querySelectorAll("li.done").forEach((li) => {
    li.classList.remove("done");
  });
}

function markProgress(stepId) {
  const li = elements.progressTracker?.querySelector(`li[data-step="${stepId}"]`);
  if (li && !li.classList.contains("done")) li.classList.add("done");
}

// ─── Demo Copilot ──────────────────────────────────────────────
// Guided demo script for presenting: the card shows the line to say next and
// what to watch for, and auto-advances when Aria's transcript matches. With
// it, one presenter can run the full demo hands-free — and if the crowd is
// noisy, "Skip" keeps the story moving.

const DEMO_SCRIPT = [
  {
    say: "I've had a crushing chest pain for the past hour — it goes into my left arm and I'm sweating.",
    watch: "🚨 emergency banner + ESI 1 · Aria tells you to call 911",
    match: /chest pain|crushing|left arm|dolor en el pecho/i,
  },
  {
    say: "I also take warfarin and aspirin.",
    watch: "💊 Medication Safety panel · high-risk bleeding interaction",
    match: /warfarin|aspirin|warfarina|aspirina/i,
  },
  {
    say: "It started around 9 this morning, and the pain is about 8 out of 10.",
    watch: "🔍 symptom match · follow-up questions woven into Aria's reply",
    match: /out of 10|this morning|de 10|esta ma\u00f1ana|\b8\b/i,
  },
  {
    say: "I think that's everything.",
    watch: "📋 ESI gauge animates · SOAP note appears with ICD-10 codes",
    match: /that'?s everything|that is everything|nothing else|that'?s all|nada m\u00e1s|eso es todo/i,
  },
];

let demoStep = 0;
let demoEnabled = false;

function renderDemo() {
  if (!elements.dcSteps) return;
  elements.dcStepCount.textContent = `${Math.min(demoStep + 1, DEMO_SCRIPT.length)} / ${DEMO_SCRIPT.length}`;
  elements.dcSteps.textContent = "";
  DEMO_SCRIPT.forEach((step, i) => {
    const li = document.createElement("li");
    li.className = i < demoStep ? "dc-done" : i === demoStep ? "dc-current" : "dc-todo";
    const say = document.createElement("p");
    say.className = "dc-say";
    say.textContent = step.say;
    const watch = document.createElement("p");
    watch.className = "dc-watch";
    watch.textContent = step.watch;
    li.append(say, watch);
    elements.dcSteps.appendChild(li);
  });
  elements.dcPrev.disabled = demoStep === 0;
  elements.dcNext.disabled = demoStep >= DEMO_SCRIPT.length;
  const current = elements.dcSteps.querySelector(".dc-current");
  if (current) current.scrollIntoView({ block: "nearest" });
}

function demoReset() {
  demoStep = 0;
  if (demoEnabled) renderDemo();
}

function demoNext() {
  demoStep = Math.min(demoStep + 1, DEMO_SCRIPT.length);
  renderDemo();
}

function demoPrev() {
  demoStep = Math.max(demoStep - 1, 0);
  renderDemo();
}

function demoOnUtterance(text) {
  if (!demoEnabled || demoStep >= DEMO_SCRIPT.length) return;
  if (DEMO_SCRIPT[demoStep].match.test(text || "")) demoNext();
}

function demoToggle(force) {
  demoEnabled = typeof force === "boolean" ? force : !demoEnabled;
  elements.demoCopilot.classList.toggle("hidden", !demoEnabled);
  if (demoEnabled) renderDemo();
}

// ─── Wiring ────────────────────────────────────────────────────

elements.startBtn.addEventListener("click", startSession);
elements.stopBtn.addEventListener("click", stopSession);
elements.downloadSoap.addEventListener("click", downloadSoap);
elements.demoToggle.addEventListener("click", () => demoToggle());
elements.dcClose.addEventListener("click", () => demoToggle(false));
elements.dcPrev.addEventListener("click", demoPrev);
elements.dcNext.addEventListener("click", demoNext);
window.addEventListener("keydown", (e) => {
  if (e.altKey && (e.key === "d" || e.key === "D")) {
    e.preventDefault();
    demoToggle();
  }
});

// Cover tab close / navigation; session.end must be sent synchronously.
window.addEventListener("pagehide", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "session.end" }));
  }
});

loadSessionCount();
requestAnimationFrame(drawWaveform);
