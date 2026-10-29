#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.cli import export_import
from mas.lab.benchmark.cli.export_import import (
    _collect_source_bundle,
    _iter_allowed_source_files,
    _manifest_source_entries,
    _require_hf_token,
    import_hf_command,
    publish_hf_command,
)


def test_collect_source_bundle_uses_archive_relative_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    app_dir = workspace / "apps" / "demo"
    app_dir.mkdir(parents=True)
    (app_dir / "manifest.yaml").write_text("name: demo\n", encoding="utf-8")

    exp_dir = workspace / "experiments" / "demo"
    exp_dir.mkdir(parents=True)
    exp_yaml = exp_dir / "experiment.yaml"
    exp_yaml.write_text("manifest: ../../apps/demo/manifest.yaml\n", encoding="utf-8")

    bundle = _collect_source_bundle(exp_yaml)
    manifest_entries = _manifest_source_entries(bundle["entries"])
    archive_paths = [entry["archive_path"] for entry in manifest_entries]

    assert all(not p.startswith("source/../") for p in archive_paths)
    assert all("original_path" not in entry for entry in manifest_entries)
    assert all(path.startswith("source/") for path in archive_paths)
    assert all(".." not in path for path in archive_paths)


def test_collect_source_bundle_skips_sensitive_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    app_dir = workspace / "apps" / "demo"
    app_dir.mkdir(parents=True)
    (app_dir / "manifest.yaml").write_text("name: demo\n", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (workspace / "__pycache__").mkdir(parents=True)
    (workspace / "__pycache__" / "cached.pyc").write_bytes(b"cached")
    (workspace / "certs").mkdir(parents=True)
    (workspace / "certs" / "key.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\n", encoding="utf-8"
    )

    exp_dir = workspace / "experiments" / "demo"
    exp_dir.mkdir(parents=True)
    exp_yaml = exp_dir / "experiment.yaml"
    exp_yaml.write_text("manifest: ../../apps/demo/manifest.yaml\n", encoding="utf-8")

    bundle = _collect_source_bundle(exp_yaml)
    archive_paths = [
        entry["archive_path"] for entry in _manifest_source_entries(bundle["entries"])
    ]

    assert not any(path.endswith(".env") for path in archive_paths)
    assert not any("__pycache__" in path for path in archive_paths)
    assert not any(path.endswith("key.pem") for path in archive_paths)


def test_iter_allowed_source_files_skips_nested_sensitive_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    app_dir = workspace / "apps" / "demo"
    app_dir.mkdir(parents=True)
    allowed_file = app_dir / "config.yaml"
    allowed_file.write_text("name: demo\n", encoding="utf-8")
    (app_dir / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    blocked_dir = app_dir / "__pycache__"
    blocked_dir.mkdir(parents=True)
    (blocked_dir / "key.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\n", encoding="utf-8"
    )
    (blocked_dir / "config.yaml").write_text("secret: true\n", encoding="utf-8")

    files = _iter_allowed_source_files(app_dir, workspace_root=workspace)
    assert allowed_file.resolve() in files
    assert all(path.name != ".env" for path in files)
    assert all(not path.name.endswith(".pem") for path in files)
    assert (blocked_dir / "config.yaml").resolve() not in files


def test_import_rejects_escape_in_manifest_path(tmp_path: Path) -> None:
    out_dir = tmp_path / "import-root"
    tarball = tmp_path / "bundle.tar.gz"
    manifest = {
        "benchmark_id": "demo",
        "short_id": "demo",
        "experiment_name": "demo",
        "experiment_yaml_path": "/tmp/evil.yaml",
        "experiment_yaml_archive_path": "../../../../tmp/evil.yaml",
        "run_dir_basename": "results",
        "has_trace_cache": False,
        "total_runs": 0,
        "trace_cache_entry_names": [],
        "source_entries": [],
    }

    payload = json.dumps(manifest, indent=2).encode("utf-8")
    with tarfile.open(tarball, "w:gz") as tf:
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(payload)
        tf.addfile(info, fileobj=__import__("io").BytesIO(payload))

    rc = export_import.import_command(
        SimpleNamespace(
            tarball=tarball,
            output_dir=str(out_dir),
            trace_cache_dir=None,
            dry_run=False,
        )
    )

    assert rc == 1


def test_publish_hf_defaults_private_review_branch(monkeypatch, tmp_path: Path) -> None:
    tarball = tmp_path / "bundle.tar.gz"
    tarball.write_bytes(b"archive")

    class FakeCommitInfo:
        def __init__(self, oid: str) -> None:
            self.oid = oid

    observed = {}

    class FakeHfApi:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def create_repo(self, **kwargs):
            observed["repo"] = kwargs

        def upload_file(self, **kwargs):
            observed["upload"] = kwargs
            return FakeCommitInfo("abc123def456")

    monkeypatch.setattr(
        export_import,
        "_load_manifest_from_tarball",
        lambda _: {"short_id": "run-1", "experiment_name": "demo"},
    )
    monkeypatch.setattr(export_import, "_require_hf_token", lambda token=None: "hf-token")
    monkeypatch.setattr("huggingface_hub.HfApi", FakeHfApi)

    rc = publish_hf_command(
        SimpleNamespace(
            tarball=tarball,
            repo_id="org/dataset",
            path_in_repo=None,
            visibility="private",
            revision="main",
            create_pr=True,
        )
    )

    assert rc == 0
    assert observed["repo"]["private"] is True
    assert observed["repo"]["visibility"] == "private"
    assert observed["upload"]["create_pr"] is True
    assert observed["upload"]["revision"] == "main"


def test_import_hf_pins_revision_and_verifies_sha256(
    monkeypatch, tmp_path: Path
) -> None:
    tarball = tmp_path / "archive.tar.gz"
    payload = b"benchmark bundle"
    tarball.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    captured = {}

    def fake_hf_hub_download(
        *, repo_id, repo_type, filename, token, revision, local_dir
    ):
        captured["revision"] = revision
        return str(tarball)

    monkeypatch.setattr(export_import, "_require_hf_token", lambda token=None: "hf-token")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_hf_hub_download)

    def fake_import_command(args):
        captured["imported"] = args
        return 0

    monkeypatch.setattr(export_import, "import_command", fake_import_command)

    rc = import_hf_command(
        SimpleNamespace(
            repo_id="org/dataset",
            path_in_repo="benchmarks/demo.tar.gz",
            revision="abc123",
            sha256=expected,
            output_dir=None,
            trace_cache_dir=None,
            dry_run=False,
        )
    )

    assert rc == 0
    assert captured["revision"] == "abc123"
    assert captured["imported"].tarball == tarball

    rc_bad = import_hf_command(
        SimpleNamespace(
            repo_id="org/dataset",
            path_in_repo="benchmarks/demo.tar.gz",
            revision="abc123",
            sha256="0" * 64,
            output_dir=None,
            trace_cache_dir=None,
            dry_run=False,
        )
    )

    assert rc_bad == 1


def test_hf_token_comes_from_the_caller_not_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "from-env")
    with pytest.raises(ValueError, match="HF_TOKEN"):
        _require_hf_token(None)
    with pytest.raises(ValueError, match="HF_TOKEN"):
        _require_hf_token("  ")
    assert _require_hf_token("cli-token") == "cli-token"


def test_import_hf_dry_run_does_not_require_a_token(monkeypatch, tmp_path: Path) -> None:
    tarball = tmp_path / "archive.tar.gz"
    tarball.write_bytes(b"bundle")
    captured = {}

    def fake_hf_hub_download(**kwargs):
        captured.update(kwargs)
        return str(tarball)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_hf_hub_download)
    monkeypatch.setattr(export_import, "import_command", lambda args: 0)

    rc = import_hf_command(
        SimpleNamespace(
            repo_id="org/dataset",
            path_in_repo="benchmarks/demo.tar.gz",
            revision=None,
            sha256=None,
            output_dir=None,
            trace_cache_dir=None,
            dry_run=True,
            token=None,
        )
    )
    assert rc == 0
    assert captured["token"] is None
