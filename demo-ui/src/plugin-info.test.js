import assert from "node:assert/strict";
import test from "node:test";
import { resolvePanelInfo } from "./plugin-info.js";

test("layout props.info overrides the plugin manifest", () => {
  const plugin = { id: "plot", info: "from plugin" };
  assert.equal(resolvePanelInfo(plugin, { info: "from layout" }), "from layout");
  assert.equal(resolvePanelInfo(plugin, {}), "from plugin");
  assert.equal(resolvePanelInfo({ id: "x", description: "desc" }, {}), "desc");
  assert.equal(resolvePanelInfo({ id: "x" }, {}), "");
});
