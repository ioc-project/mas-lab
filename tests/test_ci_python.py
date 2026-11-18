#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``.github/python-support.json`` is the one list of supported Pythons; everything else must agree with it."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ci_python():
    return _load("ci_python")


@pytest.fixture(scope="module")
def support(ci_python):
    return ci_python.load_support()


def _pyprojects() -> list[Path]:
    root = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    members = root["tool"]["uv"]["workspace"]["members"]
    return [REPO_ROOT / "pyproject.toml", *(REPO_ROOT / m / "pyproject.toml" for m in members)]


def test_the_default_is_a_supported_version(support) -> None:
    assert support["default"] in support["supported"]


def test_supported_versions_are_sorted_numerically() -> None:
    spec = importlib.util.spec_from_file_location("p", REPO_ROOT / "scripts" / "ci_python.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module._key("3.9") < module._key("3.11") < module._key("3.14t")


def test_load_support_rejects_a_default_that_is_not_supported(ci_python, tmp_path) -> None:
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"default": "3.9", "supported": ["3.11"], "free_threaded": {"version": "3.11t"}}))
    with pytest.raises(ValueError, match="default"):
        ci_python.load_support(bad)


def test_load_support_rejects_a_free_threaded_version_that_is_not_supported(ci_python, tmp_path) -> None:
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"default": "3.11", "supported": ["3.11"], "free_threaded": {"version": "3.14t"}}))
    with pytest.raises(ValueError, match="free-threaded"):
        ci_python.load_support(bad)


def test_every_package_requires_the_oldest_supported_python(support) -> None:
    oldest = support["supported"][0]
    for path in _pyprojects():
        requires = tomllib.loads(path.read_text())["project"].get("requires-python")
        assert requires == f">={oldest}", f"{path.relative_to(REPO_ROOT)}: {requires}"


def test_the_matrix_runs_every_fast_job_on_every_version_and_the_slow_ones_on_the_edges(ci_python, support) -> None:
    matrices = ci_python.build_matrices(support)
    versions = support["supported"]
    unit_rows = len(_load("ci_paths").UNIT_ROWS)
    library_rows = len(_load("ci_paths").LIBRARY_ROWS)
    suite_rows = len(_load("ci_paths").SUITE_ROWS)
    assert len(matrices["unit_matrix"]["include"]) == len(versions) * unit_rows
    assert len(matrices["library_matrix"]["include"]) == len(versions) * library_rows
    assert {row["python"] for row in matrices["suite_matrix"]["include"]} == {versions[0], versions[-1]}
    assert len(matrices["suite_matrix"]["include"]) == 2 * suite_rows
    assert matrices["oldest"] == versions[0]
    assert matrices["newest"] == versions[-1]


def test_every_matrix_row_names_a_task_that_exists(ci_python, support) -> None:
    taskfile = (REPO_ROOT / "Taskfile.yml").read_text()
    matrices = ci_python.build_matrices(support)
    tasks = {r["task"] for key in ("unit_matrix", "suite_matrix") for r in matrices[key]["include"]}
    for task in tasks | {"test-library", "install-free-threaded", "verify-free-threaded"}:
        assert re.search(rf"^  {re.escape(task)}:$", taskfile, re.M), task


def test_the_free_threaded_components_exist_and_have_tests(support) -> None:
    for component in support["free_threaded"]["components"]:
        assert (REPO_ROOT / component / "pyproject.toml").is_file(), component
        assert (REPO_ROOT / component / "tests").is_dir(), component


def test_the_cli_prints_the_install_arguments_and_test_dirs(ci_python, support, capsys) -> None:
    assert ci_python.main(["components"]) == 0
    assert capsys.readouterr().out.split() == [
        part for c in support["free_threaded"]["components"] for part in ("-e", c)
    ]
    assert ci_python.main(["test-dirs"]) == 0
    assert capsys.readouterr().out.split() == [f"{c}/tests" for c in support["free_threaded"]["components"]]
    assert ci_python.main(["nonsense"]) == 2
    capsys.readouterr()
    assert ci_python.main([]) == 0
    assert "unit_matrix=" in capsys.readouterr().out


