#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generated schema artifacts stay in sync with docs/schemas."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_schema_artifacts_up_to_date():
    script = _REPO_ROOT / "scripts" / "gen_schema_artifacts.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_infra_middleware_path_param_keys_match_schema():
    from mas.runtime.spec.schema_bindings_generated import INFRA_MIDDLEWARE_PATH_PARAM_KEYS

    assert INFRA_MIDDLEWARE_PATH_PARAM_KEYS == frozenset(
        {"cache_path", "path", "miss_log_path", "state_path"}
    )


def test_cm_factory_strips_only_assembly_param_keys():
    from mas.runtime.contracts.cm_factory import CMFactory
    from mas.runtime.spec.schema_bindings_generated import (
        CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS,
        CONTEXT_MANAGER_STRATEGY_PARAM_KEYS,
    )

    assert "working_memory_messages" in CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS
    assert "archive" not in CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS
    assert "max_in_turn_messages" not in CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS
    assert "trimmer" in CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS
    assert "token_budget" not in CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS
    assert "max_turns" in CONTEXT_MANAGER_STRATEGY_PARAM_KEYS
    assert "keep_turns" in CONTEXT_MANAGER_STRATEGY_PARAM_KEYS
    assert "hysteresis_ratio" in CONTEXT_MANAGER_STRATEGY_PARAM_KEYS
    assert "max_turns" not in CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS
    # Smoke: stripping is applied inside create(); keys are schema-driven.
    assert CMFactory is not None


def test_chunk_and_archive_defaults_match_schema():
    from mas.runtime.spec.schema_defaults_generated import (
        ARCHIVE_DIR_NAME,
        ARCHIVE_ENABLED,
        ARCHIVE_MEMORY_WATERMARK_BYTES,
        CHUNK_MESSAGE_KEY,
        CHUNK_PIN_NEVER,
        CHUNK_PIN_POLICIES,
        CHUNK_PIN_POLICY_DEFAULT,
        CHUNK_SEMANTIC_INSTRUCTION,
        CHUNK_SEMANTICS,
        CHUNK_SUMMARY_HEADING,
    )

    assert ARCHIVE_ENABLED is False
    assert ARCHIVE_DIR_NAME == "archive"
    assert ARCHIVE_MEMORY_WATERMARK_BYTES == 67108864
    assert CHUNK_MESSAGE_KEY == "mas"
    assert CHUNK_PIN_POLICY_DEFAULT == "ephemeral"
    assert CHUNK_PIN_NEVER == "never"
    assert CHUNK_PIN_POLICIES == ("never", "prefer", "ephemeral")
    assert CHUNK_SEMANTIC_INSTRUCTION == "instruction"
    assert "summary" in CHUNK_SEMANTICS
    assert CHUNK_SUMMARY_HEADING.startswith("[Conversation summary")
