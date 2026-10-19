import { apiFetch } from "./http.js";
import { getPlugin } from "./plugin-registry.js";
import { chromeMode, initialChromeMode, parseModes } from "./layout-props.js";
import { createOfflinePlayer, loadStaticBundle } from "./offline.js";

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

export async function resetView(ctx) {
  try {
    localStorage.setItem("demo-ui-attach", "new");
  } catch {
    /* ignore */
  }
  try {
    await apiFetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verb: "reset-view", ref: { origin: "demo-ui" } }),
    });
  } catch {
    /* controller may already be empty */
  }
  ctx?.bus?.emit({
    topic: "view.reset",
    ts: new Date().toISOString(),
    ref: {},
    source: "view",
    payload: { phase: "reset" },
  });
}

function laneLabel(ctx) {
  if (ctx.config?.text) return String(ctx.config.text);
  const scenario = String(ctx.dataSource?.scenario || "").toLowerCase();
  if (scenario) return scenario.toUpperCase().replace(/_/g, " ");
  return ctx.config?.title || ctx.layout?.title || "demo";
}

export const titlePlugin = {
  id: "title",
  kind: "chrome",
  windowed: false,
  mount(container, ctx) {
    const lane = Boolean(ctx.config?.lane || ctx.config?.fromScenario);
    const node = el(lane ? "div" : "span", lane ? "lane-title" : "layout-title");
    const text = lane
      ? laneLabel(ctx)
      : String(ctx.config?.text || ctx.layout?.title || laneLabel(ctx) || "demo");
    node.textContent = text;
    node.title = text;
    container.appendChild(node);
    return {
      destroy() {
        node.remove();
      },
    };
  },
};

export const promptInputPlugin = {
  id: "prompt-input",
  kind: "input",
  mount(container, ctx) {
    const wrap = el("div", "prompt-input");
    const select = document.createElement("select");
    select.appendChild(new Option("freeform", ""));
    const prompts = ctx.config.prompts || [];
    for (const item of prompts) {
      select.appendChild(new Option(item.label || "prompt", item.text || item.value || ""));
    }
    const area = document.createElement("textarea");
    area.placeholder = ctx.config.placeholder || "Prompt";
    if (ctx.config.defaultPrompt) {
      if (![...select.options].some((opt) => opt.value === ctx.config.defaultPrompt)) {
        select.appendChild(new Option(ctx.config.defaultLabel || "default", ctx.config.defaultPrompt));
      }
      area.value = ctx.config.defaultPrompt;
    }
    select.addEventListener("change", () => {
      if (select.value) area.value = select.value;
    });
    const go = document.createElement("button");
    go.textContent = ctx.config.runLabel || "Run";
    const status = el("div", "run-status");
    go.addEventListener("click", async () => {
      const text = area.value.trim();
      if (!text) return;
      ctx.closeSettings?.();
      const batch = crypto.randomUUID();
      ctx.bus?.emit({
        topic: "view.reset",
        ts: new Date().toISOString(),
        ref: {},
        source: "view",
        payload: { phase: "run" },
      });
      const experiment = ctx.dataSource?.experiment;
      const mode = ctx.dataSource?.mode || "live";
      const targets = (ctx.targets?.() || [ctx.dataSource]).map((item) => ({ ...item, batch, mode }));
      const verb =
        ctx.config.verb ||
        (mode === "replay" ? "replay" : experiment ? "run-experiment" : "run-mas");
      const command = {
        verb,
        text,
        query: text,
        ref: { ...ctx.dataSource, batch },
        timeout: ctx.config.timeout || undefined,
        pre: targets.flatMap((item) => item?.pre || []),
        targets,
        batch,
      };
      try {
        localStorage.setItem("demo-ui-attach", batch);
      } catch {
        /* ignore */
      }
      status.textContent = `Starting ${targets.length} run(s)…`;
      try {
        const result = await ctx.send(command);
        const rows = result?.results || [];
        const failed = rows.filter((row) => row && row.ok === false);
        if (failed.length) {
          status.textContent = failed.map((row) => row.detail || "failed").join(" · ");
          ctx.bus?.emit({
            topic: "ui.error",
            ts: new Date().toISOString(),
            ref: {},
            source: "view",
            payload: { error: status.textContent, results: rows },
          });
        } else {
          status.textContent = `${rows.length || targets.length} run(s) started`;
        }
      } catch (err) {
        status.textContent = String(err?.message || err);
        ctx.bus?.emit({
          topic: "ui.error",
          ts: new Date().toISOString(),
          ref: {},
          source: "view",
          payload: { error: status.textContent },
        });
      }
    });
    wrap.append(select, area, go, status);
    container.appendChild(wrap);
    return {
      update(envelope) {
        const job = envelope?.payload || {};
        if (envelope?.topic === "native.pipeline_phase" && job.phase === "post") {
          status.textContent = "Scoring…";
        } else if (envelope?.topic === "job.completed" && job.status === "completed") {
          status.textContent = "Done";
        } else if (envelope?.topic === "job.completed" && job.status && job.status !== "completed") {
          const label = job.error || job.status;
          status.textContent = label;
        }
      },
      destroy() {
        wrap.remove();
      },
    };
  },
};

