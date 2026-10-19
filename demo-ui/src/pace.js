/**
 * One FIFO clock per overlay lane. Visible speech waits `gapMs` (1.5s).
 * Tool start/stop rows follow the trace timestamps so parallel work lands
 * together and later work does not dump at t=0. Usage metrics dispatch
 * immediately; pipeline scores follow the lane clock so they land at the
 * end. Catch-up snapshot replay is never paced.
 */

const METRIC_TOPICS = new Set(["metrics.snapshot", "metric.snapshot", "metric.usage", "native.metric"]);
const SCORE_KEY = /action_correctness|remediation_progress|outcome_fix_applied|mce\.(action|remediation|outcome)/;

export function isPipelineScore(payload) {
  if (!payload || typeof payload !== "object") return false;
  if (/^eval_/i.test(String(payload.name || ""))) return true;
  if (payload.action_correctness && typeof payload.action_correctness === "object") return true;
  const bag = payload.metrics && typeof payload.metrics === "object" ? payload.metrics : payload;
  return Object.keys(bag).some((key) => SCORE_KEY.test(key));
}

export function eventTimeMs(envelope) {
  const payload = envelope?.payload || {};
  const raw = payload.timestamp ?? payload.ts ?? envelope?.ts;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw < 1e12 ? raw * 1000 : raw;
  }
  if (typeof raw === "string" && raw.trim()) {
    const numeric = Number(raw);
    if (Number.isFinite(numeric) && numeric > 1e9) {
      return numeric < 1e12 ? numeric * 1000 : numeric;
    }
    const parsed = Date.parse(raw);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

export function shouldDelay(envelope) {
  if (!envelope || envelope.replay) return false;
  if (envelope.clock === "server") return false;
  const topic = String(envelope.topic || "");
  if (METRIC_TOPICS.has(topic)) return false;
  if (!topic.startsWith("native.")) return false;
  const kind = envelope.payload?.kind || topic.slice("native.".length);
  return kind === "execution_start" || kind === "user_response" || kind === "client_response";
}

export function inLane(envelope) {
  if (!envelope || envelope.replay) return false;
  if (envelope.clock === "server") return false;
  const topic = String(envelope.topic || "");
  if (METRIC_TOPICS.has(topic)) return isPipelineScore(envelope.payload);
  if (topic === "job.completed" || topic === "job.status_changed") return true;
  if (!topic.startsWith("native.")) return false;
  const kind = envelope.payload?.kind || topic.slice("native.".length);
  const tool = String(envelope.payload?.tool_name || "");
  if (kind === "llm_call_start" || kind === "llm_call_end") return true;
  if (kind === "tool_call_start" || kind === "tool_call_end") {
    return Boolean(tool);
  }
  return shouldDelay(envelope);
}

export function pacedKind(envelope) {
  return inLane(envelope);
}

// toolMinMs/toolMaxMs are a *per-event* floor/ceiling applied to every single
// tool_call_start/end in the lane's FIFO chain -- on a dense real trace
// (hundreds of native tool events, not dozens) that compounds: 226 paced
// events at the old 80ms floor alone added ~18s of pure pacer latency on top
// of the server's own replay_delay, independent of it and not reduced when
// replay_delay was tightened. Lowered so a dense trace does not look late
// when it is really just queued behind this.
export function createDispatchPacer({
  gapMs = 1500,
  toolMinMs = 30,
  toolMaxMs = 150,
  toolScale = 0.25,
} = {}) {
  const lanes = new Map();

  function laneKey(envelope) {
    const ref = envelope?.ref || {};
    return `${ref.overlay || ""}|${ref.batch || ""}`;
  }

  function reset() {
    for (const lane of lanes.values()) lane.cancelled = true;
    lanes.clear();
  }

  function waitFor(envelope, lane) {
    if (shouldDelay(envelope) && lane.spoken) return gapMs;
    const ts = eventTimeMs(envelope);
    if (ts == null || lane.lastTs == null) return 0;
    const delta = ts - lane.lastTs;
    if (delta <= 40) return 0;
    return Math.min(toolMaxMs, Math.max(toolMinMs, delta * toolScale));
  }

  function push(envelope, dispatch) {
    if (!inLane(envelope)) {
      dispatch(envelope);
      return;
    }
    const key = laneKey(envelope);
    let lane = lanes.get(key);
    if (!lane) {
      lane = { chain: Promise.resolve(), cancelled: false, spoken: false, lastTs: null };
      lanes.set(key, lane);
    }
    const current = lane;
    current.chain = current.chain.then(
      () =>
        new Promise((resolve) => {
          const wait = waitFor(envelope, current);
          const go = () => {
            if (!current.cancelled) {
              dispatch(envelope);
              if (shouldDelay(envelope)) current.spoken = true;
              const ts = eventTimeMs(envelope);
              if (ts != null) current.lastTs = current.lastTs == null ? ts : Math.max(current.lastTs, ts);
            }
            resolve();
          };
          if (wait) setTimeout(go, wait);
          else go();
        }),
    );
  }

  return { push, reset };
}
