//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { parse, stringify } from "yaml";
import type { YamlOutputMap } from "./types";

interface WorkflowNode {
  id: string;
  role?: string;
  delegates_to?: string[];
}

interface AgentCluster {
  agentId: string;
  agentNodeId: string;
  /** Ordered list of input slot keys that this agent has. */
  inputSlots: string[];
  /** Slot key → stable canvas node ID. */
  inputNodeIds: Record<string, string>;
}

const LAYER_SPACING_X = 700;
const INPUT_NODE_X_OFFSET = -320;
const NODE_GAP = 40;
const CLUSTER_GAP = 300;

const AGENT_NODE_HEIGHT = 700;

/** Generous rendered-height estimates for each input-node type. */
const SLOT_HEIGHT: Record<string, number> = {
  model: 250,
  design_pattern: 200,
  tools: 260,
  promptSkills: 260,
  contextSkills: 260,
  memory: 200,
  role: 220,
};
const DEFAULT_SLOT_HEIGHT = 200;

function generateNodeId(): string {
  return `node_${crypto.randomUUID().slice(0, 8)}`;
}

/**
 * Assign layer indices to workflow nodes via BFS from the entry agent.
 * Agents unreachable from the entry are placed one layer beyond the deepest
 * reachable layer so they remain visible but visually separated.
 */
function assignLayers(
  workflowNodes: WorkflowNode[],
  entry: string | undefined,
): Map<string, number> {
  const adj = new Map<string, string[]>();
  for (const node of workflowNodes) {
    adj.set(node.id, node.delegates_to ?? []);
  }

  const layers = new Map<string, number>();
  const queue: string[] = [];

  if (entry && adj.has(entry)) {
    queue.push(entry);
    layers.set(entry, 0);
  }

  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentLayer = layers.get(current)!;
    for (const neighbor of adj.get(current) ?? []) {
      if (!layers.has(neighbor)) {
        layers.set(neighbor, currentLayer + 1);
        queue.push(neighbor);
      }
    }
  }

  let maxLayer = 0;
  for (const l of layers.values()) {
    if (l > maxLayer) maxLayer = l;
  }
  for (const node of workflowNodes) {
    if (!layers.has(node.id)) {
      layers.set(node.id, maxLayer + 1);
    }
  }

  return layers;
}

/**
 * Detect which input-node slots an agent spec declares, using the same
 * detection order as {@link deserializeYamlsToGraph} so indices match.
 */
function detectInputSlots(spec: Record<string, unknown>): string[] {
  const slots: string[] = [];

  const models =
    spec.models ??
    (spec["x-models-enabled"] === false ? spec["x-disabled-models"] : null);
  if (Array.isArray(models) && models.length > 0) slots.push("model");

  const dp =
    spec.design_pattern ??
    (spec["x-design-pattern-enabled"] === false
      ? spec["x-disabled-design-pattern"]
      : null);
  if (dp) slots.push("design_pattern");

  const tools =
    spec.tools ??
    (spec["x-tools-enabled"] === false ? spec["x-disabled-tools"] : null);
  if (Array.isArray(tools) && tools.length > 0) slots.push("tools");

  const promptSkills =
    spec.skills ??
    (spec["x-prompt-skills-enabled"] === false
      ? spec["x-disabled-prompt-skills"]
      : null);
  if (Array.isArray(promptSkills) && promptSkills.length > 0)
    slots.push("promptSkills");

  const ctxSkills =
    (spec.context_manager as Record<string, unknown> | undefined)?.skills ??
    (spec["x-context-skills-enabled"] === false
      ? spec["x-disabled-context-skills"]
      : null);
  if (Array.isArray(ctxSkills) && ctxSkills.length > 0)
    slots.push("contextSkills");

  const mem =
    spec.memory ??
    (spec["x-memory-enabled"] === false ? spec["x-disabled-memory"] : null);
  if (mem) slots.push("memory");

  const textInput =
    spec["x-text-input"] ??
    (spec["x-role-enabled"] === false
      ? (spec["x-disabled-role"] as Record<string, unknown> | undefined)?.text
      : null);
  if (textInput) slots.push("role");

  return slots;
}

/** Build cluster metadata for every agent in the yaml map. */
function buildClusters(yamlMap: YamlOutputMap): Map<string, AgentCluster> {
  const clusters = new Map<string, AgentCluster>();

  for (const [key, yaml] of Object.entries(yamlMap)) {
    if (!key.startsWith("agent:")) continue;
    const agentId = key.replace("agent:", "");
    const doc = parse(yaml);
    const spec: Record<string, unknown> = doc?.spec ?? {};
    const metadata: Record<string, unknown> = doc?.metadata ?? {};
    const existing: Record<string, string> = doc?.["x-canvas-node-ids"] ?? {};

    const agentNodeId = (metadata["x-node-id"] as string) ?? agentId;
    const inputSlots = detectInputSlots(spec);

    const inputNodeIds: Record<string, string> = {};
    for (const slot of inputSlots) {
      inputNodeIds[slot] = existing[slot] ?? generateNodeId();
    }

    clusters.set(agentId, { agentId, agentNodeId, inputSlots, inputNodeIds });
  }

  return clusters;
}