export const settingsPlugin = {
  id: "settings",
  kind: "chrome",
  mount(container, ctx) {
    const wrap = el("div", "settings");
    const btn = document.createElement("button");
    btn.className = "settings-toggle";
    btn.type = "button";
    btn.setAttribute("aria-label", "Settings");
    btn.textContent = "⚙";
    const panel = el("div", "settings-panel");
    panel.hidden = true;
    const inner = el("div", "settings-body");
    panel.appendChild(inner);
    const handles = [];
    const children = ctx.config.content || [{ plugin: "prompt-input", props: ctx.config }];
    btn.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
    });
    wrap.append(btn, panel);
    container.appendChild(wrap);
    for (const item of children) {
      const plugin = getPlugin(item.plugin || item.type);
      if (!plugin) continue;
      handles.push(
        plugin.mount(inner, {
          ...ctx,
          closeSettings: () => {
            panel.hidden = true;
          },
          config: { ...(ctx.config || {}), ...(item.props || {}) },
        }),
      );
    }
    return {
      update(envelope) {
        for (const handle of handles) handle.update?.(envelope);
      },
      destroy() {
        for (const handle of handles) {
          try {
            handle.destroy();
          } catch {
            /* already gone */
          }
        }
        wrap.remove();
      },
    };
  },
};

