#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import yaml
from mas.lab.lab.config.artifact_types import (
    artefact_address,
    list_artifact_types,
    register_artifact_type,
)
from mas.lab.lab.config.pipeline import ArtifactSpec

_REPO = Path(__file__).resolve().parents[4]


def test_discovers_types_from_library_manifest() -> None:
    types = list_artifact_types()
    assert types["trace"]["format"] == "jsonl"
    assert types["run_info"]["format"] == "json"
    assert types["plot"]["format"] == "png"
    assert types["metrics"]["format"] == "json"
    assert types["dataframe"]["format"] == "csv"


def test_artifact_type_names_come_from_library_lab_yaml() -> None:
    doc = yaml.safe_load(
        (_REPO / "library-lab" / "library.yaml").read_text(encoding="utf-8")
    )
    plugins = doc["plugins"]
    assert isinstance(plugins, list)
    declared = [entry["name"] for entry in plugins if entry.get("type") == "artifact"]
    assert declared, "library-lab must declare type: artifact plugins"
    types = list_artifact_types()
    assert set(declared) <= set(types)


def test_register_artifact_type_is_visible() -> None:
    register_artifact_type(
        "embeddings-test",
        path="{level_dir}/emb.npy",
        format="npy",
        description="test type",
    )
    types = list_artifact_types()
    assert types["embeddings-test"]["format"] == "npy"
    assert types["plot"]["format"] == "png"


def test_realtime_chat_and_topology_register_an_address() -> None:
    types = list_artifact_types()
    assert types["chat"]["stream"] == "custom.chat"
    assert types["chat"]["plot"] == "chat"
    assert types["chat"]["realtime"] is True
    assert types["topology"]["stream"] == "custom.topology"
    assert types["chat"]["address"].endswith("/{name}")
    href = artefact_address("chat", {"library": "demo", "experiment": "e1", "run": "r1"})
    assert href == "/api/artefacts/demo/e1/-/-/r1/chat"
    assert types["series"]["stream"] == "series"
    assert types["series"]["plot"] == "loop"
    assert types["series"]["realtime"] is True
    from mas.library.lab import artifact_types as lab_types

    assert not hasattr(lab_types, "MasLoopSeries")
    assert not hasattr(lab_types, "StorySnapshot")
    assert lab_types.LoopSeries.stream == "series"


def test_unknown_type_has_empty_format() -> None:
    spec = ArtifactSpec(name="x", type="not-a-registered-type")
    assert spec.format == ""
    assert spec.relative_path() == Path("x")
