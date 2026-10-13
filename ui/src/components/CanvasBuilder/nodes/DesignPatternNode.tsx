//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, NodeResizer, type NodeProps } from "@xyflow/react";
import { useCallback, type ChangeEvent } from "react";
import type { DesignPatternNodeType } from "../types";
import { FALLBACK_PATTERN_GROUPS } from "@/utils/designPatternOptions";

export function DesignPatternNode({ data, id, selected }: NodeProps<DesignPatternNodeType>) {
  // Static list until the backend serves GET /api/design-patterns; then swap
  // back to useDesignPatterns() + buildPatternGroups().
  const groups = FALLBACK_PATTERN_GROUPS;

  const handleChange = useCallback(
    (field: string) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      data.onChange?.(
        field,
        field === "type" ? e.target.value : parseInt(e.target.value) || 0,
      );
    },
    [data],
  );

  return (
    <div className={`canvas-node design-pattern-node${data.disabled ? " canvas-node--disabled" : ""}`}>
      <NodeResizer isVisible={selected} minWidth={220} minHeight={100} />
      <div className="canvas-node__header design-pattern-node__header">
        <span className="canvas-node__icon">🔄</span>
        <span className="canvas-node__title">Design Pattern</span>
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
          Pattern Type
          <select
            className="canvas-node__select nodrag"
            value={data.type}
            onChange={handleChange("type")}
          >
            <option value="">Select pattern...</option>
            {groups.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.options.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="canvas-node__label">
          Max Steps
          <input
            className="canvas-node__input nodrag"
            type="number"
            min={1}
            max={50}
            step={1}
            value={data.max_steps}
            onChange={handleChange("max_steps")}
          />
        </label>
      </div>
      <Handle type="source" position={Position.Right} id={`dp-out-${id}`} className="handle--design-pattern" />
    </div>
  );
}
