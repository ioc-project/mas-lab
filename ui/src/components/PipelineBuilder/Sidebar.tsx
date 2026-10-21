//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { type DragEvent } from "react";
import { useDnD } from "./DnDContext";
import { usePipelineStepTypes } from "@/api/apiCalls";
import type { PipelineStepTypeEntry } from "@/api/apiCalls";

const CATEGORY_COLORS: Record<string, string> = {
  data: "#3b82f6",
  execution: "#8b5cf6",
  extraction: "#06b6d4",
  normalization: "#14b8a6",
  analysis: "#f59e0b",
  evaluation: "#ef4444",
  visualization: "#22c55e",
  graph: "#a855f7",
};

const CATEGORY_ICONS: Record<string, string> = {
  data: "📊",
  execution: "▶️",
  extraction: "📤",
  normalization: "🔄",
  analysis: "📈",
  evaluation: "✅",
  visualization: "📉",
  graph: "🔗",
};

export function Sidebar() {
  const [, setType] = useDnD();
  const { data: registry } = usePipelineStepTypes();

  const onDragStart = (event: DragEvent, stepType: PipelineStepTypeEntry) => {
    setType("step");
    event.dataTransfer.setData("application/reactflow", "step");
    event.dataTransfer.setData("pipeline/step-type", JSON.stringify(stepType));
    event.dataTransfer.effectAllowed = "move";
  };

  const grouped = (registry?.step_types ?? []).reduce(
    (acc, entry) => {
      const cat = entry.category || "other";
      if (!acc[cat]) acc[cat] = [];
      acc[cat].push(entry);
      return acc;
    },
    {} as Record<string, PipelineStepTypeEntry[]>,
  );

  const categoryOrder = (registry?.categories ?? []).map((c) => c.id);
  const sortedCategories = Object.keys(grouped).sort(
    (a, b) => (categoryOrder.indexOf(a) ?? 99) - (categoryOrder.indexOf(b) ?? 99),
  );

  return (
    <aside className="pipeline-sidebar">
      <div className="pipeline-sidebar__title">Pipeline Steps</div>
      <div className="pipeline-sidebar__nodes">
        {sortedCategories.map((cat) => (
          <div key={cat} className="pipeline-sidebar__category">
            <div
              className="pipeline-sidebar__category-label"
              style={{ color: CATEGORY_COLORS[cat] ?? "#999" }}
            >
              {CATEGORY_ICONS[cat] ?? "📦"}{" "}
              {registry?.categories?.find((c) => c.id === cat)?.label ?? cat}
            </div>
            {grouped[cat].map((entry) => (
              <div
                key={entry.type}
                className="pipeline-sidebar__node"
                style={{ borderColor: CATEGORY_COLORS[cat] ?? "#666" }}
                onDragStart={(e) => onDragStart(e, entry)}
                draggable
                title={entry.description}
              >
                <span className="pipeline-sidebar__node-label">
                  {entry.label}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
