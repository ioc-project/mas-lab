#!/usr/bin/env node
//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const spec = resolve(uiRoot, "../docs/schemas/controller-openapi.json");
const generated = resolve(uiRoot, "src/api/generated/schema.d.ts");

if (!existsSync(spec)) {
  console.error("missing docs/schemas/controller-openapi.json");
  process.exit(1);
}
if (!existsSync(generated)) {
  console.error("missing ui/src/api/generated/schema.d.ts — run yarn generate:api");
  process.exit(1);
}
const json = JSON.parse(readFileSync(spec, "utf8"));
if (!json.paths || !json.paths["/api/scopes"]) {
  console.error("OpenAPI spec is missing /api/scopes");
  process.exit(1);
}
console.log("OK: committed OpenAPI spec and generated client types are present");
