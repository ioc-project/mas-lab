#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for typed InfraBundle spec.entries[] resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.infra.pipeline_chain import BidirectionalInfraPipeline, InfraChainContext
from mas.ctl.infra.resolve import (
    InfraResolveError,
    _load_file,
    bidirectional_pipeline_for,
    resolve_infra_refs,
)
from mas.ctl.validate import validate_file
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
from mas.runtime.engine.infra_pipeline import middleware_id


def test_bidirectional_pipeline_forward_short_circuit():
    calls: list[str] = []

    def cache(ctx: InfraChainContext):
        calls.append(f"fwd:{ctx.entry_id}")
        return {"content": "cached"}

    def llm(ctx: InfraChainContext):
        calls.append(f"fwd:{ctx.entry_id}")
        return None

    pipe = BidirectionalInfraPipeline(
        forward_handlers=[("cache", cache), ("llm", llm)],
    )
    ctx = InfraChainContext(query={"messages": []})
    reply, handled = pipe.forward_query(ctx)
    assert handled is True
    assert reply == {"content": "cached"}
    assert calls == ["fwd:cache"]


def test_bidirectional_pipeline_backward_transform():
    def cache_reply(ctx: InfraChainContext):
        return {**ctx.query, "cached": True}

    pipe = BidirectionalInfraPipeline(
        forward_handlers=[("cache", lambda _c: None)],
        reply_handlers=[("cache", cache_reply)],
    )
    ctx = InfraChainContext(query={"messages": []}, correlation_id="abc")
    out = pipe.backward_reply(ctx, {"content": "hello"})
    assert out == {"content": "hello", "cached": True}


def test_bidirectional_pipeline_from_resolved_infra(tmp_path: Path):
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    pipe = bidirectional_pipeline_for(infra.llm_proxy)
    assert isinstance(pipe, BidirectionalInfraPipeline)
    ctx = InfraChainContext(query={"messages": [{"role": "user", "content": "hi"}]})
    reply, handled = pipe.forward_query(ctx)
    assert handled is False
    assert reply is None


def test_infra_bundle_entries_loads_llm_proxy_cached(tmp_path: Path, monkeypatch):
    bundle = Path(__file__).resolve().parents[2] / (
        "library-standard/src/mas/library/standard/libs/standard/llm-proxy-cached.yaml"
    )
    if not bundle.is_file():
        pytest.skip("library-standard bundle not in workspace")

    ws = WorkspaceConfig.load(tmp_path)
    merged = _load_file(bundle, workspace=ws)
    assert merged.kind == "InfraBundle"
    leaf_steps = [
        step
        for leaf in (merged.providers or {}).values()
        for step in (leaf.get("pipeline") or [])
        if isinstance(step, dict)
    ]
    shared = [step for step in (merged.pipeline or []) if isinstance(step, dict)]
    assert any(step.get("middleware") == "llm_cache" for step in shared + leaf_steps)
    assert merged.proxy.api_base or merged.models.default_llm is not None or merged.name

    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    infra = resolve_infra_refs(["standard:llm-proxy-cached"], anchor=tmp_path, workspace=ws)
    assert "llm_cache" in [middleware_id(step) for step in infra.llm_proxy.get("pipeline") or []]


def test_relative_llm_cache_path_is_resolved_from_infra_manifest_dir(tmp_path: Path):
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    (infra_dir / "llm-cache-write.yaml").write_text(
        """
apiVersion: infra/v1
kind: InfraMiddleware
metadata:
  name: llm-cache-write
spec:
  middleware: llm_cache
  applies_to: [LLM_CALL]
  params:
    allow_read: true
    allow_write: true
    cache_path: .cache/demo-cache.json
""".strip(),
        encoding="utf-8",
    )

    infra = resolve_infra_refs(["infra/llm-cache-write.yaml"], anchor=tmp_path)
    pipeline = infra.llm_proxy.get("pipeline") or []
    assert pipeline
    params = pipeline[0]["params"]
    assert Path(params["cache_path"]).is_absolute()
    assert Path(params["cache_path"]) == (infra_dir / ".cache" / "demo-cache.json").resolve()


def test_infra_override_creates_missing_schema_declared_param(tmp_path: Path):
    manifest = tmp_path / "cache.yaml"
    manifest.write_text(
        "apiVersion: infra/v1\nkind: InfraMiddleware\nmetadata:\n  name: cache\n"
        "spec:\n  middleware: llm_cache\n  applies_to: [LLM_CALL]\n"
        "  params:\n    allow_read: true\n    raise_on_miss: true\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs(
        [str(manifest)],
        anchor=tmp_path,
        overrides=("infra:spec.interceptors[middleware=llm_cache].params.allow_write=true",),
    )

    params = infra.llm_proxy["pipeline"][0]["params"]
    assert params["allow_read"] is True
    assert params["allow_write"] is True
    assert params["raise_on_miss"] is True


