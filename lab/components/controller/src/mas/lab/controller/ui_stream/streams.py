#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Named telemetry streams plugins get() / subscribe() on."""

from __future__ import annotations

# Logical stream → topic globs. Plugins may also pass raw topic globs.
# Keep in sync with demo-ui/src/streams.js (same keys and topic lists).
STREAM_TOPICS: dict[str, tuple[str, ...]] = {
    "spec": ("spec.snapshot", "spec.*"),
    "observability": ("native.*",),
    "metrics": ("metrics.snapshot", "metric.*", "native.metric", "metric.scope"),
    "artifacts": ("artifact.*", "artifact.metric"),
    "governance": ("governance.*", "control.context"),
    "control": ("control.*", "control.structure", "control.context"),
    "hitl": ("control.*",),
    "job": ("job.*", "job.progress"),
    "custom": ("custom.*",),
    "series": ("series.*",),
}


def expand_stream_names(names: list[str] | tuple[str, ...] | None) -> list[str]:
    """Map ``spec`` / ``observability`` / topic globs onto unique topic globs."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        text = str(raw or "").strip()
        if not text or text == "*":
            continue
        if text in STREAM_TOPICS:
            parts = STREAM_TOPICS[text]
        elif text.startswith(("custom.", "series.")) and "*" not in text:
            parts = (text, f"{text}.*")
        else:
            parts = (text,)
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
    return out
