#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""FileTailStep — generic, reusable "watch a file, stream its content as it
changes" primitive.

Design, given the real v1 engine (see ``mas.lab.benchmark.pipeline.core``):
``PipelineStep.on_event(event, ctx)`` is the only hook invoked *during* a
live MAS run (once per native telemetry event); ``execute()`` only runs
*after* the run, to write end-of-run artefacts. There is no existing
mechanism for a background async loop to run concurrently with a live run,
and no cross-step publish/subscribe channel wired into the proven v1 path.

So this step piggybacks file-change detection on the live-run heartbeat:
every native event triggers ``on_event``, which (cheaply) stats the
configured path(s) and, if the mtime/size has changed since last check,
reads the new content and calls ``self.on_change(path, content, ctx)``.
Subclasses override ``on_change`` to parse and republish a structured
envelope; the default implementation forwards the raw text as a generic
``custom.{owner}.raw`` record.

Config:
    path: glob pattern for the file(s) to tail. May reference
        ``{skill_state_dir}`` (substituted from ``MAS_SKILL_STATE_DIR``),
        ``{state_dir}`` (``MAS_SKILL_STATE_DIR`` else
        ``MAS_{OWNER}_STATE_DIR``), and ``{run_dir}``
        (``ctx.run_dir``/``ctx.output_dir``). An unresolved token
        resolves to ``""`` rather than a filesystem-root glob.
    owner: short name used in the default emitted kind, ``custom.{owner}.raw``.
