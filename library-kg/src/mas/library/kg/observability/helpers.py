"""Shared low-level helpers for observability paths.

Public API::

    ns_to_s(ns) -> float        — nanoseconds (int or str) to seconds
    TRACE_CACHE_ROOT            — canonical trace-cache root directory
    load_spans(path) -> list    — load a JSONL or JSON-array span file
    GapReporter                 — dedup/rate-limit per-item observability warnings
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

from mas.runtime import hostenv

logger = logging.getLogger(__name__)


class GapReporter:
    """Dedup and rate-limit warnings for per-item observability gaps.

    A single trace can contain thousands of spans/events sharing the same
    underlying gap (e.g. one unmapped ``mas.boundary`` value used by every
    span of a given type). Logging one warning per occurrence would flood
    the logs and drown out other signal. This logs the first occurrence of
    each distinct gap key in full, silently tallies the rest, and emits one
    summary line per repeated key via ``log_summary()``.

    Usage::

        gaps = GapReporter(logger, run_id)
        ...
        except UnknownSpanBoundaryError as exc:
            gaps.report(("unmapped_kind", kind), exc,
                        "normalize_events: observability gap run_id=%s — %s.",
                        run_id, exc)
        ...
        gaps.log_summary()
    """

    def __init__(self, logger_: logging.Logger, run_id: str) -> None:
        self._logger = logger_
        self._run_id = run_id
        self._counts: Dict[Any, int] = {}
        self._last_exc: Dict[Any, Exception] = {}

    def report(self, gap_key: Any, exc: Exception, message: str, *args: Any) -> None:
        """Record one occurrence of *gap_key*; log *message* only if it's the first."""
        count = self._counts.get(gap_key, 0) + 1
        self._counts[gap_key] = count
        self._last_exc[gap_key] = exc
        if count == 1:
            self._logger.warning(message, *args)

    def log_summary(self) -> None:
        """Log one line per gap key that recurred more than once."""
        for gap_key, count in self._counts.items():
            if count > 1:
                self._logger.warning(
                    "run_id=%s — gap %r recurred %d times; only the first "
                    "occurrence was logged in full (last: %s).",
                    self._run_id,
                    gap_key,
                    count,
                    self._last_exc[gap_key],
                )


def gap_key_for_exception(exc: Exception) -> Any:
    """Best-effort stable dedup key grouping occurrences of the same gap.

    Exception ``str()`` output for this codebase's KGNormalizationError
    subclasses embeds the offending ``span_id`` (e.g. ``"[span=abc123]
    Unknown SpanName=..."``), so keying a :class:`GapReporter` by
    ``str(exc)`` never dedupes — every occurrence gets a "unique" key even
    when they share the same root cause (the exact spam scenario dedup
    exists to prevent: an SDK bump makes every span in a trace carry the
    same unmapped name/boundary). This instead keys by the exception type
    plus whichever identifying attribute it carries (``span_name``,
    ``boundary``, ``field``, ``kind``, ``attribute``) — deliberately
    excluding ``span_id`` — falling back to just the exception type for
    plain ``ValueError``/``TypeError`` that carry no such attribute.
    """
    for attr in ("span_name", "boundary", "field", "kind", "attribute"):
        val = getattr(exc, attr, None)
        if val is not None:
            return (type(exc).__name__, attr, val)
    return (type(exc).__name__,)


def ns_to_s(ns: Union[int, str, None]) -> float:
    """Convert a nanosecond value (integer *or* numeric string) to seconds.

    Returns 0.0 on ``None``, empty string, or non-numeric input.
    """
    if not ns:
        return 0.0
    try:
        return int(ns) / 1_000_000_000.0
    except (ValueError, TypeError):
        return 0.0


#: Canonical trace-cache root directory.
#: Resolved from ``$MAS_TRACE_CACHE``; defaults to ``~/.mas-lab/data/trace-cache``.
#: The run-ref indirection pattern resolves ``{root}/{run_ref}/traces/…``.
def _resolve_trace_cache_root() -> Path:
    raw = hostenv.get("MAS_TRACE_CACHE", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".mas-lab" / "data" / "trace-cache"


TRACE_CACHE_ROOT: Path = _resolve_trace_cache_root()


def load_spans(path: Path) -> List[Dict[str, Any]]:
    """Load spans from *path*.

    Accepts:
    - JSON-lines (one JSON object per line, e.g. ``ReadableSpan.to_json()``)
    - JSON array (OpenClaw / OTel batch export)

    Skips blank lines and logs a warning on malformed JSON lines.
    """
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    spans: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            spans.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed line %d in %s: %s", lineno, path, exc)
    return spans
