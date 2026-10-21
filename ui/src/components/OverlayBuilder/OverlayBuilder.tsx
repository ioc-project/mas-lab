//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useCallback, useRef, useMemo, useEffect, useState } from "react";
import { stringify, parse } from "yaml";
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
import { useScopeId } from "@/api/scope";

import { DnDProvider, useDnD } from "./DnDContext";
import { Sidebar } from "./Sidebar";
import {
  OverlayAgentNode,
  OverlayDesignPatternNode,
  OverlayToolNode,
  OverlayToolRemoveNode,
  OverlayInputPromptNode,
} from "./nodes";
import {
  HANDLE_IDS,
  type OverlayBuilderProps,
  type OverlayData,
  type OverlayNodeType,
} from "./types";
import { NamespaceProvider } from "./NamespaceContext";
import { useMasResources } from "@/api/apiCalls";
import "../CanvasBuilder/canvasBuilder.css";

function overlayToolDisplayNamesToRefs(tools: string[]): Array<{ ref: string }> {
  return tools.map((ref) => ({ ref }));
}

function normalizeOverlayToolRefs(tools: unknown): string[] {
  if (!Array.isArray(tools)) return [];
  return tools.map((entry) => {
    if (entry && typeof entry === "object") {
      const obj = entry as Record<string, unknown>;
      if (typeof obj.ref === "string") return obj.ref;
    }
    return String(entry);
  });
}

function generateNodeId(): string {
  return `node_${crypto.randomUUID().slice(0, 8)}`;
}

function getDefaultDataForType(type: string): Record<string, unknown> {
  switch (type) {
    case "agent":
      return {
        agentId: "",
        connectedDesignPattern: "",
        connectedTools: [],
        connectedToolsRemove: [],
        connectedInputPrompt: "",
      };
    case "designPattern":
      return { type: "", max_steps: 10 };
    case "tool":
      return { tools: [] };
    case "toolRemove":
      return { tools: [] };
    case "inputPrompt":
      return { text: "" };
    default:
      return {};
  }
}

const NODE_TYPE_TO_HANDLE_ID: Record<string, string> = {
  designPattern: HANDLE_IDS.designPattern,
  tool: HANDLE_IDS.tool,
  toolRemove: HANDLE_IDS.toolRemove,
  inputPrompt: HANDLE_IDS.inputPrompt,
};

function deriveAgentConnectedData(
  agentNodeId: string,
  nodes: Node[],
  edges: Edge[],
) {
  const incomingEdges = edges.filter((e) => e.target === agentNodeId);
  const result: Record<string, unknown> = {};

  for (const edge of incomingEdges) {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    if (!sourceNode) continue;
    const sourceData = sourceNode.data as Record<string, unknown>;

    switch (sourceNode.type) {
      case "designPattern": {
        const dpType = sourceData.type as string;
        if (dpType) {
          const maxSteps = sourceData.max_steps as number;
          result.connectedDesignPattern = `${dpType} (${maxSteps} steps)`;
        }
        break;
      }
      case "tool":
        result.connectedTools = sourceData.tools as string[];
        break;
      case "toolRemove":
        result.connectedToolsRemove = sourceData.tools as string[];
        break;
      case "inputPrompt":
        result.connectedInputPrompt = sourceData.text as string;
        break;
    }
  }

  return result;
}

function serializeOverlayToYaml(nodes: Node[], edges: Edge[], name?: string, description?: string, namespace?: string): string {
  const agents: OverlayData["agents"] = {};
  const canvasPositions: Record<string, { x: number; y: number }> = {};
  const canvasNodeIds: Record<string, Record<string, string>> = {};

  const agentNodes = nodes.filter((n) => n.type === "agent");

  for (const agentNode of agentNodes) {
    const agentData = agentNode.data as Record<string, unknown>;
    const agentId = (agentData.agentId as string) || "";
    if (!agentId) continue;

    canvasPositions[agentNode.id] = {
      x: Math.round(agentNode.position.x),
      y: Math.round(agentNode.position.y),
    };

    const nodeIdMap: Record<string, string> = { agent: agentNode.id };
    const incomingEdges = edges.filter((e) => e.target === agentNode.id);
    const agentOverride: OverlayData["agents"][string] = {};

    for (const edge of incomingEdges) {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      if (!sourceNode) continue;
      const sourceData = sourceNode.data as Record<string, unknown>;

      canvasPositions[sourceNode.id] = {
        x: Math.round(sourceNode.position.x),
        y: Math.round(sourceNode.position.y),
      };

      if (sourceData.disabled) continue;

      switch (sourceNode.type) {
        case "designPattern": {
          nodeIdMap.designPattern = sourceNode.id;
          const dpType = sourceData.type as string;
          if (dpType) {
            agentOverride.design_pattern = {
              type: dpType,
              config: { max_steps: sourceData.max_steps as number },
            };
          }
          break;
        }
        case "tool": {
          nodeIdMap.tool = sourceNode.id;
          const tools = sourceData.tools as string[];
          if (tools.length > 0) {
            agentOverride.tools = overlayToolDisplayNamesToRefs(tools);
          }
          break;
        }
        case "toolRemove": {
          nodeIdMap.toolRemove = sourceNode.id;
          const tools = sourceData.tools as string[];
          if (tools.length > 0) {
            agentOverride.tools_remove = overlayToolDisplayNamesToRefs(tools);
          }
          break;
        }
        case "inputPrompt": {
          nodeIdMap.inputPrompt = sourceNode.id;
          const text = sourceData.text as string;
          if (text) {
            agentOverride.context = {
              ...((agentOverride.context as Record<string, string> | undefined) ?? {}),
              role: text,
            };
          }
          break;
        }
      }
    }

    agents[agentId] = agentOverride;
    canvasNodeIds[agentId] = nodeIdMap;
  }

  if (Object.keys(agents).length === 0) return "";

  return stringify({
    apiVersion: "mas/v1",
    kind: "Overlay",
    metadata: {
      name: name || "untitled-overlay",
      ...(description ? { description } : {}),
    },
    spec: {
      target: { kind: "MAS" },
      patch: { agents },
    },
    "x-namespace": namespace || "global",
    "x-canvas-positions": canvasPositions,
    "x-canvas-node-ids": canvasNodeIds,
  }, { lineWidth: 120 });
}

