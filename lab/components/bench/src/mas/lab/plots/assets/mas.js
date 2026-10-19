// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
// Chat + topology live drawers. Keep colours, full labels, depth indent,
// and activity pulse here — plugins no longer own this surface.

const USER_COLOR = "#facc15";
const PALETTE = [
  "#38bdf8",
  "#f472b6",
  "#4ade80",
  "#c084fc",
  "#fb923c",
  "#2dd4bf",
  "#818cf8",
  "#f43f5e",
  "#a3e635",
  "#22d3ee",
  "#e879f9",
  "#f97316",
  "#34d399",
  "#60a5fa",
  "#eab308",
  "#f87171",
  "#c4b5fd",
  "#67e8f9",
  "#fdba74",
  "#86efac",
  "#fda4af",
  "#7dd3fc",
  "#d946ef",
  "#94a3b8",
];
const COLOR_OF = {};

function paletteIndex(id) {
  let h = 2166136261;
  const s = String(id || "agent");
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % PALETTE.length;
}

function agentColor(id) {
  const key = String(id || "agent");
  if (key === "user") return USER_COLOR;
  if (COLOR_OF[key]) return COLOR_OF[key];
  const used = {};
  Object.keys(COLOR_OF).forEach((name) => { used[COLOR_OF[name]] = true; });
  let color = PALETTE.find((c) => !used[c]);
  if (!color) color = PALETTE[paletteIndex(key)];
  COLOR_OF[key] = color;
  return color;
}

