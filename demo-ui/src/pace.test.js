import assert from "node:assert/strict";
import test from "node:test";
import { createDispatchPacer, eventTimeMs, inLane, shouldDelay } from "./pace.js";

test("visible speech is delayed; delegate starts stay in order without a speech gap", () => {
  const speech = {
    topic: "native.user_response",
    payload: { kind: "user_response" },
    replay: false,
  };
  const delegate = {
    topic: "native.tool_call_start",
    payload: { kind: "tool_call_start", tool_name: "delegate_to_backend" },
    replay: false,
  };
  assert.equal(shouldDelay(speech), true);
  assert.equal(inLane(speech), true);
  assert.equal(shouldDelay(delegate), false);
  assert.equal(inLane(delegate), true);
});

test("metric ticks are not paced and do not stall speech", async () => {
  assert.equal(
    shouldDelay({ topic: "metric.snapshot", payload: {}, replay: false }),
    false,
  );
  assert.equal(
    inLane({ topic: "metric.snapshot", payload: { n_llm_calls: 1 }, replay: false }),
    false,
  );
  const seen = [];
  const pacer = createDispatchPacer({ gapMs: 4000 });
  const ref = { batch: "b" };
  pacer.push(
    { topic: "native.execution_start", payload: { kind: "execution_start" }, ref },
    () => seen.push("user"),
  );
  for (let i = 0; i < 80; i += 1) {
    pacer.push({ topic: "metric.snapshot", payload: { i }, ref }, () => seen.push("m"));
  }
  pacer.push(
    {
      topic: "native.user_response",
      payload: { kind: "user_response", content: "ok" },
      ref,
    },
    () => seen.push("speech"),
  );
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.ok(seen.includes("user"));
  assert.ok(seen.includes("m"));
  assert.equal(seen.includes("speech"), false, "speech still waits one gap, not 80 metric gaps");
  await new Promise((resolve) => setTimeout(resolve, 80));
});

test("same-timestamp tools dispatch together; later tools wait", async () => {
  assert.equal(eventTimeMs({ payload: { timestamp: 1000 } }), 1_000_000);
  const seen = [];
  const pacer = createDispatchPacer({ gapMs: 5000, toolMinMs: 80, toolMaxMs: 200, toolScale: 1 });
  const ref = { batch: "b" };
  const mk = (ts, tool) => ({
    topic: "native.tool_call_start",
    payload: { kind: "tool_call_start", tool_name: tool, timestamp: ts },
    ref,
    replay: false,
  });
  pacer.push(mk(1000, "delegate_to_backend"), () => seen.push("a"));
  pacer.push(mk(1000.001, "delegate_to_db"), () => seen.push("b"));
  pacer.push(mk(1000.5, "run_action"), () => seen.push("c"));
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.deepEqual(seen, ["a", "b"]);
  await new Promise((resolve) => setTimeout(resolve, 250));
  assert.deepEqual(seen, ["a", "b", "c"]);
});
