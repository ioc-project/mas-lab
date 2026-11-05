#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The stack, its layers and the chain wiring read declared contracts, not attributes found by name.

What is scanned: every module under ``SCANNED_DIRS`` and every file in ``SCANNED_FILES`` (the
generic stack, the layer plugins, the ``agent_comm/stack`` adapter, ``SendTool`` and ctl's chain
wiring). What counts as a probe, found with ``ast`` (so comments and strings never count):

* any reference to the builtins ``getattr``, ``hasattr`` or ``vars``, called or not, so
  ``g = getattr`` is caught too;
* ``builtins.getattr`` / ``builtins.hasattr`` and any ``attrgetter`` (``operator.attrgetter``
  or imported by name), and any read of ``__dict__``;
* ``callable(x.attr)``, which only makes sense when ``attr`` may be missing;
* ``runtime_checkable``: ``isinstance`` against such a ``Protocol`` is a ``hasattr`` per member;
* a ``__getattr__`` or ``__getattribute__`` method (a proxy that forwards arbitrary names).

Not covered: a probe written through ``try: x.attr / except AttributeError``, through
``inspect``, an ``isinstance`` against a ``runtime_checkable`` protocol declared outside the
scanned set (``is_transport`` checks a link class against the runtime's released
``AgentCommContract`` this way), or a file outside the scanned set.

``ALLOWED`` lists every probe left in the scanned set, by file, enclosing function and kind,
with the reason it stays. The test fails on a probe that is not listed and on a listed probe
that is gone, so the list is exact, not a ceiling.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STD = "library-standard/src/mas/library/standard"
IOA = "library-ioa/src/library_ioa/plugins"

SCANNED_DIRS = [
    f"{STD}/lib/stack",
    f"{IOA}/slim",
    f"{IOA}/http_layer",
]
SCANNED_FILES = [
    f"{STD}/lib/comm/compose.py",
    f"{STD}/lib/comm/endpoint.py",
    f"{STD}/lib/comm/names.py",
    f"{STD}/plugins/agentcomm/stack.py",
    f"{STD}/plugins/agentcomm/local_group.py",
    f"{STD}/plugins/system_tools/send.py",
    f"{STD}/testing/layer_conformance.py",
    f"{IOA}/a2a/layer.py",
    f"{IOA}/a2a/agent_comm.py",
    "ctl/src/mas/ctl/cli/chains.py",
    "ctl/src/mas/ctl/executor/stacks.py",
    "ctl/src/mas/ctl/infra/bind.py",
    "ctl/src/mas/ctl/infra/identities.py",
]

_RELEASED = "released code, the same probe as before the layer stack"
ALLOWED: dict[tuple[str, str, str], tuple[int, str]] = {
    (f"{STD}/lib/stack/legacy.py", "_probe", "getattr"): (
        1,
        "the one lookup by name: links and BoundLayer decorators released before layers declare no type",
    ),
    (f"{STD}/plugins/system_tools/send.py", "SendTool._inspect", "getattr"): (
        2,
        f"{_RELEASED}: SendTool reads the signature of whatever send/asend a comm offers",
    ),
    (f"{STD}/plugins/system_tools/send.py", "SendTool.asend", "getattr"): (
        1,
        f"{_RELEASED}: a comm without asend is sent to on a worker thread",
    ),
    (f"{STD}/plugins/agentcomm/local_group.py", "LocalGroupAgentComm._areceive_request", "getattr"): (
        1,
        f"{_RELEASED}: the session turn may carry asend",
    ),
    (f"{IOA}/a2a/agent_comm.py", "A2AAgentComm.send", "getattr"): (
        1,
        f"{_RELEASED}: reads the optional SendContext intent",
    ),
}

_NAMES = {"getattr", "hasattr", "vars", "attrgetter", "runtime_checkable"}
_PROXIES = {"__getattr__", "__getattribute__"}


def _scanned() -> list[Path]:
    files = [path for name in SCANNED_DIRS for path in sorted((ROOT / name).glob("*.py"))]
    return files + [ROOT / name for name in SCANNED_FILES]


class _Probes(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: Counter[tuple[str, str]] = Counter()
        self._scope: list[str] = []

    def _add(self, kind: str) -> None:
        self.found[(".".join(self._scope), kind)] += 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name in _PROXIES:
            self._add(node.name)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _NAMES:
            self._add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _NAMES or node.attr == "__dict__":
            self._add(node.attr)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in _NAMES:
                self._add(f"import {alias.name}")

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "callable" and node.args:
            if isinstance(node.args[0], ast.Attribute):
                self._add("callable(attr)")
        self.generic_visit(node)


def probes_in(source: str) -> Counter[tuple[str, str]]:
    visitor = _Probes()
    visitor.visit(ast.parse(source))
    return visitor.found


def test_the_scanned_files_probe_only_where_the_table_says() -> None:
    found: Counter[tuple[str, str, str]] = Counter()
    for path in _scanned():
        relative = path.relative_to(ROOT).as_posix()
        for (scope, kind), count in probes_in(path.read_text(encoding="utf-8")).items():
            found[(relative, scope, kind)] += count
    assert dict(found) == {key: count for key, (count, _reason) in ALLOWED.items()}


def test_every_scanned_file_exists() -> None:
    assert [name for name in SCANNED_FILES if not (ROOT / name).is_file()] == []
    assert all((ROOT / name).is_dir() and any((ROOT / name).glob("*.py")) for name in SCANNED_DIRS)


def test_the_scan_sees_each_way_of_probing() -> None:
    source = """
import builtins
from operator import attrgetter
import operator
from typing import runtime_checkable

def f(x):
    getattr(x, "a", None)
    hasattr(x, "a")
    g = getattr
    builtins.getattr(x, "a")
    operator.attrgetter("a")
    vars(x)
    x.__dict__
    callable(x.a)

class P:
    def __getattr__(self, name):
        return 1
"""
    assert probes_in(source) == Counter(
        {
            ("f", "getattr"): 3,
            ("f", "hasattr"): 1,
            ("f", "attrgetter"): 1,
            ("f", "vars"): 1,
            ("f", "__dict__"): 1,
            ("f", "callable(attr)"): 1,
            ("", "import attrgetter"): 1,
            ("", "import runtime_checkable"): 1,
            ("P", "__getattr__"): 1,
        }
    )
    assert probes_in("def f(x):\n    return x.a\n") == Counter()
