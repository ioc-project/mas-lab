#!/usr/bin/env node
//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/** Emit a typed snapshot of the committed controller OpenAPI. */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const specPath = resolve(uiRoot, "../docs/schemas/controller-openapi.json");
const outDir = resolve(uiRoot, "src/api/generated");
mkdirSync(outDir, { recursive: true });
const spec = JSON.parse(readFileSync(specPath, "utf8"));
const paths = Object.keys(spec.paths || {}).sort();
const header = `//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
// Generated from docs/schemas/controller-openapi.json. Do not edit.
`;
const body = `${header}export type ControllerPath =\n  ${paths.map((p) => `| ${JSON.stringify(p)}`).join("\n  ")};\n`;
writeFileSync(resolve(outDir, "schema.d.ts"), body);
console.log(`OK: wrote ${paths.length} paths to src/api/generated/schema.d.ts`);
