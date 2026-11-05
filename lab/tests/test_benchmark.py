#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
Benchmark Tests for MAS Lab.

Validates the benchmark harness itself.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from mas.lab.benchmark import (
    ResultAnalyzer,
    ResultStorage,
    RunMetadata,
)


@pytest.fixture
def mock_mas_runner(mocker):
    mock = mocker.patch("mas.lab.benchmark.plugins.mas.MasRuntimeRunner.run")
    mock.return_value = MagicMock(
        content="ok",
        status="ok",
        error=None,
        artifacts=[],
        metadata={"usage": {"total_tokens": 1}},
    )
    return mock


def test_benchmark_storage_creates_run_dir(tmp_path):
    """Test that saving a run creates the expected folder structure and metadata file."""
    storage = ResultStorage(tmp_path)
    run_id = storage.generate_run_id("cot_balanced", 1)

    meta = RunMetadata(
        dataset_name="test_dataset",
        item_id="item_001",
        run_id=run_id,
        pattern="cot_balanced",
        timestamp=datetime.now().isoformat(),
        config={"pattern": "cot_balanced"},
        success=True,
        latency_ms=123.4,
    )

    run_dir = storage.save_run(
        dataset_name="test_dataset",
        item_id="item_001",
        run_id=run_id,
        metadata=meta,
    )

    assert run_dir.exists(), "run directory must be created"
    meta_file = run_dir / "metadata.json"
    assert meta_file.exists(), "metadata.json must be written"

    loaded = json.loads(meta_file.read_text())
    assert loaded["pattern"] == "cot_balanced"
    assert loaded["success"] is True


def test_analyzer_consolidates_results(tmp_path):
    """Test that ResultAnalyzer consolidates saved runs into a DataFrame."""
    storage = ResultStorage(tmp_path)

    for i, pattern in enumerate(["react", "cot_balanced"], start=1):
        run_id = storage.generate_run_id(pattern, i)
        meta = RunMetadata(
            dataset_name="qa_dataset",
            item_id=f"item_{i:03d}",
            run_id=run_id,
            pattern=pattern,
            timestamp=datetime.now().isoformat(),
            config={"pattern": pattern},
            success=True,
            latency_ms=float(100 * i),
        )
        storage.save_run(
            dataset_name="qa_dataset",
            item_id=f"item_{i:03d}",
            run_id=run_id,
            metadata=meta,
        )

    analyzer = ResultAnalyzer(storage)
    df = analyzer.consolidate_results("qa_dataset", cache=False)

    assert len(df) == 2, "one row per saved run"
    assert set(df["pattern"].tolist()) == {"react", "cot_balanced"}
    assert "success" in df.columns
    assert df["success"].all()