export const runStatePlugin = {
  id: "run-state",
  kind: "chrome",
  mount(container, ctx) {
    const wrap = el("div", "run-state");
    const chip = el("span", "run-state-chip");
    const spin = el("span", "widget-spinner");
    spin.setAttribute("aria-hidden", "true");
    spin.hidden = true;
    const reset = document.createElement("button");
    reset.type = "button";
    reset.textContent = "Reset";
    wrap.append(chip, spin, reset);
    container.appendChild(wrap);
    const jobs = new Map();
    const specs = new Set();
    let phase = "not-started";
    let snapshotOpen = false;
    const LIVE_KINDS = new Set([
      "execution_start",
      "mas_call_start",
      "user_response",
      "client_response",
      "llm_call_start",
      "llm_call_end",
      "tool_call_start",
    ]);
    const LABELS = {
      idle: "idle",
      loading: "loading",
      "not-started": "not started",
      starting: "starting",
      running: "running",
      started: "running",
      scoring: "scoring",
      stopping: "running",
      stopped: "stopped",
    };
    function expectedRuns() {
      return Math.max(1, (ctx.targets?.() || []).length);
    }
    function expectedSpecs() {
      const refs = ctx.targets?.() || [];
      const keys = new Set();
      for (const ref of refs) {
        keys.add(`${ref.library || ""}:${ref.experiment || ""}`);
      }
      return Math.max(1, keys.size);
    }
    function specKey(envelope) {
      const ref = envelope?.ref || {};
      return `${ref.library || ""}:${ref.experiment || ""}`;
    }
    function jobKey(envelope, payload) {
      const overlay = envelope?.ref?.overlay || payload.overlay || "";
      const id = payload.id || payload.job_id || envelope?.ref?.run || "";
      if (overlay && id) return `${overlay}:${id}`;
      if (overlay) return `overlay:${overlay}`;
      return id || "job";
    }
    function liveCount() {
      return [...jobs.values()].filter((s) => s === "running" || s === "pending").length;
    }
    function syncRunPhase() {
      const n = liveCount();
      if (n > 0) {
        phase = phase === "loading" ? "starting" : "running";
        return;
      }
      if (!jobs.size) return;
      if (jobs.size < expectedRuns() && ["starting", "running", "started", "stopping"].includes(phase)) {
        phase = "running";
        return;
      }
      if (["starting", "running", "started", "stopping"].includes(phase)) phase = "stopped";
    }
    function finishLoad() {
      if (phase !== "loading") return;
      if (specs.size === 0) {
        phase = "idle";
        return;
      }
      if (liveCount()) phase = "starting";
      else if (jobs.size) phase = "stopped";
      else phase = "not-started";
    }
    function paint() {
      const n = liveCount();
      if (phase === "running" || phase === "started" || phase === "stopping" || phase === "scoring") {
        chip.textContent = phase === "scoring" ? "scoring" : n ? `running ${n}` : "running";
        chip.className = "run-state-chip running";
        spin.hidden = n === 0 && phase !== "scoring";
        return;
      }
      chip.textContent = LABELS[phase] || phase;
      chip.className = `run-state-chip ${phase === "started" ? "running" : phase}`;
      spin.hidden = !["loading", "starting"].includes(phase);
    }
    paint();
    reset.addEventListener("click", async () => {
      jobs.clear();
      phase = "not-started";
      await resetView(ctx);
      paint();
    });
    return {
      update(envelope) {
        const topic = envelope?.topic || "";
        const payload = envelope?.payload || {};
        if (topic === "view.reset") {
          const next = payload.phase || payload.wait || "";
          jobs.clear();
          specs.clear();
          snapshotOpen = false;
          if (next === "load" || next === "spec") phase = "loading";
          else if (next === "run") phase = "starting";
          else if (next === "stopped" || next === "error") phase = "stopped";
          else phase = "not-started";
          paint();
          return;
        }
        if (topic === "view.snapshot") {
          if (payload.phase === "start") {
            snapshotOpen = true;
            phase = "loading";
            jobs.clear();
            specs.clear();
          } else {
            snapshotOpen = false;
            finishLoad();
          }
          paint();
          return;
        }
        if (topic === "spec.snapshot") {
          specs.add(specKey(envelope));
          // Topology hydrate is not a run. Finish even if a snapshot fetch is
          // still open — otherwise LOADING sticks while the prompt sits "not sent".
          if (phase === "loading" && specs.size >= expectedSpecs() && !liveCount()) finishLoad();
          paint();
          return;
        }
        if (topic === "spec.error" && phase === "loading") {
          phase = "idle";
          paint();
          return;
        }
        const acceptJob = snapshotOpen || phase === "loading" || !envelope?.replay;
        if (topic === "job.started" && acceptJob) {
          jobs.set(jobKey(envelope, payload), payload.status || "running");
          if (phase === "not-started" || phase === "stopped" || phase === "idle" || phase === "starting") {
            phase = "running";
          }
          syncRunPhase();
          paint();
          return;
        }
        if (topic === "job.status_changed" && acceptJob) {
          jobs.set(jobKey(envelope, payload), payload.status || "running");
          syncRunPhase();
          paint();
          return;
        }
        if (topic === "job.completed" && acceptJob) {
          jobs.set(jobKey(envelope, payload), payload.status || "stopped");
          syncRunPhase();
          paint();
          return;
        }
        if ((topic === "native.pipeline_phase" || payload.kind === "pipeline_phase") && payload.phase === "post") {
          if (["starting", "running", "started"].includes(phase)) phase = "scoring";
          paint();
          return;
        }
        if (phase === "starting" && topic.startsWith("native.") && !envelope?.replay) {
          const kind = payload.kind || topic.slice("native.".length);
          if (LIVE_KINDS.has(kind)) {
            phase = liveCount() ? "running" : "starting";
            paint();
          }
        }
      },
      destroy() {
        wrap.remove();
      },
    };
  },
};

