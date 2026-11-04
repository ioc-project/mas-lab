#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, List

from .interface import EvaluationInterface


class BasicEvaluator(EvaluationInterface):
    """
    Default evaluation implementation.
    Provides basic counts of system activities.
    """
    
    def evaluate(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        metrics = {
            "llm_calls": 0, 
            "tool_calls": 0, 
            "memory_reads": 0,
            "errors": 0,
            "total_events": len(events)
        }
        
        for event in events:
            kind = event.get("kind")
            
            if kind == "llm_call":
                metrics["llm_calls"] += 1
            elif kind == "tool_call":
                metrics["tool_calls"] += 1
            elif kind == "memory_read":
                metrics["memory_reads"] += 1
            elif kind == "error" or (event.get("payload", {}).get("status") == "error"):
                metrics["errors"] += 1
                
        return metrics
