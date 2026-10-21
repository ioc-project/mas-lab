//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import Ajv from "ajv";
import addFormats from "ajv-formats";
import type { ValidateFunction } from "ajv";

import { apiFetch, getApiBaseUrl } from "@/api/client";

export type ManifestValidators = {
  validateMas: ValidateFunction;
  validateAgent: ValidateFunction;
};

let _validators: ManifestValidators | null = null;
let _loadPromise: Promise<ManifestValidators> | null = null;

function stripNestedSchemaIds(node: unknown): unknown {
  if (Array.isArray(node)) {
    return node.map(stripNestedSchemaIds);
  }
  if (node !== null && typeof node === "object") {
    const obj = node as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      if (k === "$id" || k === "$schema") {
        continue;
      }
      out[k] = stripNestedSchemaIds(v);
    }
    return out;
  }
  return node;
}

function compileValidators(
  agentSchema: Record<string, unknown>,
  masSchema: Record<string, unknown>,
): ManifestValidators {
  const mas = stripNestedSchemaIds(masSchema) as Record<string, unknown>;
  const agent = stripNestedSchemaIds(agentSchema) as Record<string, unknown>;
  mas.$id = "mas-manifest";
  agent.$id = "agent-manifest";

  const ajvMas = new Ajv({ allErrors: true, strict: false, validateSchema: false });
  const ajvAgent = new Ajv({ allErrors: true, strict: false, validateSchema: false });
  addFormats(ajvMas);
  addFormats(ajvAgent);

  return {
    validateMas: ajvMas.compile(mas),
    validateAgent: ajvAgent.compile(agent),
  };
}

async function assertControllerReachable(): Promise<void> {
  const base = getApiBaseUrl();
  const response = await apiFetch("/api/health");
  if (!response.ok) {
    throw new Error(
      `mas-lab controller not reachable at ${base} (GET /api/health → ${response.status}). ` +
        "Start it from the repo root: mas-lab serve",
    );
  }
}

async function fetchResolvedSchema(id: string): Promise<Record<string, unknown>> {
  const base = getApiBaseUrl();
  const response = await apiFetch(`/api/schemas/${id}?format=json&resolved=1`);
  if (!response.ok) {
    throw new Error(
      `Failed to load schema "${id}" from ${base}/api/schemas/${id} (${response.status})`,
    );
  }
  const body = await response.json();
  if (!body || typeof body !== "object") {
    throw new Error(`Schema "${id}" response was not a JSON object`);
  }
  return body as Record<string, unknown>;
}

/** Load agent + MAS validators from GET /api/schemas/{id}?resolved=1 (controller only). */
export async function loadManifestSchemas(): Promise<ManifestValidators> {
  if (_validators) {
    return _validators;
  }
  if (_loadPromise) {
    return _loadPromise;
  }

  _loadPromise = (async () => {
    await assertControllerReachable();
    const [agentSchema, masSchema] = await Promise.all([
      fetchResolvedSchema("agent"),
      fetchResolvedSchema("mas"),
    ]);
    _validators = compileValidators(agentSchema, masSchema);
    return _validators;
  })();

  return _loadPromise;
}

export function getManifestValidators(): ManifestValidators {
  if (!_validators) {
    throw new Error("Manifest schemas not loaded — call loadManifestSchemas() first");
  }
  return _validators;
}

export function areManifestSchemasReady(): boolean {
  return _validators !== null;
}
