#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Library and runtime modules that were moved off the environment stay off it.

Only the CLI entry points read environment variables, as last-resort overrides,
and hand the values over as explicit parameters. Add a module here once it is clean.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Entry points and test support: they are where the host environment enters, or
# configure a third-party SDK that reads its own flags from the process environment.
_ALLOWED = {
    "lab/components/core/src/mas/lab/cli/__init__.py",
    "lab/components/controller/src/mas/lab/controller/daemon.py",
    # ctl is the CLI package: these read the environment on behalf of its entry points
    "ctl/src/mas/ctl/env.py",
    "ctl/src/mas/ctl/session/llm_retry_env.py",
    "ctl/src/mas/ctl/session/llm_scheduler_env.py",
    "ctl/src/mas/ctl/session/transport_obs.py",
    "ctl/src/mas/ctl/testing/cli_helpers.py",
    "ctl/src/mas/ctl/testing/scenario_commands.py",
    "runtime/src/mas/third_party_otel.py",
    "runtime/src/mas/runtime/hostenv_pytest.py",
    "library-telemetry/src/mas/library/telemetry/__init__.py",
    "library-skills/src/sandbox/runner.py",  # runs inside the skill sandbox process
    "library-ioa/compliance/mcp/fixtures/mcp_conformance_server.py",  # standalone fixture server
}
_PACKAGES = [
    "runtime/src",
    "ctl/src",
    "lab/components",
    "library-eval",
    "library-ioa",
    "library-kg",
    "library-lab",
    "library-samples",
    "library-skills",
    "library-standard",
    "library-telemetry",
]


def _sources() -> list[str]:
    out = []
    for pkg in _PACKAGES:
        for path in sorted((_ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(_ROOT).as_posix()
            if "/tests/" in rel or "/test_" in rel or rel in _ALLOWED or "/node_modules/" in rel:
                continue
            out.append(rel)
    return out


_CLEAN = _sources()


_NAMES = {"environ", "getenv", "putenv", "unsetenv", "environb", "getenvb"}


def _environment_uses(path: Path) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and node.attr in _NAMES:
            hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and any(a.name in _NAMES for a in node.names):
            hits.append(node.lineno)  # from os import environ
    return hits


@pytest.mark.parametrize("rel", _CLEAN)
def test_module_does_not_touch_the_environment(rel: str) -> None:
    assert _environment_uses(_ROOT / rel) == []
