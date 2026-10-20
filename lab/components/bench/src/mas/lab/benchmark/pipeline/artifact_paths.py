#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Where a pipeline step may write: the framework decides, steps never build paths.

Rules
-----
* The artifact location is decided by the framework from the level the step
  runs at (experiment / scenario / item / run).  A step never receives or
  builds an absolute path.
* Default file names are fixed by step type (``extract_trace_stats`` writes
  ``trace_stats.csv``).
* A step config may set at most a **relative** ``subdir`` (nested folders
  allowed) and, to avoid a collision between two steps of the same type, a
  simple ``filename`` / ``name`` (no path separators).  ``output`` may carry
  both as one relative path (``results/trace_stats.csv``).
* Absolute paths, ``..`` segments, ``~``, drive letters and symlink escapes are
  rejected when the pipeline is loaded / validated and again when a step
  resolves its location.
* The single-brace ``{output_dir}`` prefix is a deprecated alias: it is
  normalised to the relative form and warns once.  ``{{run.output_dir}}``-style
  values were never supported and are rejected.

Public API: :func:`resolve_step_artifact_path`, :func:`resolve_step_input_path`,
:func:`validate_step_config_paths`, :func:`validate_step_dicts`.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "StepPathError",
    "check_simple_name",
    "confine_under",
    "normalize_relative_value",
    "resolve_step_artifact_path",
    "resolve_step_folder",
    "resolve_step_input_path",
    "resolve_step_named_file",
    "resolve_step_root",
    "step_artifact_relpath",
    "step_config_label",
    "validate_step_config_paths",
    "validate_step_dicts",
]

#: Config keys that name an artifact location: a relative path (subdir + name).
OUTPUT_PATH_KEYS = frozenset({
    "output",
    "output_runs",
    "output_summary",
    "output_provenance",
    "output_calls",
    "summary_out",
})

#: Config keys that name a file *inside* the run folder that an earlier step
#: produced (relative path under the level root).
INPUT_PATH_KEYS = frozenset({
    "trace_stats",
    "mealy_stats",
    "trace_baseline",
    "trace_guardrail",
    "results_csv",
    "summary_csv",
    "runs_csv",
    "runs_dir",
    "input_dir",
    "csv_file",
    "data",
    "results_dir",
    "csv",
})

#: Config keys holding a bare file name (no separators).
FILENAME_KEYS = frozenset({
    "filename",
    "output_filename",
    "report_filename",
    "stem",
    "artifact_name",
    "output_file",
    "spans_filename",
    "candidate_filename",
})

#: Config keys holding a relative folder.
SUBDIR_KEYS = frozenset({"subdir"})

#: Step types whose config lists input files (``key`` -> list of relative paths / globs).
LIST_PATH_KEYS_BY_TYPE = {"join_dataframe": frozenset({"inputs"})}

#: Keys the framework injects into step configs (absolute); never user-set.
FRAMEWORK_KEYS = frozenset({"level_dir", "scenario_dir", "run_dir"})

#: Config sub-trees that hold free-form data, not step settings.
_OPAQUE_KEYS = frozenset({
    "mapping", "labels", "df_schema", "rename_values", "factor_levels", "columns",
    "filter", "transform", "identity", "dataset", "metrics", "env", "headers", "body",
})

_LEGACY_PREFIX = "{output_dir}"
_DRIVE = re.compile(r"^[A-Za-z]:")
_DOUBLE_BRACE = re.compile(r"\{\{.*?\}\}")
_LEVELS = ("experiment", "scenario", "item", "run")

_ALLOWED_PATH = (
    "a relative path under the step's output folder (for example "
    "'results/trace_stats.csv'), or omit the key to use the default name"
)
_ALLOWED_NAME = "a simple file name with no path separators (for example 'trace_stats_2.csv')"
_ALLOWED_SUBDIR = "a relative folder such as 'results' or 'results/figures'"


class StepPathError(ValueError):
    """A step config value would let a step write (or read) outside its level."""


def step_config_label(step: Any) -> str:
    """Human label for errors: ``name (type typ)`` when both differ."""
    if isinstance(step, str):
        return step
    if isinstance(step, dict):
        name = str(step.get("name") or "")
        typ = str(step.get("type") or "")
    else:
        name = str(getattr(step, "name", "") or "")
        typ = str(getattr(step, "type", "") or "")
    if name and typ and name != typ:
        return f"{name} (type {typ})"
    return name or typ or "?"


def _fail(step: Any, key: str, value: Any, why: str, allowed: str) -> StepPathError:
    return StepPathError(
        f"step '{step_config_label(step)}': config key '{key}' = {value!r} {why}. "
        f"Allowed: {allowed}"
    )


