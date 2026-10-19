import assert from "node:assert/strict";
import test from "node:test";
import {
  accumulateDiracs,
  chatPlugin,
  chartSpecs,
  diracOf,
  htmlPlotFor,
  htmlPlotPlugin,
  pluginMeta,
  addressOf,
  inferPlotKind,
  ownerFromStream,
  pinKindInShell,
  plotTopics,
  rendererKind,
  topologyPlugin,
  wants,
} from "./plot-host.js";

test("plot plugin hosts mas-lab plots; artefact names the subscribe stream", () => {
  assert.equal(htmlPlotPlugin.id, "plot");
  assert.equal(chatPlugin.id, "chat");
  assert.equal(topologyPlugin.id, "topology");
  assert.equal(pluginMeta.embedded, true);
  assert.deepEqual(htmlPlotPlugin.gets, ["spec"]);
  assert.deepEqual(plotTopics({ config: { artefact: "chat", kind: "chat" } }), [
    "custom.chat",
    "spec.snapshot",
    "spec.*",
    "view.reset",
  ]);
  assert.deepEqual(chatPlugin.subscribes, ["custom.chat", "spec.snapshot", "spec.*", "view.reset"]);
  assert.deepEqual(topologyPlugin.subscribes, ["custom.topology", "spec.snapshot", "spec.*", "view.reset", "job.completed"]);
  const extra = htmlPlotFor("custom-kind", { artefact: "custom-kind", kind: "custom-kind" });
  assert.equal(extra.id, "custom-kind");
  const loopTopics = plotTopics({ config: { stream: "series.semantic-loop", kind: "loop" } });
  assert.ok(loopTopics.includes("job.progress"));
  assert.ok(loopTopics.includes("job.completed"));
  assert.ok(loopTopics.includes("series.semantic-loop"));
});

test("plot host keeps JSON chart data and drops HTML-only shells", () => {
  const ctx = { config: { artefact: "chat", kind: "chat", stream: "custom.chat" } };
  assert.equal(ownerFromStream("custom.chat.plot"), "custom.chat");
  assert.equal(wants({ topic: "custom.chat.plot", payload: { html: "<html></html>" } }, ctx, "custom.chat", "chat"), "");
  assert.equal(
    wants({ topic: "custom.chat.plot", payload: { html: "<html></html>", cards: [{ text: "hi" }] } }, ctx, "custom.chat", "chat"),
    "artefact",
  );
  assert.equal(wants({ topic: "custom.chat.snapshot", payload: { cards: [] } }, ctx, "custom.chat", "chat"), "artefact");
  assert.equal(wants({ topic: "custom.topology.snapshot", payload: { nodes: [] } }, ctx, "custom.chat", "chat"), "");
  const panel = { config: { artefact: "panel-a", kind: "panel-a", stream: "custom.panel-a" } };
  assert.equal(
    wants(
      { topic: "custom.panel-a.plot", payload: { kind: "panel-a", data: { agents: [] } } },
      panel,
      "custom.panel-a",
      "panel-a",
    ),
    "artefact",
  );
});

test("plot host infers a renderer from artefact and refuses a foreign tick", () => {
  assert.equal(inferPlotKind({ config: { artefact: "chat" } }), "chat");
  assert.equal(inferPlotKind({ config: { stream: "series.example" } }), "series");
  assert.equal(rendererKind("loop"), "series");
  const loop = { config: { artefact: "agents", kind: "loop", stream: "series.agents" } };
  assert.equal(
    wants(
      { topic: "series.agents", payload: { kind: "custom.chat.snapshot", cards: [] } },
      loop,
      "series.agents",
      "loop",
    ),
    "",
  );
  assert.equal(
    wants(
      { topic: "series.agents", payload: { kind: "series.agents", series: { a: [0.1] } } },
      loop,
      "series.agents",
      "loop",
    ),
    "artefact",
  );
  const html = pinKindInShell("function render(chart) {\n  const kind = STATE.kind;\n", "loop");
  assert.match(html, /const kind = "series";/);
});

test("agent loop plot subscribes to the per-agent series stream only", () => {
  const ctx = {
    config: { artefact: "agents", kind: "loop", stream: "series.agents" },
  };
  const topics = plotTopics(ctx);
  assert.ok(topics.includes("series.agents"));
  assert.equal(topics.includes("series.mas"), false);
});

test("MAS loop plot subscribes to the MAS series stream", () => {
  const ctx = {
    config: {
      artefact: "mas",
      kind: "loop",
      stream: "series.mas",
      dirac: "series.threshold-mas-t0p5",
    },
  };
  assert.equal(ownerFromStream("series.mas"), "series.mas");
  assert.ok(plotTopics(ctx).includes("series.mas"));
  assert.ok(plotTopics(ctx).includes("series.threshold-mas-t0p5"));
  assert.equal(
    wants(
      { topic: "series.mas", payload: { kind: "series.mas", series: { mas: [{ t: 1, y: 0.8 }] } } },
      ctx,
      "series.mas",
      "loop",
    ),
    "artefact",
  );
  assert.equal(
    wants(
      { topic: "series.agents", payload: { kind: "series.agents", series: { specialist: [{ t: 1, y: 0.2 }] } } },
      ctx,
      "series.mas",
      "loop",
    ),
    "",
  );
});

