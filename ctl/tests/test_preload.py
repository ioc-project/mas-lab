#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The run path is imported once, on one thread, before run threads start."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(code: str) -> str:
    out = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=240
    )
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip().splitlines()[-1]


_THREADS = """
    import importlib, threading
    {before}
    names = {names}
    errors = []
    barrier = threading.Barrier(len(names))

    def go(n):
        barrier.wait()
        try:
            importlib.import_module(n)
        except BaseException as exc:
            errors.append(f"{{n}}: {{type(exc).__name__}}: {{exc}}")

    ts = [threading.Thread(target=go, args=(n,)) for n in names]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    print(errors)
"""

_CTL = [
    "mas.ctl.compose.runner",
    "mas.ctl.session.bootstrap",
    "mas.ctl.benchmark.runner",
    "mas.ctl.adapters.obs.factory",
    "mas.ctl.overlay.canvas",
    "mas.ctl.cli.obs_flags",
]
_OBSERVE = [
    "ioa_observe.sdk.tracing.runtime_events",
    "ioa_observe.sdk.utils.const",
    "mas.library.telemetry.conversion.converter",
    "mas.library.telemetry.conversion.semconv",
]


def test_ctl_modules_import_concurrently_after_the_run_path_preload():
    code = _THREADS.format(
        before="from mas.ctl.preload import preload_run_path; preload_run_path()", names=_CTL + _OBSERVE
    )
    for _ in range(2):
        assert _run(code) == "[]"


def test_preload_run_path_is_a_noop_the_second_time():
    code = """
        from mas.ctl.preload import preload_run_path
        preload_run_path()
        print(preload_run_path())
    """
    assert _run(code) == "[]"
