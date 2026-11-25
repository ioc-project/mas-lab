#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Neutral message content: a string, or a list of text and media parts.

A message stays a plain ``str`` when it has no media. Never normalize a
string into a list (that changes cache keys). A list appears only when a
part is not text. Bytes live in a media store keyed by sha256; parts hold
only the digest, type, name and size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

# Conservative estimate of one image/file part in prompt-size arithmetic.
# Provider usage is the billable count. Owner may revise this number.
MEDIA_PART_TOKEN_ESTIMATE = 256

_TEXT = "text"
_IMAGE = "image"
_FILE = "file"
_AUDIO = "audio"
_VIDEO = "video"
_MEDIA_TYPES = frozenset({_IMAGE, _FILE})
_RESERVED = frozenset({_AUDIO, _VIDEO})

Content = str | list[Any]
MediaMode = Literal["skip", "placeholder"]

__all__ = [
    "MEDIA_PART_TOKEN_ESTIMATE",
    "Content",
    "ContentPart",
    "MediaMissing",
    "MediaStore",
    "ModalityNotSupported",
    "as_part_dict",
    "check_messages_modalities",
    "check_modalities",
    "content_fingerprint",
    "has_media",
    "media_parts",
    "part_modality",
    "text_chars",
    "text_of",
    "token_estimate_for_content",
]


class ModalityNotSupported(ClassifiedFailure):
    """The model or layer does not accept this input modality. Not retryable."""

    def __init__(
        self,
        message: str = "",
        *,
        model: str | None = None,
        layer: str | None = None,
        modality: str,
    ) -> None:
        who = layer or model or "model"
        text = message or f"{who} does not accept {modality}"
        super().__init__(text, failure_class=FailureClass.FATAL, code="MODALITY_NOT_SUPPORTED")
        self.model = model
        self.layer = layer
        self.modality = modality


class MediaMissing(ClassifiedFailure):
    """The media store has no bytes for this digest. Not retryable."""

    def __init__(self, sha256: str, *, layer: str | None = None) -> None:
        who = f"{layer} " if layer else ""
        super().__init__(
            f"{who}media store missing {sha256}".strip(),
            failure_class=FailureClass.FATAL,
            code="MEDIA_MISSING",
        )
        self.sha256 = sha256
        self.layer = layer


@dataclass(frozen=True)
class ContentPart:
    """One text or media part. JSON-serializable via :meth:`to_dict`."""

    type: str
    text: str | None = None
    media_type: str | None = None
    sha256: str | None = None
    name: str | None = None
    bytes: int | None = None

    @staticmethod
    def text_part(text: str) -> ContentPart:
        return ContentPart(type=_TEXT, text=text)

    @staticmethod
    def media_part(
        *,
        type: str,
        media_type: str,
        sha256: str,
        name: str,
        bytes: int,
    ) -> ContentPart:
        kind = str(type)
        if kind in _RESERVED:
            raise ModalityNotSupported(modality=kind)
        if kind not in _MEDIA_TYPES:
            raise ModalityNotSupported(f"unknown media part type {kind!r}", modality=kind)
        if kind == _FILE and not _is_pdf_media_type(media_type):
            raise ModalityNotSupported("file parts must be PDF", modality="file")
        return ContentPart(
            type=kind,
            media_type=media_type,
            sha256=sha256,
            name=name,
            bytes=bytes,
        )

    def to_dict(self) -> dict[str, Any]:
        if self.type == _TEXT:
            return {"type": _TEXT, "text": self.text or ""}
        return {
            "type": self.type,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "name": self.name,
            "bytes": self.bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentPart:
        kind = str(data.get("type") or _TEXT)
        if kind == _TEXT:
            return cls.text_part(str(data.get("text") or ""))
        return cls.media_part(
            type=kind,
            media_type=str(data.get("media_type") or ""),
            sha256=str(data.get("sha256") or ""),
            name=str(data.get("name") or ""),
            bytes=int(data.get("bytes") or 0),
        )


class MediaStore:
    """Bytes keyed by sha256. The host builds the directory implementation."""

    def put(self, data: bytes, media_type: str, name: str) -> dict[str, Any]:
        raise NotImplementedError

    def get(self, sha256: str) -> bytes:
        raise NotImplementedError


def as_part_dict(part: Any) -> dict[str, Any] | None:
    """A part mapping, or ``None`` when *part* is not a part object."""
    if isinstance(part, ContentPart):
        return part.to_dict()
    if isinstance(part, dict):
        return part
    return None


def _is_pdf_media_type(media_type: str | None) -> bool:
    value = str(media_type or "")
    return value == "application/pdf" or value.endswith("/pdf")


def text_of(content: Any, media: MediaMode = "skip") -> str:
    """Text view of a message. A string is returned unchanged (same object)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for raw in content:
        part = as_part_dict(raw)
        if part is None:
            continue
        kind = str(part.get("type") or _TEXT)
        if kind == _TEXT:
            pieces.append(str(part.get("text") or ""))
            continue
        if media == "placeholder":
            name = str(part.get("name") or "").strip()
            label = f"{kind} {name}".strip() if name else kind
            pieces.append(f"[{label}]")
    return "\n".join(pieces)


def content_fingerprint(content: Any) -> str:
    """Stable identity of a message body. A string is itself; a part list is JSON."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for raw in content:
            part = as_part_dict(raw)
            if part is not None:
                parts.append(part)
        return json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    if content is None:
        return ""
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)


