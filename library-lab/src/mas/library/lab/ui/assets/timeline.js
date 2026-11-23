// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0

function masDraw(host, payload) {
  const data = (payload && payload.data) || payload || {};
  const events = data.events || data.records || data.points || [];
  // The page passes a d3 selection; an embedding may pass the element itself.
  const el = host && typeof host.node === "function" ? host.node() : host;
  const root = (el && el.shadowRoot) || el;
  if (!root) return;
  root.innerHTML = "";
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  const parseWall = (ev, i) => {
    const t = (ev && ev.time) || {};
    if (t.wall) {
      const ms = Date.parse(t.wall);
      if (!Number.isNaN(ms)) return ms;
    }
    if (typeof t.elapsed_ms === "number") return t.elapsed_ms;
    return i;
  };
  const laneOf = (ev) => {
    const kind = String((ev && ev.kind) || "");
    const name = String((ev && ev.name) || "");
    if (kind === "fixture" || name.indexOf("fixture.") === 0) {
      const src = (ev && ev.source) || {};
      return "fixture:" + (src.id || src.fixture || "fixture");
    }
    if (kind === "action" || name.indexOf("action.") === 0) return "actions";
    if (kind === "clock" || name.indexOf("clock.") === 0) return "clock";
    if (kind === "trigger" || kind === "timer" || name.indexOf("trigger.") === 0 || name.indexOf("timer.") === 0) {
      return "triggers";
    }
    return "events";
  };
  const stamped = events.map((ev, i) => ({ t: parseWall(ev, i), ev: ev || {} }));
  const t0 = stamped.length ? Math.min.apply(null, stamped.map((s) => s.t)) : 0;
  const t1 = stamped.length ? Math.max.apply(null, stamped.map((s) => s.t)) : 1;
  const span = Math.max(1, t1 - t0);
  const lanes = [];
  stamped.forEach((s) => {
    const lane = laneOf(s.ev);
    if (lanes.indexOf(lane) < 0) lanes.push(lane);
  });
  if (!lanes.length) lanes.push("events");
  const width = 720;
  const pad = 24;
  const laneH = 28;
  const height = 40 + laneH * lanes.length + pad;
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("width", "100%");
  const colors = { fixture: "#38bdf8", trigger: "#fbbf24", action: "#34d399", timer: "#c4b5fd", clock: "#94a3b8", event: "#94a3b8" };
  lanes.forEach((lane, li) => {
    const y = 32 + li * laneH;
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", "8");
    label.setAttribute("y", String(y + 14));
    label.setAttribute("font-size", "10");
    label.setAttribute("fill", "#94a3b8");
    label.textContent = (lane.split(":")[1] || lane).slice(0, 16);
    svg.appendChild(label);
    const axis = document.createElementNS(svgNS, "line");
    axis.setAttribute("x1", String(pad + 80));
    axis.setAttribute("y1", String(y + 12));
    axis.setAttribute("x2", String(width - pad));
    axis.setAttribute("y2", String(y + 12));
    axis.setAttribute("stroke", "#334155");
    svg.appendChild(axis);
    const starts = {};
    stamped.filter((s) => laneOf(s.ev) === lane).forEach((s) => {
      const x = pad + 80 + ((width - pad * 2 - 80) * (s.t - t0)) / span;
      const name = String(s.ev.name || "");
      const kind = String(s.ev.kind || "event");
      const src = s.ev.source || {};
      const key = String(src.id || name);
      if (/\.(ready|started|armed)$/.test(name)) starts[key] = x;
      if (/\.(stopped|done|failed|timeout)$/.test(name)) {
        const x0 = starts[key] !== undefined ? starts[key] : x - 16;
        const rect = document.createElementNS(svgNS, "rect");
        rect.setAttribute("x", String(Math.min(x0, x)));
        rect.setAttribute("y", String(y + 4));
        rect.setAttribute("width", String(Math.max(8, Math.abs(x - x0))));
        rect.setAttribute("height", "16");
        const forced = s.ev.payload && s.ev.payload.forced;
        rect.setAttribute("fill", s.ev.outcome === "failed" || s.ev.outcome === "timeout" || forced ? "#ef4444" : (colors[kind] || "#94a3b8"));
        rect.setAttribute("opacity", "0.75");
        svg.appendChild(rect);
      } else if (kind === "trigger" || name.indexOf("trigger.") === 0) {
        const poly = document.createElementNS(svgNS, "polygon");
        poly.setAttribute("points", x + "," + y + " " + (x + 7) + "," + (y + 12) + " " + x + "," + (y + 24) + " " + (x - 7) + "," + (y + 12));
        if (name === "trigger.skipped") {
          // A spent trigger that matched again: outline only, so it does not read as a firing.
          poly.setAttribute("fill", "none");
          poly.setAttribute("stroke", colors.trigger);
        } else {
          poly.setAttribute("fill", colors.trigger);
        }
        svg.appendChild(poly);
        const occ = s.ev.payload && s.ev.payload.occurrence;
        const tag = (String(src.id || "") + (occ != null ? " #" + occ : "")).trim();
        if (tag) {
          const note = document.createElementNS(svgNS, "text");
          note.setAttribute("x", String(x + 8));
          note.setAttribute("y", String(y + 10));
          note.setAttribute("font-size", "9");
          note.setAttribute("fill", "#cbd5e1");
          note.textContent = tag.slice(0, 24);
          svg.appendChild(note);
        }
      } else {
        const mark = document.createElementNS(svgNS, "rect");
        mark.setAttribute("x", String(x - 4));
        mark.setAttribute("y", String(y + 6));
        mark.setAttribute("width", "8");
        mark.setAttribute("height", "12");
        mark.setAttribute("fill", colors[kind] || "#94a3b8");
        svg.appendChild(mark);
      }
    });
  });
  root.appendChild(svg);
}
