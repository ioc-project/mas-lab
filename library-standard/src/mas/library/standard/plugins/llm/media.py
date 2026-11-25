#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared media mapping for every LLM protocol layer.

Parts and the media store are the same for every layer. Each function emits
that layer's wire objects. A string or text-only list is unchanged.
"""

from __future__ import annotations

import base64
from typing import Any

from mas.runtime.contracts.content_types import (
    MediaMissing,
    MediaStore,
    ModalityNotSupported,
    as_part_dict,
    has_media,
)

_FOREIGN_MESSAGE_KEYS = ("provider_state", "is_error")
_OPENRESPONSES_TEXT_TYPES = frozenset({None, "text", "input_text", "output_text"})
_ANTHROPIC_TEXT_TYPES = frozenset({None, "text"})
_BEDROCK_IMAGE_FORMAT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def concat_text(
    content: Any,
    *,
    join: str = "",
    types: frozenset[str | None] | None = None,
) -> str:
    """Join text carried in *content*. A string is returned unchanged."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for raw in content:
        if isinstance(raw, str):
            pieces.append(raw)
            continue
        part = as_part_dict(raw)
        if part is None:
            continue
        if types is not None and part.get("type") not in types:
            continue
        pieces.append(str(part.get("text") or ""))
    return join.join(pieces)


def media_bytes(part: dict[str, Any], media_store: MediaStore | None, *, layer: str) -> bytes:
    """Bytes for one media part. Missing store or digest is fatal."""
    digest = str(part.get("sha256") or "")
    if media_store is None:
        raise MediaMissing(digest or "(no digest)", layer=layer)
    try:
        data = media_store.get(digest)
    except MediaMissing as exc:
        if exc.layer:
            raise
        raise MediaMissing(digest, layer=layer) from None
    except (FileNotFoundError, KeyError):
        raise MediaMissing(digest, layer=layer) from None
    if data is None:
        raise MediaMissing(digest, layer=layer)
    return data


def media_b64(part: dict[str, Any], media_store: MediaStore | None, *, layer: str) -> str:
    """Standard base64 of the stored bytes, no wrapping."""
    return base64.standard_b64encode(media_bytes(part, media_store, layer=layer)).decode("ascii")


def data_url(part: dict[str, Any], media_store: MediaStore | None, *, layer: str) -> str:
    """``data:<media_type>;base64,…`` of the stored bytes."""
    media_type = str(part.get("media_type") or "application/octet-stream")
    return f"data:{media_type};base64,{media_b64(part, media_store, layer=layer)}"


