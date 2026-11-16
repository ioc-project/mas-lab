#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Layers and pipeline steps are plugins a library registers. The runtime ships none and names none."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from mas.runtime.layers.compose import layers_declaring, response_store_layer
from mas.runtime.layers.defaults import default_pipeline_steps

ROOT = Path(__file__).resolve().parents[1]
# The layers the libraries register. A runtime or ctl source that spells one as a literal has hardcoded it.
_LAYER_NAMES = ("retry", "circuit_breaker", "llm_cache", "llm_scheduler", "fault_inject", "http", "openai", "mcp")
_RUNTIME_LAYER_SOURCES = (
    "runtime/src/mas/runtime/layers",
    "runtime/src/mas/runtime/reliability",
    "runtime/src/mas/runtime/engine/infra_pipeline.py",
    "runtime/src/mas/runtime/engine/llm_live.py",
    "runtime/src/mas/runtime/registry/llm_provider_registry.py",
    "ctl/src/mas/ctl/session/engine_factory.py",
)


def _sources() -> list[Path]:
    out: list[Path] = []
    for relative in _RUNTIME_LAYER_SOURCES:
        path = ROOT / relative
        out.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return out


def test_runtime_names_no_layer() -> None:
    quoted = re.compile(r"""["'](%s)["']""" % "|".join(_LAYER_NAMES))
    found = {
        f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}"
        for path in _sources()
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if quoted.search(line) and not line.lstrip().startswith(("#", "LEGACY_LAYER_TYPES"))  # the deprecated *types*
    }
    assert not found, "runtime/ctl name a layer a library registers:\n" + "\n".join(sorted(found))


def test_runtime_ships_no_pipeline_data() -> None:
    shipped = sorted(p.name for p in (ROOT / "runtime/src/mas/runtime/layers").glob("*.yaml"))
    assert shipped == [], f"default steps belong on a library row, not in the runtime: {shipped}"


def test_default_steps_and_control_keys_come_from_library_rows() -> None:
    assert default_pipeline_steps(), "no layer row declares default_steps"
    assert {key for _name, key in layers_declaring("control_key")} >= {"retry", "circuit_breaker"}
    assert response_store_layer()


def test_every_layer_row_lives_in_a_library() -> None:
    rows = 0
    for manifest in ROOT.glob("library-*/library.yaml"):
        for plugin in (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("plugins") or []:
            if plugin.get("type") == "layer":
                rows += 1
                assert str(plugin.get("module", "")).split(".")[0] in {"mas", "library_ioa", "library_skills"}, plugin
    assert rows


def test_ctl_connect_schemes_come_from_protocol_rows() -> None:
    source = (ROOT / "ctl/src/mas/ctl/overrides/bind.py").read_text(encoding="utf-8").lower()
    assert "a2a" not in source and "mcp" not in source, "ctl names a protocol; the plugin row declares its schemes"
    from mas.ctl.overrides.bind import expand_connects, expand_listens

    connect = expand_connects(("peer=a2a://host:1/x", "tool=mcp://host:2/m#t"))
    assert any("spec.endpoints" in item and "a2a" in item for item in connect)
    assert any("spec.tool_servers" in item and "mcp" in item for item in connect)
    assert expand_listens(("agent=a2a://127.0.0.1:9/a",))
