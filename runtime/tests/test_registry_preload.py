#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plugin modules are imported once, on one thread, before any run starts."""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.schedule.run_batch import execute, workers
from mas.lab.benchmark.schedule.run_batch.workers import iter_overlay_refs
from mas.runtime.registry.preload import preload_plugins

_A = "import time\ntime.sleep(0.3)\nimport {other}\n"


@pytest.fixture
def circular(tmp_path: Path, monkeypatch):
    """Two modules that import each other after a pause.

    Two threads importing them at first use can deadlock on the per-module lock.
    """
    a, b = "pre_mod_a", "pre_mod_b"
    (tmp_path / f"{a}.py").write_text(_A.format(other=b), encoding="utf-8")
    (tmp_path / f"{b}.py").write_text(_A.format(other=a), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield a, b
    for name in (a, b):
        sys.modules.pop(name, None)


def _registry(modules: dict[str, str], *, missing: frozenset[str] = frozenset()):
    entries = [
        SimpleNamespace(
            urn=f"urn:plugin:{name}",
            shortcuts=[name],
            variants={
                "builtin": SimpleNamespace(
                    module=module,
                    warm=[],
                    missing_requires=lambda m=module: ["absent"] if m in missing else [],
                )
            },
        )
        for name, module in modules.items()
    ]
    return SimpleNamespace(all_entries=lambda: entries, all_aliases=lambda: {})


def _threads_import(*modules: str) -> list[BaseException]:
    errors: list[BaseException] = []

    def work(module: str) -> None:
        try:
            importlib.import_module(module)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(m,)) for m in modules]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_preload_imports_on_the_calling_thread(circular):
    a, b = circular
    caller = threading.current_thread().ident
    imported = preload_plugins(_registry({"first": a, "second": b}))
    assert imported == [a]  # b is pulled in by a, so it is not listed as imported by this call
    assert {a, b} <= sys.modules.keys()
    assert threading.current_thread().ident == caller
    assert _threads_import(a, b, a, b) == []


def test_second_preload_is_a_noop(circular):
    a, _b = circular
    registry = _registry({"first": a})
    assert preload_plugins(registry) == [a]
    assert preload_plugins(registry) == []


def test_every_installed_plugin_is_imported_not_only_the_ones_a_manifest_names(circular):
    a, b = circular
    preload_plugins(_registry({"first": a, "second": b}))
    assert a in sys.modules and b in sys.modules


