#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-ctl`` keeps the ``MAS_LLM_*`` values of a ``.env`` file; the file's secrets stay out."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_PROBE = """
import importlib, json, os, sys
m = importlib.import_module("mas.ctl.cli.main")
from mas.runtime import hostenv
from mas.runtime.engine import llm_cache, llm_scheduler

@m.app.command("probe")
def probe():
    print(json.dumps({"overrides": llm_scheduler.explicit_overrides(), "cache": llm_cache._PATHS.get("cli"),
                      "key": hostenv.get("OPENAI_API_KEY"), "env": os.environ.get("OPENAI_API_KEY")}))

sys.argv = ["mas-ctl", "probe"]
try:
    m.main()
except SystemExit:
    pass
"""


def test_dotenv_scheduler_values_survive_the_host_install(tmp_path: Path) -> None:
    cache = tmp_path / "cache.db"
    (tmp_path / ".env").write_text(
        f"MAS_LLM_RPM=7\nMAS_LLM_CACHE={cache}\nOPENAI_API_KEY=sk-CANARY-dotenv\n", encoding="utf-8"
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "MAS_HOME": str(tmp_path / "mas"),
    }
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip().splitlines()[-1])
    assert seen == {"overrides": {"requests_per_minute": 7.0}, "cache": str(cache), "key": None, "env": None}
