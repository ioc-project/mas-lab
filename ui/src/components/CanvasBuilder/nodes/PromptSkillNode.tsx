//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Handle, Position, NodeResizer, type NodeProps } from "@xyflow/react";
import { useCallback, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Tooltip } from "@mui/material";
import type { PromptSkillsNodeType } from "../types";
import { useSkillChoices } from "@/api/choices";
import { useScopeId } from "@/api/scope";

const INFO_TEXT =
  "Prompt Skills are injected directly into the agent's system prompt as domain knowledge. " +
  "The agent can also query them at runtime via the consult_skills tool. " +
  "Use these for behavioral guidelines, formatting rules, domain expertise, and standard procedures the agent should always be aware of.";

export function PromptSkillNode({ data, id, selected }: NodeProps<PromptSkillsNodeType>) {
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
    <div className={`canvas-node prompt-skills-node${data.disabled ? " canvas-node--disabled" : ""}`}>
      <NodeResizer isVisible={selected} minWidth={220} minHeight={100} />
      <div className="canvas-node__header prompt-skills-node__header">
        <span className="canvas-node__icon">📖</span>
        <span className="canvas-node__title">Prompt Skills</span>
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
      <Handle type="source" position={Position.Right} id={`promptSkills-out-${id}`} className="handle--prompt-skills" />
    </div>
  );
}
