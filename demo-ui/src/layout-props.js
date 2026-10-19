/** Layout-level (general) vs plugin `props`. */

const CHROME_MODES = ["live", "replay", "passive"];

export function parseModes(value) {
  if (Array.isArray(value)) {
    const out = value.map((item) => String(item).toLowerCase()).filter((item) => CHROME_MODES.includes(item));
    return out.length ? [...new Set(out)] : ["live", "replay"];
  }
  const text = String(value ?? "both").toLowerCase().trim();
  if (text === "live") return ["live"];
  if (text === "replay") return ["replay"];
  if (text === "passive") return ["passive"];
  return ["live", "replay"];
}

export function initialChromeMode(modes, dataSourceMode, layoutMode) {
  const allowed = parseModes(modes);
  if (allowed.length === 1) return allowed[0];
  for (const raw of [layoutMode, dataSourceMode]) {
    const hint = String(raw || "").toLowerCase();
    if (!hint) continue;
    if (hint === "offline") return allowed.includes("replay") ? "replay" : allowed[0];
    if (allowed.includes(hint)) return hint;
  }
  return allowed[0] || "live";
}

export function chromeMode(bus, fallback = "passive") {
  return bus?.chromeMode || fallback;
}

export function parseEdges(value) {
  const text = String(value || "curve").toLowerCase();
  if (text === "straight" || text === "line" || text === "linear") return "straight";
  return "curve";
}

export function parseDirection(value) {
  const text = String(value || "unidirectional").toLowerCase();
  if (text === "bidirectional" || text === "bi" || text === "two-way" || text === "twoway") {
    return "bidirectional";
  }
  return "unidirectional";
}

export function layoutDefaultPrompt(layoutDoc) {
  const bars = [...(layoutDoc?.navbar?.left || []), ...(layoutDoc?.navbar?.right || [])];
  for (const item of bars) {
    const text = item?.props?.defaultPrompt;
    if (text) return String(text).trim();
  }
  return "";
}

export function mergePluginConfig(pluginId, props, layoutDoc) {
  const id = pluginId || "";
  const merged = { ...(props || {}) };
  if (id === "topology") {
    return { ...(layoutDoc?.topology || {}), ...merged };
  }
  if (id === "chat") {
    if (layoutDoc?.adapter && !merged.adapter) merged.adapter = layoutDoc.adapter;
    if (!merged.defaultPrompt) {
      const prompt = layoutDefaultPrompt(layoutDoc);
      if (prompt) merged.defaultPrompt = prompt;
    }
  }
  if (id === "story" || id === "plot") {
    const shared = layoutDoc?.plot || {};
    for (const key of ["vertical", "view", "xrange", "yrange", "xlabel", "ylabel", "labels", "dirac", "threshold"]) {
      if (merged[key] == null && shared[key] != null) merged[key] = shared[key];
    }
  }
  return merged;
}