def has_media(content: Any) -> bool:
    return bool(media_parts(content))


def media_parts(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in content:
        part = as_part_dict(raw)
        if part is None:
            continue
        kind = str(part.get("type") or _TEXT)
        if kind != _TEXT:
            out.append(part)
    return out


def part_modality(part: dict[str, Any]) -> str:
    """Catalog modality for a media part (``image``, ``pdf``, ``audio``, ``video``, ``file``)."""
    kind = str(part.get("type") or "")
    if kind == _IMAGE:
        return _IMAGE
    media_type = str(part.get("media_type") or "")
    if _is_pdf_media_type(media_type):
        return "pdf"
    if media_type.startswith("image/"):
        return _IMAGE
    if media_type.startswith("audio/"):
        return _AUDIO
    if media_type.startswith("video/"):
        return _VIDEO
    return kind or "file"


def text_chars(content: Any) -> int:
    """Character count of text only. A string is ``len``; a list sums text parts."""
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0
    total = 0
    for raw in content:
        part = as_part_dict(raw)
        if part is not None and str(part.get("type") or _TEXT) == _TEXT:
            total += len(str(part.get("text") or ""))
    return total


def token_estimate_for_content(content: Any) -> int:
    """Text chars/4 plus :data:`MEDIA_PART_TOKEN_ESTIMATE` per media part. Not a billable count."""
    return text_chars(content) // 4 + MEDIA_PART_TOKEN_ESTIMATE * len(media_parts(content))


def check_modalities(model: str, content: Any, *, catalog: Any | None = None) -> None:
    """Refuse media the catalog entry does not list. Unknown models pass.

    Audio, video, and non-PDF files are out of scope even when the catalog
    omits ``modalities`` or the model is unknown.
    """
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    cat = catalog if catalog is not None else default_model_catalog()
    for part in media_parts(content):
        modality = part_modality(part)
        kind = str(part.get("type") or "")
        if kind in _RESERVED or modality in _RESERVED:
            raise ModalityNotSupported(
                f"{model} does not accept {modality}",
                model=model,
                modality=modality,
            )
        if kind == _FILE and modality != "pdf":
            raise ModalityNotSupported(
                "file parts must be PDF",
                model=model,
                modality="file",
            )
        if not cat.accepts_modality(model, modality):
            raise ModalityNotSupported(
                f"{model} does not accept {modality}",
                model=model,
                modality=modality,
            )


def check_messages_modalities(
    model: str,
    messages: list[dict[str, Any]],
    *,
    catalog: Any | None = None,
) -> None:
    """Refuse media the catalog does not list. Media is only accepted on user turns."""
    for message in messages:
        content = message.get("content")
        role = str(message.get("role") or "")
        if has_media(content) and role != "user":
            modality = part_modality(media_parts(content)[0])
            raise ModalityNotSupported(
                f"media is only accepted on user turns, not {role or 'empty'}",
                model=model,
                modality=modality,
            )
        check_modalities(model, content, catalog=catalog)
