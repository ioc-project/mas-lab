import { chromeMode, parseModes } from "./layout-props.js";

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtElapsed(t, t0) {
  const now = Number(t);
  const origin = Number(t0);
  if (!Number.isFinite(now)) return "";
  const dt = Number.isFinite(origin) ? now - origin : now;
  if (!Number.isFinite(dt)) return "";
  const s = Math.max(0, dt);
  if (s >= 60) {
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return `${m}:${String(r).padStart(2, "0")}`;
  }
  return `${s.toFixed(1)}s`;
}

export const replayControlsPlugin = {
  id: "replay-controls",
  kind: "chrome",
  subscribes: ["player.state", "job.progress", "job.started", "job.completed", "view.mode", "view.reset"],
  mount(container, ctx) {
    const wrap = el("div", "replay-controls");
    const allowed = parseModes(ctx.layout?.modes ?? ctx.config?.modes ?? "both");
    function mode() {
      return chromeMode(ctx.bus, allowed[0]);
    }
    function isReplayChrome() {
      if (ctx?.bus?.mode === "offline") return allowed.includes("replay");
      if (!allowed.includes("replay")) return false;
      return mode() === "replay";
    }
    function isPassive() {
      return allowed.includes("passive") && mode() === "passive";
    }
    function showHud() {
      return isReplayChrome() || isPassive();
    }
    wrap.hidden = !showHud();
    const play = document.createElement("button");
    play.type = "button";
    play.textContent = "Play";
    const step = document.createElement("button");
    step.type = "button";
    step.textContent = "Step";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.textContent = "Replay";
    const speed = document.createElement("select");
    // offline.js's waitMs() already paces by real recorded elapsed time
    // between envelopes (capped at 800ms so no single step ever visibly
    // stalls); dividing that by a large-enough speed makes playback
    // effectively instant without a separate code path.
    for (const n of [0.5, 1, 2, 4, 8, 10, 20, 50]) {
      speed.appendChild(new Option(n >= 50 ? `${n}× (max)` : `${n}×`, String(n)));
    }
    speed.value = "1";
    const pos = el("span", "replay-pos");
    pos.textContent = "—";
    wrap.append(play, step, reset, speed, pos);
    container.appendChild(wrap);
    let ingest = {};

    function player() {
      return ctx.bus?.player;
    }

    play.addEventListener("click", () => {
      const p = player();
      if (!p) return;
      if (p.getState().playing) p.pause();
      else p.play();
    });
    step.addEventListener("click", () => player()?.step());
    reset.addEventListener("click", () => {
      const p = player();
      if (!p) return;
      p.reset();
      p.play();
    });
    speed.addEventListener("change", () => player()?.setSpeed(speed.value));

    function paintControls(hidden) {
      play.hidden = hidden;
      step.hidden = hidden;
      reset.hidden = hidden;
      speed.hidden = hidden;
    }

    function paintIngest() {
      const t = ingest.t;
      const t0 = ingest.t0;
      const t1 = ingest.t1;
      const factor = Number(ingest.speed);
      const clock = fmtElapsed(t, t0);
      const span = fmtElapsed(t1, t0);
      const rate = Number.isFinite(factor) && factor > 0 ? `${factor.toFixed(factor >= 10 ? 1 : 2)}×` : "";
      const bits = [];
      if (clock) bits.push(span ? `${clock} / ${span}` : clock);
      if (rate) bits.push(rate);
      pos.textContent = bits.join(" · ") || "waiting";
    }

    function paint(state) {
      if (!showHud()) {
        wrap.hidden = true;
        return;
      }
      wrap.hidden = false;
      paintControls(!isReplayChrome());
      if (isPassive() || !player()) {
        paintIngest();
        return;
      }
      const s = state || player()?.getState() || {};
      play.textContent = s.playing ? "Pause" : "Play";
      const i = Number(s.index || 0);
      const total = Number(s.total || 0);
      const clock = fmtElapsed(ingest.t, ingest.t0);
      const factor = Number(ingest.speed);
      const rate = Number.isFinite(factor) && factor > 0 ? `${factor.toFixed(factor >= 10 ? 1 : 2)}×` : (s.speed ? `${s.speed}×` : "");
      pos.textContent = [total ? `${i} / ${total}` : "", clock, rate].filter(Boolean).join(" · ") || "—";
      if (s.speed) speed.value = String(s.speed);
    }

    if (player()) paint(player().getState());
    else paint();
    return {
      update(envelope) {
        const topic = envelope?.topic || "";
        if (topic === "view.reset") {
          ingest = {};
          paint();
          return;
        }
        if (topic === "view.mode") {
          paint(player()?.getState());
          return;
        }
        if (topic === "player.state") {
          paint(envelope.payload);
          return;
        }
        if (topic === "job.started" || topic === "job.progress" || topic === "job.completed") {
          const payload = envelope?.payload || {};
          ingest = {
            t: payload.t ?? ingest.t,
            t0: payload.t0 ?? ingest.t0,
            t1: payload.t1 ?? ingest.t1,
            speed: payload.speed ?? ingest.speed,
          };
          paint();
        }
      },
      destroy() {
        wrap.remove();
      },
    };
  },
};

function fmtTokens(n) {
  if (n == null || n === "") return "—";
  return Number(n).toLocaleString();
}

function resultText(payload) {
  const raw =
    payload?.output ||
    payload?.content ||
    payload?.text ||
    payload?.result ||
    payload?.response ||
    "";
  return String(raw).trim();
}

