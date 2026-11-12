#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Reliability knobs: classify, retry, circuit breaker, spec bindings."""

from __future__ import annotations

import httpx
import pytest
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import classify_http_exception, classify_tool_exception
from mas.runtime.reliability.policy import (
    ReliabilitySettings,
    RetryPolicy,
)
from mas.runtime.reliability.retry import call_with_retry
from mas.runtime.spec.gov import SpecBindingError, build_kernel_config, parse_gov_spec


def test_retry_policy_every_knob_parses() -> None:
    policy = RetryPolicy.from_mapping(
        {
            "max_attempts": 6,
            "backoff_s": 0.1,
            "backoff_multiplier": 3.0,
            "jitter": False,
            "retry_on": ["transient", "unavailable"],
            "require_idempotent": True,
            "max_backoff_s": 1.5,
        },
        field_name="retry",
    )
    assert policy.max_attempts == 6
    assert policy.backoff_s == 0.1
    assert policy.backoff_multiplier == 3.0
    assert policy.jitter is False
    assert policy.retry_on == (FailureClass.TRANSIENT, FailureClass.UNAVAILABLE)
    assert policy.require_idempotent is True
    assert policy.max_backoff_s == 1.5
    assert policy.delay_for(0) == 0.1
    assert policy.delay_for(4) == 1.5  # capped


def test_retry_policy_unknown_field_raises() -> None:
    with pytest.raises(SpecBindingError, match="unknown field"):
        RetryPolicy.from_mapping({"retries": 3}, field_name="retry")


def test_llm_default_retries_unavailable() -> None:
    policy = RetryPolicy.llm_default()
    assert policy.retry_on == (FailureClass.TRANSIENT, FailureClass.UNAVAILABLE)
    assert policy.allows(FailureClass.UNAVAILABLE, idempotent=True)
    assert policy.allows(FailureClass.TRANSIENT, idempotent=True)


def test_kernel_config_does_not_own_reliability() -> None:
    from mas.runtime.kernel.config import KernelConfig

    fields = KernelConfig.__dataclass_fields__
    assert "circuit_breaker" not in fields
    assert "reliability" not in fields
    assert "error_policy" not in fields
    assert "error_recovery_plugin" not in fields


def test_circuit_breaker_absent_adds_no_layer() -> None:
    from mas.runtime.layers.control import pipeline_from_agent_spec

    steps = pipeline_from_agent_spec({"control": {}}, [], kind="llm")
    assert [step["layer"] for step in steps] == ["retry"]


def test_error_policy_every_class() -> None:
    from mas.library.standard.plugins.governance.retry_on_error import ErrorPolicy

    policy = ErrorPolicy.from_mapping(
        {
            "transient": "retry",
            "unavailable": "block",
            "application": "skip",
            "fatal": "allow",
        }
    )
    assert policy.action_for(FailureClass.TRANSIENT) == "retry"
    assert policy.action_for(FailureClass.UNAVAILABLE) == "block"
    assert policy.action_for(FailureClass.APPLICATION) == "skip"
    assert policy.action_for(FailureClass.FATAL) == "allow"


def test_error_policy_rejects_unknown_action() -> None:
    from mas.library.standard.plugins.governance.retry_on_error import ErrorPolicy

    with pytest.raises(SpecBindingError, match="must be one of"):
        ErrorPolicy.from_mapping({"transient": "explode"})


def test_from_spec_reads_the_llm_retry_slot() -> None:
    settings = ReliabilitySettings.from_spec({"control": {"retry": {"llm": {"max_attempts": 9}}}})
    assert settings.llm_retry.max_attempts == 9
    assert settings.tool_retry.max_attempts == 2  # tools default


def test_from_spec_omitted_llm_retry_is_default() -> None:
    settings = ReliabilitySettings.from_spec({"control": {}})
    assert settings.llm_retry.max_attempts == RetryPolicy.llm_default().max_attempts


def test_from_spec_rejects_unknown_retry_slot() -> None:
    with pytest.raises(SpecBindingError, match="unknown field"):
        ReliabilitySettings.from_spec({"control": {"retry": {"http": {}}}})


