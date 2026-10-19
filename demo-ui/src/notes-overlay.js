import { holdOrQueue, normalizeNotes, pickNote } from "./narrative.js";

const LOOK_AT = {
  chat: "the discussion",
  topology: "the topology",
  plot: "the plot",
};

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function targetNode(at, scenario) {
  if (!at) return null;
  const lane = String(scenario || "").toLowerCase();
  const selectors = [`[data-plugin="${at}"]`, `[data-kind="${at}"]`];
  if (lane) {
    for (const sel of selectors) {
      const scoped = document.querySelector(`${sel}[data-scenario="${lane}"]`);
      if (scoped) return scoped;
    }
  }
  for (const sel of selectors) {
    const node = document.querySelector(sel);
    if (node) return node;
  }
  return null;
}

function laneBox(target, scenario) {
  const lane = target?.closest?.(".has-lane-header");
  if (lane) return lane.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const leftLane = String(scenario || "").toLowerCase() === "baseline";
  return {
    left: leftLane ? 0 : vw / 2,
    right: leftLane ? vw / 2 : vw,
    top: 56,
    bottom: vh,
    width: vw / 2,
    height: vh - 56,
  };
}

function place(card, target, scenario) {
  const pad = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const cr = card.getBoundingClientRect();
  const box = laneBox(target, scenario);
  const tr = target?.getBoundingClientRect();
  let left;
  let top = tr ? tr.top : box.top + pad;
  if (!tr) {
    left = String(scenario || "").toLowerCase() === "baseline" ? box.left + pad : box.right - cr.width - pad;
  } else {
    const spaceLeft = tr.left - box.left;
    const spaceRight = box.right - tr.right;
    if (spaceRight >= cr.width + pad && spaceRight >= spaceLeft) {
      left = tr.right + pad;
    } else if (spaceLeft >= cr.width + pad) {
      left = tr.left - cr.width - pad;
    } else {
      const targetMid = (tr.left + tr.right) / 2;
      const laneMid = (box.left + box.right) / 2;
      left = targetMid >= laneMid ? box.left + pad : box.right - cr.width - pad;
    }
  }
  left = Math.min(Math.max(left, box.left + pad), Math.max(box.left + pad, box.right - cr.width - pad));
  if (top + cr.height > box.bottom - pad) top = Math.max(box.top + pad, box.bottom - cr.height - pad);
  if (top < box.top + pad) top = box.top + pad;
  left = Math.min(Math.max(left, pad), vw - cr.width - pad);
  top = Math.min(Math.max(top, pad), vh - cr.height - pad);
  card.style.left = `${Math.round(left)}px`;
  card.style.top = `${Math.round(top)}px`;
}

export const pluginMeta = {
  id: "narrative-overlay",
  kind: "chrome",
  overlay: true,
};

export const narrativeOverlayPlugin = {
  id: "narrative-overlay",
  kind: "chrome",
  overlay: true,
  windowed: false,
  subscribes: ["native.*", "job.*", "view.reset", "metrics.snapshot"],
  mount(container, ctx) {
    const spec = normalizeNotes(ctx?.layout || { narrative: ctx?.config });
    if (!spec.overlay) {
      return { update() {}, destroy() {} };
    }
    const host = el("div", "narrative-layer");
    container.appendChild(host);
    const seen = new Set();
    const live = String(ctx?.bus?.chromeMode || "live") !== "replay";
    let hideTimer = 0;
    let shown = 0;
    let currentTarget = null;
    let jobArmed = !live;
    let visible = false;
    let pending = [];

    function clearFocus() {
      document.querySelectorAll(".narrative-focus").forEach((node) => node.classList.remove("narrative-focus"));
      currentTarget = null;
    }

    function hide() {
      window.clearTimeout(hideTimer);
      hideTimer = 0;
      host.replaceChildren();
      clearFocus();
      visible = false;
    }

    function show(note, envelope) {
      if (shown >= spec.maxNotes) return;
      if (spec.maxVisible <= 0) return;
      hide();
      seen.add(note.id);
      shown += 1;
      visible = true;
      const lane = envelope?.ref?.scenario || "";
      const card = el("aside", "narrative-card");
      card.setAttribute("role", "status");
      if (lane) card.dataset.scenario = lane;
      card.dataset.severity = note.severity || "info";
      const look = el("div", "narrative-look");
      look.textContent = note.at ? `Look at ${LOOK_AT[note.at] || note.at}` : "Look";
      const text = el("p", "narrative-text");
      text.textContent = note.text;
      card.append(look, text);
      host.appendChild(card);
      const target = targetNode(note.at, lane);
      if (target) {
        target.classList.add("narrative-focus");
        currentTarget = target;
      }
      place(card, target, lane);
      const hold = note.holdMs || spec.holdMs;
      hideTimer = window.setTimeout(() => {
        hide();
        if (!pending.length) return;
        const next = pending.shift();
        show(next.note, next.envelope);
      }, hold);
    }

    const onResize = () => {
      const card = host.querySelector(".narrative-card");
      if (card) place(card, currentTarget, card.dataset.scenario || "");
    };
    window.addEventListener("resize", onResize);

    if (!spec.notes.length) {
      return {
        update() {},
        destroy() {
          window.removeEventListener("resize", onResize);
          hide();
          host.remove();
        },
      };
    }

    return {
      update(envelope) {
        if (envelope?.topic === "view.reset") {
          seen.clear();
          shown = 0;
          pending = [];
          jobArmed = !live;
          hide();
          return;
        }
        if (envelope?.topic === "job.started") {
          if (!live || !envelope?.replay) jobArmed = true;
          return;
        }
        if (!jobArmed) return;
        if (shown >= spec.maxNotes) return;
        const note = pickNote(spec, envelope, seen, { live });
        if (!note) return;
        const next = holdOrQueue({ visible, pending }, { note, envelope }, { maxQueued: spec.maxQueued });
        pending = next.pending || [];
        if (next.show) show(next.show.note, next.show.envelope);
      },
      destroy() {
        window.removeEventListener("resize", onResize);
        hide();
        host.remove();
      },
    };
  },
};
