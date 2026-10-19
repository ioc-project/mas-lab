import { defineConfig } from "vite";

// DEMO_UI_API_PORT / DEMO_UI_UI_PORT let scripts/demo-ui.sh run more than one
// instance side by side (e.g. two labs compared in parallel browser tabs).
// Before this, the proxy target and the dev-server port were hardcoded, so a
// second instance's Vite server silently fell back to the next free port
// (5175, 5176, ...) while still proxying every /api and /ws call to the
// first instance's backend on 8090 -- the second instance's own backend,
// listening on whatever DEMO_UI_API_PORT it was given, was never reachable
// through its own UI.
const apiPort = process.env.DEMO_UI_API_PORT || "8090";
const uiPort = Number(process.env.DEMO_UI_UI_PORT) || 5174;

export default defineConfig({
  base: process.env.DEMO_UI_BASE || "/",
  server: {
    host: "127.0.0.1",
    port: uiPort,
    strictPort: true,
    hmr: {
      host: "127.0.0.1",
      port: uiPort,
      clientPort: uiPort,
    },
    proxy: {
      "/api": `http://127.0.0.1:${apiPort}`,
      "/ws": { target: `ws://127.0.0.1:${apiPort}`, ws: true },
      "/health": `http://127.0.0.1:${apiPort}`,
    },
  },
});
