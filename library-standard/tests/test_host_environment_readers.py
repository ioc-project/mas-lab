#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plugins that need environment values read them from the host environment, never ``os.environ``."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest
from mas.library.standard.lib import tls
from mas.library.standard.plugins.related_state.git_worktree import GitWorktreeRelatedState
from mas.library.standard.plugins.sandbox.workdir import WorkdirSandbox
from mas.runtime import hostenv


def test_ca_bundle_comes_from_the_host_environment(tmp_path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("pem", encoding="utf-8")
    with hostenv.overlay({"SSL_CERT_FILE": str(bundle)}):
        assert tls._default_ca_bundle() == str(bundle)


def test_workdir_sandbox_exports_its_root_only_through_the_overlay(tmp_path) -> None:
    sandbox = WorkdirSandbox(tmp_path / "root")
    before = os.getcwd()

    def probe() -> tuple[str, str | None]:
        return os.getcwd(), hostenv.get("MAS_EXECUTE_ROOT")

    async def aprobe() -> tuple[str, str | None]:
        return probe()

    expected = (os.path.realpath(sandbox.root), str(sandbox.root))
    for got in (sandbox.run(probe), asyncio.run(sandbox.arun(aprobe))):
        assert (os.path.realpath(got[0]), got[1]) == expected
    assert os.getcwd() == before
    assert hostenv.get("MAS_EXECUTE_ROOT") is None
    assert "MAS_EXECUTE_ROOT" not in os.environ


@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
def test_git_capture_uses_the_host_environment(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    with hostenv.overlay({"PATH": os.environ["PATH"]}):
        ref = GitWorktreeRelatedState(tmp_path).capture("s1")
    assert ref.adapter != "content-hash" and ref.fingerprint
