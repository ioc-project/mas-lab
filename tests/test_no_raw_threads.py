#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Threads are started with ``mas.runtime.aio.new_thread``, never with ``threading.Thread``.

The free-threaded build starts a thread with a copy of the creator's context variables; the other builds
start it empty. ``new_thread`` gives every build the empty one, so the runtime's per-call state does not
leak into worker threads and no interpreter setting is needed.
"""

from __future__ import annotations

import ast
import inspect
import threading
from pathlib import Path

from mas.runtime.aio import new_thread

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    "runtime/src",
    "ctl/src",
    "library-standard/src",
    "library-ioa/src",
    "library-skills/src",
    "library-eval/src",
    "library-telemetry/src",
    "library-kg/src",
    "library-lab/src",
    "library-samples/src",
    "lab/components/core/src",
    "lab/components/bench/src",
    "lab/components/controller/src",
)
# The helper itself.
ALLOWED = {"runtime/src/mas/runtime/aio.py"}
THREAD_CLASSES = {"Thread", "Timer"}


def _threading_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names bound to the ``threading`` module and to its ``Thread`` and ``Timer`` classes."""
    modules, classes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "threading")
        elif isinstance(node, ast.ImportFrom) and node.module == "threading":
            classes.update(alias.asname or alias.name for alias in node.names if alias.name in THREAD_CLASSES)
    return modules, classes


def _is_thread_class(node: ast.expr, modules: set[str], classes: set[str]) -> bool:
    if isinstance(node, ast.Attribute) and node.attr in THREAD_CLASSES:
        return isinstance(node.value, ast.Name) and node.value.id in modules
    return isinstance(node, ast.Name) and node.id in classes


def _raw_thread_uses(path: Path) -> list[int]:
    """Lines that call ``threading.Thread`` or ``Timer`` (under any alias) or subclass them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, classes = _threading_names(tree)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_thread_class(node.func, modules, classes):
            lines.append(node.lineno)
        elif isinstance(node, ast.ClassDef) and any(_is_thread_class(base, modules, classes) for base in node.bases):
            lines.append(node.lineno)
    return sorted(lines)


def test_no_source_file_starts_a_raw_thread() -> None:
    offenders = []
    for root in SOURCE_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWED:
                continue
            offenders.extend(f"{rel}:{line}" for line in _raw_thread_uses(path))
    assert not offenders, "use mas.runtime.aio.new_thread: " + ", ".join(offenders)


def test_the_allowed_files_exist() -> None:
    for rel in ALLOWED:
        assert (REPO_ROOT / rel).is_file(), rel


def test_the_scan_sees_a_raw_thread(tmp_path) -> None:
    sample = tmp_path / "s.py"
    sample.write_text(
        "import threading\n"
        "import threading as th\n"
        "from threading import Thread as T\n"
        "threading.Thread(target=f)\n"
        "T(target=f)\n"
        "threading.Timer(1, f)\n"
        "th.Thread(target=f)\n"
        "class Worker(threading.Thread):\n"
        "    pass\n"
        "class Other(T):\n"
        "    pass\n"
        "threading.Event()\n"
    )
    assert _raw_thread_uses(sample) == [4, 5, 6, 7, 8, 10]


def _new_thread_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "new_thread"
    ]


def test_every_new_thread_call_uses_only_what_new_thread_accepts() -> None:
    """A keyword ``new_thread`` lacks (``Thread`` has it) fails only when the call runs; catch it here."""
    accepted = {p.name for p in inspect.signature(new_thread).parameters.values()}
    problems = []
    for root in SOURCE_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            for call in _new_thread_calls(path):
                rel = f"{path.relative_to(REPO_ROOT).as_posix()}:{call.lineno}"
                keywords = {kw.arg for kw in call.keywords}
                if None in keywords:  # **kwargs: cannot be checked
                    continue
                if not keywords <= accepted:
                    problems.append(f"{rel}: unexpected {sorted(keywords - accepted)}")
                if not call.args and "target" not in keywords:
                    problems.append(f"{rel}: no target")
    assert not problems, problems


def test_new_thread_accepts_every_thread_argument_the_runtime_uses() -> None:
    """The arguments of ``threading.Thread`` that a thread start can sensibly pass."""
    thread = set(inspect.signature(threading.Thread.__init__).parameters)
    assert {"target", "args", "kwargs", "name", "daemon"} <= set(inspect.signature(new_thread).parameters) & thread
