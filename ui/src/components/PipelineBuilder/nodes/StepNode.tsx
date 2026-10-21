//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useCallback, useMemo, useState } from "react";
import type { StepNodeType } from "../types";
import { usePipelineStepTypes, useEvalMetrics, useMceMetrics } from "@/api/apiCalls";
import type { PipelineStepTypeConfigField } from "@/api/apiCalls";

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

function KeyValueEditor({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
}) {
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");

  const entries = Object.entries(value);

  const handleAdd = useCallback(() => {
    const k = newKey.trim();
    if (!k) return;
    onChange({ ...value, [k]: newValue });
    setNewKey("");
    setNewValue("");
  }, [newKey, newValue, value, onChange]);

  const handleRemove = useCallback(
    (key: string) => {
      const next = { ...value };
      delete next[key];
      onChange(next);
    },
    [value, onChange],
  );

  const handleValueChange = useCallback(
    (key: string, v: string) => {
      onChange({ ...value, [key]: v });
    },
    [value, onChange],
  );

  return (
    <div className="pipeline-step-node__kv-editor">
      {entries.map(([k, v]) => (
        <div key={k} className="pipeline-step-node__kv-row">
          <span className="pipeline-step-node__kv-key" title={k}>{k}</span>
          <input
            className="pipeline-step-node__input pipeline-step-node__kv-value nodrag"
            value={v}
            onChange={(e) => handleValueChange(k, e.target.value)}
          />
          <button
            className="pipeline-step-node__kv-remove"
            onClick={() => handleRemove(k)}
            title="Remove"
          >
            ×
          </button>
        </div>
      ))}
      <div className="pipeline-step-node__kv-row pipeline-step-node__kv-add">
        <input
          className="pipeline-step-node__input pipeline-step-node__kv-key-input nodrag"
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          placeholder="key"
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
        />
        <input
          className="pipeline-step-node__input pipeline-step-node__kv-value nodrag"
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
          placeholder="value"
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
        />
        <button
          className="pipeline-step-node__kv-add-btn"
          onClick={handleAdd}
          disabled={!newKey.trim()}
          title="Add"
        >
          +
        </button>
      </div>
    </div>
  );
}

