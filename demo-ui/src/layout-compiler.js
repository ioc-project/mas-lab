import { getPlugin } from "./plugin-registry.js";
import { mergeRef } from "./ref.js";

export const LANE_HEADER_PX = 20;

function sizeToGl(size) {
  if (!size) return undefined;
  const text = String(size).trim();
  if (!text) return undefined;
  if (text.endsWith("%")) return text;
  if (text.endsWith("fr")) {
    const n = Number(text.slice(0, -2));
    // Golden Layout only allocates % then leftover fr. Keep the unit.
    if (!Number.isFinite(n) || n <= 0) return undefined;
    return `${n}fr`;
  }
  if (text.endsWith("px")) {
    const px = Number(text.slice(0, -2));
    if (!Number.isFinite(px) || px <= 0) return undefined;
    // Row/column calc asserts unless unit is % or fr (ROCCAS6692).
    return `${Math.max(8, Math.round((px / 1280) * 100))}%`;
  }
  const n = Number(text);
  if (Number.isFinite(n) && n > 0) return `${n}%`;
  return undefined;
}

export function withSize(item, size) {
  if (!item) return item;
  const gl = sizeToGl(size);
  if (gl) item.size = gl;
  return item;
}

function isWindowed(node) {
  if (node?.windowed === false || node?.props?.windowed === false) return false;
  const def = getPlugin(node?.plugin);
  if (def && def.windowed === false) return false;
  return true;
}

function panelItem(node, inherited) {
  const ref = mergeRef(inherited, node.dataSource);
  const windowed = isWindowed(node);
  const props = { ...(node.props || {}) };
  for (const key of [
    "vertical",
    "xrange",
    "yrange",
    "xlabel",
    "ylabel",
    "labels",
    "layout",
    "preset",
    "settings",
    "pinUser",
    "compact",
    "dirac",
    "threshold",
    "view",
    "charts",
    "metrics",
  ]) {
    if (node[key] != null && props[key] == null) props[key] = node[key];
  }
  return withSize(
    {
      type: "component",
      title: node.title || node.plugin || "panel",
      componentType: node.plugin,
      componentState: {
        plugin: node.plugin,
        title: node.title || node.plugin || "panel",
        dataSource: ref,
        props,
        windowed,
      },
    },
    node.size,
  );
}

function wrapStack(item) {
  if (!item) return item;
  if (item.type === "component") {
    const windowed = item.componentState?.windowed !== false;
    if (!windowed) {
      return {
        ...item,
        hasHeaders: false,
        header: { show: false, popout: false, maximise: false, close: false },
      };
    }
    return {
      type: "stack",
      content: [item],
      width: item.width,
      size: item.size,
      hasHeaders: true,
      header: { show: "top", popout: false, maximise: true, close: false },
    };
  }
  if (item.content) {
    return { ...item, content: item.content.map(wrapStack) };
  }
  return item;
}

function flattenColumns(node) {
  if (!node) return node;
  if (node.content) {
    node = { ...node, content: node.content.map(flattenColumns) };
  }
  while (
    node.type === "column" &&
    Array.isArray(node.content) &&
    node.content.length === 1 &&
    node.content[0]?.type === "column"
  ) {
    const inner = node.content[0];
    node = { ...inner, width: node.width ?? inner.width, size: node.size ?? inner.size };
  }
  return node;
}

export function collectLaneTitles(node) {
  if (!node) return [];
  if (node.type === "hstack") {
    const kids = node.children || [];
    if (kids.some((child) => child.title && !child.plugin)) {
      return kids.map((child) => (child.title ? String(child.title) : ""));
    }
  }
  for (const child of node.children || []) {
    const found = collectLaneTitles(child);
    if (found.length) return found;
  }
  return [];
}

export function compileNode(node, inherited = {}) {
  if (!node) return null;
  const ref = mergeRef(inherited, node.dataSource);
  const children = (node.children || []).map((child) => compileNode(child, ref)).filter(Boolean);
  if (children.length) {
    if (node.type === "panel" && children.length === 1 && !node.plugin) {
      const inner = children[0];
      return inner ? withSize({ ...inner }, node.size || inner.size) : inner;
    }
    if (node.type === "tabs") {
      return withSize(
        {
          type: "stack",
          content: children,
          hasHeaders: true,
          header: { show: "top", popout: false, maximise: true, close: false },
        },
        node.size,
      );
    }
    if (node.type === "hstack") {
      return withSize({ type: "row", content: children }, node.size);
    }
    if (node.type === "grid") {
      const cols = Number(node.cols || children.length || 1);
      const rows = [];
      for (let i = 0; i < children.length; i += cols) {
        rows.push({ type: "row", content: children.slice(i, i + cols) });
      }
      return { type: "column", content: rows };
    }
    return withSize({ type: "column", content: children }, node.size);
  }
  if (node.type === "panel" || node.plugin) {
    return panelItem(node, inherited);
  }
  return { type: "column", content: [] };
}

export function compileLayout(doc) {
  const rootRef = doc.dataSource || {};
  const root = wrapStack(flattenColumns(compileNode(doc.layout, rootRef)));
  return {
    root: root || { type: "column", content: [] },
    openPopouts: [],
    settings: {
      constrainDragToContainer: true,
      hasHeaders: true,
      showPopoutIcon: false,
      showCloseIcon: false,
      showMaximiseIcon: true,
    },
    dimensions: {
      headerHeight: 24,
      borderWidth: 1,
    },
  };
}

function isLaneHost(item) {
  return Boolean(item && (item.isColumn || item.isStack || item.isComponent || item.isRow));
}

function findLaneColumns(item, n) {
  if (!item || !n) return [];
  const kids = [...(item.contentItems || [])];
  if (item.isGround && kids.length === 1) return findLaneColumns(kids[0], n);
  if (item.isRow && kids.length === n) return kids;
  if (kids.length === n && kids.every(isLaneHost)) return kids;
  for (const child of kids) {
    const found = findLaneColumns(child, n);
    if (found.length === n) return found;
  }
  return [];
}

export function attachLaneHeaders(gl, titles) {
  if (!titles?.length) return;
  const columns = findLaneColumns(gl.rootItem, titles.length);
  if (columns.length !== titles.length) return;
  columns.forEach((col, i) => {
    const text = titles[i];
    if (!text || !col?.element) return;
    const host = col.element;
    host.classList.add("has-lane-header");
    let bar = host.querySelector(":scope > .lane-header");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "lane-header";
      host.insertBefore(bar, host.firstChild);
    }
    bar.textContent = text;
  });
}

export function pinLaneHeaders(rootEl) {
  const headerPx = LANE_HEADER_PX;
  rootEl.querySelectorAll(".has-lane-header").forEach((host) => {
    let first = true;
    for (const child of host.children) {
      if (child.classList.contains("lane-header")) continue;
      child.style.marginTop = first && !child.classList.contains("lm_splitter") ? `${headerPx}px` : "";
      if (!child.classList.contains("lm_splitter")) first = false;
    }
  });
}