def test_infra_override_updates_cache_params_without_dropping_pipeline(tmp_path: Path):
    manifest = tmp_path / "cache.yaml"
    manifest.write_text(
        "apiVersion: infra/v1\nkind: InfraMiddleware\nmetadata:\n  name: cache\n"
        "spec:\n  middleware: llm_cache\n  applies_to: [LLM_CALL]\n"
        "  params:\n    allow_read: true\n    allow_write: false\n    raise_on_miss: true\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs(
        [str(manifest)],
        anchor=tmp_path,
        overrides=("infra:spec.interceptors[middleware=llm_cache].params.allow_write=true",),
    )

    pipeline = infra.llm_proxy["pipeline"]
    assert len(pipeline) == 1
    assert pipeline[0]["params"]["allow_write"] is True
    assert pipeline[0]["params"]["raise_on_miss"] is True


def test_infra_overrides_reach_llm_mcp_a2a_and_runtime(tmp_path: Path):
    from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware

    source = Path(__file__).parents[2] / "docs/schemas/examples/infra/override-stack.yaml"
    validate_file(source, kind="infra").raise_if_failed()
    manifest = tmp_path / "stack.yaml"
    original = source.read_text(encoding="utf-8")
    manifest.write_text(original, encoding="utf-8")
    infra = resolve_infra_refs(
        [str(manifest)],
        anchor=tmp_path,
        overrides=(
            'infra:spec.proxy.api_base="http://localhost:9100/v1"',
            "infra:spec.interceptors[middleware=llm_cache].params.allow_write=true",
            "infra:spec.interceptors[middleware=llm_cache].params.raise_on_miss=false",
            'infra:spec.tool_servers[id=tools].url="http://localhost:9101/mcp"',
            'infra:spec.endpoints["peer"].url="http://localhost:9102/agent"',
            "infra:spec.runtime_engine.cache.write=true",
        ),
    )

    assert infra.llm_proxy["api_base"] == "http://localhost:9100/v1"
    pipeline = infra.llm_proxy["pipeline"]
    assert [step["middleware"] for step in pipeline] == ["llm_cache"]
    leaf = next(iter(infra.llm_proxies.values()))
    assert [step["middleware"] for step in leaf.get("pipeline") or []] == ["fault_inject"]
    cache = LlmCacheMiddleware.wrap(object(), pipeline[0]["params"])
    assert cache.allow_read is True
    assert cache.allow_write is True
    assert cache.raise_on_miss is False
    assert cache.cache_path == tmp_path / "cache/llm-cache.json"
    assert infra.tool_server_registry["tools"]["url"] == "http://localhost:9101/mcp"
    assert infra.applications["peer"]["url"] == "http://localhost:9102/agent"
    assert infra.runtime_engine["cache"]["write"] is True
    assert manifest.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "assignment",
    [
        'infra:spec.interceptors[middleware=llm_cache].params.allow_write="false"',
        "infra:spec.interceptors[middleware=llm_cache].params.allow_write=1",
        'infra:spec.proxy.timeout="slow"',
    ],
)
def test_resolved_infra_rejects_invalid_override_types(tmp_path: Path, assignment: str):
    source = Path(__file__).parents[2] / "docs/schemas/examples/infra/override-stack.yaml"
    with pytest.raises(ValueError):
        resolve_infra_refs([str(source)], anchor=tmp_path, overrides=(assignment,))


