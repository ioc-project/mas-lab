/**
 * One stream of envelopes. Plugins subscribe by topic glob + DataSourceRef.
 * send() injects a command frame onto the same stream via POST /api/commands.
 * Catch-up envelopes have replay=true.
 * Live native bursts go through a shared pacer so chat and topology stay in sync.
 */
import { apiFetch, authToken, readJson } from "./http.js";
import { createDispatchPacer } from "./pace.js";

const ATTACH = "demo-ui-attach";
const CATCHUP_TOPICS = /^(native|metric|governance|job|command|result|control)\./;

function newNonce() {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch {
    /* ignore */
  }
  return `n-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isFreshAttach() {
  try {
    return localStorage.getItem(ATTACH) === "new";
  } catch {
    return false;
  }
}

function skipFreshCatchup(envelope) {
  return Boolean(envelope?.replay) && isFreshAttach() && CATCHUP_TOPICS.test(String(envelope.topic || ""));
}

export function createBus() {
  const listeners = new Set();
  const history = [];
  const pacer = createDispatchPacer();
  let socket = null;
  let hello = { topics: ["*"], subscribe: [], get: [], inspect: [], ref: {}, nonce: newNonce() };

  function dispatch(envelope) {
    for (const fn of listeners) fn(envelope);
  }

  function emit(envelope) {
    const next = envelope;
    if (!next) return;
    const incoming = next.nonce;
    if (
      incoming &&
      incoming !== hello.nonce &&
      api.replayNonce &&
      incoming !== api.replayNonce &&
      next.topic !== "view.reset"
    ) {
      api.replayNonce = incoming;
      emit({
        topic: "view.reset",
        ts: new Date().toISOString(),
        ref: {},
        source: "view",
        payload: { phase: "reset", nonce: incoming },
        nonce: incoming,
        replay: true,
      });
    } else if (incoming && incoming !== hello.nonce) {
      api.replayNonce = incoming;
    }
    if (next.topic === "view.reset") {
      pacer.reset();
      history.length = 0;
      history.push(next);
      dispatch(next);
      return;
    }
    if (next.topic === "view.snapshot" && next.payload?.phase === "start") {
      pacer.reset();
    }
    history.push(next);
    pacer.push(next, dispatch);
  }

  function pump(envelope) {
    const next = envelope;
    if (!next) return;
    if (next.topic === "view.reset") {
      pacer.reset();
      history.length = 0;
      history.push(next);
      dispatch(next);
      return;
    }
    history.push(next);
    dispatch(next);
  }

  function subscribe(fn) {
    listeners.add(fn);
    let start = 0;
    for (let i = history.length - 1; i >= 0; i -= 1) {
      if (history[i]?.topic === "view.reset") {
        start = i;
        break;
      }
    }
    for (let i = start; i < history.length; i += 1) fn(history[i]);
    return () => listeners.delete(fn);
  }

  async function connect({ topics = ["*"], subscribe, get = [], inspect = [], ref = {} } = {}) {
    if (api.mode === "offline") return null;
    const token = authToken();
    hello = {
      topics,
      subscribe: subscribe || topics,
      get,
      inspect,
      ref: { ...(ref || {}) },
      nonce: hello.nonce || newNonce(),
      token,
    };
    api.nonce = hello.nonce;
    if (socket) {
      try {
        socket.close();
      } catch {
        /* already closed */
      }
      socket = null;
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    const url = `${proto}://${location.host}/ws${qs}`;
    socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    socket.send(JSON.stringify(hello));
    socket.addEventListener("message", (ev) => {
      try {
        const envelope = JSON.parse(ev.data);
        if (skipFreshCatchup(envelope)) return;
        emit(envelope);
      } catch {
        /* ignore malformed frames */
      }
    });
    return socket;
  }

  async function attach(ref = {}) {
    const next = { ...(ref || {}) };
    try {
      localStorage.setItem(ATTACH, next.batch || next.session || "");
    } catch {
      /* ignore */
    }
    return connect({ ...hello, ref: next });
  }

  async function loadSnapshot(layoutName, ref = {}) {
    if (api.mode === "offline") return { events: [] };
    emit({
      topic: "view.snapshot",
      ts: new Date().toISOString(),
      ref: {},
      source: "view",
      payload: { phase: "start" },
      replay: false,
    });
    const qs = new URLSearchParams();
    if (ref.session) qs.set("session", ref.session);
    if (ref.run) qs.set("run", ref.run);
    if (ref.batch) qs.set("batch", ref.batch);
    if (ref.experiment) qs.set("experiment", ref.experiment);
    if (ref.scenario) qs.set("scenario", ref.scenario);
    if (ref.test) qs.set("test", ref.test);
    try {
      const res = await apiFetch(`/api/layouts/${encodeURIComponent(layoutName)}/snapshot?${qs}`);
      const data = await readJson(res, `snapshot ${layoutName}`);
      for (const event of data.events || []) {
        const envelope = { ...event, replay: true };
        if (skipFreshCatchup(envelope)) continue;
        emit(envelope);
      }
      return data;
    } finally {
      emit({
        topic: "view.snapshot",
        ts: new Date().toISOString(),
        ref: {},
        source: "view",
        payload: { phase: "end" },
        replay: false,
      });
    }
  }

  async function send(command) {
    if (api.mode === "offline") {
      throw new Error("offline replay — no backend");
    }
    const res = await apiFetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...command, nonce: command.nonce || api.nonce }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function match(envelope, topics, ref) {
    if (envelope?.topic === "view.reset") return true;
    const topicOk =
      !topics?.length ||
      topics.some((glob) => {
        if (glob === "*") return true;
        const topic = String(envelope.topic || "");
        if (glob.endsWith(".*")) return topic.startsWith(glob.slice(0, -1));
        return topic === glob;
      });
    if (!topicOk) return false;
    for (const key of ["library", "experiment", "test", "run", "session", "overlay", "batch"]) {
      if (ref?.[key] && envelope.ref?.[key] && ref[key] !== envelope.ref[key]) return false;
    }
    if (ref?.scenario && envelope.ref?.scenario && ref.scenario !== envelope.ref.scenario) return false;
    const theirs = envelope?.nonce;
    if (!theirs) return true;
    if (api.chromeMode === "passive") return true;
    if (envelope.replay) return true;
    if (api.replayNonce && theirs === api.replayNonce) return true;
    if (api.nonce && theirs !== api.nonce) return false;
    return true;
  }

  const api = { emit, pump, subscribe, connect, attach, loadSnapshot, send, match, history, mode: "live", player: null, nonce: hello.nonce, replayNonce: null, chromeMode: "passive" };
  return api;
}
