import assert from "node:assert/strict";
import test from "node:test";
import { holdOrQueue, matchNoteOn, normalizeNotes, noteMatches, pickNote, walkNotes } from "./narrative.js";

test("empty narrative block yields no notes", () => {
  assert.deepEqual(normalizeNotes({}).notes, []);
  assert.equal(normalizeNotes({}).enabled, false);
  assert.equal(normalizeNotes({ narrative: { notes: [] } }).notes.length, 0);
  assert.equal(normalizeNotes({ narrative: { notes: [{ id: "x", on: { kind: "x" } }] } }).notes.length, 0);
});

test("enabled: false keeps notes but disables overlay and chat", () => {
  const spec = normalizeNotes({
    narrative: {
      enabled: false,
      notes: [{ id: "x", text: "Hello", on: { kind: "tool_call_start" } }],
    },
  });
  assert.equal(spec.notes.length, 1);
  assert.equal(spec.enabled, false);
  assert.equal(spec.overlay, false);
  assert.equal(spec.chat, false);
  assert.deepEqual(walkNotes(spec, [{ topic: "job.started", replay: false }, { topic: "native.tool_call_start", replay: false, payload: { kind: "tool_call_start" } }]), []);
});

test("notes match stable telemetry, not time", () => {
  const env = {
    topic: "native.tool_call_start",
    replay: false,
    ref: { scenario: "overlay-a" },
    payload: {
      kind: "tool_call_start",
      tool_name: "run_skill_script",
      agent_id: "planner",
      arguments: { args: ["score", "--candidate-id", "seed-db"] },
    },
  };
  assert.equal(matchNoteOn({ kind: "tool_call_start", tool: "run_skill_script", argv: "score" }, env), true);
  assert.equal(matchNoteOn({ toolPrefix: "delegate_to_" }, env), false);
  assert.equal(matchNoteOn({ argv: "score" }, env), true);
  assert.equal(matchNoteOn({}, env), false);
});

test("scenario-bound notes ignore other lanes and live catch-up", () => {
  const spec = normalizeNotes({
    narrative: {
      notes: [
        {
          id: "scores",
          text: "Watch the metrics update as the run scores.",
          at: "plot",
          scenario: "overlay-a",
          on: { argv: "score" },
        },
      ],
    },
  });
  const hit = {
    topic: "native.tool_call_start",
    replay: false,
    ref: { scenario: "overlay-a" },
    payload: { kind: "tool_call_start", arguments: { args: ["score"] } },
  };
  assert.equal(noteMatches(spec.notes[0], hit, { live: true }), true);
  assert.equal(noteMatches(spec.notes[0], { ...hit, ref: { scenario: "baseline" } }, { live: true }), false);
  assert.equal(noteMatches(spec.notes[0], { ...hit, replay: true }, { live: true }), false);
  assert.equal(noteMatches(spec.notes[0], { ...hit, replay: true }, { live: false }), true);
});

test("each note fires once in layout order", () => {
  const spec = normalizeNotes({
    narrative: {
      maxNotes: 8,
      notes: [
        { id: "a", text: "A", on: { kind: "tool_call_start" } },
        { id: "b", text: "B", on: { kind: "tool_call_start" } },
      ],
    },
  });
  const env = { topic: "native.tool_call_start", replay: false, payload: { kind: "tool_call_start" } };
  const seen = new Set();
  const first = pickNote(spec, env, seen, { live: true });
  seen.add(first.id);
  const second = pickNote(spec, env, seen, { live: true });
  assert.equal(first.id, "a");
  assert.equal(second.id, "b");
});

test("a visible note is not interrupted; default queue is one", () => {
  const a = { note: { id: "a" } };
  const b = { note: { id: "b" } };
  const c = { note: { id: "c" } };
  const first = holdOrQueue({ visible: false, pending: null }, a);
  assert.equal(first.show, a);
  const queued = holdOrQueue({ visible: true, pending: null }, b);
  assert.equal(queued.show, null);
  assert.deepEqual(queued.pending, [b]);
  const dropped = holdOrQueue({ visible: true, pending: queued.pending }, c);
  assert.deepEqual(dropped.pending, [b]);
});

test("maxQueued keeps a FIFO of later notes", () => {
  const a = { note: { id: "a" } };
  const b = { note: { id: "b" } };
  const c = { note: { id: "c" } };
  const d = { note: { id: "d" } };
  let state = holdOrQueue({ visible: false, pending: [] }, a, { maxQueued: 2 });
  state = holdOrQueue({ visible: true, pending: state.pending }, b, { maxQueued: 2 });
  state = holdOrQueue({ visible: true, pending: state.pending }, c, { maxQueued: 2 });
  state = holdOrQueue({ visible: true, pending: state.pending }, d, { maxQueued: 2 });
  assert.equal(state.show, null);
  assert.deepEqual(
    state.pending.map((item) => item.note.id),
    ["b", "c"],
  );
});