def test_relative_ref_resolves_from_anchor_not_process_cwd(tmp_path: Path, monkeypatch):
    """A same-named file in the process CWD must never shadow the anchor-relative ref."""
    project_dir = tmp_path / "anchor-project"
    infra_dir = project_dir / "infra"
    infra_dir.mkdir(parents=True)
    (infra_dir / "llm-cache-write.yaml").write_text(
        """
apiVersion: infra/v1
kind: InfraMiddleware
metadata:
  name: llm-cache-write
spec:
  middleware: llm_cache
  applies_to: [LLM_CALL]
  params:
    cache_path: .cache/from-anchor.json
""".strip(),
        encoding="utf-8",
    )

    decoy_dir = tmp_path / "decoy-cwd"
    decoy_infra_dir = decoy_dir / "infra"
    decoy_infra_dir.mkdir(parents=True)
    (decoy_infra_dir / "llm-cache-write.yaml").write_text(
        """
apiVersion: infra/v1
kind: InfraMiddleware
metadata:
  name: llm-cache-write
spec:
  middleware: llm_cache
  applies_to: [LLM_CALL]
  params:
    cache_path: .cache/from-decoy-cwd.json
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(decoy_dir)
    infra = resolve_infra_refs(["infra/llm-cache-write.yaml"], anchor=project_dir)
    pipeline = infra.llm_proxy.get("pipeline") or []
    assert pipeline
    cache_path = Path(pipeline[0]["params"]["cache_path"])
    assert cache_path == (infra_dir / ".cache" / "from-anchor.json").resolve()
    assert "from-decoy-cwd" not in cache_path.as_posix()


def test_relative_ref_not_loaded_from_process_cwd_only(tmp_path: Path, monkeypatch):
    """Process CWD must not satisfy a ref that is missing under anchor."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    decoy = tmp_path / "decoy"
    decoy_infra = decoy / "infra"
    decoy_infra.mkdir(parents=True)
    (decoy_infra / "missing-at-anchor.yaml").write_text(
        """
apiVersion: infra/v1
kind: InfraMiddleware
metadata:
  name: decoy
spec:
  middleware: llm_cache
  applies_to: [LLM_CALL]
  params:
    cache_path: .cache/decoy.json
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(decoy)
    with pytest.raises((FileNotFoundError, InfraResolveError)):
        resolve_infra_refs(["infra/missing-at-anchor.yaml"], anchor=project_dir)


def test_library_samples_llm_cache_write_resolves_cache_path():
    repo = Path(__file__).resolve().parents[2]
    manifest = repo / "library-samples" / "infra" / "llm-cache-write.yaml"
    if not manifest.is_file():
        pytest.skip("library-samples/infra/llm-cache-write.yaml not in workspace")
    infra = resolve_infra_refs(["library-samples/infra/llm-cache-write.yaml"], anchor=repo)
    pipeline = infra.llm_proxy.get("pipeline") or []
    assert pipeline and pipeline[0].get("middleware") == "llm_cache"
    cache_path = Path(pipeline[0]["params"]["cache_path"])
    expected = (repo / "library-samples" / "infra" / "cache" / "demo.llm-cache.json").resolve()
    assert cache_path == expected


def test_resolve_infra_uses_workspace_root_when_anchor_omitted():
    """Library/eval paths that pass workspace= only still resolve scheme refs."""
    repo = Path(__file__).resolve().parents[2]
    sample = repo / "library-samples" / "sample-workspace"
    if not (sample / "config.yaml").is_file():
        pytest.skip("library-samples/sample-workspace/config.yaml not in workspace")
    ws = WorkspaceConfig.load(sample)
    assert ws.found and ws.root
    infra = resolve_infra_refs(["standard:openai"], workspace=ws)
    assert infra.refs == ["standard:openai"]
    assert infra.llm_proxy.get("api_key_env")


def test_workspace_infra_refs_resolve_from_subdirectory():
    """Refs like ``standard:openai`` resolve from config root, not anchor only."""
    repo = Path(__file__).resolve().parents[2]
    tutorial = repo / "docs/tutorials/01-building-an-agent"
    sample = repo / "library-samples" / "sample-workspace"
    if not (sample / "config.yaml").is_file():
        pytest.skip("library-samples/sample-workspace/config.yaml not in workspace")
    ws = WorkspaceConfig.load(sample)
    assert ws.found
    infra = resolve_infra_refs(
        ["standard:openai"],
        anchor=tutorial,
        workspace=ws,
    )
    assert infra.llm_proxy.get("api_base")


def test_tool_server_registry_resolves_connection_defaults():
    repo = Path(__file__).resolve().parents[2]
    sample = repo / "library-samples" / "infra" / "mcp-localhost.yaml"
    if not sample.is_file():
        pytest.skip("library-samples/infra/mcp-localhost.yaml not in workspace")
    infra = resolve_infra_refs([str(sample)], anchor=repo)
    server = infra.tool_server_registry["localhost-mcp-tools"]
    assert server["protocol"] == "mcp"
    assert server["url"] == "http://127.0.0.1:9001/mcp"
    assert "transport" not in server
    local = infra.tool_server_registry["local"]
    assert local["protocol"] == "local"
    assert local["tools_dir"] == "tools"
    assert local["skills_dir"] == "skills"


def test_explicit_local_tool_registry_overrides_implicit_defaults(tmp_path: Path):
    manifest = tmp_path / "local-tools.yaml"
    manifest.write_text(
        "apiVersion: infra/v1\n"
        "kind: ToolServerRegistry\n"
        "metadata:\n  name: local-tools\n"
        "spec:\n  tool_servers:\n"
        "    - id: local\n"
        "      protocol: local\n"
        "      tools_dir: app-tools\n"
        "      skills_dir: knowledge\n",
        encoding="utf-8",
    )
    validation = validate_file(manifest, kind="infra")
    assert validation.ok, validation.issues
    infra = resolve_infra_refs([str(manifest)], anchor=tmp_path)
    local = infra.tool_server_registry["local"]
    assert local["tools_dir"] == "app-tools"
    assert local["skills_dir"] == "knowledge"
