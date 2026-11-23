#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parametric smoke tests for tutorial scenario YAML commands.

Design: each scenario YAML is the test spec — COMMAND steps with
``expected_exit_code`` and optional ``expected_output`` drive the assertions.
Add a new scenario YAML entry to ``_SCENARIOS`` to include it automatically.

Mode filtering
--------------
Steps declare which execution modes they support via ``allowed_modes``:
- ``offline``  — no network or LLM required
- ``online``   — requires a live LLM / network
- ``cached``   — can use cached LLM responses

By default (CI / offline) only steps that list ``offline`` (or have no
``allowed_modes`` key at all) are executed.  Set the env var
``TUTORIAL_ONLINE=1`` to also run ``online``-only steps.

Online steps use ``gpt-4o-mini`` via ``MAS_CTL_MODEL`` and expect ``Paris`` in the
agent reply.  Infra is resolved from ``config.yaml``; override without
editing YAML via ``MAS_INFRA_REFS`` (e.g. ``MAS_INFRA_REFS=standard:llm-proxy``
for the Outshift OpenAI-compatible proxy).  The model key comes from the secret
store: ``mas-ctl secrets set openai`` (see Tutorial 0); no key is read from the
environment or ``.env``.

Run from the ioc-project/mas-lab repo root:

    pytest tests/tutorials/test_scenario_commands.py -v
    pytest tests/tutorials/test_scenario_commands.py -v -k norm-verify
    pytest tests/tutorials/test_scenario_commands.py -v -k tuto-03
    TUTORIAL_ONLINE=1 pytest tests/tutorials/test_scenario_commands.py -v -k tuto-01
"""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from mas.ctl.testing.scenario_commands import rewrite_command_for_v2 as _rewrite_command_for_v2

# Set TUTORIAL_ONLINE=1 to include steps that require a live LLM / network.
_ONLINE_MODE: bool = os.environ.get("TUTORIAL_ONLINE", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# Ensure `mas-lab` / `mas-ctl` resolve to this checkout's venv when present.
_REPO_VENV_BIN = REPO_ROOT / ".venv" / "bin"
_VENV_BIN = _REPO_VENV_BIN if (_REPO_VENV_BIN / "mas-ctl").exists() else Path(sys.executable).parent
_TEST_ENV: dict[str, str] | None = None


def _merge_dotenv(env: dict[str, str], path: Path) -> None:
    """Load the ``MAS_*`` settings of a gitignored .env without overriding existing env.

    Only the configuration namespace: a key in the file (``MAS_SECRET_*`` included) is never copied.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key.startswith("MAS_") or key.startswith("MAS_SECRET_") or key in env:
            continue
        env[key] = value.strip().strip('"').strip("'")


def _test_env() -> dict[str, str]:
    """Build an env dict that prepends the active venv bin to PATH (lazy, cached)."""
    global _TEST_ENV
    if _TEST_ENV is None:
        env = os.environ.copy()
        env["PATH"] = f"{_VENV_BIN}:{env.get('PATH', '')}"
        for dotenv_path in (REPO_ROOT / ".env", REPO_ROOT / "docker" / ".env"):
            _merge_dotenv(env, dotenv_path)
        if env.get("MAS_LLM_MODEL", "").strip() and not env.get("MAS_CTL_MODEL", "").strip():
            env["MAS_CTL_MODEL"] = env["MAS_LLM_MODEL"].strip()
        if _ONLINE_MODE and not env.get("MAS_CTL_MODEL", "").strip():
            env["MAS_CTL_MODEL"] = "gpt-4o-mini"
        _TEST_ENV = env
    return _TEST_ENV


_DOCKER_ENV_KEYS = (
    "MAS_CTL_MODEL",
    "MAS_LLM_MODEL",
    "MAS_INFRA_REFS",
    "SSL_CERT_FILE",
    "MAS_LLM_VERIFY_SSL",
)


def _inject_docker_env(command: str, env: dict[str, str]) -> str:
    """Pass online-test settings into ``docker compose run``; keys come from the mounted secret store."""
    stripped = command.strip()
    if not stripped.startswith("docker compose"):
        return command
    flags: list[str] = []
    for key in _DOCKER_ENV_KEYS:
        value = env.get(key, "")
        if value:
            flags.append(f"-e {shlex.quote(key)}={shlex.quote(value)}")
    if not flags:
        return command
    needle = "docker compose -f docker/compose.yaml run"
    alt_needle = "docker compose --profile tools -f docker/compose.yaml run"
    for n in (alt_needle, needle):
        if n in command:
            return command.replace(n, f"{n} {' '.join(flags)}", 1)
    return command


