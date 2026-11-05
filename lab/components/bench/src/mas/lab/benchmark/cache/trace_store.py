#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Content-addressed trace cache — run hashing and cache entry I/O."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def trace_cache_roots(explicit: Optional[Path] = None) -> list[Path]:
    """Return trace-cache directories to probe (canonical only)."""
    from mas.lab import paths as _paths

    canonical = _paths.trace_cache(explicit=str(explicit)) if explicit else _paths.trace_cache()
    return [canonical.expanduser().resolve()]


def resolve_run_events_path(run_dir: Path) -> Optional[Path]:
    """Resolve ``events.jsonl`` for a benchmark run directory.

    Supports inline traces, ``.run_ref`` indirection into the trace cache, and
    legacy broken symlinks that still encode the content hash in their target.
    """
    inline = run_dir / "traces" / "events.jsonl"
    if inline.is_file():
        return inline

    run_hash = ""
    run_ref = run_dir / ".run_ref"
    if run_ref.is_file():
        run_hash = run_ref.read_text(encoding="utf-8").strip()

    traces_link = run_dir / "traces"
    if not run_hash and traces_link.is_symlink():
        target = traces_link.readlink()
        parts = target.parts
        for idx, part in enumerate(parts):
            if part == "trace-cache" and idx + 1 < len(parts):
                run_hash = parts[idx + 1]
                break

    if run_hash:
        for root in trace_cache_roots():
            cached = root / run_hash / "traces" / "events.jsonl"
            if cached.is_file():
                return cached

    return None


def get_trace_cache_dir(explicit: Optional[Path] = None) -> Path:
    """Global content-addressed trace store.

    Pass *explicit* = ``cli_value or yaml_value`` to get the full
    ``CLI > YAML > env > default`` chain.
    """
    from mas.lab import paths as _paths

    return _paths.trace_cache(explicit=explicit)


def extract_flavour_info(flavour: Optional[Any]) -> dict:
    """Return all deterministic (non-secret) FlavourManifest fields for cache keying.

    Generic scrub — whatever fields a FlavourManifest happens to carry get
    captured (minus the exclusions below), so this doesn't need updating when
    the Flavour schema grows or shrinks a section.

    Post-FT4 (docs/design/flavour-boundary.md), a Flavour is deployment
    posture only: agent_comm (protocol, mode, emulation), tools
    (remote_tools_enabled, allowed), observability/control (plugin
    selection), config (free-form deployment config). llm, skills,
    and prefer_local no longer live on the Flavour — they're on the agent
    spec / workspace infra instead, and already flow into the cache key via
    ``manifest`` (the materialized per-agent config passed into
    :func:`compute_run_hash`), so removing them from the flavour side doesn't
    lose any determinism: it removes a redundant second copy of the same
    signal, not the signal itself.

    Deliberately excluded:
      api_key_env, embed_api_key_env  — secrets
      _available                      — ProviderSpec cached probe result (runtime state)
      _raw                            — FlavourManifest forward-compat store
      name, description               — organisational, not computational
      telemetry                       — logging backend doesn't affect LLM output
      bind                            — which infra rows calls ride: deployment, not logic
    """
    if flavour is None:
        return {}

    _SECRETS = frozenset({"api_key_env", "embed_api_key_env"})
    _SKIP = frozenset({"_available", "_raw", "name", "description", "telemetry", "bind"})

    def _scrub(obj: Any) -> Any:
        import dataclasses as _dc

        if _dc.is_dataclass(obj) and not isinstance(obj, type):
            return {
                f.name: _scrub(getattr(obj, f.name))
                for f in _dc.fields(obj)
                if f.name not in _SECRETS
                and f.name not in _SKIP
                and not f.name.startswith("_")
            }
        if isinstance(obj, dict):
            return {
                k: _scrub(v)
                for k, v in obj.items()
                if k not in _SECRETS
                and k not in _SKIP
                and not str(k).startswith("_")
            }
        if isinstance(obj, (list, tuple)):
            return [_scrub(v) for v in obj]
        return obj

    try:
        import dataclasses as _dc

        # FlavourManifest dataclass (most common path)
        if _dc.is_dataclass(flavour) and not isinstance(flavour, type):
            return _scrub(flavour)
        # Plain dict (used in some tests / legacy callers)
        if isinstance(flavour, dict):
            return _scrub(flavour)
        # Object with .spec attribute (e.g. FlavourSpec wrapper in experiment.py)
        spec = getattr(flavour, "spec", None)
        if spec is not None:
            if _dc.is_dataclass(spec) and not isinstance(spec, type):
                return _scrub(spec)
            if isinstance(spec, dict):
                return _scrub(spec)
        # Last resort: pull llm attributes directly
        llm = getattr(flavour, "llm", None)
        if llm is None:
            return {}
        if _dc.is_dataclass(llm) and not isinstance(llm, type):
            return _scrub(llm)
        if isinstance(llm, dict):
            return {k: v for k, v in llm.items() if k not in _SECRETS}
        return {
            "model": getattr(llm, "model", "") or "",
            "api_base": getattr(llm, "api_base", "") or "",
        }
    except Exception:
        return {}


