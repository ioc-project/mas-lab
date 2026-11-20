# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Producer/consumer wiring for scenario checkpoint forks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


def parse_resume_from(raw: object) -> tuple[str | None, int]:
    """Return (checkpoint id, 1-based occurrence). Missing id → (None, 1)."""
    if raw is None or raw == "":
        return None, 1
    if isinstance(raw, str):
        return raw, 1
    if isinstance(raw, Mapping):
        cid = str(raw.get("id") or "").strip() or None
        index = raw.get("index", raw.get("occurrence", 1))
        try:
            parsed = int(index or 1)
        except (TypeError, ValueError):
            parsed = 1
        return cid, max(1, parsed)
    raise ValueError(f"resume_from must be a name or {{id, index}}, got {raw!r}")


def _when_of(action: Mapping[str, Any]) -> Any:
    when = action.get("when")
    if when is not None:
        return when
    trigger = action.get("trigger")
    if isinstance(trigger, Mapping):
        out = dict(trigger)
        if "on" not in out and True in out:
            out["on"] = out.pop(True)
        return out
    return None


def compile_checkpoint_trigger(action: Mapping[str, Any], *, default_id: str = "") -> dict[str, Any]:
    """Compile one session_checkpoints / debug entry to an environment trigger."""
    cid = str(action.get("id") or default_id or "").strip()
    if not cid:
        raise ValueError("session checkpoint needs id")
    when = _when_of(action)
    if when is None:
        raise ValueError(f"session checkpoint {cid!r} needs when")
    if isinstance(when, Mapping):
        when_out = dict(when)
        if "on" not in when_out and True in when_out:
            when_out["on"] = when_out.pop(True)
        occ = action.get("occurrence")
        if occ is not None and "occurrence" not in when_out:
            when_out["occurrence"] = occ
    else:
        when_out = when
    persist = action.get("persist", True)
    persist_on = persist not in (False, "false", "no", "")
    label = cid if persist in (True, None, "") else str(persist)
    after = str(action.get("after") or "continue").strip().lower()
    do: list[dict[str, Any]] = [
        {
            "type": "checkpoint",
            "config": {"label": label, "persist": persist_on, "auto_stop": True},
        }
    ]
    if after in {"pause", "stop"}:
        do.append({"type": "pause", "config": {"reason": cid}})
    else:
        do.append({"type": "resume", "config": {}})
    return {"id": cid, "when": when_out, "do": do}


def read_debug_script(debug: Mapping[str, Any], base_dir: Path | None) -> str:
    """The gdb script of a ``debug:`` block: inline ``script``, else the text of ``script_file``.

    The file is read relative to the experiment and must stay inside it, the same rule a
    Control's ``script_file`` follows.
    """
    inline = str(debug.get("script") or "")
    name = str(debug.get("script_file") or "")
    if inline or not name:
        return inline
    if base_dir is None:
        raise ValueError("debug.script_file needs the experiment directory to resolve against")
    if Path(name).is_absolute():
        raise ValueError(f"debug.script_file must be relative to the experiment, got absolute path {name!r}")
    root = Path(base_dir).resolve()
    target = (root / name).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"debug.script_file {name!r} leaves the experiment directory")
    if not target.is_file():
        raise FileNotFoundError(f"debug.script_file {name!r} not found at {target}")
    return target.read_text(encoding="utf-8")