function esc(v) {
  return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function parseT(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return NaN;
  return n > 1e12 ? n / 1000 : n;
}

function formatTs(ts, t0) {
  const t = parseT(ts);
  if (!Number.isFinite(t)) return ts == null || ts === "" ? "" : String(ts);
  const origin = parseT(t0);
  const dt = Number.isFinite(origin) ? t - origin : t;
  if (!Number.isFinite(dt)) return "";
  return Math.max(0, dt).toFixed(1) + "s";
}

function cardDepth(card) {
  const n = Number(card && card.depth);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function isUserCard(card) {
  return String(card && card.role || "") === "user";
}

function isToolCard(card) {
  return String(card && card.role || "") === "tool" || String(card && card.kind || "") === "tool" || String(card && card.kind || "") === "delegate";
}

const TEXT_LIMIT = 280;
const TOOL_CAP = 4;
const WAVE_GAP = 2;

function toolStartT(tool) {
  return parseT(tool && (tool.t_start != null ? tool.t_start : (tool.t != null ? tool.t : tool.ts)));
}

function clusterToolWaves(tools) {
  const groups = [];
  for (const tool of tools) {
    const t = toolStartT(tool);
    const prev = groups[groups.length - 1];
    const prevT = prev ? toolStartT(prev[prev.length - 1]) : NaN;
    if (!prev || (Number.isFinite(t) && Number.isFinite(prevT) && Math.abs(t - prevT) > WAVE_GAP)) {
      groups.push([tool]);
    } else {
      prev.push(tool);
    }
  }
  return groups;
}

function explodeTurns(cards) {
  const out = [];
  for (const card of cards) {
    const tools = Array.isArray(card.tools) ? card.tools : [];
    const waves = clusterToolWaves(tools);
    if (waves.length <= 1) {
      out.push(card);
      continue;
    }
    waves.forEach((wave, i) => {
      const last = i === waves.length - 1;
      const t = toolStartT(wave[0]);
      out.push(Object.assign({}, card, {
        tools: wave,
        text: last ? card.text : "",
        t: Number.isFinite(t) ? t : card.t,
        ts: Number.isFinite(t) ? t : card.ts,
      }));
    });
  }
  return out;
}

function toolFold(tool, t0) {
  const state = tool.state ? " · " + esc(tool.state) : "";
  const when = formatTs(tool.t != null ? tool.t : tool.ts, t0);
  const name = esc(tool.tool || tool.to || "tool");
  const bits = [];
  if (tool.args) bits.push('<pre class="tool-io">params: ' + esc(tool.args) + "</pre>");
  const out = String(tool.text || "");
  if (out && out !== (tool.tool || tool.to)) bits.push('<pre class="tool-io">output: ' + esc(out) + "</pre>");
  return '<details class="tool-fold"><summary>' + name + state + (when ? " · " + esc(when) : "") + "</summary>" + bits.join("") + "</details>";
}

function textBlock(text) {
  const body = String(text || "");
  if (!body) return "";
  if (body.length <= TEXT_LIMIT) {
    return '<div class="text">' + esc(body) + "</div>";
  }
  return (
    '<details class="text-fold"><summary class="text">' +
    esc(body.slice(0, TEXT_LIMIT)) +
    '…</summary><div class="text">' +
    esc(body) +
    "</div></details>"
  );
}

function toolsBlock(tools, t0) {
  if (!tools.length) return "";
  const folds = tools.map((tool) => toolFold(tool, t0));
  const shown = folds.slice(0, TOOL_CAP).join("");
  const hidden = folds.slice(TOOL_CAP);
  const extra = hidden.length
    ? '<details class="tools-more"><summary>' + hidden.length + " more</summary>" + hidden.join("") + "</details>"
    : "";
  return '<div class="tools">' + shown + extra + "</div>";
}

function cardBody(c, t0) {
  const tools = Array.isArray(c.tools) ? c.tools : (isToolCard(c) ? [c] : []);
  return textBlock(c.text) + toolsBlock(tools, t0);
}

function bubbleInner(c, t0) {
  const color = agentColor(isUserCard(c) ? "user" : (c.from || c.role));
  const who = '<span class="who" style="color:' + color + '">' + esc(c.from || c.role) + "</span>"
    + (c.to ? " → " + esc(c.to) : "");
  const ts = formatTs(c.t != null ? c.t : c.ts, t0);
  const meta = ts ? who + " · " + esc(ts) : who;
  return '<div class="meta">' + meta + "</div>" + cardBody(c, t0);
}

function cardKey(c) {
  const tools = (c && c.tools) || [];
  const ids = tools.map((tool) => tool.call_id || tool.tool || "").join(",");
  return [c.role, c.from, c.t, ids, (c.text || "").slice(0, 24)].join("|");
}

function drawChat(el, data) {
  const cards = explodeTurns((data.cards || []).filter((c) => !isToolCard(c)));
  const pinUser = data.pinUser === true;
  const t0 = data.t0;
  el.select(".idle").remove();
  if (!cards.length) {
    el.html('<div class="idle">No messages</div>');
    return;
  }
  el.selectAll(":scope > div.bubble").data(cards, cardKey).join("div")
    .attr("class", (c) => {
      let cls = "bubble host";
      if (isUserCard(c)) cls += " user" + (pinUser ? " pin" : "");
      if (c.role === "action") cls += " action " + (c.ok ? "ok" : "bad");
      const depth = cardDepth(c);
      if (depth) cls += " depth-" + depth;
      return cls;
    })
    .style("margin-left", (c) => (cardDepth(c) * 20) + "px")
    .style("border-left-color", (c) => agentColor(isUserCard(c) ? "user" : (c.from || c.role)))
    .html((c) => {
      try {
        return bubbleInner(c, t0);
      } catch (err) {
        return '<div class="meta">' + esc((c && (c.from || c.role)) || "?") + "</div>";
      }
    })
    .style("background", (c) => "color-mix(in srgb, " + agentColor(isUserCard(c) ? "user" : (c.from || c.role)) + " 16%, #111827)");
  const node = el.node();
  if (node) node.scrollTop = node.scrollHeight;
}

function depthsFromGraph(nodes, edges) {
  const children = {};
  (edges || []).forEach((e) => {
    const from = String(e.from || "");
    const to = String(e.to || "");
    if (!from || !to) return;
    (children[from] = children[from] || []).push(to);
  });
  const depth = {};
  const roots = (nodes || []).filter((n) => n.role === "user").map((n) => n.id);
  if (!roots.length && nodes && nodes.length) roots.push(nodes[0].id);
  const q = roots.slice();
  roots.forEach((id) => {
    const hinted = Number((nodes.find((n) => n.id === id) || {}).depth);
    depth[id] = Number.isFinite(hinted) ? hinted : 0;
  });
  while (q.length) {
    const id = q.shift();
    (children[id] || []).forEach((child) => {
      if (depth[child] == null) {
        depth[child] = depth[id] + 1;
        q.push(child);
      }
    });
  }
  (nodes || []).forEach((n) => {
    if (depth[n.id] == null) {
      const hinted = Number(n.depth);
      depth[n.id] = Number.isFinite(hinted) ? hinted : 1;
    }
  });
  return depth;
}

function lastTick(activity) {
  const rows = activity || [];
  return rows.length ? rows[rows.length - 1] : null;
}

function lastMatching(activity, pred) {
  const rows = activity || [];
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    if (pred(rows[i])) return rows[i];
  }
  return null;
}

function tickRunning(a) {
  if (!a) return false;
  if (a.state === "done") return false;
  if (a.state === "running") return true;
  return String(a.kind || "").endsWith("start");
}

const PACKET_MS = 900;
const HOT_MS = 1100;
const PACKETS = [];
const PACKET_SEEN = new Set();
const HOT = new Map();

function markHot(id, until) {
  const key = String(id || "");
  if (!key) return;
  const prev = HOT.get(key) || 0;
  if (until > prev) HOT.set(key, until);
}

function stillHot(id, now) {
  const until = HOT.get(String(id || "")) || 0;
  return until > now;
}

function nodeRunning(node, activity) {
  if (STATE.data && STATE.data.idle) return false;
  const now = Date.now();
  if (stillHot(node.id, now)) return true;
  return PACKETS.some((p) => p.from === node.id || p.to === node.id);
}

function edgeRunning(edge, activity) {
  if (STATE.data && STATE.data.idle) return false;
  const now = Date.now();
  if (stillHot(edge.from, now) && stillHot(edge.to, now)) return true;
  return PACKETS.some((p) => p.from === edge.from && p.to === edge.to);
}

function rememberPackets(activity) {
  const now = Date.now();
  (activity || []).forEach((a) => {
    if (!a || !a.from || !a.to) return;
    const key = String(a.t || "") + ":" + a.from + ">" + a.to + ":" + (a.kind || "") + ":" + (a.state || "");
    if (PACKET_SEEN.has(key)) return;
    PACKET_SEEN.add(key);
    markHot(a.from, now + HOT_MS);
    markHot(a.to, now + HOT_MS);
    if (a.from === a.to) return;
    PACKETS.push({
      key,
      from: a.from,
      to: a.to,
      born: now,
      back: packetIsReturn(a),
      color: agentColor(a.from),
    });
  });
  for (let i = PACKETS.length - 1; i >= 0; i -= 1) {
    if (now - PACKETS[i].born > PACKET_MS) PACKETS.splice(i, 1);
  }
  HOT.forEach((until, id) => {
    if (until <= now) HOT.delete(id);
  });
}

function edgeEnds(from, to, r) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const rad = r == null ? 22 : r;
  return {
    x1: from.x + (dx / len) * rad,
    y1: from.y + (dy / len) * rad,
    x2: to.x - (dx / len) * rad,
    y2: to.y - (dy / len) * rad,
  };
}

