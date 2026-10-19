#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve run artefacts from experiment/scenario/test/run (or session) ids."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.lab.controller.ui_stream.refs import DataSourceRef


@dataclass
class RunRecord:
    ref: DataSourceRef
    events: Path | None = None
    artefacts: dict[str, Path] = field(default_factory=dict)


def _read_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def find_experiment_file(lib_dir: Path, experiment: str | None) -> Path | None:
    if not experiment:
        for name in ("experiment.yaml", "experiments/experiment.yaml"):
            candidate = lib_dir / name
            if candidate.is_file():
                return candidate
        return None
    candidates = [
        lib_dir / experiment,
        lib_dir / "experiments" / experiment,
    ]
    if not experiment.endswith((".yaml", ".yml")):
        candidates.extend(
            [
                lib_dir / f"{experiment}.yaml",
                lib_dir / "experiments" / f"{experiment}.yaml",
            ]
        )
    for path in candidates:
        if path.is_file():
            return path
    extra = list(lib_dir.glob("*.yaml"))
    experiments_dir = lib_dir / "experiments"
    if experiments_dir.is_dir():
        extra.extend(experiments_dir.glob("*.yaml"))
    for path in extra:
        doc = _read_yaml(path)
        name = str((doc.get("experiment") or {}).get("name") or doc.get("name") or path.stem)
        if name == experiment or path.stem == experiment:
            return path
    return None


def _experiment_application(lib_dir: Path, experiment: str | None) -> dict[str, Any]:
    path = find_experiment_file(lib_dir, experiment)
    if path is None:
        return {}
    doc = _read_yaml(path)
    exp = doc.get("experiment") if isinstance(doc.get("experiment"), dict) else doc
    app = (exp or {}).get("application")
    if isinstance(app, dict):
        return app
    apps = (exp or {}).get("applications") or []
    if apps and isinstance(apps[0], dict):
        return apps[0]
    return {}


def infer_mas_rel(lib_dir: Path, experiment: str | None) -> str | None:
    binding = _experiment_application(lib_dir, experiment)
    manifest = str(binding.get("manifest") or "").strip()
    if manifest:
        return manifest.lstrip("./")
    for rel in ("mas/scenario-1.mas.yaml", "mas.yaml"):
        if (lib_dir / rel).is_file():
            return rel
    return None


def resolve_mas_files(lib_dir: Path, experiment: str | None) -> tuple[Path, Path] | None:
    """Return ``(mas.yaml, MAS directory)`` for a catalog app or a relative manifest."""
    binding = _experiment_application(lib_dir, experiment)
    app_ref = str(binding.get("app") or "").strip()
    if app_ref:
        try:
            from mas.apps import get_app, resolve_app_manifest

            manifest = resolve_app_manifest(get_app(app_ref), app_id=app_ref)
        except Exception:
            return None
        return manifest, manifest.parent
    rel = infer_mas_rel(lib_dir, experiment)
    if not rel:
        return None
    manifest = Path(rel) if Path(rel).is_absolute() else lib_dir / rel
    if not manifest.is_file():
        return None
    return manifest, manifest.parent