export const alertBannerPlugin = {
  id: "alert-banner",
  kind: "chrome",
  mount(container) {
    const wrap = el("div", "alert-banner");
    wrap.hidden = true;
    const summary = document.createElement("button");
    summary.type = "button";
    summary.className = "alert-summary";
    const log = el("pre", "alert-log");
    log.hidden = true;
    wrap.append(summary, log);
    container.appendChild(wrap);
    const lines = [];
    function paint() {
      wrap.hidden = lines.length === 0;
      summary.textContent = lines.length === 1 ? lines[0].short : `${lines.length} errors`;
      log.textContent = lines.map((item) => `${item.ts}  ${item.topic}  ${item.detail}`).join("\n");
    }
    summary.addEventListener("click", () => {
      log.hidden = !log.hidden;
    });
    function push(envelope, detail) {
      const text = String(detail || "").trim();
      if (!text) return;
      const ts = envelope.ts || "";
      const topic = envelope.topic || "";
      if (lines.some((item) => item.detail === text)) return;
      lines.push({ ts, topic, detail: text, short: text.split("\n")[0].slice(0, 120) });
      paint();
    }
    return {
      update(envelope) {
        const topic = envelope?.topic || "";
        const payload = envelope?.payload || {};
        if (topic === "view.reset") {
          lines.length = 0;
          log.hidden = true;
          paint();
          return;
        }
        if (envelope?.replay) return;
        let attach = "";
        try {
          attach = localStorage.getItem("demo-ui-attach") || "";
        } catch {
          attach = "";
        }
        const batch = envelope?.ref?.batch;
        if (attach && attach !== "new" && batch && batch !== attach) return;
        if (attach === "new" && envelope?.replay) return;
        if (topic === "ui.error" || topic === "spec.error") {
          push(envelope, payload.error || payload.detail || JSON.stringify(payload));
          return;
        }
        if (topic === "job.completed") {
          const status = String(payload.status || "");
          if (status && !["completed", "ok", "success"].includes(status)) {
            push(
              envelope,
              payload.error || payload.error_message || payload.detail || status,
            );
          }
          return;
        }
        if (topic.startsWith("result.")) {
          for (const row of payload.results || []) {
            if (row && row.ok === false) push(envelope, row.detail || "command failed");
          }
          return;
        }
        const blob = String(payload.output || payload.content || payload.text || payload.error || "");
        // Recorded traces can contain a historical miss string. Live MAS failures
        // still arrive as job.completed with a non-ok status above.
        if (/llm_cache miss/i.test(blob)) {
          return;
        }
        if (/\[delegation\]/i.test(blob) && /failed/i.test(blob)) {
          push(envelope, blob.split("\n")[0].slice(0, 200));
          return;
        }
        if (payload.error) {
          push(envelope, String(payload.error).split("\n")[0].slice(0, 200));
        }
      },
      destroy() {
        wrap.remove();
      },
    };
  },
};

