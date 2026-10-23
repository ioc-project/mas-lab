#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MkDocs hook: bake GitHub source facts (version, stars, forks) at build.

Material 9 fetches those facts in the browser against api.github.com, which
hits the unauthenticated rate limit on GitHub Pages. This hook reads VERSION
and (when reachable) the GitHub repo API, then the source partial renders
them statically. Pages CI passes GITHUB_TOKEN; a nightly rebuild refreshes
stars and forks.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_GITHUB_REPO_RE = re.compile(
    r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_STATS: dict[str, str] | None = None


def compact_count(n: int) -> str:
    """Match Material's client-side compact formatter (thousands as ``k``)."""
    if n > 999:
        decimals = int(((n - 950) % 1000) > 99)
        return f"{(n + 1e-6) / 1000:.{decimals}f}k"
    return str(n)


def github_owner_repo(repo_url: str) -> tuple[str, str] | None:
    match = _GITHUB_REPO_RE.search((repo_url or "").rstrip("/"))
    if not match:
        return None
    return match.group("owner"), match.group("repo")


def read_version(root: Path) -> str:
    raw = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    return raw if raw.lower().startswith("v") else f"v{raw}"


def fetch_repo_stats(owner: str, repo: str, token: str | None) -> dict[str, str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mas-lab-docs",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    stats: dict[str, str] = {}
    if "stargazers_count" in payload:
        stats["stars"] = compact_count(int(payload["stargazers_count"]))
    if "forks_count" in payload:
        stats["forks"] = compact_count(int(payload["forks_count"]))
    return stats


def github_facts(repo_url: str, root: Path, token: str | None) -> dict[str, str]:
    facts: dict[str, str] = {}
    version = read_version(root)
    if version:
        facts["version"] = version
    parsed = github_owner_repo(repo_url)
    if parsed is None:
        return facts
    try:
        facts.update(fetch_repo_stats(*parsed, token))
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        pass
    return facts


def on_config(config: Any) -> Any:
    global _STATS
    extra = config.extra
    if extra is None:
        extra = {}
        config.extra = extra
    if _STATS is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        root = Path(config.config_file_path).resolve().parent
        _STATS = github_facts(str(config.repo_url or ""), root, token)
    extra["github"] = dict(_STATS)
    return config
