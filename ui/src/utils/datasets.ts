//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import type {
  DatasetContent,
  DatasetItem,
} from "@/components/DatasetEditor/DatasetEditor";
import type {
  UserMessage,
  YamlDatasetItem,
  YamlRecord,
} from "@/types/datasets-types";

function userInputToPrompt(user: unknown): string {
  if (typeof user === "string") return user;
  if (Array.isArray(user)) {
    return user
      .map(userInputToPrompt)
      .filter((text) => text !== "")
      .join("\n");
  }
  if (user && typeof user === "object" && "content" in user) {
    const { content } = user as UserMessage;
    return content == null ? "" : String(content);
  }
  return "";
}

export function yamlToEditorContent(yamlStr: string): DatasetContent {
  const doc = parseYaml(yamlStr);
  const meta = doc?.metadata ?? {};
  const items: YamlDatasetItem[] = doc?.spec?.items ?? doc?.items ?? [];
  return {
    name: meta.name ?? doc?.name,
    version: meta.version ?? doc?.version,
    description: meta.description ?? doc?.description,
    items: items.map((it) => {
      const prompt = userInputToPrompt(it.inputs?.user);
      const result: DatasetItem = { id: it.id, prompt };
      if (it.expectations?.ground_truth)
        result.ground_truth = it.expectations.ground_truth;
      if (it.group) result.group = it.group;
      if (it.target_agents?.length) result.target_agents = it.target_agents;
      if (it.category) result.category = it.category;
      if (it.tags?.length) result.tags = it.tags;
      return result;
    }),
  };
}

function isRecord(value: unknown): value is YamlRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function setOrDelete(
  target: YamlRecord,
  key: string,
  value: unknown,
  keep: boolean,
) {
  if (keep) target[key] = value;
  else delete target[key];
}

// Merge the editor's fields onto the item as it was in the file, so fields the
// editor does not model (hitl, tool_fixtures, expectations.details, ...) survive.
function editorItemToYaml(
  it: DatasetItem,
  original: YamlRecord | undefined,
): YamlRecord {
  const result: YamlRecord = original ? { ...original } : {};
  result.id = it.id;

  const inputs: YamlRecord = isRecord(result.inputs)
    ? { ...result.inputs }
    : {};
  // Rewrite `user` only when the prompt was edited: that keeps {ref: ...}
  // objects and multi-turn lists intact, and otherwise writes the plain string.
  const promptUnchanged =
    inputs.user !== undefined && userInputToPrompt(inputs.user) === it.prompt;
  if (!promptUnchanged) {
    inputs.user = it.prompt;
  }
  result.inputs = inputs;

  const expectations: YamlRecord = isRecord(result.expectations)
    ? { ...result.expectations }
    : {};
  setOrDelete(expectations, "ground_truth", it.ground_truth, !!it.ground_truth);
  setOrDelete(
    result,
    "expectations",
    expectations,
    Object.keys(expectations).length > 0,
  );

  setOrDelete(result, "group", it.group, !!it.group);
  setOrDelete(
    result,
    "target_agents",
    it.target_agents,
    !!it.target_agents?.length,
  );
  setOrDelete(result, "category", it.category, !!it.category);
  setOrDelete(result, "tags", it.tags, !!it.tags?.length);
  return result;
}

export function editorContentToYaml(
  editorJson: DatasetContent,
  name: string,
  description: string,
  originalYaml?: string,
): string {
  let originalDoc: YamlRecord = {};
  try {
    const parsed = originalYaml ? parseYaml(originalYaml) : undefined;
    if (isRecord(parsed)) originalDoc = parsed;
  } catch {
    /* unparseable original: nothing to preserve */
  }
  const originalSpec = isRecord(originalDoc.spec) ? originalDoc.spec : {};
  const originalMeta = isRecord(originalDoc.metadata)
    ? originalDoc.metadata
    : {};
  const rawItems = originalSpec.items ?? originalDoc.items;
  const originalById = new Map<string, YamlRecord>();
  if (Array.isArray(rawItems)) {
    for (const raw of rawItems) {
      if (
        isRecord(raw) &&
        raw.id !== undefined &&
        !originalById.has(String(raw.id))
      ) {
        originalById.set(String(raw.id), raw);
      }
    }
  }

  const items = editorJson.items.map((it) =>
    editorItemToYaml(it, originalById.get(String(it.id))),
  );

  // Carry over unknown top-level keys; name/version/description and the legacy
  // top-level `items` are rewritten under metadata/spec.
  const {
    items: _legacyItems,
    name: _legacyName,
    version: _legacyVersion,
    description: _legacyDescription,
    metadata: _metadata,
    spec: _spec,
    ...otherTopLevel
  } = originalDoc;
  const metadata: YamlRecord = { ...originalMeta, name };
  setOrDelete(metadata, "version", editorJson.version, !!editorJson.version);
  setOrDelete(metadata, "description", description, !!description);

  const doc = {
    ...otherTopLevel,
    apiVersion: "lab/v1",
    kind: "Dataset",
    metadata,
    spec: { ...originalSpec, items },
  };
  return stringifyYaml(doc, { lineWidth: 0 });
}
