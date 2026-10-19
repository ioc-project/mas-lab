/**
 * Host for a mas-lab plot. Demo-ui ships no plot plugins — this is
 * dispatch: load GET /api/plots/{kind}?mode=embedded plus draw.js, subscribe
 * to the artefact address registered on that plot, and feed ticks into the
 * in-document drawer. Plugins render in the host (no iframe).
 *
 * Routing is the panel ``stream`` (and optional ``dirac``) prop. A stacked
 * panel lists N ``charts``; each chart is its own stream subscription.
 */

import { apiFetch } from "./http.js";
import { createEmbeddedPlot, ensureD3, loadEmbeddedSpec } from "./plot-runtime.js";

function streamOf(ctx) {
  return String(ctx?.config?.stream || "").trim();
}

function artefactName(ctx) {
  return String(ctx?.config?.artefact || ctx?.config?.kind || "").trim();
}

export function ownerFromStream(stream) {
  const raw = String(stream || "").trim().replace(/\.plot$/, "").replace(/\.sealed$/, "").replace(/\.snapshot$/, "");
  if (!raw) return "";
  if (raw.startsWith("custom.") || raw.startsWith("series.") || raw.startsWith("artifact.")) {
    const parts = raw.split(".").filter(Boolean);
    return parts.slice(0, 2).join(".");
  }
  if (raw.startsWith("metric")) return "metric";
  return raw;
}

export function ownerOf(ctx) {
  const named = ownerFromStream(streamOf(ctx));
  if (named) return named;
  const artefact = artefactName(ctx);
  if (!artefact) return "";
  if (artefact.startsWith("custom.") || artefact.startsWith("series.")) return ownerFromStream(artefact);
  if (artefact.includes(".")) return ownerFromStream(artefact);
  return `custom.${artefact}`;
}

export function diracOf(ctx) {
  return ownerFromStream(String(ctx?.config?.dirac || "").trim());
}

export function accumulateDiracs(prev, incoming) {
  const out = Array.isArray(prev) ? prev.slice() : [];
  if (!Array.isArray(incoming) || !incoming.length) return out;
  const seen = new Set(out.map((row) => row && row.id).filter(Boolean));
  for (const row of incoming) {
    if (!row) continue;
    const id = String(row.id || "");
    if (id && seen.has(id)) continue;
    if (id) seen.add(id);
    out.push(row);
  }
  return out;
}

export function chartSpecs(ctx) {
  const raw = ctx?.config?.charts || ctx?.config?.metrics;
  if (Array.isArray(raw) && raw.length) {
    return raw.map((row) => ({
      title: row.title || row.ylabel || row.artefact || row.stream,
      stream: String(row.stream || "").trim(),
      artefact: String(row.artefact || "").trim(),
      dirac: String(row.dirac || "").trim(),
      threshold: row.threshold,
      ylabel: row.ylabel,
      xlabel: row.xlabel,
      yrange: row.yrange,
      labels: row.labels,
    }));
  }
  const stream = streamOf(ctx);
  const artefact = artefactName(ctx);
  if (!stream && !artefact) return [];
  return [
    {
      title: ctx?.config?.title,
      stream,
      artefact,
      dirac: String(ctx?.config?.dirac || "").trim(),
      threshold: ctx?.config?.threshold,
      ylabel: ctx?.config?.ylabel,
      xlabel: ctx?.config?.xlabel,
      yrange: ctx?.config?.yrange,
      labels: ctx?.config?.labels,
    },
  ];
}

function ownerOfSpec(spec) {
  if (!spec) return "";
  if (spec.stream) return ownerFromStream(spec.stream);
  if (!spec.artefact) return "";
  if (spec.artefact.startsWith("custom.") || spec.artefact.startsWith("series.")) {
    return ownerFromStream(spec.artefact);
  }
  if (spec.artefact.includes(".")) return ownerFromStream(spec.artefact);
  if (["chat", "topology", "story", "events"].includes(spec.artefact)) {
    return `custom.${spec.artefact}`;
  }
  return `series.${spec.artefact}`;
}

export function plotTopics(ctx) {
  const specs = chartSpecs(ctx);
  const topics = [];
  function add(name) {
    if (name && !topics.includes(name)) topics.push(name);
  }
  if (specs.length) {
    for (const spec of specs) {
      add(ownerOfSpec(spec));
      add(ownerFromStream(spec.dirac));
    }
  } else {
    add(ownerOf(ctx));
    add(diracOf(ctx));
  }
  topics.push("spec.snapshot", "spec.*", "view.reset");
  if (rendererKind(inferPlotKind(ctx)) === "series") topics.push("job.progress", "job.completed");
  if (inferPlotKind(ctx) === "topology") topics.push("job.completed");
  return topics;
}