def test_preload_continues_after_a_module_that_raises(tmp_path: Path, monkeypatch):
    bad, good = "pre_bad", "pre_good"
    (tmp_path / f"{bad}.py").write_text("raise RuntimeError('nope')\n", encoding="utf-8")
    (tmp_path / f"{good}.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        imported = preload_plugins(_registry({"bad": bad, "ok": good}))
        assert imported == [good]
        assert good in sys.modules
    finally:
        for name in (bad, good):
            sys.modules.pop(name, None)


def test_missing_requires_are_skipped(tmp_path: Path, monkeypatch):
    present, skipped = "pre_ok", "pre_skip"
    (tmp_path / f"{present}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / f"{skipped}.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        imported = preload_plugins(_registry({"ok": present, "skip": skipped}, missing={skipped}))
        assert imported == [present]
        assert skipped not in sys.modules
    finally:
        for name in (present, skipped):
            sys.modules.pop(name, None)


def test_execute_batch_preloads_before_the_pool_and_runs():
    source = inspect.getsource(execute._execute_batch)
    preload = source.index("preload_run_path)")
    assert preload < source.index("RunWorkerPool(")
    assert preload < source.index("asyncio.create_task(_guarded")


def test_spawn_worker_uses_iter_overlay_refs():
    assert 'iter_overlay_refs(job.get("overlay_refs"))' in inspect.getsource(workers._run_job)


def test_iter_overlay_refs_keeps_a_string_whole():
    assert iter_overlay_refs("extra.yml") == ["extra.yml"]
    assert iter_overlay_refs("") == []
    assert iter_overlay_refs({"ref": "x.yaml"}) == [{"ref": "x.yaml"}]
    assert iter_overlay_refs(["a.yml", "b.yml"]) == ["a.yml", "b.yml"]


_STRESS = textwrap.dedent(
    """
    import importlib, sys, threading
    from mas.runtime.registry import get_registry
    {before}
    modules = sorted(
        {{v.module for e in get_registry().all_entries() for v in e.variants.values()
          if v.module and not v.missing_requires()}}
    )
    errors = []

    def go(name):
        try:
            importlib.import_module(name)
        except BaseException as exc:
            errors.append(f"{{name}}: {{type(exc).__name__}}: {{exc}}")

    threads = [threading.Thread(target=go, args=(m,)) for m in modules * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    print("ALIVE" if any(t.is_alive() for t in threads) else "DONE")
    for line in errors:
        print(line)
    """
)


def _stress(before: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-I", "-c", _STRESS.format(before=before)],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    ).stdout.splitlines()
    assert out[0] == "DONE", out
    return out[1:]


def test_no_deadlock_when_threads_first_import_every_installed_plugin_after_preload():
    """The real registry, fresh process, 2 threads per module started together: nothing deadlocks."""
    errors = _stress("from mas.runtime.registry.preload import preload_all_plugins; preload_all_plugins()")
    assert errors == []


def test_mcp_plugin_modules_import_concurrently_without_preload():
    """The mcp package ``__init__`` imports nothing, so no submodule pulls in the cycle of the others."""
    code = textwrap.dedent(
        """
        import importlib, threading
        import mas.runtime.registry
        import mcp.server  # the SDK has import cycles of its own; the preload covers those
        names = ["layer", "provider", "classifier", "client", "hooks", "tasks", "spec", "contract"]
        errors = []

        def go(n):
            try:
                importlib.import_module("library_ioa.plugins.mcp." + n)
            except BaseException as exc:
                errors.append(f"{n}: {exc}")

        ts = [threading.Thread(target=go, args=(n,)) for n in names * 4]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
        print(errors)
        """
    )
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, check=True
        )
        assert out.stdout.strip() == "[]", out.stdout


def test_warm_modules_are_imported_when_installed_and_skipped_when_not(tmp_path: Path, monkeypatch):
    plugin, sdk = "pre_plugin", "pre_sdk"
    (tmp_path / f"{plugin}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / f"{sdk}.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    registry = _registry({"p": plugin})
    registry.all_entries()[0].variants["builtin"].warm = [sdk, "pre_sdk_not_installed"]
    try:
        assert preload_plugins(registry) == [plugin, sdk]
        assert sdk in sys.modules
    finally:
        for name in (plugin, sdk):
            sys.modules.pop(name, None)


def test_a2a_and_grpc_stacks_do_not_deadlock_after_preload():
    """The A2A plugin keeps its SDK and grpc imports lazy; its ``warm`` list makes preload import them."""
    pytest.importorskip("a2a")
    pytest.importorskip("grpc")
    code = textwrap.dedent(
        """
        import importlib, threading
        from mas.runtime.registry.preload import preload_all_plugins
        preload_all_plugins()
        names = ["a2a.client", "a2a.utils.grpc_status", "a2a.types.a2a_pb2_grpc", "grpc", "grpc.aio",
                 "a2a.server.request_handlers.grpc_handler", "fastapi", "a2a.server.routes"]
        errors = []

        def go(n):
            try:
                importlib.import_module(n)
            except BaseException as exc:
                errors.append(f"{n}: {exc}")

        ts = [threading.Thread(target=go, args=(n,)) for n in names * 3]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
        print(errors)
        """
    )
    for _ in range(2):
        out = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=180, check=True
        )
        assert out.stdout.strip().splitlines()[-1] == "[]", out.stdout
