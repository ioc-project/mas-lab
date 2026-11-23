#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Architecture guard over every ``src`` tree: the host environment enters only through ctl and the entry
points, no spawn inherits it, no YAML file skips the secret check, no client sends a hand-built credential.

1. no ``os.environ`` / ``getenv`` / ``putenv``, ``from os import environ``, ``getattr(os, "environ")``
   or dotenv outside ctl and :data:`ENTRY_POINTS`;
2. every ``subprocess`` / ``asyncio`` / ``os`` spawn passes an env built by ``child_env`` (a call, or a
   name the same function assigns only from one), unless listed in :data:`SPAWNS`;
3. a bare ``yaml`` load only in :data:`YAML_READERS` (names, schemas, metadata, datasets): a document that
   may hold a secret goes through ``load_checked_document`` or ``safe_yaml_load``;
4. an HTTP client call with ``headers=`` / ``auth=``, or an ``Authorization`` / ``x-api-key`` literal, only
   in :data:`HTTP_SENDERS`; a secret header is revealed by the ``mas.runtime.secrets`` transport helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ENTRY_POINTS = {
    "runtime/src/mas/runtime/entrypoint.py": "what every console entry point calls first",
    "runtime/src/mas/runtime/hostenv_pytest.py": "the test harness's host is os.environ",
    "runtime/src/mas/third_party_otel.py": "pins third-party SDK flags, which they read only from os.environ",
}
_LAB = "lab/components/bench/src/mas/lab/"
_CTRL = "lab/components/controller/src/mas/lab/controller/"
_STEPS = "library-lab/src/mas/library/lab/steps/"
# A path ending in ``/`` lists a whole directory.
SPAWNS = {
    "ctl/src/mas/ctl/testing/": "test support outside the wheel: runs a command in the developer's own shell",
    f"{_LAB}benchmark/environment/fixtures.py": "_fixture_env filters the CLI's child_env(lab=True) base",
    f"{_LAB}benchmark/service_manager.py": "_compose_env: child_env plus the service's own declared env",
    f"{_CTRL}jobs.py": "run_job's env is child_env(..., lab=True), built by submit",
    f"{_STEPS}environment/shell_action.py": "the CHILD_KEYS of the CLI's child_env base",
    "library-skills/src/sandbox/runner.py": "the sandbox process's own _SAFE_ENV_KEYS allowlist",
    "library-standard/src/mas/library/standard/plugins/loop/stdio_harness.py": "only the loop's configured env",
}
YAML_READERS = dict.fromkeys(
    [
        "ctl/src/mas/ctl/overrides/parser.py",
        "ctl/src/mas/ctl/validate/providers.py",
        "ctl/src/mas/ctl/overlay/merge.py",
        "ctl/src/mas/ctl/libraries/bundles.py",
        "ctl/src/mas/ctl/registry/catalog.py",
        "ctl/src/mas/ctl/session/flavour.py",
        "ctl/src/mas/ctl/testing/",
        "ctl/src/mas/ctl/adapters/memory_seed.py",
        f"{_LAB}benchmark/pipeline/schema_validation.py",
        f"{_LAB}benchmark/pipeline/models.py",
        f"{_LAB}benchmark/pipeline/lib/plot_lib.py",
        f"{_LAB}benchmark/metadata.py",
        f"{_LAB}benchmark/cli/export_import.py",
        f"{_LAB}benchmark/cli/declared.py",
        f"{_LAB}benchmark/environment/attach.py",
        f"{_LAB}benchmark/runner.py",
        f"{_LAB}manifests/validator.py",
        f"{_LAB}plots/topology.py",
        f"{_LAB}labs.py",
        f"{_LAB}verification/contracts/loader.py",
        f"{_CTRL}routes/health.py",
        f"{_CTRL}routes/libraries.py",
        f"{_CTRL}routes/benchmark.py",
        f"{_CTRL}ui_stream/",
        "lab/components/core/src/mas/lab/paths.py",
        "lab/components/core/src/mas/lab/processor.py",
        "lab/components/core/src/mas/lab/cli/commands/config.py",
        "lab/components/core/src/mas/lab/cli/commands/init.py",
        "lab/components/core/src/mas/lab/contracts/loader.py",
        f"{_STEPS}eval/collect_metrics.py",
        f"{_STEPS}data/serialize.py",
        f"{_STEPS}data/generate_dataset.py",
        f"{_STEPS}viz/plot_multilevel_trajectory.py",
        "library-kg/src/mas/library/kg/observability/native/validate.py",
        "library-telemetry/src/mas/library/telemetry/verification/spanspec.py",
        "library-skills/src/agentskills/parsing.py",
        "library-skills/src/mas/library/skills/lib/frontmatter.py",
        "runtime/src/mas/library_roots.py",
        "runtime/src/mas/runtime/engine/llm_model_catalog.py",
        "runtime/src/mas/runtime/engine/tools.py",
        "runtime/src/mas/runtime/registry/",
        "runtime/src/mas/runtime/spec/yaml_errors.py",
    ],
    "reads names, schemas, catalogs, metadata, library paths, datasets or artefacts: never a credential",
) | {"runtime/src/mas/runtime/engine/textual_tool_calls.py": "parses model output, not a file"}
HTTP_SENDERS = {
    f"{_LAB}benchmark/pipeline/ui_bridge.py": "the demo UI's own ingest token, to its configured ingest URL",
    f"{_LAB}benchmark/pipeline/ws.py": "the demo UI's own stream token",
    f"{_CTRL}ui_stream/auth.py": "the demo UI's own ingest token",
    f"{_CTRL}ui_stream/replay_cli.py": "ingest_headers() of ui_stream/auth.py",
    f"{_LAB}benchmark/environment/fixtures.py": "loopback fixture clock push, Content-Type only",
    "lab/components/core/src/mas/lab/connections.py": "the local MAS Lab API, Content-Type only",
    "lab/components/core/src/mas/lab/tools/experiment_designer_tools.py": "a localhost API, Content-Type only",
    f"{_STEPS}environment/http_call.py": "a loopback or allow-listed URL, Content-Type and declared headers",
    "library-telemetry/src/mas/library/telemetry/collector/otlp.py": "local collector export, Content-Type only",
    "library-ioa/src/library_ioa/plugins/a2a/exposure.py": "an A2A push: the credential its subscriber supplied",
}