def test_classify_http_status_and_timeout() -> None:
    timeout = classify_http_exception(httpx.TimeoutException("boom"))
    assert timeout.failure_class is FailureClass.TRANSIENT
    assert timeout.code == "TIMEOUT"
    resp = httpx.Response(429, request=httpx.Request("POST", "https://x"))
    limited = classify_http_exception(httpx.HTTPStatusError("n", request=resp.request, response=resp))
    assert limited.failure_class is FailureClass.TRANSIENT
    auth = httpx.Response(401, request=httpx.Request("POST", "https://x"))
    fatal = classify_http_exception(httpx.HTTPStatusError("n", request=auth.request, response=auth))
    assert fatal.failure_class is FailureClass.FATAL


def test_classify_unknown_tool_is_application() -> None:
    from mas.runtime.engine.manifest_tool_provider import ManifestToolLoadError

    classified = classify_tool_exception(ManifestToolLoadError("nope"))
    assert classified.failure_class is FailureClass.APPLICATION
    assert classified.code == "TOOL_UNKNOWN"


def test_classify_explicit_unavailable() -> None:
    from mas.runtime.engine.tool_routing import ExplicitToolUnavailableError

    classified = classify_tool_exception(ExplicitToolUnavailableError("down"))
    assert classified.failure_class is FailureClass.UNAVAILABLE


def test_call_with_retry_respects_require_idempotent() -> None:
    attempts = {"n": 0}

    def boom() -> None:
        attempts["n"] += 1
        raise ClassifiedFailure("x", failure_class=FailureClass.TRANSIENT, code="T")

    policy = RetryPolicy(max_attempts=4, retry_on=(FailureClass.TRANSIENT,), require_idempotent=True)
    with pytest.raises(ClassifiedFailure):
        call_with_retry(boom, policy=policy, classify=lambda e: e, idempotent=False)  # type: ignore[arg-type,return-value]
    assert attempts["n"] == 1


def test_call_with_retry_retries_transient_when_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mas.runtime.reliability.retry.time.sleep", lambda _s: None)
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ClassifiedFailure("x", failure_class=FailureClass.TRANSIENT, code="T")
        return "ok"

    policy = RetryPolicy(max_attempts=4, backoff_s=0, jitter=False)
    assert call_with_retry(flaky, policy=policy, classify=lambda e: e) == "ok"  # type: ignore[arg-type,return-value]
    assert attempts["n"] == 3


def test_build_kernel_config_wires_error_policy_and_plugin() -> None:
    from mas.library.standard.plugins.governance.retry_on_error import RetryOnErrorPlugin
    from mas.runtime.registry import get_registry, register_plugin

    if get_registry().resolve_by_type("governance", "retry_on_error") is None:
        register_plugin(
            "mas.gov.retry_on_error",
            RetryOnErrorPlugin,
            shortcuts=["retry_on_error"],
            attributes={"plugin_type": "governance"},
        )
    binding = parse_gov_spec(
        [
            {
                "retry_on_error": {
                    "error_recovery_plugin": "retry_on_error",
                    "error_policy": {
                        "transient": "retry",
                        "unavailable": "retry",
                        "application": "allow",
                        "fatal": "block",
                    },
                }
            }
        ]
    )
    assert binding.error_recovery_plugin == "retry_on_error"
    assert binding.error_policy["transient"] == "retry"
    spec = {
        "control": {
            "retry": {"llm": {"max_attempts": 5}, "tools": {"max_attempts": 3, "require_idempotent": False}},
            "circuit_breaker": {"failure_threshold": 2, "reset_timeout_s": 1, "on": ["unavailable"]},
        }
    }
    config = build_kernel_config(binding, agent_spec=spec)
    settings = ReliabilitySettings.from_spec(spec)
    assert settings.llm_retry.max_attempts == 5
    assert settings.tool_retry.max_attempts == 3
    from mas.runtime.layers.control import pipeline_from_agent_spec

    breaker = next(s for s in pipeline_from_agent_spec(spec, [], kind="llm") if s["layer"] == "circuit_breaker")
    assert breaker["params"]["failure_threshold"] == 2
    recovery = [
        entry.plugin
        for entry in config.ingress_governance_plugins
        if getattr(entry.plugin, "plugin_id", "").startswith("retry_on_error")
    ]
    assert recovery
    assert recovery[0]._policy.transient == "retry"


