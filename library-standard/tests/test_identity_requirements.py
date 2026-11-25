#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity gate's requirement grammar: operators, freshness, value-free reasons."""

from __future__ import annotations

import pytest
from mas.library.standard.lib.identity.contract import (
    PublicView,
)
from mas.library.standard.lib.identity.requirements import OPERATORS, evaluate_requirement
from mas.library.standard.testing.identity_fakes import CANARY, FAKE_NOW

VIEW = PublicView(facts={"grade": "gold", "calls": 40, "limits": {"depth": 3}, "flag": True, "scopes": ["read"]})


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ({"fact": "grade", "equals": "gold"}, None),
        ({"fact": "grade", "equals": "silver"}, "fact_mismatch:grade"),
        ({"fact": "grade", "in": ["gold", "silver"]}, None),
        ({"fact": "grade", "in": ["bronze"]}, "fact_not_in:grade"),
        ({"fact": "calls", "min": 40}, None),
        ({"fact": "calls", "min": 41}, "fact_below_min:calls"),
        ({"fact": "calls", "max": 40}, None),
        ({"fact": "calls", "max": 39}, "fact_above_max:calls"),
        ({"fact": "calls", "min": 10, "max": 50}, None),
        ({"fact": "grade", "min": 1}, "fact_not_number:grade"),
        ({"fact": "flag", "min": 0}, "fact_not_number:flag"),
        ({"fact": "limits.depth", "equals": 3}, None),
        ({"fact": "limits.width", "exists": False}, None),
        ({"fact": "limits.width", "exists": True}, "fact_missing:limits.width"),
        ({"fact": "grade", "exists": False}, "fact_present:grade"),
        ({"fact": "grade", "exists": True}, None),
        ({"fact": "absent", "equals": 1}, "fact_missing:absent"),
        ({"fact": "scopes", "equals": ["read"]}, None),
        ({"fact": "Weird Name!", "equals": 1}, "fact_missing:weird_name_"),
        ({"fact": "grade", "bogus": 1}, "invalid"),
        ({"fact": "grade"}, "invalid"),
        ({"equals": 1}, "invalid"),
        ({"fact": "", "equals": 1}, "invalid"),
        ({"fact": "grade", "in": "gold"}, "invalid"),
        ({"fact": "calls", "min": "ten"}, "invalid"),
        ({"fact": "grade", "exists": "yes"}, "invalid"),
        ("text", "invalid"),
    ],
)
def test_the_generic_grammar(requirement, expected) -> None:
    assert evaluate_requirement(requirement, VIEW) == expected


def test_a_view_without_facts_satisfies_nothing_but_absence() -> None:
    assert evaluate_requirement({"fact": "a", "equals": 1}, PublicView()) == "fact_missing:a"
    assert evaluate_requirement({"fact": "a", "exists": False}, PublicView()) is None
    assert OPERATORS == {"equals", "in", "contains", "min", "max", "exists", "fresh_within_s"}


def test_a_reason_never_holds_an_operand_or_a_fact_value() -> None:
    for requirement in (
        {"fact": "grade", "equals": CANARY},
        {"fact": "grade", "in": [CANARY]},
        {"fact": "calls", "min": 10**9},
    ):
        reason = evaluate_requirement(requirement, VIEW)
        assert reason and CANARY not in reason and "gold" not in reason and "40" not in reason


NOW = FAKE_NOW


def fresh(**facts):
    return PublicView(facts=facts)


@pytest.mark.parametrize(
    ("facts", "requirement", "expected"),
    [
        ({"t": NOW - 100}, {"fact": "t", "fresh_within_s": 3600}, None),
        ({"t": NOW - 3600}, {"fact": "t", "fresh_within_s": 3600}, None),
        ({"t": NOW - 3601}, {"fact": "t", "fresh_within_s": 3600}, "fact_stale:t"),
        ({"t": NOW + 30}, {"fact": "t", "fresh_within_s": 3600}, None),
        ({"t": NOW + 61}, {"fact": "t", "fresh_within_s": 3600}, "fact_in_future:t"),
        ({}, {"fact": "t", "fresh_within_s": 3600}, "fact_missing:t"),
        ({"t": "yesterday"}, {"fact": "t", "fresh_within_s": 3600}, "fact_not_number:t"),
        ({"t": True}, {"fact": "t", "fresh_within_s": 3600}, "fact_not_number:t"),
        ({"t": NOW}, {"fact": "t", "fresh_within_s": 0}, "invalid"),
        ({"t": NOW}, {"fact": "t", "fresh_within_s": "1h"}, "invalid"),
        ({"t": NOW}, {"fact": "t", "fresh_within_s": True}, "invalid"),
        # not_before / not_after are min / max on a timestamp
        ({"exp": NOW + 500}, {"fact": "exp", "min": NOW + 400}, None),
        ({"exp": NOW + 500}, {"fact": "exp", "max": NOW + 400}, "fact_above_max:exp"),
    ],
)
def test_freshness_and_timestamp_bounds(facts, requirement, expected) -> None:
    assert evaluate_requirement(requirement, fresh(**facts), now=NOW) == expected


def test_freshness_needs_the_clock_and_never_shows_the_timestamp() -> None:
    assert evaluate_requirement({"fact": "t", "fresh_within_s": 10}, fresh(t=NOW)) == "invalid"
    reason = evaluate_requirement({"fact": "t", "fresh_within_s": 10}, fresh(t=12345.0), now=NOW)
    assert reason == "fact_stale:t" and "12345" not in reason


@pytest.mark.parametrize(
    ("facts", "requirement", "expected"),
    [
        ({"scopes": ["read", "write"]}, {"fact": "scopes", "contains": "write"}, None),
        ({"scopes": ["read"]}, {"fact": "scopes", "contains": "write"}, "fact_not_in:scopes"),
        ({"group": "infraadmin"}, {"fact": "group", "contains": "admin"}, None),
        ({"group": "infraadmin"}, {"fact": "group", "contains": 3}, "fact_not_in:group"),
        ({"grade": 3}, {"fact": "grade", "contains": 3}, "fact_not_in:grade"),
        ({}, {"fact": "scopes", "contains": "read"}, "fact_missing:scopes"),
    ],
)
def test_contains_is_membership_of_a_list_or_text_and_never_shows_the_operand(facts, requirement, expected) -> None:
    """Kills: a ``contains`` that is true for a number, or that raises on a text fact with a non-text operand."""
    assert evaluate_requirement(requirement, fresh(**facts), now=NOW) == expected


def test_a_label_is_not_an_operator_and_must_be_a_short_slug() -> None:
    """Kills: a label read as an operator (``invalid``), and one that is not a short slug being accepted."""
    assert evaluate_requirement({"fact": "grade", "equals": "gold", "label": "tier-1"}, VIEW) is None
    assert evaluate_requirement({"fact": "grade", "label": "tier-1"}, VIEW) == "invalid"  # a label alone asks nothing
    for bad in ("a b", "", 7, "x" * 41):
        assert evaluate_requirement({"fact": "grade", "equals": "gold", "label": bad}, VIEW) == "invalid"
