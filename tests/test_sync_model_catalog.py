#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/sync_model_catalog.py (no network)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_model_catalog.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "models-dev-api-subset.json"

CATALOG_HEADER = """\
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
apiVersion: mas/v1
kind: LlmModelCatalog
metadata:
  name: standard
  version: "2026.11.1"
spec:
  sources: []
  models:
"""


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_model_catalog", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sync():
    return _load_module()


def _write_catalog(path: Path, models_yaml: str) -> Path:
    path.write_text(CATALOG_HEADER + models_yaml, encoding="utf-8")
    return path


def test_sync_maps_factual_fields(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    gpt-4o:
      aliases: [openai/gpt-4o]
      context_window: 1
      max_output_tokens: 2
      settings:
        think: {supported: false}
""",
    )
    result = sync.sync_catalog(catalog, from_file=FIXTURE)
    assert result.exit_code == 0
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    entry = data["spec"]["models"]["gpt-4o"]
    assert entry["context_window"] == 128000
    assert entry["max_output_tokens"] == 16384
    assert entry["modalities"]["input"] == ["text", "image"]
    assert entry["modalities"]["output"] == ["text"]
    assert entry["pricing"]["input_per_million_tokens"] == 2.5
    assert entry["pricing"]["output_per_million_tokens"] == 10
    assert entry["pricing"]["cached_input_per_million_tokens"] == 1.25
    assert "gpt-4o" in result.updated


def test_sync_matches_alias(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    local-name:
      aliases: [alias-source]
      context_window: 8
      max_output_tokens: 8
""",
    )
    result = sync.sync_catalog(catalog, from_file=FIXTURE)
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    entry = data["spec"]["models"]["local-name"]
    assert entry["context_window"] == 64000
    assert entry["modalities"]["input"] == ["text", "pdf"]
    assert "local-name" in result.updated
    assert "local-name" not in result.unmatched


def test_sync_reports_unmatched_and_does_not_drop_it(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    unmatched-entry:
      aliases: [nobody/unmatched]
      context_window: 111
      max_output_tokens: 22
      settings:
        think: {supported: true}
""",
    )
    result = sync.sync_catalog(catalog, from_file=FIXTURE)
    text = catalog.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert "unmatched-entry" in data["spec"]["models"]
    assert data["spec"]["models"]["unmatched-entry"]["context_window"] == 111
    assert "unmatched-entry" in result.unmatched
    assert "unmatched-entry:" in text


def test_sync_leaves_handwritten_fields_untouched(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    gpt-4o:
      aliases: [openai/gpt-4o, keep-me]
      context_window: 128000
      max_output_tokens: 16384
      thinking_mode: none
      max_thinking_tokens: 99
      apis: [chat_completions]
      defaults:
        temperature: 0.7
      settings:
        think: {supported: false}
        reasoning.effort: {supported: false}
""",
    )
    sync.sync_catalog(catalog, from_file=FIXTURE)
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    entry = data["spec"]["models"]["gpt-4o"]
    assert entry["aliases"] == ["openai/gpt-4o", "keep-me"]
    assert entry["thinking_mode"] == "none"
    assert entry["max_thinking_tokens"] == 99
    assert entry["apis"] == ["chat_completions"]
    assert entry["defaults"] == {"temperature": 0.7}
    assert entry["settings"]["think"] == {"supported": False}
    assert entry["settings"]["reasoning.effort"] == {"supported": False}


def test_sync_check_exit_code(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    gpt-4o:
      context_window: 1
      max_output_tokens: 2
""",
    )
    before = catalog.read_text(encoding="utf-8")
    dirty = sync.sync_catalog(catalog, from_file=FIXTURE, check=True)
    assert dirty.exit_code != 0
    assert catalog.read_text(encoding="utf-8") == before
    sync.sync_catalog(catalog, from_file=FIXTURE)
    after = catalog.read_text(encoding="utf-8")
    clean = sync.sync_catalog(catalog, from_file=FIXTURE, check=True)
    assert clean.exit_code == 0
    assert catalog.read_text(encoding="utf-8") == after


def test_sync_is_idempotent(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    gpt-4o:
      context_window: 1
      max_output_tokens: 2
""",
    )
    sync.sync_catalog(catalog, from_file=FIXTURE)
    first = catalog.read_text(encoding="utf-8")
    sync.sync_catalog(catalog, from_file=FIXTURE)
    assert catalog.read_text(encoding="utf-8") == first


def test_sync_preserves_comments(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    # keep this model comment
    gpt-4o:
      aliases: [openai/gpt-4o]
      context_window: 1
      # keep this inline note
      max_output_tokens: 2
      settings:
        think: {supported: false}
""",
    )
    sync.sync_catalog(catalog, from_file=FIXTURE)
    text = catalog.read_text(encoding="utf-8")
    assert "# keep this model comment" in text
    assert "# keep this inline note" in text


def test_sync_keeps_existing_pricing_shape(tmp_path, sync):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    claude-haiku-4-5:
      context_window: 200000
      max_output_tokens: 64000
      pricing:
        currency: USD
        input_per_million_tokens: 9
        output_per_million_tokens: 9
        source: https://example.test
        checked_at: "2026-01-01"
""",
    )
    sync.sync_catalog(catalog, from_file=FIXTURE)
    entry = yaml.safe_load(catalog.read_text(encoding="utf-8"))["spec"]["models"]["claude-haiku-4-5"]
    assert entry["pricing"]["input_per_million_tokens"] == 1
    assert entry["pricing"]["output_per_million_tokens"] == 5
    assert "cached_input_per_million_tokens" not in entry["pricing"]
    assert entry["pricing"]["currency"] == "USD"
    assert entry["pricing"]["source"] == "https://example.test"
    assert entry["pricing"]["checked_at"] == "2026-01-01"


def test_sync_cli_from_file(tmp_path, sync, monkeypatch):
    catalog = _write_catalog(
        tmp_path / "cat.yaml",
        """\
    gpt-4o:
      context_window: 1
      max_output_tokens: 2
""",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_model_catalog.py", "--catalog", str(catalog), "--from-file", str(FIXTURE)],
    )
    assert sync.main() == 0
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert data["spec"]["models"]["gpt-4o"]["context_window"] == 128000