def materialize_config(config: dict, base_path: "Path | None") -> dict:
    """Return a fully-materialized copy of *config* with every external file
    reference replaced by its content — the MAS equivalent of a Kustomize /
    Helm rendered manifest.

    Hashing this object (rather than the raw config + a separate file-hash
    map) gives a single, self-contained fingerprint: if any referenced file
    changes, the hash changes automatically, without needing to enumerate
    reference types in advance.

    Transformations applied:
      - ``agents[*]._agent_dir``
            → stripped (machine-local absolute path, not part of behaviour).
      - ``agents[*].spec_tools[*]`` where entry has a ``ref`` key
            → ``{_ref: <original-ref>, _content: <parsed-YAML>}``.
      - ``agents[*].context.role`` where value is ``{ref: …}``
            → inlined as raw text on ``context.role``; ref object removed.
      - ``agents[*].skills_dir``
            → inlined as ``_skills: {"rel/path.md": text, …}`` (sorted);
              ``skills_dir`` key removed.

    All other fields pass through unchanged.  Missing files are silently
    skipped (they will produce a runtime warning during execution).
    """
    import copy

    from mas.runtime.spec.source import load_yaml_file

    def _load_yaml(p: "Path") -> "Any | None":
        try:
            return load_yaml_file(p)
        except Exception:
            return None

    def _read_text(p: "Path") -> "str | None":
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return None

    def _resolve(ref: str, anchor: "Path") -> "Path":
        p = Path(ref)
        if p.is_absolute():
            return p
        if ":" in ref:
            try:
                from mas.runtime.package_refs import resolve_path_ref

                resolved = resolve_path_ref(ref, anchor)
                if resolved.exists():
                    return resolved
            except Exception:
                pass
        return (anchor / p).resolve()

    base_dir: "Path" = (
        (base_path.parent if base_path.is_file() else base_path)
        if base_path is not None
        else Path(".")
    )

    mat = copy.deepcopy(config)

    for agent in mat.get("agents", []):
        # Strip machine-local path — not part of observable behaviour.
        agent_dir = Path(agent.pop("_agent_dir", str(base_dir)))

        # spec_tools: inline each {ref: …} entry with parsed content.
        if agent.get("spec_tools") is not None:
            inlined = []
            for entry in agent["spec_tools"]:
                if isinstance(entry, dict) and entry.get("ref"):
                    content = _load_yaml(_resolve(entry["ref"], agent_dir))
                    inlined.append(
                        {"_ref": entry["ref"], "_content": content}
                        if content is not None else entry
                    )
                else:
                    inlined.append(entry)
            agent["spec_tools"] = inlined

        # context.role {ref: …} → inline text for stable trace fingerprint.
        ctx = agent.get("context")
        if isinstance(ctx, dict):
            role = ctx.get("role")
            if isinstance(role, dict) and role.get("ref"):
                text = _read_text(_resolve(str(role["ref"]), agent_dir))
                if text is not None:
                    inlined_ctx = dict(ctx)
                    inlined_ctx["role"] = text
                    agent["context"] = inlined_ctx
            elif isinstance(role, str) and (
                role.startswith("./") or role.startswith("../")
            ):
                text = _read_text(_resolve(role, agent_dir))
                if text is not None:
                    inlined_ctx = dict(ctx)
                    inlined_ctx["role"] = text
                    agent["context"] = inlined_ctx

        # skills_dir → inline all .md files as a sorted content map.
        skills_dir_ref = agent.pop("skills_dir", None)
        if skills_dir_ref:
            sd = _resolve(skills_dir_ref, agent_dir)
            if sd.is_dir():
                agent["_skills"] = {
                    str(md.relative_to(sd)): md.read_text(encoding="utf-8", errors="replace")
                    for md in sorted(sd.rglob("*.md"))
                }

    return mat


