#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""On-disk UI trace: the WS envelope stream, split into runtime vs pipeline.

A replay directory looks like::

    ui-trace/
      manifest.json                 # kind, files[], runtime_digest
      runtime.jsonl                 # native / spec / job  (MAS telemetry)
      pipeline/<artefact>.jsonl     # one materialized artefact (chat, series, …)

``runtime.jsonl`` is the input log. Each ``pipeline/<artefact>.jsonl`` is the
realtime counterpart of a pipeline-step fingerprint: a content-addressed
snapshot keyed by ``hash(artefact, version, runtime_digest)``. Replay skips a
shard when that key still matches (interrupt/restart, or adding a new fold
that only misses its own file). ``files[]`` is the extension point.

The batch executor keeps the same contract on
``<output_dir>/.cache/<step>.fingerprint`` + ``artefacts/<aa>/<key>/``.
This tree is the WS/live view of those artefacts, not a second cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

KIND = "mas-lab.ui-trace"
VERSION = 1
ARTEFACT_VERSION = "2"

RUNTIME_PREFIXES = (
    "native.",
    "spec.",
    "job.",
    "command.",
    "result.",
    "control.",
    "governance.",
    "io.",
)
PIPELINE_PREFIXES = ("custom.", "series.", "artifact.", "metric.", "metrics.")


def artefact_owner(topic: str) -> str:
    name = str(topic or "")
    parts = name.split(".")
    if name.startswith(("custom.", "series.", "artifact.")) and len(parts) >= 2:
        return ".".join(parts[:2])
    if name.startswith("metric"):
        return "metric"
    return name or "pipeline"


def artefact_filename(owner: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "." for ch in owner).strip(".")
    return f"pipeline/{safe or 'pipeline'}.jsonl"


