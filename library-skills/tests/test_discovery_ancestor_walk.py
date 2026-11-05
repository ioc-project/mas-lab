#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Ancestor-dir walk-up finds shared ``skills/`` dirs without git.

The walk is a pure filesystem scan: nearest ``skills/`` first, bounded by the
home directory and ``ancestor_walk_depth``. See docs/spec-coverage.md.
"""

from __future__ import annotations

from pathlib import Path

from agentskills import Discovery

from .conftest import make_skill


def test_walk_up_finds_shared_skills_dir(tmp_path: Path):
    """A skill living only in <root>/skills/ is discoverable from a
    nested app dir that has no local skills/ of its own."""
    make_skill(tmp_path, "root-skill", description="Lives at the repo root.")

    app_dir = tmp_path / "mas-lab" / "apps" / "trip-planner"
    app_dir.mkdir(parents=True)

    discovery = Discovery(manifest_skills=["root-skill"], base_dir=app_dir)
    registry = discovery.discover()

    rec = registry.get("root-skill")
    assert rec is not None
    assert rec.path == (tmp_path / "skills" / "root-skill" / "SKILL.md").resolve()


def test_local_skills_dir_still_shadows_root(tmp_path: Path):
    """An app-local skills/<name> still wins over the same name higher up
    (first-found-wins)."""
    make_skill(tmp_path, "dup", description="Root version.")

    app_dir = tmp_path / "app"
    make_skill(app_dir, "dup", description="Local version.")

    discovery = Discovery(manifest_skills=["dup"], base_dir=app_dir)
    registry = discovery.discover()

    rec = registry.get("dup")
    assert rec is not None
    assert rec.path == (app_dir / "skills" / "dup" / "SKILL.md").resolve()


def test_ancestor_walk_depth_limit_excludes_distant_skills_dir(tmp_path: Path):
    """ancestor_walk_depth=0 stops before a skills/ dir several levels up."""
    make_skill(tmp_path, "root-skill", description="Lives at the repo root.")

    app_dir = tmp_path / "mas-lab" / "apps" / "trip-planner"
    app_dir.mkdir(parents=True)

    discovery = Discovery(manifest_skills=["root-skill"], base_dir=app_dir, ancestor_walk_depth=0)
    registry = discovery.discover()

    assert registry.get("root-skill") is None


def test_missing_base_dir_does_not_crash_the_walk(tmp_path: Path):
    """base_dir defaults to <manifest_dir>/skills, which often does not exist."""
    make_skill(tmp_path, "root-skill", description="Lives at the repo root.")

    agents_dir = tmp_path / "apps" / "example-app" / "v2" / "agents"
    agents_dir.mkdir(parents=True)

    discovery = Discovery(manifest_skills=["root-skill"], base_dir=agents_dir / "skills")
    registry = discovery.discover()

    rec = registry.get("root-skill")
    assert rec is not None
    assert rec.path == (tmp_path / "skills" / "root-skill" / "SKILL.md").resolve()
