#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from mas.ctl.session.bootstrap import InstantiationOptions, resolve_archive_dir
from mas.ctl.workspace.config import WorkspaceConfig


def test_archive_colocates_next_to_events_file(tmp_path: Path) -> None:
    events = tmp_path / "traces" / "events.jsonl"
    ws = WorkspaceConfig({"mas_ctl": {"archive": {"enabled": True}}})
    options = InstantiationOptions(workspace=ws, events_file=str(events))
    assert resolve_archive_dir(options) == str(events.parent / "archive")


def test_archive_off_by_default_even_with_events(tmp_path: Path) -> None:
    events = tmp_path / "traces" / "events.jsonl"
    options = InstantiationOptions(events_file=str(events))
    assert resolve_archive_dir(options) == ""


def test_bench_style_events_file_does_not_persist(tmp_path: Path) -> None:
    """mas-lab benchmark always sets events_file; that must not write an archive."""
    events = tmp_path / "output" / "traces" / "events.jsonl"
    options = InstantiationOptions(
        workspace=WorkspaceConfig({}),
        events_file=str(events),
        app_root=tmp_path,
    )
    assert resolve_archive_dir(options) == ""


def test_archive_disabled_returns_empty(tmp_path: Path) -> None:
    ws = WorkspaceConfig({"mas_ctl": {"archive": {"enabled": False}}})
    options = InstantiationOptions(
        workspace=ws,
        events_file=str(tmp_path / "traces" / "events.jsonl"),
    )
    assert resolve_archive_dir(options) == ""


def test_checkpoint_dir_alone_does_not_create_archive(tmp_path: Path) -> None:
    options = InstantiationOptions(checkpoint_dir=tmp_path / "checkpoints")
    assert resolve_archive_dir(options) == ""


def test_explicit_archive_dir_does_not_enable_persist(tmp_path: Path) -> None:
    ws = WorkspaceConfig({"paths": {"archive_dir": "my-archive"}})
    options = InstantiationOptions(workspace=ws, app_root=tmp_path)
    assert resolve_archive_dir(options) == ""


def test_explicit_archive_dir_used_only_when_enabled(tmp_path: Path) -> None:
    ws = WorkspaceConfig(
        {
            "mas_ctl": {"archive": {"enabled": True}},
            "paths": {"archive_dir": "my-archive"},
        }
    )
    options = InstantiationOptions(workspace=ws, app_root=tmp_path)
    assert resolve_archive_dir(options) == str(tmp_path / "my-archive")


def test_explicit_archive_dir_honors_enabled_false(tmp_path: Path) -> None:
    ws = WorkspaceConfig(
        {
            "mas_ctl": {"archive": {"enabled": False}},
            "paths": {"archive_dir": "my-archive"},
        }
    )
    options = InstantiationOptions(workspace=ws, app_root=tmp_path)
    assert resolve_archive_dir(options) == ""