# Each entry: (scenario_yaml_path, working_dir, scenario_id)
_SCENARIOS = [
    (
        REPO_ROOT / "docs/tutorials/00-environment-setup/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-00",
    ),
    (
        REPO_ROOT / "docs/tutorials/01-building-an-agent/demo/scenario.yaml",
        REPO_ROOT,  # working_dir is relative to repo root (as declared in YAML)
        "tuto-01",
    ),
    (
        REPO_ROOT / "docs/tutorials/02-creating-a-mas/demo/scenario.yaml",
        REPO_ROOT,  # working_dir is relative to repo root (as declared in YAML)
        "tuto-02",
    ),
    (
        REPO_ROOT / "docs/tutorials/03-experiments-and-analysis/demo/scenario.yaml",
        REPO_ROOT,  # working_dir is relative to repo root (as declared in YAML)
        "tuto-03",
    ),
    (
        REPO_ROOT / "docs/tutorials/04-mcp-tools/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-04",
    ),
    (
        REPO_ROOT / "docs/tutorials/05-a2a-agents/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-05",
    ),
    (
        REPO_ROOT / "docs/tutorials/06-agent-skills/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-06",
    ),
    (
        REPO_ROOT / "docs/tutorials/07-slim-link/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-07",
    ),
    (
        REPO_ROOT / "docs/tutorials/11-sessions-and-recovery/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-11",
    ),
    (
        REPO_ROOT / "docs/tutorials/12-subagents/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-12",
    ),
    (
        REPO_ROOT / "docs/tutorials/13-control-and-debug/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-13",
    ),
    (
        REPO_ROOT / "docs/tutorials/14-governance-hitl/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-14",
    ),
    (
        REPO_ROOT / "docs/tutorials/15-world-events-and-fixtures/demo/scenario.yaml",
        REPO_ROOT,
        "tuto-15",
    ),
]

if (REPO_ROOT / "library-telemetry/examples/qa-agent").is_dir():
    _SCENARIOS.append(
        (
            REPO_ROOT / "docs/tutorials/08-telemetry/demo/scenario.yaml",
            REPO_ROOT,
            "tuto-08",
        )
    )
