/** Merge partial data-source refs. Empty fields inherit. */
export function mergeRef(base = {}, override = {}) {
  const out = { ...base };
  for (const [key, value] of Object.entries(override || {})) {
    if (value !== undefined && value !== null && value !== "") out[key] = value;
  }
  return out;
}

export function missingFields(ref, required) {
  return (required || []).filter((key) => !ref?.[key]);
}

export function refKey(ref = {}) {
  return ["library", "experiment", "scenario", "test", "run", "session", "overlay", "batch", "mode"]
    .map((k) => ref[k] || "")
    .join("|");
}

export function uniqueRefs(refs = []) {
  const seen = new Set();
  const out = [];
  for (const ref of refs) {
    const key = refKey(ref);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(ref);
  }
  return out;
}
