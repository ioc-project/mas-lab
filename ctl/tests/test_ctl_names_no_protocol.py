#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ctl names no agent-to-agent protocol: no slim:// scheme, no protocol literal in the chain code, no MAS agent_comm."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from mas.ctl.env import env_named_secret
from mas.ctl.overrides.bind import expand_connects, expand_listens
from mas.ctl.validate import validate_file


def test_connect_slim_is_rejected() -> None:
    with pytest.raises(ValueError, match="slim"):
        expand_connects(("node=slim://127.0.0.1:46357",))


def test_connect_slims_is_rejected() -> None:
    with pytest.raises(ValueError, match="slims"):
        expand_connects(("node=slims://example.test:46357",))


def test_listen_slim_is_rejected() -> None:
    with pytest.raises(ValueError, match="slim"):
        expand_listens(("buyer=slim://127.0.0.1:46357",))


def test_bind_module_does_not_name_slim() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/mas/ctl/overrides/bind.py").read_text(encoding="utf-8")
    assert "slim" not in source.lower()
    flags = (Path(__file__).resolve().parents[1] / "src/mas/ctl/cli/bind_flags.py").read_text(encoding="utf-8")
    assert "slim://" not in flags


def test_no_ctl_source_file_names_slim() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    named = sorted(
        str(path.relative_to(src)) for path in src.rglob("*.py") if "slim" in path.read_text(encoding="utf-8").lower()
    )
    assert named == []


def test_env_named_secret_does_not_log_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
    monkeypatch.setenv("SLIM_SECRET", secret)
    with caplog.at_level(logging.DEBUG, logger="mas.ctl.env"):
        assert env_named_secret("SLIM_SECRET") == secret
    assert secret not in caplog.text
    monkeypatch.delenv("SLIM_SECRET")
    with pytest.raises(ValueError, match="missing secret"):
        env_named_secret("SLIM_SECRET")
    with pytest.raises(ValueError, match="environment variable name"):
        env_named_secret("not a var")


def test_mas_spec_rejects_agent_comm(tmp_path: Path) -> None:
    path = tmp_path / "mas.yaml"
    (tmp_path / "coordinator.yaml").write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: coordinator}\nspec: {}\n",
        encoding="utf-8",
    )
    path.write_text(
        "apiVersion: mas/v1\n"
        "kind: MAS\n"
        "metadata: {name: slim-demo}\n"
        "spec:\n"
        "  agency:\n"
        "    agents:\n"
        "      - id: coordinator\n"
        "        ref: coordinator.yaml\n"
        "  workflow:\n"
        "    entry: coordinator\n"
        "    nodes:\n"
        "      - id: coordinator\n"
        "  agent_comm:\n"
        "    type: slim\n",
        encoding="utf-8",
    )
    result = validate_file(path, kind="mas", strict=True)
    assert not result.ok


@pytest.mark.parametrize(
    "module",
    ["infra/bind.py", "infra/identities.py", "infra/show.py", "executor/stacks.py", "executor/mas_session.py"],
)
def test_the_chain_code_names_no_layer(module: str) -> None:
    import re

    source = (Path(__file__).resolve().parents[1] / "src/mas/ctl" / module).read_text(encoding="utf-8")
    literals = re.findall(r"[\"']([a-z0-9_-]+)[\"']", source)
    named = sorted(
        {word for word in literals if word in {"slim", "a2a", "http", "local", "local-group", "agent-local"}}
    )
    assert named == [], f"{module} names {named}"


def test_mas_overlay_rejects_agent_comm(tmp_path: Path) -> None:
    path = tmp_path / "overlay.yaml"
    path.write_text(
        "apiVersion: mas/v1\n"
        "kind: Overlay\n"
        "metadata: {name: slim-comm}\n"
        "spec:\n"
        "  target: {kind: MAS}\n"
        "  patch:\n"
        "    agent_comm:\n"
        "      type: slim\n",
        encoding="utf-8",
    )
    result = validate_file(path, kind="overlay", strict=True)
    assert not result.ok
