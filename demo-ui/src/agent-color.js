/** Stable color per id. User is always yellow; agents get first-seen palette slots. */
export const USER_COLOR = "#facc15";

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

export function resetAgentColors() {
  Object.keys(COLOR_OF).forEach((key) => {
    delete COLOR_OF[key];
  });
}

function paletteIndex(id) {
  let h = 2166136261;
  const s = String(id || "agent");
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % PALETTE.length;
}

export function agentColor(id) {
  const key = String(id || "agent");
  if (key === "user") return USER_COLOR;
  if (COLOR_OF[key]) return COLOR_OF[key];
  const used = new Set(Object.values(COLOR_OF));
  const color = PALETTE.find((c) => !used.has(c)) || PALETTE[paletteIndex(key)];
  COLOR_OF[key] = color;
  return color;
}
