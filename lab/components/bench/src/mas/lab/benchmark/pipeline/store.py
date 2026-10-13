#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Content-addressed artefact store. Writes are tmp+rename; manifest last."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline.counters import counters
from mas.lab.benchmark.pipeline.digest import node_key


@dataclass
class StoreManifest:
    key: str
    step_type: str
    version: str
    config: dict[str, Any]
    input_digests: list[str]
    output_digest: str = ""
    producer: str = "pipeline"
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "step_type": self.step_type,
            "version": self.version,
            "config": self.config,
            "input_digests": self.input_digests,
            "output_digest": self.output_digest,
            "producer": self.producer,
            **self.extra,
        }


def compute_node_key(step_type: str, version: str, config: dict[str, Any], *input_digests: str) -> str:
    return node_key(step_type, version, config, list(input_digests))


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / key

    def exists(self, key: str) -> bool:
        return (self.path_for(key) / "manifest.json").is_file()

    def get(self, key: str) -> StoreManifest | None:
        path = self.path_for(key) / "manifest.json"
        if not path.is_file():
            counters().store_misses += 1
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            counters().store_misses += 1
            return None
        counters().store_hits += 1
        return StoreManifest(
            key=data.get("key", key),
            step_type=data.get("step_type", ""),
            version=data.get("version", ""),
            config=data.get("config") or {},
            input_digests=list(data.get("input_digests") or []),
            output_digest=data.get("output_digest", ""),
            producer=data.get("producer", "pipeline"),
            extra={k: v for k, v in data.items() if k not in StoreManifest.__dataclass_fields__},
        )

    def put(self, manifest: StoreManifest, files: dict[str, Path | bytes] | None = None) -> Path:
        dest = self.path_for(manifest.key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="artefact-", dir=str(dest.parent)))
        try:
            for name, payload in (files or {}).items():
                target = tmp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(payload, (bytes, bytearray)):
                    target.write_bytes(payload)
                else:
                    src = Path(payload)
                    if src.is_file():
                        shutil.copy2(src, target)
            (tmp / "manifest.json").write_text(
                json.dumps(manifest.as_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(tmp, dest)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return dest

    def link_into(self, key: str, dest: Path) -> bool:
        src = self.path_for(key)
        if not (src / "manifest.json").is_file():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name == "manifest.json":
                continue
            target = dest / item.name
            try:
                os.link(item, target)
            except OSError:
                try:
                    target.symlink_to(item)
                except OSError:
                    shutil.copy2(item, target)
        return True
