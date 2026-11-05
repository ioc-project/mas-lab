#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab golden fixture manifest and optional fingerprint parity."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.golden.labs import DEFAULT_MANIFEST, load_labs_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / DEFAULT_MANIFEST
GOLDEN_ROOT = ROOT / "tests/fixtures/golden-runs"


@pytest.fixture(scope="module")
def labs_manifest() -> dict[str, Path]:
    return load_labs_manifest(MANIFEST, root=ROOT)


def test_labs_manifest_entries_exist(labs_manifest: dict[str, Path]) -> None:
    assert len(labs_manifest) >= 3
    for label, exp in labs_manifest.items():
        assert exp.is_file(), f"missing experiment for {label}: {exp}"


@pytest.mark.parametrize(
    "label",
    ["design-space", "lifecycle-control", "extensions"],
)
def test_lab_golden_fingerprint_when_present(label: str) -> None:
    """When a lab golden fixture is captured, events.sha256 must match events.jsonl."""
    golden_dir = GOLDEN_ROOT / label
    fp_path = golden_dir / "events.sha256"
    events_path = golden_dir / "events.jsonl"
    if not fp_path.is_file() or not events_path.is_file():
        pytest.fail(
            f"golden fixture not captured for {label} — run: python scripts/capture_golden_run.py --labs {label}"
        )

    from mas.lab.benchmark.golden.events import events_fingerprint, normalize_events_file

    expected = fp_path.read_text(encoding="utf-8").strip()
    actual = events_fingerprint(normalize_events_file(events_path))
    assert actual == expected


def test_resolve_lab_spec_accepts_directory() -> None:
    from mas.lab.benchmark.golden.labs import resolve_lab_spec

    label, exp = resolve_lab_spec(
        "labs/extensions.lab",
        root=ROOT,
        manifest=load_labs_manifest(MANIFEST, root=ROOT),
    )
    assert label == "extensions"
    assert exp.is_file()
