#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import yaml

from mas.lab.lab.config.execution import split_legacy_execution

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "migrate_experiment_execution.py"


def _load_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("migrate_experiment_execution", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_split_legacy_execution_maps_fields() -> None:
    mapped = split_legacy_execution(
        {
            "parallel_scenarios": 8,
            "strategy": "depth",
            "n_runs": 9,
            "design": {"mode": "coupled"},
            "emulation": {"runtime": {"cache": "disabled"}},
            "replay": {"mode": "scripted"},
        }
    )
    assert mapped["schedule"]["parallel_scenarios"] == 8
    assert mapped["schedule"]["ordering"] == "depth"
    assert "n_runs" not in mapped.get("schedule", {})
    assert mapped["design"]["mode"] == "coupled"
    assert mapped["bench_emulation"]["runtime"]["cache"] == "disabled"
    assert mapped["replay"]["mode"] == "scripted"


def test_codemod_rewrites_fixture(tmp_path: Path) -> None:
    before = tmp_path / "experiment.yaml"
    before.write_text(
        "#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates\n"
        "#  SPDX-License-Identifier: Apache-2.0\n"
        "experiment:\n"
        "  name: fixture\n"
        "  applications:\n"
        "    - manifest: ./mas.yaml\n"
        "  run:\n"
        "    n_runs: 3\n"
        "  execution:\n"
        "    parallel_scenarios: 2\n"
        "    timeout: 300\n"
        "    strategy: coverage\n"
        "    emulation:\n"
        "      runtime:\n"
        "        cache: disabled\n",
        encoding="utf-8",
    )
    expected = {
        "schedule": {
            "parallel_scenarios": 2,
            "timeout": 300,
            "ordering": "coverage",
        },
        "bench_emulation": {"runtime": {"cache": "disabled"}},
    }
    mod = _load_script()
    assert mod.migrate_file(before) is True
    data = yaml.safe_load(before.read_text(encoding="utf-8"))
    exp = data["experiment"]
    assert "execution" not in exp
    assert exp["schedule"] == expected["schedule"]
    assert exp["bench_emulation"] == expected["bench_emulation"]
    assert exp["run"]["n_runs"] == 3
    assert "Copyright" in before.read_text(encoding="utf-8")


def test_discover_includes_library_samples() -> None:
    mod = _load_script()
    discovered = {str(p.relative_to(ROOT)) for p in mod._discover(ROOT)}
    assert "library-samples/experiments/moderator-design-patterns.yaml" in discovered


def test_codemod_is_noop_without_execution(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: already-migrated\n"
        "  schedule:\n"
        "    ordering: coverage\n",
        encoding="utf-8",
    )
    mod = _load_script()
    assert mod.migrate_file(path) is False
