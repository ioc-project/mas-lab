#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ControlCapability.intersect cannot grant methods or sessions the parent lacks."""

from mas.runtime.boundary.control.contract import ControlCapability


def test_intersect_drops_methods_and_sessions_the_parent_lacks() -> None:
    parent = ControlCapability(
        actor="admin",
        surface="admin",
        methods=frozenset({"steer", "inspect"}),
        session_ids=frozenset({"s1"}),
    )
    child = ControlCapability(
        actor="peer",
        surface="peer",
        methods=frozenset({"steer", "pause"}),
        session_ids=frozenset({"s1", "s2"}),
    )
    narrowed = parent.intersect(child)
    assert narrowed.actor == "peer"
    assert narrowed.surface == "peer"
    assert narrowed.methods == frozenset({"steer"})
    assert narrowed.session_ids == frozenset({"s1"})
    assert narrowed.allows("steer", "s1")
    assert not narrowed.allows("pause", "s1")
    assert not narrowed.allows("steer", "s2")
    assert not narrowed.allows("inspect", "s1")


def test_intersect_restricted_parent_keeps_methods_when_child_is_unrestricted() -> None:
    parent = ControlCapability(
        actor="admin",
        surface="admin",
        methods=frozenset({"steer"}),
        session_ids=frozenset({"s1"}),
    )
    child = ControlCapability(
        actor="peer",
        surface="peer",
        methods=None,
        session_ids=frozenset({"s1"}),
    )
    narrowed = parent.intersect(child)
    assert narrowed.methods == frozenset({"steer"})
    assert narrowed.allows("steer", "s1")
    assert not narrowed.allows("pause", "s1")


def test_intersect_restricted_parent_keeps_sessions_when_child_is_unrestricted() -> None:
    parent = ControlCapability(
        actor="admin",
        surface="admin",
        methods=frozenset({"steer"}),
        session_ids=frozenset({"s1"}),
    )
    child = ControlCapability(
        actor="peer",
        surface="peer",
        methods=frozenset({"steer"}),
        session_ids=None,
    )
    narrowed = parent.intersect(child)
    assert narrowed.session_ids == frozenset({"s1"})
    assert narrowed.allows("steer", "s1")
    assert not narrowed.allows("steer", "s2")


def test_intersect_unrestricted_parent_keeps_child_methods() -> None:
    parent = ControlCapability(actor="admin", surface="admin")
    child = ControlCapability(
        actor="peer",
        surface="peer",
        methods=frozenset({"steer"}),
        session_ids=frozenset({"s1"}),
    )
    narrowed = parent.intersect(child)
    assert narrowed.methods == frozenset({"steer"})
    assert narrowed.session_ids == frozenset({"s1"})
    assert narrowed.allows("steer", "s1")
    assert not narrowed.allows("pause", "s1")
    assert not narrowed.allows("steer", "s2")
