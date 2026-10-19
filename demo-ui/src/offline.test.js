import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { loadStaticBundle } from "./offline.js";

const here = path.dirname(fileURLToPath(import.meta.url));

test("loadStaticBundle returns the JSON body on 200", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ layoutName: "run-view.yaml", envelopes: [{ topic: "spec.snapshot" }] }),
  });
  try {
    const bundle = await loadStaticBundle("/bundles/run-view.bundle.json");
    assert.equal(bundle.layoutName, "run-view.yaml");
    assert.equal(bundle.envelopes.length, 1);
  } finally {
    globalThis.fetch = previous;
  }
});

test("loadStaticBundle returns null on a missing or bad URL", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 404 });
  try {
    assert.equal(await loadStaticBundle("/missing.json"), null);
    assert.equal(await loadStaticBundle(""), null);
  } finally {
    globalThis.fetch = previous;
  }
});

test("replay-controls keep the extra speeds behind Replay chrome", () => {
  const src = fs.readFileSync(path.join(here, "player.js"), "utf8");
  assert.match(src, /\[0\.5, 1, 2, 4, 8, 10, 20, 50\]/);
  assert.match(src, /\(max\)/);
  assert.match(src, /job\.progress/);
  assert.match(src, /isPassive/);
  assert.match(src, /fmtElapsed/);
});
