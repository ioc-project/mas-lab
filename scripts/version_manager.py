#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
Multi-package versioning script for the MAS Lab monorepo.

Each package has its own version tracked in its pyproject.toml.
The root VERSION file is the source of truth for coordinated releases.

Packages: short key → pyproject.toml (wheel name is [project].name there).
  runtime, ctl, library, samples, skills, eval, lab-library, ioa, kg,
  telemetry, lab, core, bench, controller

Usage:
  python scripts/version_manager.py get [package]         # Get version
  python scripts/version_manager.py get-full [package]    # With SHA if RC
    python scripts/version_manager.py get-rolling [package] # Always base+g<sha>
    python scripts/version_manager.py check                 # Verify versions consistency
  python scripts/version_manager.py bump <type> [package] # Bump version
  python scripts/version_manager.py set <version> [package] # Set version

    package: runtime | ctl | library | samples | skills | eval | lab-library |
                     ioa | kg | telemetry | lab | core | bench | controller | all
  type:    rc | patch | minor | major
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
VERSION_FILE = ROOT / "VERSION"

# Package name → pyproject.toml path
PACKAGES: dict[str, Path] = {
    "runtime": ROOT / "runtime" / "pyproject.toml",
    "ctl": ROOT / "ctl" / "pyproject.toml",
    "library": ROOT / "library-standard" / "pyproject.toml",
    "samples": ROOT / "library-samples" / "pyproject.toml",
    "skills": ROOT / "library-skills" / "pyproject.toml",
    "eval": ROOT / "library-eval" / "pyproject.toml",
    "lab-library": ROOT / "library-lab" / "pyproject.toml",
    "ioa": ROOT / "library-ioa" / "pyproject.toml",
    "kg": ROOT / "library-kg" / "pyproject.toml",
    "telemetry": ROOT / "library-telemetry" / "pyproject.toml",
    "lab": ROOT / "pyproject.toml",
    "core": ROOT / "lab" / "components" / "core" / "pyproject.toml",
    "bench": ROOT / "lab" / "components" / "bench" / "pyproject.toml",
    "controller": ROOT / "lab" / "components" / "controller" / "pyproject.toml",
}

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(rc\d+))?$")


def get_git_sha(short: bool = True) -> str | None:
    try:
        cmd = ["git", "rev-parse", "--short=7" if short else "", "HEAD"]
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return None


def parse_version(v: str) -> dict:
    m = VERSION_RE.match(v)
    if not m:
        raise ValueError(f"Invalid version: {v!r} (expected X.Y.Z or X.Y.Z-rcN)")
    major, minor, patch, pre = m.groups()
    return {"major": int(major), "minor": int(minor), "patch": int(patch), "prerelease": pre}


def format_version(c: dict) -> str:
    base = f"{c['major']}.{c['minor']}.{c['patch']}"
    return f"{base}-{c['prerelease']}" if c["prerelease"] else base


def read_root_version() -> str:
    return VERSION_FILE.read_text().strip()


def read_pyproject_version(pkg: str) -> str:
    path = PACKAGES[pkg]
    for line in path.read_text().splitlines():
        m = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    raise ValueError(f"version not found in {path}")


def write_pyproject_version(pkg: str, version: str) -> None:
    path = PACKAGES[pkg]
    content = path.read_text()
    updated = re.sub(r'version = "[^"]+"', f'version = "{version}"', content, count=1)
    path.write_text(updated)
    print(f"  ✅ {pkg}: {version} ({path.name})")


def _targets(pkg: str) -> list[str]:
    if pkg == "all":
        return list(PACKAGES)
    if pkg not in PACKAGES:
        raise ValueError(f"Unknown package {pkg!r}. Valid: {', '.join(PACKAGES)} | all")
    return [pkg]


def bump_version(bump_type: str, pkg: str = "all") -> None:
    targets = _targets(pkg)
    print(f"Bumping {bump_type} for: {', '.join(targets)}")
    for p in targets:
        v = read_pyproject_version(p)
        c = parse_version(v)
        if bump_type == "rc":
            if not c["prerelease"]:
                raise ValueError(f"[{p}] Cannot bump RC on non-RC version")
            n = int(re.match(r"rc(\d+)", c["prerelease"]).group(1))
            c["prerelease"] = f"rc{n + 1}"
        elif bump_type == "patch":
            if c["prerelease"]:
                c["prerelease"] = None
            else:
                c["patch"] += 1
        elif bump_type == "minor":
            c["minor"] += 1
            c["patch"] = 0
            c["prerelease"] = None
        elif bump_type == "major":
            c["major"] += 1
            c["minor"] = 0
            c["patch"] = 0
            c["prerelease"] = None
        else:
            raise ValueError(f"Invalid bump type: {bump_type!r}")
        new_v = format_version(c)
        write_pyproject_version(p, new_v)
    # Update root VERSION with the lab version
    lab_v = read_pyproject_version("lab")
    VERSION_FILE.write_text(lab_v + "\n")
    print(f"  ✅ VERSION: {lab_v}")


def set_version(version: str, pkg: str = "all") -> None:
    parse_version(version)
    targets = _targets(pkg)
    print(f"Setting {version} for: {', '.join(targets)}")
    for p in targets:
        write_pyproject_version(p, version)
    VERSION_FILE.write_text(version + "\n")
    print(f"  ✅ VERSION: {version}")


def get_full(pkg: str = "lab") -> str:
    v = read_pyproject_version(pkg)
    c = parse_version(v)
    if c["prerelease"]:
        sha = get_git_sha()
        if sha:
            return f"{v}+g{sha}"
    return v


def get_rolling(pkg: str = "lab") -> str:
    """Return rolling version for main snapshots: X.Y.Z+g<sha>."""
    v = read_pyproject_version(pkg)
    sha = get_git_sha()
    if not sha:
        return v
    return f"{v}+g{sha}"


def check_versions() -> None:
    """Fail if package versions are not aligned with root VERSION."""
    root = read_root_version()
    mismatches: list[str] = []
    for pkg in PACKAGES:
        pv = read_pyproject_version(pkg)
        if pv != root:
            mismatches.append(f"{pkg}={pv}")
    if mismatches:
        raise ValueError(f"Version mismatch with VERSION={root}: " + ", ".join(mismatches))
    print(f"All package versions match VERSION={root}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd, pkg = args[0], args[1] if len(args) > 1 else "lab"

    try:
        if cmd == "get":
            if pkg == "all":
                for p in PACKAGES:
                    print(f"{p}={read_pyproject_version(p)}")
            else:
                print(read_pyproject_version(pkg if pkg in PACKAGES else "lab"))
        elif cmd == "get-full":
            print(get_full(pkg if pkg in PACKAGES else "lab"))
        elif cmd == "get-rolling":
            print(get_rolling(pkg if pkg in PACKAGES else "lab"))
        elif cmd == "check":
            check_versions()
        elif cmd == "bump":
            if len(args) < 2:
                print("Error: bump requires type", file=sys.stderr)
                sys.exit(1)
            bump_type = args[1]
            pkg = args[2] if len(args) > 2 else "all"
            bump_version(bump_type, pkg)
        elif cmd == "set":
            if len(args) < 2:
                print("Error: set requires version", file=sys.stderr)
                sys.exit(1)
            version = args[1]
            pkg = args[2] if len(args) > 2 else "all"
            set_version(version, pkg)
        else:
            print(f"Error: Unknown command {cmd!r}", file=sys.stderr)
            sys.exit(1)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
