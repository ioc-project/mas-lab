#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Record tests/fixtures/llm-cache/ci.llm-cache.json from a live provider.

Runs the same CI workloads tests replay (lab-smoke, golden capture,
cache-rerun, tutorial HITL chat), then recaptures golden events under
strict replay. Does not run skip-gated pytest — an empty fixture would
never fill that way.

Usage (repo root, live provider key set):

    python scripts/record_ci_llm_cache.py

``MAS_RECORD_PROVIDER`` overrides the inner LLM bundle (default: user
``~/.config/mas/infra/llm-proxy.yaml`` if present, else ``standard:openai``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WRITE = REPO / "tests" / "fixtures" / "llm-cache" / "ci-write.yaml"
REPLAY = REPO / "tests" / "fixtures" / "llm-cache" / "ci-replay.yaml"
CACHE = REPO / "tests" / "fixtures" / "llm-cache" / "ci.llm-cache.json"
T01 = REPO / "docs" / "tutorials" / "01-building-an-agent"
SAMPLE_WS = REPO / "library-samples" / "sample-workspace"
USER_PROXY = Path.home() / ".config" / "mas" / "infra" / "llm-proxy.yaml"

_MANIFESTS = (
    REPO / "tests" / "fixtures" / "lab-smoke" / "labs.yaml",
    REPO / "tests" / "fixtures" / "golden-runs" / "labs.yaml",
    REPO / "tests" / "fixtures" / "cache-rerun" / "labs.yaml",
)


def _load_dotenv(root: Path) -> None:
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export").strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val


def _provider() -> str:
    env = os.environ.get("MAS_RECORD_PROVIDER", "").strip()
    if env:
        return env
    if USER_PROXY.is_file():
        return str(USER_PROXY.resolve())
    return "standard:openai"


def _experiments() -> list[tuple[str, Path]]:
    seen: set[Path] = set()
    out: list[tuple[str, Path]] = []
    for manifest in _MANIFESTS:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        for entry in data.get("labs") or []:
            label = str(entry["label"])
            exp = (REPO / entry["experiment"]).resolve()
            if exp in seen:
                continue
            seen.add(exp)
            out.append((f"{manifest.parent.name}/{label}", exp))
    return out


def _isolate(tmp: Path, *, refs: str) -> dict[str, str]:
    env = os.environ.copy()
    xdg = tmp / "xdg-config"
    (xdg / "mas").mkdir(parents=True, exist_ok=True)
    (tmp / "trace-cache").mkdir(exist_ok=True)
    (tmp / "mas-home").mkdir(exist_ok=True)
    env["MAS_HOME"] = str(tmp / "mas-home")
    env["MAS_TRACE_CACHE"] = str(tmp / "trace-cache")
    env["MAS_LLM_CACHE"] = str(tmp / "engine-llm_cache.json")
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["XDG_DATA_HOME"] = str(tmp / "xdg-data")
    env["XDG_CACHE_HOME"] = str(tmp / "xdg-cache")
    env["XDG_STATE_HOME"] = str(tmp / "xdg-state")
    env["MAS_WORKSPACE_ROOT"] = str(SAMPLE_WS)
    env["MAS_INFRA_REFS"] = refs
    env["MAS_MCE_OFFLINE"] = "1"
    env["MAS_WEB_SEARCH_CACHE"] = str(CACHE.parent / "web-search")
    return env


def _run_experiment(experiment: Path, out: Path, traces: Path, env: dict[str, str]) -> int:
    traces.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from mas.lab.benchmark.worker import run_benchmark_sync; "
                "ok = run_benchmark_sync(Path(sys.argv[1]), force=True, single_run=True, "
                "max_runs=1, output_dir=Path(sys.argv[2]), trace_cache_dir=Path(sys.argv[3])); "
                "sys.exit(0 if ok else 1)"
            ),
            str(experiment),
            str(out),
            str(traces),
        ],
        cwd=REPO,
        env=env,
    ).returncode


def _event_kinds(path: Path) -> list[str]:
    kinds: list[str] = []
    if not path.is_file():
        return kinds
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        kinds.append(json.loads(line).get("kind", ""))
    return kinds


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--append",
        action="store_true",
        help="Keep existing ci.llm-cache.json entries (default: wipe then record).",
    )
    parser.add_argument(
        "--hitl-only",
        action="store_true",
        help="Record tutorial HITL chat only; implies --append and skips benches.",
    )
    parser.add_argument(
        "--skip-goldens",
        action="store_true",
        help="Do not recapture golden events after recording.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_dotenv(REPO)
    provider = _provider()
    write_refs = f"{provider},{WRITE.resolve()}"
    replay_refs = f"standard:openai,{REPLAY.resolve()}"
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("OPENAI_API_KEY is unset — cannot record a live fixture", file=sys.stderr)
        return 1

    append = args.append or args.hitl_only
    print(f"Recording with provider={provider} append={append}", flush=True)
    if not append:
        CACHE.write_text("{}\n", encoding="utf-8")

    experiments = [] if args.hitl_only else _experiments()
    with tempfile.TemporaryDirectory(prefix="mas-ci-llm-record-") as raw:
        tmp = Path(raw)
        for label, experiment in experiments:
            if not experiment.is_file():
                print(f"missing experiment {label}: {experiment}", file=sys.stderr)
                return 1
            print(f"  live {label}", flush=True)
            slug = label.replace("/", "_")
            exp_tmp = tmp / slug
            exp_tmp.mkdir()
            env = _isolate(exp_tmp, refs=write_refs)
            out = exp_tmp / "out"
            traces = exp_tmp / "traces"
            rc = 1
            for attempt in range(1, 3):
                if attempt > 1:
                    print(f"  retry {label} ({attempt}/2)", flush=True)
                rc = _run_experiment(experiment, out, traces, env)
                if rc == 0:
                    break
            if rc != 0:
                print(f"benchmark failed while recording {label}", file=sys.stderr)
                return rc

        events_file = tmp / "hitl-events.jsonl"
        mas_ctl = Path(sys.executable).parent / "mas-ctl"
        print("  live tutorial HITL chat", flush=True)
        chat_env = _isolate(tmp / "hitl", refs=write_refs)
        chat = subprocess.run(
            [
                str(mas_ctl),
                "chat",
                "agent.yaml",
                "-q",
                "Who is POTUS",
                "--infra-ref",
                provider,
                "--infra-ref",
                str(WRITE.resolve()),
                "-o",
                "overlays/tools.yaml",
                "--control",
                "controls/governance-hitl.yaml",
                "--events",
                "--events-file",
                str(events_file),
            ],
            cwd=T01,
            env=chat_env,
        )
        if chat.returncode != 0:
            return chat.returncode
        if "hitl_gate" not in _event_kinds(events_file):
            print(
                "recorded HITL chat did not emit hitl_gate — the model must call a tool for CLI HITL replay",
                file=sys.stderr,
            )
            return 1

    data = json.loads(CACHE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        print("ci.llm-cache.json is still empty after recording", file=sys.stderr)
        return 1
    print(f"recorded {len(data)} llm_cache entries → {CACHE}", flush=True)

    if args.skip_goldens:
        return 0

    print("Recapturing golden events under replay", flush=True)
    capture = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "capture_golden_run.py"), "--labs", "all"],
        cwd=REPO,
        env={**os.environ, "MAS_INFRA_REFS": replay_refs, "MAS_MCE_OFFLINE": "1"},
    )
    return capture.returncode


if __name__ == "__main__":
    raise SystemExit(main())
