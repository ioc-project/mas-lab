//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { type DragEvent } from "react";
import { Tooltip } from "@open-ui-kit/core";
import { useDnD } from "./DnDContext";
import { mediumBlue, ciscoBlue, orange, magenta, violet, teal } from "./color-tokens";
import type { CanvasNodeType } from "./types";

const NODE_ITEMS: Array<{ type: CanvasNodeType; label: string; icon: string; color: string; tooltip?: string }> = [
  { type: "agent", label: "Agent", icon: "🤖", color: mediumBlue[50] },
  { type: "model", label: "Model", icon: "🧠", color: ciscoBlue[50] },
  { type: "designPattern", label: "Design Pattern", icon: "🔄", color: mediumBlue[60] },
  { type: "tool", label: "Tools", icon: "🔧", color: orange[50] },
  { type: "promptSkills", label: "Prompt Skills", icon: "📖", color: violet[60], tooltip: "Prompt Skills are injected directly into the agent's system prompt as domain knowledge. The agent can also query them at runtime via the consult_skills tool. Use these for behavioral guidelines, formatting rules, domain expertise, and standard procedures the agent should always be aware of." },
  { type: "contextSkills", label: "Context Skills", icon: "⚡", color: teal[60], tooltip: "Context Skills are loaded dynamically by the context manager into the agent's working context at runtime. Unlike Prompt Skills, these aren't always present — the context manager selects which ones to inject based on the current conversation. Use these for reference material, knowledge bases, or situational documents the agent should access when relevant." },
  { type: "memory", label: "Memory", icon: "💾", color: magenta[50] },
  { type: "textInput", label: "Text Input", icon: "📝", color: mediumBlue[40] },
];

export function Sidebar() {
  const [, setType] = useDnD();

  const onDragStart = (event: DragEvent, nodeType: CanvasNodeType) => {
    setType(nodeType);
    event.dataTransfer.setData("application/reactflow", nodeType);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside className="canvas-sidebar">
      <div className="canvas-sidebar__title">Drag nodes to the canvas</div>
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
