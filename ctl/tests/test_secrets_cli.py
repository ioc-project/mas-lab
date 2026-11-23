#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl secrets: values never printed, files written atomically, refs checked."""

from __future__ import annotations

import os
import stat
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner
from mas.ctl.cli.main import app
from mas.ctl.secrets import store as ctl_store
from mas.ctl.secrets import write as ctl_write
from mas.ctl.secrets.write import write_file_secret
from mas.runtime.secrets import SecretDenied, SecretRef, SecretRefError
from secret_fakes import CANARY

_POSIX = sys.platform != "win32"


def _store(tmp_path: Path) -> Path:
    return tmp_path / "store"


def _cli(*args: str, input: str | None = None):
    return CliRunner().invoke(app, list(args), input=input)


def test_list_walks_keyed_directories_and_never_prints_a_value(tmp_path: Path) -> None:
    store = _store(tmp_path)
    write_file_secret(store, "openai", CANARY)
    write_file_secret(store, "mcp", "b", key="bearer")
    write_file_secret(store, "mcp", "c", key="value")
    result = _cli("--secrets-dir", str(store), "secrets", "list")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["mcp/bearer  present", "mcp/value  present", "openai/value  present"]
    assert CANARY not in result.output


def test_list_refuses_the_env_store(tmp_path: Path) -> None:
    write_file_secret(tmp_path, "file-only", CANARY)
    result = _cli("--secrets-dir", str(tmp_path), "--secret-store", "env", "secrets", "list")
    assert result.exit_code != 0
    assert "cannot be listed" in result.output and "file-only" not in result.output


@pytest.mark.skipif(not _POSIX, reason="POSIX modes")
def test_write_file_secret_is_0400_and_leaves_no_temporary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    write_file_secret(store, "openai", "first")
    path = write_file_secret(store, "openai", "second")
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert path.read_text() == "second"
    assert sorted(p.name for p in store.iterdir()) == [".gitignore", "openai"]


def test_write_file_secret_removes_the_temporary_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)

    def boom(*_a: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ctl_write.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_file_secret(store, "openai", "v")
    assert sorted(p.name for p in store.iterdir()) == [".gitignore"]


def test_write_file_secret_never_replaces_a_keyed_directory_and_refuses_the_reverse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    write_file_secret(store, "mcp", "b", key="bearer")
    assert write_file_secret(store, "mcp", "x") == store / "mcp" / "value"
    assert (store / "mcp" / "bearer").read_text() == "b"
    write_file_secret(store, "openai", "a")
    with pytest.raises(PermissionError, match="not a directory"):
        write_file_secret(store, "openai", "x", key="bearer")


@pytest.mark.skipif(not _POSIX, reason="POSIX owners")
def test_write_file_secret_refuses_a_keyed_directory_of_another_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_file_secret(tmp_path / "s", "svc", "a", key="one")
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(tmp_path / "s").st_uid + 1)
    with pytest.raises(PermissionError, match="not owned"):
        write_file_secret(tmp_path / "s", "svc", "b", key="two")


def test_write_file_secret_refuses_bad_names_values_and_sizes(tmp_path: Path) -> None:
    with pytest.raises(SecretDenied, match="not a valid secret value") as caught:
        write_file_secret(tmp_path / "s", "openai", f"{CANARY}\r\nX: y")
    assert CANARY not in str(caught.value)
    with pytest.raises(SecretRefError, match="not a secret name"):
        write_file_secret(tmp_path / "s", "openai\n", "x")
    with pytest.raises(SecretRefError, match="not a secret name"):
        write_file_secret(tmp_path / "s", "openai", "x", key="value\n")
    with pytest.raises(SecretDenied, match="too large"):
        write_file_secret(tmp_path / "s", "big", b"a" * 11, max_bytes=10)
    assert not (tmp_path / "s").exists()


@pytest.mark.skipif(not _POSIX, reason="POSIX modes")
def test_init_creates_0700_and_never_changes_an_existing_directory(tmp_path: Path) -> None:
    store = tmp_path / "ws" / ".mas" / "secrets"
    result = _cli("--secrets-dir", str(store), "secrets", "init")
    assert result.exit_code == 0, result.output
    assert (store / ".gitignore").read_text() == "*\n"
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    loose = tmp_path / "loose"
    loose.mkdir()
    os.chmod(loose, 0o755)
    result = _cli("--secrets-dir", str(loose), "secrets", "init")
    assert result.exit_code != 0 and "group or world" in result.output
    assert stat.S_IMODE(loose.stat().st_mode) == 0o755


