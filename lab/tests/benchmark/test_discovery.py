#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from mas.lab.benchmark.run_manager.discovery import iter_metadata_locations


def test_iter_metadata_locations_flat_and_nested_without_duplicates(tmp_path):
    root = tmp_path / "batch-out"
    root.mkdir()
    (root / "metadata.yaml").write_text("experiment: flat\n", encoding="utf-8")

    nested = root / "2026-07-01_12-00-00_abc123"
    nested.mkdir()
    (nested / "metadata.yaml").write_text("experiment: nested\n", encoding="utf-8")

    pairs = list(iter_metadata_locations(root))
    assert len(pairs) == 2
    assert pairs[0][0] == root
    assert pairs[1][0] == nested
    assert len({p[1] for p in pairs}) == 2
