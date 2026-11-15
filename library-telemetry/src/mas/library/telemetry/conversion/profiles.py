#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Converter profiles for native events -> OTel spans.

``raw`` is the toy round-trip self-test shape (PascalCase span names,
``mas.*``-prefixed attributes only -- see mas.spanspec.yaml). ``observe_sdk``
additionally overlays the real GenAI/ioa-observe vocabulary (``ioa_observe.*``
/ ``traceloop.*`` attributes, real dotted span names like ``{agent}.agent``)
on top of the same ``mas.*`` attributes, so the resulting spans are also
consumable by a real ioa-observe/InsightClaw-vocabulary consumer (e.g.
library-kg's IoaObserveHandler) -- see genai-observe-sdk.spanspec.yaml for
the reference this profile targets.
"""

from __future__ import annotations

from typing import Final, Literal

ConverterProfile = Literal["raw", "observe_sdk"]
DEFAULT_CONVERTER_PROFILE: Final[ConverterProfile] = "raw"


def default_extensions(profile: ConverterProfile, extensions: bool | None) -> bool:
    """Extension suffixes stay off unless the caller opts in.

    Observe-sdk default is session, graph, invoke_agent, ``*.agent``,
    ``*.chat``, ``*.tool``, and ``*.processing`` (ProcessingCall is a
    SCOPE core class). Governance / context / memory / skill / rag spans
    are opt-in via ``extensions=True``.
    """
    del profile
    if extensions is None:
        return False
    return bool(extensions)


def normalize_converter_profile(value: str | None) -> ConverterProfile:
    """Normalize converter profile names and validate allowed values."""
    profile = str(value or DEFAULT_CONVERTER_PROFILE).strip().lower()
    if profile in {"raw", "otel", "opentelemetry"}:
        return "raw"
    if profile in {"observe_sdk", "observe-sdk", "observe"}:
        return "observe_sdk"
    raise ValueError(f"invalid converter profile {value!r}; expected one of: raw, observe_sdk")


__all__ = [
    "ConverterProfile",
    "DEFAULT_CONVERTER_PROFILE",
    "default_extensions",
    "normalize_converter_profile",
]