function packetIsReturn(a) {
  const kind = String((a && a.kind) || "");
  if (kind === "user_response" || kind === "client_response") return false;
  return kind.endsWith("end") || (a && a.state === "done");
}

function packetPos(a, b, u, back) {
  const t = back ? 1 - u : u;
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, ang: Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI };
}

function kickPacketLoop() {
  if (window.__masPacketTimer) return;
  const tick = () => {
    window.__masPacketTimer = 0;
    const now = Date.now();
    const live = PACKETS.some((p) => now - p.born < PACKET_MS) || [...HOT.values()].some((until) => until > now);
    if (!live) {
      if (window.__masNeedIdlePaint) {
        window.__masNeedIdlePaint = false;
        if (typeof window.masUpdateChart === "function") window.masUpdateChart(STATE);
      }
      return;
    }
    window.__masNeedIdlePaint = true;
    if (typeof window.masUpdateChart === "function") window.masUpdateChart(STATE);
    window.__masPacketTimer = requestAnimationFrame(tick);
  };
  window.__masPacketTimer = requestAnimationFrame(tick);
}

function markerIds() {
  if (!STATE._arrId) STATE._arrId = "arr-" + Math.random().toString(36).slice(2, 8);
  return { cold: STATE._arrId, hot: STATE._arrId + "-hot" };
}

function ensureMarkers(svg) {
  const ids = markerIds();
  let defs = svg.select("defs");
  if (defs.empty()) defs = svg.append("defs");
  if (defs.select("#" + ids.cold).empty()) {
    defs.append("marker")
      .attr("id", ids.cold)
      .attr("viewBox", "0 0 10 8")
      .attr("refX", 9)
      .attr("refY", 4)
      .attr("markerWidth", 8)
      .attr("markerHeight", 7)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,0 L10,4 L0,8 z")
      .attr("fill", "#64748b");
  }
  if (defs.select("#" + ids.hot).empty()) {
    defs.append("marker")
      .attr("id", ids.hot)
      .attr("viewBox", "0 0 10 8")
      .attr("refX", 9)
      .attr("refY", 4)
      .attr("markerWidth", 8)
      .attr("markerHeight", 7)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,0 L10,4 L0,8 z")
      .attr("fill", "#e2e8f0");
  }
}

