#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for benchmark storage module."""

import tempfile
from datetime import datetime

from mas.lab.benchmark import ResultStorage, RunMetadata


def test_result_storage_init():
    """Test ResultStorage initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ResultStorage(tmpdir)
        assert storage.base_dir.exists()


def test_generate_run_id():
    """Test run ID generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ResultStorage(tmpdir)

        run_id = storage.generate_run_id("cot", 1)

        assert run_id.startswith("cot_run_001_")
        assert len(run_id) > 20  # Includes timestamp


def test_save_and_load_run():
    """Test saving and loading run metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ResultStorage(tmpdir)

        metadata = RunMetadata(
            dataset_name="math",
            item_id="001",
            run_id="test_run_001",
            pattern="cot",
            timestamp=datetime.now().isoformat(),
            config={"model": "gpt-4"},
            success=True,
            latency_ms=1234.5,
        )

        # Save
        run_dir = storage.save_run(dataset_name="math", item_id="001", run_id="test_run_001", metadata=metadata)

        assert run_dir.exists()
        assert (run_dir / "metadata.json").exists()

        # Load
        loaded = storage.load_run_metadata("math", "001", "test_run_001")

        assert loaded is not None
        assert loaded.pattern == "cot"
        assert loaded.success is True
        assert loaded.latency_ms == 1234.5


def test_list_runs():
    """Test listing runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ResultStorage(tmpdir)

        # Create multiple runs
        for item_id in ["001", "002"]:
            for run_num in [1, 2]:
                metadata = RunMetadata(
                    dataset_name="math",
                    item_id=item_id,
                    run_id=f"run_{run_num}",
                    pattern="cot",
                    timestamp=datetime.now().isoformat(),
                    config={},
                    success=True,
                )
                storage.save_run("math", item_id, f"run_{run_num}", metadata)

        # List all runs
        runs = storage.list_runs("math")

        assert len(runs) == 4
        assert ("math", "001", "run_1") in runs
        assert ("math", "002", "run_2") in runs


def test_get_next_run_number():
    """Test getting next run number."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ResultStorage(tmpdir)

        # No runs yet
        assert storage.get_next_run_number("math", "001", "cot") == 1

        # Create a run
        metadata = RunMetadata(
            dataset_name="math",
            item_id="001",
            run_id="cot_run_001_20260218_120000",
            pattern="cot",
            timestamp=datetime.now().isoformat(),
            config={},
            success=True,
        )
        storage.save_run("math", "001", "cot_run_001_20260218_120000", metadata)

        # Next should be 2
        assert storage.get_next_run_number("math", "001", "cot") == 2
