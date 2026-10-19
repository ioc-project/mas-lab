import assert from "node:assert/strict";
import test from "node:test";
import { agentColor, resetAgentColors, USER_COLOR } from "./agent-color.js";

test("user stays yellow; agents get distinct first-seen colors", () => {
  resetAgentColors();
  assert.equal(agentColor("user"), USER_COLOR);
  const names = ["coordinator", "alpha", "beta", "gamma", "delta"];
  const colors = names.map((name) => agentColor(name));
  assert.equal(new Set(colors).size, names.length);
  assert.equal(colors.includes(USER_COLOR), false);
  assert.equal(agentColor("alpha"), colors[1]);
});
