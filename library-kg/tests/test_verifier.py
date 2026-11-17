#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unit tests for mas.library.kg.core.verifier — one test per structural invariant.

All tests operate on plain dicts; no real KG file required.
Node schema: callId (camelCase), node_type, startTime, endTime.
Edge schema: edge_type, from_id, to_id.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(callId: str, node_type: str, start: float = 0.0, end: float = 1.0, **extra):
    return {"callId": callId, "node_type": node_type, "startTime": start, "endTime": end, **extra}


# ---------------------------------------------------------------------------
# check_unknown_node_types
# ---------------------------------------------------------------------------


class TestCheckUnknownNodeTypes:
    def test_known_node_type_passes(self):
        from mas.library.kg.core.verifier import KNOWN_NODE_TYPES, check_unknown_node_types

        assert KNOWN_NODE_TYPES
        node_type = next(iter(KNOWN_NODE_TYPES))
        nodes = [{"node_type": node_type, "callId": "x"}]
        ok, violations = check_unknown_node_types(nodes, [])
        assert ok
        assert violations == []

    def test_unknown_node_type_is_violation(self):
        from mas.library.kg.core.verifier import check_unknown_node_types

        nodes = [{"node_type": "CompletelyFakeNodeXYZ", "callId": "x"}]
        ok, violations = check_unknown_node_types(nodes, [])
        assert not ok
        assert any("CompletelyFakeNodeXYZ" in str(v) for v in violations)

    def test_missing_node_type_is_violation(self):
        from mas.library.kg.core.verifier import check_unknown_node_types

        nodes = [{"callId": "x"}]
        ok, violations = check_unknown_node_types(nodes, [])
        assert not ok

    def test_empty_nodes_passes(self):
        from mas.library.kg.core.verifier import check_unknown_node_types

        ok, violations = check_unknown_node_types([], [])
        assert ok


# ---------------------------------------------------------------------------
# check_unknown_edge_types
# ---------------------------------------------------------------------------


class TestCheckUnknownEdgeTypes:
    def test_known_edge_type_passes(self):
        from mas.library.kg.core.verifier import KNOWN_EDGE_TYPES, check_unknown_edge_types

        assert KNOWN_EDGE_TYPES
        etype = next(iter(KNOWN_EDGE_TYPES))
        edges = [{"edge_type": etype, "from_id": "a", "to_id": "b"}]
        ok, violations = check_unknown_edge_types([], edges)
        assert ok

    def test_unknown_edge_type_is_violation(self):
        from mas.library.kg.core.verifier import check_unknown_edge_types

        edges = [{"edge_type": "completelyFakeEdge", "from_id": "a", "to_id": "b"}]
        ok, violations = check_unknown_edge_types([], edges)
        assert not ok
        assert any("completelyFakeEdge" in str(v) for v in violations)

    def test_missing_edge_type_is_violation(self):
        from mas.library.kg.core.verifier import check_unknown_edge_types

        edges = [{"from_id": "a", "to_id": "b"}]  # no edge_type
        ok, violations = check_unknown_edge_types([], edges)
        assert not ok

    def test_empty_edges_passes(self):
        from mas.library.kg.core.verifier import check_unknown_edge_types

        ok, _ = check_unknown_edge_types([], [])
        assert ok


# ---------------------------------------------------------------------------
# check_block_vocabulary
# ---------------------------------------------------------------------------


class TestCheckBlockVocabulary:
    def test_no_block_attribute_passes(self):
        from mas.library.kg.core.verifier import check_block_vocabulary

        nodes = [_node("c", "AgentCall")]
        ok, _ = check_block_vocabulary(nodes, [])
        assert ok

    def test_invalid_block_value_is_violation(self):
        from mas.library.kg.core.verifier import _VALID_BLOCK_VALUES, check_block_vocabulary

        assert _VALID_BLOCK_VALUES
        nodes = [_node("c", "AgentCall", block="notAValidValue")]
        ok, violations = check_block_vocabulary(nodes, [])
        assert not ok

    def test_empty_nodes_passes(self):
        from mas.library.kg.core.verifier import check_block_vocabulary

        ok, _ = check_block_vocabulary([], [])
        assert ok


# ---------------------------------------------------------------------------
# check_layer_vocabulary
# ---------------------------------------------------------------------------


class TestCheckLayerVocabulary:
    def test_no_layer_attribute_passes(self):
        from mas.library.kg.core.verifier import check_layer_vocabulary

        nodes = [_node("c", "AgentCall")]
        ok, _ = check_layer_vocabulary(nodes, [])
        assert ok

    def test_layer_on_non_trajectory_node_is_violation(self):
        from mas.library.kg.core.verifier import _TRAJECTORY_NODE_TYPES, check_layer_vocabulary

        # AgentCall is an execution element, not trajectory
        nodes = [_node("c", "AgentCall", layer="normalized")]
        assert "AgentCall" not in _TRAJECTORY_NODE_TYPES
        ok, violations = check_layer_vocabulary(nodes, [])
        assert not ok

    def test_layer_on_state_node_passes(self):
        from mas.library.kg.core.verifier import _TRAJECTORY_NODE_TYPES, check_layer_vocabulary

        assert "State" in _TRAJECTORY_NODE_TYPES
        state = {"node_type": "State", "stateNodeId": "s1", "layer": "normalized"}
        ok, _ = check_layer_vocabulary([state], [])
        assert ok
