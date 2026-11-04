#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import os

from .basic import BasicEvaluator
from .interface import EvaluationInterface

# Registry of available evaluators
_EVALUATORS = {
    "basic": BasicEvaluator,
    # "mce": MCEEvaluator, # Future plugin
}

def get_evaluator(name: str = None) -> EvaluationInterface:
    """
    Factory function to get an evaluator instance.
    Defaults to 'basic' if not specified or found.
    """
    if not name:
        name = os.getenv("MAS_EVALUATOR", "basic")
        
    evaluator_cls = _EVALUATORS.get(name.lower())
    if not evaluator_cls:
        # Fallback or attempt dynamic loading?
        # For now, fallback to basic
        return BasicEvaluator()
        
    return evaluator_cls()
