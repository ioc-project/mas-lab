/**
 * Offline replay: pump bundled envelopes through the same plugin bus.
 * Live plugins stay unchanged. No WebSocket, no /api.
 */

function envelopeMs(envelope) {
  const raw = envelope?.ts || envelope?.payload?.timestamp || envelope?.payload?.ts;
  if (raw == null || raw === "") return null;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw > 1e12 ? raw : raw * 1000;
  }
  const parsed = Date.parse(String(raw));
  if (!Number.isNaN(parsed)) return parsed;
  const num = Number(raw);
  if (Number.isFinite(num)) return num > 1e12 ? num : num * 1000;
  return null;
}

function isImmediate(envelope) {
  const topic = String(envelope?.topic || "");
  return topic.startsWith("spec.") || topic.startsWith("view.") || topic.startsWith("player.");
}

/**
 * Fetch a layout-declared static bundle (`dataSource.staticBundle`) by URL.
 * Unlike loadOfflineBundle(), this is not gated on ?mode=offline / a global
 * flag -- it is called directly when a layout's own mode-switch is flipped
 * to "replay" and that layout names a staticBundle, so switching to replay
 * mode plays that real recording through createOfflinePlayer() instead of
 * attempting the (separately broken, not fixed here) live mode:replay WS
 * path. Returns null on any fetch/parse failure rather than throwing, so a
 * bad or missing URL just leaves the existing (empty) replay chrome in
 * place.
 */
export async function loadStaticBundle(url) {
  if (!url) return null;
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function loadOfflineBundle() {
  if (window.__DEMO_UI_BUNDLE__) return window.__DEMO_UI_BUNDLE__;
  const params = new URLSearchParams(location.search);
  if (params.get("mode") === "live") return null;
  const hinted = params.get("bundle");
  const offline = Boolean(window.__DEMO_UI_OFFLINE__) || params.get("mode") === "offline";
  const url = hinted || (offline ? "./bundle.json" : null);
  if (!url) return null;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`offline bundle ${url}: ${res.status}`);
  return res.json();
}

export function createOfflinePlayer(bus, envelopes, { capMs = 800 } = {}) {
  const frames = Array.isArray(envelopes) ? envelopes : [];
  let index = 0;
  let playing = false;
  let speed = 1;
  let timer = 0;

  function emitNow(envelope) {
    const framed = { ...envelope, replay: false };
    if (typeof bus.pump === "function") bus.pump(framed);
    else bus.emit(framed);
  }

  function publishState() {
    bus.emit({
      topic: "player.state",
      ts: new Date().toISOString(),
      ref: {},
      source: "view",
      payload: {
        playing,
        index,
        total: frames.length,
        speed,
        done: index >= frames.length,
      },
      replay: false,
    });
  }

  function waitMs(from, to) {
    const a = envelopeMs(from);
    const b = envelopeMs(to);
    if (a == null || b == null) return Math.min(capMs, 120) / Math.max(speed, 0.01);
    const delta = Math.max(0, b - a);
    return Math.min(capMs, delta) / Math.max(speed, 0.01);
  }

  function stopTimer() {
    if (timer) {
      clearTimeout(timer);
      timer = 0;
    }
  }

  function resetView() {
    bus.emit({
      topic: "view.reset",
      ts: new Date().toISOString(),
      ref: {},
      source: "view",
      payload: { phase: "run" },
      replay: false,
    });
  }

  function stepOnce() {
    if (index >= frames.length) {
      playing = false;
      publishState();
      return false;
    }
    emitNow(frames[index]);
    index += 1;
    publishState();
    return index < frames.length;
  }

  function tick() {
    if (!playing) return;
    if (!stepOnce()) return;
    const prev = frames[index - 1];
    const next = frames[index];
    const wait = isImmediate(next) ? 0 : waitMs(prev, next);
    timer = setTimeout(tick, wait);
  }

  return {
    play() {
      if (index >= frames.length) {
        this.reset();
      }
      playing = true;
      publishState();
      tick();
    },
    pause() {
      playing = false;
      stopTimer();
      publishState();
    },
    step() {
      playing = false;
      stopTimer();
      stepOnce();
    },
    setSpeed(next) {
      const n = Number(next);
      speed = Number.isFinite(n) && n > 0 ? n : 1;
      publishState();
    },
    reset() {
      playing = false;
      stopTimer();
      index = 0;
      resetView();
      publishState();
    },
    getState() {
      return { playing, index, total: frames.length, speed, done: index >= frames.length };
    },
  };
}
