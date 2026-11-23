#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Each hardening check fires on a bad input and is silent on a good one."""

from __future__ import annotations

import pytest
from mas.ctl.hardening import BY_ID, CHECKS, Exposure, HardeningInput
from mas.ctl.hardening.collect import from_documents
from mas.library.standard.lib.comm.payload import MAX_MESSAGE_SIZE

GOOD_AGENT = {"governance": [{"retry_on_error": {"error_policy": {"fatal": "block"}}}]}


def _problems(check_id: str, inp: HardeningInput) -> list[str]:
    return BY_ID[check_id].evaluate(inp)


def _inp(**kw) -> HardeningInput:
    kw.setdefault("agents", {"a": GOOD_AGENT})
    return HardeningInput(**kw)


def test_registry_has_unique_ids_severities_rationale_and_fix() -> None:
    assert len({c.id for c in CHECKS}) == len(CHECKS)
    for check in CHECKS:
        assert check.severity in {"critical", "high", "medium"} and check.scope in {"static", "runtime"}
        assert check.rationale.strip() and check.fix.strip()


def test_a_clean_input_passes_every_check() -> None:
    # kills: a check that fires on an empty, permissive-by-nature session
    for check in CHECKS:
        assert check.evaluate(_inp()) == [], check.id


@pytest.mark.parametrize(
    ("host", "identity", "fires"),
    [
        ("0.0.0.0", "none", True),
        ("0.0.0.0", "empty", True),
        ("0.0.0.0", "verified", False),
        ("127.0.0.1", "none", False),
    ],
)
def test_listener_without_identity(host, identity, fires) -> None:
    # kills: loopback treated as exposed, an empty policy treated as a policy, a verified one flagged
    problems = _problems(
        "listener-without-identity", _inp(exposures=(Exposure("agent", "a2a", f"http://{host}:1", host, identity),))
    )
    assert bool(problems) is fires


def test_ingress_errors_unclassified() -> None:
    # kills: an agent without retry_on_error / backtrack_on_error passing; one with it failing
    assert _problems("ingress-errors-unclassified", _inp(agents={"a": {}}))
    assert _problems("ingress-errors-unclassified", _inp(agents={"a": {"governance": ["budget"]}}))
    assert not _problems("ingress-errors-unclassified", _inp(agents={"a": {"governance": ["gov_retry_on_error"]}}))
    assert not _problems("ingress-errors-unclassified", _inp(agents={"a": {"governance": ["backtrack_on_error"]}}))


@pytest.mark.parametrize("mode", ["auto-approve", "auto_approve", "AUTO-APPROVE"])
def test_hitl_auto_approve_found_in_either_governance_shape(mode) -> None:
    # kills: reading only one governance shape, or one spelling of the mode
    assert _problems("hitl-auto-approve", _inp(agents={"a": {"governance": {"hitl_on_tool": True, "hitl_mode": mode}}}))
    assert _problems(
        "hitl-auto-approve", _inp(agents={"a": {"governance": [{"sample_governance": {"hitl_mode": mode}}]}})
    )
    assert not _problems("hitl-auto-approve", _inp(agents={"a": {"governance": {"hitl_mode": "interactive"}}}))
    assert not _problems("hitl-auto-approve", _inp(agents={"a": {"governance": {"hitl_mode": "auto-deny"}}}))


def test_llm_cache_replay() -> None:
    # kills: ignoring the cache step, the engine flag, or an explicit enabled: false / allow_read: false
    step = lambda **p: {"layer": "llm_cache", "params": p}  # noqa: E731
    assert _problems("llm-cache-replay", _inp(llm=({"name": "m", "steps": [step(allow_read=True)]},)))
    assert _problems("llm-cache-replay", _inp(llm=({"name": "m", "steps": [step()]},)))
    assert not _problems("llm-cache-replay", _inp(llm=({"name": "m", "steps": [step(allow_read=False)]},)))
    assert _problems("llm-cache-replay", _inp(engine_cache={"read": True}))
    assert not _problems("llm-cache-replay", _inp(engine_cache={"read": True, "enabled": False}))
    assert not _problems("llm-cache-replay", _inp(engine_cache={"write": True}))


def test_plain_http_remote() -> None:
    # kills: flagging loopback, missing ws://, flagging https
    urls = (
        ("a", "http://gw.example.com/v1"),
        ("b", "ws://x.example.com"),
        ("c", "https://ok.example.com"),
        ("d", "http://localhost:9"),
        ("e", "http://[::1]:9"),
    )
    problems = _problems("plain-http-remote", _inp(urls=urls))
    assert len(problems) == 2 and all("not TLS" in p for p in problems)


def test_tls_verification_disabled() -> None:
    # kills: missing nested keys, dev on SLIM rows, and flagging verify: true
    off = (
        ("x", {"verify_ssl": False}),
        ("y", {"deep": [{"verify": False}]}),
        ("z", {"dev": True}),
        ("w", {"insecure": True}),
    )
    assert len(_problems("tls-verification-disabled", _inp(settings=off))) == 4
    on = (("x", {"verify_ssl": True, "verify": True, "dev": False, "ca_bundle": "/etc/ca.pem"}),)
    assert not _problems("tls-verification-disabled", _inp(settings=on))


def test_mcp_tools_wildcard() -> None:
    # kills: the default (no tools key) passing; an explicit list failing; non-MCP rows failing
    rows = {
        "open": {"protocol": "mcp"},
        "star": {"protocol": "mcp", "tools": "*"},
        "listed": {"protocol": "mcp", "tools": ["search"]},
        "local": {"protocol": "local"},
    }
    assert len(_problems("mcp-tools-wildcard", _inp(tool_servers=rows))) == 2


