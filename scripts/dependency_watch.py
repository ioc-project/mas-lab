#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""List the direct dependencies that have a newer release than the lock, and keep one issue per such dependency.

A dependency is *behind* when PyPI has a stable release newer than the one in ``uv.lock`` that the declared
range allows, and *blocked* when the newest release is outside a declared range (``slim-bindings<4``).
A patch release (same major and minor) is not reported: it does not ask for code changes. Either way
someone has to read the release notes and adapt the code, so each reported dependency gets one issue titled
``Dependency: <name>``. The issue is updated when a newer release appears and closed when the lock has
caught up. An issue closed by hand is not reopened for the same release, only for a newer one.

    python3 scripts/dependency_watch.py            # print the findings
    python3 scripts/dependency_watch.py --issues   # also open, update and close the issues (needs ``gh``)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[1]
LABEL = "dependency-watch"
PYPI = "https://pypi.org/pypi/{name}/json"


@dataclass(frozen=True)
class Finding:
    name: str
    locked: str
    latest: str
    status: str  # "behind" or "blocked"
    ranges: tuple[str, ...]  # "<file>: <specifier>" for each place that declares the dependency

    @property
    def major(self) -> bool:
        return Version(self.latest).major != Version(self.locked).major


def _requirements(document: dict[str, Any]) -> list[str]:
    project = document.get("project", {})
    found = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        found += extra
    for group in document.get("dependency-groups", {}).values():
        found += [item for item in group if isinstance(item, str)]
    return found


def declared(root: Path) -> tuple[dict[str, list[tuple[str, Requirement]]], set[str]]:
    """Third-party requirements by name, each with the file that declares it, and the workspace's own names."""
    top = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    members = ["."] + top["tool"]["uv"]["workspace"]["members"]
    own: set[str] = set()
    found: dict[str, list[tuple[str, Requirement]]] = {}
    documents = []
    for member in members:
        path = root / member / "pyproject.toml"
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        own.add(canonicalize_name(document["project"]["name"]))
        documents.append((path.relative_to(root).as_posix(), document))
    for relative, document in documents:
        for text in _requirements(document):
            requirement = Requirement(text)
            name = canonicalize_name(requirement.name)
            if name not in own:
                found.setdefault(name, []).append((relative, requirement))
    return found, own


def locked(root: Path) -> dict[str, str]:
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = lock["package"]
    return {canonicalize_name(package["name"]): package["version"] for package in packages if "version" in package}


def pypi_latest(name: str) -> str | None:
    """The newest stable, not fully yanked release of ``name`` on PyPI."""
    with urllib.request.urlopen(PYPI.format(name=name), timeout=30) as response:  # noqa: S310 - fixed https host
        releases = json.load(response)["releases"]
    best: Version | None = None
    for text, files in releases.items():
        try:
            version = Version(text)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease or not files or all(f.get("yanked") for f in files):
            continue
        best = version if best is None or version > best else best
    return str(best) if best else None


def _release(version: str) -> tuple[int, int]:
    parsed = Version(version)
    return parsed.major, parsed.minor


def findings(root: Path, latest_of: Callable[[str], str | None] = pypi_latest) -> list[Finding]:
    wanted, _own = declared(root)
    pinned = locked(root)
    names = sorted(name for name in wanted if name in pinned)
    with ThreadPoolExecutor(max_workers=8) as pool:
        latest = dict(zip(names, pool.map(latest_of, names), strict=True))
    out: list[Finding] = []
    for name in names:
        newest = latest[name]
        if newest is None or Version(newest) <= Version(pinned[name]) or _release(newest) == _release(pinned[name]):
            continue
        ranges = tuple(f"{file}: {requirement.specifier or 'any'}" for file, requirement in wanted[name])
        allowed = all(requirement.specifier.contains(newest, prereleases=False) for _file, requirement in wanted[name])
        out.append(Finding(name, pinned[name], newest, "behind" if allowed else "blocked", ranges))
    return out


def marker(name: str, latest: str | None = None) -> str:
    return f"<!-- {LABEL}:{name}" + (f":{latest}" if latest else "") + " -->"


