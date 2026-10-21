#!/usr/bin/env node
//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const srcRoot = join(fileURLToPath(import.meta.url), "../../src");
const forbidden = [
  '".tool.yaml"',
  '"/mas.yaml"',
  '"mas.yaml"',
  '"agents/"',
  '"apps/"',
  '"pipelines/"',
  "pipeline.yaml",
  "overlay.yaml",
  ".agent.yaml",
  "~/.local/share/mas/labs",
  "gpt-",
  "gemini",
  "azure/",
  "vertex_ai/",
  "api/mock",
];

function skip(rel) {
  return (
    rel.startsWith("test/") ||
    rel.includes(".test.") ||
    rel.startsWith("api/generated/") ||
    rel.startsWith("types/")
  );
}

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) walk(full, acc);
    else if (/\.(ts|tsx|js|jsx)$/.test(name)) acc.push(full);
  }
  return acc;
}

const hits = [];
for (const file of walk(srcRoot)) {
  const rel = relative(srcRoot, file);
  if (skip(rel)) continue;
  const text = readFileSync(file, "utf8");
  for (const needle of forbidden) {
    if (text.includes(needle)) hits.push(`${rel}: ${needle}`);
  }
}

if (hits.length) {
  console.error("Forbidden path/model literals in ui/src:\n" + hits.join("\n"));
  process.exit(1);
}
console.log("OK: no forbidden path/model literals in ui/src");