export const sessionPickerPlugin = {
  id: "session-picker",
  kind: "chrome",
  mount(container, ctx) {
    const wrap = el("div", "session-picker");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "session-toggle";
    toggle.setAttribute("aria-haspopup", "listbox");
    toggle.setAttribute("aria-label", "Session");
    const menu = el("div", "session-menu");
    menu.hidden = true;
    wrap.append(toggle, menu);
    container.appendChild(wrap);
    const ATTACH = "demo-ui-attach";
    let rows = [];
    let open = false;

    function currentAttach() {
      try {
        return localStorage.getItem(ATTACH) || "";
      } catch {
        return "";
      }
    }
function shortOverlay(name) {
  return String(name || "")
    .replace(/-round-cap$/, "")
    .replace(/^with-/, "")
    .replace(/-entry$/, "");
}

function rowTime(row) {
  if (row.time) return row.time;
  const stamp = String(row.label || row.ts || "");
  const match = stamp.match(/\b(\d{2}:\d{2})\b/);
  return match ? match[1] : "";
}

function rowOverlays(row) {
  const names = (row.overlays || []).map(shortOverlay).filter(Boolean);
  if (names.length) return [...new Set(names)].join(" / ");
  const fromLabel = String(row.label || "").split(" · ").slice(1).join(" · ");
  return fromLabel
    .split(",")
    .map((part) => shortOverlay(part.trim()))
    .filter(Boolean)
    .join(" / ");
}

function rowLabel(row, { compact = false } = {}) {
  const time = rowTime(row);
  const overlays = rowOverlays(row);
  const status = row.status && row.status !== "completed" ? row.status : "";
  if (compact) return [time || "session", status].filter(Boolean).join(" · ");
  return [time, overlays, status].filter(Boolean).join(" · ") || String(row.id || "session").slice(0, 8);
}
    function close() {
      open = false;
      menu.hidden = true;
    }
    function paint() {
      const attach = currentAttach();
      const current = rows.find((row) => row.id === attach);
      if (!attach || attach === "new" || !current) {
        toggle.textContent = "New session ▾";
      } else {
        toggle.textContent = `${rowLabel(current, { compact: true })} ▾`;
      }
      menu.replaceChildren();
      const actions = el("div", "session-menu-actions");
      const neu = document.createElement("button");
      neu.type = "button";
      neu.className = "session-action";
      neu.textContent = "New session";
      neu.addEventListener("click", (event) => {
        event.stopPropagation();
        attachSession("new");
        close();
      });
      const wipe = document.createElement("button");
      wipe.type = "button";
      wipe.className = "session-action danger";
      wipe.textContent = "Delete all UI sessions";
      wipe.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteAll();
      });
      actions.append(neu, wipe);
      menu.appendChild(actions);
      if (!rows.length) {
        const empty = el("div", "session-empty");
        empty.textContent = "No UI sessions";
        menu.appendChild(empty);
        return;
      }
      for (const row of rows) {
        const item = el("div", "session-row");
        const pick = document.createElement("button");
        pick.type = "button";
        pick.className = "session-pick";
        pick.textContent = rowLabel(row);
        if (row.id === attach) pick.classList.add("active");
        pick.addEventListener("click", (event) => {
          event.stopPropagation();
          attachSession(row.id);
          close();
        });
        const del = document.createElement("button");
        del.type = "button";
        del.className = "session-delete";
        del.title = "Delete session";
        del.setAttribute("aria-label", `Delete ${row.label}`);
        del.textContent = "✕";
        del.addEventListener("click", (event) => {
          event.stopPropagation();
          deleteOne(row);
        });
        item.append(pick, del);
        menu.appendChild(item);
      }
    }
    async function refresh() {
      try {
        const res = await apiFetch("/api/runs");
        if (!res.ok) return;
        const data = await res.json();
        rows = data.runs || [];
        paint();
      } catch {
        /* gateway down */
      }
    }
    async function attachSession(id) {
      try {
        localStorage.setItem(ATTACH, id || "new");
      } catch {
        /* ignore */
      }
      ctx.bus?.emit({ topic: "view.reset", ts: new Date().toISOString(), ref: {}, source: "view", payload: { phase: "load" } });
      paint();
      if (!id || id === "new") {
        await ctx.bus?.loadSnapshot?.(ctx.layoutName, {});
        return;
      }
      const row = rows.find((item) => item.id === id) || { batch: id };
      await ctx.bus?.loadSnapshot?.(ctx.layoutName, { batch: row.batch || row.id, session: row.session });
    }
    async function command(verb, extra) {
      await apiFetch("/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verb, ...extra }),
      });
    }
    async function deleteOne(row) {
      await command("delete-run", { ref: { batch: row.batch || row.id, run: row.id, origin: "demo-ui" } });
      if (currentAttach() === row.id) await attachSession("new");
      await refresh();
    }
    async function deleteAll() {
      rows = [];
      paint();
      await command("delete-ui-runs", { ref: { origin: "demo-ui" } });
      await attachSession("new");
      await refresh();
      close();
    }
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (open) {
        close();
        return;
      }
      open = true;
      menu.hidden = false;
      paint();
    });
    wrap.addEventListener("click", (event) => event.stopPropagation());
    const onDocClick = () => close();
    document.addEventListener("click", onDocClick);
    refresh();
    return {
      update(envelope) {
        const topic = envelope?.topic || "";
        if (
          topic === "job.started" ||
          topic === "job.completed" ||
          topic === "job.status_changed" ||
          topic === "job.deleted"
        ) {
          refresh();
        }
      },
      destroy() {
        document.removeEventListener("click", onDocClick);
        wrap.remove();
      },
    };
  },
};

