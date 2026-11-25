#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Image and file parts on every LLM protocol layer, from one shared helper."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import Any

import httpx
import pytest
from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
from mas.library.standard.plugins.llm.compatible import GroqLLMProvider
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.media import (
    bedrock_user_blocks,
    chat_completions_content,
    concat_text,
    media_bytes,
    wire_chat_messages,
)
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.library.standard.plugins.llm.openresponses import OpenResponsesLLMProvider
from mas.library.standard.plugins.llm.vertex import VertexLLMProvider
from mas.runtime.contracts.content_types import ContentPart, MediaMissing, ModalityNotSupported

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PDF = b"%PDF-1.1\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
_PNG_SHA = hashlib.sha256(_PNG).hexdigest()
_PDF_SHA = hashlib.sha256(_PDF).hexdigest()
_PNG_B64 = base64.standard_b64encode(_PNG).decode("ascii")
_PDF_B64 = base64.standard_b64encode(_PDF).decode("ascii")

_CHAT_OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
_OR_OK = {
    "status": "completed",
    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}],
}
_ANTHROPIC_OK = {
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 4, "output_tokens": 2},
}
_GEMINI_OK = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
}
_BEDROCK_OK = {
    "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 4, "outputTokens": 2},
}


class _Store:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def get(self, sha256: str) -> bytes:
        return self.blobs[sha256]


class _Transport:
    def __init__(self, body: dict[str, Any]) -> None:
        self.requests: list[httpx.Request] = []
        self.body = body
        handler = httpx.MockTransport(self._answer)
        self.client = httpx.Client(transport=handler)
        self.aclient = httpx.AsyncClient(transport=handler)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=self.body)

    def sent(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _image_content() -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": "What is the trend?"},
        {
            "type": "image",
            "media_type": "image/png",
            "sha256": _PNG_SHA,
            "name": "chart.png",
            "bytes": len(_PNG),
        },
    ]


def _pdf_content() -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": "Summarise this."},
        {
            "type": "file",
            "media_type": "application/pdf",
            "sha256": _PDF_SHA,
            "name": "notes.pdf",
            "bytes": len(_PDF),
        },
    ]


def test_openai_image_url_is_exact_base64_of_the_file() -> None:
    store = _Store({_PNG_SHA: _PNG})
    transport = _Transport(_CHAT_OK)
    provider = OpenAILLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openai"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    out = asyncio.run(
        provider.achat_completion(model="gpt-4o", messages=[{"role": "user", "content": _image_content()}])
    )
    assert out["content"] == "ok"
    body = transport.sent()
    parts = body["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "What is the trend?"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{_PNG_B64}"


def test_openai_pdf_uses_file_shape() -> None:
    store = _Store({_PDF_SHA: _PDF})
    transport = _Transport(_CHAT_OK)
    provider = OpenAILLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openai"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    asyncio.run(provider.achat_completion(model="gpt-4o", messages=[{"role": "user", "content": _pdf_content()}]))
    parts = transport.sent()["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "Summarise this."}
    assert parts[1]["type"] == "file"
    assert parts[1]["file"]["filename"] == "notes.pdf"
    assert parts[1]["file"]["file_data"] == f"data:application/pdf;base64,{_PDF_B64}"


def test_openai_refuses_image_on_text_only_catalog_model_and_sends_nothing() -> None:
    store = _Store({_PNG_SHA: _PNG})
    transport = _Transport(_CHAT_OK)
    provider = OpenAILLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openai"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    with pytest.raises(ModalityNotSupported, match="gemma"):
        asyncio.run(
            provider.achat_completion(
                model="gemma-4-26b",
                messages=[{"role": "user", "content": _image_content()}],
            )
        )
    assert transport.requests == []


def test_openai_missing_blob_is_media_missing() -> None:
    transport = _Transport(_CHAT_OK)
    provider = OpenAILLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openai"},
            "media_store": _Store({}),
        },
        call_kinds=("llm",),
    )
    with pytest.raises(MediaMissing, match=_PNG_SHA):
        asyncio.run(
            provider.achat_completion(
                model="gpt-4o",
                messages=[{"role": "user", "content": _image_content()}],
            )
        )
    assert transport.requests == []


def test_groq_uses_the_same_chat_completions_image_url() -> None:
    store = _Store({_PNG_SHA: _PNG})
    transport = _Transport(_CHAT_OK)
    provider = GroqLLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "groq"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _image_content()}]))
    parts = transport.sent()["messages"][0]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{_PNG_B64}"


def test_concat_text_joins_strings_parts_and_skips_noise() -> None:
    mixed = [
        "hello",
        {"type": "text", "text": "there"},
        ContentPart.text_part("world"),
        object(),
        {"type": "image", "media_type": "image/png", "sha256": "x", "name": "a.png", "bytes": 1},
    ]
    assert concat_text(mixed) == "hellothereworld"
    assert concat_text(mixed, types=frozenset({None, "text"})) == "hellothereworld"
    assert concat_text(3) == ""


