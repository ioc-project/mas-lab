#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Evaluation pipeline building blocks for mas-lab.

    mas-runtime  →  emits raw observability dicts (JSONL)
    mas-lab/evaluation  →  evaluate metrics from raw events

Modules
-------
events          Kind, obs_event()
eval_contract   EvalContract ABC, MetricSpec, MetricValue, load_eval_plugin_class
obs_event       Re-exports from events (convenience aliases)
"""
from mas.lab.evaluation.eval_contract import (  # noqa: F401
    EvalContract,
    MetricSpec,
    MetricValue,
    load_eval_plugin_class,
)
from mas.lab.evaluation.events import Kind, obs_event  # noqa: F401