def overlay_rel(lib_dir: Path, overlay: str | None) -> Path | None:
    if not overlay:
        return None
    name = overlay if overlay.endswith((".yaml", ".yml")) else f"{overlay}.yaml"
    for folder in (lib_dir / "mas" / "overlays", lib_dir / "overlays", lib_dir / "mas" / "configs"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    skip = {".cache", "versions", "node_modules", ".git"}
    matches = [path for path in lib_dir.rglob(name) if not skip.intersection(path.parts)]
    return matches[0] if matches else None


def _item_dir_name(test: str | None) -> str:
    raw = str(test or "1").strip() or "1"
    if raw.startswith("item"):
        return raw
    return f"item{raw}"


def _run_dir_name(run: str | None) -> str:
    raw = str(run or "1").strip() or "1"
    if raw.startswith("r") and raw[1:].isdigit():
        return raw
    if raw.isdigit():
        return f"r{raw}"
    return raw


def _is_run_index(run: str | None) -> bool:
    raw = str(run or "").strip()
    if not raw:
        return False
    if raw.isdigit():
        return True
    return raw.startswith("r") and raw[1:].isdigit()


def _newest_scenario_run(
    root: Path,
    scenario: str,
    item: str | None,
    *,
    glob_ok: bool,
    experiment: str | None,
) -> Path | None:
    item_pat = item or "item*"
    patterns = [f"{scenario}/{item_pat}/r*"]
    if experiment:
        patterns.append(f"{experiment}/{scenario}/{item_pat}/r*")
    if glob_ok:
        patterns.append(f"**/{scenario}/{item_pat}/r*")
    found: list[Path] = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_dir() and path.name.startswith("r") and path.name[1:].isdigit():
                found.append(path)
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def _standard_run_dir(lib_dir: Path, ref: DataSourceRef) -> Path | None:
    scenario = ref.scenario
    if not scenario:
        return None
    item = _item_dir_name(ref.test) if ref.test else None
    run = _run_dir_name(ref.run) if _is_run_index(ref.run) else None
    local_roots: list[Path] = []
    for name in ("output", "outputs", "runs"):
        candidate = lib_dir / name
        if candidate.is_dir():
            local_roots.append(candidate)
    extra_roots: list[Path] = []
    try:
        from mas.lab.paths import data_dir, labs_root

        extra_roots.append(data_dir())
        extra_roots.append(labs_root())
    except Exception:
        pass

    def _match(root: Path, *, glob_ok: bool) -> Path | None:
        if run is not None:
            item_name = item or _item_dir_name(None)
            direct = root / scenario / item_name / run
            if direct.is_dir():
                return direct
            if ref.experiment:
                nested = root / ref.experiment / scenario / item_name / run
                if nested.is_dir():
                    return nested
            if glob_ok:
                matches = list(root.glob(f"**/{scenario}/{item_name}/{run}"))
                if matches:
                    return matches[0]
            return None
        if not glob_ok:
            return None
        return _newest_scenario_run(
            root, scenario, item, glob_ok=True, experiment=ref.experiment
        )

    for root in local_roots:
        found = _match(root, glob_ok=True)
        if found is not None:
            return found
    for root in extra_roots:
        found = _match(root, glob_ok=False)
        if found is not None:
            return found
    return None


def _events_in_run_dir(run_dir: Path) -> Path | None:
    try:
        from mas.lab.benchmark.cache.trace_store import resolve_run_events_path

        found = resolve_run_events_path(run_dir)
        if found is not None:
            return found
    except Exception:
        pass
    inline = run_dir / "traces" / "events.jsonl"
    return inline if inline.is_file() else None


def _gold_events(lib_dir: Path, scenario: str) -> Path | None:
    cache = lib_dir / "infra" / ".cache"
    if not cache.is_dir():
        return None
    exact = list(cache.glob(f"**/{scenario}.events.jsonl"))
    if exact:
        return sorted(exact)[-1]
    return None


def _gold_metrics(lib_dir: Path, scenario: str) -> Path | None:
    cache = lib_dir / "infra" / ".cache"
    if not cache.is_dir():
        return None
    exact = list(cache.glob(f"**/{scenario}.metrics.json"))
    if exact:
        return sorted(exact)[-1]
    return None


def _gold_progress(lib_dir: Path, scenario: str) -> Path | None:
    cache = lib_dir / "infra" / ".cache"
    if not cache.is_dir():
        return None
    exact = list(cache.glob(f"**/{scenario}.progress.jsonl"))
    if exact:
        return sorted(exact)[-1]
    return None


def _gold_custom_owners(lib_dir: Path, scenario: str) -> dict[str, Path]:
    """Gold-cache siblings for every ``custom.{owner}`` trace actually present,
    e.g. ``infra/.cache/{scenario}.custom.topology.jsonl``, next to the
    existing ``{scenario}.events.jsonl`` / ``{scenario}.metrics.json``
    convention. Discovered by glob, not a hardcoded owner list."""
    cache = lib_dir / "infra" / ".cache"
    if not cache.is_dir():
        return {}
    found: dict[str, Path] = {}
    prefix, suffix = f"{scenario}.custom.", ".jsonl"
    for path in cache.glob(f"**/{scenario}.custom.*.jsonl"):
        owner = path.name[len(prefix) : -len(suffix)]
        if owner and (owner not in found or path.stat().st_mtime > found[owner].stat().st_mtime):
            found[owner] = path
    return found


def resolve_run(lib_dir: Path, ref: DataSourceRef, *, indexed: dict[str, RunRecord] | None = None) -> RunRecord:
    if indexed:
        for key in (ref.run or "", ref.session or "", ref.coordinate()):
            if key and key in indexed:
                return indexed[key]
    run_dir = _standard_run_dir(lib_dir, ref)
    events = _events_in_run_dir(run_dir) if run_dir else None
    if events is None and ref.scenario:
        events = _gold_events(lib_dir, ref.scenario)
    artefacts: dict[str, Path] = {}
    if events:
        artefacts["events"] = events
    if run_dir:
        for key, name in (
            ("metrics", "metrics.json"),
            ("progress", "progress.jsonl"),
            ("progress_latest", "progress.json"),
            ("protocol", "protocol.json"),
            ("report", "session_report.yaml"),
            ("dataframe", "data.csv"),
        ):
            path = run_dir / name
            if path.is_file():
                artefacts[key] = path
        if "report" not in artefacts:
            json_report = run_dir / "session_report.json"
            if json_report.is_file():
                artefacts["report"] = json_report
        # custom.{owner}.jsonl siblings (file_tail.append_custom_trace) --
        # discovered by glob so any owner is picked up with no code change.
        for path in run_dir.glob("custom.*.jsonl"):
            owner = path.stem.split(".", 1)[1] if path.stem.count(".") >= 1 else ""
            if owner:
                artefacts[f"custom_{owner}"] = path
    if "metrics" not in artefacts and events is not None:
        sibling = events.with_name(events.name.replace(".events.jsonl", ".metrics.json"))
        if sibling.is_file():
            artefacts["metrics"] = sibling
    if "progress" not in artefacts and events is not None:
        sibling = events.with_name(events.name.replace(".events.jsonl", ".progress.jsonl"))
        if sibling.is_file():
            artefacts["progress"] = sibling
    if "metrics" not in artefacts and ref.scenario:
        gold = _gold_metrics(lib_dir, ref.scenario)
        if gold is not None:
            artefacts["metrics"] = gold
    if "progress" not in artefacts and ref.scenario:
        gold = _gold_progress(lib_dir, ref.scenario)
        if gold is not None:
            artefacts["progress"] = gold
    if ref.scenario:
        for owner, path in _gold_custom_owners(lib_dir, ref.scenario).items():
            artefacts.setdefault(f"custom_{owner}", path)
    return RunRecord(ref=ref, events=events, artefacts=artefacts)
