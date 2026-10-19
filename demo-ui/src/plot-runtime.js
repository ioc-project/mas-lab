/**
 * Per-panel plot runtime. Plugins render into the host document (no iframe).
 * Draw functions load as same-origin ES modules from GET /api/plots/{kind}/draw.js.
 */

import { apiFetch, authHeaders, authToken } from "./http.js";

function scopeCss(css, scope) {
  return String(css || "").replace(/(^|})\s*([^@{}][^{]*){/g, (match, brace, sel) => {
    const trimmed = String(sel || "").trim();
    if (!trimmed) return match;
    const parts = trimmed.split(",").map((part) => {
      const token = part.trim();
      if (!token) return token;
      if (token.startsWith(scope)) return token;
      return `${scope} ${token}`;
    });
    return `${brace} ${parts.join(", ")} {`;
  });
}

export function injectPlotCss(kind, css) {
  const id = `plot-css-${String(kind || "plot").replace(/[^a-z0-9_-]/gi, "")}`;
  if (!css) return;
  let style = document.getElementById(id);
  if (!style) {
    style = document.createElement("style");
    style.id = id;
    document.head.appendChild(style);
  }
  style.textContent = scopeCss(css, `.plot-embed[data-kind="${kind}"]`);
}

let d3Promise;

export function ensureD3() {
  if (typeof window !== "undefined" && window.d3) return Promise.resolve(window.d3);
  if (d3Promise) return d3Promise;
  d3Promise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const qs = new URLSearchParams();
    const token = authToken();
    if (token) qs.set("token", token);
    script.src = `/api/plots/assets/d3.min.js${qs.toString() ? `?${qs}` : ""}`;
    script.onload = () => {
      if (!window.d3) reject(new Error("d3 failed to load"));
      else resolve(window.d3);
    };
    script.onerror = () => {
      script.remove();
      d3Promise = null;
      reject(new Error("d3 failed to load"));
    };
    document.head.appendChild(script);
  });
  return d3Promise;
}

function chartData(chart) {
  const data = chart && chart.data && typeof chart.data === "object" && !Array.isArray(chart.data)
    ? Object.assign({}, chart, chart.data)
    : (chart || {});
  return data;
}

function mergeState(STATE, incoming) {
  if (!incoming || typeof incoming !== "object") return STATE;
  const data = chartData(incoming);
  if (incoming.xrange != null) STATE.xrange = Number(incoming.xrange);
  if (data.xrange != null) STATE.xrange = Number(data.xrange);
  if (incoming.yrange) STATE.yrange = incoming.yrange;
  if (data.yrange) STATE.yrange = data.yrange;
  if (incoming.labels) STATE.labels = incoming.labels;
  if (data.labels) STATE.labels = data.labels;
  if (incoming.xlabel) STATE.xlabel = incoming.xlabel;
  if (data.xlabel) STATE.xlabel = data.xlabel;
  if (incoming.ylabel) STATE.ylabel = incoming.ylabel;
  if (data.ylabel) STATE.ylabel = data.ylabel;
  if (incoming.threshold != null) STATE.threshold = Number(incoming.threshold);
  if (data.threshold != null) STATE.threshold = Number(data.threshold);
  if (!STATE.data || typeof STATE.data !== "object") STATE.data = {};
  if (incoming.kind) STATE.kind = incoming.kind;
  if (incoming.vertical != null) STATE.data.vertical = incoming.vertical;
  if (data.vertical != null) STATE.data.vertical = data.vertical;
  if (incoming.view != null) STATE.data.view = incoming.view;
  if (data.view != null) STATE.data.view = data.view;
  if (incoming.t0 != null && Number.isFinite(Number(incoming.t0))) STATE.data.t0 = Number(incoming.t0);
  if (data.t0 != null && Number.isFinite(Number(data.t0))) STATE.data.t0 = Number(data.t0);
  if (incoming.now != null && Number.isFinite(Number(incoming.now))) STATE.data.now = incoming.now;
  if (data.now != null && Number.isFinite(Number(data.now))) STATE.data.now = data.now;
  if (incoming.t != null) STATE.data.t = incoming.t;
  if (data.t != null) STATE.data.t = data.t;
  if (incoming.speed != null && Number.isFinite(Number(incoming.speed))) STATE.data.speed = Number(incoming.speed);
  if (data.speed != null && Number.isFinite(Number(data.speed))) STATE.data.speed = Number(data.speed);
  if (incoming.t1 != null && Number(incoming.t1)) STATE.data.t1 = incoming.t1;
  if (data.t1 != null && Number(data.t1)) STATE.data.t1 = data.t1;
  if (incoming.idle != null) STATE.data.idle = incoming.idle;
  if (data.idle != null) STATE.data.idle = data.idle;
  if (incoming.charts) STATE.data.charts = incoming.charts;
  if (data.charts) STATE.data.charts = data.charts;
  ["nodes", "edges", "cards", "activity", "events", "status", "series", "diracs"].forEach((key) => {
    if (data[key] == null) return;
    if (key === "activity" && Array.isArray(data[key]) && !data[key].length && !data.idle && !incoming.idle) return;
    if (key === "series" && typeof data[key] === "object" && !Array.isArray(data[key])) {
      if (!Object.keys(data[key]).length) return;
      STATE.data.series = data[key];
      return;
    }
    if (key === "diracs" && Array.isArray(data[key])) {
      const prev = Array.isArray(STATE.data.diracs) ? STATE.data.diracs : [];
      if (!data[key].length) {
        STATE.data.diracs = prev;
        return;
      }
      const seen = new Set(prev.map((row) => row && row.id).filter(Boolean));
      const next = prev.slice();
      for (const row of data[key]) {
        if (!row) continue;
        const id = String(row.id || "");
        if (id && seen.has(id)) continue;
        if (id) seen.add(id);
        next.push(row);
      }
      STATE.data.diracs = next;
      return;
    }
    STATE.data[key] = data[key];
  });
  return STATE;
}

