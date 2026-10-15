#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed artifact streams: value / records / multi; append / upsert / snapshot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Literal

from mas.lab.benchmark.pipeline.channel import StreamFrame, TelemetryChannel, get_channel
from mas.lab.benchmark.pipeline.digest import rolling_digest
from mas.lab.benchmark.pipeline.keys import RunKey

logger = logging.getLogger(__name__)

Shape = Literal["value", "records", "multi"]
Mode = Literal["append", "upsert", "snapshot"]
OnInvalid = Literal["error", "drop", "quarantine"]


class ArtifactError(RuntimeError):
    def __init__(self, message: str, *, offset: int | None = None, path: str = "") -> None:
        super().__init__(message)
        self.offset = offset
        self.path = path


@dataclass
class ArtifactType:
    name: str
    shape: Shape = "value"
    mode: Mode = "append"
    version: int = 1
    schema: dict[str, Any] | None = None
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    columns: list[dict[str, Any]] = field(default_factory=list)
    key: list[str] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    on_invalid: OnInvalid = "error"
    persist: str = "auto"


_REGISTRY: dict[str, ArtifactType] = {}
_VALIDATORS: dict[int, Any] = {}


def register_artifact_type(spec: ArtifactType) -> ArtifactType:
    _REGISTRY[spec.name] = spec
    return spec


def get_artifact_type(name: str) -> ArtifactType:
    if name in _REGISTRY:
        return _REGISTRY[name]
    return ArtifactType(name=name, shape="value")


def _as_batch(rec_or_batch: Any) -> list[dict[str, Any]]:
    if rec_or_batch is None:
        return []
    if isinstance(rec_or_batch, dict):
        return [rec_or_batch]
    if hasattr(rec_or_batch, "to_dict"):
        try:
            rows = rec_or_batch.to_dict(orient="records")
            return [dict(row) for row in rows]
        except Exception:
            pass
    return [dict(item) for item in rec_or_batch]


def _validator_for(schema: dict[str, Any]):
    try:
        import jsonschema
    except ImportError:
        return None
    cached = _VALIDATORS.get(id(schema))
    if cached is None:
        cached = jsonschema.Draft7Validator(schema)
        _VALIDATORS[id(schema)] = cached
    return cached


def _schema_for(spec: ArtifactType, record: dict[str, Any]) -> dict[str, Any] | None:
    schema = spec.schema
    kind = str(record.get("kind") or "")
    if spec.shape == "multi" and kind in spec.records:
        schema = spec.records[kind].get("schema") or schema
    return schema


def _validate_record(spec: ArtifactType, record: dict[str, Any], offset: int) -> dict[str, Any] | None:
    schema = _schema_for(spec, record)
    if not schema:
        return record
    validator = _validator_for(schema)
    if validator is None:
        return record
    error = next(validator.iter_errors(record), None)
    if error is None:
        return record
    exc = error.message
    path = ".".join(str(p) for p in error.absolute_path) or str(exc)
    if spec.on_invalid == "drop":
        logger.warning("dropping invalid %s record at %s: %s", spec.name, offset, exc)
        return None
    if spec.on_invalid == "quarantine":
        return {"_quarantine": True, "offset": offset, "error": str(exc), "record": record}
    raise ArtifactError(f"{spec.name} record {offset}: {exc}", offset=offset, path=path) from None


def validate_batch(
    spec: ArtifactType, records: list[dict[str, Any]], *, start_offset: int = 0
) -> list[dict[str, Any] | None]:
    """Validate a batch with one compiled schema validator (vectorized over the list)."""
    return [_validate_record(spec, rec, start_offset + i) for i, rec in enumerate(records)]


