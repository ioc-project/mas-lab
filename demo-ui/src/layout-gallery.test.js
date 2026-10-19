import assert from "node:assert/strict";
import test from "node:test";
import { catalogRows, layoutFromHash } from "./layout-gallery.js";

test("empty hash is the layout catalog, not a default yaml", () => {
  assert.equal(layoutFromHash(""), "");
  assert.equal(layoutFromHash("#"), "");
  assert.equal(layoutFromHash("#/"), "");
  assert.equal(layoutFromHash("#run-view.yaml"), "run-view.yaml");
  assert.equal(layoutFromHash("#run-view"), "run-view.yaml");
});

test("catalogRows prefers title rows from /api/layouts", () => {
  const rows = catalogRows({
    layouts: ["run-view.yaml"],
    catalog: [{ slug: "panel-a.yaml", title: "Panel A", group: "library-lab" }],
  });
  assert.equal(rows[0].title, "Panel A");
  assert.deepEqual(
    catalogRows({ layouts: ["run-view.yaml"] }).map((row) => row.slug),
    ["run-view.yaml"],
  );
});
