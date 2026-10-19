/**
 * Layout-YAML demo notes. Triggered by stable telemetry, never timestamps.
 */

export const DEFAULT_HOLD_MS = 5000;
export const DEFAULT_MAX_NOTES = 8;
export const DEFAULT_MAX_VISIBLE = 1;
export const DEFAULT_MAX_QUEUED = 1;

function argvBlob(payload) {
  const args = payload?.arguments || payload?.tool_arguments || {};
  const raw = args.args || args.argv || args.command || args.task || args.name || args.skill || "";
  if (Array.isArray(raw)) return raw.map(String).join(" ");
  if (raw && typeof raw === "object") return JSON.stringify(raw);
  return String(raw || "");
}

function textBlob(payload) {
  const parts = [
    payload?.output,
    payload?.content,
    payload?.text,
    payload?.message,
    argvBlob(payload),
    payload?.tool_name,
  ];
  return parts
    .map((item) => (item && typeof item === "object" ? JSON.stringify(item) : String(item || "")))
    .join(" ");
}

function scenarioOf(note) {
  const raw = note?.scenario;
  if (raw == null || raw === "") return [];
  if (Array.isArray(raw)) return raw.map((item) => String(item).toLowerCase());
  return [String(raw).toLowerCase()];
}

function matcherBlock(note) {
  // YAML 1.1 (PyYAML) turns the key `on` into boolean true — use `when`.
  const raw = note?.when || note?.on || note?.true;
  return raw && typeof raw === "object" ? raw : null;
}

function flagOn(value, fallback) {
  if (value === false || value === "false" || value === 0 || value === "0") return false;
  if (value === true || value === "true" || value === 1 || value === "1") return true;
  return fallback;
}

export function normalizeNotes(doc) {
  const block = doc?.narrative;
  if (!block || typeof block !== "object") {
    return {
      notes: [],
      holdMs: DEFAULT_HOLD_MS,
      maxNotes: DEFAULT_MAX_NOTES,
      maxVisible: DEFAULT_MAX_VISIBLE,
      enabled: false,
      overlay: false,
      chat: false,
      maxQueued: DEFAULT_MAX_QUEUED,
    };
  }
  const notes = Array.isArray(block.notes) ? block.notes : [];
  const maxNotes = Number(block.maxNotes) > 0 ? Math.min(12, Number(block.maxNotes)) : DEFAULT_MAX_NOTES;
  const maxQueued = Number(block.maxQueued) > 0 ? Math.min(maxNotes, Number(block.maxQueued)) : DEFAULT_MAX_QUEUED;
  const normalized = notes
    .filter((note) => note && String(note.text || "").trim() && !flagOn(note.disabled, false))
    .slice(0, maxNotes)
    .map((note, index) => ({
      id: String(note.id || `note-${index + 1}`),
      text: String(note.text).trim(),
      at: String(note.at || "").trim(),
        on: matcherBlock(note),
      scenario: scenarioOf(note),
      holdMs: Number(note.holdMs) > 0 ? Number(note.holdMs) : null,
      severity: ["bad", "good"].includes(String(note.severity || "").toLowerCase())
        ? String(note.severity).toLowerCase()
        : "info",
    }));
  const enabled = flagOn(block.enabled, normalized.length > 0) && normalized.length > 0;
  return {
    holdMs: Number(block.holdMs) > 0 ? Number(block.holdMs) : DEFAULT_HOLD_MS,
    maxNotes,
    maxVisible: Number(block.maxVisible) > 0 ? Number(block.maxVisible) : DEFAULT_MAX_VISIBLE,
    maxQueued,
    notes: normalized,
    enabled,
    overlay: enabled && flagOn(block.overlay, true),
    chat: enabled && flagOn(block.chat, true),
  };
}

export function notesForScenario(spec, scenario) {
  const lane = String(scenario || "").toLowerCase();
  const notes = spec?.notes || [];
  if (!lane) return notes.filter((note) => !note.scenario.length);
  return notes.filter((note) => !note.scenario.length || note.scenario.includes(lane));
}

export function matchNoteOn(on, envelope) {
  if (!on || typeof on !== "object") return false;
  const keys = Object.keys(on).filter((key) => on[key] != null && on[key] !== "");
  if (!keys.length) return false;
  const payload = envelope?.payload || {};
  const kind = String(payload.kind || String(envelope?.topic || "").replace(/^native\./, ""));
  const tool = String(payload.tool_name || "");
  const agent = String(payload.agent_id || "");
  const argv = argvBlob(payload).toLowerCase();
  const blob = textBlob(payload);
  if (on.topic && String(envelope?.topic || "") !== String(on.topic)) return false;
  if (on.kind && kind !== String(on.kind)) return false;
  if (on.tool && tool !== String(on.tool)) return false;
  if (on.toolPrefix && !tool.startsWith(String(on.toolPrefix))) return false;
  if (on.agent && agent !== String(on.agent)) return false;
  if (on.argv && !argv.includes(String(on.argv).toLowerCase())) return false;
  if (on.contains && !blob.includes(String(on.contains))) return false;
  if (on.phase) {
    const phase = String(on.phase).toLowerCase();
    if (!argv.includes(phase) && !blob.toLowerCase().includes(phase)) return false;
  }
  return true;
}

export function noteMatches(note, envelope, { live = true } = {}) {
  if (!note?.text || !note.on) return false;
  if (live && envelope?.replay) return false;
  const lane = String(envelope?.ref?.scenario || "").toLowerCase();
  if (note.scenario.length && lane && !note.scenario.includes(lane)) return false;
  if (note.scenario.length && !lane) return false;
  return matchNoteOn(note.on, envelope);
}

export function pickNote(spec, envelope, seen, opts) {
  if (!spec?.notes?.length) return null;
  for (const note of spec.notes) {
    if (seen.has(note.id)) continue;
    if (noteMatches(note, envelope, opts)) return note;
  }
  return null;
}

function pendingList(pending) {
  if (!pending) return [];
  return Array.isArray(pending) ? pending.slice() : [pending];
}

/** One card at a time. Never interrupt. Queue up to maxQueued followers (default 1). */
export function holdOrQueue({ visible, pending }, candidate, opts = {}) {
  const maxQueued = Number(opts.maxQueued) > 0 ? Number(opts.maxQueued) : DEFAULT_MAX_QUEUED;
  const queue = pendingList(pending);
  if (!candidate) return { visible, pending: queue, show: null };
  if (visible) {
    if (queue.length < maxQueued) queue.push(candidate);
    return { visible, pending: queue, show: null };
  }
  return { visible: true, pending: queue, show: candidate };
}

/** Overlay/chat replica walk. Arms on live job.started; does not use hold/queue. */
export function walkNotes(spec, envelopes, { live = true } = {}) {
  const seen = new Set();
  const fired = [];
  if (!spec?.enabled) return fired;
  let armed = !live;
  for (const envelope of envelopes || []) {
    if ((envelope?.topic || "") === "job.started" && (!live || !envelope?.replay)) {
      armed = true;
      continue;
    }
    if (!armed) continue;
    const note = pickNote(spec, envelope, seen, { live });
    if (!note) continue;
    seen.add(note.id);
    fired.push(note.id);
  }
  return fired;
}
