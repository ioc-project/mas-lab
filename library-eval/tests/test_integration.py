#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Integration tests for the eval library."""


def test_import_mce():
    from mas.library.eval.mce import (
        METRIC_REGISTRY,
    )

    assert len(METRIC_REGISTRY) > 0
    assert "GoalSuccessRate" in METRIC_REGISTRY
    assert "Groundedness" in METRIC_REGISTRY


def test_import_cli():
    from mas.library.eval.cli import eval_cmd

    assert eval_cmd is not None


def test_import_cli_component():
    from mas.library.eval.cli import EvalCliComponent

    component = EvalCliComponent()
    assert hasattr(component, "register")
    assert callable(component.register)


def test_import_steps():
    from mas.library.eval.steps import EvalMceStep

    assert EvalMceStep is not None


def test_metric_registry():
    from mas.library.eval.mce import METRIC_REGISTRY

    quality_metrics = [
        "AnswerRelevancy",
        "GoalSuccessRate",
        "Groundedness",
        "ResponseCompleteness",
        "ContextPreservation",
    ]
    for metric in quality_metrics:
        assert metric in METRIC_REGISTRY
        assert "mce_metrics_plugin" in METRIC_REGISTRY[metric]

    core_metrics = [
        "ToolUtilizationAccuracy",
        "ToolErrorRate",
    ]
    for metric in core_metrics:
        assert metric in METRIC_REGISTRY
        assert "metrics_computation_engine" in METRIC_REGISTRY[metric]

    # Span stub not used in OSS labs — excluded from registry
    assert "TaskDelegationAccuracy" not in METRIC_REGISTRY
