import assert from "node:assert/strict";
import test from "node:test";
import { compileLayout, withSize } from "./layout-compiler.js";

test("px sizes become percents so Golden Layout does not assert ROCCAS6692", () => {
  const item = withSize({ type: "component" }, "240px");
  assert.match(item.size, /%$/);
  assert.equal(item.width, undefined);
});

test("fr sizes keep the fr unit", () => {
  const item = withSize({ type: "component" }, "2fr");
  assert.equal(item.size, "2fr");
});

test("hstack compiles a row of percent/fr children", () => {
  const config = compileLayout({
    dataSource: { scenario: "baseline" },
    layout: {
      type: "hstack",
      children: [
        { type: "panel", plugin: "topology", size: "2fr" },
        { type: "panel", plugin: "plot", size: "1.1fr" },
        { type: "panel", plugin: "story", size: "240px" },
      ],
    },
  });
  assert.equal(config.root.type, "row");
  const sizes = config.root.content.map((child) => child.size);
  assert.ok(sizes.every((size) => typeof size === "string" && /(%|fr)$/.test(size)));
});
