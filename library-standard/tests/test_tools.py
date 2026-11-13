#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest tool loading via Tool YAML refs."""

import asyncio
from pathlib import Path

import pytest
import yaml
from mas.library.standard.plugins.tools.local import LocalToolProvider
from mas.runtime.contracts.tool_contract import ToolContract
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
from mas.runtime.engine.tool_dispatch import execute_engine_tool
from mas.runtime.registry.provider_protocol import ManifestToolLoadError


def _provider_with_calculator(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calculator.py").write_text(
        """
import re

class CalculatorTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        expr = str(args.get("expression") or "")
        if re.search(r"17\\s*[×x*]\\s*23", expr):
            return {"result": 391}
        return {"result": 65536}
""",
        encoding="utf-8",
    )
    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {"impl": {"module_path": "./calculator.py", "class_name": "CalculatorTool"}},
            }
        ),
        encoding="utf-8",
    )
    return build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        tmp_path,
    )


def test_calculator_via_manifest_provider(tmp_path: Path):
    provider = _provider_with_calculator(tmp_path)
    out = execute_engine_tool(
        "calculator",
        user="17 × 23",
        arguments={"expression": "17*23"},
        tool_provider=provider,
    )
    assert "391" in out


def test_web_search_via_manifest_provider(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "web_search.py").write_text(
        """
class WebSearchTool:
    def on_collect_tools(self, **_):
        return [{"name": "web-search", "description": "search", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"query": args.get("query"), "results": []}
""",
        encoding="utf-8",
    )
    (tool_dir / "web-search.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "web-search"},
                "spec": {"impl": {"module_path": "./web_search.py", "class_name": "WebSearchTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/web-search.tool.yaml"}],
        tmp_path,
    )
    out = execute_engine_tool(
        "web-search",
        arguments={"query": "test query"},
        tool_provider=provider,
    )
    assert "test query" in out


