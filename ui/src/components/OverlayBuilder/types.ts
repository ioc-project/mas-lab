//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { Node } from "@xyflow/react";

export type OverlayNodeType =
  | "agent"
  | "designPattern"
  | "tool"
  | "toolRemove"
  | "inputPrompt";

export type DnDNodeType = OverlayNodeType | null;

export type NodeDataChangeHandler = (field: string, value: unknown) => void;

export const HANDLE_IDS = {
  designPattern: "design_pattern",
  tool: "tools",
  toolRemove: "tools_remove",
  inputPrompt: "input_prompt",
} as const satisfies Record<Exclude<OverlayNodeType, "agent">, string>;

export const AGENT_OUTPUT_HANDLE = "agent_output" as const;

export type HandleId = (typeof HANDLE_IDS)[keyof typeof HANDLE_IDS];

export type OverlayAgentNodeData = {
  agentId: string;
  connectedDesignPattern?: string;
  connectedTools?: string[];
  connectedToolsRemove?: string[];
  connectedInputPrompt?: string;
  onChange?: NodeDataChangeHandler;
};

export type OverlayDesignPatternNodeData = {
  /** Manifest name for spec.design_pattern.type (e.g. "react", "deterministic_staged_debate"). */
  type: string;
  max_steps: number;
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type OverlayToolNodeData = {
  tools: string[];
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type OverlayToolRemoveNodeData = {
  tools: string[];
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export type OverlayInputPromptNodeData = {
  text: string;
  disabled?: boolean;
  onChange?: NodeDataChangeHandler;
};

export interface OverlayBuilderProps {
  overlayName?: string;
  initialData?: SavedOverlay | null;
  onYamlChange?: (yaml: string) => void;
}

export type ToolRefEntry = string | { ref: string };

export interface OverlayData {
  agents: Record<
    string,
    {
      design_pattern?: { type: string; config?: { max_steps?: number } };
      context?: { role?: string; intent?: string; tool_usage?: string };
      tools?: ToolRefEntry[];
      tools_remove?: ToolRefEntry[];
    }
  >;
}

export interface SavedOverlay {
  name: string;
  yaml: string;
}

export type OverlayAgentNodeType = Node<OverlayAgentNodeData, "agent">;
export type OverlayDesignPatternNodeType = Node<OverlayDesignPatternNodeData, "designPattern">;
export type OverlayToolNodeType = Node<OverlayToolNodeData, "tool">;
export type OverlayToolRemoveNodeType = Node<OverlayToolRemoveNodeData, "toolRemove">;
export type OverlayInputPromptNodeType = Node<OverlayInputPromptNodeData, "inputPrompt">;
