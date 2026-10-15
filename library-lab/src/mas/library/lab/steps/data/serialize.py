#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""SerializeStep — generic artifact encoding step.

Encodes an artifact produced by a previous step and writes it to a target
store using the registered :class:`~mas.lab.benchmark.codecs.base.Codec`
for ``(artifact_kind, store.type)``.

The codec to use is resolved from the global registry — no hard-coded mapping.
External packages register their codecs by calling
:func:`~mas.lab.benchmark.codecs.register_codec` at import time.

Config keys
-----------
artifact_kind : str
    Logical artifact kind (e.g. ``"otel_traces"``, ``"kg"``, ``"events"``).
source_step : str
    Name of the step whose output contains the artifact to encode.
source_key : str, optional
    Key within the source step's ``data`` dict.  When omitted the step's
    entire ``data`` dict is passed to the codec.
store : dict
    Inline store connection spec (see :class:`~mas.lab.infra.datastore.DatastoreSpec`).
    Required keys depend on the store type.  Example::

        store:
          type: clickhouse
          host: localhost
          port: 8123
          user: admin
          password_env: CLICKHOUSE_PASSWORD
          database: default
opts : dict, optional
    Extra keyword arguments forwarded to :meth:`~mas.lab.benchmark.codecs.base.Codec.encode`.

Example YAML::

    - type: serialize
      name: push_traces
      depends_on: [export_otel]
      config:
        artifact_kind: otel_traces
        source_step: export_otel
        source_key: spans
        store:
          type: clickhouse
          host: localhost
          port: 8123
          user: admin
          password_env: CLICKHOUSE_PASSWORD
          database: default
