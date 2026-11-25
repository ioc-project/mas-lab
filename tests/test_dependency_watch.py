#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``scripts/dependency_watch.py``: what counts as a finding, and how the issues follow the findings."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dependency_watch", REPO_ROOT / "scripts" / "dependency_watch.py")
assert _spec and _spec.loader
watch = importlib.util.module_from_spec(_spec)
sys.modules["dependency_watch"] = watch
_spec.loader.exec_module(watch)


def _repo(tmp_path: Path, locked: dict[str, str], deps: list[str], lib_deps: list[str]) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mas-lab"\nversion = "1"\ndependencies = ["mas-lab-lib"]\n'
        '[tool.uv.workspace]\nmembers = ["lib"]\n[dependency-groups]\ndev = ' + json.dumps(deps) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "pyproject.toml").write_text(
        '[project]\nname = "mas-lab-lib"\nversion = "1"\ndependencies = ' + json.dumps(lib_deps) + "\n",
        encoding="utf-8",
    )
    packages = "".join(f'[[package]]\nname = "{n}"\nversion = "{v}"\n' for n, v in locked.items())
    (tmp_path / "uv.lock").write_text(packages + '[[package]]\nname = "mas-lab-lib"\nsource = { editable = "lib" }\n')
    return tmp_path


def _found(tmp_path: Path, latest: dict[str, str | None], **repo: Any) -> dict[str, Any]:
    root = _repo(tmp_path, **repo)
    return {f.name: f for f in watch.findings(root, latest_of=latest.get)}


def test_a_newer_minor_or_major_is_a_finding_and_a_patch_is_not(tmp_path: Path) -> None:
    got = _found(
        tmp_path,
        {"minor": "1.3.0", "major": "3.0.0", "patch": "1.2.9", "same": "1.2.3", "older": "1.0.0", "none": None},
        locked={"minor": "1.2.3", "major": "2.9.0", "patch": "1.2.3", "same": "1.2.3", "older": "1.2.3", "none": "1.0"},
        deps=["minor", "major", "patch", "same", "older", "none"],
        lib_deps=[],
    )
    assert sorted(got) == ["major", "minor"]
    assert got["major"].major and not got["minor"].major
    assert got["minor"].status == "behind"


def test_a_release_outside_the_declared_range_is_blocked_and_names_the_file(tmp_path: Path) -> None:
    got = _found(
        tmp_path,
        {"slim": "4.0.0", "loose": "5.1.0"},
        locked={"slim": "3.3.9", "loose": "5.0.0"},
        deps=["loose>=5"],
        lib_deps=["slim>=3,<4"],
    )
    assert got["slim"].status == "blocked"
    assert got["slim"].ranges == ("lib/pyproject.toml: <4,>=3",)
    assert got["loose"].status == "behind"


def test_the_workspace_members_and_unlocked_names_are_not_looked_up(tmp_path: Path) -> None:
    asked: list[str] = []

    def latest(name: str) -> str | None:
        asked.append(name)
        return "9.0"

    root = _repo(tmp_path, locked={"known": "1.0"}, deps=["known", "unlocked"], lib_deps=[])
    watch.findings(root, latest_of=latest)
    assert asked == ["known"]


def test_the_repository_lock_and_pyprojects_parse() -> None:
    wanted, own = watch.declared(REPO_ROOT)
    assert "mas-lab-runtime" in own and "mas-lab-runtime" not in wanted
    assert set(wanted) & set(watch.locked(REPO_ROOT))


def test_pypi_latest_skips_prereleases_and_yanked_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "releases": {
            "1.0": [{"yanked": False}],
            "2.0": [{"yanked": False}],
            "3.0": [{"yanked": True}],
            "4.0rc1": [{"yanked": False}],
            "5.0": [],
            "junk": [{"yanked": False}],
        }
    }
    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *a, **k: io.StringIO(json.dumps(payload)))
    assert watch.pypi_latest("x") == "2.0"


class _Gh:
    """A ``gh`` that keeps issues in memory."""

    def __init__(self, open_issues: list[dict[str, Any]] = (), closed: list[dict[str, Any]] = ()) -> None:
        self.state = {"open": list(open_issues), "closed": list(closed)}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps(self.state[args[args.index("--state") + 1]])
        return ""


def _finding(latest: str = "2.3.0", name: str = "mcp") -> Any:
    return watch.Finding(name, "2.2.0", latest, "behind", ("library-ioa/pyproject.toml: >=2.2",))


def _issue(number: int, name: str, latest: str, *, body: str | None = None, label: bool = True) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Dependency: {name}",
        "body": f"text\n\n{watch.marker(name, latest)}" if body is None else body,
        "labels": [{"name": watch.LABEL}] if label else [],
    }