def compute_run_hash(
    config: dict,
    run_input: dict,
    item_id: Any,
    run_idx: int,
    flavour_info: dict,
    base_path: "Path | None" = None,
    infra_info: "dict | None" = None,
) -> str:
    """Compute a 20-character SHA-256 prefix that uniquely identifies a MAS run.

    The hash is computed over a *materialized* manifest — all external file
    references (tool YAMLs, instruction files, skill files, fixtures) are
    resolved and inlined before serialization, so any change to a referenced
    file automatically invalidates the cache without having to enumerate
    reference types here.

    Inputs:
      - ``config``:       resolved MAS config dict (topology + overlays applied)
      - ``prompt``:       dataset item prompt text
      - ``item_id``:      dataset item identifier
      - ``run_idx``:      0-based repetition index
      - ``flavour_info``: full non-secret FlavourManifest dict from _extract_flavour_info
      - ``base_path``:    config file / mas.yaml path for resolving relative refs
      - ``infra_info``:   non-secret resolved infra (endpoint, model mappings, engine)
      - ``turns``:        optional multi-turn conversation list (included in hash)

    Excluded (organisational, not computational):
      - experiment name / scenario ID / benchmark ID
      - timestamps / API keys / random seeds
      - api_key_env, embed_api_key_env (secrets, stripped by _extract_flavour_info)

    Cache key semantics — effective model:
      The cache key injects the *effective* model name so the hash always
      reflects what is actually sent to the LLM.  Resolution order:
        1. flavour_info["llm"]["model"] — legacy/defensive only: post-FT4
           (docs/design/flavour-boundary.md) a Flavour no longer carries
           spec.llm at all, so this branch won't fire for any
           schema-validated flavour going forward; kept so a stale/custom
           flavour file that still has it degrades gracefully instead of
           erroring.
        2. config["agents"][0]["llm_model"] (agent manifest — the actual
           source of truth now)
      This mirrors ctl compose → RuntimeBuilder model resolution.
    """
    import hashlib
    import json

    manifest = materialize_config(config, base_path)

    # Resolve the effective model from nested flavour llm config or agent manifest.
    _llm_section = flavour_info.get("llm") if isinstance(flavour_info.get("llm"), dict) else {}
    _flavour_model = _llm_section.get("model") or ""
    if not _flavour_model:
        # Fall back to the primary agent's model from the agent manifest.
        _agents = config.get("agents", [])
        if _agents:
            _flavour_model = _agents[0].get("llm_model", "") or ""

    # Inject the resolved effective model alongside the full flavour spec so
    # the hash is unambiguous even when providers[] overrides spec.llm.model.
    _effective_flavour = {**flavour_info, "_effective_model": _flavour_model}

    payload = {
        "manifest": manifest,
        "run_input": run_input,
        "item_id": str(item_id),
        "run_idx": run_idx,
        "flavour": _effective_flavour,
        "infra": infra_info,
    }
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:20]


def link_trace_to_cache_entry(
    run_output_dir: Path,
    global_run_dir: Path,
    run_hash: str,
) -> None:
    """Create a symlink ``run_output_dir/traces → global_run_dir/traces``.

    Falls back to a ``.run_ref`` file (containing the hash) on systems where
    cross-device symlinks are not supported.
    """
    global_traces = global_run_dir / "traces"
    global_traces.mkdir(parents=True, exist_ok=True)

    traces_link = run_output_dir / "traces"
    if traces_link.is_symlink():
        if traces_link.resolve() == global_traces.resolve():
            return  # already linked correctly
        traces_link.unlink()
    elif traces_link.exists() and not traces_link.is_symlink():
        # Real directory — already populated by a non-cached run; leave it.
        (run_output_dir / ".run_ref").write_text(run_hash + "\n")
        return

    try:
        traces_link.symlink_to(global_traces)
    except OSError:
        logger.debug("symlink to trace cache failed; using .run_ref pointer", exc_info=True)
    (run_output_dir / ".run_ref").write_text(run_hash + "\n")


def _first_user_prompt_text(user: Any) -> str:
    """Extract the lead prompt string from a run-input ``inputs.user`` value.

    ``user`` is a string, a list of strings (sequential user messages), or
    absent — never the legacy ``[{role, content}]`` shape (see
    ``run_input_to_dict``).
    """
    if isinstance(user, str):
        return user
    if isinstance(user, list) and user:
        first = user[0]
        return first if isinstance(first, str) else str(first)
    return ""


