#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 06 — Agent skills: validation, frontmatter contract, chat against a fake endpoint."""

from __future__ import annotations

from fake_openai import FakeOpenAI, last_role, offered_tools, text, tool_call
from tutorial_support import T06, load_yaml, run_cli, tutorial_copy


class TestManifestValidation:
    def test_validate_base_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T06 / "agent.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_validate_with_skills_overlay(self):
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(T06 / "agent.yaml"),
                "--overlay",
                str(T06 / "overlays" / "with-skills.yaml"),
            ]
        )
        assert r.returncode == 0, r.stderr


class TestManifestStructure:
    def test_agent_yaml_structure(self):
        m = load_yaml(T06 / "agent.yaml")
        assert m["apiVersion"] == "mas/v1"
        assert m["kind"] == "Agent"
        assert "name" in m.get("metadata", {})

    def test_overlay_adds_answer_expert_skill(self):
        ov = load_yaml(T06 / "overlays" / "with-skills.yaml")
        skills = ov["spec"]["patch"]["skills"]["$op"]["add"]
        assert "answer-expert" in skills


class TestSkillContract:
    """The skill ships a well-formed SKILL.md plus its referenced material."""

    def test_skill_md_starts_with_yaml_frontmatter(self):
        from mas.library.skills.lib.frontmatter import parse_skill_frontmatter

        skill_md = T06 / "skills" / "answer-expert" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---"), "SKILL.md must begin with YAML frontmatter"
        meta, body = parse_skill_frontmatter(text)
        assert meta.get("name") == "answer-expert"
        description = meta.get("description") or ""
        assert "factual science questions" in description
        assert "Procedure" in body

    def test_referenced_material_exists(self):
        skill_dir = T06 / "skills" / "answer-expert"
        assert (skill_dir / "references" / "physical-constants.md").is_file()
        assert (skill_dir / "scripts" / "convert_speed.py").is_file()

    def test_convert_speed_script_is_runnable(self):
        import subprocess
        import sys

        script = T06 / "skills" / "answer-expert" / "scripts" / "convert_speed.py"
        r = subprocess.run(
            [sys.executable, str(script), "299792458"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r.returncode == 0, r.stderr
        assert "299792.458" in r.stdout or "km" in r.stdout.lower()


class TestOverlayMerging:
    def test_with_skills_overlay_merges_onto_base_agent(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T06 / "agent.yaml")
        ov = load_yaml(T06 / "overlays" / "with-skills.yaml")
        merged = merge_overlay(base, ov)
        assert "answer-expert" in merged["spec"]["skills"]


# ═══════════════════════════════════════════════════════════════════════════
# mas-ctl chat against a fake OpenAI endpoint
# ═══════════════════════════════════════════════════════════════════════════

_QUERY = "What is the speed of light in kilometres and miles per second? Use the answer-expert skill."


def _script(body):
    if last_role(body) != "tool" and "activate_skill" in offered_tools(body):
        return tool_call("activate_skill", {"name": "answer-expert"})
    return text("About 299,792 km/s, or 186,282 miles per second.\n\nConfidence: high")


class TestSkillChat:
    def test_with_skills_activates_and_answers(self, tmp_path):
        with FakeOpenAI(_script) as fake:
            r = run_cli(
                [
                    "mas-ctl",
                    "chat",
                    "agent.yaml",
                    "-o",
                    "overlays/with-skills.yaml",
                    "--no-cache-read",
                    "--model",
                    "gpt-4o",
                    "--trace",
                    "-q",
                    _QUERY,
                ],
                cwd=tutorial_copy(T06, tmp_path),
                timeout=120,
                extra_env=fake.env(),
            )
        combined = f"{r.stdout}\n{r.stderr}"
        assert r.returncode == 0, r.stderr
        assert "TOOL[activate_skill]" in combined
        skill_result = next(body["messages"][-1] for body in fake.bodies if last_role(body) == "tool")
        assert "answer-expert" in skill_result["content"]
        assert "Confidence:" in combined