function MetricClassDropdown({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { data: metrics } = useEvalMetrics();
  const entries = useMemo(() => Object.entries(metrics ?? {}), [metrics]);

  return (
    <select
      className="pipeline-step-node__select nodrag"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">— Select metric —</option>
      {entries.map(([key, label]) => (
        <option key={key} value={key}>
          {label}
        </option>
      ))}
    </select>
  );
}

function MceMetricsMultiSelect({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const { data: metrics } = useMceMetrics();
  const entries = useMemo(() => Object.entries(metrics ?? {}), [metrics]);

  const handleSelect = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const key = e.target.value;
      if (key && !value.includes(key)) {
        onChange([...value, key]);
      }
      e.target.value = "";
    },
    [value, onChange],
  );

  const handleRemove = useCallback(
    (key: string) => {
      onChange(value.filter((k) => k !== key));
    },
    [value, onChange],
  );

  const labelMap = useMemo(
    () => new Map(entries),
    [entries],
  );

  return (
    <div className="pipeline-step-node__metrics-multi nodrag">
      <select
        className="pipeline-step-node__select nodrag"
        onChange={handleSelect}
        value=""
      >
        <option value="">— Add metric —</option>
        {entries
          .filter(([key]) => !value.includes(key))
          .map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
      </select>
      {value.length > 0 && (
        <div className="pipeline-step-node__metrics-chips">
          {value.map((key) => (
            <span key={key} className="pipeline-step-node__metrics-chip">
              {labelMap.get(key) ?? key}
              <button
                className="pipeline-step-node__metrics-chip-remove"
                onClick={() => handleRemove(key)}
                title="Remove"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function StepNode({ data, selected }: NodeProps<StepNodeType>) {
  const { data: registry } = usePipelineStepTypes();

  const stepDef = useMemo(
    () => (registry?.step_types ?? []).find((s) => s.type === data.type),
    [registry, data.type],
  );

  const categoryColor = CATEGORY_COLORS[stepDef?.category ?? ""] ?? "#666";

  const isAnnotateMetrics = data.type === "annotate_metrics";
  const isEvalMceBatch = data.type === "eval_batch";

  const handleNameChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      data.onChange?.("name", e.target.value);
    },
    [data],
  );

  const handleConfigChange = useCallback(
    (key: string, value: string, fieldDef?: PipelineStepTypeConfigField) => {
      const newConfig = { ...data.config };
      if (value === "" || value === undefined) {
        delete newConfig[key];
      } else {
        if (fieldDef?.type === "integer") {
          const parsed = parseInt(value, 10);
          newConfig[key] = isNaN(parsed) ? value : parsed;
        } else if (fieldDef?.type === "number") {
          const parsed = parseFloat(value);
          newConfig[key] = isNaN(parsed) ? value : parsed;
        } else if (fieldDef?.type === "boolean") {
          newConfig[key] = value === "true";
        } else {
          newConfig[key] = value;
        }
      }
      data.onChange?.("config", newConfig);
    },
    [data],
  );

  const handleObjectConfigChange = useCallback(
    (key: string, obj: Record<string, string>) => {
      const newConfig = { ...data.config, [key]: obj };
      data.onChange?.("config", newConfig);
    },
    [data],
  );

  const handleArrayConfigChange = useCallback(
    (key: string, arr: string[]) => {
      const newConfig = { ...data.config };
      if (arr.length === 0) {
        delete newConfig[key];
      } else {
        newConfig[key] = arr;
      }
      data.onChange?.("config", newConfig);
    },
    [data],
  );

  const configFields = stepDef?.config ?? {};

  return (
    <div
      className={`pipeline-step-node ${selected ? "pipeline-step-node--selected" : ""}`}
      style={{ borderColor: categoryColor }}
    >
      <Handle type="target" position={Position.Left} id="dep-in" />

      <div
        className="pipeline-step-node__header"
        style={{ backgroundColor: categoryColor }}
        title={stepDef?.description}
      >
        <span className="pipeline-step-node__type-label">
          {stepDef?.label ?? data.type}
        </span>
        <span className="pipeline-step-node__phase-badge">
          {data.phase}
        </span>
      </div>

      <div className="pipeline-step-node__body">
        <div className="pipeline-step-node__field">
          <label>Name</label>
          <input
            value={data.name}
            onChange={handleNameChange}
            placeholder="step-name"
            className="pipeline-step-node__input nodrag"
          />
        </div>

        {data.depends_on.length > 0 && (
          <div className="pipeline-step-node__field">
            <label>Depends On</label>
            <div className="pipeline-step-node__chips">
              {data.depends_on.map((dep) => (
                <span key={dep} className="pipeline-step-node__chip">
                  {dep}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="pipeline-step-node__config-section">
          <label className="pipeline-step-node__config-title">Config</label>
          {Object.entries(configFields).map(([key, fieldDef]) => {
            if (isAnnotateMetrics && key === "metric_class") {
              return (
                <div key={key} className="pipeline-step-node__field">
                  <label title={fieldDef.description}>
                    {key}
                    {fieldDef.required && <span className="pipeline-step-node__required">*</span>}
                  </label>
                  <MetricClassDropdown
                    value={String(data.config[key] ?? "")}
                    onChange={(v) => handleConfigChange(key, v, fieldDef)}
                  />
                </div>
              );
            }

            if (isEvalMceBatch && key === "metrics") {
              return (
                <div key={key} className="pipeline-step-node__field">
                  <label title={fieldDef.description}>
                    {key}
                  </label>
                  <MceMetricsMultiSelect
                    value={(data.config[key] as string[]) ?? []}
                    onChange={(v) => handleArrayConfigChange(key, v)}
                  />
                </div>
              );
            }

            return (
              <div key={key} className="pipeline-step-node__field">
                <label title={fieldDef.description}>
                  {key}
                  {fieldDef.required && <span className="pipeline-step-node__required">*</span>}
                </label>
                {fieldDef.type === "object" ? (
                  <KeyValueEditor
                    value={(data.config[key] as Record<string, string>) ?? {}}
                    onChange={(v) => handleObjectConfigChange(key, v)}
                  />
                ) : fieldDef.type === "boolean" ? (
                  <select
                    className="pipeline-step-node__select nodrag"
                    value={String(data.config[key] ?? fieldDef.default ?? "")}
                    onChange={(e) => handleConfigChange(key, e.target.value, fieldDef)}
                  >
                    <option value="">—</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : fieldDef.enum ? (
                  <select
                    className="pipeline-step-node__select nodrag"
                    value={String(data.config[key] ?? fieldDef.default ?? "")}
                    onChange={(e) => handleConfigChange(key, e.target.value, fieldDef)}
                  >
                    <option value="">—</option>
                    {fieldDef.enum.map((v) => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="pipeline-step-node__input nodrag"
                    value={String(data.config[key] ?? "")}
                    onChange={(e) => handleConfigChange(key, e.target.value, fieldDef)}
                    placeholder={
                      fieldDef.default !== undefined && fieldDef.default !== null
                        ? String(fieldDef.default)
                        : fieldDef.description ?? ""
                    }
                    title={fieldDef.description}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      <Handle type="source" position={Position.Right} id="dep-out" />
    </div>
  );
}
