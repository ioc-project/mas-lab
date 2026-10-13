#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Optional persistence sinks for sealed artifacts (jsonl/json/csv/parquet)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline.artifact import Artifact
from mas.lab.benchmark.pipeline.digest import rolling_digest


def digest_of_records(records: list[dict[str, Any]]) -> str:
    digest = ""
    for rec in records:
        digest = rolling_digest(digest, rec)
    return digest


def persist_artifact(art: Artifact, dest: Path, fmt: str | None = None) -> Path | None:
    if art.spec.persist == "never":
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or dest.suffix.lstrip(".") or "jsonl").lower()
    records = art.records()
    if fmt in {"json"}:
        payload: Any = records[0] if art.spec.shape == "value" and records else records
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return dest
    if fmt in {"csv"}:
        if not records:
            dest.write_text("", encoding="utf-8")
            return dest
        fieldnames = sorted({k for rec in records for k in rec.keys()})
        with dest.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        return dest
    if fmt in {"parquet"}:
        try:
            frame = art.to_pandas()
            frame.to_parquet(dest, index=False)
            return dest
        except Exception:
            fmt = "jsonl"
    with dest.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return dest


def spill_over_bound(art: Artifact, bound: int, dest: Path) -> Path | None:
    """Persist when in-memory records exceed ``bound``. Digest is unchanged."""
    if len(art.records()) <= bound:
        return None
    return persist_artifact(art, dest)
