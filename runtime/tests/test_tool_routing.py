#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.runtime.engine.tool_routing import (
    ProviderClaim,
    ToolRouteConflictError,
    UnclaimedToolError,
    build_tool_routes,
    is_star_claim,
    owner_for_tool,
    tool_server_claim,
)


def test_no_external_implicit_local():
    routes = build_tool_routes(["web-search", "calc"], [])
    assert routes["web-search"].provider_name == "local"
    assert routes["calc"].via == "local"


def test_star_claim_is_discovery():
    assert is_star_claim("*")
    assert is_star_claim(("*",))
    assert not is_star_claim(("web-search",))


def test_external_star_without_local_overlay_errors_on_leftover():
    with pytest.raises(UnclaimedToolError, match="not claimed by any provider"):
        build_tool_routes(
            ["web-search", "calc"],
            [ProviderClaim("search", ("web-search",), "*", object())],
        )


def test_external_star_plus_local_star_keeps_leftover_in_process():
    ext = object()
    routes = build_tool_routes(
        ["web-search", "calc"],
        [
            ProviderClaim("search", ("web-search",), "*", ext),
            ProviderClaim("in-process", ("web-search", "calc"), "*", None, origin="local"),
        ],
    )
    assert routes["web-search"].provider_name == "search"
    assert routes["web-search"].via == "discover"
    assert routes["calc"].provider_name == "in-process"
    assert routes["calc"].via == "local"
    assert routes["calc"].handler is None


def test_system_tools_stay_implicit_local_when_external_present():
    ext = object()
    routes = build_tool_routes(
        ["web-search", "request_human_input", "inform_user"],
        [ProviderClaim("search", ("web-search",), "*", ext)],
    )
    assert routes["web-search"].handler is ext
    assert routes["request_human_input"].provider_name == "local"
    assert routes["inform_user"].via == "local"


def test_explicit_claim_beats_star():
    a, b = object(), object()
    routes = build_tool_routes(
        ["web-search"],
        [
            ProviderClaim("star", ("web-search", "other"), "*", a),
            ProviderClaim("named", ("web-search",), ("web-search",), b),
        ],
    )
    assert routes["web-search"].provider_name == "named"
    assert routes["web-search"].via == "explicit"
    assert routes["other"].provider_name == "star"


def test_two_stars_same_name_conflict():
    with pytest.raises(ToolRouteConflictError, match="advertised by both"):
        build_tool_routes(
            [],
            [
                ProviderClaim("a", ("web-search",), "*", object()),
                ProviderClaim("b", ("web-search",), "*", object()),
            ],
        )


def test_two_explicit_same_name_conflict():
    with pytest.raises(ToolRouteConflictError, match="claimed explicitly"):
        build_tool_routes(
            [],
            [
                ProviderClaim("a", ("web-search",), ("web-search",), object()),
                ProviderClaim("b", ("web-search",), ("web-search",), object()),
            ],
        )


def test_two_providers_different_tools():
    a, b = object(), object()
    routes = build_tool_routes(
        ["web-search", "lookup-schedule"],
        [
            ProviderClaim("search", ("web-search",), ("web-search",), a),
            ProviderClaim("sched", ("lookup-schedule",), "*", b),
        ],
    )
    assert routes["web-search"].provider_name == "search"
    assert routes["lookup-schedule"].provider_name == "sched"


def test_tool_server_star_and_unlisted_are_star_claims():
    star = tool_server_claim("a", {"tools": "*"})
    missing = tool_server_claim("a", {})
    empty = tool_server_claim("a", {"tools": ""})
    listed = tool_server_claim("a", {"tools": ["b_tool"]})
    assert is_star_claim(star.claim)
    assert is_star_claim(missing.claim)
    assert is_star_claim(empty.claim)
    assert not is_star_claim(listed.claim)
    assert listed.claim == ("b_tool",)


def test_owner_for_tool_explicit_beats_star_and_unlisted():
    star = tool_server_claim("a", {"tools": "*"})
    unlisted = tool_server_claim("a", {})
    named = tool_server_claim("b", {"tools": ["b_tool"]})
    assert owner_for_tool("b_tool", [star, named]) == "b"
    assert owner_for_tool("b_tool", [unlisted, named]) == "b"
    assert owner_for_tool("other", [star, named]) == "a"
    assert owner_for_tool("other", [unlisted, named]) == "a"


def test_owner_for_tool_conflict_is_unclaimed():
    a = tool_server_claim("a", {"tools": ["shared"]})
    b = tool_server_claim("b", {"tools": ["shared"]})
    star_a = tool_server_claim("a", {"tools": "*"})
    star_b = tool_server_claim("b", {"tools": "*"})
    assert owner_for_tool("shared", [a, b]) is None
    assert owner_for_tool("shared", [star_a, star_b]) is None


def test_owner_for_tool_star_with_advertised_names_does_not_claim_unknown():
    star = tool_server_claim("a", {"tools": "*", "advertised": ["a_tool"]})
    assert star.advertised == ("a_tool",)
    assert owner_for_tool("a_tool", [star]) == "a"
    assert owner_for_tool("other", [star]) is None
    assert owner_for_tool("request_human_input", [star]) is None


def test_owner_for_tool_star_does_not_claim_system_tools():
    star = tool_server_claim("a", {"tools": "*"})
    unlisted = tool_server_claim("a", {})
    empty = tool_server_claim("a", {"tools": ""})
    listed = tool_server_claim("a", {"tools": ["request_human_input"]})
    for claim in (star, unlisted, empty):
        assert owner_for_tool("request_human_input", [claim]) is None
        assert owner_for_tool("inform_user", [claim]) is None
        assert owner_for_tool("other", [claim]) == "a"
    assert owner_for_tool("request_human_input", [listed]) == "a"
    assert owner_for_tool("", [star]) is None
    assert owner_for_tool("  ", [star]) is None


def test_star_with_empty_advertisement_claims_nothing():
    with pytest.raises(UnclaimedToolError, match="web-search"):
        build_tool_routes(
            ["web-search"],
            [ProviderClaim("empty", (), "*", object())],
        )
