import assert from "node:assert/strict";
import test from "node:test";
import { chromeMode, initialChromeMode, parseModes } from "./layout-props.js";

test("chrome defaults to the first listed layout mode", () => {
  assert.deepEqual(parseModes("both"), ["live", "replay"]);
  assert.equal(initialChromeMode(["live", "replay", "passive"]), "live");
  assert.equal(initialChromeMode(undefined), "live");
  assert.equal(initialChromeMode(["live"]), "live");
  assert.equal(initialChromeMode(["passive", "live", "replay"]), "passive");
  assert.equal(initialChromeMode(["live", "replay", "passive"], "replay"), "replay");
  assert.equal(initialChromeMode(["live", "replay"], undefined, "replay"), "replay");
  assert.equal(initialChromeMode(["passive", "live", "replay"], undefined, "passive"), "passive");
  assert.equal(chromeMode({}), "passive");
});
