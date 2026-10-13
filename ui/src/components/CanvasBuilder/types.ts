//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { Edge, Node } from "@xyflow/react";

export type CanvasNodeType =
  | "agent"
  | "model"
  | "designPattern"
  | "tool"
  | "promptSkills"
  | "contextSkills"
  | "memory"
  | "textInput";

export type DnDNodeType = CanvasNodeType | null;

export type NodeDataChangeHandler = (field: string, value: unknown) => void;

export const HANDLE_IDS = {
  model: "model",
  designPattern: "design_pattern",
  tool: "tools",
  promptSkills: "prompt_skills",
  contextSkills: "context_skills",
  memory: "memory",
  textInput: "text_input",
} as const satisfies Record<Exclude<CanvasNodeType, "agent">, string>;

export const AGENT_OUTPUT_HANDLE = "agent_output" as const;

export type HandleId = (typeof HANDLE_IDS)[keyof typeof HANDLE_IDS];

export type AgentRole = "moderator" | "specialist";

// Node data types
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  /** Full technical detail shown on hover for error messages. */
  detail?: string;
}

export type AgentNodeData = {
  name: string;
  description: string;
  intent: string;
  role: AgentRole;
  instructions?: string;
  context?: Record<string, string>;
  chatHistory?: ChatMessage[];
  connectedModel?: string;
  connectedDesignPattern?: string;
  connectedTools?: string[];
  connectedPromptSkills?: string[];
  connectedContextSkills?: string[];
  connectedMemory?: string;
  connectedTextInput?: string;
  onChange?: NodeDataChangeHandler;
  onChat?: () => void;
};

export type ModelNodeData = {
  model: string;
  temperature: number;
  max_tokens: number;
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type DesignPatternNodeData = {
  /** Manifest name for spec.design_pattern.type (e.g. "react", "deterministic_staged_debate"). */
  type: string;
  max_steps: number;
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type ToolNodeData = {
  tools: string[];
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type PromptSkillsNodeData = {
  skills: string[];
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type ContextSkillsNodeData = {
  skills: string[];
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type MemoryNodeData = {
  type: "semantic" | "semantic-persistent" | "episodic" | "procedural" | "";
  path: string;
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type TextInputNodeData = {
  text: string;
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

/**
 * Map of generated YAML documents:
 * - Keys like "agent:<name>" hold per-agent manifests
 * - Key "mas" holds the MAS manifest (only when multiple agents are connected)
 */
export type YamlOutputMap = Record<string, string>;

// Component props
export interface CanvasBuilderProps {
  sourceYaml?: string;
  initialYamlMap?: YamlOutputMap;
  masName?: string;
  onYamlChange?: (yamls: YamlOutputMap) => void;
  onAgentSelect?: (agentName: string | null) => void;
}

// Serialized graph state for YAML conversion
export interface SerializedGraph {
  nodes: Node[];
  edges: Edge[];
}

// Typed node aliases
export type AgentNodeType = Node<AgentNodeData, "agent">;
export type ModelNodeType = Node<ModelNodeData, "model">;
export type DesignPatternNodeType = Node<
  DesignPatternNodeData,
  "designPattern"
>;
export type ToolNodeType = Node<ToolNodeData, "tool">;
export type PromptSkillsNodeType = Node<PromptSkillsNodeData, "promptSkills">;
export type ContextSkillsNodeType = Node<ContextSkillsNodeData, "contextSkills">;
export type MemoryNodeType = Node<MemoryNodeData, "memory">;
export type TextInputNodeType = Node<TextInputNodeData, "textInput">;