test("empty dataSource does not poll the dash artefact coordinate", () => {
  assert.equal(addressOf("agents", {}), "");
  assert.equal(addressOf("chat", { library: "", scenario: "" }), "");
  assert.match(addressOf("chat", { library: "lib", scenario: "baseline" }), /\/api\/artefacts\/lib\/-\/baseline\/-\/-\/chat$/);
});

test("view.reset is always a plot hit", () => {
  const ctx = { config: { artefact: "chat", kind: "chat", stream: "custom.chat" } };
  assert.equal(wants({ topic: "view.reset", payload: { phase: "reset" } }, ctx, "custom.chat", "chat"), "reset");
});

test("threshold diracs accumulate; an empty series tick does not wipe them", () => {
  const first = accumulateDiracs([], [{ id: "a", kind: "failure" }]);
  assert.deepEqual(first, [{ id: "a", kind: "failure" }]);
  assert.deepEqual(accumulateDiracs(first, []), first);
  assert.deepEqual(
    accumulateDiracs(first, [{ id: "b", kind: "recovery" }]),
    [{ id: "a", kind: "failure" }, { id: "b", kind: "recovery" }],
  );
});

test("loop plot also subscribes to a dirac threshold stream", () => {
  const ctx = {
    config: {
      artefact: "agents",
      kind: "loop",
      stream: "series.agents",
      dirac: "series.threshold-agents-t0p5",
    },
  };
  assert.equal(diracOf(ctx), "series.threshold-agents-t0p5");
  assert.ok(plotTopics(ctx).includes("series.threshold-agents-t0p5"));
  assert.equal(
    wants(
      { topic: "series.threshold-agents-t0p5", payload: { diracs: [{ id: "a", kind: "failure" }] } },
      ctx,
      "series.agents",
      "loop",
    ),
    "dirac",
  );
});

test("detector series owners stay distinct from the generic series artefact", () => {
  const loop = {
    config: { artefact: "semantic-loop", kind: "loop", stream: "series.semantic-loop" },
  };
  assert.equal(ownerFromStream("series.semantic-loop"), "series.semantic-loop");
  assert.equal(
    wants(
      { topic: "series", payload: { series: { a: [{ t: 1, y: 0.2 }] } } },
      loop,
      "series.semantic-loop",
      "loop",
    ),
    "",
  );
  assert.equal(
    wants(
      { topic: "series.mas-semantic-loop", payload: { series: { mas: [{ t: 1, y: 0.9 }] } } },
      loop,
      "series.semantic-loop",
      "loop",
    ),
    "",
  );
  assert.equal(
    wants(
      { topic: "series.semantic-loop", payload: { series: { alpha: [{ t: 1, y: 0.8 }] } } },
      loop,
      "series.semantic-loop",
      "loop",
    ),
    "artefact",
  );
  assert.equal(
    wants({ topic: "job.progress", payload: { t: 12.4, t0: 0, speed: 12 } }, loop, "series.semantic-loop", "loop"),
    "clock",
  );
  assert.equal(
    wants({ topic: "job.completed", payload: {} }, loop, "series.semantic-loop", "loop"),
    "idle",
  );
});

test("one plot plugin can subscribe to N stacked metric streams", () => {
  const ctx = {
    config: {
      kind: "loop",
      charts: [
        { title: "Loop · MAS", stream: "series.mas-semantic-loop", artefact: "mas-semantic-loop" },
        { title: "Loop · agents", stream: "series.semantic-loop", artefact: "semantic-loop" },
      ],
    },
  };
  const specs = chartSpecs(ctx);
  assert.equal(specs.length, 2);
  const topics = plotTopics(ctx);
  assert.ok(topics.includes("series.mas-semantic-loop"));
  assert.ok(topics.includes("series.semantic-loop"));
  assert.equal(
    wants({ topic: "series.mas-semantic-loop", payload: { series: { mas: [{ t: 1, y: 0.4 }] } } }, ctx, "", "loop"),
    "artefact",
  );
  assert.equal(
    wants({ topic: "job.completed", payload: {} }, { config: { kind: "topology", stream: "custom.topology" } }, "custom.topology", "topology"),
    "idle",
  );
});

test("plot host mounts in-document; no iframe", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const dir = path.dirname(new URL(import.meta.url).pathname);
  for (const name of ["plot-host.js", "plot-runtime.js"]) {
    const src = fs.readFileSync(path.join(dir, name), "utf8");
    assert.equal(/<iframe|createElement\(\s*["']iframe["']/.test(src), false, name);
    assert.doesNotMatch(src, /new Function/);
    assert.doesNotMatch(src, /jsdelivr|cdn\./);
    assert.match(src, /embedded|draw\.js/);
  }
});

test("embedded plots interpolate the ingest clock every second", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const dir = path.dirname(new URL(import.meta.url).pathname);
  const runtime = fs.readFileSync(path.join(dir, "plot-runtime.js"), "utf8");
  assert.match(runtime, /requestAnimationFrame\(step\)/);
  assert.match(runtime, /freezeClock/);
  assert.match(runtime, /clockSpeed > 0/);
  assert.match(runtime, /!Number.isFinite\(clockT\) && Number.isFinite\(hinted\)/);
  assert.doesNotMatch(runtime, /lastSeriesTime/);
  const host = fs.readFileSync(path.join(dir, "plot-host.js"), "utf8");
  assert.match(host, /speed: clock.speed/);
});