def _is_simple_name(value: str) -> bool:
    return not (
        "/" in value
        or "\\" in value
        or value in (".", "..")
        or value.startswith("~")
        or bool(_DRIVE.match(value))
    )


def _check_segments(step: Any, key: str, value: str, allowed: str) -> None:
    if "\x00" in value:
        raise _fail(step, key, value, "contains a NUL byte", allowed)
    if value.startswith("~"):
        raise _fail(step, key, value, "starts with '~' (home directory)", allowed)
    if value.startswith(("/", "\\")):
        raise _fail(step, key, value, "is an absolute path", allowed)
    if _DRIVE.match(value):
        raise _fail(step, key, value, "starts with a drive letter", allowed)
    for seg in re.split(r"[\\/]", value):
        if seg == "..":
            raise _fail(step, key, value, "contains a '..' segment", allowed)


def normalize_relative_value(
    step: Any,
    key: str,
    value: Any,
    *,
    allowed: str = _ALLOWED_PATH,
    warn: bool = True,
) -> Any:
    """Return *value* with the legacy ``{output_dir}`` prefix removed.

    Raises :class:`StepPathError` for anything that is not a confined relative
    path.  Non-string values are returned unchanged (``@step`` references too).
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text.startswith("@"):
        return value
    if _DOUBLE_BRACE.search(text):
        raise _fail(
            step,
            key,
            value,
            "uses a double-brace template, which was never supported",
            allowed,
        )
    if text.startswith(_LEGACY_PREFIX):
        rest = text[len(_LEGACY_PREFIX):]
        if rest and rest[0] not in "/\\":
            raise _fail(step, key, value, "mixes '{output_dir}' into a path segment", allowed)
        if warn:
            from mas.lab.deprecations import warn_deprecated

            warn_deprecated(
                "pipeline.output_dir_template",
                where=f"step '{step_config_label(step)}' config.{key}",
            )
        text = rest.lstrip("/\\") or "."
    if "{output_dir}" in text:
        raise _fail(
            step, key, value, "uses '{output_dir}' other than as a leading prefix", allowed
        )
    _check_segments(step, key, text, allowed)
    return text


def _check_filename(step: Any, key: str, value: Any) -> None:
    if not isinstance(value, str) or not value or value.startswith("@"):
        return
    if _DOUBLE_BRACE.search(value):
        raise _fail(step, key, value, "uses a double-brace template", _ALLOWED_NAME)
    if not _is_simple_name(value):
        raise _fail(step, key, value, "is not a simple file name", _ALLOWED_NAME)


def _simple_component(step: Any, key: str, value: Any) -> str:
    text = str(value or "")
    if text and not _is_simple_name(text):
        raise _fail(step, key, value, "is not a simple folder name", "a name without separators")
    return text


def _subdir_from_config(step: Any, config: Dict[str, Any], default: str) -> str:
    raw = config.get("subdir")
    if isinstance(raw, str) and raw.strip():
        return normalize_relative_value(step, "subdir", raw, allowed=_ALLOWED_SUBDIR, warn=False)
    return default


def validate_step_config_paths(
    step: Any,
    config: Optional[Dict[str, Any]],
    *,
    framework_injected: bool = False,
    warn: bool = True,
    step_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate (and normalise) the path-like keys of one step config.

    Returns a new config dict; raises :class:`StepPathError`.  With
    ``framework_injected=True`` the framework's own absolute keys
    (``output_dir`` ...) are left alone.
    """
    cfg = dict(config or {})
    list_keys = LIST_PATH_KEYS_BY_TYPE.get(step_type or "", frozenset())
    for key in list(cfg):
        value = cfg[key]
        if key in list_keys and isinstance(value, list):
            cfg[key] = [normalize_relative_value(step, key, v, warn=warn) for v in value]
            continue
        if isinstance(value, (dict, list)) and key not in _OPAQUE_KEYS:
            cfg[key] = _validate_nested(
                step, value, framework_injected=framework_injected, warn=warn
            )
            continue
        if key in OUTPUT_PATH_KEYS or key in INPUT_PATH_KEYS:
            cfg[key] = normalize_relative_value(step, key, value, warn=warn)
        elif key in SUBDIR_KEYS:
            cfg[key] = normalize_relative_value(step, key, value, allowed=_ALLOWED_SUBDIR, warn=warn)
        elif key in FILENAME_KEYS:
            _check_filename(step, key, value)
        elif key == "output_dir" and not framework_injected:
            norm = normalize_relative_value(step, key, value, allowed=_ALLOWED_SUBDIR, warn=warn)
            if norm in ("", "."):
                cfg.pop(key)  # the level folder is the default
            else:
                raise _fail(
                    step,
                    key,
                    value,
                    "is chosen by the framework from the level the step runs at",
                    "no 'output_dir' (use 'subdir' for a nested folder)",
                )
        elif key in FRAMEWORK_KEYS and not framework_injected:
            raise _fail(
                step, key, value, "is set by the framework", "no such key in a pipeline file"
            )
    return cfg


