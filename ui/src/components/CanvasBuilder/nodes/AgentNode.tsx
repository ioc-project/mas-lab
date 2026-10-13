//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, NodeResizer, type NodeProps } from "@xyflow/react";
import { useCallback, useState, type ChangeEvent } from "react";
import { AGENT_OUTPUT_HANDLE, type AgentNodeType } from "../types";

/**
 * Context values are usually plain strings, but some (e.g. ``role``) are ref
 * objects like ``{ ref: "prompts/sre/1.2.0.md" }``. Render those as their ref
 * path instead of the default ``[object Object]`` string coercion.
 */
function contextValueToText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  if (typeof value === "object" && "ref" in (value as object)) {
    return String((value as { ref: unknown }).ref);
  }
  return JSON.stringify(value);
}

export function AgentNode({ data, selected }: NodeProps<AgentNodeType>) {
  const [newKey, setNewKey] = useState("");

  const handleChange = useCallback(
    (field: string) =>
      (
        e: ChangeEvent<
          HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
        >,
      ) => {
        data.onChange?.(field, e.target.value);
      },
    [data],
  );

  const context: Record<string, string> = data.context ?? {};

  const handleAddContext = () => {
    const key = newKey.trim();
    if (!key) return;
    data.onChange?.("context", { ...context, [key]: "" });
    setNewKey("");
  };

  const handleRemoveContext = (key: string) => {
    const { [key]: _, ...rest } = context;
    data.onChange?.("context", rest);
  };

  const handleContextValueChange = (key: string, value: string) => {
    data.onChange?.("context", { ...context, [key]: value });
  };

  return (
    <div className="canvas-node agent-node">
      <NodeResizer isVisible={selected} minWidth={260} minHeight={200} />
      <div className="canvas-node__header agent-node__header">
        <span className="canvas-node__icon">🤖</span>
        <span className="canvas-node__title">Agent</span>
        <button
          className="agent-node__chat-btn nodrag"
          onClick={(e) => {
            e.stopPropagation();
            data.onChat?.();
          }}
          title="Chat with agent"
        >
          💬
        </button>
      </div>
      <div className="canvas-node__body">
        <label className="canvas-node__label">
          Name
          <input
            className="canvas-node__input nodrag"
            value={data.name}
            onChange={handleChange("name")}
            placeholder="agent-name"
          />
        </label>
        <label className="canvas-node__label">
          Description
          <textarea
            className="canvas-node__input nodrag"
            value={data.description}
            onChange={handleChange("description")}
            placeholder="Short role description"
            rows={2}
          />
        </label>
        <label className="canvas-node__label">
          Instructions
          <textarea
            className="canvas-node__input nodrag agent-node__instructions"
            value={data.instructions ?? ""}
            onChange={(e) => data.onChange?.("instructions", e.target.value)}
            placeholder="Detailed instructions for agent behavior"
            rows={3}
          />
        </label>
        <label className="canvas-node__label">
          Intent
          <textarea
            className="canvas-node__input nodrag"
            value={data.intent}
            onChange={handleChange("intent")}
            placeholder="What this agent does (used for delegation)"
            rows={2}
          />
        </label>

        <div className="agent-node__separator" />

        <div className="agent-node__context-section">
          <span className="agent-node__context-title">CONTEXT</span>
          {Object.entries(context).map(([key, value]) => (
            <div key={key} className="agent-node__context-entry">
              <div className="agent-node__context-entry-header">
                <span className="agent-node__context-key" title={key}>
                  {key}
                </span>
                <button
                  className="agent-node__context-remove nodrag"
                  onClick={() => handleRemoveContext(key)}
                >
                  ×
                </button>
              </div>
              <textarea
                className="canvas-node__input nodrag agent-node__context-value"
                value={contextValueToText(value)}
                onChange={(e) => handleContextValueChange(key, e.target.value)}
                rows={2}
              />
            </div>
          ))}
          <div className="agent-node__context-add">
            <input
              className="canvas-node__input nodrag agent-node__context-key-input"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="key"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAddContext();
              }}
            />
            <button
              className="agent-node__context-add-btn nodrag"
              onClick={handleAddContext}
              disabled={!newKey.trim()}
            >
              +
            </button>
          </div>
        </div>

        <div className="agent-node__separator" />

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="model"
            className="agent-node__handle agent-node__handle--model"
          />
          <span className="agent-node__slot-label">MODEL</span>
          <span className="agent-node__slot-value">
            {data.connectedModel || "gpt-4o-mini"}
          </span>
        </div>

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="design_pattern"
            className="agent-node__handle agent-node__handle--design-pattern"
          />
          <span className="agent-node__slot-label">DESIGN PATTERN</span>
          <span className="agent-node__slot-value">
            {data.connectedDesignPattern || "ReAct"}
          </span>
        </div>

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="tools"
            className="agent-node__handle agent-node__handle--tools"
          />
          <span className="agent-node__slot-label">TOOLS</span>
          <span
            className="agent-node__slot-value"
            title={
              data.connectedTools && data.connectedTools.length > 0
                ? data.connectedTools.join(", ")
                : undefined
            }
          >
            {data.connectedTools && data.connectedTools.length > 0
              ? data.connectedTools.join(", ")
              : "—"}
          </span>
        </div>

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="prompt_skills"
            className="agent-node__handle agent-node__handle--prompt-skills"
          />
          <span className="agent-node__slot-label">PROMPT SKILLS</span>
          <span
            className="agent-node__slot-value"
            title={
              data.connectedPromptSkills &&
              data.connectedPromptSkills.length > 0
                ? data.connectedPromptSkills.join(", ")
                : undefined
            }
          >
            {data.connectedPromptSkills && data.connectedPromptSkills.length > 0
              ? data.connectedPromptSkills.join(", ")
              : "—"}
          </span>
        </div>

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="context_skills"
            className="agent-node__handle agent-node__handle--context-skills"
          />
          <span className="agent-node__slot-label">CONTEXT SKILLS</span>
          <span
            className="agent-node__slot-value"
            title={
              data.connectedContextSkills &&
              data.connectedContextSkills.length > 0
                ? data.connectedContextSkills.join(", ")
                : undefined
            }
          >
            {data.connectedContextSkills &&
            data.connectedContextSkills.length > 0
              ? data.connectedContextSkills.join(", ")
              : "—"}
          </span>
        </div>

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="memory"
            className="agent-node__handle agent-node__handle--memory"
          />
          <span className="agent-node__slot-label">MEMORY</span>
          <span className="agent-node__slot-value">
            {data.connectedMemory || "—"}
          </span>
        </div>

        <div className="agent-node__slot">
          <Handle
            type="target"
            position={Position.Left}
            id="text_input"
            className="agent-node__handle agent-node__handle--text-input"
          />
          <span className="agent-node__slot-label">TEXT INPUT</span>
          <span className="agent-node__slot-value">
            {data.connectedTextInput
              ? data.connectedTextInput.length > 30
                ? data.connectedTextInput.slice(0, 30) + "…"
                : data.connectedTextInput
              : "—"}
          </span>
        </div>

        <div className="agent-node__separator" />

        <div className="agent-node__slot agent-node__slot--output">
          <span className="agent-node__slot-label">AGENT OUTPUT</span>
          <Handle
            type="source"
            position={Position.Right}
            id={AGENT_OUTPUT_HANDLE}
            className="agent-node__handle agent-node__handle--agent-output"
          />
        </div>
      </div>
    </div>
  );
}
