import assert from "node:assert/strict";
import test from "node:test";
import { resetView } from "./chrome.js";

test("resetView emits view.reset so Passive can clear every plugin", async () => {
  const emitted = [];
  const ctx = {
    bus: {
      emit(envelope) {
        emitted.push(envelope);
      },
    },
  };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true });
  try {
    await resetView(ctx);
  } finally {
    globalThis.fetch = origFetch;
  }
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].topic, "view.reset");
  assert.equal(emitted[0].payload.phase, "reset");
});

test("resetView without ctx does not throw", async () => {
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true });
  try {
    await resetView();
    await resetView(undefined);
  } finally {
    globalThis.fetch = origFetch;
  }
});