"""

import glob as _glob
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from mas.lab.benchmark.pipeline import ConfigParam, PipelineStep, StepOutput

logger = logging.getLogger(__name__)


def resolve_path_pattern(pattern: str, ctx: "Any" = None, *, owner: str = "") -> str:
    """Substitute ``{skill_state_dir}``/``{state_dir}``/``{run_dir}`` tokens in
    a path pattern.

    ``{state_dir}`` is the owner-aware token: ``MAS_SKILL_STATE_DIR`` if set,
    else ``MAS_{OWNER}_STATE_DIR`` when the step has an ``owner``.
    ``{skill_state_dir}`` is the narrower token for the shared skill-state
    directory with no per-owner fallback.

    Returns ``""`` (never a partially-substituted pattern) when a referenced
    token resolves empty -- e.g. neither env var is set because the skill
    ran through something other than ``run_skill_script`` and no per-owner
    override was exported either, or a lab config that uses ``{run_dir}`` on
    a step not declared under a ``run: post:`` block. Without this guard,
    ``"{skill_state_dir}/state*.json"`` would silently collapse to
    ``"/state*.json"`` -- an absolute-root glob that looks
    superficially harmless (nothing is usually there) but is the wrong
    failure mode: a misconfigured step should produce no matches and a loud
    one-time warning (see ``FileTailStep.on_event``'s empty-token check), not
    quietly scan from filesystem root.
    """
    skill_state_dir = os.environ.get("MAS_SKILL_STATE_DIR", "")
    owner_env = f"MAS_{owner.upper()}_STATE_DIR" if owner else ""
    state_dir = skill_state_dir or (os.environ.get(owner_env, "") if owner_env else "")
    run_dir = ""
    if ctx is not None:
        run_dir = str(getattr(ctx, "run_dir", None) or getattr(ctx, "output_dir", None) or "")
    needs_skill_dir = "{skill_state_dir}" in pattern
    needs_state_dir = "{state_dir}" in pattern
    needs_run_dir = "{run_dir}" in pattern
    if (needs_skill_dir and not skill_state_dir) or (needs_state_dir and not state_dir) or (needs_run_dir and not run_dir):
        return ""
    try:
        return pattern.format(skill_state_dir=skill_state_dir, state_dir=state_dir, run_dir=run_dir)
    except (KeyError, IndexError):
        return ""


def tail_changed(pattern: str, last_seen: Dict[str, tuple]) -> list[tuple[str, str]]:
    """Pure, unit-testable core: given a glob pattern and a ``{path: (mtime,
    size)}`` memo of what was last read, return ``[(path, new_content), ...]``
    for every matching file whose mtime or size has changed (or that is new),
    and update ``last_seen`` in place. No I/O side effects beyond reading the
    changed files, so this is safe to call from a hot per-event path.
    """
    changed: list[tuple[str, str]] = []
    for path in sorted(_glob.glob(pattern)):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        key = (stat.st_mtime, stat.st_size)
        if last_seen.get(path) == key:
            continue
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        last_seen[path] = key
        changed.append((path, content))
    return changed


def append_custom_trace(ctx: "Any", owner: str, record: Dict[str, Any]) -> None:
    """Persist one ``custom.{owner}`` tick to ``{run_dir}/custom.{owner}.jsonl``,
    the sibling-artefact convention ``metrics.json``/``progress.jsonl`` already
    use (see ``run_index.py``'s ``resolve_run()``), so replay can rebuild the
    same panel/plot envelopes a live run produced — without this, a tick that
    is persisted next to the native trace so post-run replay can rebuild it.

    ``ctx.output_dir`` is the per-run directory for a step declared under a
    pipeline's ``run: post:`` block (the same assumption ``metrics.json``/
    ``progress.jsonl`` writers already make — confirmed against
    ``run_index.py``'s ``resolve_run()``, which looks for exactly these
    sibling filenames next to ``events.jsonl`` in that directory).
    """
    run_dir = getattr(ctx, "output_dir", None)
    if run_dir is None:
        return
    path = Path(run_dir) / f"custom.{owner}.jsonl"
    stamped = dict(record)
    stamped.setdefault("ts", time.time())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stamped, default=str) + "\n")
    except OSError:
        logger.debug("append_custom_trace failed for %s", path, exc_info=True)


def ref_from_ctx(config: Dict[str, Any], ctx: "Any") -> Dict[str, Any]:
    """Build the ``{library, experiment, scenario, run}`` ref for a custom record."""
    scope = getattr(getattr(ctx, "scope_context", None), "scenario", None)
    return {
        "library": config.get("library"),
        "experiment": config.get("experiment"),
        "scenario": config.get("scenario") or scope,
        "run": getattr(ctx, "run_id", None),
    }


def publish_custom_record(record: Dict[str, Any], *, ref: Optional[Dict[str, Any]] = None) -> None:
    """Publish a custom record onto EventStream and the pipeline channel."""
    payload = dict(record)
    if ref:
        for key, value in ref.items():
            if value not in (None, "") and key not in payload:
                payload[key] = value
    try:
        from mas.runtime.boundary.obs.event_stream import emit_to_channel, publish_event

        publish_event(payload)
        emit_to_channel(payload)
    except Exception:
        logger.debug("custom record publish skipped", exc_info=True)


class FileTailStep(PipelineStep):
    """Generic reusable tail-and-stream primitive. See module docstring."""

    type = "file_tail"

    PARAMS = [
        ConfigParam("path", str, default=None, description="Glob pattern to tail."),
        ConfigParam("owner", str, default="file", description="Owner name for the default custom.{owner}.raw kind."),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_seen: Dict[str, tuple] = {}
        self._warned_unresolved = False
        self._warned_no_match = False
        self._event_count = 0

    def _pattern(self, ctx: "Any") -> str:
        raw = str(self.config.get("path") or "")
        if not raw:
            if not self._warned_unresolved:
                logger.warning("%s: no 'path' configured, this step will never tail anything", self.name)
                self._warned_unresolved = True
            return ""
        owner = str(self.config.get("owner") or "")
        resolved = resolve_path_pattern(raw, ctx, owner=owner)
        if not resolved and not self._warned_unresolved:
            logger.warning(
                "%s: path pattern %r references a token (skill_state_dir/state_dir/run_dir) "
                "that resolved empty -- check MAS_SKILL_STATE_DIR, or the per-protocol "
                "MAS_%s_STATE_DIR, is set (e.g. the skill ran through run_skill_script, "
                "or the script's own --state-dir env var was exported) or that this "
                "step is declared under a pipeline's 'run: post:' block",
                self.name,
                raw,
                owner.upper() or "<OWNER>",
            )
            self._warned_unresolved = True
        return resolved

    def _ref(self, ctx: "Any") -> Dict[str, Any]:
        return ref_from_ctx(self.config, ctx)

    def on_change(self, path: str, content: str, ctx: "Any") -> None:
        """Default: forward the raw file content as a generic custom.{owner}.raw
        record. Subclasses override this to parse + transform."""
        owner = str(self.config.get("owner") or "file")
        record = {"kind": f"custom.{owner}.raw", "path": path, "content": content}
        append_custom_trace(ctx, owner, record)
        publish_custom_record(record, ref=self._ref(ctx))

    def on_batch(self, batch: list, ctx: "Any") -> None:  # noqa: F821
        del batch
        self.on_event({}, ctx)

    def on_event(self, event: Dict[str, Any], ctx: "Any") -> None:  # noqa: F821
        pattern = self._pattern(ctx)
        if not pattern:
            return
        self._event_count += 1
        changed = tail_changed(pattern, self._last_seen)
        for path, content in changed:
            try:
                self.on_change(path, content, ctx)
            except Exception:
                # A broken transform must not crash the live MAS run this
                # step is observing (same "optional observability plugin,
                # never fatal" contract for optional ingest) -- but silence
                # at debug level means an operator
                # running at default log levels would never see a genuinely
                # broken on_change() implementation. Warn instead.
                logger.warning("%s: on_change failed for %s", self.name, path, exc_info=True)
        # A pattern that has run for a while (enough native events to expect
        # at least one skill invocation) without ever matching a file is very
        # likely misconfigured (wrong filename convention, wrong owner, the
        # skill never actually ran) rather than "just hasn't started yet" --
        # surface that once instead of staying silently idle for the whole
        # run (the exact "nothing shows up and no error" failure mode an
        # author configuring a new owner would otherwise hit blind).
        if not self._last_seen and not self._warned_no_match and self._event_count >= 20:
            logger.warning("%s: path pattern %r has matched no files after %d events", self.name, pattern, self._event_count)
            self._warned_no_match = True

    async def execute(self, ctx: "Any") -> StepOutput:  # noqa: F821
        # Post-run safety net: pick up any final write that landed between the
        # last on_event tick and run completion (e.g. a certificate written
        # right before mas_call_end, after the last tool_call event fired).
        self.on_event({}, ctx)
        if not self._last_seen:
            logger.info("%s: completed having never matched a file (pattern=%r)", self.name, self.config.get("path"))
        return StepOutput(data={"tailed": sorted(self._last_seen)}, files=[], metadata={})