def write_cache_inputs(
    global_run_dir: Path,
    run_hash: str,
    run_input: dict,
    item_id: Any,
    run_idx: int,
    flavour_info: dict,
) -> None:
    """Write the input fingerprint once to the global cache entry.

    ``run.json`` is written **once** (immutable: if it already exists it is
    not overwritten).  It describes *what inputs* produced this hash so that
    the cache entry is self-describing.  It contains NO experiment-specific
    information — the cache stores only reusable computation results.
    """
    import json

    prov_path = global_run_dir / "run.json"
    if prov_path.exists():
        return  # immutable — inputs are fixed for a given hash
    _llm = flavour_info.get("llm") if isinstance(flavour_info.get("llm"), dict) else {}
    prov = {
        "run_hash": run_hash,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "item_id": str(item_id),
        "run_idx": run_idx,
        "model": _llm.get("model", ""),
        "api_base": _llm.get("api_base", flavour_info.get("api_base", "")),
        "prompt": _first_user_prompt_text(run_input.get("inputs", {}).get("user")),
    }
    prov_path.write_text(json.dumps(prov, indent=2))
    inputs_path = global_run_dir / "inputs.json"
    if not inputs_path.exists():
        inputs_path.write_text(json.dumps(run_input, indent=2, sort_keys=True))


def write_run_result(
    global_run_dir: Path,
    status: str,
    elapsed_ms: float,
    error: str,
) -> None:
    """Write execution results to the global cache entry.

    ``result.json`` records what the run *produced* — status, wall-clock
    timing, error message.  Unlike ``run.json`` (immutable inputs), this is
    written after execution completes.

    Written once for a successful run. An ``status=error`` file is replaced
    so a lab-level reattempt or a later live execution can become the cache
    hit.
    """
    import json

    result_path = global_run_dir / "result.json"
    if result_path.exists():
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            prior = {}
        # Keep a successful cache entry. Replace an error so a later live
        # reattempt (lab retry or a subsequent `benchmark run`) can land.
        if prior.get("status") != "error":
            return
    result = {
        "status": status,
        "elapsed_ms": round(elapsed_ms, 1),
        "error": error,
        "executed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    result_path.write_text(json.dumps(result, indent=2))


def write_run_info(
    global_run_dir: Path,
    run_output_dir: Path,
    run_hash: str,
    experiment_name: str,
    scenario_id: str,
    item_id: Any,
    run_idx: int,
    *,
    app: str = "",
    app_version: str = "",
    mas_ref: str = "",
    overlay_ref: str = "",
) -> None:
    """Write benchmark reference context to the trace-cache entry.

    ``run_info.json`` is written **once** (immutable — if it already exists it
    is left untouched) to the global cache entry
    ``{trace_cache}/{run_hash}/run_info.json``, alongside
    ``traces/events.jsonl``, ``run.json`` (inputs), and ``result.json``
    (execution results).  Storing it in the content-addressed cache ensures
    the file is identical for cache-hit re-runs and live runs — it cannot
    contain stale values such as ``elapsed_ms: 0`` produced by a cache hit.

    A symlink ``run_output_dir/run_info.json → global_run_dir/run_info.json``
    is created so downstream pipeline steps reading from the experiment-local
    directory continue to work unchanged.

    Execution results (status, elapsed_ms, error) live in the cache's
    ``result.json``, written once during execution.  Input parameters
    (model, api_base, prompt) live in the cache's ``run.json``.

    App provenance fields (all keyword-only, all optional for backward compat):
      app:          MAS application name (e.g. "trip-planner-v1")
      app_version:  Version string from the MAS manifest metadata
      mas_ref:      Path to the MAS manifest that was used (relative or absolute)
      overlay_ref:  Path to the overlay YAML applied on top of the base manifest
    """
    import json

    global_run_dir.mkdir(parents=True, exist_ok=True)
    cache_path = global_run_dir / "run_info.json"
    if not cache_path.exists():
        info = {
            "run_hash": run_hash,
            "experiment": experiment_name,
            "scenario": scenario_id,
            "item_id": str(item_id),
            "run_idx": run_idx,
            "referenced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if app or app_version or mas_ref or overlay_ref:
            info["mas"] = {
                k: v for k, v in {
                    "app": app,
                    "app_version": app_version,
                    "mas_ref": mas_ref,
                    "overlay": scenario_id,
                    "overlay_ref": overlay_ref,
                }.items() if v
            }
        cache_path.write_text(json.dumps(info, indent=2))

    # Symlink experiment-local dir → trace-cache (backward compat for readers).
    link = run_output_dir / "run_info.json"
    if link.is_symlink():
        if link.resolve() == cache_path.resolve():
            return  # already linked correctly
        link.unlink()
    elif link.exists():
        return  # real file already there (e.g. historical run) — leave it
    try:
        link.symlink_to(cache_path)
    except OSError:
        pass  # cross-device or unsupported; cache copy still exists
