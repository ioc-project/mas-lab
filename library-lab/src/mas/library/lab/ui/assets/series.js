// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0

function pointY(p) {
  if (p && typeof p === "object") return Number(p.y ?? p.value);
  return Number(p);
}

function parseTime(v) {
  if (v == null || v === "") return NaN;
  if (typeof v === "number" && Number.isFinite(v)) return v > 1e12 ? v / 1000 : v;
  const n = Number(v);
  if (Number.isFinite(n)) return n > 1e12 ? n / 1000 : n;
  const ms = Date.parse(String(v).replace("Z", "+00:00"));
  return Number.isFinite(ms) ? ms / 1000 : NaN;
}

function pointT(p) {
  if (p && typeof p === "object") {
    const t = parseTime(p.t ?? p.ts ?? p.time);
    if (Number.isFinite(t)) return t;
  }
  return NaN;
}

function fmtAxisTime(t, t0) {
  if (!Number.isFinite(t)) return "";
  const dt = t - t0;
  if (!Number.isFinite(dt) || dt < 0) return "0s";
  if (dt >= 60) {
    const m = Math.floor(dt / 60);
    const s = Math.floor(dt % 60);
    return m + ":" + String(s).padStart(2, "0");
  }
  return dt.toFixed(1) + "s";
}

function diracColor(d) {
  if (d && d.ground_truth) return "#64748b";
  const kind = String((d && d.kind) || "").toLowerCase();
  if (kind === "recovery") return "#22c55e";
  if (kind === "failure") return "#ef4444";
  const y = Number(d && d.y);
  const thr = Number(d && d.threshold);
  if (Number.isFinite(y) && Number.isFinite(thr)) return y >= thr ? "#ef4444" : "#22c55e";
  return "#ef4444";
}

function recordingT0(data, tMin) {
  const hinted = Number(data && data.t0);
  if (Number.isFinite(hinted) && hinted > 0) return hinted;
  return tMin;
}

function timedPoints(ys) {
  if (!Array.isArray(ys) || !ys.length) return [];
  return ys.map((p) => {
    const y = pointY(p);
    const t = pointT(p);
    if (!Number.isFinite(y) || !Number.isFinite(t)) return null;
    return { y, t };
  }).filter(Boolean);
}

function seriesNamed(data) {
  const named = {};
  const series = data.series;
  if (series && typeof series === "object" && !Array.isArray(series)) {
    Object.entries(series).forEach(([label, ys]) => {
      const pts = timedPoints(ys);
      if (pts.length) named[label] = pts;
    });
  }
  if (data.shape === "dirac") return named;
  const points = timedPoints(data.points || data.values || []);
  if (points.length) {
    named[data.instance || data.kind || "series"] = points;
  }
  return named;
}

function yBounds(data) {
  const raw = data.yrange || STATE.yrange || [0, 1];
  let lo = Number(raw[0]);
  let hi = Number(raw[1]);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) {
    lo = 0;
    hi = 1;
  }
  return [lo, hi];
}

function prettyLabel(key, data) {
  const map = data.labels || STATE.labels || (CHART.labels) || {};
  if (map[key]) return map[key];
  return String(key).replace(/[-_.]/g, " ");
}

const SERIES_PALETTE = [
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
const SERIES_COLOR_OF = {};

function seriesColor(id) {
  const s = String(id || "series");
  if (s === "user") return "#facc15";
  if (SERIES_COLOR_OF[s]) return SERIES_COLOR_OF[s];
  const used = {};
  Object.keys(SERIES_COLOR_OF).forEach((name) => { used[SERIES_COLOR_OF[name]] = true; });
  let color = SERIES_PALETTE.find((c) => !used[c]);
  if (!color) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i += 1) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    color = SERIES_PALETTE[(h >>> 0) % SERIES_PALETTE.length];
  }
  SERIES_COLOR_OF[s] = color;
  return color;
}

