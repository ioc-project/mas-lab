#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inspect pulls the UI may request. Nothing is sampled unless subscribed."""

from __future__ import annotations

from dataclasses import dataclass

from mas.lab.controller.ui_stream.refs import topic_matches

# Canonical topic a plugin subscribes to → which inspect verb / extra native kinds.
# The space of inspectable session state is large; add a row only when a UI
# plugin actually declares `inspects: ["…"]` (or subscribes to the topic).


@dataclass(frozen=True)
class InspectPull:
    topic: str
    verb: str
    native_kinds: tuple[str, ...]


PULLS: dict[str, InspectPull] = {
    "context": InspectPull(
        topic="governance.context",
        verb="inspect_context",
        native_kinds=("context_assembled", "context_usage"),
    ),
}


def infer_inspect(topics: list[str] | tuple[str, ...]) -> list[str]:
    """Map named WS/plugin topic globs onto inspect pulls.

    Catch-all ``*`` is a viewer glob, not a demand to sample every inspect
    family. Only an explicit topic (``governance.context``, ``governance.*``,
    ``inspect.context``) opts in.
    """
    declared = tuple(glob for glob in (topics or ()) if glob not in {"", "*"})
    if not declared:
        return []
    wanted: list[str] = []
    for pull_id, spec in PULLS.items():
        if topic_matches(declared, spec.topic) or topic_matches(declared, f"inspect.{pull_id}"):
            wanted.append(pull_id)
    return wanted


def extra_native_kinds(inspect: list[str] | tuple[str, ...]) -> frozenset[str]:
    kinds: set[str] = set()
    for pull_id in inspect:
        spec = PULLS.get(pull_id)
        if spec is not None:
            kinds.update(spec.native_kinds)
    return frozenset(kinds)
