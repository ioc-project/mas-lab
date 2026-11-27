#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A run-wide ``.infra`` model-mapping override reaches the LLM leaf the engine reads."""

from __future__ import annotations

from pathlib import Path

from mas.ctl.compose.runner import resolve_workspace_infra
from mas.ctl.workspace.config import WorkspaceConfig

INFRA = """\
apiVersion: infra/v1
kind: LLMProxy
metadata: {name: proxy}
spec:
  proxy:
    api_base: https://example.invalid/v1
    api_key: {secret: openai}
  models:
    mappings:
      gpt-4o: azure/gpt-4o
"""


def _resolve(tmp_path: Path, overrides: list[str]):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "proxy.yaml").write_text(INFRA, encoding="utf-8")
    (tmp_path / "config.yaml").write_text("infra_refs:\n  - infra/proxy.yaml\n", encoding="utf-8")
    return resolve_workspace_infra(WorkspaceConfig.load(tmp_path), anchor=tmp_path, cli_refs=[], overrides=overrides)


def test_an_added_mapping_reaches_the_leaf(tmp_path: Path) -> None:
    # Kills: the override changing the merged view only (the model name went out unmapped, HTTP 403).
    resolved = _resolve(tmp_path, [".infra.spec.models.mappings.lab-model=azure/gpt-4o-mini"])
    leaf = next(iter(resolved.llm_proxies.values()))
    assert leaf["mappings"]["lab-model"] == "azure/gpt-4o-mini" and leaf["mappings"]["gpt-4o"] == "azure/gpt-4o"
    assert resolved.llm_proxy["mappings"]["lab-model"] == "azure/gpt-4o-mini"


def test_a_changed_mapping_replaces_the_old_target(tmp_path: Path) -> None:
    leaf = next(iter(_resolve(tmp_path, [".infra.spec.models.mappings.gpt-4o=azure/gpt-4.1"]).llm_proxies.values()))
    assert leaf["mappings"]["gpt-4o"] == "azure/gpt-4.1"