def test_media_bytes_missing_paths() -> None:
    part = {"sha256": "abc", "type": "image"}
    with pytest.raises(MediaMissing, match="openai"):
        media_bytes(part, None, layer="openai")

    class _Layered:
        def get(self, sha256: str) -> bytes:
            raise MediaMissing(sha256, layer="openai")

    with pytest.raises(MediaMissing, match="openai"):
        media_bytes(part, _Layered(), layer="bedrock")

    class _Bare:
        def get(self, sha256: str) -> bytes:
            raise MediaMissing(sha256)

    with pytest.raises(MediaMissing, match="openai"):
        media_bytes(part, _Bare(), layer="openai")

    class _MissingFile:
        def get(self, sha256: str) -> bytes:
            raise FileNotFoundError(sha256)

    with pytest.raises(MediaMissing, match="abc"):
        media_bytes(part, _MissingFile(), layer="openai")

    class _MissingKey:
        def get(self, sha256: str) -> bytes:
            raise KeyError(sha256)

    with pytest.raises(MediaMissing):
        media_bytes(part, _MissingKey(), layer="openai")

    class _None:
        def get(self, sha256: str) -> bytes | None:
            return None

    with pytest.raises(MediaMissing):
        media_bytes(part, _None(), layer="openai")


def test_chat_completions_content_maps_mixed_list_with_contentpart() -> None:
    store = _Store({_PNG_SHA: _PNG, _PDF_SHA: _PDF})
    content = [
        "What is the trend?",
        ContentPart.media_part(
            type="image",
            media_type="image/png",
            sha256=_PNG_SHA,
            name="chart.png",
            bytes=len(_PNG),
        ),
    ]
    wired = chat_completions_content(content, store, layer="openai")
    assert wired[0] == {"type": "text", "text": "What is the trend?"}
    assert wired[1]["type"] == "image_url"
    pdf = [
        ContentPart.text_part("Summarise this."),
        ContentPart.media_part(
            type="file",
            media_type="application/pdf",
            sha256=_PDF_SHA,
            name="notes.pdf",
            bytes=len(_PDF),
        ),
    ]
    file_wired = chat_completions_content(pdf, store, layer="openai")
    assert file_wired[1]["type"] == "file"
    assert file_wired[1]["file"]["filename"] == "notes.pdf"


def test_bedrock_refuses_non_pdf_file() -> None:
    store = _Store({"x" * 64: b"hello"})
    digest = "x" * 64
    content = [
        {
            "type": "file",
            "media_type": "text/plain",
            "sha256": digest,
            "name": "notes.txt",
            "bytes": 5,
        }
    ]
    with pytest.raises(ModalityNotSupported, match="PDF"):
        bedrock_user_blocks(content, store, layer="bedrock")


def test_openai_text_only_messages_are_the_same_objects() -> None:
    messages = [{"role": "user", "content": "hello"}]
    wired = wire_chat_messages(messages)
    assert wired[0] is messages[0]
    assert wired[0]["content"] is messages[0]["content"]


def test_openresponses_image_is_input_image_in_order_with_text() -> None:
    store = _Store({_PNG_SHA: _PNG})
    transport = _Transport(_OR_OK)
    provider = OpenResponsesLLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openresponses"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _image_content()}]))
    items = transport.sent()["input"]
    user = next(item for item in items if item.get("role") == "user")
    assert user["content"][0] == {"type": "input_text", "text": "What is the trend?"}
    assert user["content"][1] == {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{_PNG_B64}",
    }


def test_openresponses_pdf_is_input_file() -> None:
    store = _Store({_PDF_SHA: _PDF})
    transport = _Transport(_OR_OK)
    provider = OpenResponsesLLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openresponses"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _pdf_content()}]))
    items = transport.sent()["input"]
    user = next(item for item in items if item.get("role") == "user")
    parts = user["content"]
    assert parts[0] == {"type": "input_text", "text": "Summarise this."}
    assert parts[1]["type"] == "input_file"
    assert parts[1]["filename"] == "notes.pdf"
    assert parts[1]["file_data"] == f"data:application/pdf;base64,{_PDF_B64}"


