#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canonical artefact ↔ pipeline I/O ↔ WS stream binder.

One name, four projections:

* ``name`` / ``stream`` — WS topic owner (``custom.chat``, ``native``)
* ``uri`` — ``artifact://experiment/scenario/item/run/name#attempt``
* ``address`` — ``/api/artefacts/{library}/…/{name}``
* ``path`` — ui-trace relative file (``pipeline/custom.chat.jsonl``)

``inputs`` / ``outputs`` / ``producers`` / ``consumers`` are the DAG edges
in both directions. ``complete`` is sealed: a cache hit is only legal on a
complete artefact whose input digest still matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from mas.lab.lab.config.artifact_types import artefact_address, artefact_uri

# Catalog shortcuts (`chat`) map onto the stream-owner identity (`custom.chat`).
SHORTCUTS = {
    "chat": "custom.chat",
    "topology": "custom.topology",
    "loop": "series",
    "trace": "events",
    "events": "events",
    "metrics": "metric",
}

# Live folds consume the events artefact and produce one named snapshot each.
# YAML pipeline steps merge on top via :func:`graph_from_steps`.
LIVE_GRAPH: dict[str, dict[str, Any]] = {
    "events": {
        "stream": "native",
        "inputs": [],
        "outputs": ["events"],
        "producers": ["runtime"],
        "consumers": ["fold.chat", "fold.topology"],
    },
    "custom.chat": {
        "stream": "custom.chat",
        "plot": "chat",
        "inputs": ["events"],
        "outputs": ["custom.chat"],
        "producers": ["fold.chat"],
        "consumers": ["plot.chat"],
    },
    "custom.topology": {
        "stream": "custom.topology",
        "plot": "topology",
        "inputs": ["events"],
        "outputs": ["custom.topology"],
        "producers": ["fold.topology"],
        "consumers": ["plot.topology"],
    },
    "series": {
        "stream": "series",
        "plot": "loop",
        "inputs": ["events"],
        "outputs": ["series"],
        "producers": ["fold.series"],
        "consumers": ["plot.loop"],
    },
    "metric": {
        "stream": "metric",
        "plot": "metrics",
        "inputs": ["events"],
        "outputs": ["metric"],
        "producers": ["fold.metrics"],
        "consumers": ["plot.metrics"],
    },
}


def canonical_artefact(name: str) -> str:
    """Shortcut (`chat`) or topic (`custom.chat.snapshot`) → stream owner."""
    key = str(name or "").strip()
    if key in SHORTCUTS:
        return SHORTCUTS[key]
    if key in LIVE_GRAPH:
        return key
    return name_of_stream(key) or key


def _node(name: str) -> dict[str, Any]:
    declared = LIVE_GRAPH.get(name) or {}
    return {
        "stream": str(declared.get("stream") or name),
        "plot": declared.get("plot"),
        "inputs": list(declared.get("inputs") or ([] if name == "events" else ["events"])),
        "outputs": list(declared.get("outputs") or [name]),
        "producers": list(declared.get("producers") or []),
        "consumers": list(declared.get("consumers") or []),
    }


def stream_of(name: str) -> str:
    return str(_node(name).get("stream") or name)


_STREAM_TICK_SUFFIXES = frozenset({"snapshot", "plot", "tick", "sealed", "html", "complete"})


def _stream_matches(declared: str, owner: str) -> bool:
    """Exact owner, or ``declared.snapshot`` / ``.sealed`` — not ``declared.foo``."""
    if not declared or owner == declared:
        return bool(declared) and owner == declared
    prefix = declared + "."
    if not owner.startswith(prefix):
        return False
    rest = owner[len(prefix) :]
    first = rest.split(".", 1)[0]
    return first in _STREAM_TICK_SUFFIXES


def name_of_stream(stream: str) -> str:
    owner = str(stream or "").strip()
    if not owner:
        return ""
    for name, node in LIVE_GRAPH.items():
        declared = str(node.get("stream") or name)
        if _stream_matches(declared, owner):
            return name
    for prefix in ("custom.", "series.", "artifact."):
        if owner.startswith(prefix):
            parts = owner.split(".")
            return ".".join(parts[:2]) if len(parts) >= 2 else owner
    if owner.startswith("metric"):
        return "metric"
    if owner.startswith(("native.", "spec.", "job.", "command.", "result.", "control.", "governance.", "io.")) or owner in {
        "native",
        "job",
        "spec",
    }:
        return "events"
    return owner


def is_sealed_topic(topic: str) -> bool:
    name = str(topic or "")
    return name.endswith(".sealed") or name.endswith(".complete") or name in {"job.completed"}


