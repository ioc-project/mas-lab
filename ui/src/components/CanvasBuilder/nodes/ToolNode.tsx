//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, NodeResizer, type NodeProps } from "@xyflow/react";
import { useCallback, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Tooltip } from "@mui/material";
import type { ToolNodeType } from "../types";
import { useToolChoices } from "@/api/choices";
import { useScopeId } from "@/api/scope";

export function ToolNode({ data, id, selected }: NodeProps<ToolNodeType>) {
  const [inputValue, setInputValue] = useState("");
  const library = useScopeId();
  const { tools: toolOptions = [] } = useToolChoices(library);

  const toolDescMap = new Map(
    toolOptions.map((t) => [t.name, t.description]),
  );

  const handleSelectTool = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      const tool = e.target.value;
      if (tool && !data.tools.includes(tool)) {
        data.onChange?.("tools", [...data.tools, tool]);
      }
      e.target.value = "";
    },
    [data],
  );

  const handleCustomTool = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && inputValue.trim()) {
        if (!data.tools.includes(inputValue.trim())) {
          data.onChange?.("tools", [...data.tools, inputValue.trim()]);
        }
        setInputValue("");
      }
    },
    [data, inputValue],
  );

  const handleRemoveTool = useCallback(
    (tool: string) => {
      data.onChange?.("tools", data.tools.filter((t) => t !== tool));
    },
    [data],
  );

  return (
    <div className={`canvas-node tool-node${data.disabled ? " canvas-node--disabled" : ""}`}>
      <NodeResizer isVisible={selected} minWidth={220} minHeight={100} />
      <div className="canvas-node__header tool-node__header">
        <span className="canvas-node__icon">🔧</span>
        <span className="canvas-node__title">Tools</span>
        <button
          className="canvas-node__toggle-btn nodrag"
          onClick={(e) => { e.stopPropagation(); data.onChange?.("disabled", !data.disabled); }}
          title={data.disabled ? "Enable node" : "Disable node"}
        >
          {data.disabled ? "🔴" : "🟢"}
        </button>
      </div>
      <div className="canvas-node__body">
        <label className="canvas-node__label">
          Add from list
          <select className="canvas-node__select nodrag" onChange={handleSelectTool} defaultValue="">
            <option value="">Select tool...</option>
            {toolOptions
              .filter((t) => !data.tools.includes(t.name))
              .map((t) => (
                <option key={t.name} value={t.name} title={t.description || undefined}>
                  {t.name}
                </option>
              ))}
          </select>
        </label>
        <label className="canvas-node__label">
          Custom tool (Enter to add)
          <input
            className="canvas-node__input nodrag"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleCustomTool}
            placeholder="module_path.ToolClass"
          />
        </label>
        {data.tools.length > 0 && (
          <div className="canvas-node__tags">
            {data.tools.map((tool) => {
              const desc = toolDescMap.get(tool);
              const chip = (
                <span key={tool} className="canvas-node__tag">
                  {tool}
                  <button
                    className="canvas-node__tag-remove"
                    onClick={() => handleRemoveTool(tool)}
                  >
                    ×
                  </button>
                </span>
              );
              return desc ? (
                <Tooltip key={tool} title={desc} arrow placement="top">
                  {chip}
                </Tooltip>
              ) : (
                chip
              );
            })}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id={`tool-out-${id}`} className="handle--tools" />
    </div>
  );
}
