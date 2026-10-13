#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.library.lab.steps.services.metric_event import flatten_mce_document


def test_flatten_mce_document_session_scores() -> None:
    out = flatten_mce_document(
        {
            "session": {
                "goal_success_rate": {"value": 0.8, "reason": "ok"},
                "skip_me": {"value": True},
                "nested": "nope",
            },
        }
    )
    assert out == {"mce.goal_success_rate": 0.8, "goal_success_rate": 0.8}


def test_flatten_mce_document_skips_null_values() -> None:
    out = flatten_mce_document(
        {
            "session": {
                "goal_success_rate": {"value": None},
                "other_score": {"value": 0.0},
            }
        }
    )
    assert "mce.goal_success_rate" not in out
    assert out["mce.other_score"] == 0.0
    assert out["other_score"] == 0.0
