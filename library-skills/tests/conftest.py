#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for skill plugin tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Optional-dependency detection (google-adk / deepagents come from the
# `library-skills[all]` extra; a plain `pip install library-skills` shouldn't
# make the test suite fail on import/collection for these engines).
# ---------------------------------------------------------------------------

try:
    import google.adk.skills  # noqa: F401
except ImportError:
    HAS_ADK = False
else:
    HAS_ADK = True

try:
    import deepagents  # noqa: F401
except ImportError:
    HAS_DEEPAGENTS = False
else:
    HAS_DEEPAGENTS = True

MISSING_EXTRA_REASON = "requires the 'library-skills[all]' extra (google-adk / deepagents)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_SKILL_MD = textwrap.dedent("""\
    ---
    name: {name}
    description: {description}
    ---
    # {name}

    This is the body of the {name} skill.
    """)

FULL_SKILL_MD = textwrap.dedent("""\
    ---
    name: {name}
    description: {description}
    compatibility: ">=0.1.0"
    license: Apache-2.0
    allowed-tools: python bash
    ---
    # {name}

    Full instructions for {name}.

    ## Usage

    Call this skill to do something useful.
    """)

WORKFLOW_MD = textwrap.dedent("""\
    ---
    name: {name}
    description: {description}
    ---
    # {name} Workflow

    A LlamaIndex-style workflow.
    """)

HELLO_PY = textwrap.dedent("""\
    #!/usr/bin/env python3
    import sys
    print("hello from skill")
    sys.exit(0)
    """)

HELLO_SH = textwrap.dedent("""\
    #!/bin/sh
    echo "hello from shell"
    exit 0
    """)

EXIT_1_PY = textwrap.dedent("""\
    #!/usr/bin/env python3
    import sys
    sys.exit(1)
    """)


# ---------------------------------------------------------------------------
# Fixture: minimal skill tree
# ---------------------------------------------------------------------------


def make_skill(
    root: Path,
    name: str,
    description: str = "A test skill.",
    *,
    full_frontmatter: bool = False,
    with_script_py: bool = False,
    with_script_sh: bool = False,
    with_reference: bool = False,
    with_workflow_md: bool = False,
    subdir: str = "skills",
) -> Path:
    """Create a skill directory under root/<subdir>/<name>/."""
    skill_dir = root / subdir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    template = FULL_SKILL_MD if full_frontmatter else MINIMAL_SKILL_MD
    (skill_dir / "SKILL.md").write_text(template.format(name=name, description=description), encoding="utf-8")

    if with_workflow_md:
        (skill_dir / "WORKFLOW.md").write_text(WORKFLOW_MD.format(name=name, description=description), encoding="utf-8")

    if with_script_py:
        scripts = skill_dir / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "main.py").write_text(HELLO_PY, encoding="utf-8")

    if with_script_sh:
        scripts = skill_dir / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "run.sh").write_text(HELLO_SH, encoding="utf-8")

    if with_reference:
        refs = skill_dir / "references"
        refs.mkdir(exist_ok=True)
        (refs / "README.md").write_text(f"# {name} reference\n", encoding="utf-8")

    return skill_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def skill_root(tmp_path: Path) -> Path:
    """A temp directory with one skill."""
    make_skill(tmp_path, "qa-skill", description="Question answering.")
    return tmp_path


@pytest.fixture()
def skill_root_full(tmp_path: Path) -> Path:
    """A temp directory with a fully-featured skill."""
    make_skill(
        tmp_path,
        "full-skill",
        description="Full skill with all features.",
        full_frontmatter=True,
        with_script_py=True,
        with_script_sh=True,
        with_reference=True,
    )
    return tmp_path


@pytest.fixture()
def skill_root_multi(tmp_path: Path) -> Path:
    """A temp directory with multiple skills."""
    make_skill(tmp_path, "skill-a", description="Skill A.")
    make_skill(tmp_path, "skill-b", description="Skill B.", with_script_py=True)
    make_skill(tmp_path, "skill-c", description="Skill C.", with_reference=True)
    return tmp_path


@pytest.fixture()
def skill_root_workflow(tmp_path: Path) -> Path:
    """A temp directory with a WORKFLOW.md skill (LlamaIndex style)."""
    # Skill with only WORKFLOW.md (no SKILL.md)
    wf_dir = tmp_path / "skills" / "wf-skill"
    wf_dir.mkdir(parents=True)
    (wf_dir / "WORKFLOW.md").write_text(
        WORKFLOW_MD.format(name="wf-skill", description="A workflow."),
        encoding="utf-8",
    )
    steps = wf_dir / "steps"
    steps.mkdir()
    (steps / "step1.py").write_text(HELLO_PY, encoding="utf-8")
    return tmp_path