def test_a_new_finding_opens_one_labelled_issue() -> None:
    gh = _Gh()
    assert watch.sync([_finding()], run=gh) == ["opened mcp 2.3.0"]
    create = next(c for c in gh.calls if c[:2] == ("issue", "create"))
    assert create[create.index("--title") + 1] == "Dependency: mcp"
    assert create[create.index("--label") + 1] == watch.LABEL
    assert watch.marker("mcp", "2.3.0") in create[create.index("--body") + 1]


def test_the_same_release_changes_nothing() -> None:
    gh = _Gh(open_issues=[_issue(7, "mcp", "2.3.0")])
    assert watch.sync([_finding()], run=gh) == []
    assert [c for c in gh.calls if c[:2] != ("issue", "list")] == []


def test_a_newer_release_updates_the_open_issue_and_says_so() -> None:
    gh = _Gh(open_issues=[_issue(7, "mcp", "2.3.0")])
    assert watch.sync([_finding("2.4.0")], run=gh) == ["updated #7 mcp 2.3.0 -> 2.4.0"]
    assert any(c[:3] == ("issue", "edit", "7") for c in gh.calls)
    assert any(c[:3] == ("issue", "comment", "7") for c in gh.calls)


def test_an_issue_closed_by_hand_is_not_reopened_for_the_same_release_only() -> None:
    gh = _Gh(closed=[_issue(3, "mcp", "2.3.0")])
    assert watch.sync([_finding("2.3.0")], run=gh) == []
    assert watch.sync([_finding("2.4.0")], run=gh) == ["opened mcp 2.4.0"]


def test_an_issue_whose_dependency_caught_up_is_closed() -> None:
    gh = _Gh(open_issues=[_issue(7, "mcp", "2.3.0"), _issue(8, "click", "8.5.0")])
    assert watch.sync([_finding("2.3.0", "mcp")], run=gh) == ["closed #8 click"]
    assert any(c[:3] == ("issue", "close", "8") for c in gh.calls)


def test_the_body_flags_a_major_version_and_a_blocking_range() -> None:
    major = watch.Finding("openai", "2.44.0", "3.0.0", "blocked", ("a/pyproject.toml: <3",))
    text = watch.body(major)
    assert "new major version" in text and "outside a declared range" in text
    assert "`a/pyproject.toml: <3`" in text and text.endswith(watch.marker("openai", "3.0.0"))
    assert "major" not in watch.body(_finding())


def _writes(gh: _Gh) -> list[tuple[str, ...]]:
    return [c for c in gh.calls if c[:2] != ("issue", "list")]


def test_an_open_issue_whose_body_lost_its_marker_is_updated_not_duplicated() -> None:
    gh = _Gh(open_issues=[_issue(7, "mcp", "2.3.0", body="someone rewrote this")])
    assert watch.sync([_finding("2.3.0")], run=gh) == ["updated #7 mcp ? -> 2.3.0"]
    assert not any(c[:2] == ("issue", "create") for c in gh.calls)
    edit = next(c for c in gh.calls if c[:2] == ("issue", "edit"))
    assert watch.marker("mcp", "2.3.0") in edit[edit.index("--body") + 1]


def test_an_open_issue_without_our_label_is_found_by_its_title() -> None:
    gh = _Gh(open_issues=[_issue(7, "mcp", "2.2.9", label=False)])
    assert watch.sync([_finding("2.3.0")], run=gh) == ["updated #7 mcp 2.2.9 -> 2.3.0"]
    assert not any(c[:2] == ("issue", "create") for c in gh.calls)
    edit = next(c for c in gh.calls if c[:2] == ("issue", "edit"))
    assert edit[edit.index("--add-label") + 1] == watch.LABEL


def test_a_second_run_after_an_update_does_nothing() -> None:
    first = _Gh(open_issues=[_issue(7, "mcp", "2.3.0")])
    watch.sync([_finding("2.4.0")], run=first)
    edited = next(c for c in first.calls if c[:2] == ("issue", "edit"))
    again = _Gh(open_issues=[_issue(7, "mcp", "2.3.0", body=edited[edited.index("--body") + 1])])
    assert watch.sync([_finding("2.4.0")], run=again) == []
    assert _writes(again) == []


def test_of_several_open_issues_with_one_title_the_oldest_is_updated() -> None:
    gh = _Gh(open_issues=[_issue(9, "mcp", "2.3.0"), _issue(4, "mcp", "2.3.0"), _issue(6, "mcp", "2.3.0")])
    assert watch.sync([_finding("2.4.0")], run=gh) == ["updated #4 mcp 2.3.0 -> 2.4.0"]


def test_an_issue_that_is_not_ours_is_never_closed() -> None:
    mine = _issue(7, "click", "8.5.0")
    theirs = {"number": 8, "title": "Dependency: yaml", "body": "a person wrote this", "labels": []}
    other = {"number": 9, "title": "Crash on start", "body": "", "labels": []}
    gh = _Gh(open_issues=[mine, theirs, other])
    assert watch.sync([], run=gh) == ["closed #7 click"]
