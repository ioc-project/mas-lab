#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace

from mas.lab.benchmark.pipeline.config import PipelineEngineSpec, pipeline_settings
from mas.lab.lab.config.execution import EmulationSpec, PipelineEngineSpec as YamlPipelineEngineSpec


def test_v2_config_selects_engine() -> None:
    default = pipeline_settings(SimpleNamespace(bench_emulation=EmulationSpec()))
    assert default.engine == "v2"
    emu = EmulationSpec.from_dict({"pipeline": {"engine": "v1", "shadow": True, "store": "/tmp/arts"}})
    spec = pipeline_settings(SimpleNamespace(bench_emulation=emu))
    assert spec.engine == "v2"
    assert spec.shadow is False
    assert spec.store == "/tmp/arts"
    override = pipeline_settings(None, extra={"pipeline_store": "/x"})
    assert override.store == "/x"
    assert override.engine == "v2"


def test_pipeline_engine_spec_ignores_unknown() -> None:
    spec = PipelineEngineSpec.from_dict({"engine": "v9", "shadow": True})
    assert spec.engine == "v2"
    assert spec.shadow is False
    assert PipelineEngineSpec is YamlPipelineEngineSpec
    assert spec.store_root().name == "artefacts"
