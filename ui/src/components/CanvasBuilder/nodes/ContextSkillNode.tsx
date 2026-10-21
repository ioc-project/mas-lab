//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, NodeResizer, type NodeProps } from "@xyflow/react";
import { useCallback, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Tooltip } from "@mui/material";
import type { ContextSkillsNodeType } from "../types";
import { useSkillChoices } from "@/api/choices";
import { useScopeId } from "@/api/scope";

const INFO_TEXT =
  "Context Skills are loaded dynamically by the context manager into the agent's working context at runtime. " +
  "Unlike Prompt Skills, these aren't always present — the context manager selects which ones to inject based on the current conversation. " +
  "Use these for reference material, knowledge bases, or situational documents the agent should access when relevant.";

export function ContextSkillNode({ data, id, selected }: NodeProps<ContextSkillsNodeType>) {
  const [inputValue, setInputValue] = useState("");
  const library = useScopeId();
  const { skills: skillOptions = [] } = useSkillChoices(library);

  const skillDescMap = new Map(
    skillOptions.map((s) => [s.name, s.description]),
  );

  const handleSelectSkill = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      const skill = e.target.value;
      if (skill && !data.skills.includes(skill)) {
        data.onChange?.("skills", [...data.skills, skill]);
      }
      e.target.value = "";
    },
    [data],
  );

  const handleCustomSkill = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && inputValue.trim()) {
        if (!data.skills.includes(inputValue.trim())) {
          data.onChange?.("skills", [...data.skills, inputValue.trim()]);
        }
        setInputValue("");
      }
    },
    [data, inputValue],
  );

  const handleRemoveSkill = useCallback(
    (skill: string) => {
      data.onChange?.("skills", data.skills.filter((s) => s !== skill));
    },
    [data],
  );

  return (
    <div className={`canvas-node context-skills-node${data.disabled ? " canvas-node--disabled" : ""}`}>
      <NodeResizer isVisible={selected} minWidth={220} minHeight={100} />
      <div className="canvas-node__header context-skills-node__header">
        <span className="canvas-node__icon">⚡</span>
        <span className="canvas-node__title">Context Skills</span>
        <Tooltip title={INFO_TEXT} arrow placement="top">
          <span className="canvas-node__info-icon nodrag">ℹ️</span>
        </Tooltip>
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
          <select className="canvas-node__select nodrag" onChange={handleSelectSkill} defaultValue="">
            <option value="">Select skill...</option>
            {skillOptions
              .filter((s) => !data.skills.includes(s.name))
              .map((s) => (
                <option key={s.name} value={s.name} title={s.description || undefined}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
        <label className="canvas-node__label">
          Custom skill (Enter to add)
          <input
            className="canvas-node__input nodrag"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleCustomSkill}
            placeholder="skill_name"
          />
        </label>
        {data.skills.length > 0 && (
          <div className="canvas-node__tags">
            {data.skills.map((skill) => {
              const desc = skillDescMap.get(skill);
              const chip = (
                <span key={skill} className="canvas-node__tag">
                  {skill}
                  <button
                    className="canvas-node__tag-remove"
                    onClick={() => handleRemoveSkill(skill)}
                  >
                    ×
                  </button>
                </span>
              );
              return desc ? (
                <Tooltip key={skill} title={desc} arrow placement="top">
                  {chip}
                </Tooltip>
              ) : (
                chip
              );
            })}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} id={`contextSkills-out-${id}`} className="handle--context-skills" />
    </div>
  );
}