export const connectionStatusPlugin = {
  id: "connection-status",
  kind: "chrome",
  mount(container, ctx) {
    const wrap = el("div", "connection-status");
    const dot = el("span", "status-dot");
    wrap.append(dot);
    container.appendChild(wrap);
    if (ctx?.bus?.mode === "offline") {
      wrap.title = "offline";
      dot.classList.add("offline");
    } else {
      wrap.title = "controller";
      fetch("/health")
        .then((r) => {
          if (r.ok) dot.classList.add("live");
        })
        .catch(() => {});
    }
    return {
      destroy() {
        wrap.remove();
      },
    };
  },
};

export const navLinksPlugin = {
  id: "nav-links",
  kind: "chrome",
  mount(container, ctx) {
    const root = el("div", "nav-links");
    const current = (location.hash || "").replace(/^#/, "");
    for (const item of ctx.config.items || []) {
      const a = document.createElement("a");
      a.className = "menu-item";
      a.href = `#${item.view || ""}`;
      a.textContent = item.label || item.view || "view";
      if ((item.view || "") === current || current.endsWith(item.view || "")) {
        a.classList.add("active");
      }
      root.appendChild(a);
    }
    container.appendChild(root);
    return {
      destroy() {
        root.remove();
      },
    };
  },
};

export const breadcrumbPlugin = { ...titlePlugin, id: "breadcrumb" };
export const runCommandBarPlugin = { ...promptInputPlugin, id: "run-command-bar" };

export const modeSwitchPlugin = {
  id: "mode-switch",
  kind: "chrome",
  mount(container, ctx) {
    const wrap = el("div", "chrome-modes");
    const modes = el("div", "mode-switch");
    const allowed = parseModes(ctx.layout?.modes ?? ctx.config?.modes ?? "both");
    const liveBtn = document.createElement("button");
    liveBtn.type = "button";
    liveBtn.textContent = "Live";
    const replayBtn = document.createElement("button");
    replayBtn.type = "button";
    replayBtn.textContent = "Replay";
    const passiveBtn = document.createElement("button");
    passiveBtn.type = "button";
    passiveBtn.textContent = "Passive";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "view-reset";
    reset.textContent = "Reset";
    reset.title = "Clear every plugin";
    function paint() {
      const mode = chromeMode(ctx.bus, allowed[0]);
      liveBtn.classList.toggle("active", mode === "live");
      replayBtn.classList.toggle("active", mode === "replay");
      passiveBtn.classList.toggle("active", mode === "passive");
      liveBtn.hidden = !allowed.includes("live");
      replayBtn.hidden = !allowed.includes("replay");
      passiveBtn.hidden = !allowed.includes("passive");
      modes.hidden = allowed.length < 2;
    }
    async function setMode(mode) {
      if (!allowed.includes(mode)) return;
      if (ctx.bus) ctx.bus.chromeMode = mode;
      // Layout-declared static replay: dataSource.staticBundle names a real
      // recorded bundle (see scripts/record-bundle.py), so switching to
      // replay plays THAT through the same offline player the ?mode=offline
      // URL path already uses, instead of attempting the live mode:replay
      // WS path (broken independently of this change -- not fixed here).
      // Guarded by !ctx.bus.player so repeat clicks don't re-fetch/re-play.
      // NOTE: this is read from the mode-switch plugin's own `props`, not
      // `dataSource.staticBundle` -- the server's DataSourceRef model
      // (lab/.../ui_stream/refs.py, used by GET /api/layouts/<name>) only
      // round-trips a fixed field set (library/experiment/scenario/mode/...)
      // and silently drops unknown dataSource keys before the client ever
      // sees them (confirmed: a `staticBundle` placed under `dataSource:`
      // in the layout YAML never reaches the browser at all). Plugin props
      // are untouched free-form JSON (same as live-run's defaultPrompt/
      // timeout), so that's where this has to live instead.
      const staticBundleUrl = ctx.config?.staticBundle;
      if (mode === "replay" && staticBundleUrl && ctx.bus && !ctx.bus.player) {
        const bundle = await loadStaticBundle(staticBundleUrl);
        if (bundle) {
          ctx.bus.mode = "offline";
          const offlinePlayer = createOfflinePlayer(ctx.bus, bundle.envelopes || []);
          ctx.bus.player = offlinePlayer;
          offlinePlayer.play();
        }
      }
      ctx.bus?.emit({
        topic: "view.mode",
        ts: new Date().toISOString(),
        ref: {},
        source: "view",
        payload: { mode },
      });
      if (mode === "live") {
        ctx.bus?.attach?.({
          library: ctx.dataSource?.library,
          experiment: ctx.dataSource?.experiment,
        });
      }
      paint();
    }
    liveBtn.addEventListener("click", () => setMode("live"));
    replayBtn.addEventListener("click", () => setMode("replay"));
    passiveBtn.addEventListener("click", () => setMode("passive"));
    reset.addEventListener("click", () => resetView(ctx));
    if (allowed.includes("live")) modes.appendChild(liveBtn);
    if (allowed.includes("replay")) modes.appendChild(replayBtn);
    if (allowed.includes("passive")) modes.appendChild(passiveBtn);
    wrap.append(modes, reset);
    container.appendChild(wrap);
    if (ctx.bus && !ctx.bus.chromeMode) {
      ctx.bus.chromeMode = initialChromeMode(allowed, ctx.dataSource?.mode, ctx.layout?.mode);
    }
    paint();
    return {
      update(envelope) {
        if (envelope?.topic === "view.mode") {
          paint();
          return;
        }
        if (envelope?.topic === "job.started" && (envelope?.replay || envelope?.payload?.command === "ingest")) {
          if (allowed.includes("passive") && chromeMode(ctx.bus) !== "passive") {
            setMode("passive");
          }
        }
      },
      destroy() {
        wrap.remove();
      },
    };
  },
};

export const liveRunPlugin = {
  id: "live-run",
  kind: "chrome",
  mount(container, ctx) {
    const wrap = el("div", "live-run");
    const go = document.createElement("button");
    go.type = "button";
    go.textContent = ctx.config.runLabel || "Run";
    const status = el("div", "run-status");
    wrap.append(go, status);
    container.appendChild(wrap);
    const allowed = parseModes(ctx.layout?.modes ?? ctx.config?.modes ?? "both");
    function isLive() {
      if (!allowed.includes("live")) return false;
      return chromeMode(ctx.bus, allowed[0]) === "live";
    }
    wrap.hidden = !isLive();
    go.addEventListener("click", async () => {
      const text = String(ctx.config.defaultPrompt || ctx.config.text || "").trim();
      if (!text) return;
      const batch = crypto.randomUUID();
      ctx.bus?.emit({
        topic: "view.reset",
        ts: new Date().toISOString(),
        ref: {},
        source: "view",
        payload: { phase: "run" },
      });
      const experiment = ctx.dataSource?.experiment;
      const mode = "live";
      const seen = new Set();
      const targets = [];
      for (const item of ctx.targets?.() || [ctx.dataSource]) {
        const key = ["library", "experiment", "scenario", "overlay"].map((k) => item?.[k] || "").join("|");
        if (seen.has(key)) continue;
        seen.add(key);
        targets.push({ ...item, batch, mode });
      }
      const verb = ctx.config.verb || (experiment ? "run-experiment" : "run-mas");
      const liveRef = {
        library: ctx.dataSource?.library,
        experiment,
        batch,
        mode: "live",
      };
      // Explicit opt-in/out for the "no recorded gold trace -> fall through to
      // a genuine, LLM-cache-backed live run" behavior. Layouts that don't set
      // this prop get the historical behavior (server treats it as allowed),
      // so existing layouts stay unaffected.
      // Layouts that do set it make the choice visible in their own YAML
      // instead of it being a silent side effect of whether a
      // infra/.cache/<scenario>.events.jsonl happens to exist on disk.
      const allowLiveFallback = ctx.config.allowLiveFallback;
      try {
        localStorage.setItem("demo-ui-attach", batch);
      } catch {
        /* ignore */
      }
      try {
        await ctx.bus?.attach?.(liveRef);
        status.textContent = `Starting ${targets.length} run(s)…`;
        const result = await ctx.send({
          verb,
          text,
          query: text,
          experiment,
          ref: { ...ctx.dataSource, batch, mode: "live" },
          timeout: ctx.config.timeout || undefined,
          pre: targets.flatMap((item) => item?.pre || []),
          targets,
          batch,
          ...(allowLiveFallback === undefined ? {} : { allow_live_fallback: Boolean(allowLiveFallback) }),
        });
        const rows = result?.results || [];
        const okRows = rows.filter((row) => row && row.ok !== false);
        const failed = rows.filter((row) => row && row.ok === false);
        if (!okRows.length && failed.length) {
          status.textContent = failed.map((row) => row.detail || "failed").join(" · ");
          ctx.bus?.emit({
            topic: "ui.error",
            ts: new Date().toISOString(),
            ref: liveRef,
            source: "view",
            payload: { error: status.textContent, results: rows },
          });
        } else {
          status.textContent = `${okRows.length || targets.length} run(s) started`;
        }
      } catch (err) {
        status.textContent = String(err?.message || err);
        ctx.bus?.emit({
          topic: "ui.error",
          ts: new Date().toISOString(),
          ref: liveRef,
          source: "view",
          payload: { error: String(err?.message || err) },
        });
        ctx.bus?.emit({
          topic: "view.reset",
          ts: new Date().toISOString(),
          ref: {},
          source: "view",
          payload: { phase: "error" },
        });
      }
    });
    return {
      update(envelope) {
        if (envelope?.topic === "view.mode") wrap.hidden = !isLive();
        const payload = envelope?.payload || {};
        if (envelope?.topic === "native.pipeline_phase" && payload.phase === "post") {
          status.textContent = "Scoring…";
        } else if (envelope?.topic === "job.completed" && payload.status === "completed") {
          status.textContent = "Done";
        } else if (envelope?.topic === "job.completed" && payload.status && payload.status !== "completed") {
          status.textContent = payload.error || payload.status;
        }
      },
      destroy() {
        wrap.remove();
      },
    };
  },
};