def test_memory_search_via_manifest_provider(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "memory_search.py").write_text(
        """
class MemorySearchTool:
    def on_collect_tools(self, **_):
        return [{
            "name": "memory-search",
            "description": "search memory",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }]
    def on_execute_tool(self, name, args, **_):
        if name != "memory-search":
            return None
        return {"query": args.get("query"), "items": [], "text": "no matches"}
""",
        encoding="utf-8",
    )
    (tool_dir / "memory-search.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "memory-search"},
                "spec": {
                    "impl": {
                        "module_path": "./memory_search.py",
                        "class_name": "MemorySearchTool",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/memory-search.tool.yaml"}],
        tmp_path,
    )
    out = execute_engine_tool(
        "memory-search",
        arguments={"query": "Paris trip"},
        tool_provider=provider,
    )
    assert "Paris trip" in out


def test_local_execute_failure_propagates():
    class Boom:
        def on_collect_tools(self, **_):
            return [{"name": "boom"}]

        def on_execute_tool(self, name, args, **_):
            raise RuntimeError("nope")

    provider = LocalToolProvider()
    provider._add_instance(Boom(), {"name": "boom"})
    with pytest.raises(RuntimeError, match="nope"):
        provider.call_tool("boom", {})


def test_from_tool_servers_skips_non_local_and_keeps_the_last_local() -> None:
    from mas.library.standard.plugins.tools.local import LocalToolProvider

    assert LocalToolProvider.from_tool_servers([]) == []
    assert LocalToolProvider.from_tool_servers([{"protocol": "mcp"}]) == []
    providers = LocalToolProvider.from_tool_servers(
        [
            "skip",
            {"id": "local-first", "protocol": "local", "tools": ["calc"]},
            {"id": "local-last", "protocol": "LOCAL", "tools": ["search"]},
        ]
    )
    assert len(providers) == 1
    assert providers[0].provider_name == "local-last"
    assert providers[0].tools_claim == ("search",)


class _AsyncEcho:
    def on_collect_tools(self, **_):
        return [{"name": "echo"}]

    async def on_aexecute_tool(self, name, args, **_):
        await asyncio.sleep(0)
        return {"async": name, **args}

    def on_execute_tool(self, name, args, **_):
        raise AssertionError("sync on_execute_tool must not run when on_aexecute_tool is present")


class _NoneAsync:
    def on_collect_tools(self, **_):
        return [{"name": "empty"}]

    def on_aexecute_tool(self, name, args, **_):
        return None


class _LegacySync:
    def on_collect_tools(self, **_):
        raise NotImplementedError

    def get_name(self):
        return "legacy"

    def on_execute_tool(self, name, args, **_):
        return {"sync": name}


class _AsyncOnExecute:
    def on_collect_tools(self, **_):
        return [{"name": "echo"}]

    async def on_execute_tool(self, name, args, **_):
        await asyncio.sleep(0)
        return {"one-method": name, **args}


class _Blind:
    def on_collect_tools(self, **_):
        return [{"name": "other"}]

    def get_name(self):
        raise RuntimeError("no name")


@pytest.mark.asyncio
async def test_acall_tool_prefers_on_aexecute_tool():
    provider = LocalToolProvider()
    provider._add_instance(_AsyncEcho(), {"name": "echo"})
    out = await provider.acall_tool("echo", {"q": 1})
    assert out == {"async": "echo", "q": 1}


@pytest.mark.asyncio
async def test_acall_tool_none_from_on_aexecute_tool_is_empty_string():
    provider = LocalToolProvider()
    provider._add_instance(_NoneAsync(), {"name": "empty"})
    assert await provider.acall_tool("empty", {}) == ""


@pytest.mark.asyncio
async def test_acall_tool_awaits_async_on_execute_tool():
    provider = LocalToolProvider()
    provider._add_instance(_AsyncOnExecute(), {"name": "echo"})
    out = await provider.acall_tool("echo", {"q": 1})
    assert out == {"one-method": "echo", "q": 1}


class _AsyncExecute(ToolContract):
    """One method, the ``ToolContract`` way: ``async def execute``."""

    def get_name(self):
        return "slow"

    async def execute(self, **kwargs):
        await asyncio.sleep(0.2)
        return {"slow": kwargs}


class _SlowOnExecute:
    def on_collect_tools(self, **_):
        return [{"name": "nap"}]

    async def on_execute_tool(self, name, args, **_):
        await asyncio.sleep(0.2)
        return {"nap": args["i"]}


@pytest.mark.asyncio
async def test_acall_tool_awaits_async_execute_of_a_tool_contract():
    provider = LocalToolProvider()
    provider._add_instance(_AsyncExecute(), None)
    assert await provider.acall_tool("slow", {"x": 1}) == {"slow": {"x": 1}}


@pytest.mark.asyncio
async def test_a_batch_of_async_on_execute_tools_overlaps_on_the_loop():
    import time

    from mas.runtime.engine.tool_workers import ToolPlacement, bind_placement, unbind_placement

    provider = LocalToolProvider()
    provider._add_instance(_SlowOnExecute(), None)
    token = bind_placement(ToolPlacement(worker="inline"))
    try:
        started = time.monotonic()
        results = await asyncio.gather(*(provider.acall_tool("nap", {"i": i}) for i in range(5)))
        elapsed = time.monotonic() - started
    finally:
        unbind_placement(token)
    assert results == [{"nap": i} for i in range(5)]
    assert elapsed < 0.6  # five 0.2 s calls on one loop, not one after another (1.0 s)


def test_the_sync_path_runs_an_async_tool_to_completion():
    provider = LocalToolProvider()
    provider._add_instance(_AsyncOnExecute(), {"name": "echo"})
    provider._add_instance(_AsyncExecute(), None)
    assert provider.call_tool("echo", {"q": 1}) == {"one-method": "echo", "q": 1}
    assert provider.call_tool("slow", {"x": 2}) == {"slow": {"x": 2}}


class _LegacyAsyncExecute:
    def on_collect_tools(self, **_):
        raise NotImplementedError

    def get_name(self):
        return "legacy-async"

    def on_execute_tool(self, name, args, **_):
        raise NotImplementedError

    async def execute(self, **kwargs):
        return {"via": "execute", **kwargs}


class _NoneThenAsyncExecute:
    def on_collect_tools(self, **_):
        raise NotImplementedError

    def get_name(self):
        return "none-exec"

    def on_execute_tool(self, name, args, **_):
        return None

    async def execute(self, **kwargs):
        return {"via": "execute-after-none"}


def test_call_tool_finishes_async_execute_when_on_execute_tool_is_missing():
    provider = LocalToolProvider()
    provider._add_instance(_LegacyAsyncExecute(), None)
    assert provider.call_tool("legacy-async", {"n": 1}) == {"via": "execute", "n": 1}


def test_call_tool_finishes_async_execute_when_on_execute_tool_returns_none():
    provider = LocalToolProvider()
    provider._add_instance(_NoneThenAsyncExecute(), None)
    assert provider.call_tool("none-exec", {}) == {"via": "execute-after-none"}


@pytest.mark.asyncio
async def test_call_tool_from_a_running_loop_tells_you_to_use_acall_tool():
    provider = LocalToolProvider()
    provider._add_instance(_AsyncOnExecute(), {"name": "echo"})
    with pytest.raises(RuntimeError, match="use acall_tool"):
        provider.call_tool("echo", {"q": 3})


@pytest.mark.asyncio
async def test_acall_tool_runs_sync_owner_in_a_worker():
    provider = LocalToolProvider()
    provider._add_instance(_LegacySync(), {"name": "legacy"})
    assert await provider.acall_tool("legacy", {}) == {"sync": "legacy"}


@pytest.mark.asyncio
async def test_acall_tool_falls_back_when_no_owner():
    provider = LocalToolProvider()
    provider._add_instance(_Blind(), {"name": "other"})
    with pytest.raises(ManifestToolLoadError, match="not found"):
        await provider.acall_tool("missing", {})


@pytest.mark.asyncio
async def test_acall_tool_system_plugin_uses_call_tool():
    class _Sys:
        def claims(self, name):
            return name == "sys"

        def call(self, name, arguments, context=None):
            return {"via": "system", "name": name, "args": arguments}

    provider = LocalToolProvider()
    provider.system_plugins = _Sys()
    assert await provider.acall_tool("sys", {"k": 2}) == {"via": "system", "name": "sys", "args": {"k": 2}}


@pytest.mark.asyncio
async def test_acall_tool_awaits_async_system_plugin():
    class _SysAsync:
        def claims(self, name):
            return name == "sys"

        async def call(self, name, arguments, context=None):
            await asyncio.sleep(0)
            return {"via": "async-system", "name": name}

    provider = LocalToolProvider()
    provider.system_plugins = _SysAsync()
    assert await provider.acall_tool("sys", {}) == {"via": "async-system", "name": "sys"}


class _WrappedCoroutine:
    """A plain ``def on_execute_tool`` that hands back a coroutine through a ``functools.wraps`` decorator."""

    def on_collect_tools(self, **_):
        return [{"name": "wrapped"}]

    @staticmethod
    def _decorate(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    async def _impl(self, name, args, **_):
        return {"wrapped": True}

    def on_execute_tool(self, name, args, **kw):
        return self._decorate(self._impl)(name, args, **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker", ["inline", "thread"])
async def test_a_wrapped_coroutine_from_a_sync_on_execute_tool_is_awaited(worker):
    from mas.runtime.engine.tool_workers import ToolPlacement, bind_placement, unbind_placement

    provider = LocalToolProvider()
    inst = _WrappedCoroutine()
    # the decorator hides the coroutine function from a plain iscoroutinefunction check
    inst.on_execute_tool = inst._decorate(inst._impl)
    provider._add_instance(inst, None)
    token = bind_placement(ToolPlacement(worker=worker))
    try:
        assert await provider.acall_tool("wrapped", {}) == {"wrapped": True}
    finally:
        unbind_placement(token)


class _NoneThenLegacyExecute:
    """Listed by ``get_name`` only; ``on_aexecute_tool`` has no answer, so the legacy ``execute`` does."""

    def get_name(self):
        return "legacy"

    def on_collect_tools(self, **_):
        raise NotImplementedError

    def on_execute_tool(self, name, args, **_):
        return None

    async def on_aexecute_tool(self, name, args, **_):
        return None

    async def execute(self, **kwargs):
        return {"legacy": kwargs}


class _NoneFromListedTool:
    def on_collect_tools(self, **_):
        return [{"name": "quiet"}]

    async def on_aexecute_tool(self, name, args, **_):
        return None


@pytest.mark.asyncio
async def test_a_none_answer_matches_the_sync_path():
    legacy = LocalToolProvider()
    legacy._add_instance(_NoneThenLegacyExecute(), None)
    assert await legacy.acall_tool("legacy", {"x": 1}) == {"legacy": {"x": 1}}

    quiet = LocalToolProvider()
    quiet._add_instance(_NoneFromListedTool(), None)
    assert await quiet.acall_tool("quiet", {}) == ""


def test_a_none_answer_matches_the_sync_path_for_legacy_execute():
    legacy = LocalToolProvider()
    legacy._add_instance(_NoneThenLegacyExecute(), None)
    assert legacy.call_tool("legacy", {"x": 1}) == {"legacy": {"x": 1}}


class _NoneThenUnimplementedExecute(_NoneThenLegacyExecute):
    async def execute(self, **kwargs):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_a_none_answer_with_an_unimplemented_legacy_execute_is_empty():
    provider = LocalToolProvider()
    provider._add_instance(_NoneThenUnimplementedExecute(), None)
    assert await provider.acall_tool("legacy", {}) == ""