def envelopes_digest(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = row.get("payload") if isinstance(row, dict) else row
        digest.update(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def artefact_key(owner: str, runtime_digest: str, *, version: str = ARTEFACT_VERSION) -> str:
    blob = f"{owner}|{version}|{runtime_digest}".encode()
    return hashlib.sha256(blob).hexdigest()


def tree_of(topic: str) -> str:
    name = str(topic or "")
    if name.startswith(PIPELINE_PREFIXES):
        return "pipeline"
    if name.startswith(RUNTIME_PREFIXES):
        return "runtime"
    if name.startswith("view."):
        return "control"
    return "pipeline" if name else "runtime"


def is_ui_trace_dir(path: Path) -> bool:
    manifest = path / "manifest.json"
    if not manifest.is_file():
        return False
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return isinstance(doc, dict) and doc.get("kind") == KIND


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def split_envelopes(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"runtime": [], "pipeline": [], "control": []}
    for row in rows:
        tree = tree_of(str(row.get("topic") or ""))
        out.setdefault(tree, []).append(row)
    return out


def _stamp_rows(rows: list[dict[str, Any]], *, tree: str) -> list[dict[str, Any]]:
    from mas.lab.controller.ui_stream.artefact_bind import link_for_topic

    stamped: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        topic = str(item.get("topic") or "")
        link = link_for_topic(topic, item.get("ref") if isinstance(item.get("ref"), dict) else {}, complete=item.get("complete") if isinstance(item.get("complete"), bool) else None)
        owner = link.name if tree == "pipeline" else "events"
        item.setdefault("artefact", owner)
        item.setdefault("stream", link.stream)
        item.setdefault("uri", link.uri)
        item.setdefault("path", link.path)
        item.setdefault("inputs", list(link.inputs))
        item.setdefault("outputs", list(link.outputs))
        if item.get("complete") is None and link.complete is not None:
            item["complete"] = link.complete
        stamped.append(item)
    return stamped


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_ui_trace(dest: Path, envelopes: list[dict[str, Any]], *, extra: dict[str, Any] | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    buckets = split_envelopes(envelopes)
    files = []
    from mas.lab.controller.ui_stream.artefact_bind import graph_from_steps, link_for, shard_is_complete

    runtime = _stamp_rows(buckets.get("runtime") or [], tree="runtime")
    runtime_digest = envelopes_digest(runtime)
    extra_doc = dict(extra or {})
    ref = extra_doc.get("ref") if isinstance(extra_doc.get("ref"), dict) else {}
    runtime_link = link_for("events", ref, tree="runtime", digest=runtime_digest, complete=shard_is_complete(runtime, tree="runtime"))
    _write_jsonl(dest / "runtime.jsonl", runtime)
    files.append(
        {
            **runtime_link.as_dict(),
            "path": "runtime.jsonl",
            "n": len(runtime),
            "digest": runtime_digest,
        }
    )
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for row in _stamp_rows(buckets.get("pipeline") or [], tree="pipeline"):
        by_owner.setdefault(str(row.get("artefact") or "pipeline"), []).append(row)
    for owner, rows in sorted(by_owner.items()):
        rel = artefact_filename(owner)
        _write_jsonl(dest / rel, rows)
        complete = shard_is_complete(rows, tree="pipeline")
        link = link_for(
            owner,
            ref,
            tree="pipeline",
            complete=complete,
            digest=runtime_digest,
            key=artefact_key(owner, runtime_digest),
        )
        files.append({**link.as_dict(), "path": rel, "n": len(rows), "input_digest": runtime_digest})
    manifest = {
        "kind": KIND,
        "version": VERSION,
        "runtime_digest": runtime_digest,
        "graph": extra_doc.pop("graph", None) or graph_from_steps(extra_doc.pop("steps", None)),
        "files": files,
        **extra_doc,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


def read_ui_trace(dest: Path, *, trees: tuple[str, ...] | None = None) -> dict[str, list[dict[str, Any]]]:
    dest = dest.resolve()
    if dest.is_file() and dest.name == "manifest.json":
        dest = dest.parent
    if not is_ui_trace_dir(dest):
        raise FileNotFoundError(f"not a ui-trace directory: {dest}")
    wanted = set(trees) if trees else {"runtime", "pipeline"}
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    out: dict[str, list[dict[str, Any]]] = {}
    for spec in manifest.get("files") or []:
        tree = str(spec.get("tree") or "")
        if tree not in wanted:
            continue
        rel = str(spec.get("path") or "")
        if not rel:
            continue
        out.setdefault(tree, []).extend(load_jsonl(dest / rel))
    for tree in wanted:
        out.setdefault(tree, [])
    return out


def cached_pipeline_files(dest: Path, runtime_digest: str) -> list[dict[str, Any]]:
    """Pipeline shards whose node key still matches ``runtime_digest``."""
    dest = dest.resolve()
    if dest.is_file() and dest.name == "manifest.json":
        dest = dest.parent
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    hits: list[dict[str, Any]] = []
    for spec in manifest.get("files") or []:
        if str(spec.get("tree") or "") != "pipeline":
            continue
        owner = str(spec.get("artefact") or "")
        if not owner:
            continue
        if spec.get("complete") is not True:
            continue
        if spec.get("input_digest") != runtime_digest:
            continue
        if spec.get("key") != artefact_key(owner, runtime_digest):
            continue
        hits.append(spec)
    return hits


def discover_ui_traces(target: Path) -> list[tuple[str, Path]]:
    """Return (scenario, ui-trace dir) pairs under a lab or a single dir."""
    target = target.expanduser().resolve()
    if is_ui_trace_dir(target):
        return [(target.name, target)]
    if target.is_file():
        return []
    hits: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for manifest in sorted(target.rglob("manifest.json")):
        parent = manifest.parent
        if parent in seen or not is_ui_trace_dir(parent):
            continue
        seen.add(parent)
        scenario = parent.parent.name if parent.name in {"ui-trace", "trace"} else parent.name
        hits.append((scenario, parent))
    return hits
