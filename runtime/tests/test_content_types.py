#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Neutral message content: str | list[part], never normalized."""

from __future__ import annotations

import dataclasses

import pytest
from mas.runtime.contracts.content_types import (
    ContentPart,
    MediaMissing,
    ModalityNotSupported,
    check_messages_modalities,
    check_modalities,
    has_media,
    media_parts,
    text_of,
)
from mas.runtime.reliability.classes import FailureClass
from mas.runtime.reliability.classify import is_retryable_llm_http_error


def test_text_part_round_trip():
    part = ContentPart.text_part("hello")
    assert dataclasses.is_dataclass(part) and part.__dataclass_params__.frozen
    assert part.to_dict() == {"type": "text", "text": "hello"}
    restored = ContentPart.from_dict({"type": "text", "text": "hello"})
    assert restored.to_dict() == part.to_dict()


def test_media_part_round_trip():
    part = ContentPart.media_part(
        type="image",
        media_type="image/png",
        sha256="abc",
        name="chart.png",
        bytes=12,
    )
    assert part.to_dict() == {
        "type": "image",
        "media_type": "image/png",
        "sha256": "abc",
        "name": "chart.png",
        "bytes": 12,
    }
    restored = ContentPart.from_dict(part.to_dict())
    assert restored.to_dict() == part.to_dict()


def test_text_of_passes_str_through_unchanged():
    raw = "hello"
    assert text_of(raw) is raw
    assert text_of(raw, media="placeholder") is raw


def test_text_of_joins_text_parts_and_placeholders():
    content = [
        {"type": "text", "text": "see"},
        {
            "type": "image",
            "media_type": "image/png",
            "sha256": "abc",
            "name": "chart.png",
            "bytes": 4,
        },
        {"type": "text", "text": "this"},
    ]
    assert text_of(content) == "see\nthis"
    assert text_of(content, media="placeholder") == "see\n[image chart.png]\nthis"
    assert text_of(content, media="skip") == "see\nthis"


def test_text_of_unknown_is_empty():
    assert text_of(None) == ""
    assert text_of({"oops": 1}) == ""
    assert text_of(3) == ""


def test_has_media_and_media_parts():
    text_only = [{"type": "text", "text": "hi"}]
    assert has_media("hi") is False
    assert has_media(text_only) is False
    assert media_parts("hi") == []
    image = {
        "type": "image",
        "media_type": "image/png",
        "sha256": "abc",
        "name": "chart.png",
        "bytes": 4,
    }
    mixed = [{"type": "text", "text": "hi"}, image]
    assert has_media(mixed) is True
    assert media_parts(mixed) == [image]


def test_file_part_must_be_pdf():
    with pytest.raises(ModalityNotSupported, match="PDF"):
        ContentPart.media_part(
            type="file",
            media_type="text/plain",
            sha256="abc",
            name="notes.txt",
            bytes=4,
        )
    pdf = ContentPart.media_part(
        type="file",
        media_type="application/pdf",
        sha256="abc",
        name="notes.pdf",
        bytes=4,
    )
    assert pdf.to_dict()["type"] == "file"


def test_audio_and_video_parts_are_refused():
    with pytest.raises(ModalityNotSupported, match="audio"):
        ContentPart.media_part(
            type="audio",
            media_type="audio/wav",
            sha256="abc",
            name="clip.wav",
            bytes=8,
        )
    with pytest.raises(ModalityNotSupported, match="video"):
        ContentPart.from_dict(
            {
                "type": "video",
                "media_type": "video/mp4",
                "sha256": "abc",
                "name": "clip.mp4",
                "bytes": 8,
            }
        )


def test_modality_not_supported_is_fatal_and_not_retryable():
    err = ModalityNotSupported("openai does not accept video", layer="openai", modality="video")
    assert isinstance(err, Exception)
    assert err.failure_class is FailureClass.FATAL
    assert is_retryable_llm_http_error(err) is False
    assert "openai" in str(err)
    assert "video" in str(err)


class _Catalog:
    def __init__(self, allowed: dict[str, list[str]]) -> None:
        self.allowed = allowed

    def accepts_modality(self, model: str, modality: str, direction: str = "input") -> bool:
        listed = self.allowed.get(model)
        if listed is None:
            return True
        return modality in listed


_IMAGE = {
    "type": "image",
    "media_type": "image/png",
    "sha256": "abc",
    "name": "chart.png",
    "bytes": 4,
}


def test_check_modalities_text_only_model_refuses_image():
    catalog = _Catalog({"text-only": ["text"]})
    with pytest.raises(ModalityNotSupported, match="text-only"):
        check_modalities(
            "text-only",
            [{"type": "text", "text": "see"}, _IMAGE],
            catalog=catalog,
        )


def test_check_modalities_shipped_gemma_row_refuses_image():
    with pytest.raises(ModalityNotSupported, match="gemma"):
        check_modalities(
            "gemma-4-26b-a4b-it-node1-a100-0",
            [{"type": "text", "text": "see"}, _IMAGE],
        )


def test_check_modalities_unknown_and_unlisted_pass():
    content = [{"type": "text", "text": "see"}, _IMAGE]
    check_modalities("not-a-catalog-model", content)
    check_modalities("llama3.2", content)
    check_modalities("gpt-4o", content)


def test_check_modalities_refuses_audio_and_non_pdf_file_on_unknown_model():
    audio = {
        "type": "audio",
        "media_type": "audio/wav",
        "sha256": "abc",
        "name": "clip.wav",
        "bytes": 4,
    }
    plain = {
        "type": "file",
        "media_type": "text/plain",
        "sha256": "abc",
        "name": "notes.txt",
        "bytes": 4,
    }
    with pytest.raises(ModalityNotSupported, match="audio"):
        check_modalities("not-a-catalog-model", [audio])
    with pytest.raises(ModalityNotSupported, match="PDF"):
        check_modalities("not-a-catalog-model", [plain])


def test_check_messages_modalities_scans_every_turn():
    catalog = _Catalog({"text-only": ["text"]})
    with pytest.raises(ModalityNotSupported, match="text-only"):
        check_messages_modalities(
            "text-only",
            [
                {"role": "user", "content": "hello"},
                {"role": "user", "content": [{"type": "text", "text": "see"}, _IMAGE]},
            ],
            catalog=catalog,
        )


def test_check_messages_modalities_refuses_system_media():
    with pytest.raises(ModalityNotSupported, match="user turns"):
        check_messages_modalities(
            "gpt-4o",
            [{"role": "system", "content": [_IMAGE]}],
        )


def test_media_missing_is_fatal():
    err = MediaMissing("abc", layer="openai")
    assert err.failure_class is FailureClass.FATAL
    assert err.code == "MEDIA_MISSING"
    assert "abc" in str(err)
