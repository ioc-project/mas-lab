#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""File and env secret stores: permissions, escape, rotation, conformance."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mas.library.standard.plugins.secrets.env_store import EnvSecretStore, env_var_for
from mas.library.standard.plugins.secrets.file_store import FileSecretStore
from mas.runtime.secrets.errors import SecretDenied, SecretMissing
from mas.runtime.secrets.ref import SecretRef
from secret_fakes import CANARY, check_secret_store

_WIN = sys.platform == "win32"


def _secret_dir(tmp_path: Path) -> Path:
    path = tmp_path / "secrets"
    path.mkdir(mode=0o700)
    return path


def _write(path: Path, value: str, *, mode: int = 0o600) -> None:
    path.write_bytes(value.encode("utf-8"))
    path.chmod(mode)


def test_file_store_passes_the_kit(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    store = FileSecretStore(root)

    def put(name: str, key: str, value: str) -> None:
        if key == "value":
            target = root / name
        else:
            (root / name).mkdir(mode=0o700, exist_ok=True)
            target = root / name / key
        _write(target, value)

    check_secret_store(store, put=put, rotate=put)


@pytest.mark.skipif(_WIN, reason="POSIX mode checks")
def test_file_store_rejects_world_readable(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "openai", "tok", mode=0o644)
    with pytest.raises(SecretDenied, match="group or world"):
        FileSecretStore(root).get(SecretRef("openai"))
    FileSecretStore(root, allow_loose_permissions=True).get(SecretRef("openai"))


def test_env_store_fixed_mapping_and_kit(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.runtime import hostenv

    store = EnvSecretStore(hostenv.private)
    ref = SecretRef("openai")
    assert env_var_for(ref) == "MAS_SECRET_OPENAI"
    assert env_var_for(SecretRef(name="mcp-upstream", key="bearer")) == "MAS_SECRET_MCP_UPSTREAM__BEARER"

    def put(name: str, key: str, value: str) -> None:
        monkeypatch.setenv(env_var_for(SecretRef(name=name, key=key)), value)

    check_secret_store(store, put=put, rotate=put)
    monkeypatch.delenv("MAS_SECRET_KIT", raising=False)
    with pytest.raises(SecretMissing):
        store.get(SecretRef("kit"))


@pytest.mark.parametrize("name", ["a_b", "a--b"])
def test_env_store_refuses_names_it_cannot_map_one_to_one(name: str) -> None:
    store = EnvSecretStore({})
    with pytest.raises(SecretDenied, match="env store"):
        store.get(SecretRef(name))
    assert store.describe(SecretRef(name)).present is False


def test_env_store_names_never_share_a_variable() -> None:
    from mas.library.standard.plugins.secrets.env_store import ref_for_env_var

    refs = [SecretRef("a-b"), SecretRef(name="a", key="b"), SecretRef("ab"), SecretRef(name="a-b", key="c")]
    variables = [env_var_for(ref) for ref in refs]
    assert len(set(variables)) == len(refs)
    for ref, variable in zip(refs, variables, strict=True):
        assert ref_for_env_var(variable) == ref
    assert ref_for_env_var("MAS_SECRET_A___B") is None
    assert ref_for_env_var("OPENAI_API_KEY") is None


def _assert_canary_absent(exc: BaseException) -> None:
    seen: list[BaseException] = []
    node: BaseException | None = exc
    while node is not None and node not in seen:
        seen.append(node)
        assert CANARY not in str(node)
        assert CANARY not in repr(node.args)
        node = node.__cause__ or node.__context__


def test_file_store_strips_crlf(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "openai", f"{CANARY}\r\n")
    assert FileSecretStore(root).get(SecretRef("openai")).reveal() == CANARY


def test_file_store_refuses_a_control_byte_without_naming_the_value(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "openai", f"{CANARY}\x1btail")
    with pytest.raises(SecretDenied, match="not a valid secret value") as caught:
        FileSecretStore(root).get(SecretRef("openai"))
    _assert_canary_absent(caught.value)


def test_env_store_refuses_a_control_byte_without_naming_the_value() -> None:
    store = EnvSecretStore({"MAS_SECRET_OPENAI": f"{CANARY}\r\nX-Injected: 1"})
    with pytest.raises(SecretDenied, match="not a valid secret value") as caught:
        store.get(SecretRef("openai"))
    _assert_canary_absent(caught.value)


def test_file_store_refuses_a_value_over_the_cap(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "big", "x" * 20)
    store = FileSecretStore(root, max_bytes=10)
    assert store.describe(SecretRef("big")).reason == "too large"
    with pytest.raises(SecretDenied, match="too large"):
        store.get(SecretRef("big"))


def test_file_store_describe_runs_the_value_checks_of_get(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "openai", "tok\n")
    store = FileSecretStore(root)
    info = store.describe(SecretRef("openai"))
    assert (info.present, info.size) == (True, 3)
    for name, data, reason in (
        ("nul", b"ab\x00cd", "is not a valid secret value"),
        ("latin", b"\xff\xfe", "is not utf-8"),
        ("empty", b"", "is empty"),
        ("newline", b"\n", "is empty"),
    ):
        path = root / name
        path.write_bytes(data)
        os.chmod(path, 0o400)
        info = store.describe(SecretRef(name))
        assert (info.present, info.reason) == (False, reason)
        with pytest.raises(SecretDenied, match=reason):
            store.get(SecretRef(name))


posix_only = pytest.mark.skipif(_WIN, reason="POSIX descriptors and modes")


def test_file_store_short_ref_on_a_keyed_directory_reads_value(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    (root / "svc").mkdir(mode=0o700)
    _write(root / "svc" / "value", "tok")
    _write(root / "svc" / "other", "x")
    store = FileSecretStore(root)
    assert store.get(SecretRef("svc")).reveal() == "tok"
    assert store.describe(SecretRef("svc")).present is True


@posix_only
@pytest.mark.skipif(not _WIN and os.geteuid() == 0, reason="root reads a mode 0000 file")
def test_file_store_unreadable_file_is_denied_without_a_path(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "noperm", "tok", mode=0o000)
    store = FileSecretStore(root)
    with pytest.raises(SecretDenied, match="unreadable") as caught:
        store.get(SecretRef("noperm"))
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    info = store.describe(SecretRef("noperm"))
    assert (info.present, info.reason) == (False, "unreadable")


def test_file_store_refuses_a_loose_keyed_directory_on_read(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    keyed = root / "svc"
    keyed.mkdir()
    keyed.chmod(0o777)
    _write(keyed / "tok", "t", mode=0o400)
    with pytest.raises(SecretDenied, match="keyed directory is group or world"):
        FileSecretStore(root).get(SecretRef(name="svc", key="tok"))


@posix_only
def test_file_store_refuses_entries_of_another_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _secret_dir(tmp_path)
    _write(root / "openai", "tok")
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(root).st_uid + 1)
    with pytest.raises(SecretDenied, match="not owned by this user"):
        FileSecretStore(root).get(SecretRef("openai"))


def test_file_store_without_a_directory_reads_every_secret_as_missing(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "never-created")
    with pytest.raises(SecretMissing) as caught:
        store.get(SecretRef("openai"))
    message = str(caught.value)
    assert "openai" in message
    assert "mas-ctl secrets init" in message
    assert str(tmp_path) not in message
    info = store.describe(SecretRef("openai"))
    assert (info.present, info.reason) == (False, "directory missing")
    assert store.change_token(SecretRef("openai")) is None


def test_file_store_on_windows_needs_the_loose_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.library.standard.plugins.secrets import file_store

    root = _secret_dir(tmp_path)
    _write(root / "openai", "tok")
    monkeypatch.setattr(file_store.sys, "platform", "win32")
    with pytest.raises(SecretDenied, match="Windows"):
        FileSecretStore(root).get(SecretRef("openai"))
    assert FileSecretStore(root, allow_loose_permissions=True).get(SecretRef("openai")).reveal() == "tok"


@posix_only
@pytest.mark.parametrize("case", ["name", "key"])
def test_file_store_refuses_a_symlink(tmp_path: Path, case: str) -> None:
    root = _secret_dir(tmp_path)
    _write(tmp_path / "victim", CANARY)
    if case == "name":
        (root / "openai").symlink_to(tmp_path / "victim")
        ref = SecretRef("openai")
    else:
        (root / "svc").mkdir(mode=0o700)
        (root / "svc" / "tok").symlink_to(tmp_path / "victim")
        ref = SecretRef(name="svc", key="tok")
    store = FileSecretStore(root)
    with pytest.raises(SecretDenied, match="is a symlink") as caught:
        store.get(ref)
    assert str(tmp_path) not in str(caught.value) and CANARY not in str(caught.value)
    assert store.describe(ref).reason == "is a symlink"
    assert store.change_token(ref) is None


def test_env_store_reports_an_unset_ref_as_unchanged_and_a_set_one_as_new_each_time() -> None:
    store = EnvSecretStore({"MAS_SECRET_OPENAI": "tok"})
    assert store.change_token(SecretRef("kit")) is None
    assert store.change_token(SecretRef("openai")) != store.change_token(SecretRef("openai"))
    assert store.describe(SecretRef("kit")).present is False
    assert store.describe(SecretRef("openai")).size == 3


@pytest.mark.skipif(_WIN, reason="POSIX modes and symlinks")
def test_file_store_refuses_an_unsafe_layout(tmp_path: Path) -> None:
    root = _secret_dir(tmp_path)
    store = FileSecretStore(root)
    assert store.change_token(SecretRef("none")) is None
    _write(root / "real", "tok")
    (root / "link").symlink_to(root / "real")
    with pytest.raises(SecretDenied, match="symlink"):
        store.get(SecretRef("link"))
    (root / "keyed").mkdir(mode=0o700)
    _write(root / "keyed" / "bearer", "tok")
    assert store.get(SecretRef(name="keyed", key="bearer")).reveal() == "tok"
    with pytest.raises(SecretMissing):
        store.get(SecretRef(name="real", key="other"))
    with pytest.raises(SecretMissing, match="store directory does not exist"):
        FileSecretStore(tmp_path / "absent").get(SecretRef("real"))
    root.chmod(0o755)
    with pytest.raises(SecretDenied, match="group or world"):
        store.get(SecretRef("real"))