def test_the_oldest_version_job_in_test_yml_uses_the_oldest_supported_version(support) -> None:
    workflow = (REPO_ROOT / ".github/workflows/test.yml").read_text()
    block = workflow.split("  unit-311:", 1)[1].split("\n  suite:", 1)[0]
    assert f'python-version: "{support["supported"][0]}"' in block


def test_the_default_version_is_set_in_one_place(support) -> None:
    """Only setup-mas names the default; test.yml pins nothing but the oldest version."""
    action = (REPO_ROOT / ".github/actions/setup-mas/action.yml").read_text()
    assert f'default: "{support["default"]}"' in action
    workflow = (REPO_ROOT / ".github/workflows/test.yml").read_text()
    assert set(re.findall(r'python-version: "(\d+\.\d+)"', workflow)) == {support["supported"][0]}


def test_the_daily_run_is_gone_and_the_weekly_one_runs_the_protocol_conformance_suites() -> None:
    assert not (REPO_ROOT / ".github/workflows/nightly.yml").exists()
    weekly = (REPO_ROOT / ".github/workflows/weekly.yml").read_text()
    assert 'cron: "11 5 * * 1"' in weekly
    assert "uses: ./.github/workflows/compliance.yml" in weekly


def test_the_readme_lists_the_supported_versions(support) -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    assert f"| {', '.join(support['supported'])} | full |" in readme
    assert f"| {support['free_threaded']['version']} (free-threaded) | partial |" in readme


def test_the_docs_page_lists_the_versions_and_every_free_threaded_component(support) -> None:
    page = (REPO_ROOT / "docs/python-support.md").read_text()
    for version in (*support["supported"], support["free_threaded"]["version"]):
        assert version in page
    for component in support["free_threaded"]["components"]:
        assert f"`{component}`" in page, component


def test_the_docs_page_is_in_the_navigation() -> None:
    assert "python-support.md" in (REPO_ROOT / "mkdocs.yml").read_text()


def test_setup_checks_the_interpreter_it_was_asked_for() -> None:
    action = (REPO_ROOT / ".github/actions/setup-mas/action.yml").read_text()
    assert "UV_PYTHON=$WANTED" in action
    assert 'test "$got" = "$WANTED"' in action


def test_the_release_workflow_waits_for_the_python_matrix() -> None:
    publish = (REPO_ROOT / ".github/workflows/publish.yml").read_text()
    assert "uses: ./.github/workflows/python-matrix.yml" in publish
    assert "needs: [plan, python-matrix]" in publish


def test_every_uncovered_free_threaded_test_exists_and_has_a_reason(support) -> None:
    entries = support["free_threaded"]["not_covered"]
    assert entries
    for entry in entries:
        assert entry["reason"].strip(), entry
        path, _, name = entry["path"].partition("::")
        file = REPO_ROOT / path
        assert file.is_file(), path
        if name:
            assert f"def {name}(" in file.read_text(), entry["path"]


def test_pytest_args_ignore_files_and_deselect_test_ids_under_one_directory(ci_python, support) -> None:
    fake = {
        "free_threaded": {
            "not_covered": [
                {"path": "a/tests/test_x.py", "reason": "r"},
                {"path": "a/tests/test_y.py::test_one", "reason": "r"},
                {"path": "b/tests/test_z.py", "reason": "r"},
            ]
        }
    }
    assert ci_python.pytest_args(fake, "a") == [
        "--ignore=a/tests/test_x.py",
        "--deselect=a/tests/test_y.py::test_one",
        "--deselect=tests/test_y.py::test_one",
    ]
    assert ci_python.pytest_args(fake, "c") == []


def test_every_workflow_file_a_taskfile_names_exists() -> None:
    """A renamed or removed workflow must not leave a Taskfile or a check pointing at it."""
    taskfiles = [REPO_ROOT / "Taskfile.yml", *sorted((REPO_ROOT / "library-ioa/compliance").glob("*/Taskfile.yml"))]
    missing = []
    for taskfile in taskfiles:
        for name in re.findall(r"\.github/(?:workflows|actions)/[\w./-]+\.ya?ml", taskfile.read_text()):
            if not (REPO_ROOT / name).is_file():
                missing.append(f"{taskfile.relative_to(REPO_ROOT)}: {name}")
    assert not missing, missing
