#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``--control`` and ``--events-sink`` write the session's Flavour; a bad value is one error line."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

MAS_CTL = Path(sys.executable).parent / "mas-ctl"
_AGENT = (
    "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: desk}\n"
    'spec:\n  description: "x"\n  models: [{model: gpt-4o-mini}]\n  context: {role: "x"}\n'
)


def _chat(tmp_path: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    if not MAS_CTL.is_file():
        pytest.skip("mas-ctl CLI not in venv")
    (tmp_path / "agent.yaml").write_text(_AGENT, encoding="utf-8")
    return subprocess.run(
        [str(MAS_CTL), "chat", "agent.yaml", *flags, "-q", "hi"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("command", ["chat", "tui", "serve", "run-mas", "compile", "infra resolved"])
def test_the_control_flag_exists_on_every_session_command(command: str) -> None:
    if not MAS_CTL.is_file():
        pytest.skip("mas-ctl CLI not in venv")
    out = subprocess.run([str(MAS_CTL), *command.split(), "--help"], capture_output=True, text=True, timeout=60).stdout
    assert "--control REF[@SELECTOR]" in out
    if command not in {"compile", "infra resolved"}:
        assert "--events-sink ROW" in out
    if command in {"chat", "tui", "serve", "run-mas"}:
        assert "--events / --no-events" in out
        assert "--events-format [" in out


def test_no_control_dir_is_listed_on_chat_and_serve() -> None:
    if not MAS_CTL.is_file():
        pytest.skip("mas-ctl CLI not in venv")
    for command in ("chat", "serve"):
        out = subprocess.run([str(MAS_CTL), command, "--help"], capture_output=True, text=True, timeout=60).stdout
        assert "--no-control-dir" in out
        assert "not of --control" in out


def test_no_control_dir_does_not_skip_control_files(tmp_path: Path) -> None:
    proc = _chat(tmp_path, "--no-control-dir", "--control", "nowhere.yaml")
    assert proc.returncode != 0
    assert "Control ref 'nowhere.yaml' is not a file" in proc.stderr
    assert "skips the live control endpoint" not in proc.stderr


def test_an_unknown_control_ref_is_one_error_line(tmp_path: Path) -> None:
    proc = _chat(tmp_path, "--control", "nowhere.yaml")
    assert proc.returncode != 0
    assert "error: Control ref 'nowhere.yaml' is not a file" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_an_unknown_events_sink_row_is_one_error_line(tmp_path: Path) -> None:
    proc = _chat(tmp_path, "--events-sink", "collector")
    assert proc.returncode != 0
    assert "error: Flavour bind.events names 'collector', which is no Application row" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_compile_control_does_not_copy_governance(tmp_path: Path) -> None:
    if not MAS_CTL.is_file():
        pytest.skip("mas-ctl CLI not in venv")
    (tmp_path / "agent.yaml").write_text(_AGENT, encoding="utf-8")
    (tmp_path / "gov.yaml").write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: gov}\nspec:\n  governance: [sample_governance]\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(MAS_CTL), "compile", "agent.yaml", "--control", "gov.yaml", "--no-validate"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "sample_governance" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_compile_flavour_control_does_not_copy_governance(tmp_path: Path) -> None:
    if not MAS_CTL.is_file():
        pytest.skip("mas-ctl CLI not in venv")
    (tmp_path / "agent.yaml").write_text(_AGENT, encoding="utf-8")
    (tmp_path / "gov.yaml").write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: gov}\nspec:\n  governance: [sample_governance]\n",
        encoding="utf-8",
    )
    (tmp_path / "desk.yaml").write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\nspec:\n  control:\n    - {ref: gov.yaml}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(MAS_CTL), "compile", "agent.yaml", "--flavour", "desk.yaml", "--no-validate"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "sample_governance" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_a_control_with_an_absolute_path_is_refused_on_load(tmp_path: Path) -> None:
    control = tmp_path / "abs.yaml"
    control.write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: abs}\n"
        "spec:\n  governance:\n    - path_probe: {script_file: /etc/x.gdb}\n",
        encoding="utf-8",
    )
    proc = _chat(tmp_path, "--control", str(control))
    assert proc.returncode != 0
    assert "absolute path '/etc/x.gdb'" in proc.stderr
