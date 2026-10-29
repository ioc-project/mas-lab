#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ctl resolves the Hugging Face token; lab does not read the environment."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.env import huggingface_token


def test_explicit_token_wins_over_file_and_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "from-env")
    token_file = tmp_path / "token"
    token_file.write_text("from-file\n", encoding="utf-8")
    assert huggingface_token(token="from-flag", token_file=token_file) == "from-flag"


def test_token_file_wins_over_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "from-env")
    token_file = tmp_path / "token"
    token_file.write_text("from-file\n", encoding="utf-8")
    assert huggingface_token(token_file=token_file) == "from-file"


def test_environment_is_used_when_no_flag_is_set(monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert huggingface_token() == "from-env"


def test_missing_token_is_an_error(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(ValueError, match="HF_TOKEN"):
        huggingface_token()


def test_empty_token_file_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    token_file = tmp_path / "token"
    token_file.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        huggingface_token(token_file=token_file)


def test_import_hf_dry_run_uses_hf_token_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "from-env")
    captured: dict[str, object] = {}

    def fake_import(args: object) -> int:
        captured["token"] = getattr(args, "token", None)
        return 0

    monkeypatch.setattr(
        "mas.lab.benchmark.cli.export_import.import_hf_command", fake_import
    )
    from click.testing import CliRunner
    from mas.lab.cli.commands.benchmark.import_hf import import_hf_cmd

    result = CliRunner().invoke(
        import_hf_cmd,
        ["benchmarks/demo.tar.gz", "--repo-id", "org/ds", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert captured["token"] == "from-env"


def test_import_hf_dry_run_without_a_token_is_allowed(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    captured: dict[str, object] = {}

    def fake_import(args: object) -> int:
        captured["token"] = getattr(args, "token", None)
        return 0

    monkeypatch.setattr(
        "mas.lab.benchmark.cli.export_import.import_hf_command", fake_import
    )
    from click.testing import CliRunner
    from mas.lab.cli.commands.benchmark.import_hf import import_hf_cmd

    result = CliRunner().invoke(
        import_hf_cmd,
        ["benchmarks/demo.tar.gz", "--repo-id", "org/ds", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert captured["token"] is None


def test_import_hf_dry_run_uses_an_explicit_token_flag(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    captured: dict[str, object] = {}

    def fake_import(args: object) -> int:
        captured["token"] = getattr(args, "token", None)
        return 0

    monkeypatch.setattr(
        "mas.lab.benchmark.cli.export_import.import_hf_command", fake_import
    )
    from click.testing import CliRunner
    from mas.lab.cli.commands.benchmark.import_hf import import_hf_cmd

    result = CliRunner().invoke(
        import_hf_cmd,
        [
            "benchmarks/demo.tar.gz",
            "--repo-id",
            "org/ds",
            "--dry-run",
            "--token",
            "from-flag",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["token"] == "from-flag"
