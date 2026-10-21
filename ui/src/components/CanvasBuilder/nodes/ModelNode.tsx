//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, NodeResizer, type NodeProps } from "@xyflow/react";
import { useCallback, type ChangeEvent } from "react";
import type { ModelNodeType } from "../types";
import { useModelChoices } from "@/api/choices";

export function ModelNode({ data, id, selected }: NodeProps<ModelNodeType>) {
  const { models, defaultModel } = useModelChoices();
  const handleChange = useCallback(
    (field: string) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      data.onChange?.(
        field,
        field === "model" ? e.target.value : parseFloat(e.target.value) || 0,
      );
    },
    [data],
  );

  const options = models.map((m) => m.id);
  const current = data.model || defaultModel || "";

  return (
    <div className={`canvas-node model-node${data.disabled ? " canvas-node--disabled" : ""}`}>
      <NodeResizer isVisible={selected} minWidth={220} minHeight={150} />
      <div className="canvas-node__header model-node__header">
        <span className="canvas-node__icon">🧠</span>
        <span className="canvas-node__title">Model</span>
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
          Model
          <select
            className="canvas-node__select nodrag"
            value={current}
            onChange={handleChange("model")}
          >
            <option value="">Select model...</option>
            {options.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {current && !options.includes(current) && (
              <option value={current}>{current}</option>
            )}
          </select>
        </label>
        <label className="canvas-node__label">
          Temperature
          <input
            className="canvas-node__input nodrag"
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={data.temperature}
            onChange={handleChange("temperature")}
          />
        </label>
        <label className="canvas-node__label">
          Max Tokens
          <input
            className="canvas-node__input nodrag"
            type="number"
            min={1}
            max={128000}
            step={256}
            value={data.max_tokens}
            onChange={handleChange("max_tokens")}
          />
        </label>
      </div>
      <Handle type="source" position={Position.Right} id={`model-out-${id}`} className="handle--model" />
    </div>
  );
}
