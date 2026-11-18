#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live HuggingFace download (needs network and the ``datasets`` package)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.dataset_source import materialize_source

pytestmark = pytest.mark.functional


def test_huggingface_live_mmlu_pro_one_row():
    items = materialize_source(
        {
            "kind": "huggingface",
            "id": "TIGER-Lab/MMLU-Pro",
            "split": "validation[:1]",
            "map": {
                "id": "question_id",
                "inputs.user": "{question}\n{options_text}",
                "expectations.ground_truth": "answer_index",
            },
        },
        base_path=Path("."),
    )
    assert items[0]["id"]
    assert items[0]["inputs"]["user"]
    assert items[0]["expectations"]["ground_truth"] is not None
