#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""DeserializeStep — generic artifact decoding step.

Reads an artifact from a store using the registered
:class:`~mas.lab.benchmark.codecs.base.Codec` for
``(artifact_kind, store.type)`` and makes it available to downstream steps.

Config keys
-----------
artifact_kind : str
    Logical artifact kind (e.g. ``"otel_traces"``, ``"kg"``, ``"events"``).
output_key : str, optional
    Key under which the decoded artifact is stored in the step output's
    ``data`` dict.  Defaults to the value of ``artifact_kind``.
store : dict
    Inline store connection spec.  Same structure as for :class:`SerializeStep`.
opts : dict, optional
    Extra keyword arguments forwarded to :meth:`~mas.lab.benchmark.codecs.base.Codec.decode`.

Example YAML::

    - type: deserialize
      name: load_kg
      config:
        artifact_kind: kg
        output_key: graph
        store:
          type: neo4j
          uri: bolt://localhost:7687
          user: neo4j
          password_env: NEO4J_PASSWORD
          database: neo4j
        opts:
          session_id: "{session_id}"
"""


import logging
from pathlib import Path
from typing import Any

from mas.lab.benchmark.codecs import get_codec
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, ConfigParam
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.schema_validation import validate_payload
from mas.library.lab.steps.data.serialize import _iter_channel_records, _jsonl_path, _resolve_store

logger = logging.getLogger(__name__)


def decode_streaming(codec: Any, extra_opts: dict[str, Any]) -> Any:
    """Prefer a live channel or JSONL file; fall back to ``codec.decode``."""
    live = _iter_channel_records(extra_opts)
    if live is not None:
        return list(live)
    path = _jsonl_path(extra_opts) or _jsonl_path(extra_opts.get("path") or extra_opts.get("source") or "")
    if path is not None:
        from mas.library.standard.lib.jsonl_iter import iter_jsonl_records

        rows: list[dict] = []
        for batch in iter_jsonl_records(path, batch_size=int(extra_opts.get("batch_size") or 256)):
            rows.extend(batch)
        return rows
    return codec.decode(**extra_opts)


class DeserializeStep(PipelineStep):
    """Decode an artifact from a store and expose it to downstream steps.

    Dispatches to the registered :class:`~mas.lab.benchmark.codecs.base.Codec`
    for the declared ``artifact_kind`` and the store's ``type``.
    """

    type: str = "deserialize"

    PARAMS = [
        ConfigParam("artifact_kind", str,
                    description="Logical artifact kind (e.g. 'otel_traces', 'kg')."),
        ConfigParam("output_key", str, default=None,
                    description="Key for the decoded artifact in the output data dict.  "
                                "Defaults to artifact_kind."),
        ConfigParam("store", dict, default=None,
                    description="Inline DatastoreSpec: type, host/uri, port, user, "
                                "password_env, database, path."),
        ConfigParam("store_name", str, default=None,
                    description="Named store ID from the workspace infra manifest. "
                                "Alternative to inline 'store:'."),
        ConfigParam("infra", str, default=None,
                    description="Experiment infra bundle name. Auto-selects store "
                                "matching artifact_kind+read."),
        ConfigParam("opts", dict, default=None,
                    description="Extra keyword arguments forwarded to Codec.decode()."),
        ConfigParam("artifact_schema", dict, default=None,
                description="Optional JSON Schema (inline dict or file path) "
                    "used to validate the decoded artifact."),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        artifact_kind: str = self.config["artifact_kind"]
        output_key: str = self.config.get("output_key") or artifact_kind
        extra_opts: dict[str, Any] = self.config.get("opts") or {}
        artifact_schema: Any = self.config.get("artifact_schema")

        schema_base_dir = (
            ctx.pipeline.config_path.parent
            if getattr(ctx.pipeline, "config_path", None)
            else Path.cwd()
        )

        store = _resolve_store(
            self.name,
            config=self.config,
            artifact_kind=artifact_kind,
            op="read",
            ctx=ctx,
        )
        codec_cls = get_codec(artifact_kind, store.type)
        codec = codec_cls(store)

        logger.info(
            "[%s] decoding %r ← %s store",
            self.name, artifact_kind, store.type,
        )
        artifact = decode_streaming(codec, extra_opts)

        validate_payload(
            artifact,
            artifact_schema,
            label=f"DeserializeStep '{self.name}' artifact '{artifact_kind}'",
            base_dir=schema_base_dir,
        )

        return StepOutput(
            data={output_key: artifact},
            metadata={"codec": type(codec).__name__, "artifact_kind": artifact_kind},
        )
