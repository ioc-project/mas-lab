#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Codec lookup — coder/decoder pairs for artifact kinds and store types.

This is a thin wrapper over ``mas.runtime.registry`` — there is no
bench-local codec catalog. Codec *implementations* are library plugins
(see ``library-lab/library.yaml``'s ``types:``/``plugins:`` block); bench only defines
the :class:`~mas.lab.benchmark.codecs.base.Codec` contract and looks
codecs up by ``(artifact_kind, store_type)``.

Usage
-----
To register a codec programmatically (declarative manifest registration
via a library's ``*.plugins.yaml`` is preferred — see
``runtime/docs/plugin-registry-manifests.md``)::

    from mas.lab.benchmark.codecs import register_codec
    from mas.lab.benchmark.codecs.base import Codec

    @register_codec
    class ClickHouseOtelCodec(Codec):
        artifact_kind = "otel_traces"
        store_type    = "clickhouse"
        ...

To look up a codec at runtime::

    from mas.lab.benchmark.codecs import get_codec
    codec_cls = get_codec("otel_traces", "clickhouse")
    codec = codec_cls(store_spec)
    codec.encode(artifact)
"""

from __future__ import annotations

from mas.lab.benchmark.codecs.base import Codec
from mas.runtime.registry import get_registry, register_plugin

__all__ = ["Codec", "register_codec", "get_codec", "list_codecs"]


def _codec_name(artifact_kind: str, store_type: str) -> str:
    return f"{artifact_kind}-{store_type}".strip().lower().replace(" ", "_").replace(".", "_")


def _codec_urn(artifact_kind: str, store_type: str) -> str:
    return f"mas.codec.{_codec_name(artifact_kind, store_type).replace('-', '_')}"


def register_codec(codec_class: type[Codec]) -> type[Codec]:
    """Register a codec by its declared ``artifact_kind`` and ``store_type``."""
    register_plugin(
        _codec_urn(codec_class.artifact_kind, codec_class.store_type),
        codec_class,
        shortcuts=[_codec_name(codec_class.artifact_kind, codec_class.store_type)],
        attributes={
            "plugin_type": "codec",
            "artifact_kind": codec_class.artifact_kind,
            "store_type": codec_class.store_type,
        },
    )
    return codec_class


def get_codec(artifact_kind: str, store_type: str) -> type[Codec]:
    """Retrieve the codec class for a given ``(artifact_kind, store_type)`` pair."""
    info = get_registry().get(
        "codec",
        attributes={"artifact_kind": artifact_kind, "store_type": store_type},
    )
    if info is None:
        raise ValueError(f"No codec registered for ({artifact_kind!r}, {store_type!r}). Available: {list_codecs()}")
    return info.load_class()


def list_codecs() -> list[tuple[str, str]]:
    """Return all discoverable ``(artifact_kind, store_type)`` pairs."""
    keys: set[tuple[str, str]] = set()
    for item in get_registry().list():
        if str(item.get("category") or "") != "codec":
            continue
        attrs = item.get("attributes") or {}
        artifact_kind = str(attrs.get("artifact_kind") or "")
        store_type = str(attrs.get("store_type") or "")
        if artifact_kind and store_type:
            keys.add((artifact_kind, store_type))
    return sorted(keys)
