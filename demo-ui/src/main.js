import "./styles.css";
import { apiFetch, readJson } from "./http.js";
import { createBus } from "./bus.js";
import { startShell } from "./shell.js";
import { createOfflinePlayer, loadOfflineBundle } from "./offline.js";
import { catalogRows, hideGallery, layoutFromHash, renderGallery } from "./layout-gallery.js";

const app = document.getElementById("app");
const bus = createBus();
let current = null;
let player = null;
const ATTACH = "demo-ui-attach";

async function defaultAttachRef() {
  let attach = "";
  try {
    attach = localStorage.getItem(ATTACH) || "";
  } catch {
    attach = "";
  }
  if (attach === "new") return {};
  try {
    const res = await apiFetch("/api/runs");
    if (!res.ok) return {};
    const data = await readJson(res, "runs");
    const rows = data.runs || [];
    const wanted = attach ? rows.find((row) => row.id === attach) : rows[0];
    if (!wanted) return {};
    return { batch: wanted.batch || wanted.id, session: wanted.session };
  } catch {
    return {};
  }
}

async function showGallery() {
  hideGallery();
  if (current) {
    current.destroy();
    current = null;
  }
  if (player) {
    player.pause();
    player = null;
    bus.player = null;
  }
  try {
    const res = await apiFetch("/api/layouts");
    const data = await readJson(res, "layouts");
    renderGallery(app, catalogRows(data));
  } catch (err) {
    app.textContent = `Failed to list layouts: ${err}`;
  }
}

async function boot() {
  const name = layoutFromHash();
  if (!name) {
    await showGallery();
    return;
  }
  hideGallery();
  if (current) current.destroy();
  if (player) {
    player.pause();
    player = null;
    bus.player = null;
  }
  try {
    const bundle = await loadOfflineBundle();
    if (bundle) {
      bus.mode = "offline";
      const layoutName = bundle.layoutName || name;
      current = await startShell({
        app,
        bus,
        layoutName,
        layoutDoc: bundle.layout && Object.keys(bundle.layout).length ? bundle.layout : undefined,
      });
      player = createOfflinePlayer(bus, bundle.envelopes || []);
      bus.player = player;
      player.play();
      return;
    }
    current = await startShell({ app, bus, layoutName: name });
    bus.mode = current.mode || "live";
    const ref = bus.chromeMode === "replay" ? await defaultAttachRef() : {};
    if (bus.chromeMode === "replay") await bus.loadSnapshot(name, ref);
    await bus.connect({
      topics: current.topics,
      subscribe: current.subscribe || current.topics,
      get: current.get || ["spec"],
      inspect: current.inspect,
      ref,
    });
  } catch (err) {
    const msg = String(err && err.message ? err.message : err);
    if (/\b404\b/.test(msg)) {
      try {
        history.replaceState(null, "", location.pathname + location.search);
      } catch {
        /* ignore */
      }
      await showGallery();
      return;
    }
    app.textContent = `Failed to start demo UI: ${err}`;
  }
}

window.addEventListener("hashchange", boot);
boot();