if (REPO_ROOT / "library-kg/examples/qa-agent").is_dir():
    _SCENARIOS.append(
        (
            REPO_ROOT / "docs/tutorials/09-kg-scope/demo/scenario.yaml",
            REPO_ROOT,
            "tuto-09",
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CommandCase(NamedTuple):
    scenario_id: str
    section_title: str
    step_title: str
    command: str
    working_dir: Path
    expected_exit_code: int
    expected_output: str  # substring; empty = not checked
    requires: tuple[str, ...] = ()  # Python modules that must be importable
    allowed_modes: tuple[str, ...] = ()  # empty = no restriction
    timeout_s: int = 60


def _step_runnable(step: dict) -> bool:
    """Return True if this step should run in the current mode.

    Rules:
    - No ``allowed_modes`` key → always run (legacy / unrestricted step).
    - Contains ``offline`` → always run.
    - Contains only ``online`` (and/or ``cached``) → skip unless TUTORIAL_ONLINE=1.
    """
    modes = step.get("allowed_modes")
    if not modes:
        return True
    if "offline" in modes:
        return True
    return _ONLINE_MODE


def _collect_command_cases() -> list[_CommandCase]:
    """Load all scenario YAMLs and collect every COMMAND step."""
    cases: list[_CommandCase] = []
    for yaml_path, base_dir, scenario_id in _SCENARIOS:
        if not yaml_path.exists():
            # Scenario file missing — will be reported as a collection warning
            continue
        with open(yaml_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        # Resolve working_dir declared inside the YAML
        working_dir_str = raw.get("working_dir", ".")
        working_dir = (base_dir / working_dir_str).resolve()

        for section in raw.get("sections", []):
            sec_title = section.get("title", section.get("id", "?"))
            for step in section.get("steps", []):
                if step.get("type") != "command":
                    continue
                if not _step_runnable(step):
                    continue
                cases.append(
                    _CommandCase(
                        scenario_id=scenario_id,
                        section_title=sec_title,
                        step_title=step.get("title", step.get("command", "?")[:40]),
                        command=step["command"],
                        working_dir=working_dir,
                        expected_exit_code=step.get("expected_exit_code", 0),
                        expected_output=step.get("expected_output", ""),
                        requires=tuple(step.get("requires", [])),
                        allowed_modes=tuple(step.get("allowed_modes", [])),
                        timeout_s=int(step.get("timeout_s", 60)),
                    )
                )
    return cases


def is_docker_command(command: str) -> bool:
    """True for steps that run ``docker ...`` (functional suite, needs an image build)."""
    return " ".join(command.split()).startswith("docker ")


_ALL_CASES = _collect_command_cases()
# Docker steps need a daemon and a built image: they run in tests/functional.
_CASES = [c for c in _ALL_CASES if not is_docker_command(c.command)]
DOCKER_CASES = [c for c in _ALL_CASES if is_docker_command(c.command)]


def scenario_case_id(case: _CommandCase) -> str:
    # e.g. "norm-verify/Inspecting Traces/Telemetry summary"
    return f"{case.scenario_id}/{case.section_title}/{case.step_title}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def run_scenario_case(case: _CommandCase, cwd: Path | None = None) -> None:
    """Run a COMMAND step from a scenario YAML and check exit code + output.

    ``cwd`` overrides the step's working directory (the tests pass a throwaway copy).
    """
    for mod in case.requires:
        assert importlib.util.find_spec(mod) is not None, f"scenario step requires {mod}"

    # Normalise multi-line folded YAML command (> operator produces single line)
    command = " ".join(case.command.split())
    command = _rewrite_command_for_v2(command)
    command = _inject_docker_env(command, _test_env())

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(cwd or case.working_dir),
        env=_test_env(),
        timeout=case.timeout_s,
    )
    combined = result.stdout + result.stderr

    assert result.returncode == case.expected_exit_code, (
        f"\nCommand: {command}"
        f"\nWorking dir: {case.working_dir}"
        f"\nExit code: {result.returncode} (expected {case.expected_exit_code})"
        f"\nOutput:\n{combined}"
    )

    if case.expected_output:
        assert case.expected_output in combined, (
            f"\nCommand: {command}\nExpected substring in output: {case.expected_output!r}\nActual output:\n{combined}"
        )


_SCRATCH_DIRS: dict[Path, Path] = {}


def _scratch_working_dir(working_dir: Path, tmp_path_factory) -> Path:
    """The directory a step runs in: a per-scenario temporary copy of a tutorial directory.

    Steps write traces and plots beside the manifests, so a tutorial directory runs on a copy that
    its later steps share (a plot step reads the trace a chat step wrote). The repository root is
    used as is.
    """
    if working_dir == REPO_ROOT:
        return working_dir
    if working_dir not in _SCRATCH_DIRS:
        dest = tmp_path_factory.mktemp("scenario") / working_dir.name
        shutil.copytree(working_dir, dest)
        _SCRATCH_DIRS[working_dir] = dest
    return _SCRATCH_DIRS[working_dir]


@pytest.mark.parametrize("case", _CASES, ids=[scenario_case_id(c) for c in _CASES])
def test_scenario_command(case: _CommandCase, tmp_path_factory):
    run_scenario_case(case, cwd=_scratch_working_dir(case.working_dir, tmp_path_factory))


# ---------------------------------------------------------------------------
# Scenario file presence guard (separate from parametrized tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("yaml_path,_base,scenario_id", _SCENARIOS, ids=[s[2] for s in _SCENARIOS])
def test_scenario_yaml_exists(yaml_path: Path, _base: Path, scenario_id: str):
    """Each registered scenario YAML must exist."""
    assert yaml_path.exists(), f"Scenario YAML for '{scenario_id}' not found: {yaml_path}"


@pytest.mark.parametrize("yaml_path,_base,scenario_id", _SCENARIOS, ids=[s[2] for s in _SCENARIOS])
def test_scenario_yaml_parses(yaml_path: Path, _base: Path, scenario_id: str):
    """Each registered scenario YAML must parse cleanly."""
    with open(yaml_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert isinstance(raw, dict), f"Top-level must be a dict, got {type(raw)}"
    assert "sections" in raw, "YAML must have a 'sections' key"
    assert raw.get("name"), "YAML must have a 'name' field"


@pytest.mark.parametrize("yaml_path,_base,scenario_id", _SCENARIOS, ids=[s[2] for s in _SCENARIOS])
def test_scenario_has_commands(yaml_path: Path, _base: Path, scenario_id: str):
    """Each registered scenario YAML must have at least one COMMAND step."""
    matching = [c for c in _ALL_CASES if c.scenario_id == scenario_id]
    assert len(matching) > 0, f"No COMMAND steps found in {yaml_path.name}"


def _scenarios_using_fixture() -> list[tuple[Path, Path, str, Path]]:
    """Return (yaml_path, base_dir, scenario_id, fixture_path) for scenarios that need fixtures."""
    cases: list[tuple[Path, Path, str, Path]] = []
    for yaml_path, base_dir, scenario_id in _SCENARIOS:
        if not yaml_path.exists():
            continue
        with open(yaml_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        working_dir = (base_dir / raw.get("working_dir", ".")).resolve()
        fixture = working_dir / "fixtures" / "events.jsonl"
        uses_fixture = any(
            "fixtures/events.jsonl" in step.get("command", "")
            for section in raw.get("sections", [])
            for step in section.get("steps", [])
            if step.get("type") == "command"
        )
        if uses_fixture:
            cases.append((yaml_path, base_dir, scenario_id, fixture))
    return cases


_FIXTURE_SCENARIOS = _scenarios_using_fixture()


@pytest.mark.parametrize(
    "yaml_path,_base,scenario_id,fixture",
    _FIXTURE_SCENARIOS,
    ids=[s[2] for s in _FIXTURE_SCENARIOS],
)
def test_scenario_fixture_exists(
    yaml_path: Path,
    _base: Path,
    scenario_id: str,
    fixture: Path,
):
    """Scenarios that reference fixtures/events.jsonl must ship that file."""
    assert fixture.exists(), f"Fixture events.jsonl not found for scenario '{scenario_id}': {fixture}"
