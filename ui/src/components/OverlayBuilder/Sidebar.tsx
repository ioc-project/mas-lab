//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { type DragEvent } from "react";
import { Tooltip } from "@open-ui-kit/core";
import { useDnD } from "./DnDContext";
import { mediumBlue, orange, teal, violet } from "../CanvasBuilder/color-tokens";
import type { OverlayNodeType } from "./types";

const NODE_ITEMS: Array<{
  type: OverlayNodeType;
  label: string;
  icon: string;
  color: string;
  tooltip?: string;
}> = [
  {
    type: "agent",
    label: "Agent",
    icon: "🤖",
    color: mediumBlue[50],
    tooltip:
      "Agent node represents a target agent in the overlay. Connect design pattern, tools, and prompt nodes to configure per-agent overrides.",
  },
  {
    type: "designPattern",
    label: "Design Pattern",
    icon: "🔄",
    color: violet[60],
    tooltip:
      "Override the design pattern (react, cot, reflection) for the connected agent.",
  },
  {
    type: "tool",
    label: "Tools (Add)",
    icon: "🔧",
    color: orange[50],
    tooltip:
      "Add tools to the connected agent. These are appended (deduplicated) to the agent's existing tools.",
  },
  {
    type: "toolRemove",
    label: "Tools (Remove)",
    icon: "🗑️",
    color: teal[60],
    tooltip:
      "Remove tools from the connected agent. These tools will be excluded from the agent's tool set.",
  },
  {
    type: "inputPrompt",
    label: "Instructions",
    icon: "📝",
    color: mediumBlue[40],
    tooltip:
      "Override spec.context.role for the connected agent.",
  },
];

export function Sidebar() {
  const [, setType] = useDnD();

  const onDragStart = (event: DragEvent, nodeType: OverlayNodeType) => {
    setType(nodeType);
    event.dataTransfer.setData("application/reactflow", nodeType);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside className="canvas-sidebar">
      <div className="canvas-sidebar__title">Overlay Nodes</div>
      <div className="canvas-sidebar__nodes">
        {NODE_ITEMS.map((item) => {
          const node = (
            <div
              key={item.type}
              className="canvas-sidebar__node"
              style={{ borderColor: item.color }}
              onDragStart={(e) => onDragStart(e, item.type)}
              draggable
            >
              <span className="canvas-sidebar__node-icon">{item.icon}</span>
              <span className="canvas-sidebar__node-label">{item.label}</span>
            </div>
          );
          return item.tooltip ? (
            <Tooltip key={item.type} title={item.tooltip} arrow placement="right">
              {node}
            </Tooltip>
          ) : (
            node
          );
        })}
      </div>
    </aside>
  );
}
