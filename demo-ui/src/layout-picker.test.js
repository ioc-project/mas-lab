import assert from "node:assert/strict";
import test from "node:test";
import { catalogForGroup, catalogSections, currentLayoutSlug } from "./layout-picker.js";

test("catalogForGroup filters by group", () => {
  const catalog = [
    { slug: "run-view.yaml", title: "Run view", group: "samples" },
    { slug: "compare-overlays.yaml", title: "Compare overlay variants", group: "samples" },
    { slug: "other.yaml", title: "Other", group: "other" },
  ];
  const rows = catalogForGroup(catalog, "samples");
  assert.deepEqual(
    rows.map((row) => row.slug),
    ["run-view.yaml", "compare-overlays.yaml"],
  );
});

test("catalogSections lists unknown groups alphabetically", () => {
  const catalog = [
    { slug: "b.yaml", title: "B", group: "beta" },
    { slug: "a.yaml", title: "A", group: "alpha" },
  ];
  const sections = catalogSections(catalog);
  assert.deepEqual(
    sections.map((section) => section.group),
    ["alpha", "beta"],
  );
});

test("currentLayoutSlug uses the hash filename", () => {
  assert.equal(currentLayoutSlug("run-view.yaml"), "run-view.yaml");
  assert.equal(currentLayoutSlug("compare-overlays"), "compare-overlays.yaml");
});
