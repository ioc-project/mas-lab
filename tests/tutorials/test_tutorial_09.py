#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 09 — KG & SCOPE: normalize native/OTel example traces, Neo4j dry-run."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REPO_ROOT, T09, run_cli

QA = REPO_ROOT / "library-kg" / "examples" / "qa-agent"


pytestmark = pytest.mark.skipif(
    not QA.is_dir(),
    reason="library-kg/examples/qa-agent not in this checkout",
)


class TestExampleArtifacts:
    def test_qa_agent_kg_examples_exist(self):
        for name in ("events.jsonl", "otel_spans.jsonl", "kg-native.jsonld", "kg-otel.jsonld"):
            path = QA / name
            assert path.is_file(), path
            assert path.stat().st_size > 0

    def test_tutorial_readme_points_at_examples(self):
        text = (T09 / "README.md").read_text(encoding="utf-8")
        assert "library-kg/examples/qa-agent" in text


class TestNormalize:
    def test_native_to_kg(self, tmp_path: Path):
        pytest.importorskip("mas.library.kg")
        from mas.library.kg.steps.normalize import run_normalize

        art = run_normalize(
            QA / "events.jsonl",
            run_id="t8-native",
            output_dir=tmp_path,
        )
        assert art.node_count > 0
        assert (tmp_path / "kg.jsonld").is_file()

    def test_otel_to_kg(self, tmp_path: Path):
        pytest.importorskip("mas.library.kg")
        pytest.importorskip("norm")
        from mas.library.kg.steps.normalize_otel import run_normalize_otel

        art = run_normalize_otel(
            QA / "otel_spans.jsonl",
            run_id="t8-otel",
            output_dir=tmp_path,
        )
        assert art.node_count > 0
        assert (tmp_path / "kg.jsonld").is_file()


class TestOxpValidation:
    def test_validate_runs_shacl_on_native_kg(self):
        pytest.importorskip("mas.library.kg")
        pytest.importorskip("pyshacl")
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        try:
            report = run_validate_kg(QA / "kg-native.jsonld", warning_verbosity="summary")
        except KGValidationError as exc:
            report = exc.report
        checks = {row["check"]: row["status"] for row in report["results"]}
        assert "shacl" in checks
        assert checks["shacl"] != "skipped"
        structural = [
            row for row in report["results"]
            if row["check"] in {
                "unknown_node_types",
                "unknown_edge_types",
                "block_vocabulary",
                "layer_vocabulary",
                "orphaned_edges",
            }
        ]
        assert structural
        assert all(row["status"] == "pass" for row in structural), structural

    def test_validate_runs_shacl_on_otel_kg(self):
        pytest.importorskip("mas.library.kg")
        pytest.importorskip("pyshacl")
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        try:
            report = run_validate_kg(QA / "kg-otel.jsonld", warning_verbosity="summary")
        except KGValidationError as exc:
            report = exc.report
        statuses = [row["status"] for row in report["results"] if row["check"] == "shacl"]
        assert statuses
        assert "skipped" not in statuses
    def test_neo4j_push_dry_run(self):
        pytest.importorskip("mas.library.kg")
        r = run_cli(
            [
                "mas-lab",
                "kg",
                "neo4j-push",
                str(QA / "kg-native.jsonld"),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
        )
        assert r.returncode == 0, r.stderr + r.stdout
        combined = r.stdout + r.stderr
        assert "dry_run" in combined