export async function compileDrawer(kind, d3, STATE, onUpdate) {
  const win = { masDraw: null, masUpdateChart: onUpdate || null, STATE, d3 };
  const token = new URLSearchParams();
  token.set("t", String(Date.now()));
  const headers = authHeaders();
  if (headers.Authorization) token.set("token", authTokenFromHeaders(headers));
  const url = `/api/plots/${encodeURIComponent(kind)}/draw.js?${token}`;
  const mod = await import(/* @vite-ignore */ url);
  if (typeof mod.default !== "function") throw new Error(`plot ${kind} has no install()`);
  return mod.default(d3, win, STATE, STATE) || win.masDraw;
}

function authTokenFromHeaders(headers) {
  const raw = String(headers.Authorization || "");
  return raw.toLowerCase().startsWith("bearer ") ? raw.slice(7).trim() : raw;
}

export async function createEmbeddedPlot(container, { kind, css, d3 }) {
  injectPlotCss(kind, css);
  container.innerHTML = "";
  const root = document.createElement("div");
  root.className = "plot-embed";
  root.dataset.kind = kind;
  root.style.cssText = "height:100%;min-height:0;display:flex;flex-direction:column;overflow:hidden;";
  const chart = document.createElement("div");
  chart.className = "plot-chart";
  chart.style.cssText = "flex:1;min-height:0;overflow:auto;height:100%;";
  root.appendChild(chart);
  container.appendChild(root);

  const STATE = {
    kind,
    title: kind,
    xrange: 64,
    yrange: [0, 1],
    labels: {},
    xlabel: "",
    ylabel: "",
    data: { kind },
  };
  const el = d3.select(chart);
  let raf = 0;
  let masDraw = null;
  let clockRaf = 0;
  let clockWall = 0;
  let clockT = NaN;
  let clockSpeed = 1;
  let clockT1 = Infinity;
  function wallNow() {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
  }
  function paint() {
    raf = 0;
    if (typeof masDraw === "function") masDraw(el, STATE.data);
  }
  function requestPaint() {
    if (raf) return;
    raf = requestAnimationFrame(paint);
  }
  function stopClock() {
    if (clockRaf) {
      cancelAnimationFrame(clockRaf);
      clockRaf = 0;
    }
  }
  function freezeClock() {
    const now = playhead();
    if (Number.isFinite(now)) {
      clockT = now;
      clockWall = wallNow();
      STATE.data.now = now;
      STATE.data.t = now;
    }
    clockSpeed = 0;
    stopClock();
  }
  function latchClock(incoming) {
    const data = incoming && typeof incoming === "object" ? incoming : {};
    if (data.idle) {
      freezeClock();
      return;
    }
    if (data.t1 != null && Number(data.t1)) clockT1 = Number(data.t1);
    const incomingSpeed = data.speed != null ? Number(data.speed) : NaN;
    const fromClock = data.speed != null && (data.now != null || data.t != null);
    const hinted = Number(fromClock ? (data.now ?? data.t) : NaN);
    if (!Number.isFinite(clockT) && Number.isFinite(hinted)) {
      clockT = hinted;
      clockWall = wallNow();
    } else if (Number.isFinite(incomingSpeed) && incomingSpeed !== clockSpeed) {
      clockT = playhead();
      clockWall = wallNow();
    }
    if (Number.isFinite(incomingSpeed)) clockSpeed = Math.max(0, incomingSpeed);
  }
  function playhead() {
    if (!Number.isFinite(clockT)) return NaN;
    let now = clockT + ((wallNow() - clockWall) / 1000) * clockSpeed;
    if (now > clockT1) now = clockT1;
    return now;
  }
  function tickClock() {
    const now = playhead();
    if (!Number.isFinite(now)) return;
    STATE.data.now = now;
    STATE.data.t = now;
    if (Number.isFinite(clockT1) && clockT1 !== Infinity) STATE.data.t1 = clockT1;
    requestPaint();
  }
  function armClock() {
    if (clockRaf || !Number.isFinite(clockT) || clockSpeed <= 0) return;
    const step = () => {
      tickClock();
      clockRaf = (Number.isFinite(clockT) && clockSpeed > 0) ? requestAnimationFrame(step) : 0;
    };
    clockRaf = requestAnimationFrame(step);
  }
  masDraw = await compileDrawer(kind, d3, STATE, requestPaint);
  return {
    update(incoming) {
      latchClock(incoming || {});
      mergeState(STATE, incoming || {});
      STATE.data.kind = STATE.kind;
      if ((STATE.kind === "series" || STATE.kind === "loop") && Number.isFinite(clockT)) {
        const now = playhead();
        if (Number.isFinite(now)) {
          STATE.data.now = now;
          STATE.data.t = now;
        }
      }
      if (STATE.kind === "series" || STATE.kind === "loop") armClock();
      requestPaint();
    },
    destroy() {
      stopClock();
      if (raf) cancelAnimationFrame(raf);
      root.remove();
    },
  };
}

export async function loadEmbeddedSpec(kind) {
  const qs = new URLSearchParams({ mode: "embedded", t: String(Date.now()) });
  const res = await apiFetch(`/api/plots/${encodeURIComponent(kind)}?${qs}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`plot ${kind} ${res.status}`);
  return res.json();
}
