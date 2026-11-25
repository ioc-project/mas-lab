#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dataset user turns with media parts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from mas.lab.benchmark.cache.trace_store import compute_run_hash
from mas.lab.inputs import load_run_input, run_input_to_dict, validate_run_input_envelope


def _png(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_string_and_list_and_ref_still_load(tmp_path: Path):
    (tmp_path / "prompt.txt").write_text("From file\n", encoding="utf-8")
    one = load_run_input({"id": "t", "inputs": {"user": "hello"}})
    assert run_input_to_dict(one) == {"inputs": {"user": "hello"}}
    many = load_run_input({"id": "m", "inputs": {"user": ["First", "Second"]}})
    assert run_input_to_dict(many) == {"inputs": {"user": ["First", "Second"]}}
    ref = load_run_input({"id": "r", "inputs": {"user": {"ref": "prompt.txt"}}}, base_path=tmp_path)
    assert ref.primary_prompt == "From file"


def test_text_only_parts_is_a_string():
    run = load_run_input({"id": "t", "inputs": {"user": {"parts": [{"text": "hello"}]}}})
    assert run.user[0]["content"] == "hello"
    assert isinstance(run.user[0]["content"], str)
    assert run_input_to_dict(run) == {"inputs": {"user": "hello"}}
    two = load_run_input({"id": "t", "inputs": {"user": {"parts": [{"text": "a"}, {"text": "b"}]}}})
    assert two.user[0]["content"] == "a\nb"


def test_parts_one_turn(tmp_path: Path):
    data = b"\x89PNG chart"
    _png(tmp_path, "q3.png", data)
    digest = hashlib.sha256(data).hexdigest()
    item = {
        "id": "chart",
        "inputs": {
            "user": {
                "parts": [
                    {"text": "What is the trend in this chart?"},
                    {"image": {"ref": "q3.png"}},
                ]
            }
        },
    }
    validate_run_input_envelope({"inputs": item["inputs"]})
    run = load_run_input(item, base_path=tmp_path)
    content = run.user[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "What is the trend in this chart?"}
    assert content[1]["type"] == "image"
    assert content[1]["sha256"] == digest
    assert content[1]["name"] == "q3.png"
    assert content[1]["bytes"] == len(data)
    assert "path" not in content[1]
    assert run.scripted_queries() == ["What is the trend in this chart?"]
    turns = run.scripted_turns()
    assert turns[0] == content
    payload = run_input_to_dict(run)
    assert payload["inputs"]["user"] == {"role": "user", "content": content}


def test_mixed_list_of_strings_and_parts(tmp_path: Path):
    _png(tmp_path, "a.png", b"aaa")
    run = load_run_input(
        {
            "id": "mix",
            "inputs": {
                "user": [
                    "First",
                    {"parts": [{"text": "see"}, {"image": {"ref": "a.png"}}]},
                ]
            },
        },
        base_path=tmp_path,
    )
    assert run.scripted_queries()[0] == "First"
    assert isinstance(run.user[1]["content"], list)


def test_missing_file_names_the_item(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="chart"):
        load_run_input(
            {
                "id": "chart",
                "inputs": {"user": {"parts": [{"image": {"ref": "missing.png"}}]}},
            },
            base_path=tmp_path,
        )


def test_unknown_extension_names_the_item(tmp_path: Path):
    (tmp_path / "notes.bin").write_bytes(b"xx")
    with pytest.raises(ValueError, match="chart"):
        load_run_input(
            {
                "id": "chart",
                "inputs": {"user": {"parts": [{"file": {"ref": "notes.bin"}}]}},
            },
            base_path=tmp_path,
        )


def test_file_part_rejects_non_pdf(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "chart.png").write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="notes"):
        load_run_input(
            {
                "id": "notes",
                "inputs": {"user": {"parts": [{"file": {"ref": "notes.txt"}}]}},
            },
            base_path=tmp_path,
        )
    with pytest.raises(ValueError, match="chart"):
        load_run_input(
            {
                "id": "chart",
                "inputs": {"user": {"parts": [{"file": {"ref": "chart.png"}}]}},
            },
            base_path=tmp_path,
        )


def test_audio_part_is_refused_and_names_the_item(tmp_path: Path):
    (tmp_path / "clip.wav").write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="chart"):
        load_run_input(
            {
                "id": "chart",
                "inputs": {"user": {"parts": [{"audio": {"ref": "clip.wav"}}]}},
            },
            base_path=tmp_path,
        )


def test_run_hash_changes_with_file_bytes_not_path(tmp_path: Path):
    payload = b"\x89PNG same"
    (tmp_path / "sub").mkdir()
    _png(tmp_path, "chart.png", payload)
    _png(tmp_path / "sub", "chart.png", payload)
    a = load_run_input(
        {"id": "t", "inputs": {"user": {"parts": [{"image": {"ref": "chart.png"}}]}}},
        base_path=tmp_path,
    )
    b = load_run_input(
        {"id": "t", "inputs": {"user": {"parts": [{"image": {"ref": "sub/chart.png"}}]}}},
        base_path=tmp_path,
    )
    config = {"agents": [{"id": "a", "llm_model": "gpt-4o"}]}
    ha = compute_run_hash(config, run_input_to_dict(a), "t", 0, {})
    hb = compute_run_hash(config, run_input_to_dict(b), "t", 0, {})
    assert ha == hb
    (tmp_path / "chart.png").write_bytes(b"\x89PNG other")
    c = load_run_input(
        {"id": "t", "inputs": {"user": {"parts": [{"image": {"ref": "chart.png"}}]}}},
        base_path=tmp_path,
    )
    hc = compute_run_hash(config, run_input_to_dict(c), "t", 0, {})
    assert hc != ha


def test_deprecated_role_content_keeps_a_part_list():
    parts = [
        {"type": "text", "text": "see"},
        {
            "type": "image",
            "media_type": "image/png",
            "sha256": "abc",
            "name": "chart.png",
            "bytes": 4,
        },
    ]
    run = load_run_input({"id": "legacy", "inputs": {"user": [{"role": "user", "content": parts}]}})
    assert run.user[0]["content"] == parts
    assert isinstance(run.user[0]["content"], list)
