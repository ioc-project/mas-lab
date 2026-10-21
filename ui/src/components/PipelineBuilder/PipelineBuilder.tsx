//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useCallback, useRef, useMemo, useEffect, useState } from "react";
import { stringify, parse } from "yaml";
import {
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  useNodesState,
  useEdgesState,
  Controls,
  Background,
  MiniMap,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeTypes,
  type Node,
  type OnEdgesChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { DnDProvider, useDnD } from "./DnDContext";
import { Sidebar } from "./Sidebar";
import { StepNode } from "./nodes";
import type { PipelineBuilderProps, StepNodeData } from "./types";
import { useExperiments, type PipelineStepTypeEntry } from "@/api/apiCalls";
import { useScopeId } from "@/api/scope";
import "./pipelineBuilder.css";

function generateNodeId(): string {
  return `pnode_${crypto.randomUUID().slice(0, 8)}`;
}

function serializeGraphToYaml(
  nodes: Node[],
  _edges: Edge[],
  experimentName: string,
  metadata?: Record<string, unknown>,
): string {
  const stepNodes = nodes
    .filter((n) => n.type === "step")
    .sort((a, b) => a.position.y - b.position.y);

  if (stepNodes.length === 0) return "";

  const steps: Record<string, unknown>[] = [];
  const canvasPositions: Record<string, { x: number; y: number }> = {};

  for (const node of stepNodes) {
    const data = node.data as StepNodeData;
    canvasPositions[node.id] = { x: node.position.x, y: node.position.y };

    const step: Record<string, unknown> = {
      name: data.name || "unnamed",
      type: data.type,
      "x-node-id": node.id,
    };

    if (data.depends_on.length > 0) {
      step.depends_on = [...data.depends_on];
    }

    if (data.phase && data.phase !== "post") {
      step.phase = data.phase;
    }

    const config = { ...data.config };
    const cleanConfig: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(config)) {
      if (v !== "" && v !== undefined && v !== null) {
        cleanConfig[k] = v;
      }
    }
    if (Object.keys(cleanConfig).length > 0) {
      step.config = cleanConfig;
    }

    steps.push(step);
  }

  const resolvedMetadata =
    metadata && Object.keys(metadata).length > 0
      ? metadata
      : { name: "__PIPELINE_NAME__" };
  if (!resolvedMetadata.name) {
    resolvedMetadata.name = "__PIPELINE_NAME__";
  }

  const pipeline: Record<string, unknown> = {
    api_version: "pipeline/v1",
    kind: "Pipeline",
    metadata: resolvedMetadata,
    spec: { steps },
    "x-canvas-positions": canvasPositions,
  };

  return stringify(pipeline, { lineWidth: 0 });
}

function deserializeYamlToGraph(yaml: string): {
  nodes: Node[];
  edges: Edge[];
  experimentName: string;
  metadata: Record<string, unknown>;
} {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  let doc: Record<string, unknown>;
  try {
    doc = parse(yaml);
  } catch {
    return { nodes: [], edges: [], experimentName: "", metadata: {} };
  }

  if (!doc || doc.kind !== "Pipeline")
    return { nodes: [], edges: [], experimentName: "", metadata: {} };

  const metadata = (doc.metadata as Record<string, unknown>) ?? {};
  const spec = doc.spec as Record<string, unknown> | undefined;
  const steps = (spec?.steps as Record<string, unknown>[]) ?? [];
  const canvasPositions =
    (doc["x-canvas-positions"] as Record<string, { x: number; y: number }>) ??
    {};

  const output = spec?.output as Record<string, unknown> | undefined;
  const baseDir = (output?.base_dir as string) ?? "";
  const match = baseDir.match(/mas\/labs\/(.+)$/);
  const experimentName = match?.[1] ?? "";

  const nameToNodeId = new Map<string, string>();
  const X_START = 100;
  const Y_START = 80;
  const Y_SPACING = 200;

  steps.forEach((step, i) => {
    const name = (step.name as string) ?? `step-${i}`;
    const storedNodeId = step["x-node-id"] as string | undefined;
    const actualNodeId =
      storedNodeId && canvasPositions[storedNodeId]
        ? storedNodeId
        : generateNodeId();

    nameToNodeId.set(name, actualNodeId);

    nodes.push({
      id: actualNodeId,
      type: "step",
      position: canvasPositions[actualNodeId] ?? {
        x: X_START,
        y: Y_START + i * Y_SPACING,
      },
      data: {
        name,
        type: (step.type as string) ?? "",
        phase: (step.phase as string) ?? "post",
        depends_on: (step.depends_on as string[]) ?? [],
        config: (step.config as Record<string, unknown>) ?? {},
      },
    });
  });

  for (const node of nodes) {
    const data = node.data as StepNodeData;
    for (const dep of data.depends_on) {
      const sourceNodeId = nameToNodeId.get(dep);
      if (sourceNodeId) {
        edges.push({
          id: `e_${sourceNodeId}_${node.id}`,
          source: sourceNodeId,
          target: node.id,
          sourceHandle: "dep-out",
          targetHandle: "dep-in",
        });
      }
    }
  }

  return { nodes, edges, experimentName, metadata };
}

