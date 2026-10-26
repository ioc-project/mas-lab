#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 06 — Agent skills: validation, frontmatter contract, live chat."""

from __future__ import annotations

from conftest import T06, live_cli_env, live_tutorial_chat, load_yaml, run_cli


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
# Live mas-ctl chat (real LLM)
# ═══════════════════════════════════════════════════════════════════════════

_LIVE = live_tutorial_chat
_LIVE_QUERY = (
    "What is the speed of light in kilometres and miles per second? "
    "Use the answer-expert skill."
)


@_LIVE
class TestLiveSkillChat:
    def test_with_skills_activates_and_answers(self):
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
                _LIVE_QUERY,
            ],
            cwd=T06,
            timeout=120,
            extra_env=live_cli_env(),
        )
        combined = f"{r.stdout}\n{r.stderr}"
        assert r.returncode == 0, r.stderr
        assert "SKILL[answer-expert]" in combined
        assert "Confidence:" in combined