def test_openai_maps_contentpart_objects() -> None:
    store = _Store({_PNG_SHA: _PNG})
    transport = _Transport(_CHAT_OK)
    provider = OpenAILLMProvider.wrap(
        transport,
        {
            "api_base": "http://127.0.0.1:9/v1",
            "api_key": {"secret": "openai"},
            "media_store": store,
        },
        call_kinds=("llm",),
    )
    content = [
        ContentPart.text_part("What is the trend?"),
        ContentPart.media_part(
            type="image",
            media_type="image/png",
            sha256=_PNG_SHA,
            name="chart.png",
            bytes=len(_PNG),
        ),
    ]
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": content}]))
    parts = transport.sent()["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "What is the trend?"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{_PNG_B64}"


def _wrap(
    cls: type, body: dict[str, Any], extra: dict[str, Any], store: _Store | None, layer: str
) -> tuple[Any, _Transport]:
    transport = _Transport(body)
    params: dict[str, Any] = {
        "api_base": "http://127.0.0.1:9/v1",
        "api_key": {"secret": layer},
        **extra,
    }
    if store is not None:
        params["media_store"] = store
    return cls.wrap(transport, params, call_kinds=("llm",)), transport


def test_anthropic_image_is_base64_source() -> None:
    provider, transport = _wrap(AnthropicLLMProvider, _ANTHROPIC_OK, {}, _Store({_PNG_SHA: _PNG}), "anthropic")
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _image_content()}]))
    user = transport.sent()["messages"][0]["content"]
    assert user[0]["type"] == "text" and user[0]["text"] == "What is the trend?"
    assert user[1]["type"] == "image"
    assert user[1]["source"] == {"type": "base64", "media_type": "image/png", "data": _PNG_B64}


def test_anthropic_pdf_is_document_source() -> None:
    provider, transport = _wrap(AnthropicLLMProvider, _ANTHROPIC_OK, {}, _Store({_PDF_SHA: _PDF}), "anthropic")
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _pdf_content()}]))
    user = transport.sent()["messages"][0]["content"]
    assert user[1]["type"] == "document"
    assert user[1]["source"] == {"type": "base64", "media_type": "application/pdf", "data": _PDF_B64}
    assert user[1]["title"] == "notes.pdf"


def test_gemini_image_is_inline_data() -> None:
    provider, transport = _wrap(GeminiLLMProvider, _GEMINI_OK, {}, _Store({_PNG_SHA: _PNG}), "gemini")
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _image_content()}]))
    parts = transport.sent()["contents"][0]["parts"]
    assert parts[0] == {"text": "What is the trend?"}
    assert parts[1] == {"inline_data": {"mime_type": "image/png", "data": _PNG_B64}}


def test_vertex_uses_the_same_gemini_inline_data() -> None:
    provider, transport = _wrap(
        VertexLLMProvider,
        _GEMINI_OK,
        {"project": "demo-proj", "location": "us-central1"},
        _Store({_PNG_SHA: _PNG}),
        "vertex",
    )
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _image_content()}]))
    parts = transport.sent()["contents"][0]["parts"]
    assert parts[1] == {"inline_data": {"mime_type": "image/png", "data": _PNG_B64}}


def test_bedrock_image_is_converse_image_bytes() -> None:
    provider, transport = _wrap(BedrockLLMProvider, _BEDROCK_OK, {}, _Store({_PNG_SHA: _PNG}), "bedrock")
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _image_content()}]))
    blocks = transport.sent()["messages"][0]["content"]
    assert blocks[0] == {"text": "What is the trend?"}
    assert blocks[1] == {"image": {"format": "png", "source": {"bytes": _PNG_B64}}}


def test_bedrock_pdf_is_converse_document() -> None:
    provider, transport = _wrap(BedrockLLMProvider, _BEDROCK_OK, {}, _Store({_PDF_SHA: _PDF}), "bedrock")
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": _pdf_content()}]))
    blocks = transport.sent()["messages"][0]["content"]
    assert blocks[1] == {
        "document": {"format": "pdf", "name": "notes.pdf", "source": {"bytes": _PDF_B64}},
    }


def test_image_only_user_message_still_sends_the_image() -> None:
    content = [
        {
            "type": "image",
            "media_type": "image/png",
            "sha256": _PNG_SHA,
            "name": "chart.png",
            "bytes": len(_PNG),
        }
    ]
    provider, transport = _wrap(AnthropicLLMProvider, _ANTHROPIC_OK, {}, _Store({_PNG_SHA: _PNG}), "anthropic")
    asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": content}]))
    user = transport.sent()["messages"][0]["content"]
    assert user[0]["type"] == "image"
    assert "text" not in {block.get("type") for block in user if isinstance(block, dict)}


@pytest.mark.parametrize(
    "cls, body, extra, layer",
    [
        (AnthropicLLMProvider, _ANTHROPIC_OK, {}, "anthropic"),
        (GeminiLLMProvider, _GEMINI_OK, {}, "gemini"),
        (
            VertexLLMProvider,
            _GEMINI_OK,
            {"project": "demo-proj", "location": "us-central1"},
            "vertex",
        ),
        (BedrockLLMProvider, _BEDROCK_OK, {}, "bedrock"),
    ],
)
def test_vendor_layers_leave_text_alone(cls: type, body: dict[str, Any], extra: dict[str, Any], layer: str) -> None:
    provider, transport = _wrap(cls, body, extra, None, layer)
    text_out = asyncio.run(provider.achat_completion(model="m", messages=[{"role": "user", "content": "hello"}]))
    assert "ok" in str(text_out.get("content") or "")
    assert len(transport.requests) == 1