function drawTopology(el, data) {
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const activity = data.activity || [];
  if (data.idle) {
    PACKETS.length = 0;
    HOT.clear();
  } else {
    rememberPackets(activity);
  }
  el.select(".idle").remove();
  if (!nodes.length) {
    PACKETS.length = 0;
    PACKET_SEEN.clear();
    HOT.clear();
    el.html('<div class="idle">Waiting for topology…</div>');
    return;
  }
  const depthOf = depthsFromGraph(nodes, edges);
  const cols = {};
  nodes.forEach((n) => {
    const d = depthOf[n.id] || 0;
    (cols[d] = cols[d] || []).push(n);
  });
  const depths = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const maxCol = depths.reduce((m, d) => Math.max(m, cols[d].length), 1);
  const W = Math.max(560, 80 + depths.length * 150);
  const H = Math.max(280, 56 + maxCol * 72);
  const pos = {};
  depths.forEach((d) => {
    const col = cols[d];
    const gap = H / (col.length + 1);
    col.forEach((n, j) => {
      pos[n.id] = { x: 56 + d * 150, y: gap * (j + 1) };
    });
  });
  let svg = el.select("svg.topo");
  if (svg.empty()) {
    svg = el.append("svg")
      .attr("class", "topo")
      .attr("width", "100%")
      .attr("height", "100%");
  }
  svg.attr("viewBox", "0 0 " + W + " " + H);
  ensureMarkers(svg);
  const now = Date.now();
  svg.selectAll("line.edge").data(edges.filter((e) => pos[e.from] && pos[e.to])).join("line")
    .attr("class", (e) => "edge" + (edgeRunning(e, activity) ? " running" : ""))
    .attr("x1", (e) => edgeEnds(pos[e.from], pos[e.to]).x1)
    .attr("y1", (e) => edgeEnds(pos[e.from], pos[e.to]).y1)
    .attr("x2", (e) => edgeEnds(pos[e.from], pos[e.to]).x2)
    .attr("y2", (e) => edgeEnds(pos[e.from], pos[e.to]).y2)
    .attr("stroke", (e) => (edgeRunning(e, activity) ? agentColor(e.to) : "#475569"))
    .attr("stroke-width", (e) => (edgeRunning(e, activity) ? 2.6 : 1.5))
    .attr("marker-end", (e) => {
      const ids = markerIds();
      return edgeRunning(e, activity) ? "url(#" + ids.hot + ")" : "url(#" + ids.cold + ")";
    });
  const g = svg.selectAll("g.node").data(nodes, (d) => d.id).join(
    (enter) => {
      const node = enter.append("g").attr("class", "node");
      node.append("circle").attr("r", 18);
      node.append("text")
        .attr("class", "label")
        .attr("text-anchor", "middle")
        .attr("dy", 34)
        .attr("fill", "#e2e8f0")
        .attr("font-size", 12);
      return node;
    }
  );
  g.attr("class", (d) => "node" + (nodeRunning(d, activity) ? " running" : ""))
    .attr("transform", (d) => {
      const p = pos[d.id] || { x: 40, y: 40 };
      return "translate(" + p.x + "," + p.y + ")";
    });
  g.select("circle")
    .attr("fill", (d) => agentColor(d.id))
    .attr("stroke", (d) => agentColor(d.id))
    .attr("stroke-width", (d) => (nodeRunning(d, activity) ? 3 : 2))
    .attr("fill-opacity", (d) => (nodeRunning(d, activity) ? 0.45 : 0.28));
  g.select("text.label").text((d) => String(d.id || ""));
  const flying = PACKETS.filter((p) => pos[p.from] && pos[p.to] && now - p.born < PACKET_MS);
  svg.selectAll("ellipse.packet").data(flying, (d) => d.key).join("ellipse")
    .attr("class", (d) => "packet" + (d.back ? " back" : ""))
    .attr("rx", 11)
    .attr("ry", 3.5)
    .attr("fill", (d) => d.color)
    .attr("transform", (d) => {
      const a = pos[d.from];
      const b = pos[d.to];
      const u = Math.min(1, Math.max(0, (now - d.born) / PACKET_MS));
      const p = packetPos(a, b, u, d.back);
      return "translate(" + p.x + "," + p.y + ") rotate(" + p.ang + ")";
    });
  kickPacketLoop();
}

window.masDraw = function(el, data) {
  if (STATE.kind === "topology") drawTopology(el, data);
  else drawChat(el, data);
};