def test_ingress_error_policy_retry_then_allow() -> None:
    from mas.library.standard.plugins.governance.retry_on_error import ErrorPolicy, RetryOnErrorPlugin
    from mas.runtime.boundary.gov.filter import GovTransitionFilter
    from mas.runtime.boundary.gov.ingress_chain import RegisteredIngressPlugin, evaluate_ingress_chain
    from mas.runtime.boundary.gov.ingress_plugin import IngressIntentView
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile

    config = KernelConfig(
        ingress_governance_plugins=(
            RegisteredIngressPlugin(
                plugin=RetryOnErrorPlugin(
                    error_policy=ErrorPolicy(transient="retry", fatal="block"),
                ),
                filter=GovTransitionFilter(hook="ingress", response_kind=("ERROR",)),
            ),
        )
    )
    retry = evaluate_ingress_chain(
        IngressIntentView(
            response_kind="ERROR",
            failure_class="transient",
            retry_count=0,
            max_retries=2,
            profile=GovIngressProfile.PERMISSIVE,
        ),
        config=config,
    )
    assert retry.action == GovernanceAction.RETRY
    exhausted = evaluate_ingress_chain(
        IngressIntentView(
            response_kind="ERROR",
            failure_class="transient",
            retry_count=2,
            max_retries=2,
            profile=GovIngressProfile.PERMISSIVE,
        ),
        config=config,
    )
    assert exhausted.action == GovernanceAction.ALLOW
    blocked = evaluate_ingress_chain(
        IngressIntentView(
            response_kind="ERROR",
            failure_class="fatal",
            retry_count=0,
            max_retries=2,
            profile=GovIngressProfile.PERMISSIVE,
        ),
        config=config,
    )
    assert blocked.action == GovernanceAction.BLOCK


def test_application_tool_failure_stays_allow_by_default() -> None:
    from mas.runtime.boundary.gov.ingress_plugin import IngressIntentView, ingress_from_profile
    from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile

    decision = ingress_from_profile(
        IngressIntentView(
            response_kind="TOOL_RESULT",
            failure_class="application",
            failure_code="TOOL_ERROR",
            profile=GovIngressProfile.PERMISSIVE,
        ),
    )
    assert decision.action == GovernanceAction.ALLOW


def test_typed_error_without_plugin_follows_ingress_profile() -> None:
    from mas.runtime.boundary.gov.ingress_plugin import IngressIntentView, ingress_from_profile
    from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile

    decision = ingress_from_profile(
        IngressIntentView(
            response_kind="ERROR",
            failure_class="fatal",
            profile=GovIngressProfile.PERMISSIVE,
        ),
    )
    assert decision.action == GovernanceAction.ALLOW


def test_retry_logs_reattempt_then_recovery(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    monkeypatch.setattr("mas.runtime.reliability.retry.time.sleep", lambda _s: None)
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ClassifiedFailure("x", failure_class=FailureClass.TRANSIENT, code="TIMEOUT")
        return "ok"

    policy = RetryPolicy(max_attempts=4, backoff_s=0, jitter=False)
    with caplog.at_level(logging.INFO, logger="mas.runtime.reliability"):
        assert call_with_retry(flaky, policy=policy, classify=lambda e: e, target="LLM HTTP POST") == "ok"  # type: ignore[arg-type,return-value]
    messages = [r.getMessage() for r in caplog.records]
    assert any("retrying" in m for m in messages)
    assert any("succeeded after 2 attempts" in m for m in messages)
    extras = [getattr(r, "mas.outcome", None) for r in caplog.records]
    assert "retry" in extras
    assert "recovered" in extras


def test_exhausted_retry_logs_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    monkeypatch.setattr("mas.runtime.reliability.retry.time.sleep", lambda _s: None)

    def boom() -> None:
        raise ClassifiedFailure("x", failure_class=FailureClass.TRANSIENT, code="TIMEOUT")

    policy = RetryPolicy(max_attempts=2, backoff_s=0, jitter=False)
    with caplog.at_level(logging.WARNING, logger="mas.runtime.reliability"):
        with pytest.raises(ClassifiedFailure):
            call_with_retry(boom, policy=policy, classify=lambda e: e, target="tool:x")  # type: ignore[arg-type,return-value]
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert any(getattr(r, "mas.outcome", None) == "exhausted" for r in caplog.records)