def apply_session_checkpoint_triggers(
    scenarios: Sequence[Any],
    session_checkpoints: Sequence[Mapping[str, Any]],
    *,
    base_dir: Path | None = None,
) -> None:
    """Compile session_checkpoints and scenarios[].debug into environment triggers.

    A raw ``debug.script`` is attached as ``environment.script`` (gdb). Other
    structured debug entries become checkpoint/pause triggers.
    """
    by_id = {str(item.get("id")): item for item in session_checkpoints if isinstance(item, Mapping) and item.get("id")}
    for spec in scenarios:
        extra: list[dict[str, Any]] = []
        for name in getattr(spec, "produce_checkpoints", None) or []:
            if name in by_id:
                extra.append(compile_checkpoint_trigger(by_id[name]))
        debug = dict(getattr(spec, "debug", None) or {})
        env = dict(getattr(spec, "environment", None) or {})
        if debug.get("script") or debug.get("script_file"):
            env["script"] = read_debug_script(debug, base_dir) or str(env.get("script") or "")
        else:
            breakpoints = debug.get("breakpoints")
            if isinstance(breakpoints, list) and breakpoints:
                for index, item in enumerate(breakpoints):
                    if isinstance(item, Mapping):
                        extra.append(
                            compile_checkpoint_trigger(
                                item,
                                default_id=str(item.get("id") or f"{spec.id}-debug-{index + 1}"),
                            )
                        )
            elif debug.get("when") or debug.get("trigger"):
                extra.append(compile_checkpoint_trigger(debug, default_id=f"{spec.id}-debug"))
        if extra:
            existing = list(env.get("triggers") or [])
            seen = {row.get("id") for row in existing if isinstance(row, Mapping)}
            for row in extra:
                rid = row.get("id")
                if rid in seen:
                    continue
                existing.append(row)
                seen.add(rid)
            env["triggers"] = existing
        if extra or debug.get("script") or debug.get("script_file"):
            spec.environment = env


def producer_ids(scenarios: Iterable[Any]) -> dict[str, str]:
    """Map checkpoint name → producing scenario id. Two owners is an error."""
    owners: dict[str, str] = {}
    for spec in scenarios:
        for name in getattr(spec, "produce_checkpoints", None) or []:
            key = str(name)
            previous = owners.get(key)
            owner = str(spec.id)
            if previous is not None and previous != owner:
                raise ValueError(f"produce_checkpoints {key!r} is owned by {previous} and {owner}")
            owners[key] = owner
    return owners


def consumer_gate(spec: Any) -> tuple[str, str | None, int]:
    """How a dependent scenario becomes schedulable.

    ``("checkpoint", id, index)`` waits for that published file, not the
    producer's full run. ``("done", None, 1)`` waits until the producer finishes.
    Either way the run takes a parallel slot only when it starts.
    """
    raw = getattr(spec, "resume_from", None)
    resume, parsed = parse_resume_from(raw)
    index = int(getattr(spec, "resume_index", None) or parsed or 1)
    if resume:
        return "checkpoint", resume, index
    return "done", None, 1


_CHECKPOINT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _check_checkpoint_name(name: str) -> None:
    """A checkpoint name becomes part of a file name in the catalog: no separators, no dots."""
    if not _CHECKPOINT_NAME.fullmatch(str(name)):
        raise ValueError(f"checkpoint name {name!r} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")


