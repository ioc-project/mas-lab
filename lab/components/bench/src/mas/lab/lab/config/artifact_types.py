#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Artifact type contract — implementations are library.yaml plugins.

Libraries declare ``type: artifact`` entries (name, module, class) in
``library.yaml``.  Path and format live on the class.  Bench looks them
up through ``mas.runtime.registry``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from mas.runtime.registry import PluginEntry, get_registry, register_plugin


COORD_KEYS = ("library", "experiment", "scenario", "test", "run")
UNSET = "-"
ADDRESS_TEMPLATE = "/api/artefacts/{library}/{experiment}/{scenario}/{test}/{run}/{name}"


class ArtifactType:
    """Named artifact kind.  Subclasses set ``path`` / ``format``.

    Realtime artefacts also set ``stream`` (EventStream owner) and
    optionally ``plot`` (the HTML plot kind that consumes this artefact).
    The live address is ``ADDRESS_TEMPLATE`` filled from a run coordinate.
    """

    path: str = "{level_dir}/{name}"
    format: str = ""
    description: str = ""
    schema: str | None = None
    stream: str | None = None
    plot: str | None = None
    realtime: bool = False


def _safe_segment(value: Any, *, unset: str = UNSET) -> str:
    text = str(value or "").strip()
    if not text:
        return unset
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"invalid path segment {text!r}")
    return text


def artefact_uri(name: str, ref: dict[str, Any] | None = None) -> str:
    """Canonical artefact identity: ``artifact://experiment/scenario/item/run/name#attempt``."""
    from mas.lab.benchmark.pipeline.keys import run_key_from_parts

    data = dict(ref or {})
    key = run_key_from_parts(
        experiment=str(data.get("experiment") or ""),
        scenario=str(data.get("scenario") or ""),
        item=str(data.get("test") or data.get("item") or ""),
        run=str(data.get("run") or ""),
        attempt=int(data.get("attempt") or 1),
    )
    return key.artifact_uri(str(name or "").strip())


def parse_artefact_uri(uri: str) -> dict[str, str]:
    """Inverse of ``artefact_uri`` (empty dict if the string is not a URI)."""
    raw = str(uri or "").strip()
    if not raw.startswith("artifact://"):
        return {}
    body = raw[len("artifact://") :]
    name_part, _, attempt = body.partition("#")
    parts = name_part.split("/")
    if len(parts) < 5:
        return {}
    experiment, scenario, item, run, name = parts[0], parts[1], parts[2], parts[3], "/".join(parts[4:])
    out = {
        "name": name,
        "experiment": experiment,
        "scenario": scenario,
        "test": item,
        "run": run,
        "attempt": attempt or "1",
    }
    return {k: v for k, v in out.items() if v}


def artefact_address(name: str, ref: dict[str, Any] | None = None) -> str:
    """Hierarchical address for one artefact at a lab run coordinate."""
    data = dict(ref or {})
    parts = [_safe_segment(data.get(key)) for key in COORD_KEYS]
    return "/api/artefacts/" + "/".join([*parts, _safe_segment(name, unset="")])


def ref_from_artefact_path(
    library: str,
    experiment: str,
    scenario: str,
    test: str,
    run: str,
) -> dict[str, str]:
    """Inverse of ``artefact_address`` path segments (``-`` means unset)."""
    raw = {
        "library": library,
        "experiment": experiment,
        "scenario": scenario,
        "test": test,
        "run": run,
    }
    return {k: v for k, v in raw.items() if v not in (None, "", UNSET, "_")}


def _urn(name: str) -> str:
    token = str(name).strip().lower().replace("-", "_").replace(".", "_")
    return f"mas.artifact.{token}"


def _info_from_entry(entry: PluginEntry) -> dict[str, Any]:
    cls = entry.resolve().load_class()
    attrs = entry.attributes or {}
    info: dict[str, Any] = {
        "path": attrs.get("path") or getattr(cls, "path", None) or "{level_dir}/{name}",
        "format": attrs.get("format") or getattr(cls, "format", None) or "",
        "description": (
            entry.description
            or attrs.get("description")
            or getattr(cls, "description", None)
            or ""
        ),
    }
    schema = attrs.get("schema") or getattr(cls, "schema", None)
    if schema:
        info["schema"] = schema
    stream = attrs.get("stream") or getattr(cls, "stream", None)
    if stream:
        info["stream"] = stream
    plot = attrs.get("plot") or getattr(cls, "plot", None)
    if plot:
        info["plot"] = plot
    realtime = attrs.get("realtime")
    if realtime is None:
        realtime = bool(getattr(cls, "realtime", False) or stream)
    if realtime:
        info["realtime"] = True
    info["address"] = ADDRESS_TEMPLATE
    return info


def _named_entry(name: str) -> PluginEntry | None:
    key = str(name).strip().lower()
    for entry in get_registry().get_by_category("artifact"):
        shortcuts = {str(s).lower() for s in entry.shortcuts}
        if key in shortcuts or entry.urn.lower() == _urn(name).lower():
            return entry
    return None


def type_info(name: str) -> Dict[str, Any]:
    """Return path/format/description for one artifact type, or ``{}``."""
    entry = _named_entry(name)
    return _info_from_entry(entry) if entry is not None else {}


def list_artifact_types() -> Dict[str, Dict[str, Any]]:
    """Return artifact types declared in library manifests."""
    items: Dict[str, Dict[str, Any]] = {}
    for entry in get_registry().get_by_category("artifact"):
        name = str(entry.shortcuts[0]) if entry.shortcuts else entry.urn.rsplit(".", 1)[-1]
        if name:
            items[name] = _info_from_entry(entry)
    return dict(sorted(items.items()))


def register_artifact_type(
    name: str,
    path: str,
    format: str = "json",
    description: str = "",
    schema: Optional[str] = None,
    stream: Optional[str] = None,
    plot: Optional[str] = None,
    realtime: bool = False,
) -> None:
    """Register an artifact type in this process.

    Libraries declare types in ``library.yaml`` (``type: artifact``).
    Call this to add a type without a manifest (tests).
    """
    attrs: dict[str, Any] = {
        "plugin_type": "artifact",
        "path": path,
        "format": format,
    }
    if schema:
        attrs["schema"] = schema
    if stream:
        attrs["stream"] = stream
    if plot:
        attrs["plot"] = plot
    if realtime or stream:
        attrs["realtime"] = True
    register_plugin(
        _urn(name),
        ArtifactType,
        shortcuts=[name],
        description=description,
        attributes=attrs,
    )