@dataclass
class ArtefactLink:
    name: str
    stream: str
    uri: str = ""
    address: str = ""
    path: str = ""
    complete: bool | None = None
    digest: str = ""
    key: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    producers: list[str] = field(default_factory=list)
    consumers: list[str] = field(default_factory=list)
    plot: str | None = None
    tree: str = "pipeline"

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "artefact": self.name,
            "stream": self.stream,
            "uri": self.uri,
            "address": self.address,
            "path": self.path,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "producers": list(self.producers),
            "consumers": list(self.consumers),
            "tree": self.tree,
        }
        if self.complete is not None:
            data["complete"] = self.complete
        if self.digest:
            data["digest"] = self.digest
        if self.key:
            data["key"] = self.key
        if self.plot:
            data["plot"] = self.plot
        return data

    def envelope_fields(self) -> dict[str, Any]:
        data = {
            "artefact": self.name,
            "stream": self.stream,
            "uri": self.uri,
            "path": self.path,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
        }
        if self.complete is not None:
            data["complete"] = self.complete
        return data


def link_for(
    name: str,
    ref: dict[str, Any] | None = None,
    *,
    complete: bool | None = None,
    tree: str | None = None,
    digest: str = "",
    key: str = "",
) -> ArtefactLink:
    name = canonical_artefact(name)
    node = _node(name)
    stream = str(node.get("stream") or name)
    resolved_tree = tree or ("runtime" if name == "events" else "pipeline")
    if resolved_tree == "runtime":
        rel = "runtime.jsonl"
    else:
        rel = f"pipeline/{name}.jsonl"
    data = dict(ref or {})
    return ArtefactLink(
        name=name,
        stream=stream,
        uri=artefact_uri(name, data),
        address=artefact_address(name, data),
        path=rel,
        complete=complete,
        digest=digest,
        key=key,
        inputs=list(node.get("inputs") or []),
        outputs=list(node.get("outputs") or [name]),
        producers=list(node.get("producers") or []),
        consumers=list(node.get("consumers") or []),
        plot=node.get("plot"),
        tree=resolved_tree,
    )


def link_for_topic(
    topic: str,
    ref: dict[str, Any] | None = None,
    *,
    complete: bool | None = None,
) -> ArtefactLink:
    name = canonical_artefact(name_of_stream(topic))
    sealed = is_sealed_topic(topic)
    flag = True if sealed else complete
    tree = "runtime" if name == "events" else "pipeline"
    return link_for(name, ref, complete=flag, tree=tree)


def _step_io(spec: Any) -> tuple[str, list[str], list[str]]:
    step_name = str(getattr(spec, "name", None) or getattr(spec, "type", "") or "")
    inputs = list(getattr(spec, "inputs", None) or [])
    outputs = list(getattr(spec, "outputs", None) or [])
    if isinstance(spec, dict):
        if not step_name:
            step_name = str(spec.get("name") or spec.get("type") or "")
        if not inputs:
            raw = spec.get("inputs") or spec.get("in")
            if raw:
                inputs = [raw] if isinstance(raw, str) else list(raw)
        if not outputs:
            raw = spec.get("outputs") or spec.get("out")
            if raw:
                outputs = [raw] if isinstance(raw, str) else list(raw)
    return (
        step_name,
        [canonical_artefact(str(item)) for item in inputs if str(item).strip()],
        [canonical_artefact(str(item)) for item in outputs if str(item).strip()],
    )


def graph_from_steps(steps: Iterable[Any] | None = None) -> dict[str, dict[str, Any]]:
    """Merge YAML ``in:`` / ``out:`` onto the live-fold graph (bidirectional)."""
    graph: dict[str, dict[str, Any]] = {name: dict(node) for name, node in LIVE_GRAPH.items()}
    for spec in steps or []:
        step_name, inputs, outputs = _step_io(spec)
        for artefact in outputs:
            node = graph.setdefault(artefact, _node(artefact))
            producers = list(node.get("producers") or [])
            if step_name and step_name not in producers:
                producers.append(step_name)
            node["producers"] = producers
            node["outputs"] = list(dict.fromkeys([*(node.get("outputs") or []), artefact]))
            node["inputs"] = list(dict.fromkeys([*(node.get("inputs") or []), *inputs]))
        for artefact in inputs:
            node = graph.setdefault(artefact, _node(artefact))
            consumers = list(node.get("consumers") or [])
            if step_name and step_name not in consumers:
                consumers.append(step_name)
            node["consumers"] = consumers
            node.setdefault("outputs", [artefact])
            node.setdefault("inputs", [])
            node.setdefault("producers", [])
            node.setdefault("stream", _node(artefact).get("stream"))
    return graph


def shard_is_complete(rows: list[dict[str, Any]], *, tree: str) -> bool:
    if not rows:
        return False
    if any(row.get("complete") is True or is_sealed_topic(str(row.get("topic") or "")) for row in rows):
        return True
    if tree == "runtime":
        kinds = {str((row.get("payload") or {}).get("kind") if isinstance(row.get("payload"), dict) else "") for row in rows}
        kinds.update(str(row.get("topic") or "").rsplit(".", 1)[-1] for row in rows)
        return bool(kinds & {"mas_call_end", "execution_end", "job.completed"})
    return False