def _listed(rel: str, table: dict[str, str]) -> bool:
    return any(rel == key or (key.endswith("/") and rel.startswith(key)) for key in table)


_ENV_NAMES = {"environ", "getenv", "putenv", "environb", "getenvb", "unsetenv"}
_SPAWNS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "os": {"system", "popen", "posix_spawn", "posix_spawnp", "execve", "execvpe", "spawnve", "spawnvpe"},
}
_YAML_LOADS = {"safe_load", "load", "load_all", "full_load", "unsafe_load", "safe_load_all"}
_HTTP = {
    "httpx": {"Client", "AsyncClient"},
    "requests": {"Session", "get", "post", "put", "request"},
    "urllib.request": {"urlopen", "Request"},
}
_AUTH_LITERALS = {"authorization", "x-api-key"}


def _qualname(node: ast.AST, aliases: dict[str, str]) -> str:
    """``module.attr`` of a call target, through ``import x as y`` and ``from x import a``."""
    if isinstance(node, ast.Attribute):
        return f"{_qualname(node.value, aliases)}.{node.attr}"
    return aliases.get(node.id, node.id) if isinstance(node, ast.Name) else ""


def _aliases(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update({a.asname or a.name: a.name for a in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.update({a.asname or a.name: f"{node.module}.{a.name}" for a in node.names})
    return out


def _is_child_env(node: ast.AST | None, assigned: dict[str, list[ast.AST]]) -> bool:
    if isinstance(node, ast.IfExp):
        return _is_child_env(node.body, assigned) and _is_child_env(node.orelse, assigned)
    if isinstance(node, ast.Call):
        return _qualname(node.func, {}).split(".")[-1] == "child_env"
    if isinstance(node, ast.Name) and assigned.get(node.id):
        return all(_is_child_env(value, {}) for value in assigned[node.id])
    return False


def findings(tree: ast.Module, rel: str = "probe.py") -> list[str]:
    aliases, hits = _aliases(tree), []
    outside_ctl = not rel.startswith("ctl/") and rel not in ENTRY_POINTS
    scopes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    owner = {id(n): s for s in scopes for n in ast.walk(s)}  # the innermost function wins
    for node in ast.walk(tree):
        line = f"{rel}:{getattr(node, 'lineno', 0)}"
        if outside_ctl and (
            (isinstance(node, ast.Attribute) and node.attr in _ENV_NAMES)
            or (isinstance(node, ast.ImportFrom) and node.module == "os" and {a.name for a in node.names} & _ENV_NAMES)
            or (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dotenv"))
            or (isinstance(node, ast.Import) and any(a.name.startswith("dotenv") for a in node.names))
        ):
            hits.append(f"{line} reads the host environment")
        if not isinstance(node, ast.Call):
            continue
        name = _qualname(node.func, aliases)
        module, _, attr = name.rpartition(".")
        if outside_ctl and name == "getattr" and any(getattr(a, "value", None) in _ENV_NAMES for a in node.args):
            hits.append(f"{line} reads the host environment")
        if attr in _SPAWNS.get(module, ()) and not _listed(rel, SPAWNS):
            scope = owner[id(node)]
            assigned: dict[str, list[ast.AST]] = {}
            for sub in ast.walk(scope):
                if isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        assigned.setdefault(getattr(target, "id", ""), []).append(sub.value)
            values = [k.value for k in node.keywords if k.arg == "env"] + node.args[1:]
            if not any(_is_child_env(value, assigned) for value in values):
                hits.append(f"{line} {name} without env=child_env(...)")
        if module == "yaml" and attr in _YAML_LOADS and not _listed(rel, YAML_READERS):
            hits.append(f"{line} bare {name}: use load_checked_document or safe_yaml_load")
        if attr in _HTTP.get(module, ()) and {"headers", "auth"} & {k.arg for k in node.keywords}:
            if not _listed(rel, HTTP_SENDERS):
                hits.append(f"{line} {name} with headers or auth outside the secret transport")
    # A header set by hand: a dict key or an item assignment, not a read of an inbound request's header.
    keys = [k for n in ast.walk(tree) if isinstance(n, ast.Dict) for k in n.keys]
    keys += [n.slice for n in ast.walk(tree) if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)]
    for key in keys if not _listed(rel, HTTP_SENDERS) else ():
        if isinstance(key, ast.Constant) and str(key.value).lower() in _AUTH_LITERALS:
            hits.append(f"{rel}:{key.lineno} hand-built credential header")
    return hits


def _sources() -> list[Path]:
    trees = [ROOT / "runtime/src", ROOT / "ctl/src", *ROOT.glob("library-*/src"), *ROOT.glob("lab/components/*/src")]
    return sorted(p for tree in trees for p in tree.rglob("*.py") if "node_modules" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_source_tree_keeps_the_host_out() -> None:
    hits = [hit for path in _sources() for hit in findings(ast.parse(path.read_text()), _rel(path))]
    assert hits == [], "\n".join(hits)


def test_every_listed_file_exists() -> None:
    listed = {*ENTRY_POINTS, *SPAWNS, *YAML_READERS, *HTTP_SENDERS}
    assert sorted(rel for rel in listed if not (ROOT / rel).exists()) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.environ.get('X')",
        "from os import environ",
        "import os\ngetattr(os, 'environ')",
        "from dotenv import load_dotenv",
        "import subprocess\nsubprocess.run(['ls'])",
        "import asyncio\nasyncio.create_subprocess_exec('ls', env={'PATH': '/bin'})",
        "from mas.runtime.secrets import child_env\nimport subprocess\ndef f():\n    env = child_env()\n"
        "    env = None\n    subprocess.run(['ls'], env=env)",
        "import yaml\nyaml.safe_load(open('m.yaml'))",
        "import httpx\nhttpx.Client(headers={'X': 'y'})",
        "h = {'Authorization': 'Bearer x'}",
    ],
)
def test_the_guard_catches(source: str) -> None:
    assert findings(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "from mas.runtime.secrets import child_env\nimport subprocess\nsubprocess.run(['ls'], env=child_env())",
        "from mas.runtime.secrets import child_env\nimport asyncio\nasync def f():\n"
        "    env = child_env({'A': '1'}, lab=True)\n    await asyncio.create_subprocess_exec('ls', env=env)",
        "import os\nflags = getattr(os, 'O_NOFOLLOW', 0)\nimport httpx\nhttpx.Client(timeout=3)",
    ],
)
def test_the_guard_passes(source: str) -> None:
    assert findings(ast.parse(source)) == []
