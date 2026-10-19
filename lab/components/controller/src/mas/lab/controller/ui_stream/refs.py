#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ID-only DataSourceRef, topic matching, and event envelopes."""

from __future__ import annotations

import fnmatch
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

_clock: ContextVar[str | None] = ContextVar("mas_ui_clock", default=None)
_nonce: ContextVar[str | None] = ContextVar("mas_ui_nonce", default=None)


def use_clock(value: str | None):
    """Stamp envelopes with a clock owner (``server`` = DES owns timing)."""
    return _clock.set(value)


def use_nonce(value: str | None):
    """Stamp envelopes with the UI registration nonce for this channel."""
    return _nonce.set(value)


def reset_clock(token) -> None:
    _clock.reset(token)


def reset_nonce(token) -> None:
    _nonce.reset(token)

Mode = Literal["live", "replay", "historical"]
Source = Literal[
    "command",
    "result",
    "control",
    "native",
    "telemetry",
    "metric",
    "job",
    "catalog",
    "spec",
    "artifact",
    "io",
    "governance",
    "custom",
    "view",
]

STREAMS = {
    "spec": "spec",
    "observability": "native",
    "hitl": "control",
    "artifacts": "artifact",
    "metrics": "metric",
    "governance": "governance",
}


@dataclass
class DataSourceRef:
    library: str | None = None
    experiment: str | None = None
    scenario: str | None = None
    test: str | None = None
    run: str | None = None
    session: str | None = None
    overlay: str | None = None
    batch: str | None = None
    origin: str | None = None
    mode: Mode | None = None

    def merge(self, override: DataSourceRef | dict[str, Any] | None) -> DataSourceRef:
        if override is None:
            return DataSourceRef(**asdict(self))
        data = asdict(self)
        extra = asdict(override) if isinstance(override, DataSourceRef) else dict(override)
        for key, value in extra.items():
            if key not in data:
                continue
            if value is not None and value != "":
                data[key] = value
        return DataSourceRef(**data)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def matches(self, other: DataSourceRef) -> bool:
        for key in ("library", "experiment", "test", "run", "session", "overlay", "batch"):
            mine = getattr(self, key)
            theirs = getattr(other, key)
            if mine and theirs and mine != theirs:
                return False
        # A panel that binds a scenario must not also paint unscoped or other-lane events.
        if self.scenario and self.scenario != (other.scenario or ""):
            return False
        return True

    def missing_for(self, required: tuple[str, ...]) -> list[str]:
        return [name for name in required if not getattr(self, name)]

    def coordinate(self) -> str:
        return "|".join(
            [
                self.library or "",
                self.experiment or "",
                self.scenario or "",
                self.test or "",
                self.run or "",
                self.session or "",
            ]
        )


def envelope_from_mapping(data: dict[str, Any] | None) -> EventEnvelope:
    row = data or {}
    source = row.get("source") or "native"
    allowed = {
        "command",
        "result",
        "control",
        "native",
        "telemetry",
        "metric",
        "job",
        "catalog",
        "spec",
        "artifact",
        "io",
        "governance",
        "custom",
        "view",
    }
    if source not in allowed:
        source = "custom" if str(row.get("topic") or "").startswith("custom.") else "native"
    return envelope(
        str(row.get("topic") or "native.unknown"),
        ref_from_mapping(row.get("ref") if isinstance(row.get("ref"), dict) else {}),
        source,  # type: ignore[arg-type]
        row.get("payload"),
        replay=bool(row.get("replay", True)),
        event_id=row.get("id"),
        ts=row.get("ts") if isinstance(row.get("ts"), str) else None,
        clock=row.get("clock") or "server",
        complete=row.get("complete") if isinstance(row.get("complete"), bool) else None,
        artefact=str(row.get("artefact") or "") or None,
        uri=str(row.get("uri") or "") or None,
        path=str(row.get("path") or "") or None,
        inputs=list(row.get("inputs") or []) or None,
        outputs=list(row.get("outputs") or []) or None,
        nonce=str(row["nonce"]) if row.get("nonce") else None,
    )


def ref_from_mapping(data: dict[str, Any] | None) -> DataSourceRef:
    if not data:
        return DataSourceRef()
    allowed = {f.name for f in DataSourceRef.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in allowed and v not in (None, "")}
    return DataSourceRef(**filtered)


@dataclass
class EventEnvelope:
    topic: str
    ts: str
    ref: DataSourceRef
    source: Source
    payload: Any = None
    replay: bool = False
    id: str | None = None
    clock: str | None = None
    complete: bool | None = None
    artefact: str | None = None
    uri: str | None = None
    path: str | None = None
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    nonce: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {
            "topic": self.topic,
            "ts": self.ts,
            "ref": self.ref.as_dict(),
            "source": self.source,
            "payload": self.payload,
            "replay": self.replay,
            "id": self.id,
        }
        if self.clock:
            data["clock"] = self.clock
        if self.artefact:
            data["artefact"] = self.artefact
        if self.uri:
            data["uri"] = self.uri
        if self.path:
            data["path"] = self.path
        if self.inputs:
            data["inputs"] = list(self.inputs)
        if self.outputs:
            data["outputs"] = list(self.outputs)
        if self.complete is not None:
            data["complete"] = self.complete
        if self.nonce:
            data["nonce"] = self.nonce
        return data


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def topic_matches(declared: list[str] | tuple[str, ...], topic: str) -> bool:
    if not declared:
        return True
    return any(fnmatch.fnmatchcase(topic, glob) for glob in declared)


def envelope(
    topic: str,
    ref: DataSourceRef,
    source: Source,
    payload: Any,
    *,
    replay: bool = False,
    event_id: str | None = None,
    ts: str | None = None,
    clock: str | None = None,
    complete: bool | None = None,
    artefact: str | None = None,
    uri: str | None = None,
    path: str | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    nonce: str | None = None,
) -> EventEnvelope:
    from mas.lab.controller.ui_stream.artefact_bind import link_for_topic

    link = link_for_topic(topic, ref.as_dict(), complete=complete)
    return EventEnvelope(
        topic=topic,
        ts=ts or now_iso(),
        ref=ref,
        source=source,
        payload=payload,
        replay=replay,
        id=event_id,
        clock=clock or _clock.get(),
        complete=complete if complete is not None else link.complete,
        artefact=artefact or link.name,
        uri=uri or link.uri,
        path=path or link.path,
        inputs=list(inputs) if inputs is not None else list(link.inputs),
        outputs=list(outputs) if outputs is not None else list(link.outputs),
        nonce=nonce if nonce is not None else _nonce.get(),
    )
