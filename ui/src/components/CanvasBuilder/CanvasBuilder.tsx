//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useCallback, useRef, useMemo, useEffect, useState } from "react";
import { useParams } from "react-router";
import { stringify, parse } from "yaml";
import { AgentChatDrawer } from "./AgentChatDrawer";
import {
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useNodesState,
  useEdgesState,
  Controls,
  Background,
  MiniMap,
  useReactFlow,
  type Connection,
  type Edge,
  type NodeTypes,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { DnDProvider, useDnD } from "./DnDContext";
import { Sidebar } from "./Sidebar";
import {
  AgentNode,
  ModelNode,
  DesignPatternNode,
  ToolNode,
  PromptSkillNode,
  ContextSkillNode,
  MemoryNode,
  TextInputNode,
} from "./nodes";
import {
  HANDLE_IDS,
  AGENT_OUTPUT_HANDLE,
  type CanvasBuilderProps,
  type YamlOutputMap,
  type ChatMessage,
} from "./types";
import { autoLayoutYamlMap } from "./autoLayout";
import "./canvasBuilder.css";

function generateNodeId(): string {
  return `node_${crypto.randomUUID().slice(0, 8)}`;
}

/**
 * Convert manifest ``{ ref: path }`` tool entries to display names for the canvas.
 *
 * Refs pointing to ``../../../tools/<name>.tool.yaml`` (global tools) are
 * returned as ``global/<name>``.  Refs pointing to ``../tools/<name>.tool.yaml``
 * (app-local tools) are returned as ``<name>``.
 */
function normalizeToolRefs(tools: unknown): string[] {
  if (!Array.isArray(tools)) return [];
  return tools.map((entry) => {
    if (entry && typeof entry === "object") {
      const obj = entry as Record<string, unknown>;
      if (typeof obj.ref === "string") {
        const ref = obj.ref;
        const stem = ref.split("/").pop()?.replace(".tool.yaml", "") ?? ref;
        if (ref.startsWith("../../../tools/")) {
          return `global/${stem}`;
        }
        return stem;
      }
    }
    return String(entry);
  });
}

/**
 * Convert canvas display tool names back to ``{ ref: path }`` entries for
 * agent YAML serialization.
 *
 * ``global/<name>`` -> ``{ ref: ../../../tools/<name>.tool.yaml }``
 * ``<name>``        -> ``{ ref: ../tools/<name>.tool.yaml }``
 */
function toolDisplayNamesToRefs(tools: string[]): Array<{ ref: string }> {
  return tools.map((name) => {
    if (name.startsWith("global/")) {
      const bare = name.slice("global/".length);
      return { ref: `../../../tools/${bare}.tool.yaml` };
    }
    return { ref: `../tools/${name}.tool.yaml` };
  });
}

function normalizeIntent(intent: unknown): string {
  if (typeof intent === "string") return intent;
  if (intent && typeof intent === "object" && "summary" in intent) {
    return String((intent as { summary?: string }).summary ?? "");
  }
  return "";
}

function getDefaultDataForType(type: string): Record<string, unknown> {
  switch (type) {
    case "agent":
      return {
        name: "",
        description: "",
        intent: "",
        role: "specialist",
        instructions: "",
        context: {},
        connectedModel: "",
        connectedDesignPattern: "",
        connectedTools: [],
        connectedPromptSkills: [],
        connectedContextSkills: [],
        connectedMemory: "",
      };
    case "model":
      return { model: "gpt-4o-mini", temperature: 0.7, max_tokens: 4096 };
    case "designPattern":
      return { type: "react", max_steps: 10 };
    case "tool":
      return { tools: [] };
    case "promptSkills":
      return { skills: [] };
    case "contextSkills":
      return { skills: [] };
    case "memory":
      return { type: "", path: "" };
    case "textInput":
      return { text: "" };
    default:
      return {};
  }
}

function getDisplayValueForSourceNode(node: Node): {
  handleId: string;
  value: string | string[];
} {
  const data = node.data as Record<string, unknown>;
  switch (node.type) {
    case "model":
      return {
        handleId: "model",
        value: (data.model as string) || "",
      };
    case "designPattern": {
      const patternType = (data.type as string) || "";
      const maxIter = data.max_steps as number;
      return {
        handleId: "design_pattern",
        value: patternType ? `${patternType} (max: ${maxIter})` : "",
      };
    }
    case "tool":
      return {
        handleId: "tools",
        value: normalizeToolRefs(data.tools),
      };
    case "promptSkills":
      return {
        handleId: "prompt_skills",
        value: (data.skills as string[]) || [],
      };
    case "contextSkills":
      return {
        handleId: "context_skills",
        value: (data.skills as string[]) || [],
      };
    case "memory": {
      const memType = (data.type as string) || "";
      const path = (data.path as string) || "";
      return {
        handleId: "memory",
        value: memType ? (path ? `${memType} (${path})` : memType) : "",
      };
    }
    case "textInput":
      return {
        handleId: "text_input",
        value: (data.text as string) || "",
      };
    default:
      return { handleId: "", value: "" };
  }
}

function deriveAgentConnectedData(
  agentNodeId: string,
  allNodes: Node[],
  allEdges: Edge[],
): Record<string, unknown> {
  const incomingEdges = allEdges.filter((e) => e.target === agentNodeId);
  const connected: Record<string, unknown> = {
    connectedModel: "",
    connectedDesignPattern: "",
    connectedTools: [] as string[],
    connectedPromptSkills: [] as string[],
    connectedContextSkills: [] as string[],
    connectedMemory: "",
    connectedTextInput: "",
  };

  for (const edge of incomingEdges) {
    const sourceNode = allNodes.find((n) => n.id === edge.source);
    if (!sourceNode) continue;

    // Agent output → text_input slot
    if (
      sourceNode.type === "agent" &&
      edge.targetHandle === HANDLE_IDS.textInput
    ) {
      const agentName = (sourceNode.data as Record<string, unknown>)
        .name as string;
      connected.connectedTextInput = agentName
        ? `Agent: ${agentName}`
        : "Agent output";
      continue;
    }

    const { handleId, value } = getDisplayValueForSourceNode(sourceNode);
    switch (handleId) {
      case "model":
        connected.connectedModel = value;
        break;
      case "design_pattern":
        connected.connectedDesignPattern = value;
        break;
      case "tools":
        connected.connectedTools = value;
        break;
      case "prompt_skills":
        connected.connectedPromptSkills = value;
        break;
      case "context_skills":
        connected.connectedContextSkills = value;
        break;
      case "memory":
        connected.connectedMemory = value;
        break;
      case "text_input":
        connected.connectedTextInput = value;
        break;
    }
  }

  return connected;
}

const NODE_TYPE_TO_HANDLE_ID: Record<string, string> = HANDLE_IDS;

function buildAgentManifest(
  agentNode: Node,
  allNodes: Node[],
  allEdges: Edge[],
): Record<string, unknown> {
  const agentData = agentNode.data as Record<string, unknown>;
  const incomingEdges = allEdges.filter(
    (e) => e.target === agentNode.id && e.targetHandle !== HANDLE_IDS.textInput,
  );
  const textInputEdges = allEdges.filter(
    (e) => e.target === agentNode.id && e.targetHandle === HANDLE_IDS.textInput,
  );

  const agentId = getAgentId(agentNode);
  const metadata: Record<string, unknown> = {
    "x-node-id": agentNode.id,
    name: agentId,
  };
  const spec: Record<string, unknown> = {};
  const canvasNodeIds: Record<string, string> = {};

  const context = { ...((agentData.context as Record<string, string>) ?? {}) };
  const description = agentData.description as string | undefined;
  const instructions = agentData.instructions as string | undefined;
  if (description?.trim()) {
    spec.description = description.trim();
  }
  if (instructions?.trim()) {
    context.role = instructions.trim();
  }
  const intent = agentData.intent as string | undefined;
  if (intent?.trim()) {
    context.intent = intent.trim();
  }
  if (Object.keys(context).length > 0) {
    spec.context = context;
  }

  // TextInput node → x-text-input (used as query for Run MAS)
  for (const edge of textInputEdges) {
    const sourceNode = allNodes.find((n) => n.id === edge.source);
    if (!sourceNode) continue;
    const data = sourceNode.data as Record<string, unknown>;
    if (sourceNode.type === "textInput") {
      const text = data.text as string;
      if (text) {
        if (data.disabled) {
          spec["x-role-enabled"] = false;
          spec["x-disabled-role"] = { text };
        } else {
          spec["x-text-input"] = text;
        }
        canvasNodeIds.role = sourceNode.id;
      }
    }
  }

  for (const edge of incomingEdges) {
    const sourceNode = allNodes.find((n) => n.id === edge.source);
    if (!sourceNode) continue;
    const data = sourceNode.data as Record<string, unknown>;

    switch (sourceNode.type) {
      case "model": {
        const model = data.model as string;
        if (model) {
          const modelEntry: Record<string, unknown> = { model };
          if (data.temperature != null)
            modelEntry.temperature = data.temperature;
          if (data.max_tokens != null) modelEntry.max_tokens = data.max_tokens;
          if (data.disabled) {
            spec["x-models-enabled"] = false;
            spec["x-disabled-models"] = [modelEntry];
          } else {
            spec.models = [modelEntry];
          }
          canvasNodeIds.model = sourceNode.id;
        }
        break;
      }
      case "designPattern": {
        const patternType = data.type as string;
        if (patternType) {
          const dp: Record<string, unknown> = { type: patternType };
          if (data.max_steps != null) {
            dp.params = { max_steps: data.max_steps };
          }
          if (data.disabled) {
            spec["x-design-pattern-enabled"] = false;
            spec["x-disabled-design-pattern"] = dp;
          } else {
            spec.design_pattern = dp;
          }
          canvasNodeIds.design_pattern = sourceNode.id;
        }
        break;
      }
      case "tool": {
        const tools = data.tools as string[];
        if (tools && tools.length > 0) {
          const refs = toolDisplayNamesToRefs(tools);
          if (data.disabled) {
            spec["x-tools-enabled"] = false;
            spec["x-disabled-tools"] = refs;
          } else {
            spec.tools = refs;
          }
          canvasNodeIds.tools = sourceNode.id;
        }
        break;
      }
      case "promptSkills": {
        const skills = data.skills as string[];
        if (skills && skills.length > 0) {
          if (data.disabled) {
            spec["x-prompt-skills-enabled"] = false;
            spec["x-disabled-prompt-skills"] = skills;
          } else {
            spec.skills = skills;
          }
          canvasNodeIds.promptSkills = sourceNode.id;
        }
        break;
      }
      case "contextSkills": {
        const skills = data.skills as string[];
        if (skills && skills.length > 0) {
          if (data.disabled) {
            spec["x-context-skills-enabled"] = false;
            spec["x-disabled-context-skills"] = skills;
          } else {
            spec.context_manager = { skills: [...skills] };
          }
          canvasNodeIds.contextSkills = sourceNode.id;
        }
        break;
      }
      case "memory": {
        const memType = data.type as string;
        if (memType) {
          if (data.disabled) {
            spec["x-memory-enabled"] = false;
            spec["x-disabled-memory"] = memType;
          } else {
            spec.memory = memType;
          }
          canvasNodeIds.memory = sourceNode.id;
        }
        break;
      }
    }
  }

  const chatHistory = agentData.chatHistory as
    | Array<{ id: string; role: string; content: string }>
    | undefined;

  const manifest: Record<string, unknown> = {
    apiVersion: "mas/v1",
    kind: "Agent",
    metadata,
    spec,
  };

  if (Object.keys(canvasNodeIds).length > 0) {
    manifest["x-canvas-node-ids"] = canvasNodeIds;
  }

  if (chatHistory && chatHistory.length > 0) {
    manifest["x-chat-history"] = chatHistory;
  }

  return manifest;
}

/** Derive a stable kebab-case ID from the agent's user-provided name. */
function getAgentId(agentNode: Node): string {
  const data = agentNode.data as Record<string, unknown>;
  const name = (data.name as string) || "";
  return name.replace(/\s+/g, "-").toLowerCase();
}

/** Compute in-degree and out-degree for each agent node in the agent-to-agent edge subgraph. */
function buildDegrees(
  agentNodes: Node[],
  agentEdges: Edge[],
): { outDegree: Map<string, number>; inDegree: Map<string, number> } {
  const outDegree = new Map<string, number>();
  const inDegree = new Map<string, number>();
  for (const node of agentNodes) {
    outDegree.set(node.id, 0);
    inDegree.set(node.id, 0);
  }
  for (const edge of agentEdges) {
    outDegree.set(edge.source, (outDegree.get(edge.source) ?? 0) + 1);
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
  }
  return { outDegree, inDegree };
}

/**
 * BFS over the undirected agent graph to find connected components
 * (groups of agents linked by edges but disconnected from other groups).
 * Returns a map from node ID → size of its connected component.
 *
 * Example: 5 agents on canvas, A → B → C and D → E (no edges to A/B/C).
 * That yields two connected components: {A,B,C} (size 3) and {D,E} (size 2).
 * Returned map: { A: 3, B: 3, C: 3, D: 2, E: 2 }
 *
 * findEntryAgent uses component size as a tiebreaker — it prefers to pick
 * the entry agent from the largest group ({A,B,C}), since that's likely
 * the "main" workflow rather than a smaller side chain.
 */
function getConnectedComponentSizes(
  agentNodes: Node[],
  agentEdges: Edge[],
): Map<string, number> {
  const adj = new Map<string, Set<string>>();
  for (const node of agentNodes) adj.set(node.id, new Set());
  for (const edge of agentEdges) {
    adj.get(edge.source)?.add(edge.target);
    adj.get(edge.target)?.add(edge.source);
  }

  const visited = new Set<string>();
  const componentSize = new Map<string, number>();

  for (const node of agentNodes) {
    if (visited.has(node.id)) continue;
    const queue = [node.id];
    const component: string[] = [];
    while (queue.length > 0) {
      const current = queue.pop()!;
      if (visited.has(current)) continue;
      visited.add(current);
      component.push(current);
      for (const neighbor of adj.get(current) ?? []) {
        if (!visited.has(neighbor)) queue.push(neighbor);
      }
    }
    for (const id of component) componentSize.set(id, component.length);
  }

  return componentSize;
}

/**
 * Pick the best coordinator/entry agent using a weighted scoring heuristic:
 *   - Root bonus (+10 000): no incoming agent edges — strongest signal
 *   - Out-degree  (+100 each): delegates to more agents → more likely an orchestrator
 *   - Component size (+1 each): prefer agents in the largest connected subgraph
 *
 * Handles edge cases: circular graphs (no roots), disconnected subgraphs,
 * and multiple roots (picks the one with the most delegates).
 */
function findEntryAgent(agentNodes: Node[], agentEdges: Edge[]): Node {
  if (agentNodes.length === 1) return agentNodes[0];

  const { outDegree, inDegree } = buildDegrees(agentNodes, agentEdges);
  const componentSizes = getConnectedComponentSizes(agentNodes, agentEdges);

  const scored = agentNodes.map((node) => {
    const data = node.data as Record<string, unknown>;
    const isModerator = data.role === "moderator" ? 1 : 0;
    const isRoot = (inDegree.get(node.id) ?? 0) === 0 ? 1 : 0;
    const out = outDegree.get(node.id) ?? 0;
    const compSize = componentSizes.get(node.id) ?? 1;
    return {
      node,
      score: isModerator * 100000 + isRoot * 10000 + out * 100 + compSize,
    };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored[0].node;
}

function serializeGraphToYamls(
  nodes: Node[],
  edges: Edge[],
  masName?: string,
): Record<string, string> {
  const agentNodes = nodes.filter((n) => n.type === "agent");
  if (agentNodes.length === 0) return {};

  const result: Record<string, string> = {};

  // Build and serialize individual agent manifests
  for (const agentNode of agentNodes) {
    const agentId = getAgentId(agentNode);
    const manifest = buildAgentManifest(agentNode, nodes, edges);
    result[`agent:${agentId}`] = stringify(manifest);
  }

  // Collect agent-to-agent edges (output → text_input connections)
  const agentEdges = edges.filter(
    (e) =>
      e.sourceHandle === AGENT_OUTPUT_HANDLE &&
      e.targetHandle === HANDLE_IDS.textInput &&
      agentNodes.some((n) => n.id === e.source) &&
      agentNodes.some((n) => n.id === e.target),
  );

  // Build MAS manifest whenever at least one agent exists
  if (agentNodes.length >= 1) {
    const entryNode = findEntryAgent(agentNodes, agentEdges);
    const entryAgentId = getAgentId(entryNode);

    const MAS_NAME_PLACEHOLDER = "__MAS_NAME__";
    const effectiveMasName = masName || MAS_NAME_PLACEHOLDER;

    // spec.agency.agents: lightweight list with id + ref only
    const agents = agentNodes.map((agentNode) => {
      const agentId = getAgentId(agentNode);
      return {
        id: agentId,
        ref: `agents/${agentId}.yaml`,
      };
    });

    // workflow.nodes: describes each agent's role and delegation targets
    const workflowNodes = agentNodes.map((agentNode) => {
      const agentId = getAgentId(agentNode);
      const data = agentNode.data as Record<string, unknown>;
      const role = (data.role as string) || "specialist";

      const delegatesTo = agentEdges
        .filter((e) => e.source === agentNode.id)
        .map((e) => {
          const target = agentNodes.find((n) => n.id === e.target);
          return target ? getAgentId(target) : null;
        })
        .filter(Boolean) as string[];

      const node: Record<string, unknown> = { id: agentId, role };
      if (delegatesTo.length > 0) node.delegates_to = delegatesTo;
      return node;
    });

    // Build canvas positions for connected nodes only (agents + their inputs)
    const connectedInputIds = new Set<string>();
    for (const edge of edges) {
      const targetNode = nodes.find((n) => n.id === edge.target);
      if (targetNode?.type === "agent") {
        connectedInputIds.add(edge.source);
      }
    }
    const agentNodeIds = new Set(agentNodes.map((n) => n.id));

    const canvasPositions: Record<string, { x: number; y: number }> = {};
    for (const node of nodes) {
      if (!agentNodeIds.has(node.id) && !connectedInputIds.has(node.id))
        continue;
      canvasPositions[node.id] = {
        x: Math.round(node.position.x),
        y: Math.round(node.position.y),
      };
    }

    const workflow: Record<string, unknown> = {
      entry: entryAgentId,
      nodes: workflowNodes,
    };

    const masManifest = {
      apiVersion: "mas/v1",
      kind: "MAS",
      metadata: {
        name: effectiveMasName,
      },
      spec: {
        agency: { agents },
        workflow,
      },
      "x-canvas-positions": canvasPositions,
    };

    result["mas"] = stringify(masManifest);
  }

  return result;
}

function deserializeYamlsToGraph(yamlMap: YamlOutputMap): {
  nodes: Node[];
  edges: Edge[];
} {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  const masYaml = yamlMap["mas"];
  const masDoc = masYaml ? parse(masYaml) : null;
  const canvasPositions: Record<string, { x: number; y: number }> =
    masDoc?.["x-canvas-positions"] ?? {};

  const workflowNodes: {
    id: string;
    role?: string;
    delegates_to?: string[];
  }[] = masDoc?.spec?.workflow?.nodes ?? [];

  const agentKeys = Object.keys(yamlMap).filter((k) => k.startsWith("agent:"));
  const agentIdToNodeId = new Map<string, string>();

  const INPUT_NODE_X_OFFSET = -320;
  const INPUT_NODE_Y_SPACING = 120;

  for (const key of agentKeys) {
    const agentId = key.replace("agent:", "");
    const doc = parse(yamlMap[key]);
    const spec = doc?.spec ?? {};
    const metadata = doc?.metadata ?? {};
    const canvasNodeIds: Record<string, string> =
      doc?.["x-canvas-node-ids"] ?? {};

    const agentNodeId = metadata["x-node-id"] ?? agentId;
    const position = canvasPositions[agentNodeId] ??
      canvasPositions[agentId] ?? { x: 0, y: 0 };
    agentIdToNodeId.set(agentId, agentNodeId);

    const workflowEntry = workflowNodes.find((n) => n.id === agentId);
    const role = workflowEntry?.role ?? "specialist";

    const chatHistory = doc?.["x-chat-history"] ?? [];

    const agentNode: Node = {
      id: agentNodeId,
      type: "agent",
      position,
      data: {
        name: metadata.name ?? "",
        description: spec.description ?? "",
        intent: normalizeIntent(
          (spec.context as Record<string, unknown> | undefined)?.intent,
        ),
        role,
        instructions:
          typeof (spec.context as Record<string, unknown> | undefined)?.role ===
          "string"
            ? ((spec.context as Record<string, string>).role ?? "")
            : "",
        context: spec.context ?? {},
        chatHistory,
        connectedModel: "",
        connectedDesignPattern: "",
        connectedTools: [],
        connectedPromptSkills: [],
        connectedContextSkills: [],
        connectedMemory: "",
        connectedTextInput: "",
      },
    };
    nodes.push(agentNode);

    let inputOffsetIndex = 0;

    const modelsData =
      spec.models ??
      (spec["x-models-enabled"] === false ? spec["x-disabled-models"] : null);
    const modelsDisabled = spec["x-models-enabled"] === false;
    if (modelsData?.length > 0) {
      const m = modelsData[0];
      const modelNodeId = canvasNodeIds.model ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      nodes.push({
        id: modelNodeId,
        type: "model",
        position: canvasPositions[modelNodeId] ?? fallback,
        data: {
          model: m.model ?? "",
          temperature: m.temperature ?? 0.7,
          max_tokens: m.max_tokens ?? 4096,
          ...(modelsDisabled && { disabled: true }),
        },
      });
      edges.push({
        id: `e_${modelNodeId}_${agentNodeId}`,
        source: modelNodeId,
        target: agentNodeId,
        sourceHandle: `model-out-${modelNodeId}`,
        targetHandle: HANDLE_IDS.model,
      });
      inputOffsetIndex++;
    }

    const dpData =
      spec.design_pattern ??
      (spec["x-design-pattern-enabled"] === false
        ? spec["x-disabled-design-pattern"]
        : null);
    const dpDisabled = spec["x-design-pattern-enabled"] === false;
    if (dpData) {
      const dpNodeId = canvasNodeIds.design_pattern ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      nodes.push({
        id: dpNodeId,
        type: "designPattern",
        position: canvasPositions[dpNodeId] ?? fallback,
        data: {
          type: dpData.type ?? "",
          max_steps: dpData.params?.max_steps ?? dpData.config?.max_steps ?? 10,
          ...(dpDisabled && { disabled: true }),
        },
      });
      edges.push({
        id: `e_${dpNodeId}_${agentNodeId}`,
        source: dpNodeId,
        target: agentNodeId,
        sourceHandle: `dp-out-${dpNodeId}`,
        targetHandle: HANDLE_IDS.designPattern,
      });
      inputOffsetIndex++;
    }

    const toolsArray =
      spec.tools ??
      (spec["x-tools-enabled"] === false ? spec["x-disabled-tools"] : null);
    const toolsDisabled = spec["x-tools-enabled"] === false;
    if (toolsArray?.length > 0) {
      const toolNodeId = canvasNodeIds.tools ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      nodes.push({
        id: toolNodeId,
        type: "tool",
        position: canvasPositions[toolNodeId] ?? fallback,
        data: {
          tools: normalizeToolRefs(toolsArray),
          ...(toolsDisabled && { disabled: true }),
        },
      });
      edges.push({
        id: `e_${toolNodeId}_${agentNodeId}`,
        source: toolNodeId,
        target: agentNodeId,
        sourceHandle: `tool-out-${toolNodeId}`,
        targetHandle: HANDLE_IDS.tool,
      });
      inputOffsetIndex++;
    }

    const promptSkillsArray =
      spec.skills ??
      (spec["x-prompt-skills-enabled"] === false
        ? spec["x-disabled-prompt-skills"]
        : null);
    const promptSkillsDisabled = spec["x-prompt-skills-enabled"] === false;
    if (promptSkillsArray?.length > 0) {
      const nodeId = canvasNodeIds.promptSkills ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      nodes.push({
        id: nodeId,
        type: "promptSkills",
        position: canvasPositions[nodeId] ?? fallback,
        data: {
          skills: promptSkillsArray,
          ...(promptSkillsDisabled && { disabled: true }),
        },
      });
      edges.push({
        id: `e_${nodeId}_${agentNodeId}`,
        source: nodeId,
        target: agentNodeId,
        sourceHandle: `promptSkills-out-${nodeId}`,
        targetHandle: HANDLE_IDS.promptSkills,
      });
      inputOffsetIndex++;
    }

    const contextSkillsArray =
      spec.context_manager?.skills ??
      (spec["x-context-skills-enabled"] === false
        ? spec["x-disabled-context-skills"]
        : null);
    const contextSkillsDisabled = spec["x-context-skills-enabled"] === false;
    if (contextSkillsArray?.length > 0) {
      const nodeId = canvasNodeIds.contextSkills ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      nodes.push({
        id: nodeId,
        type: "contextSkills",
        position: canvasPositions[nodeId] ?? fallback,
        data: {
          skills: contextSkillsArray,
          ...(contextSkillsDisabled && { disabled: true }),
        },
      });
      edges.push({
        id: `e_${nodeId}_${agentNodeId}`,
        source: nodeId,
        target: agentNodeId,
        sourceHandle: `contextSkills-out-${nodeId}`,
        targetHandle: HANDLE_IDS.contextSkills,
      });
      inputOffsetIndex++;
    }

    const memType =
      spec.memory ??
      (spec["x-memory-enabled"] === false ? spec["x-disabled-memory"] : null);
    const memDisabled = spec["x-memory-enabled"] === false;
    if (memType) {
      const memNodeId = canvasNodeIds.memory ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      const memTypeStr =
        typeof memType === "string" ? memType : (memType.type ?? "");
      nodes.push({
        id: memNodeId,
        type: "memory",
        position: canvasPositions[memNodeId] ?? fallback,
        data: {
          type: memTypeStr,
          path: "",
          ...(memDisabled && { disabled: true }),
        },
      });
      edges.push({
        id: `e_${memNodeId}_${agentNodeId}`,
        source: memNodeId,
        target: agentNodeId,
        sourceHandle: `memory-out-${memNodeId}`,
        targetHandle: HANDLE_IDS.memory,
      });
      inputOffsetIndex++;
    }

    const textInputValue =
      spec["x-text-input"] ??
      (spec["x-role-enabled"] === false ? spec["x-disabled-role"]?.text : null);
    const roleDisabled = spec["x-role-enabled"] === false;
    if (textInputValue) {
      const textNodeId = canvasNodeIds.role ?? generateNodeId();
      const fallback = {
        x: position.x + INPUT_NODE_X_OFFSET,
        y: position.y + INPUT_NODE_Y_SPACING * inputOffsetIndex,
      };
      nodes.push({
        id: textNodeId,
        type: "textInput",
        position: canvasPositions[textNodeId] ?? fallback,
        data: { text: textInputValue, ...(roleDisabled && { disabled: true }) },
      });
      edges.push({
        id: `e_${textNodeId}_${agentNodeId}`,
        source: textNodeId,
        target: agentNodeId,
        sourceHandle: `text-input-out-${textNodeId}`,
        targetHandle: HANDLE_IDS.textInput,
      });
    }
  }

  for (const wfNode of workflowNodes) {
    if (!wfNode.delegates_to) continue;
    const sourceNodeId = agentIdToNodeId.get(wfNode.id);
    if (!sourceNodeId) continue;
    for (const targetAgentId of wfNode.delegates_to) {
      const targetNodeId = agentIdToNodeId.get(targetAgentId);
      if (!targetNodeId) continue;
      edges.push({
        id: `e_${sourceNodeId}_${targetNodeId}_delegation`,
        source: sourceNodeId,
        target: targetNodeId,
        sourceHandle: AGENT_OUTPUT_HANDLE,
        targetHandle: HANDLE_IDS.textInput,
      });
    }
  }

  return { nodes, edges };
}

function CanvasFlow({
  onYamlChange,
  onAgentSelect,
  initialYamlMap,
  masName: masNameProp,
}: {
  onYamlChange?: (yamls: Record<string, string>) => void;
  onAgentSelect?: (agentName: string | null) => void;
  initialYamlMap?: YamlOutputMap;
  masName?: string;
}) {
  const { library = "" } = useParams();
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const initialGraph = useMemo(
    () =>
      initialYamlMap
        ? deserializeYamlsToGraph(autoLayoutYamlMap(initialYamlMap))
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(
    initialGraph?.nodes ?? [],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    initialGraph?.edges ?? [],
  );

  const hasInitialized = useRef(
    initialGraph !== null && initialGraph.nodes.length > 0,
  );
  useEffect(() => {
    if (hasInitialized.current) return;
    if (!initialYamlMap || Object.keys(initialYamlMap).length === 0) return;
    const graph = deserializeYamlsToGraph(autoLayoutYamlMap(initialYamlMap));
    if (graph.nodes.length === 0) return;
    setNodes(graph.nodes);
    setEdges(graph.edges);
    hasInitialized.current = true;
  }, [initialYamlMap, setNodes, setEdges]);

  const { screenToFlowPosition } = useReactFlow();
  const [type] = useDnD();

  const nodeTypes: NodeTypes = useMemo(
    () => ({
      agent: AgentNode,
      model: ModelNode,
      designPattern: DesignPatternNode,
      tool: ToolNode,
      promptSkills: PromptSkillNode,
      contextSkills: ContextSkillNode,
      memory: MemoryNode,
      textInput: TextInputNode,
    }),
    [],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);

      if (!sourceNode || !targetNode) return false;

      // Prevent self-connections
      if (sourceNode.id === targetNode.id) return false;

      // Agent output → another Agent's text_input
      if (
        sourceNode.type === "agent" &&
        targetNode.type === "agent" &&
        connection.sourceHandle === AGENT_OUTPUT_HANDLE &&
        connection.targetHandle === HANDLE_IDS.textInput
      ) {
        return true;
      }

      // Standard input-node → Agent connections
      if (targetNode.type !== "agent") return false;

      // Only allow connections FROM the 4 input node types
      if (!sourceNode.type || !(sourceNode.type in NODE_TYPE_TO_HANDLE_ID))
        return false;

      // The target handle must match the source node type
      const expectedHandle = NODE_TYPE_TO_HANDLE_ID[sourceNode.type];
      if (connection.targetHandle !== expectedHandle) return false;

      // Prevent connecting a second node of the same type to the same agent
      const alreadyConnected = edges.some(
        (e) =>
          e.target === targetNode.id &&
          e.targetHandle === expectedHandle &&
          e.source !== sourceNode.id,
      );
      if (alreadyConnected) return false;

      return true;
    },
    [nodes, edges],
  );

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      if (!type) return;

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      const newNode: Node = {
        id: generateNodeId(),
        type,
        position,
        data: getDefaultDataForType(type),
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [screenToFlowPosition, type, setNodes],
  );

  const handleNodeDataChange = useCallback(
    (nodeId: string, field: string, value: unknown) => {
      setNodes((nds) =>
        nds.map((node) => {
          if (node.id === nodeId) {
            return {
              ...node,
              data: { ...node.data, [field]: value },
            };
          }
          return node;
        }),
      );
    },
    [setNodes],
  );

  const [chatAgentNodeId, setChatAgentNodeId] = useState<string | null>(null);

  const chatAgentName = useMemo(() => {
    if (!chatAgentNodeId) return null;
    const node = nodes.find((n) => n.id === chatAgentNodeId);
    if (!node) return null;
    return ((node.data as Record<string, unknown>).name as string) || "Agent";
  }, [chatAgentNodeId, nodes]);

  const chatAgentYaml = useMemo(() => {
    if (!chatAgentNodeId) return "";
    const node = nodes.find((n) => n.id === chatAgentNodeId);
    if (!node) return "";
    const agentId = getAgentId(node);
    const yamls = serializeGraphToYamls(nodes, edges, masNameProp);
    return yamls[`agent:${agentId}`] ?? "";
  }, [chatAgentNodeId, nodes, edges, masNameProp]);

  const chatInitialMessages = useMemo(() => {
    if (!chatAgentNodeId) return [];
    const node = nodes.find((n) => n.id === chatAgentNodeId);
    if (!node) return [];
    const data = node.data as Record<string, unknown>;
    return (data.chatHistory as ChatMessage[]) ?? [];
  }, [chatAgentNodeId]);

  const handleChatMessagesChange = useCallback(
    (msgs: ChatMessage[]) => {
      if (!chatAgentNodeId) return;
      handleNodeDataChange(chatAgentNodeId, "chatHistory", msgs);
    },
    [chatAgentNodeId, handleNodeDataChange],
  );

  const nodesWithDerivedData = useMemo(
    () =>
      nodes.map((node) => {
        const baseData: Record<string, unknown> = {
          ...node.data,
          onChange: (field: string, value: unknown) =>
            handleNodeDataChange(node.id, field, value),
        };

        if (node.type === "agent") {
          const connectedData = deriveAgentConnectedData(node.id, nodes, edges);
          Object.assign(baseData, connectedData);
          baseData.onChat = () => {
            setChatAgentNodeId(node.id);
          };
        }

        return { ...node, data: baseData };
      }),
    [nodes, edges, handleNodeDataChange],
  );

  useEffect(() => {
    if (onYamlChange) {
      const yamls = serializeGraphToYamls(nodes, edges, masNameProp);
      onYamlChange(yamls);
    }
  }, [nodes, edges, onYamlChange, masNameProp]);

  useEffect(() => {
    if (!onAgentSelect) return;
    const selectedAgents = nodes.filter(
      (n) => n.type === "agent" && n.selected,
    );
    if (selectedAgents.length === 1) {
      const agentId = getAgentId(selectedAgents[0]);
      onAgentSelect(agentId || null);
    } else {
      onAgentSelect(null);
    }
  }, [nodes, onAgentSelect]);

  return (
    <div className="canvas-builder">
      <div className="canvas-builder__flow" ref={reactFlowWrapper}>
        <ReactFlow
          nodes={nodesWithDerivedData}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onDrop={onDrop}
          onDragOver={onDragOver}
          nodeTypes={nodeTypes}
          proOptions={{ hideAttribution: true }}
          fitView
          snapToGrid
          snapGrid={[16, 16]}
          minZoom={0.1}
          maxZoom={4}
          colorMode="dark"
        >
          <Controls />
          <Background gap={16} size={1} color="#2e3447" />
          <MiniMap
            nodeStrokeWidth={3}
            zoomable
            pannable
            maskColor="rgba(18, 20, 31, 0.7)"
            style={{ background: "#1a1d2e" }}
          />
        </ReactFlow>
      </div>
      <Sidebar />
      <AgentChatDrawer
        open={chatAgentNodeId !== null}
        onClose={() => setChatAgentNodeId(null)}
        agentName={chatAgentName ?? ""}
        agentYaml={chatAgentYaml}
        library={library}
        initialMessages={chatInitialMessages}
        onMessagesChange={handleChatMessagesChange}
      />
    </div>
  );
}

export function CanvasBuilder({
  sourceYaml: _sourceYaml,
  initialYamlMap,
  masName,
  onYamlChange,
  onAgentSelect,
}: CanvasBuilderProps) {
  return (
    <ReactFlowProvider>
      <DnDProvider>
        <CanvasFlow
          onYamlChange={onYamlChange}
          onAgentSelect={onAgentSelect}
          initialYamlMap={initialYamlMap}
          masName={masName}
        />
      </DnDProvider>
    </ReactFlowProvider>
  );
}