function PipelineFlow({
  onYamlChange,
  onExperimentChange: onExperimentChangeProp,
  initialYaml,
  experimentName: initialExperiment = "",
}: {
  onYamlChange?: (yaml: string) => void;
  onExperimentChange?: (name: string) => void;
  initialYaml?: string;
  experimentName?: string;
}) {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  const initialGraph = useMemo(
    () => (initialYaml ? deserializeYamlToGraph(initialYaml) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(
    initialGraph?.nodes ?? [],
  );
  const [edges, setEdges] = useEdgesState<Edge>(initialGraph?.edges ?? []);

  const handleEdgesChange: OnEdgesChange<Edge> = useCallback(
    (changes) => {
      const removals = changes.filter(
        (c): c is EdgeChange & { type: "remove"; id: string } =>
          c.type === "remove",
      );

      if (removals.length > 0) {
        const removedIds = new Set(removals.map((c) => c.id));

        setEdges((currentEdges) => {
          const removedEdges = currentEdges.filter((e) => removedIds.has(e.id));

          if (removedEdges.length > 0) {
            setNodes((nds) =>
              nds.map((node) => {
                const data = node.data as StepNodeData;
                const depsToRemove = removedEdges
                  .filter((e) => e.target === node.id)
                  .map((e) => {
                    const srcNode = nds.find((n) => n.id === e.source);
                    return srcNode ? (srcNode.data as StepNodeData).name : null;
                  })
                  .filter(Boolean) as string[];

                if (depsToRemove.length === 0) return node;

                return {
                  ...node,
                  data: {
                    ...node.data,
                    depends_on: data.depends_on.filter(
                      (d) => !depsToRemove.includes(d),
                    ),
                  },
                };
              }),
            );
          }

          return applyEdgeChanges(changes, currentEdges);
        });
      } else {
        setEdges((eds) => applyEdgeChanges(changes, eds));
      }
    },
    [setEdges, setNodes],
  );

  const experimentNameRef = useRef(
    initialGraph?.experimentName ?? initialExperiment,
  );
  const metadataRef = useRef<Record<string, unknown>>(
    initialGraph?.metadata ?? {},
  );

  const hasInitialized = useRef(
    initialGraph !== null && initialGraph.nodes.length > 0,
  );

  useEffect(() => {
    if (hasInitialized.current) return;
    if (!initialYaml) return;
    const graph = deserializeYamlToGraph(initialYaml);
    if (graph.nodes.length === 0) return;
    setNodes(graph.nodes);
    setEdges(graph.edges);
    if (graph.experimentName) experimentNameRef.current = graph.experimentName;
    if (Object.keys(graph.metadata).length > 0)
      metadataRef.current = graph.metadata;
    hasInitialized.current = true;
  }, [initialYaml, setNodes, setEdges]);

  const { screenToFlowPosition } = useReactFlow();
  const [type] = useDnD();

  const nodeTypes: NodeTypes = useMemo(() => ({ step: StepNode }), []);

  const isValidConnection = useCallback(
    (connection: Edge | Connection) => {
      if (connection.source === connection.target) return false;
      const alreadyExists = edges.some(
        (e) => e.source === connection.source && e.target === connection.target,
      );
      return !alreadyExists;
    },
    [edges],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) => addEdge(params, eds));

      const targetNode = nodes.find((n) => n.id === params.target);
      const sourceNode = nodes.find((n) => n.id === params.source);
      if (targetNode && sourceNode) {
        const targetData = targetNode.data as StepNodeData;
        const sourceName = (sourceNode.data as StepNodeData).name;
        if (sourceName && !targetData.depends_on.includes(sourceName)) {
          setNodes((nds) =>
            nds.map((n) => {
              if (n.id === targetNode.id) {
                return {
                  ...n,
                  data: {
                    ...n.data,
                    depends_on: [...targetData.depends_on, sourceName],
                  },
                };
              }
              return n;
            }),
          );
        }
      }
    },
    [setEdges, nodes, setNodes],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      if (!type) return;

      const stepTypeJson = event.dataTransfer.getData("pipeline/step-type");
      let stepTypeDef: PipelineStepTypeEntry | undefined;
      try {
        stepTypeDef = JSON.parse(stepTypeJson);
      } catch {
        /* empty */
      }

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      const existingNames = nodes.map((n) => (n.data as StepNodeData).name);
      const baseName = stepTypeDef?.type?.replace(/_/g, "-") ?? "step";
      let name = baseName;
      let counter = 1;
      while (existingNames.includes(name)) {
        name = `${baseName}-${counter++}`;
      }

      const defaultConfig: Record<string, unknown> = {};
      if (stepTypeDef?.config) {
        for (const [key, fieldDef] of Object.entries(stepTypeDef.config)) {
          if (fieldDef.default !== undefined && fieldDef.default !== null) {
            defaultConfig[key] = fieldDef.default;
          }
        }
        if (
          "metric_kwargs" in stepTypeDef.config &&
          (!defaultConfig.metric_kwargs ||
            (typeof defaultConfig.metric_kwargs === "object" &&
              Object.keys(defaultConfig.metric_kwargs as object).length === 0))
        ) {
          defaultConfig.metric_kwargs = {
            model: "",
          };
        }
      }

      const newNode: Node = {
        id: generateNodeId(),
        type: "step",
        position,
        data: {
          name,
          type: stepTypeDef?.type ?? "",
          phase: stepTypeDef?.phase ?? "post",
          depends_on: [],
          config: defaultConfig,
        } satisfies StepNodeData,
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [screenToFlowPosition, type, setNodes, nodes],
  );

  const handleNodeDataChange = useCallback(
    (nodeId: string, field: string, value: unknown) => {
      setNodes((nds) =>
        nds.map((node) => {
          if (node.id === nodeId) {
            return { ...node, data: { ...node.data, [field]: value } };
          }
          return node;
        }),
      );
    },
    [setNodes],
  );

  const nodesWithHandlers = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          onChange: (field: string, value: unknown) =>
            handleNodeDataChange(node.id, field, value),
        },
      })),
    [nodes, handleNodeDataChange],
  );

  useEffect(() => {
    if (onYamlChange) {
      const yaml = serializeGraphToYaml(
        nodes,
        edges,
        experimentNameRef.current,
        metadataRef.current,
      );
      onYamlChange(yaml);
    }
  }, [nodes, edges, onYamlChange]);

  const handleExperimentChange = useCallback(
    (name: string) => {
      experimentNameRef.current = name;
      if (onYamlChange) {
        const yaml = serializeGraphToYaml(
          nodes,
          edges,
          name,
          metadataRef.current,
        );
        onYamlChange(yaml);
      }
      onExperimentChangeProp?.(name);
    },
    [nodes, edges, onYamlChange, onExperimentChangeProp],
  );

  return (
    <div className="pipeline-builder" style={{ flexDirection: "column" }}>
      <ExperimentSelector
        value={experimentNameRef.current}
        onChange={handleExperimentChange}
      />
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <div className="pipeline-builder__flow" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodesWithHandlers}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={handleEdgesChange}
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
  );
}

function ExperimentSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (name: string) => void;
}) {
  const [localValue, setLocalValue] = useState(value);

  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  const experiments = useExperimentNames();

  return (
    <div className="pipeline-builder__toolbar">
      <label>Experiment:</label>
      <select
        value={localValue}
        onChange={(e) => {
          setLocalValue(e.target.value);
          onChange(e.target.value);
        }}
      >
        <option value="">Select experiment...</option>
        {experiments.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}

function useExperimentNames(): string[] {
  const library = useScopeId();
  const { data: experiments = [] } = useExperiments(library);
  return experiments.map((e) => e.name);
}

export function PipelineBuilder({
  initialYaml,
  experimentName,
  onYamlChange,
  onExperimentChange,
}: PipelineBuilderProps) {
  return (
    <ReactFlowProvider>
      <DnDProvider>
        <PipelineFlow
          onYamlChange={onYamlChange}
          onExperimentChange={onExperimentChange}
          initialYaml={initialYaml}
          experimentName={experimentName}
        />
      </DnDProvider>
    </ReactFlowProvider>
  );
}