"""


import logging
from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.codecs import get_codec
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, ConfigParam
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.lab.benchmark.pipeline.schema_validation import validate_payload
from mas.lab.infra.datastore import DatastoreSpec

logger = logging.getLogger(__name__)


class SerializeStep(PipelineStep):
    """Encode an artifact from a previous step and write it to a store.

    Dispatches to the registered :class:`~mas.lab.benchmark.codecs.base.Codec`
    for the declared ``artifact_kind`` and the store's ``type``.

    Store resolution order
    ----------------------
    1. ``store:`` dict  — inline spec (all fields explicit)
    2. ``store_name:``  — named store from the workspace infra manifest
                          (``config/infra/*.yaml``, ``kind: Datastore``)
    3. ``infra:``       — experiment infra bundle name; auto-selects the store
                          that declares ``artifact_kind`` under ``artifacts:``.
    Exactly one of the three must be provided.
    """

    type: str = "serialize"

    PARAMS = [
        ConfigParam("artifact_kind", str,
                    description="Logical artifact kind (e.g. 'otel_traces', 'kg')."),
        ConfigParam("source_step", str,
                    description="Name of the dependency step that produced the artifact."),
        ConfigParam("source_key", str, default=None,
                    description="Key in the source step's data dict.  "
                                "If omitted, the whole data dict is passed."),
        ConfigParam("store", dict, default=None,
                    description="Inline DatastoreSpec: type, host/uri, port, user, "
                                "password_env, database, path."),
        ConfigParam("store_name", str, default=None,
                    description="Named store ID from the workspace infra manifest "
                                "(config/infra/*.yaml).  Alternative to inline 'store:'."),
        ConfigParam("infra", str, default=None,
                    description="Experiment infra bundle name (e.g. 'local', 'prod'). "
                                "Auto-selects the store matching artifact_kind+write from "
                                "<experiment_dir>/infra/<name>.yaml."),
        ConfigParam("opts", dict, default=None,
                    description="Extra keyword arguments forwarded to Codec.encode()."),
        ConfigParam("artifact_schema", dict, default=None,
                description="Optional JSON Schema (inline dict or file path) "
                    "used to validate the artifact payload before encoding."),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        artifact_kind: str = self.config["artifact_kind"]
        source_step: str = self.config["source_step"]
        source_key: str | None = self.config.get("source_key")
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
            op="write",
            ctx=ctx,
        )
        codec_cls = get_codec(artifact_kind, store.type)
        codec = codec_cls(store)

        dep_data = ctx.get_dependency_output(source_step)
        artifact = dep_data[source_key] if source_key else dep_data

        validate_payload(
            artifact,
            artifact_schema,
            label=f"SerializeStep '{self.name}' artifact '{artifact_kind}'",
            base_dir=schema_base_dir,
        )

        logger.info(
            "[%s] encoding %r → %s store",
            self.name, artifact_kind, store.type,
        )
        streamed = encode_streaming(codec, artifact, extra_opts)

        return StepOutput(
            data={"artifact_kind": artifact_kind, "store_type": store.type, "streamed": streamed},
            metadata={"codec": type(codec).__name__, "streamed": streamed},
        )


def _jsonl_path(artifact: Any) -> Path | None:
    if isinstance(artifact, (str, Path)):
        path = Path(artifact)
        if path.is_file() and path.suffix in {".jsonl", ".ndjson"}:
            return path
    if isinstance(artifact, dict):
        for key in ("path", "jsonl", "events_path", "spans_path", "df_path"):
            raw = artifact.get(key)
            if raw:
                path = Path(str(raw))
                if path.is_file() and path.suffix in {".jsonl", ".ndjson"}:
                    return path
    return None


def _iter_channel_records(artifact: Any):
    stream_id = None
    if isinstance(artifact, str) and "/" in artifact and "#" in artifact:
        stream_id = artifact
    if isinstance(artifact, dict):
        stream_id = artifact.get("stream_id")
    if not stream_id:
        return None
    try:
        from mas.lab.benchmark.pipeline.channel import get_channel, native_body
    except Exception:
        return None
    channel = get_channel()
    replay = getattr(channel, "replay", None) if channel is not None else None
    if replay is None:
        return None
    frames = replay(stream_id)
    if not frames:
        return None
    return (native_body(frame) for frame in frames)


def encode_streaming(codec: Any, artifact: Any, extra_opts: dict[str, Any]) -> bool:
    """Encode from a JSONL path or live stream when possible; else whole artifact.

    Streaming is the large-file path: batches of records, no ``read_text``.
    """
    batch_size = max(1, int(extra_opts.get("batch_size") or 256))
    path = _jsonl_path(artifact)
    if path is not None:
        from mas.library.standard.lib.jsonl_iter import iter_jsonl_records

        def _records():
            for batch in iter_jsonl_records(path, batch_size=batch_size):
                yield from batch

        codec.encode_iter(_records(), **extra_opts)
        return True
    live = _iter_channel_records(artifact)
    if live is not None:
        codec.encode_iter(live, **extra_opts)
        return True
    if isinstance(artifact, list):
        codec.encode_iter(artifact, **extra_opts)
        return True
    codec.encode(artifact, **extra_opts)
    return False


# ---------------------------------------------------------------------------
# Shared store-resolution helpers (used by DeserializeStep too)
# ---------------------------------------------------------------------------

def _resolve_store(
    step_name: str,
    config: dict[str, Any],
    artifact_kind: str,
    op: str,
    ctx: Any,
) -> DatastoreSpec:
    """Resolve a :class:`DatastoreSpec` from step config.

    Resolution order: ``store:`` (inline) → ``store_name:`` (workspace manifest
    lookup) → ``infra:`` (experiment bundle auto-match by artifact_kind + op).
    """
    store_cfg: Optional[dict] = config.get("store")
    store_name: Optional[str] = config.get("store_name")
    infra_name: Optional[str] = config.get("infra")

    if store_cfg:
        return _build_store(store_cfg)

    if store_name:
        return _load_named_store(step_name, store_name)

    if infra_name:
        return _load_bundle_store(step_name, infra_name, artifact_kind, op, ctx)

    raise ValueError(
        f"Step '{step_name}': provide 'store' (inline), 'store_name' (workspace "
        f"infra lookup), or 'infra' (bundle auto-select) in config."
    )


def _build_store(cfg: dict[str, Any]) -> DatastoreSpec:
    """Build a :class:`DatastoreSpec` from an inline step config dict."""
    return DatastoreSpec(
        type=cfg.get("type", ""),
        uri=cfg.get("uri", ""),
        host=cfg.get("host", ""),
        port=int(cfg.get("port", 0)),
        path=cfg.get("path", ""),
        user=cfg.get("user", ""),
        password_env=cfg.get("password_env", ""),
        database=cfg.get("database", ""),
        description=cfg.get("description", ""),
    )


def _load_named_store(step_name: str, store_name: str) -> DatastoreSpec:
    """Load a :class:`DatastoreSpec` by ID from the workspace infra manifest."""
    from mas.lab.connections import _load_infra_store
    spec = _load_infra_store(store_name)
    if spec is None:
        raise ValueError(
            f"Step '{step_name}': store '{store_name}' not found in workspace "
            "infra manifest (config/infra/*.yaml)."
        )
    return spec


def _load_bundle_store(
    step_name: str,
    infra_name: str,
    artifact_kind: str,
    op: str,
    ctx: Any,
) -> DatastoreSpec:
    """Load the first store from an experiment infra bundle that handles
    *artifact_kind* with *op* (``'read'`` or ``'write'``)."""
    import yaml
    config_path: Optional[Path] = getattr(
        getattr(ctx, "pipeline", None), "config_path", None
    )
    exp_dir = config_path.parent if config_path else Path(".")
    bundle_path = exp_dir / "infra" / f"{infra_name}.yaml"

    if not bundle_path.exists():
        # walk up to workspace root
        for parent in [exp_dir, *exp_dir.parents]:
            candidate = parent / "infra" / f"{infra_name}.yaml"
            if candidate.exists():
                bundle_path = candidate
                break
        else:
            raise FileNotFoundError(
                f"Step '{step_name}': infra bundle '{infra_name}.yaml' not found "
                f"under {exp_dir} or any parent directory."
            )

    with bundle_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    stores_raw: dict = (data or {}).get("spec", {}).get("stores", {})
    for store_id, s in stores_raw.items():
        for artifact in s.get("artifacts", []):
            if artifact.get("kind") == artifact_kind and op in artifact.get("ops", []):
                cfg = dict(s)
                cfg["name"] = store_id
                return _build_store(cfg)

    raise ValueError(
        f"Step '{step_name}': no store in bundle '{infra_name}' handles "
        f"artifact_kind='{artifact_kind}' with op='{op}'."
    )
