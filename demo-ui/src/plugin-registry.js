const plugins = new Map();

export function registerPlugin(manifest) {
  if (!manifest?.id || typeof manifest.mount !== "function") {
    throw new Error("plugin requires id and mount()");
  }
  plugins.set(manifest.id, manifest);
}

export function getPlugin(id) {
  return plugins.get(id);
}

export function listPlugins() {
  return [...plugins.values()];
}
