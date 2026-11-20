#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Existence-based manifest reference checks and composed MAS validation.

Lab experiment envelopes extend :func:`mas.ctl.validate.refs.iter_ref_paths` with
``app:``, ``dataset:``, ``library:``, and ``overlay_id:`` scheme ids.

Composed MAS validation is implemented once in :mod:`composed_manifest` (not in
``load_stacked_config`` or ``MASSpecValidator``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from mas.ctl.validate.refs import iter_ref_paths
from mas.runtime.package_refs import resolve_library_scheme_root, resolve_path_ref

logger = logging.getLogger(__name__)

_BACKWARD = re.compile(r"(?:^|/)\.\.(?:/|$)")


def backward_traversal_warning(ref: str, *, field: str, source: str) -> str | None:
    if _BACKWARD.search(ref.replace("\\", "/")):
        return f"{source}: {field} uses backward traversal {ref!r}"
    return None


def _iter_lab_scheme_refs(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Lab-only reference ids (app/dataset/library/overlay_id) not covered by ctl walk."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if k == "app" and isinstance(v, str) and v.strip():
                found.append((p, f"app:{v.strip()}"))
            elif k == "name" and isinstance(v, str) and v.strip():
                locator = obj.get("locator")
                if locator is not None or prefix.endswith("dataset"):
                    loc = f":{locator}" if locator else ""
                    found.append((p, f"dataset:{v.strip()}{loc}"))
            elif k == "libraries" and isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, str) and item.strip():
                        found.append((f"{p}[{i}]", f"library:{item.strip()}"))
            found.extend(_iter_lab_scheme_refs(v, p))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and prefix.endswith("overlays") and item.strip():
                found.append((f"{prefix}[{i}]", f"overlay_id:{item.strip()}"))
            found.extend(_iter_lab_scheme_refs(item, f"{prefix}[{i}]"))
    return found


def iter_manifest_refs(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Yield ``(json_path, ref_string)`` for filesystem refs and lab scheme ids."""
    return iter_ref_paths(obj, prefix) + _iter_lab_scheme_refs(obj, prefix) + _iter_library_name_path_refs(obj, prefix)


def _is_library_name_path(value: str) -> bool:
    """True for ``name:path`` library refs (not ``pkg://`` or lab scheme ids)."""
    if value.startswith(("/", "\\", "pkg://", "app:", "dataset:", "library:", "overlay_id:")):
        return False
    scheme, sep, rest = value.partition(":")
    return bool(sep and scheme and rest and "/" not in scheme and "\\" not in scheme)


def _iter_library_name_path_refs(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Filesystem-ref keys whose value is a ``name:path`` library ref."""
    from mas.ctl.validate.refs import REF_KEYS

    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if k in REF_KEYS and isinstance(v, str) and _is_library_name_path(v.strip()):
                found.append((p, v.strip()))
            found.extend(_iter_library_name_path_refs(v, p))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_iter_library_name_path_refs(item, f"{prefix}[{i}]"))
    return found


def _resolve_ref_target(base_dir: Path, ref: str) -> Path:
    if ref.startswith("/"):
        return Path(ref)
    try:
        return resolve_path_ref(ref, base_dir)
    except LookupError:
        raise
    except Exception:
        return (base_dir / ref).resolve()


def _resolve_special_ref(ref: str, base_dir: Path) -> Path | None:
    if ref.startswith("app:"):
        from mas.apps import get_app

        root = get_app(ref[4:])
        if root is None:
            return None
        mas_yaml = root / "mas.yaml"
        return mas_yaml if mas_yaml.is_file() else root

    if ref.startswith("dataset:"):
        body = ref[len("dataset:") :]
        if ":" in body and not body.endswith(":"):
            name, _, locator = body.partition(":")
        else:
            name, locator = body, None
        try:
            from mas.lab.benchmark.experiment import _resolve_dataset_by_name

            return _resolve_dataset_by_name(base_dir, name, locator=locator or None)
        except FileNotFoundError:
            return None

    if ref.startswith("library:"):
        scheme = ref[len("library:") :]
        root = resolve_library_scheme_root(scheme, base_dir)
        if root is not None:
            return root
        candidate = (base_dir / scheme).resolve()
        return candidate if candidate.is_dir() else None

    return None


def check_recursive_refs(
    data: dict[str, Any],
    base_dir: Path,
    *,
    source: str,
    overlay_context: dict[str, Any] | None = None,
) -> list[str]:
    """Resolve every reference in *data*; return violation messages."""
    violations: list[str] = []
    ctx = overlay_context or {}

    for field_path, ref in iter_manifest_refs(data):
        w = backward_traversal_warning(ref, field=field_path, source=source)
        if w:
            logger.warning("[manifest] %s", w)

        if ref.startswith("overlay_id:"):
            ov_id = ref[len("overlay_id:") :]
            configs_dir = ctx.get("configs_dir")
            if configs_dir is None:
                continue
            ov_file = Path(configs_dir) / f"{ov_id}.yaml"
            if not ov_file.is_file():
                violations.append(f"{field_path} overlay id {ov_id!r} — file not found: {ov_file}")
            continue

        special = _resolve_special_ref(ref, base_dir)
        if special is not None:
            if not special.exists():
                violations.append(f"{field_path} = {ref!r} — not found: {special}")
            continue
        if ref.startswith(("app:", "dataset:", "library:")):
            violations.append(f"{field_path} = {ref!r} — unresolved identifier")
            continue

        target = _resolve_ref_target(base_dir, ref)
        if field_path.endswith("configs_dir") or (".plugins[" in field_path and field_path.endswith(".path")):
            ok = target.is_dir()
        else:
            ok = target.is_file() or target.is_dir()
        if not ok:
            violations.append(f"{field_path} = {ref!r} — not found: {target}")

    return violations


def _experiment_mas_context(payload: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    from mas.lab.lab.config import MASSpec

    mas_block = payload.get("mas") or {}
    if not isinstance(mas_block, dict):
        return {}
    try:
        spec = MASSpec.from_dict(mas_block, base_dir)
    except Exception:
        return {}
    configs = mas_block.get("configs_dir")
    if configs:
        configs_dir = resolve_path_ref(str(configs), base_dir)
    else:
        configs_dir = spec.effective_configs_dir or base_dir / "overlays"
    mas_yaml = spec.manifest
    return {"configs_dir": configs_dir, "mas_yaml": mas_yaml}


def manifest_to_run(manifest: Path) -> Path:
    """The application manifest that runs for the file named, or raise.

    The file named is the file that runs; a file with no recognised kind is refused
    (see :func:`mas.ctl.benchmark.runner_dispatch.mas_manifest_path`).
    """
    from mas.ctl.benchmark.runner_dispatch import mas_manifest_path
    from mas.runtime.spec.source import load_yaml_mapping

    try:
        document = load_yaml_mapping(manifest)
    except yaml.YAMLError as exc:
        raise ValueError(f"{manifest}: cannot be read as YAML: {exc}") from exc
    resolved = mas_manifest_path(document, manifest)
    return manifest if resolved is None else resolved


def experiment_manifest_to_run(data: dict[str, Any], base_dir: Path) -> Path | None:
    """Manifest path an experiment will run, for ``validate`` output; ``None`` when none is named."""
    from mas.lab.lab.config import MASSpec

    payload = data.get("experiment", data)
    mas_block = payload.get("mas") if isinstance(payload, dict) else None
    if not isinstance(mas_block, dict):
        return None
    spec = MASSpec.from_dict(mas_block, base_dir)
    if spec.manifest is None or not spec.manifest.is_file():
        return None
    return manifest_to_run(spec.manifest)


def check_composed_mas(payload: dict[str, Any], base_dir: Path, *, source: str) -> list[str]:
    """Compose MAS per scenario and validate the resolved kind:MAS tree."""
    from mas.lab.lab.config import MASSpec, OverlayStack
    from mas.lab.manifests.composed_manifest import validate_composed_scenario

    mas_block = payload.get("mas")
    if not isinstance(mas_block, dict):
        return []

    try:
        mas_spec = MASSpec.from_dict(mas_block, base_dir)
    except Exception as exc:
        return [f"mas: cannot resolve MAS pointer — {exc}"]

    if mas_spec.manifest is None or not mas_spec.manifest.is_file():
        return [f"mas: manifest not found: {mas_spec.manifest}"]
    try:
        manifest_to_run(mas_spec.manifest)
    except ValueError as exc:
        return [f"mas: {exc}"]

    configs_dir = mas_spec.configs_dir
    if configs_dir is None:
        configs_dir = mas_spec.effective_configs_dir
    overlays_dir = configs_dir if configs_dir and configs_dir.is_dir() else None

    violations: list[str] = []
    scenarios = payload.get("scenarios") or [{"id": mas_spec.base_scenario}]

    for i, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            continue
        sid = scenario.get("id") or mas_spec.base_scenario
        raw_ov = scenario.get("overlays")
        if isinstance(raw_ov, dict):
            stack = OverlayStack.from_dict(raw_ov, scenario_id=str(sid))
            overlay_ids = stack.flattened()
        elif isinstance(raw_ov, list):
            overlay_ids = raw_ov
        else:
            overlay_ids = [sid]

        label = f"scenarios[{i}] ({sid!r})"
        violations.extend(
            validate_composed_scenario(
                mas_spec.manifest,
                overlay_ids,
                overlays_dir=overlays_dir,
                base_dir=base_dir,
                label=label,
            )
        )

    return violations


def check_lab_manifest_refs(
    payload: dict[str, Any],
    base_dir: Path,
    *,
    source: str,
    kind: str,
) -> list[str]:
    """Full reference + composition checks for a lab manifest payload."""
    overlay_ctx = _experiment_mas_context(payload, base_dir) if kind == "experiment" else {}
    violations = check_recursive_refs(payload, base_dir, source=source, overlay_context=overlay_ctx)
    if kind == "experiment":
        violations.extend(check_composed_mas(payload, base_dir, source=source))
    return violations