def test_secret_store_loose_permissions_names_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.ctl.hardening import checks

    monkeypatch.setattr(checks.sys, "platform", "win32")
    assert "Windows" in _problems("secret-store-loose-permissions", _inp(allow_loose_permissions=True))[0]


def test_secret_store_env_and_loose_permissions() -> None:
    assert _problems("secret-store-env", _inp(secret_store="env"))
    assert not _problems("secret-store-env", _inp())
    assert _problems("secret-store-loose-permissions", _inp(allow_loose_permissions=True))
    assert not _problems("secret-store-loose-permissions", _inp(secret_store="env", allow_loose_permissions=True))
    assert not _problems("secret-store-loose-permissions", _inp())


@pytest.mark.parametrize("value", [0, -1, MAX_MESSAGE_SIZE + 1, None, True, "big"])
def test_sizes_unbounded_fires_on_a_bad_cap(value) -> None:
    # kills: accepting zero, negative, over-limit, null or non-integer caps
    assert _problems("sizes-unbounded", _inp(settings=(("row", {"nested": {"max_message_size": value}}),)))


def test_sizes_unbounded_is_silent_on_the_shared_cap_or_no_cap() -> None:
    assert not _problems("sizes-unbounded", _inp(settings=(("row", {"max_message_size": MAX_MESSAGE_SIZE}),)))
    assert not _problems("sizes-unbounded", _inp(settings=(("row", {"timeout": 3}),)))


def test_tool_retry_not_idempotent() -> None:
    # kills: the default (require_idempotent true) failing; false passing; LLM retry being judged
    step = lambda **p: {"layer": "retry", "params": p}  # noqa: E731
    assert _problems(
        "tool-retry-not-idempotent", _inp(tool=({"name": "t", "steps": [step(require_idempotent=False)]},))
    )
    assert not _problems(
        "tool-retry-not-idempotent", _inp(tool=({"name": "t", "steps": [step(require_idempotent=True)]},))
    )
    assert not _problems("tool-retry-not-idempotent", _inp(tool=({"name": "t", "steps": [step(max_attempts=2)]},)))
    assert not _problems(
        "tool-retry-not-idempotent", _inp(llm=({"name": "m", "steps": [step(require_idempotent=False)]},))
    )


def test_from_documents_reads_manifest_files_alone() -> None:
    # kills: the validate --profile path seeing nothing of the files it was given
    docs = [
        (
            "agent.yaml",
            {"kind": "Agent", "metadata": {"name": "a"}, "spec": {"governance": {"hitl_mode": "auto-approve"}}},
        ),
        (
            "app.yaml",
            {
                "kind": "Application",
                "spec": {"endpoints": {"a": {"protocol": "a2a", "usage": "listen", "url": "http://0.0.0.0:9"}}},
            },
        ),
        (
            "tools.yaml",
            {
                "kind": "ToolServerRegistry",
                "spec": {"tool_servers": [{"id": "m", "protocol": "mcp", "url": "http://x.example.com/mcp"}]},
            },
        ),
        (
            "llm.yaml",
            {
                "kind": "LLMProxy",
                "metadata": {"name": "p"},
                "spec": {"proxy": {"api_base": "http://llm.example.com", "verify_ssl": False}},
            },
        ),
    ]
    inp = from_documents(docs)
    for check in (
        "hitl-auto-approve",
        "listener-without-identity",
        "mcp-tools-wildcard",
        "plain-http-remote",
        "tls-verification-disabled",
    ):
        assert _problems(check, inp), check


def test_a_listening_chain_row_is_verified_only_when_it_carries_a_secret() -> None:
    # kills: chain rows ignored as listeners, or a row with no secret read as verified
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.hardening.collect import from_session

    def chain(params: dict, usage: list[str]) -> ResolvedInfra:
        row = {"row": "node", "type": "slim", "params": params, "usage": usage}
        return ResolvedInfra(agent_chains={"a": [row]})

    open_node = from_session({}, chain({"url": "https://0.0.0.0:46357"}, ["listen"]))
    secured = from_session({}, chain({"url": "https://0.0.0.0:46357", "secret": "x" * 40}, ["connect", "listen"]))
    client_only = from_session({}, chain({"url": "https://0.0.0.0:46357"}, ["connect"]))
    assert _problems("listener-without-identity", open_node)
    assert not _problems("listener-without-identity", secured)
    assert not client_only.exposures


def test_an_admission_policy_is_read_from_the_row_as_none_empty_or_verified() -> None:
    # kills: an empty policy counted as a verified one when the row is read into an exposure
    from mas.ctl.hardening.collect import a2a_exposure

    row = {"protocol": "a2a", "usage": "listen", "url": "http://0.0.0.0:9"}
    assert a2a_exposure("a", row).identity == "none"
    for admission in ({}, {"callers": {}}, {"callers": None}, None):
        assert a2a_exposure("a", {**row, "a2a": {"admission": admission}}).identity == "empty"
    assert a2a_exposure("a", {**row, "a2a": {"admission": {"callers": {"x": "t" * 40}}}}).identity == "verified"
    assert a2a_exposure("a", {**row, "a2a": {"listen": {"host": "127.0.0.1"}}}).loopback


def test_from_documents_survives_malformed_manifests() -> None:
    # kills: a traceback from `validate --profile` on a manifest with a wrongly typed field
    docs = [
        ("a.yaml", {"kind": "LLMProxy", "spec": {"proxy": "x"}}),
        ("b.yaml", {"kind": "Agent", "spec": ["x"]}),
        ("c.yaml", {"kind": "Application", "metadata": "x", "spec": {"endpoints": ["x"]}}),
        ("d.yaml", {"kind": "ToolServerRegistry", "spec": {"tool_servers": {"a": 1}}}),
    ]
    from_documents(docs)