def body(finding: Finding) -> str:
    why = (
        "The newest release is outside a declared range, so it cannot be locked until the range is changed."
        if finding.status == "blocked"
        else "The declared range allows it; the lock is behind."
    )
    lines = [
        f"`{finding.name}` {finding.latest} is out. The lock has {finding.locked}. {why}",
        "",
        "Declared in:",
        *[f"- `{item}`" for item in finding.ranges],
        "",
        f"Release: https://pypi.org/project/{finding.name}/{finding.latest}/",
        "",
        "To do: read the release notes, adapt the code, widen the range if it blocks, run "
        f"`uv lock --upgrade-package {finding.name}`, run the suites.",
    ]
    if finding.major:
        lines.insert(1, "")
        lines.insert(2, "This is a new major version: expect breaking changes.")
    lines += ["", marker(finding.name, finding.latest)]
    return "\n".join(lines)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _issues(state: str, run: Callable[..., str]) -> list[dict[str, Any]]:
    """Every issue in ``state`` (any label), as listed by the API, not by the search index."""
    out = run("issue", "list", "--state", state, "--limit", "1000", "--json", "number,title,body,labels")
    return json.loads(out)


def _title(name: str) -> str:
    return f"Dependency: {name}"


def _seen(issue: dict[str, Any]) -> str:
    """The release the issue was last written for, or "" when its body has lost the marker."""
    for line in (issue.get("body") or "").splitlines():
        if line.startswith(f"<!-- {LABEL}:") and line.endswith(" -->"):
            parts = line[len("<!-- ") : -len(" -->")].split(":")
            if len(parts) == 3:
                return parts[2]
    return ""


def _ours(issue: dict[str, Any]) -> bool:
    return bool(_seen(issue)) or LABEL in {label["name"] for label in issue.get("labels", [])}


def sync(items: list[Finding], run: Callable[..., str] = _gh) -> list[str]:
    """Open, update and close the issues so that they mirror ``items``. Returns what it did.

    An issue is found by its title alone, so an edited body or a removed label never makes a second one;
    of several issues with one title the oldest is kept up to date.
    """
    done: list[str] = []
    opened: dict[str, dict[str, Any]] = {}
    for issue in sorted(_issues("open", run), key=lambda i: i["number"], reverse=True):
        opened[issue["title"]] = issue
    closed = {(issue["title"], _seen(issue)) for issue in _issues("closed", run)}
    for finding in items:
        title = _title(finding.name)
        issue = opened.get(title)
        if issue is not None:
            number, seen = issue["number"], _seen(issue)
            if seen != finding.latest:
                run("issue", "edit", str(number), "--body", body(finding), "--add-label", LABEL)
                note = f"{finding.latest} is out" + (f" (was {seen})." if seen else ".")
                run("issue", "comment", str(number), "--body", note)
                done.append(f"updated #{number} {finding.name} {seen or '?'} -> {finding.latest}")
        elif (title, finding.latest) not in closed:
            run("issue", "create", "--title", title, "--body", body(finding), "--label", LABEL)
            done.append(f"opened {finding.name} {finding.latest}")
    current = {_title(finding.name) for finding in items}
    for title, issue in opened.items():
        if title not in current and _ours(issue) and title.startswith("Dependency: "):
            name = title.removeprefix("Dependency: ")
            run("issue", "close", str(issue["number"]), "--comment", f"The lock has caught up with `{name}`.")
            done.append(f"closed #{issue['number']} {name}")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--issues", action="store_true", help="open, update and close the issues with gh")
    args = parser.parse_args(argv)
    items = findings(REPO_ROOT)
    for item in items:
        flag = " (major)" if item.major else ""
        print(f"{item.name:30} locked {item.locked:12} latest {item.latest:12} {item.status}{flag}")
    if not items:
        print("every direct dependency is on its newest release")
    if args.issues:
        description = "A dependency has a newer release"
        _gh("label", "create", LABEL, "--color", "0e8a16", "--description", description, "--force")
        for line in sync(items):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