def _parts(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in content:
        if isinstance(raw, str):
            out.append({"type": "text", "text": raw})
            continue
        part = as_part_dict(raw)
        if part is not None:
            out.append(part)
    return out


def chat_completions_part(part: dict[str, Any], media_store: MediaStore | None, *, layer: str) -> dict[str, Any]:
    """One chat-completions ``text``, ``image_url`` or ``file`` object."""
    url = data_url(part, media_store, layer=layer)
    if str(part.get("type") or "") == "image":
        return {"type": "image_url", "image_url": {"url": url}}
    return {"type": "file", "file": {"filename": str(part.get("name") or "file"), "file_data": url}}


def chat_completions_content(content: Any, media_store: MediaStore | None, *, layer: str = "openai") -> Any:
    """Map a part list to chat-completions content. A string or text-only list is unchanged."""
    if not has_media(content):
        return content
    wired: list[dict[str, Any]] = []
    for raw in _parts(content):
        if str(raw.get("type") or "text") == "text":
            wired.append({"type": "text", "text": str(raw.get("text") or "")})
        else:
            wired.append(chat_completions_part(raw, media_store, layer=layer))
    return wired


def wire_chat_messages(
    messages: list[dict[str, Any]],
    media_store: MediaStore | None = None,
    *,
    layer: str = "openai",
) -> list[dict[str, Any]]:
    """Drop foreign keys; map media parts. Text-only messages keep the same objects."""
    out: list[dict[str, Any]] = []
    for message in messages:
        cleaned = (
            {k: v for k, v in message.items() if k not in _FOREIGN_MESSAGE_KEYS}
            if any(k in message for k in _FOREIGN_MESSAGE_KEYS)
            else message
        )
        if not has_media(cleaned.get("content")):
            out.append(cleaned)
            continue
        wired = dict(cleaned)
        wired["content"] = chat_completions_content(cleaned.get("content"), media_store, layer=layer)
        out.append(wired)
    return out


def openresponses_user_parts(
    content: Any,
    media_store: MediaStore | None,
    *,
    layer: str = "openresponses",
) -> list[dict[str, Any]]:
    """Open Responses ``input_text`` / ``input_image`` / ``input_file`` items, in order."""
    if not has_media(content):
        text = concat_text(content, types=_OPENRESPONSES_TEXT_TYPES)
        return [{"type": "input_text", "text": text}] if text.strip() else []
    items: list[dict[str, Any]] = []
    for raw in _parts(content):
        kind = str(raw.get("type") or "text")
        if kind == "text":
            text = str(raw.get("text") or "")
            if text:
                items.append({"type": "input_text", "text": text})
        elif kind == "image":
            items.append({"type": "input_image", "image_url": data_url(raw, media_store, layer=layer)})
        else:
            items.append(
                {
                    "type": "input_file",
                    "filename": str(raw.get("name") or "file"),
                    "file_data": data_url(raw, media_store, layer=layer),
                }
            )
    return items


def anthropic_user_content(
    content: Any,
    media_store: MediaStore | None,
    *,
    layer: str = "anthropic",
) -> Any:
    """Anthropic user ``content``: a string when there is no media, else text/image/document blocks."""
    if not has_media(content):
        return concat_text(content, types=_ANTHROPIC_TEXT_TYPES)
    blocks: list[dict[str, Any]] = []
    for raw in _parts(content):
        kind = str(raw.get("type") or "text")
        if kind == "text":
            text = str(raw.get("text") or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        source = {
            "type": "base64",
            "media_type": str(raw.get("media_type") or "application/octet-stream"),
            "data": media_b64(raw, media_store, layer=layer),
        }
        if kind == "image":
            blocks.append({"type": "image", "source": source})
        else:
            block: dict[str, Any] = {"type": "document", "source": source}
            name = str(raw.get("name") or "").strip()
            if name:
                block["title"] = name
            blocks.append(block)
    return blocks


def gemini_user_parts(
    content: Any,
    media_store: MediaStore | None,
    *,
    layer: str = "gemini",
) -> list[dict[str, Any]]:
    """Gemini / Vertex ``parts``: ``text`` and ``inline_data``."""
    if not has_media(content):
        text = concat_text(content)
        return [{"text": text}] if text.strip() else []
    parts: list[dict[str, Any]] = []
    for raw in _parts(content):
        kind = str(raw.get("type") or "text")
        if kind == "text":
            text = str(raw.get("text") or "")
            if text:
                parts.append({"text": text})
            continue
        parts.append(
            {
                "inline_data": {
                    "mime_type": str(raw.get("media_type") or "application/octet-stream"),
                    "data": media_b64(raw, media_store, layer=layer),
                }
            }
        )
    return parts


def bedrock_user_blocks(
    content: Any,
    media_store: MediaStore | None,
    *,
    layer: str = "bedrock",
) -> list[dict[str, Any]]:
    """Bedrock Converse user ``content``: ``text``, ``image``, ``document``."""
    if not has_media(content):
        text = concat_text(content)
        return [{"text": text}] if text.strip() else []
    blocks: list[dict[str, Any]] = []
    for raw in _parts(content):
        kind = str(raw.get("type") or "text")
        if kind == "text":
            text = str(raw.get("text") or "")
            if text:
                blocks.append({"text": text})
            continue
        payload = {"bytes": media_b64(raw, media_store, layer=layer)}
        media_type = str(raw.get("media_type") or "")
        if kind == "image":
            fmt = _BEDROCK_IMAGE_FORMAT.get(media_type) or media_type.rsplit("/", 1)[-1] or "png"
            blocks.append({"image": {"format": fmt, "source": payload}})
        else:
            if media_type != "application/pdf" and not media_type.endswith("/pdf"):
                raise ModalityNotSupported("file parts must be PDF", layer=layer, modality="file")
            blocks.append(
                {
                    "document": {
                        "format": "pdf",
                        "name": str(raw.get("name") or "file"),
                        "source": payload,
                    }
                }
            )
    return blocks


def anthropic_text(content: Any) -> str:
    return concat_text(content, types=_ANTHROPIC_TEXT_TYPES)


def openresponses_text(content: Any) -> str:
    return concat_text(content, types=_OPENRESPONSES_TEXT_TYPES)


def gemini_text(content: Any) -> str:
    return concat_text(content)


def bedrock_text(content: Any) -> str:
    return concat_text(content)


__all__ = [
    "anthropic_text",
    "anthropic_user_content",
    "bedrock_text",
    "bedrock_user_blocks",
    "chat_completions_content",
    "concat_text",
    "data_url",
    "gemini_text",
    "gemini_user_parts",
    "media_b64",
    "media_bytes",
    "openresponses_text",
    "openresponses_user_parts",
    "wire_chat_messages",
]