@pytest.mark.skipif(not _POSIX, reason="POSIX symlinks")
def test_init_and_the_store_resolve_a_symlinked_root_the_same_way(tmp_path: Path) -> None:
    real = tmp_path / "real"
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    real.mkdir(mode=0o700)
    assert ctl_write.init_secrets_dir(link) == real.resolve()
    write_file_secret(link, "openai", "tok")
    assert ctl_store.build_resolver(directory=link).reveal(SecretRef("openai")) == "tok"


def test_set_reads_stdin_strips_one_crlf_and_refuses_over_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mas.library.standard.plugins.secrets import file_store

    store = _store(tmp_path)
    result = _cli("--secrets-dir", str(store), "secrets", "set", "openai", input="tok\r\n")
    assert result.exit_code == 0, result.output
    assert (store / "openai").read_bytes() == b"tok"
    monkeypatch.setattr(file_store, "DEFAULT_MAX_BYTES", 10)
    result = _cli("--secrets-dir", str(store), "secrets", "set", "big", input="a" * 50)
    assert result.exit_code != 0 and "larger than 10 bytes" in result.output
    assert not (store / "big").exists()
    ok = _cli("--secrets-dir", str(store), "secrets", "set", "ok", input="a" * 10 + "\r\n")
    assert ok.exit_code == 0, ok.output


@pytest.mark.parametrize(
    "extra", [["sk-canary-Vq12"], ["sk-canary-Vq12", "more"], ["-sk-canary-Vq12"], ["--sk-canary-Vq12"]]
)
def test_set_refuses_a_value_on_the_command_line_without_printing_it(tmp_path: Path, extra: list[str]) -> None:
    # kills: Click's "Got unexpected extra argument (<value>)" / "No such option" echoing the secret
    store = _store(tmp_path)
    result = _cli("--secrets-dir", str(store), "secrets", "set", "openai", *extra, input="tok\n")
    assert result.exit_code == 2
    assert "sk-canary-Vq12" not in result.output
    assert "never from the command line" in result.output
    assert not (store / "openai").exists()


def test_set_on_a_terminal_prompts_hidden_with_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.ctl.cli.commands import secrets as cmd

    store = _store(tmp_path)
    monkeypatch.setattr(cmd, "sys", types.SimpleNamespace(stdin=types.SimpleNamespace(isatty=lambda: True)))
    result = _cli("--secrets-dir", str(store), "secrets", "set", "openai", input=f"{CANARY}\n{CANARY}\n")
    assert result.exit_code == 0, result.output
    assert CANARY not in result.output
    assert "Repeat for confirmation" in result.output
    assert (store / "openai").read_text() == CANARY


def test_import_env_writes_known_variables_and_skips_the_rest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_API_KEY=sk-one # personal key\nMAS_SECRET_MCP_UPSTREAM__BEARER='tok'\nDEBUG=1\n",
        encoding="utf-8",
    )
    result = _cli("--secrets-dir", str(store), "secrets", "import-env", str(env))
    assert result.exit_code == 0, result.output
    assert (store / "openai").read_text() == "sk-one"
    assert (store / "mcp-upstream" / "bearer").read_text() == "tok"
    assert "skipped DEBUG" in result.output
    assert not (store / "debug").exists()
    missing = _cli("--secrets-dir", str(store), "secrets", "import-env", CANARY)
    assert missing.exit_code != 0 and "no such dotenv file" in missing.output
    assert CANARY not in missing.output


@pytest.mark.parametrize(
    "args",
    [["secrets", "set", "openai"], ["secrets", "init"], ["secrets", "import-env", "ENVFILE"]],
)
def test_write_subcommands_refuse_the_env_store_and_write_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=tok\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    args = [str(env_file) if a == "ENVFILE" else a for a in args]
    result = _cli("--secret-store", "env", *args, input="tok\n")
    assert result.exit_code != 0
    assert "the env store reads MAS_SECRET_* variables" in result.output
    assert "--secret-store file" in result.output
    assert not (work / ".mas").exists()


def test_hardening_reads_the_installed_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: the hardening input reloading the workspace instead of reading the store this process uses
    from mas.ctl.hardening.collect import from_documents
    from mas.ctl.workspace.config import WorkspaceConfig
    from mas.runtime.secrets import resolver

    monkeypatch.setattr(resolver, "_PROCESS", [])
    workspace = WorkspaceConfig({"secrets": {"allow_loose_permissions": True, "dir": str(tmp_path)}})
    ctl_store.install_process_resolver(workspace=workspace)
    assert (from_documents([]).secret_store, from_documents([]).allow_loose_permissions) == ("file", True)
    ctl_store.install_process_resolver(store="env", workspace=workspace)
    assert (from_documents([]).secret_store, from_documents([]).allow_loose_permissions) == ("env", False)