export function inferPlotKind(ctx) {
  const named = String(ctx?.config?.kind || "").trim();
  if (named && named.indexOf(".") < 0) return named;
  const blob = `${streamOf(ctx)} ${artefactName(ctx)} ${ownerOf(ctx)}`.toLowerCase();
  if (blob.includes("topology")) return "topology";
  if (blob.includes("chat")) return "chat";
  if (blob.includes("story")) return "story";
  if (blob.includes("trajectory")) return "trajectory";
  if (streamOf(ctx).startsWith("series.") || artefactName(ctx).startsWith("series.")) return "series";
  return named || "series";
}

export function rendererKind(plotKind) {
  const k = String(plotKind || "").trim();
  if (k === "loop" || k === "series") return "series";
  return k;
}

export function pinKindInShell(html, plotKind) {
  const k = JSON.stringify(rendererKind(plotKind) || "chat");
  if (!html) return html;
  return String(html)
    .replace(/const kind = STATE\.kind;/, `const kind = ${k};`)
    .replace(/const kind = kindOf\([^;]+;/, `const kind = ${k};`);
}

const CHART_KEYS = ["data", "cards", "nodes", "edges", "series", "events", "diracs", "agents", "roster", "fragments", "activity"];

function hasChart(payload) {
  if (!payload || typeof payload !== "object") return false;
  return CHART_KEYS.some((key) => payload[key] != null && payload[key] !== "");
}

function payloadRenderer(payload) {
  if (!payload || typeof payload !== "object") return "";
  const k = String(payload.kind || "");
  if (k.includes("topology") || k === "mas_topology") return "topology";
  if (k.startsWith("series.") || k === "series" || k === "loop") return "series";
  if (k.includes("chat")) return "chat";
  const owner = k.startsWith("custom.") ? k.split(".")[1] : "";
  if (owner && owner.indexOf(".") < 0) return owner;
  if (payload.nodes || payload.edges) return "topology";
  if (payload.cards) return "chat";
  if (payload.series) return "series";
  if (Array.isArray(payload.diracs) || payload.shape === "dirac") return "dirac";
  if (payload.events) return (k && k.indexOf(".") < 0) ? k : "events";
  return "";
}

export function wants(envelope, ctx, owner, plotKind) {
  const topic = String(envelope?.topic || "");
  if (topic === "view.reset") return "reset";
  if (topic === "job.progress" && rendererKind(plotKind) === "series") return "clock";
  if (
    topic === "job.completed"
    && (rendererKind(plotKind) === "series" || plotKind === "topology" || String(owner).includes("topology"))
  ) {
    return "idle";
  }
  if (
    (plotKind === "topology" || String(owner).includes("topology")) &&
    (topic === "spec.snapshot" || topic.startsWith("spec."))
  ) {
    return "spec";
  }
  const specs = chartSpecs(ctx);
  for (const spec of specs) {
    const dirac = ownerFromStream(spec.dirac);
    if (dirac && (topic === dirac || topic.startsWith(`${dirac}.`))) {
      if (topic.endsWith(".html")) return "";
      return artefactOf(envelope.payload) ? "dirac" : "";
    }
  }
  const trees = specs.length
    ? specs.map(ownerOfSpec).filter(Boolean)
    : [owner || ownerOf(ctx)].filter(Boolean);
  if (!trees.some((tree) => topic === tree || topic.startsWith(`${tree}.`))) return "";
  if (topic.endsWith(".html")) return "";
  const payload = artefactOf(envelope.payload);
  if (!payload) return "";
  const mine = rendererKind(plotKind);
  const theirs = payloadRenderer(payload);
  if (theirs === "dirac") return "dirac";
  if (mine && theirs && mine !== theirs) return "";
  return "artefact";
}

export function artefactOf(payload) {
  if (!payload || typeof payload !== "object") return null;
  // HTML is the shell (GET /api/plots). Live ticks are JSON chart data only.
  if (typeof payload.html === "string" && payload.html && !hasChart(payload)) return null;
  if (payload.data && typeof payload.data === "object") return payload.data;
  const out = { ...payload };
  delete out.html;
  return out;
}

function capSeries(data, xrange) {
  const cap = Math.max(8, Number(xrange) || 64);
  const series = data?.series;
  if (!series || typeof series !== "object") return data;
  const next = {};
  let trimmed = false;
  for (const [key, ys] of Object.entries(series)) {
    if (Array.isArray(ys) && ys.length > cap) {
      next[key] = ys.slice(-cap);
      trimmed = true;
    } else {
      next[key] = ys;
    }
  }
  return trimmed ? { ...data, series: next } : data;
}

function coordOf(ref) {
  const keys = ["library", "experiment", "scenario", "test", "run"];
  return keys.map((key) => String(ref?.[key] || "").trim() || "-");
}

export function addressOf(name, ref) {
  if (!name) return "";
  const coord = coordOf(ref);
  if (coord.every((part) => part === "-")) return "";
  return `/api/artefacts/${coord.join("/")}/${encodeURIComponent(name)}`;
}

function topologyFromSpec(spec) {
  if (!spec || typeof spec !== "object") return null;
  const user = String(spec.user || "user");
  const skip = new Set(["", "mas", "user", "agent"]);
  let entry = String(spec.entry || "");
  if (!entry || skip.has(entry)) {
    const dels = spec.delegates_to || {};
    const children = new Set(Object.values(dels).flat().filter(Boolean).map(String));
    const roots = Object.keys(dels).filter((id) => !children.has(id) && !skip.has(id));
    entry = roots[0] || "";
  }
  const nodes = [{ id: user, role: "user" }];
  const seen = new Set([user]);
  function ensure(id, role) {
    const aid = String(id || "").trim();
    if (!aid || seen.has(aid)) return;
    seen.add(aid);
    nodes.push({ id: aid, role: aid === entry ? "entry" : role });
  }
  for (const agent of spec.agents || []) {
    ensure(agent?.id || agent?.name, "agent");
  }
  const edges = [];
  if (entry) {
    ensure(entry, "entry");
    edges.push({ from: user, to: entry });
  }
  for (const [src, peers] of Object.entries(spec.delegates_to || {})) {
    ensure(src, src === entry ? "entry" : "agent");
    for (const peer of peers || []) {
      if (src && peer) {
        ensure(peer, "agent");
        edges.push({ from: String(src), to: String(peer) });
      }
    }
  }
  if (nodes.length < 2) return null;
  return { kind: "topology", nodes, edges };
}

function coerce(plotKind, data) {
  if (!data || typeof data !== "object") return data;
  if (
    plotKind === "topology" &&
    (data.agents || data.kind === "mas_topology" || data.delegates_to)
  ) {
    return topologyFromSpec(data) || data;
  }
  return data;
}

let catalogPromise;

function artefactCatalog() {
  if (!catalogPromise) {
    catalogPromise = apiFetch("/api/artefacts")
      .then((res) => (res.ok ? res.json() : { artefacts: [] }))
      .then((data) => {
        const map = new Map();
        for (const row of data.artefacts || []) {
          if (row.name) map.set(row.name, row);
          if (row.plot) map.set(row.plot, row);
        }
        return map;
      })
      .catch(() => new Map());
  }
  return catalogPromise;
}

export const pluginMeta = {
  id: "plot",
  kind: "plot",
  formats: ["html"],
  realtime: true,
  embedded: true,
};

export const htmlPlotPlugin = {
  id: "plot",
  kind: "output",
  formats: ["html"],
  realtime: true,
  topics: plotTopics,
  title: "Plot",
  info:
    "Hosts a mas-lab plot. The panel artefact (or kind) names a registered " +
    "realtime artefact; the subscribe path is that artefact's address. " +
    "A pipeline uses the same plot on the end-state artefact.",
  gets: ["spec"],
  subscribes: ["view.reset"],
  mount(container, ctx) {
    container.style.cssText = "height:100%;min-height:0;overflow:hidden;background:#0f172a";
    let runtime = null;
    let ready = false;
    let pending = null;
    let owner = ownerOf(ctx);
    let plotKind = inferPlotKind(ctx);
    let artefact = String(ctx?.config?.artefact || "").trim();
    let tries = 0;
    let timer = 0;
    let gotPayload = false;
    let lastSent = "";
    let gen = 0;
    function extras() {
      const cfg = ctx?.config || {};
      const out = {};
      if (cfg.xrange != null) out.xrange = cfg.xrange;
      if (cfg.yrange != null) out.yrange = cfg.yrange;
      if (cfg.labels) out.labels = cfg.labels;
      if (cfg.xlabel) out.xlabel = cfg.xlabel;
      if (cfg.ylabel) out.ylabel = cfg.ylabel;
      if (cfg.vertical != null) out.vertical = cfg.vertical;
      if (cfg.view != null) out.view = cfg.view;
      if (cfg.threshold != null) out.threshold = cfg.threshold;
      if (cfg.pinUser != null) out.pinUser = cfg.pinUser;
      return out;
    }
    let diracs = [];
    function mergeDiracs(incoming) {
      diracs = accumulateDiracs(diracs, incoming);
    }
    function send(data, epoch) {
      if (epoch != null && epoch !== gen) return;
      const coerced = coerce(plotKind, data);
      const locked = rendererKind(plotKind) || plotKind;
      const cfg = extras();
      if (coerced && typeof coerced === "object") mergeDiracs(coerced.diracs);
      const withMarks =
        coerced && typeof coerced === "object" ? { ...coerced, diracs } : coerced;
      const payload =
        withMarks && typeof withMarks === "object"
          ? capSeries({ ...withMarks, ...cfg, kind: locked }, cfg.xrange || withMarks.xrange)
          : withMarks;
      const token = JSON.stringify(payload);
      if (token && token === lastSent) return;
      lastSent = token;
      if (payload && typeof payload === "object") gotPayload = true;
      if (!ready || !runtime) {
        pending = payload;
        return;
      }
      runtime.update(payload);
    }
    let shellLoaded = false;
    function loadEmbedded(spec) {
      if (shellLoaded) return;
      shellLoaded = true;
      ready = false;
      ensureD3().then(async (d3) => {
        runtime = await createEmbeddedPlot(container, {
          kind: rendererKind(plotKind) || plotKind,
          css: spec.css || "",
          d3,
        });
        ready = true;
        const specs = chartSpecs(ctx);
        if (specs.length > 1 && !Array.isArray(ctx._facets)) {
          ctx._facets = specs.map((spec) => ({ ...spec, series: {}, diracs: [] }));
          send({ charts: ctx._facets, kind: "series" }, gen);
        }
        if (pending) {
          send(pending, gen);
          pending = null;
        }
      }).catch(() => {});
    }
    function loadFile(url) {
      container.innerHTML = "";
      const img = document.createElement("img");
      img.src = url;
      img.style.cssText = "max-width:100%;max-height:100%;object-fit:contain";
      container.appendChild(img);
    }
    function pullLatest() {
      const specs = chartSpecs(ctx);
      const names = [];
      for (const spec of specs) {
        const name = spec.artefact || String(spec.stream || "").replace(/^series\./, "");
        if (name && !names.includes(name)) names.push(name);
      }
      if (!names.length && (artefact || plotKind)) names.push(artefact || plotKind);
      names.forEach((name) => pullOne(name));
    }
    function pullOne(name) {
      const epoch = gen;
      if (!name) return;
      const href = addressOf(name, ctx?.dataSource);
      const qs = new URLSearchParams();
      const ds = ctx?.dataSource || {};
      if (ds.scenario) qs.set("scenario", ds.scenario);
      if (ds.library) qs.set("library", ds.library);
      if (ds.experiment) qs.set("experiment", ds.experiment);
      if (ds.test) qs.set("test", ds.test);
      if (ds.run) qs.set("run", ds.run);
      const fallback = `/api/artefacts/${encodeURIComponent(name)}${qs.toString() ? `?${qs}` : ""}`;
      const urls = href && href !== fallback ? [href, fallback] : [fallback];
      function tryUrl(index) {
        apiFetch(urls[index])
          .then((res) => (res.ok ? res.json() : null))
          .then((body) => {
            if (body?.stream && !streamOf(ctx)) owner = ownerFromStream(body.stream) || owner;
            const payload = body?.payload && typeof body.payload === "object" ? artefactOf(body.payload) : null;
            if (payload) {
              gotPayload = true;
              if (timer) {
                clearTimeout(timer);
                timer = 0;
              }
              const spec = chartSpecs(ctx).find(
                (row) => row.artefact === name || row.stream === name || row.stream.endsWith(`.${name}`),
              );
              const topic = body?.stream || spec?.stream || (name.includes(".") ? name : `series.${name}`);
              applyEnvelope({ topic, payload: body.payload || payload }, epoch);
              return;
            }
            if (gotPayload) return;
            if (index + 1 < urls.length) {
              tryUrl(index + 1);
              return;
            }
            if (tries < 3) {
              tries += 1;
              timer = setTimeout(pullLatest, 400 * tries);
            }
          })
          .catch(() => {
            if (index + 1 < urls.length) tryUrl(index + 1);
            else if (tries < 3) {
              tries += 1;
              timer = setTimeout(pullLatest, 400 * tries);
            }
          });
      }
      tryUrl(0);
    }
    function loadShell() {
      const kind = plotKind || artefact;
      if (!kind) return;
      loadEmbeddedSpec(kind)
        .then((spec) => {
          if (spec?.file) loadFile(spec.file);
          else loadEmbedded(spec);
        })
        .catch(() => {});
    }
    function applyEnvelope(envelope, epoch) {
      const hit = wants(envelope, ctx, owner, plotKind);
      if (hit === "reset") {
        gen += 1;
        pending = null;
        lastSent = "";
        gotPayload = false;
        diracs = [];
        if (timer) {
          clearTimeout(timer);
          timer = 0;
        }
        ctx._facets = null;
        send({ kind: plotKind, series: {}, events: [], cards: [], nodes: [], edges: [], charts: [], idle: true }, gen);
        return;
      }
      if (hit !== "artefact" && hit !== "spec" && hit !== "dirac" && hit !== "clock" && hit !== "idle") return;
      if (hit === "spec" && gotPayload) return;
      if (hit === "idle") {
        send({ activity: [], idle: true }, epoch);
        return;
      }
      if (hit === "clock") {
        const clock = envelope.payload || {};
        send({ now: clock.t, t: clock.t, t0: clock.t0, speed: clock.speed, t1: clock.t1 }, epoch);
        return;
      }
      const payload = artefactOf(envelope.payload);
      if (!payload) return;
      const specs = chartSpecs(ctx);
      if (specs.length > 1 && (hit === "artefact" || hit === "dirac")) {
        const topic = String(envelope?.topic || "");
        const idx = specs.findIndex((spec) => {
          const tree = ownerOfSpec(spec);
          const dirac = ownerFromStream(spec.dirac);
          return (tree && (topic === tree || topic.startsWith(`${tree}.`)))
            || (dirac && (topic === dirac || topic.startsWith(`${dirac}.`)));
        });
        if (idx < 0) return;
        const facets = Array.isArray(ctx._facets) ? ctx._facets : specs.map((spec) => ({ ...spec, series: {}, diracs: [] }));
        const cur = { ...facets[idx], title: specs[idx].title, stream: specs[idx].stream };
        const kept = cur.diracs;
        if (hit === "dirac") {
          cur.diracs = accumulateDiracs(kept, payload.diracs);
        } else {
          Object.assign(cur, payload);
          cur.diracs = accumulateDiracs(kept, payload.diracs);
        }
        if (specs[idx].threshold != null) cur.threshold = specs[idx].threshold;
        if (specs[idx].ylabel) cur.ylabel = specs[idx].ylabel;
        if (specs[idx].yrange) cur.yrange = specs[idx].yrange;
        facets[idx] = cur;
        ctx._facets = facets;
        send({ charts: facets, kind: "series" }, epoch);
        return;
      }
      if (hit === "dirac") {
        send({ diracs: payload.diracs || [] }, epoch);
        return;
      }
      send(payload, epoch);
    }
    artefactCatalog().then((catalog) => {
      const row = catalog.get(artefact) || catalog.get(plotKind);
      if (row) {
        artefact = row.name || artefact;
        plotKind = row.plot || plotKind || artefact;
        if (row.stream && !streamOf(ctx)) owner = ownerFromStream(row.stream) || owner;
      }
      loadShell();
      pullLatest();
    });
    return {
      update(envelope) {
        applyEnvelope(envelope);
      },
      destroy() {
        if (timer) clearTimeout(timer);
        runtime?.destroy();
      },
    };
  },
};

export const plotPlugin = htmlPlotPlugin;

export function htmlPlotFor(id, defaults) {
  return {
    ...htmlPlotPlugin,
    id,
    title: defaults?.title || htmlPlotPlugin.title,
    info: defaults?.info || htmlPlotPlugin.info,
    gets: ["spec"],
    topics: (ctx) => plotTopics({ config: { ...defaults, ...(ctx?.config || {}) } }),
    subscribes: plotTopics({ config: defaults }),
    mount(container, ctx) {
      const next = { ...ctx, config: { ...defaults, ...(ctx?.config || {}) } };
      return htmlPlotPlugin.mount(container, next);
    },
  };
}

export const chatPlugin = htmlPlotFor("chat", {
  artefact: "chat",
  kind: "chat",
  title: "Chat",
});

export const topologyPlugin = htmlPlotFor("topology", {
  artefact: "topology",
  kind: "topology",
  title: "Topology",
});