/** Vertical extent of a cluster: the taller of the agent or its stacked inputs. */
function clusterHeight(cluster: AgentCluster): number {
  if (cluster.inputSlots.length === 0) return AGENT_NODE_HEIGHT;
  const inputsHeight =
    cluster.inputSlots.reduce(
      (sum, slot) => sum + (SLOT_HEIGHT[slot] ?? DEFAULT_SLOT_HEIGHT),
      0,
    ) +
    (cluster.inputSlots.length - 1) * NODE_GAP;
  return Math.max(AGENT_NODE_HEIGHT, inputsHeight);
}

/**
 * If the MAS manifest in `yamlMap` lacks `x-canvas-positions`, compute a
 * clustered hierarchical layout and inject positions for every node (agents
 * and their input nodes).
 *
 * Each agent is treated as a cluster: the agent node on the right with its
 * input nodes stacked vertically to the left.  Layers flow left-to-right
 * based on the workflow delegation graph, and clusters within a layer are
 * spaced to avoid overlap.
 *
 * When positions already exist the map is returned unchanged.
 *
 * @example
 * ```ts
 * import { autoLayoutYamlMap } from "./autoLayout";
 * const layouted = autoLayoutYamlMap(initialYamlMap);
 * const graph = deserializeYamlsToGraph(layouted);
 * ```
 */
export function autoLayoutYamlMap(yamlMap: YamlOutputMap): YamlOutputMap {
  const masYaml = yamlMap["mas"];
  if (!masYaml) return yamlMap;

  const masDoc = parse(masYaml);
  if (!masDoc) return yamlMap;

  const existing = masDoc["x-canvas-positions"];
  if (existing && Object.keys(existing).length > 0) return yamlMap;

  const workflowNodes: WorkflowNode[] = masDoc?.spec?.workflow?.nodes ?? [];
  if (workflowNodes.length === 0) return yamlMap;

  const entry: string | undefined = masDoc?.spec?.workflow?.entry;
  const layers = assignLayers(workflowNodes, entry);
  const clusters = buildClusters(yamlMap);

  // Group agents by layer
  const layerGroups = new Map<number, string[]>();
  for (const [id, layer] of layers) {
    if (!layerGroups.has(layer)) layerGroups.set(layer, []);
    layerGroups.get(layer)!.push(id);
  }

  // Compute positions for all nodes
  const positions: Record<string, { x: number; y: number }> = {};

  for (const [layer, agentIds] of layerGroups) {
    const layerClusters = agentIds.map(
      (id) =>
        clusters.get(id) ?? {
          agentId: id,
          agentNodeId: id,
          inputSlots: [],
          inputNodeIds: {},
        },
    );

    // Total height of this layer: sum of cluster heights + gaps between them
    const heights = layerClusters.map(clusterHeight);
    const totalHeight =
      heights.reduce((sum, h) => sum + h, 0) +
      (layerClusters.length - 1) * CLUSTER_GAP;

    let cursorY = -totalHeight / 2;
    const agentX = layer * LAYER_SPACING_X;

    for (let i = 0; i < layerClusters.length; i++) {
      const cluster = layerClusters[i];

      // Agent node: centered vertically within its cluster
      const agentY = cursorY + (heights[i] - AGENT_NODE_HEIGHT) / 2;
      positions[cluster.agentNodeId] = {
        x: Math.round(agentX),
        y: Math.round(agentY),
      };

      // Input nodes: stacked to the left, centered as a group within the cluster
      const inputsHeight =
        cluster.inputSlots.reduce(
          (sum, slot) => sum + (SLOT_HEIGHT[slot] ?? DEFAULT_SLOT_HEIGHT),
          0,
        ) +
        Math.max(0, cluster.inputSlots.length - 1) * NODE_GAP;
      const inputStartY = cursorY + (heights[i] - inputsHeight) / 2;

      const inputX = agentX + INPUT_NODE_X_OFFSET;
      let inputCursorY = inputStartY;
      for (const slot of cluster.inputSlots) {
        const nodeId = cluster.inputNodeIds[slot];
        positions[nodeId] = {
          x: Math.round(inputX),
          y: Math.round(inputCursorY),
        };
        inputCursorY += (SLOT_HEIGHT[slot] ?? DEFAULT_SLOT_HEIGHT) + NODE_GAP;
      }

      cursorY += heights[i] + CLUSTER_GAP;
    }
  }

  masDoc["x-canvas-positions"] = positions;

  // Inject stable input-node IDs into agent manifests so the deserializer
  // uses matching IDs when looking up positions.
  const updatedMap: YamlOutputMap = { ...yamlMap, mas: stringify(masDoc) };

  for (const cluster of clusters.values()) {
    if (Object.keys(cluster.inputNodeIds).length === 0) continue;
    const agentKey = `agent:${cluster.agentId}`;
    const agentYaml = yamlMap[agentKey];
    if (!agentYaml) continue;

    const agentDoc = parse(agentYaml);
    if (!agentDoc) continue;

    if (!agentDoc.metadata) agentDoc.metadata = {};
    agentDoc.metadata["x-node-id"] = cluster.agentNodeId;
    agentDoc["x-canvas-node-ids"] = {
      ...(agentDoc["x-canvas-node-ids"] ?? {}),
      ...cluster.inputNodeIds,
    };

    updatedMap[agentKey] = stringify(agentDoc);
  }

  return updatedMap;
}