def _validate_nested(step: Any, value: Any, *, framework_injected: bool, warn: bool) -> Any:
    if isinstance(value, dict):
        return validate_step_config_paths(
            step, value, framework_injected=True, warn=warn
        )
    if isinstance(value, list):
        return [
            _validate_nested(step, v, framework_injected=framework_injected, warn=warn)
            if isinstance(v, (dict, list))
            else v
            for v in value
        ]
    return value


def validate_step_dicts(
    steps: Iterable[Any], *, warn: bool = True, framework_injected: bool = False
) -> List[Dict[str, Any]]:
    """Validate raw step dicts (``{name, type, config}``); return normalised copies."""
    out: List[Dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        step = dict(raw)
        if isinstance(step.get("config"), dict):
            step["config"] = validate_step_config_paths(
                step_config_label(step),
                step["config"],
                warn=warn,
                framework_injected=framework_injected,
                step_type=str(step.get("type") or ""),
            )
        out.append(step)
    return out


def resolve_step_root(
    ctx: Any,
    config: Optional[Dict[str, Any]] = None,
    *,
    level: Optional[str] = None,
    step: Any = "",
) -> Path:
    """The folder of the level the step runs at (framework-decided).

    ``experiment`` -> ``<output_dir>``; ``scenario`` -> ``.../<scenario>``;
    ``item`` -> ``.../<scenario>/<item>``; ``run`` -> ``.../<item>/<run>``.
    When *level* is omitted it is inferred from the scenario / test / run the
    framework put in the config.
    """
    cfg = config or {}
    # Only the framework-filled config names the level: ``ctx.scope_context`` is
    # shared by every step of a pipeline and may still hold a previous step's.
    scenario = _simple_component(step, "scenario", cfg.get("scenario"))
    test = _simple_component(step, "test", cfg.get("test"))
    run = _simple_component(step, "run", cfg.get("run"))
    if level is not None:
        if level == "test":
            level = "item"
        if level not in _LEVELS:
            raise ValueError(f"unknown level {level!r}; expected one of {_LEVELS}")
        if level == "experiment":
            scenario = test = run = ""
        elif level == "scenario":
            test = run = ""
        elif level == "item":
            run = ""
    parts = [scenario] if scenario else []
    if parts and test:
        parts.append(test)
        if run:
            parts.append(run)
    return Path(ctx.output_dir).joinpath(*parts)


def _confine(step: Any, key: str, root: Path, rel: str, value: Any) -> Path:
    target = (root / rel) if rel not in ("", ".") else root
    try:
        resolved = target.resolve()
        resolved.relative_to(root.resolve())
    except ValueError:
        raise _fail(
            step,
            key,
            value,
            f"resolves outside the output folder {str(root)!r} (symlink or template escape)",
            _ALLOWED_PATH,
        ) from None
    return target


def step_artifact_relpath(
    step: Any,
    default_name: str,
    config: Optional[Dict[str, Any]] = None,
    *,
    key: str = "output",
    default_subdir: str = "",
    name_keys: Iterable[str] = ("filename", "name"),
) -> str:
    """Relative (validated) location of a step artifact below its level folder.

    Precedence: ``config[key]`` (a relative path) > ``config['subdir']`` +
    ``config['filename'|'name']`` > ``default_subdir`` + *default_name*.
    """
    cfg = config or {}
    raw = cfg.get(key)
    if isinstance(raw, str) and raw.strip():
        rel = normalize_relative_value(step, key, raw, warn=False)
        # a file name is required: "." / "results/" would name the folder itself
        if rel in (".", "") or rel.endswith(("/", "\\")):
            raise _fail(step, key, raw, "names a folder, not a file", _ALLOWED_PATH)
        return rel
    subdir = _subdir_from_config(step, cfg, default_subdir)
    name = default_name
    for name_key in name_keys:
        cand = cfg.get(name_key)
        if isinstance(cand, str) and cand.strip():
            _check_filename(step, name_key, cand)
            name = cand
            break
    return str(PurePosixPath(subdir) / name) if subdir and subdir != "." else name


def confine_under(
    step: Any, key: str, root: Path, rel: str, *, mkdir: bool = False
) -> Path:
    """``root / rel`` if it stays under *root* once symlinks are resolved."""
    path = _confine(step, key, Path(root), rel, rel)
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def resolve_step_artifact_path(
    ctx: Any,
    level: Optional[str],
    step: Any,
    default_name: str,
    config: Optional[Dict[str, Any]] = None,
    *,
    key: str = "output",
    default_subdir: str = "",
    name_keys: Iterable[str] = ("filename", "name"),
    mkdir: bool = False,
) -> Path:
    """Absolute path of the artifact a step writes, confined to its level folder.

    Pass ``mkdir=True`` to create the parent folder.  The default leaves the
    tree untouched so callers can inspect the path without writing.
    """
    cfg = config or {}
    root = resolve_step_root(ctx, cfg, level=level, step=step)
    rel = step_artifact_relpath(
        step, default_name, cfg, key=key, default_subdir=default_subdir, name_keys=name_keys
    )
    return confine_under(step, key, root, rel, mkdir=mkdir)


def resolve_step_folder(
    ctx: Any,
    step: Any,
    config: Optional[Dict[str, Any]] = None,
    default_subdir: str = "",
    *,
    level: Optional[str] = None,
    mkdir: bool = False,
) -> Path:
    """Folder a step writes several artifacts into (``subdir`` or the default)."""
    cfg = config or {}
    root = resolve_step_root(ctx, cfg, level=level, step=step)
    path = confine_under(step, "subdir", root, _subdir_from_config(step, cfg, default_subdir))
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def check_simple_name(step: Any, key: str, value: Any) -> str:
    """Return *value* if it is a bare file name; raise :class:`StepPathError`."""
    _check_filename(step, key, value)
    return str(value)


def resolve_step_named_file(
    ctx: Any,
    step: Any,
    config: Optional[Dict[str, Any]],
    default_name: str,
    *,
    default_subdir: str = "",
    suffix: str = "",
    mkdir: bool = True,
    level: Optional[str] = None,
) -> Path:
    """Folder from ``subdir`` plus a simple ``filename`` (optional suffix)."""
    cfg = config or {}
    folder = resolve_step_folder(
        ctx, step, cfg, default_subdir, level=level, mkdir=mkdir
    )
    name = check_simple_name(step, "filename", cfg.get("filename", default_name))
    return folder / f"{name}{suffix}"


def resolve_step_input_path(
    ctx: Any,
    step: Any,
    config: Optional[Dict[str, Any]],
    key: str,
    default: Optional[str] = None,
    *,
    level: Optional[str] = None,
) -> Optional[Path]:
    """Path of a file an earlier step produced, relative to the level folder.

    Returns ``None`` when neither the key nor *default* is set.
    """
    cfg = config or {}
    raw = cfg.get(key)
    if not (isinstance(raw, str) and raw.strip()):
        raw = default
    if raw is None:
        return None
    root = resolve_step_root(ctx, cfg, level=level, step=step)
    rel = normalize_relative_value(step, key, raw)
    return confine_under(step, key, root, rel)


# ---------------------------------------------------------------------------
# Manifest-level check (``mas-lab validate``)
# ---------------------------------------------------------------------------


def _iter_manifest_steps(node: Any, trail: str = ""):
    """Yield ``(where, step_dict)`` for every ``{type, config}`` step found in a list."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_manifest_steps(value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            if (
                isinstance(item, dict)
                and isinstance(item.get("type"), str)
                and isinstance(item.get("config"), dict)
            ):
                yield f"{trail}[{idx}]", item
            else:
                yield from _iter_manifest_steps(item, f"{trail}[{idx}]")


def _legacy_keys(config: Dict[str, Any]) -> List[str]:
    keys = OUTPUT_PATH_KEYS | INPUT_PATH_KEYS | SUBDIR_KEYS | {"output_dir"}
    return [
        k
        for k, v in config.items()
        if k in keys and isinstance(v, str) and v.strip().startswith(_LEGACY_PREFIX)
    ]


def collect_manifest_path_issues(payload: Any) -> "tuple[List[str], List[str]]":
    """Check every step config in an experiment / pipeline manifest.

    Returns ``(violations, deprecations)``; the first list holds actionable
    errors naming the step, the key and the allowed form.
    """
    violations: List[str] = []
    deprecations: List[str] = []
    for where, step in _iter_manifest_steps(payload):
        label = step_config_label(step)
        try:
            validate_step_config_paths(
                label, step["config"], warn=False, step_type=str(step["type"])
            )
        except StepPathError as exc:
            violations.append(f"{where}: {exc}")
            continue
        for key in _legacy_keys(step["config"]):
            deprecations.append(
                f"{where}: step '{label}' config.{key} uses the '{{output_dir}}' prefix; "
                "use a relative path or omit the key"
            )
    return violations, deprecations
