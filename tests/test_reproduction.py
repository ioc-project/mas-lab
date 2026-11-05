#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Reproduction gate — dry-run all declared experiments + doc checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "tests" / "fixtures" / "reproduction" / "experiments.yaml"
MAS_LAB = Path(sys.executable).parent / "mas-lab"

_TUTORIAL_READMES = [
    REPO_ROOT / "docs" / "tutorials" / "01-building-an-agent" / "README.md",
    REPO_ROOT / "docs" / "tutorials" / "02-creating-a-mas" / "README.md",
    REPO_ROOT / "docs" / "tutorials" / "03-experiments-and-analysis" / "README.md",
]


def _experiment_paths() -> list[Path]:
    if not MANIFEST.is_file():
        pytest.skip(f"missing {MANIFEST}")
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    paths: list[Path] = []
    for rel in data.get("experiments") or []:
        path = REPO_ROOT / rel
        if not path.is_file():
            pytest.fail(f"reproduction manifest entry missing: {rel}")
        paths.append(path)
    return paths


@pytest.mark.parametrize(
    "experiment",
    _experiment_paths(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
@pytest.mark.timeout(90)
def test_experiment_dry_run(experiment: Path) -> None:
    """Every listed experiment must plan successfully without API keys."""
    if not MAS_LAB.is_file():
        pytest.skip("mas-lab CLI not in venv")
    proc = subprocess.run(
        [str(MAS_LAB), "benchmark", "run", str(experiment), "--dry-run"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=85,
    )
    assert proc.returncode == 0, (
        f"dry-run failed for {experiment.relative_to(REPO_ROOT)}:\n{proc.stderr}\n{proc.stdout}"
    )


def test_tutorial_readmes_exist() -> None:
    """Tutorial walk-through entry points are present for manual/doc reproduction."""
    missing = [p for p in _TUTORIAL_READMES if not p.is_file()]
    assert not missing, f"missing tutorial README(s): {missing}"


def test_docs_reference_pages_exist() -> None:
    """Generated reference docs checked into docs/."""
    for name in ("packages-reference.md", "plugins-reference.md"):
        path = REPO_ROOT / "docs" / name
        assert path.is_file(), f"missing docs/{name} — run: task docs-gen"


def test_reproduction_manifest_covers_all_lab_experiments() -> None:
    """Warn if a new labs/**/experiment.yaml is not in the reproduction manifest."""
    declared = {
        (REPO_ROOT / rel).resolve() for rel in (yaml.safe_load(MANIFEST.read_text()) or {}).get("experiments", [])
    }
    on_disk = {p.resolve() for p in REPO_ROOT.glob("labs/**/experiment*.yaml")}
    # experiment-fact-recall.yaml is heavy — optional full run, smoke variant is in manifest
    optional = {
        (REPO_ROOT / "labs/extensions.lab/experiment-fact-recall.yaml").resolve(),
    }
    missing = sorted(on_disk - declared - optional)
    assert not missing, "add to tests/fixtures/reproduction/experiments.yaml: " + ", ".join(
        str(p.relative_to(REPO_ROOT)) for p in missing
    )
