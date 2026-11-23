#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""No child of a lab process reads a host secret, and a declared secret reaches only its own child."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mas.runtime import hostenv
from mas.runtime.secrets import SecretRef, SecretRefError
from secret_fakes import bound_secrets

CANARY = "sk-CANARY-7f3a"


def _python(code: str, env: dict[str, str]) -> dict:
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _host(tmp_path: Path, **extra: str) -> dict[str, str]:
    import os

    return {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "OPENAI_API_KEY": CANARY,
        "MAS_LLM_RPM": "30",
        **extra,
    }


# --------------------------------------------------------------------------- the entry point


def test_entry_point_scrubs_the_process_so_inheriting_spawns_see_no_secret(tmp_path: Path) -> None:
    out_mp = tmp_path / "mp.txt"
    out_sp = tmp_path / "sp.txt"
    code = f"""
import json, multiprocessing, os, subprocess
from mas.runtime.entrypoint import install_host_environment
from mas.runtime import hostenv
install_host_environment()
ctx = multiprocessing.get_context("spawn")
p = ctx.Process(target=os.system, args=('printf %s "$OPENAI_API_KEY" > {out_mp}',))
p.start(); p.join(60)
subprocess.run(["/bin/sh", "-c", 'printf %s "$OPENAI_API_KEY" > {out_sp}'])
print(json.dumps({{"host": hostenv.get("OPENAI_API_KEY"), "rpm": os.environ.get("MAS_LLM_RPM"),
                  "env": os.environ.get("OPENAI_API_KEY")}}))
"""
    seen = _python(code, _host(tmp_path))
    assert out_mp.read_text() == ""
    assert out_sp.read_text() == ""
    assert seen == {"host": None, "rpm": "30", "env": None}  # hostenv keeps declared keys only


# --------------------------------------------------------------------------- spawn run workers


def test_spawn_worker_drops_what_it_inherited_and_uses_the_parents_store(tmp_path: Path) -> None:
    """A worker of an unscrubbed parent: the inherited canary is gone; the store is the parent's."""
    from mas.ctl.secrets import write_file_secret
    from mas.lab.secret_host import SecretHostSettings

    store = tmp_path / "store"
    write_file_secret(store, "openai", "from-the-parents-store")
    payload = SecretHostSettings(store="file", directory=str(store)).to_payload()
    child = tmp_path / "child.txt"
    code = f"""
import json, os, subprocess
from mas.lab.benchmark.schedule.run_batch.workers import prepare_worker_host
from mas.runtime.secrets import SecretRef, current_resolver
prepare_worker_host({{"PATH": os.environ["PATH"], "MAS_LLM_RPM": "30"}}, {payload!r})
subprocess.run(["/bin/sh", "-c", 'printf %s "$OPENAI_API_KEY" > {child}'])
r = current_resolver()
print(json.dumps({{"env": os.environ.get("OPENAI_API_KEY"), "dir": str(r.store.directory),
                  "value": r.reveal(SecretRef(name="openai"))}}))
"""
    seen = _python(code, _host(tmp_path))
    assert seen == {"env": None, "dir": str(store), "value": "from-the-parents-store"}
    assert child.read_text() == ""


# --------------------------------------------------------------------------- services and fixtures


def _bundle(tmp_path: Path, env_yaml: str, command: str) -> Path:
    path = tmp_path / "services.yaml"
    path.write_text(
        "spec:\n  services:\n    svc:\n      backend: process\n"
        f"      command: [{sys.executable!r}, -c, {command!r}]\n"
        f"      env: {env_yaml}\n",
        encoding="utf-8",
    )
    return path