function seedOriginSeries(data) {
  const named = seriesNamed(data);
  if (Object.keys(named).length) return data;
  const origin = parseTime(data && (data.t0 ?? data.t));
  if (!Number.isFinite(origin)) return data;
  return Object.assign({}, data, { series: { series: [{ t: origin, y: 0 }] } });
}

function drawOne(el, data) {
  data = seedOriginSeries(data);
  const named = seriesNamed(data);
  const labels = Object.keys(named);
  if (!labels.length) {
    el.select("svg").remove();
    el.html('<div class="idle">Waiting for series…</div>');
    return;
  }
  const W = 720, H = 280, padL = 48, padR = 16, padT = 28, padB = 36;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const [ymin, ymax] = yBounds(data);
  const span = ymax - ymin || 1;
  el.select(".idle").remove();
  let tMin = Infinity, tMax = -Infinity;
  labels.forEach((label) => {
    named[label].forEach((p) => {
      const t = pointT(p);
      if (!Number.isFinite(t)) return;
      if (t < tMin) tMin = t;
      if (t > tMax) tMax = t;
    });
  });
  (data.diracs || []).forEach((d) => {
    const t = parseTime(d && (d.t ?? d.ts));
    if (!Number.isFinite(t)) return;
    if (t < tMin) tMin = t;
    if (t > tMax) tMax = t;
  });
  if (!Number.isFinite(tMin)) {
    el.select("svg").remove();
    el.html('<div class="idle">Waiting for series…</div>');
    return;
  }
  const t0 = recordingT0(data, tMin);
  const nowHint = parseTime(data.now ?? data.t);
  const t1Hint = parseTime(data.t1);
  const sampleMax = tMax;
  if (Number.isFinite(t1Hint) && t1Hint > tMax) tMax = t1Hint;
  else if (Number.isFinite(nowHint) && nowHint > tMax) tMax = nowHint;
  if (tMax <= t0) tMax = t0 + 1;
  const playhead = Number.isFinite(nowHint) ? Math.min(nowHint, tMax) : sampleMax;
  const tSpan = tMax - t0 || 1;
  let svg = el.select("svg.series");
  if (svg.empty()) {
    svg = el.append("svg").attr("class", "series").attr("viewBox", "0 0 " + W + " " + H)
      .attr("width", "100%").attr("height", "100%");
    svg.append("line").attr("class", "axis-x").attr("x1", padL).attr("y1", H - padB)
      .attr("x2", W - padR).attr("y2", H - padB).attr("stroke", "#334155");
    svg.append("line").attr("class", "axis-y").attr("x1", padL).attr("y1", padT)
      .attr("x2", padL).attr("y2", H - padB).attr("stroke", "#334155");
    svg.append("text").attr("class", "xlabel").attr("x", padL + innerW / 2).attr("y", H - 4)
      .attr("text-anchor", "middle").attr("fill", "#94a3b8").attr("font-size", 11)
      .text(data.xlabel || STATE.xlabel || "time");
    svg.append("text").attr("class", "ylabel").attr("x", 12).attr("y", padT - 10)
      .attr("fill", "#94a3b8").attr("font-size", 11)
      .text(data.title || data.ylabel || STATE.ylabel || "score");
  } else {
    svg.select("text.xlabel").text(data.xlabel || STATE.xlabel || "time");
    svg.select("text.ylabel").text(data.title || data.ylabel || STATE.ylabel || "score");
  }
  const xticks = [0, 0.25, 0.5, 0.75, 1].map((f) => t0 + tSpan * f);
  svg.selectAll("text.xtick").data(xticks).join("text")
    .attr("class", "xtick")
    .attr("x", (d) => padL + innerW * ((d - t0) / tSpan))
    .attr("y", H - padB + 14)
    .attr("text-anchor", "middle")
    .attr("fill", "#64748b")
    .attr("font-size", 10)
    .text((d) => fmtAxisTime(d, t0));
  const yticks = [ymin, ymin + span / 2, ymax];
  svg.selectAll("text.ytick").data(yticks).join("text")
    .attr("class", "ytick")
    .attr("x", padL - 6)
    .attr("y", (d) => (H - padB) - innerH * ((d - ymin) / span) + 3)
    .attr("text-anchor", "end")
    .attr("fill", "#64748b")
    .attr("font-size", 10)
    .text((d) => Number(d).toFixed(1));
  const rows = labels.map((label) => {
    const samples = named[label].slice();
    const last = samples[samples.length - 1];
    const visible = samples.filter((p) => pointT(p) <= playhead + 1e-6);
    const drawn = visible.length ? visible : samples.slice(0, 0);
    const coords = drawn.map((p) => {
      const t = pointT(p);
      const y = pointY(p);
      return {
        x: padL + innerW * ((t - t0) / tSpan),
        y: (H - padB) - innerH * ((y - ymin) / span),
        t,
      };
    });
    const bits = [];
    coords.forEach((p, j) => {
      if (j === 0) {
        bits.push(p.x.toFixed(1) + "," + p.y.toFixed(1));
        return;
      }
      const prev = coords[j - 1];
      bits.push(p.x.toFixed(1) + "," + prev.y.toFixed(1));
      bits.push(p.x.toFixed(1) + "," + p.y.toFixed(1));
    });
    let holdPts = "";
    const holdDots = [];
    const lastDrawn = drawn[drawn.length - 1] || last;
    if (lastDrawn && coords.length && pointT(lastDrawn) < playhead) {
      const lastC = coords[coords.length - 1];
      const hx = padL + innerW * ((playhead - t0) / tSpan);
      holdPts = lastC.x.toFixed(1) + "," + lastC.y.toFixed(1) + " " + hx.toFixed(1) + "," + lastC.y.toFixed(1);
      const lastT = pointT(lastDrawn);
      if (playhead - lastT < 3600) {
        const HOLD_STEP = 0.1;
        const maxDots = 400;
        let step = HOLD_STEP;
        if ((playhead - lastT) / step > maxDots) step = (playhead - lastT) / maxDots;
        for (let t = lastT + step; t < playhead; t += step) {
          holdDots.push({
            x: padL + innerW * ((t - t0) / tSpan),
            y: lastC.y,
            t,
          });
        }
      }
      holdDots.push({ x: hx, y: lastC.y, t: playhead, tip: true });
    }
    return { label, title: prettyLabel(label, data), pts: bits.join(" "), holdPts, dots: coords, holdDots, color: seriesColor(label) };
  });
  svg.selectAll("polyline.series-line").data(rows, (d) => d.label).join("polyline")
    .attr("class", "series-line")
    .attr("fill", "none")
    .attr("stroke", (d) => d.color)
    .attr("stroke-width", 1.5)
    .attr("points", (d) => d.pts);
  svg.selectAll("polyline.series-hold").data(rows, (d) => d.label).join("polyline")
    .attr("class", "series-hold")
    .attr("fill", "none")
    .attr("stroke", (d) => d.color)
    .attr("stroke-width", 1.5)
    .attr("stroke-dasharray", "4 3")
    .attr("stroke-opacity", (d) => d.holdPts ? 0.7 : 0)
    .attr("points", (d) => d.holdPts || null);
  const dots = [];
  rows.forEach((row) => {
    row.dots.forEach((p, i) => {
      dots.push({ key: row.label + ":" + p.t + ":" + i, x: p.x, y: p.y, color: row.color });
    });
  });
  svg.selectAll("circle.series-dot").data(dots, (d) => d.key).join("circle")
    .attr("class", "series-dot")
    .attr("r", 3)
    .attr("cx", (d) => d.x)
    .attr("cy", (d) => d.y)
    .attr("fill", (d) => d.color)
    .attr("stroke", "#0f172a")
    .attr("stroke-width", 1);
  const ticks = [];
  rows.forEach((row) => {
    (row.holdDots || []).forEach((p, i) => {
      ticks.push({ key: row.label + ":hold:" + p.t + ":" + i, x: p.x, y: p.y, color: row.color, tip: p.tip });
    });
  });
  svg.selectAll("circle.series-hold-dot").data(ticks, (d) => d.key).join("circle")
    .attr("class", "series-hold-dot")
    .attr("r", (d) => d.tip ? 3.5 : 2)
    .attr("cx", (d) => d.x)
    .attr("cy", (d) => d.y)
    .attr("fill", (d) => d.tip ? d.color : "none")
    .attr("stroke", (d) => d.color)
    .attr("stroke-width", 1.2)
    .attr("fill-opacity", (d) => d.tip ? 1 : 0.35);
  svg.selectAll("text.series-label").data(rows, (d) => d.label).join("text")
    .attr("class", "series-label")
    .attr("x", W - padR - 4)
    .attr("y", (d, i) => padT + 12 * (i + 1))
    .attr("font-size", 11)
    .attr("text-anchor", "end")
    .attr("fill", (d) => d.color)
    .text((d) => d.title);
  const thr = Number(data.threshold != null ? data.threshold : STATE.threshold);
  if (Number.isFinite(thr)) {
    const py = (H - padB) - innerH * ((thr - ymin) / span);
    svg.selectAll("line.threshold").data([thr]).join("line")
      .attr("class", "threshold")
      .attr("x1", padL).attr("x2", W - padR)
      .attr("y1", py).attr("y2", py)
      .attr("stroke", "#94a3b8")
      .attr("stroke-dasharray", "4 4")
      .attr("stroke-width", 1);
  }
  const marks = (data.diracs || []).filter((d) => {
    const t = parseTime(d && (d.t ?? d.ts));
    return d && Number.isFinite(t) && t <= playhead + 1e-6;
  });
  const beads = svg.selectAll("g.dirac").data(marks, (d) => d.id || (d.t + ":" + d.kind)).join(
    (enter) => {
      const g = enter.append("g").attr("class", "dirac");
      g.append("line").attr("class", "dirac-stem");
      g.append("circle").attr("r", 4);
      g.append("title");
      return g;
    }
  );
  beads.attr("transform", (d) => {
    const x = padL + innerW * ((parseTime(d.t ?? d.ts) - t0) / tSpan);
    return "translate(" + x + ",0)";
  });
  beads.select("line.dirac-stem")
    .attr("y1", H - padB)
    .attr("y2", (d) => (H - padB) - innerH * ((Number(d.y ?? thr) - ymin) / span))
    .attr("stroke", (d) => diracColor(d))
    .attr("stroke-opacity", (d) => d.ground_truth ? 0.35 : 0.95)
    .attr("stroke-width", 1.5);
  beads.select("circle")
    .attr("cy", (d) => (H - padB) - innerH * ((Number(d.y ?? thr) - ymin) / span))
    .attr("fill", (d) => diracColor(d))
    .attr("fill-opacity", (d) => d.ground_truth ? 0.35 : 1);
  beads.select("title").text((d) => d.label || ((d.challenge ? d.challenge + " " : "") + (d.kind || "event")));
}

function drawSeries(el, data) {
  const charts = Array.isArray(data.charts) && data.charts.length ? data.charts : null;
  if (charts && charts.length > 1) {
    el.select("svg.series").remove();
    el.select(".idle").remove();
    let root = el.select("div.series-stack");
    if (root.empty()) {
      root = el.append("div").attr("class", "series-stack")
        .style("height", "100%").style("overflow", "auto");
    }
    const clock = { now: data.now, t: data.t, t0: data.t0, t1: data.t1, xlabel: data.xlabel };
    const items = root.selectAll("div.series-facet").data(charts, (d, i) => d.stream || d.title || String(i));
    items.exit().remove();
    items.enter().append("div")
      .attr("class", "series-facet")
      .style("min-height", "132px")
      .style("height", (100 / charts.length) + "%");
    root.selectAll("div.series-facet").each(function (chart) {
      const payload = Object.assign({}, clock, chart, { kind: "series" });
      drawOne(d3.select(this), payload);
    });
    return;
  }
  el.select("div.series-stack").remove();
  drawOne(el, data);
}

window.masDraw = function(el, data) {
  drawSeries(el, data);
};