export const runInspectPlugin = {
  id: "run-inspect",
  kind: "output",
  subscribes: ["spec.*", "native.*", "metric.*", "governance.*", "view.reset"],
  mount(container) {
    const root = el("div", "plugin-root inspect-root");
    container.appendChild(root);
    const agentsEl = el("div", "inspect-section");
    const protoEl = el("div", "inspect-section");
    const govEl = el("div", "inspect-section");
    const resultEl = el("div", "inspect-section");
    root.append(agentsEl, protoEl, govEl, resultEl);

    const agents = new Map();
    const protocol = {};
    const gov = { decisions: 0, hitl: 0, by: {} };
    let result = "";
    let specAgents = [];

    function ensureAgent(id) {
      if (!id || id === "mas" || id === "user") return null;
      if (!agents.has(id)) agents.set(id, { id, llm: 0, tokens: 0, tools: 0 });
      return agents.get(id);
    }

    function paintAgents() {
      const rows = specAgents.length
        ? specAgents.map((item) => ensureAgent(item.id) || { id: item.id, llm: 0, tokens: 0, tools: 0 })
        : [...agents.values()];
      if (!rows.length) {
        agentsEl.innerHTML = `<div class="label">agents</div><div class="muted">—</div>`;
        return;
      }
      const body = rows
        .map(
          (row) =>
            `<tr><td>${esc(row.id)}</td><td>${row.llm}</td><td>${fmtTokens(row.tokens)}</td><td>${row.tools}</td></tr>`,
        )
        .join("");
      agentsEl.innerHTML = `<div class="label">agents</div>
        <table class="inspect-table"><thead><tr><th>id</th><th>llm</th><th>tokens</th><th>tools</th></tr></thead>
        <tbody>${body}</tbody></table>`;
    }

    function paintProtocol() {
      const keys = Object.keys(protocol);
      if (!keys.length) {
        protoEl.innerHTML = `<div class="label">protocol / scores</div><div class="muted">—</div>`;
        return;
      }
      protoEl.innerHTML =
        `<div class="label">protocol / scores</div>` +
        keys
          .map((key) => {
            const value = protocol[key];
            let text = String(value);
            if (key.endsWith(".activated") || key.endsWith("_activated")) text = value ? "yes" : "no";
            else if (typeof value === "number" && value >= 0 && value <= 1 && !Number.isInteger(value)) {
              text = `${Math.round(value * 100)}%`;
            }
            return `<div class="kpi-line"><div class="label">${esc(key.replace(/_/g, " "))}</div><div class="value">${esc(text)}</div></div>`;
          })
          .join("");
    }

    function paintGov() {
      const parts = Object.entries(gov.by)
        .map(([k, v]) => `${k}: ${v}`)
        .join(" · ");
      govEl.innerHTML = `<div class="label">governance</div>
        <div class="kpi-line"><div class="label">decisions</div><div class="value">${gov.decisions}</div></div>
        <div class="kpi-line"><div class="label">hitl</div><div class="value">${gov.hitl}</div></div>
        ${parts ? `<div class="muted">${parts}</div>` : ""}`;
    }

    function paintResult() {
      const shown = result.length > 360 ? `${result.slice(0, 360)}…` : result;
      resultEl.innerHTML = `<div class="label">result</div><pre class="inspect-result">${esc(shown || "—")}</pre>`;
    }

    function reset() {
      agents.clear();
      specAgents = [];
      for (const key of Object.keys(protocol)) delete protocol[key];
      gov.decisions = 0;
      gov.hitl = 0;
      gov.by = {};
      result = "";
      paintAgents();
      paintProtocol();
      paintGov();
      paintResult();
    }

    function takeProtocol(metrics) {
      const skip = new Set([
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "cost",
        "llm_calls",
        "n_llm_calls",
        "n_tool_calls",
        "duration_s",
      ]);
      for (const [key, value] of Object.entries(metrics || {})) {
        if (!key || key.startsWith("_") || skip.has(key)) continue;
        protocol[key] = value;
      }
    }

    reset();
    return {
      update(envelope) {
        const topic = envelope?.topic || "";
        const payload = envelope?.payload || {};
        if (topic === "view.reset") {
          reset();
          return;
        }
        if (topic === "spec.snapshot") {
          specAgents = Array.isArray(payload.agents) ? payload.agents : [];
          for (const item of specAgents) ensureAgent(item.id);
          paintAgents();
          return;
        }
        if (topic === "metric.snapshot" || topic === "metric.usage" || topic === "native.metric") {
          takeProtocol(payload.metrics || payload);
          paintProtocol();
          return;
        }
        if (!topic.startsWith("native.")) return;
        const kind = payload.kind || topic.slice("native.".length);
        const agent = ensureAgent(payload.agent_id);
        if (kind === "llm_call_end" && agent) {
          agent.llm += 1;
          const usage = payload.tokens_used || payload.usage || {};
          agent.tokens += Number(usage.total_tokens || 0) || Number(usage.prompt_tokens || 0) + Number(usage.completion_tokens || 0);
          paintAgents();
        }
        if (kind === "tool_call_start" && agent) {
          agent.tools += 1;
          paintAgents();
        }
        if (kind === "governance_decision") {
          gov.decisions += 1;
          const decision = String(payload.decision || payload.action || payload.verdict || "decision");
          gov.by[decision] = (gov.by[decision] || 0) + 1;
          paintGov();
        }
        if (kind.startsWith("hitl_")) {
          gov.hitl += 1;
          paintGov();
        }
        if (kind === "user_response" || kind === "client_response" || kind === "mas_call_end") {
          const text = resultText(payload);
          if (text) {
            result = text;
            paintResult();
          }
        }
      },
      destroy() {
        root.remove();
      },
    };
  },
};