def validate_scenario_graph(
    scenarios: Sequence[Any],
    session_checkpoints: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Reject unknown depends_on / resume_from and cycles."""
    by_id = {str(spec.id): spec for spec in scenarios}
    declared_ids = [
        str(item.get("id")) for item in (session_checkpoints or []) if isinstance(item, Mapping) and item.get("id")
    ]
    if len(declared_ids) != len(set(declared_ids)):
        raise ValueError("duplicate session_checkpoints id")
    for ident in declared_ids:
        _check_checkpoint_name(ident)
    owners = producer_ids(scenarios)
    declared = set(declared_ids)
    for spec in scenarios:
        for dep in getattr(spec, "depends_on", None) or []:
            if dep not in by_id:
                raise ValueError(f"scenario {spec.id}: depends_on unknown scenario {dep!r}")
        resume, _index = parse_resume_from(getattr(spec, "resume_from", None))
        if resume and resume not in owners:
            raise ValueError(f"scenario {spec.id}: resume_from {resume!r} has no produce_checkpoints owner")
        if resume and str(spec.id) not in (getattr(spec, "depends_on", None) or []):
            owner = owners[resume]
            if owner != spec.id and owner not in (getattr(spec, "depends_on", None) or []):
                raise ValueError(f"scenario {spec.id}: resume_from {resume!r} requires depends_on: [{owner}]")
        for name in getattr(spec, "produce_checkpoints", None) or []:
            _check_checkpoint_name(name)
            if declared and name not in declared:
                raise ValueError(
                    f"scenario {spec.id}: produce_checkpoints {name!r} is not in experiment.session_checkpoints"
                )
    visiting: set[str] = set()
    seen: set[str] = set()

    def walk(sid: str) -> None:
        if sid in seen:
            return
        if sid in visiting:
            raise ValueError(f"scenario depends_on cycle involving {sid!r}")
        visiting.add(sid)
        spec = by_id[sid]
        for dep in getattr(spec, "depends_on", None) or []:
            walk(str(dep))
        visiting.remove(sid)
        seen.add(sid)

    for spec in scenarios:
        walk(str(spec.id))


def checkpoint_key(item: Mapping[str, Any], run_idx: int) -> str:
    """Catalog folder for one dataset item and run: ``{item}/r{n}``."""
    raw = str(item.get("source_item_id", item.get("id", 0)))
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw) or "item"
    if safe != raw:
        # "a/b" and "a_b" must not share a folder: a changed name carries a digest of the original.
        safe = f"{safe}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"
    return f"{safe}/r{int(run_idx) + 1}"


def _catalog_dir(output_dir: Path, key: str) -> Path:
    base = Path(output_dir) / "checkpoints"
    return base / key if key else base


def published_checkpoint_path(output_dir: Path, name: str, *, index: int = 1, key: str = "") -> Path:
    dest_dir = _catalog_dir(output_dir, key)
    indexed = dest_dir / f"{name}-{int(index)}.checkpoint.json"
    if indexed.is_file():
        return indexed
    if int(index) == 1:
        alias = dest_dir / f"{name}.checkpoint.json"
        if alias.is_file():
            return alias
    return indexed


def _occurrence_from_stem(stem: str, name: str) -> int | None:
    """Parse 1-based persist index from `{name}` or `{name}-{n}` only."""
    if stem == name:
        return 1
    prefix = f"{name}-"
    if stem.startswith(prefix) and stem[len(prefix) :].isdigit():
        return int(stem[len(prefix) :])
    return None


def _match_persist_files(run_checkpoint_dir: Path, name: str) -> list[Path]:
    root = Path(run_checkpoint_dir)
    found: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(root.glob("*.checkpoint.json")):
        if _occurrence_from_stem(path.name.removesuffix(".checkpoint.json"), name) is None:
            continue
        if path not in seen:
            seen.add(path)
            found.append(path)
    return found


def publish_named_checkpoints(
    *,
    run_checkpoint_dir: Path,
    output_dir: Path,
    names: Sequence[str],
    missing_ok: bool = False,
    key: str = "",
) -> list[Path]:
    """Copy persisted files into the experiment-level checkpoint catalog.

    ``missing_ok`` is for the mid-run watcher: copy whatever exists so a
    consumer can start on ``{name}-{index}.checkpoint.json`` before the
    producer finishes. The final publish (after status==ok) stays strict.
    """
    dest_dir = _catalog_dir(output_dir, key)
    dest_dir.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    for name in names:
        matches = _match_persist_files(run_checkpoint_dir, name)
        if not matches:
            if missing_ok:
                continue
            raise FileNotFoundError(
                f"scenario produced checkpoint {name!r} but {run_checkpoint_dir} has no *.checkpoint.json"
            )
        for src in matches:
            stem = src.name.removesuffix(".checkpoint.json")
            index = _occurrence_from_stem(stem, name) or 1
            data = src.read_bytes()
            try:
                json.loads(data)
            except ValueError:
                # The producer is still writing it. Never hand a consumer half a file.
                if missing_ok:
                    continue
                raise ValueError(f"checkpoint {src} is not valid JSON") from None
            dest = dest_dir / f"{name}-{index}.checkpoint.json"
            _write_atomic(dest, data)
            published.append(dest)
            if index == 1:
                alias = dest_dir / f"{name}.checkpoint.json"
                _write_atomic(alias, data)
                published.append(alias)
    return published


def _write_atomic(dest: Path, data: bytes) -> None:
    """Write via a temp file and rename: a poller sees the whole file or none of it."""
    try:
        if dest.is_file() and dest.read_bytes() == data:
            return  # the watcher republishes every tick; do not rewrite what is already there
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def row_status(rows: Sequence[Mapping[str, Any]], scenario: str, item_id: Any, run_number: int) -> str:
    """Status of the newest result row for this scenario, item and run; ``ok`` when none was written.

    Matching on the item matters: a consumer waits for one item's producer, and another item's
    row must not speak for it.
    """
    for row in reversed(rows):
        if row.get("scenario") == scenario and int(row.get("run") or 0) == run_number and row.get("item_id") == item_id:
            return str(row.get("status") or "ok")
    return "ok"