function deserializeYamlToGraph(yaml: string): { nodes: Node[]; edges: Edge[]; description?: string; namespace?: string } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  let doc: Record<string, unknown>;
  try {
    doc = parse(yaml) as Record<string, unknown>;
  } catch {
    return { nodes, edges };
  }

  const metadata = doc?.metadata as Record<string, unknown> | undefined;
  const description = (metadata?.description as string) || undefined;
  const namespace = (doc["x-namespace"] as string) || undefined;

  const spec = doc?.spec as Record<string, unknown> | undefined;
  const patch = spec?.patch as Record<string, unknown> | undefined;
  const agents = patch?.agents as Record<string, Record<string, unknown>> | undefined;
  if (!agents) return { nodes, edges, description, namespace };

  const canvasPositions = (doc["x-canvas-positions"] ?? {}) as Record<
    string,
    { x: number; y: number }
  >;
  const canvasNodeIds = (doc["x-canvas-node-ids"] ?? {}) as Record<
    string,
    Record<string, string>
  >;

  const DEFAULT_AGENT_X = 500;
  const DEFAULT_INPUT_X = 50;
  const Y_SPACING = 350;
  const NODE_Y_GAP = 140;

  const agentIds = Object.keys(agents);

  agentIds.forEach((agentId, agentIndex) => {
    const defaultAgentY = agentIndex * Y_SPACING;
    const savedIds = canvasNodeIds[agentId] ?? {};

    const agentNodeId = savedIds.agent ?? generateNodeId();
    const agentPos = canvasPositions[agentNodeId] ?? {
      x: DEFAULT_AGENT_X,
      y: defaultAgentY,
    };

    nodes.push({
      id: agentNodeId,
      type: "agent",
      position: agentPos,
      data: {
        agentId,
        connectedDesignPattern: "",
        connectedTools: [],
        connectedToolsRemove: [],
        connectedInputPrompt: "",
      },
    });

    const agentCfg = agents[agentId];
    let inputOffset = 0;

    const dp = agentCfg.design_pattern as Record<string, unknown> | undefined;
    if (dp) {
      const dpNodeId = savedIds.designPattern ?? generateNodeId();
      const defaultPos = { x: DEFAULT_INPUT_X, y: defaultAgentY + inputOffset };
      const dpConfig = dp.config as Record<string, unknown> | undefined;
      nodes.push({
        id: dpNodeId,
        type: "designPattern",
        position: canvasPositions[dpNodeId] ?? defaultPos,
        data: {
          type: (dp.type as string) || "",
          max_steps: (dpConfig?.max_steps as number) ?? 10,
        },
      });
      edges.push({
        id: `e-${dpNodeId}-${agentNodeId}`,
        source: dpNodeId,
        sourceHandle: `dp-out-${dpNodeId}`,
        target: agentNodeId,
        targetHandle: "design_pattern",
      });
      inputOffset += NODE_Y_GAP;
    }

    const rawTools = agentCfg.tools as unknown[] | undefined;
    if (rawTools && rawTools.length > 0) {
      const toolNodeId = savedIds.tool ?? generateNodeId();
      const defaultPos = { x: DEFAULT_INPUT_X, y: defaultAgentY + inputOffset };
      nodes.push({
        id: toolNodeId,
        type: "tool",
        position: canvasPositions[toolNodeId] ?? defaultPos,
        data: { tools: normalizeOverlayToolRefs(rawTools) },
      });
      edges.push({
        id: `e-${toolNodeId}-${agentNodeId}`,
        source: toolNodeId,
        sourceHandle: `tool-out-${toolNodeId}`,
        target: agentNodeId,
        targetHandle: "tools",
      });
      inputOffset += NODE_Y_GAP;
    }

    const rawToolsRemove = agentCfg.tools_remove as unknown[] | undefined;
    if (rawToolsRemove && rawToolsRemove.length > 0) {
      const trNodeId = savedIds.toolRemove ?? generateNodeId();
      const defaultPos = { x: DEFAULT_INPUT_X, y: defaultAgentY + inputOffset };
      nodes.push({
        id: trNodeId,
        type: "toolRemove",
        position: canvasPositions[trNodeId] ?? defaultPos,
        data: { tools: normalizeOverlayToolRefs(rawToolsRemove) },
      });
      edges.push({
        id: `e-${trNodeId}-${agentNodeId}`,
        source: trNodeId,
        sourceHandle: `toolrm-out-${trNodeId}`,
        target: agentNodeId,
        targetHandle: "tools_remove",
      });
      inputOffset += NODE_Y_GAP;
    }

    const context = agentCfg.context as Record<string, unknown> | undefined;
    const instructions =
      typeof context?.role === "string" ? (context.role as string) : undefined;
    if (instructions) {
      const promptNodeId = savedIds.inputPrompt ?? generateNodeId();
      const defaultPos = { x: DEFAULT_INPUT_X, y: defaultAgentY + inputOffset };
      nodes.push({
        id: promptNodeId,
        type: "inputPrompt",
        position: canvasPositions[promptNodeId] ?? defaultPos,
        data: { text: instructions },
      });
      edges.push({
        id: `e-${promptNodeId}-${agentNodeId}`,
        source: promptNodeId,
        sourceHandle: `prompt-out-${promptNodeId}`,
        target: agentNodeId,
        targetHandle: "input_prompt",
      });
    }
  });

  return { nodes, edges, description, namespace };
}

function NamespaceSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (ns: string) => void;
}) {
  const library = useScopeId();
  const { data: masResources } = useMasResources(library);
  const appNames = useMemo(
    () => (masResources ? Object.keys(masResources) : []),
    [masResources],
  );

  return (
    <div className="pipeline-builder__toolbar">
      <label>Namespace:</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="global">Global</option>
        {appNames.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}

function OverlayFlow({
  overlayName,
  initialData,
  onYamlChange,
}: {
  overlayName?: string;
  initialData?: OverlayBuilderProps["initialData"];
  onYamlChange?: (yaml: string) => void;
}) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const initialGraph = useMemo(
    () => (initialData?.yaml ? deserializeYamlToGraph(initialData.yaml) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [selectedNamespace, setSelectedNamespaceRaw] = useState<string>(
    initialGraph?.namespace ?? "global",
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(
    initialGraph?.nodes ?? [],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    initialGraph?.edges ?? [],
  );

  const setSelectedNamespace = useCallback(
    (ns: string) => {
      setSelectedNamespaceRaw(ns);
      setNodes((nds) =>
        nds.map((node) => {
          if (node.type === "tool" || node.type === "toolRemove") {
            return { ...node, data: { ...node.data, tools: [] } };
          }
          return node;
        }),
      );
    },
    [setNodes],
  );

  const { screenToFlowPosition } = useReactFlow();
  const [type] = useDnD();

  const nodeTypes: NodeTypes = useMemo(
    () => ({
      agent: OverlayAgentNode,
      designPattern: OverlayDesignPatternNode,
      tool: OverlayToolNode,
      toolRemove: OverlayToolRemoveNode,
      inputPrompt: OverlayInputPromptNode,
    }),
    [],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const targetNode = nodes.find((n) => n.id === connection.target);

      if (!sourceNode || !targetNode) return false;
      if (sourceNode.id === targetNode.id) return false;

      if (targetNode.type !== "agent") return false;
      if (!sourceNode.type || !(sourceNode.type in NODE_TYPE_TO_HANDLE_ID))
        return false;

      const expectedHandle = NODE_TYPE_TO_HANDLE_ID[sourceNode.type];
      if (connection.targetHandle !== expectedHandle) return false;

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
        type: type as OverlayNodeType,
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
        }

        return { ...node, data: baseData };
      }),
    [nodes, edges, handleNodeDataChange],
  );

  useEffect(() => {
    if (onYamlChange) {
      onYamlChange(serializeOverlayToYaml(nodes, edges, overlayName, initialGraph?.description, selectedNamespace));
    }
  }, [nodes, edges, onYamlChange, overlayName, selectedNamespace]);

  return (
    <NamespaceProvider value={selectedNamespace}>
      <div className="canvas-builder" style={{ flexDirection: "column" }}>
        <NamespaceSelector
          value={selectedNamespace}
          onChange={setSelectedNamespace}
        />
        <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
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
        </div>
      </div>
    </NamespaceProvider>
  );
}

export function OverlayBuilder({
  overlayName,
  initialData,
  onYamlChange,
}: OverlayBuilderProps) {
  return (
    <ReactFlowProvider>
      <DnDProvider>
        <OverlayFlow
          overlayName={overlayName}
          initialData={initialData}
          onYamlChange={onYamlChange}
        />
      </DnDProvider>
    </ReactFlowProvider>
  );
}
