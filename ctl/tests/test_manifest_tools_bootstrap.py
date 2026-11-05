#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bootstrap attaches manifest tool providers to engines."""

from pathlib import Path

import yaml
from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime


def test_instantiate_runtime_attaches_tool_provider(monkeypatch, tmp_path: Path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "ci-test-unused")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calc.py").write_text(
        """
class CalcTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"ok": True}
""",
        encoding="utf-8",
    )
    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {"impl": {"module_path": "./calc.py", "class_name": "CalcTool"}},
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "metadata": {"name": "agent"},
        "spec": {
            "models": [{"id": "main", "model": "gpt-4o"}],
            "tools": [{"ref": "./tools/calculator.tool.yaml"}],
        },
    }
    instance, _ = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=manifest,
            manifest_dir=tmp_path,
            resolved_infra=infra,
            enable_observability=False,
            enable_governance=False,
        )
    )
    from mas.runtime.engine.leaf import leaf_engine

    leaf = leaf_engine(instance.driver.engine)
    assert getattr(leaf, "tool_provider", None) is not None
    assert leaf.tool_provider.has_tools()