@dataclass
class Artifact:
    name: str
    key: RunKey
    spec: ArtifactType
    channel: TelemetryChannel | None = None
    _records: list[dict[str, Any]] = field(default_factory=list)
    _digest: str = ""
    _sealed: bool = False
    _offset: int = 0
    _latest: dict[str, Any] | None = None
    _by_key: dict[tuple[Any, ...], dict[str, Any]] = field(default_factory=dict)

    @property
    def stream_id(self) -> str:
        return self.key.artifact_uri(self.name)

    def _chan(self) -> TelemetryChannel | None:
        return self.channel or get_channel()

    def _append_records(self, records: list[dict[str, Any]]) -> None:
        if self._sealed:
            raise ArtifactError(f"artifact {self.name} is sealed")
        for checked in validate_batch(self.spec, records, start_offset=self._offset):
            if checked is None:
                continue
            if checked.get("_quarantine"):
                self._records.append(checked)
                self._offset += 1
                continue
            self._digest = rolling_digest(self._digest, checked)
            if self.spec.mode == "snapshot":
                self._latest = checked
                self._records = [checked]
            elif self.spec.mode == "upsert":
                key_fields = self.spec.key or self.spec.records.get(str(checked.get("kind") or ""), {}).get("key") or []
                k = tuple(checked.get(f) for f in key_fields) if key_fields else (self._offset,)
                self._by_key[k] = checked
                self._records = list(self._by_key.values())
            else:
                self._records.append(checked)
            chan = self._chan()
            if chan is not None:
                payload = {
                    "kind": f"artifact.{self.spec.mode}",
                    "artifact": self.name,
                    "record": checked,
                }
                try:
                    chan.publish_sync(self.stream_id, payload)
                except Exception:
                    logger.debug("artifact publish failed", exc_info=True)
            self._offset += 1

    def append(self, rec_or_batch: Any) -> None:
        self._append_records(_as_batch(rec_or_batch))

    def upsert(self, rec_or_batch: Any) -> None:
        self._append_records(_as_batch(rec_or_batch))

    def snapshot(self, rec: Any) -> None:
        batch = _as_batch(rec)
        if not batch:
            return
        self._append_records([batch[-1]])

    def seal(self) -> str:
        self._check_invariants()
        self._sealed = True
        chan = self._chan()
        if chan is not None:
            try:
                chan.seal_sync(self.stream_id)
            except Exception:
                logger.debug("artifact seal channel failed", exc_info=True)
        return self._digest

    def _check_invariants(self) -> None:
        for name in self.spec.invariants:
            if name == "edges_reference_nodes":
                nodes = {r.get("id") for r in self._records if r.get("kind") == "node"}
                for rec in self._records:
                    if rec.get("kind") != "edge":
                        continue
                    if rec.get("src") not in nodes or rec.get("dst") not in nodes:
                        raise ArtifactError(f"{self.name}: dangling edge {rec}")
        if self.spec.key:
            seen: set[tuple[Any, ...]] = set()
            for rec in self._records:
                k = tuple(rec.get(f) for f in self.spec.key)
                if k in seen and self.spec.mode == "append":
                    raise ArtifactError(f"{self.name}: duplicate key {k}")
                seen.add(k)

    @property
    def digest(self) -> str:
        return self._digest

    @property
    def sealed(self) -> bool:
        return self._sealed

    def latest(self) -> dict[str, Any] | None:
        if self.spec.mode == "snapshot":
            return self._latest
        return self._records[-1] if self._records else None

    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    async def batches(self, size: int = 64) -> AsyncIterator[list[dict[str, Any]]]:
        batch: list[dict[str, Any]] = []
        for rec in self._records:
            batch.append(rec)
            if len(batch) >= size:
                yield batch
                batch = []
        if batch:
            yield batch

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for rec in self._records:
            yield rec

    def materialize(self) -> list[dict[str, Any]]:
        if not self._sealed and self.spec.shape != "snapshot":
            raise ArtifactError(f"{self.name} is not sealed")
        return list(self._records)

    def to_pandas(self):
        import pandas as pd

        return pd.DataFrame(self._records)


@dataclass
class ArtifactContext:
    """Step-facing ``ctx.input`` / ``ctx.output``."""

    key: RunKey
    outputs: dict[str, Artifact] = field(default_factory=dict)
    inputs: dict[str, Artifact] = field(default_factory=dict)
    channel: TelemetryChannel | None = None

    def output(self, name: str, spec: ArtifactType | None = None) -> Artifact:
        if name not in self.outputs:
            self.outputs[name] = Artifact(
                name=name,
                key=self.key,
                spec=spec or get_artifact_type(name),
                channel=self.channel,
            )
        return self.outputs[name]

    def input(self, name: str) -> Artifact:
        if name not in self.inputs:
            raise ArtifactError(f"no input artifact named {name!r}")
        return self.inputs[name]


def plan_time_check(edges: list[tuple[ArtifactType, ArtifactType]]) -> list[str]:
    """Warn (not error) until existing artifacts are fully typed."""
    warnings: list[str] = []
    for producer, consumer in edges:
        if producer.name != consumer.name and producer.shape != consumer.shape:
            warnings.append(
                f"shape mismatch {producer.name}:{producer.shape} -> {consumer.name}:{consumer.shape}"
            )
        if consumer.version and producer.version and consumer.version > producer.version:
            warnings.append(
                f"version {producer.name} v{producer.version} not accepted by consumer v{consumer.version}"
            )
    return warnings


def wrap_step_output(data: Any, files: list | None, key: RunKey, name: str = "value") -> Artifact:
    base = get_artifact_type(name)
    spec = ArtifactType(
        name=base.name,
        shape="records" if hasattr(data, "to_dict") and not isinstance(data, dict) else base.shape,
        mode=base.mode,
        version=base.version,
        persist=base.persist,
    )
    art = Artifact(name=name, key=key, spec=spec)
    if hasattr(data, "to_dict") and not isinstance(data, dict):
        art.append(data)
    elif isinstance(data, dict):
        art.append(data)
    elif isinstance(data, list):
        art.append(data)
    art.seal()
    return art
