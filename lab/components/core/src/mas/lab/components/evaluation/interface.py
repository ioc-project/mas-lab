#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class EvaluationInterface(ABC):
    """
    Abstract interface for evaluation mas.lab.components.
    Allows plugging in different evaluation engines (e.g. Basic, MCE).
    """

    @abstractmethod
    def evaluate(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Compute metrics based on a sequence of events.
        
        Args:
            events: List of event dictionaries (chronological order)
            
        Returns:
            Dictionary of metric names and values.
        """
        pass
