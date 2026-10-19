import assert from "node:assert/strict";
import test from "node:test";
import { createBus } from "./bus.js";

test("a new replay nonce emits view.reset before the next envelope", () => {
  const bus = createBus();
  const seen = [];
  bus.subscribe((envelope) => seen.push(envelope.topic));
  bus.chromeMode = "passive";
  bus.emit({ topic: "custom.timeline.snapshot", nonce: "r1", ref: {}, payload: { events: [{ id: "old" }] } });
  bus.emit({ topic: "custom.timeline.snapshot", nonce: "r2", ref: {}, payload: { events: [{ id: "new" }] } });
  assert.deepEqual(seen, [
    "custom.timeline.snapshot",
    "view.reset",
    "custom.timeline.snapshot",
  ]);
  assert.equal(bus.replayNonce, "r2");
});

test("subscribe replays history so a late plot still paints", () => {
  const bus = createBus();
  bus.pump({ topic: "custom.chat.snapshot", ref: {}, payload: { cards: [{ text: "hi" }] } });
  const seen = [];
  bus.subscribe((envelope) => seen.push(envelope.topic));
  assert.deepEqual(seen, ["custom.chat.snapshot"]);
});

test("replay chrome mode still matches paced ingest envelopes", () => {
  const bus = createBus();
  bus.chromeMode = "replay";
  bus.nonce = "ui-tab";
  bus.replayNonce = "r1";
  assert.ok(
    bus.match(
      { topic: "custom.chat.snapshot", nonce: "r1", replay: true, ref: {} },
      ["custom.chat", "custom.chat.*"],
      {},
    ),
  );
});

test("view.reset matches every plugin and still delivers series afterwards", () => {
  const bus = createBus();
  const seen = [];
  bus.subscribe((envelope) => seen.push(envelope.topic));
  bus.pump({ topic: "custom.timeline.snapshot", ref: {}, payload: { events: [{ id: "old" }] } });
  bus.pump({ topic: "view.reset", ref: {}, payload: { phase: "reset" } });
  bus.pump({ topic: "series.agents", ref: {}, payload: { series: { a: [{ t: 1, y: 0.2 }] } } });
  bus.pump({ topic: "series.mas", ref: {}, payload: { series: { mas: [{ t: 1, y: 0.3 }] } } });
  assert.ok(bus.match({ topic: "view.reset", ref: {} }, ["custom.timeline"], { scenario: "baseline" }));
  assert.deepEqual(seen, [
    "custom.timeline.snapshot",
    "view.reset",
    "series.agents",
    "series.mas",
  ]);
});