def test_service_secret_reaches_its_child_only_and_no_log(tmp_path: Path, caplog) -> None:
    from mas.lab.benchmark.service_manager import ServiceManager

    seen = tmp_path / "seen.json"
    code = (
        f"import json,os,time; json.dump({{'t': os.environ.get('TOKEN'), 'k': os.environ.get('OPENAI_API_KEY')}},"
        f" open({str(seen)!r}, 'w')); time.sleep(30)"
    )
    mgr = ServiceManager(
        _bundle(tmp_path, "{TOKEN: {secret: svc-token}, PLAIN: p}", code), {"output_dir": str(tmp_path)}
    )
    caplog.set_level(logging.DEBUG)
    with bound_secrets({"svc-token": CANARY}):
        assert mgr.start("svc")
        try:
            for _ in range(200):
                if seen.exists() and seen.read_text():
                    break
                time.sleep(0.02)
            assert json.loads(seen.read_text()) == {"t": CANARY, "k": None}
            assert hostenv.get("TOKEN") is None  # never exported to the run
            assert hostenv.get("PLAIN") == "p"
        finally:
            mgr.stop("svc")
    assert CANARY not in caplog.text
    assert isinstance(mgr._services["svc"].env["TOKEN"], SecretRef)


def test_fixture_env_reveals_a_secret_into_that_fixture_only(caplog) -> None:
    from mas.lab.benchmark.environment.fixtures import _fixture_env
    from mas.lab.benchmark.environment.model import compile_environment

    spec = compile_environment(
        {"fixtures": [{"name": "f", "command": ["true"], "env": {"TOKEN": {"secret": "fx"}, "MODE": "a"}}]}
    ).fixtures[0]
    assert isinstance(spec.env["TOKEN"], SecretRef)
    caplog.set_level(logging.DEBUG)
    with bound_secrets({"fx": CANARY}):
        env = _fixture_env(spec, {"PATH": "/bin", "OPENAI_API_KEY": "host"})
    assert env["TOKEN"] == CANARY and env["MODE"] == "a"
    assert "OPENAI_API_KEY" not in env
    # Not exported to the process (the overlay every later child inherits), not logged.
    assert hostenv.get("TOKEN") is None and "TOKEN" not in hostenv.overlay_snapshot()
    assert CANARY not in repr(hostenv.snapshot())
    assert CANARY not in caplog.text


_LITERAL_SECRETS = (
    ("HTTPS_PROXY", "http://user:pw@proxy:3128"),
    ("OPENAI_API_KEY", "sk-literal"),
    ("UV_INDEX", "https://token@index/simple"),
    ("TOKEN", "env:OPENAI_API_KEY"),
)


@pytest.mark.parametrize(("name", "value"), _LITERAL_SECRETS)
def test_literal_or_credential_env_is_refused_before_anything_starts(tmp_path: Path, name: str, value: str) -> None:
    from mas.lab.benchmark.environment.model import compile_environment
    from mas.lab.benchmark.service_manager import ServiceManager

    marker = tmp_path / "started"
    with pytest.raises(SecretRefError) as info:
        ServiceManager(_bundle(tmp_path, f"{{{name}: {value!r}}}", f"open({str(marker)!r}, 'w')"))
    assert value not in str(info.value) and not marker.exists()
    with pytest.raises(SecretRefError):
        compile_environment({"fixtures": [{"name": "f", "command": ["true"], "env": {name: value}}]})
    assert hostenv.exported(name) is None


# --------------------------------------------------------------------------- the entry point's refusals and pins


def test_imports_after_the_scrub_do_not_load_a_dotenv_file(tmp_path: Path) -> None:
    pytest.importorskip("litellm")
    pytest.importorskip("deepeval")
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={CANARY}\nDOTENV_CANARY={CANARY}\n", encoding="utf-8")
    code = """
import json, os
from mas.runtime.entrypoint import install_host_environment
install_host_environment()
import litellm, deepeval
from dotenv import load_dotenv
load_dotenv()
print(json.dumps({"leaked": sorted(k for k, v in os.environ.items() if "CANARY" in v)}))
"""
    host = _host(tmp_path)
    del host["OPENAI_API_KEY"]
    out = subprocess.run(
        [sys.executable, "-c", code], env=host, cwd=tmp_path, capture_output=True, text=True, timeout=300
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"leaked": []}


def test_a_pinned_switch_is_not_also_kept_from_the_host() -> None:
    from mas.runtime.entrypoint import THIRD_PARTY_PINS
    from mas.runtime.envkeys import DECLARED_KEYS

    assert not DECLARED_KEYS & set(THIRD_PARTY_PINS)
