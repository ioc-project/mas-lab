#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Built-in gather over sealed child artifacts (fan-in at item/scenario/experiment)."""

from __future__ import annotations

from typing import Any, Iterable

from mas.lab.benchmark.pipeline.artifact import Artifact
from mas.lab.benchmark.pipeline.keys import RunKey


def gather_records(
    children: Iterable[Iterable[dict[str, Any]]],
    *,
    annotate: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Concatenate child record lists. Same row identity as ``gather_level``."""
    extra = {k: v for k, v in (annotate or {}).items() if v not in (None, "")}
    out: list[dict[str, Any]] = []
    for rows in children:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            for key, value in extra.items():
                if key not in item:
                    item[key] = value
            out.append(item)
    return out


def gather_artifacts(
    artifacts: Iterable[Artifact],
    *,
    name: str = "data",
    key: RunKey | None = None,
    annotate: dict[str, Any] | None = None,
) -> Artifact:
    from mas.lab.benchmark.pipeline.types import DATA

    dest = Artifact(name=name, key=key or RunKey("e", "s", "item", "r1"), spec=DATA)
    dest.append(gather_records((art.records() for art in artifacts), annotate=annotate))
    dest.seal()
    return dest
