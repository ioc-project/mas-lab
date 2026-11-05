#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from mas.library.lab.steps.eval.metrics_files import load_merged_session
from mas.library.lab.steps.eval.metrics_to_dataframe import rows_from_run_metrics


def test_merge_two_metrics_files_and_details(tmp_path: Path) -> None:
    run_dir = tmp_path / "baseline" / "item1" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"session": {"groundedness": {"value": 0.5, "reasoning": "mce", "error": None}}}),
        encoding="utf-8",
    )
    (run_dir / "metrics_judge2.json").write_text(
        json.dumps(
            {
                "session": {
                    "toy_echo": {
                        "value": 1.0,
                        "reasoning": "ok",
                        "error": None,
                        "details": {"state": "absent"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_info.json").write_text(
        json.dumps({"elapsed_ms": 1000, "status": "ok"}),
        encoding="utf-8",
    )
    session, _quality, paths = load_merged_session(run_dir, ["metrics.json", "metrics_judge2.json"])
    assert len(paths) == 2
    assert set(session) == {"groundedness", "toy_echo"}

    rows = rows_from_run_metrics(
        run_dir,
        scenario="baseline",
        test="item1",
        run="r1",
        metrics_filename="metrics*.json",
    )
    by_metric = {row["metric"]: row for row in rows}
    assert by_metric["groundedness"]["value"] == 0.5
    assert json.loads(by_metric["toy_echo"]["details"]) == {"state": "absent"}
