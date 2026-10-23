#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "scripts" / "mkdocs_github_stats.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("mkdocs_github_stats", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


def test_compact_count_matches_material() -> None:
    assert hook.compact_count(12) == "12"
    assert hook.compact_count(999) == "999"
    assert hook.compact_count(1000) == "1k"
    assert hook.compact_count(1200) == "1.2k"


def test_github_owner_repo_parses_https_and_git() -> None:
    assert hook.github_owner_repo("https://github.com/ioc-project/mas-lab") == (
        "ioc-project",
        "mas-lab",
    )
    assert hook.github_owner_repo("https://github.com/ioc-project/mas-lab.git") == (
        "ioc-project",
        "mas-lab",
    )
    assert hook.github_owner_repo("https://example.com/not-github") is None


def test_read_version_prefixes_v(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    assert hook.read_version(tmp_path) == "v0.2.0"
    (tmp_path / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
    assert hook.read_version(tmp_path) == "v1.0.0"


def test_github_facts_uses_version_when_api_fails(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    def boom(*_args, **_kwargs):
        raise TimeoutError("no network")

    monkeypatch.setattr(hook, "fetch_repo_stats", boom)
    facts = hook.github_facts(
        "https://github.com/ioc-project/mas-lab", tmp_path, token=None
    )
    assert facts == {"version": "v0.2.0"}


def test_github_facts_includes_stars_and_forks(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    monkeypatch.setattr(
        hook,
        "fetch_repo_stats",
        lambda *_args, **_kwargs: {"stars": "12", "forks": "3"},
    )
    facts = hook.github_facts(
        "https://github.com/ioc-project/mas-lab", tmp_path, token="x"
    )
    assert facts == {"version": "v0.2.0", "stars": "12", "forks": "3"}


def test_on_config_injects_extra_github(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    (tmp_path / "mkdocs.yml").write_text("site_name: t\n", encoding="utf-8")
    monkeypatch.setattr(
        hook,
        "fetch_repo_stats",
        lambda *_args, **_kwargs: {"stars": "1", "forks": "0"},
    )
    monkeypatch.setattr(hook, "_STATS", None)
    config = SimpleNamespace(
        extra={},
        repo_url="https://github.com/ioc-project/mas-lab",
        config_file_path=str(tmp_path / "mkdocs.yml"),
    )
    hook.on_config(config)
    assert config.extra["github"]["version"] == "v0.2.0"
    assert config.extra["github"]["stars"] == "1"


def test_fetch_repo_stats_reads_github_payload(monkeypatch) -> None:
    class _Resp:
        def read(self) -> bytes:
            return json.dumps(
                {"stargazers_count": 1200, "forks_count": 3}
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization") or request.get_header(
            "Authorization"
        )
        return _Resp()

    monkeypatch.setattr(hook.urllib.request, "urlopen", fake_urlopen)
    stats = hook.fetch_repo_stats("ioc-project", "mas-lab", "tok")
    assert stats == {"stars": "1.2k", "forks": "3"}
    assert captured["url"].endswith("/repos/ioc-project/mas-lab")
    assert captured["auth"] == "Bearer tok"
