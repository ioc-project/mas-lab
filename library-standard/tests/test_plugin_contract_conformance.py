#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every registered governance plugin and HITL responder keeps the obligations the kernel relies on.

The kernel trusts a plugin in three ways: its verdict has the contract's shape, the same input gives
the same verdict, and nothing the kernel handed over (the intent, the config, the request) is written
to. A plugin that denies keeps denying the same input. Separately, the chain keeps a deny a deny whatever
the other plugins say, and a human approval turns a HITL hold into an allow without lifting a deny.

The checks are functions that return the violations they find. The tests run them over the registry (so a
new plugin is held to them without being named here) and, as a negative control, over deliberately bad
test doubles, which must be caught: a check that cannot fail is not a check.

Stateful plugins are built in the configurations where they do deny (a budget of zero, an error policy
that blocks), and `test_the_corpus_makes_the_deny_capable_plugins_deny` fails if the corpus stops reaching a
deny, so the "no ALLOW after a deny" check cannot pass because nothing was ever denied. `gdb` never denies by
design; its other checks still run.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest
from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision, IngressIntentView
from mas.runtime.boundary.gov.plugin import GovernancePluginChain
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import EmitHitlRequest
from mas.runtime.schema.governance import GovernanceAction, GovIngressProfile
from mas.runtime.schema.hitl import HitlQuestionType, HitlResolveChoice
from mas.runtime.schema.ingress import HitlResolve

_DENY = {GovDecision.BLOCK, GovDecision.SKIP, GovDecision.TERMINATE, GovDecision.BLACKLIST}
_BLOCKING = {GovernanceAction.BLOCK, GovernanceAction.TERMINATE, GovernanceAction.SKIP, GovernanceAction.BLACKLIST}
_SPEC = {"tools": [{"name": "read_file"}], "communication": {"peers": ["b"]}}
_C = HitlResolveChoice


def _classes(kind: str) -> dict[str, type]:
    """One class per registered plugin of ``kind``, keyed by a name the registry resolves."""
    registry = get_registry()
    found: dict[str, type] = {}
    for name in registry.list_names(kind):
        info = registry.get(kind, name)
        if info is None:
            continue
        cls = info.load_class()
        if cls not in found.values():
            found[name] = cls
    return found


GOVERNANCE = _classes("governance")
RESPONDERS = _classes("hitl_responder")

# Constructor arguments per plugin: the default, and the configurations in which a stateful or
# policy-driven plugin does deny or hold. Every registered plugin has at least the default.
VARIANTS: dict[str, list[dict]] = {
    "BudgetPlugin": [{}, {"max_tool_calls": 0, "max_llm_calls": 0}],
    "RetryOnErrorPlugin": [
        {},
        {"error_policy": {"transient": "block", "unavailable": "skip", "application": "block", "fatal": "block"}},
    ],
    "SampleGovernancePlugin": [
        {},
        {"hitl_on_tool": True, "hitl_on_tool_result": True},
        {"gov_block_destructive": True},
    ],
}
# Plugins that deny some corpus input in one of their configurations.
DENY_CAPABLE = {
    "BudgetPlugin",
    "NoUndeclaredToolPlugin",
    "NoUndeclaredPeerPlugin",
    "RetryOnErrorPlugin",
    "SampleGovernancePlugin",
}


def _variants(name: str) -> list[dict]:
    return VARIANTS.get(GOVERNANCE[name].__name__, [{}])


EGRESS_CASES = [
    (n, i) for n, cls in GOVERNANCE.items() if hasattr(cls, "evaluate_egress") for i in range(len(_variants(n)))
]
INGRESS_CASES = [
    (n, i) for n, cls in GOVERNANCE.items() if hasattr(cls, "evaluate_ingress") for i in range(len(_variants(n)))
]


def _config() -> KernelConfig:
    return KernelConfig(agent_id="a", agent_spec=_SPEC)


def _egress_intents() -> list[EgressIntentView]:
    """Fresh objects each call: a plugin that edits an intent in place must not hide it behind a shared one."""
    return [
        EgressIntentView(op="LLM_CALL", destructive=False, correlation_id=1),
        EgressIntentView(op="LLM_CALL", destructive=False, correlation_id=9),
        EgressIntentView(
            op="TOOL_CALL", destructive=False, correlation_id=2, tool_name="read_file", tool_arguments={"p": 1}
        ),
        EgressIntentView(
            op="TOOL_CALL", destructive=True, correlation_id=3, tool_name="delete_file", tool_arguments={}
        ),
        EgressIntentView(
            op="TOOL_CALL",
            destructive=False,
            correlation_id=4,
            tool_name="read_file",
            tool_arguments={"p": {"nested": [1, 2]}},
            offered_tools=("read_file",),
            running_spec=_SPEC,
        ),
        EgressIntentView(
            op="TOOL_CALL", destructive=False, correlation_id=5, tool_name="not_offered", offered_tools=("read_file",)
        ),
        EgressIntentView(op="TOOL_CALL", destructive=False, correlation_id=6, tool_name="delegate_to_stranger"),
        EgressIntentView(
            op="TOOL_CALL", destructive=False, correlation_id=7, tool_name="read_file", hitl_approved=True
        ),
        EgressIntentView(op="MEMORY_OP", destructive=False, correlation_id=8),
    ]


def _ingress_intents() -> list[IngressIntentView]:
    return [
        IngressIntentView(response_kind="TOOL_RESULT"),
        IngressIntentView(response_kind="MODEL_TEXT", error_text="x"),
        IngressIntentView(response_kind="ERROR", error_text="boom", failure_class="transient", max_retries=2),
        IngressIntentView(response_kind="ERROR", error_text="boom", failure_class="unavailable", max_retries=2),
        IngressIntentView(
            response_kind="ERROR", error_text="boom", failure_class="fatal", retry_count=2, max_retries=2
        ),
        IngressIntentView(response_kind="ERROR", error_text="boom", profile=GovIngressProfile.BLOCK_ON_ERROR),
    ]


def _egress(plugin, intent):
    return plugin.evaluate_egress(intent, config=_config())


def _ingress(plugin, intent):
    return plugin.evaluate_ingress(intent, config=_config())


# --- the checks: each returns the violations it finds --------------------------------------------------


def _sequence(make, call, intents):
    """The verdicts of one fresh instance driven through the corpus in order."""
    plugin = make()
    return [call(plugin, copy.deepcopy(i)) for i in intents]


def egress_violations(make: Callable[[], object]) -> list[str]:
    """Every obligation of an egress plugin, over the corpus."""
    found: list[str] = []
    one = make()
    first_run = []
    for intent in _egress_intents():
        label = f"{intent.op}/{intent.tool_name or '-'}/{intent.correlation_id}"
        before = (copy.deepcopy(intent), copy.deepcopy(_config()))
        config = _config()
        verdict = one.evaluate_egress(intent, config=config)
        first_run.append(verdict)
        if not (
            isinstance(verdict, tuple)
            and len(verdict) == 3
            and isinstance(verdict[0], GovDecision)
            and isinstance(verdict[1], str)
            and isinstance(verdict[2], str)
        ):
            found.append(f"{label}: verdict is not (GovDecision, str, str): {verdict!r}")
            return found
        if intent != before[0] or config != before[1]:
            found.append(f"{label}: the plugin wrote to what it was handed")
        if verdict[0] in _DENY:
            again = [_egress(one, copy.deepcopy(intent))[0] for _ in range(3)]
            if any(a not in _DENY for a in again):
                found.append(f"{label}: a deny turned into {[a.value for a in again]} for the same input")
    # Same sequence, fresh instance: the same verdicts (state is allowed, drift between instances is not).
    if _sequence(make, _egress, _egress_intents()) != first_run:
        found.append("two fresh instances driven through the same inputs gave different verdicts")
    return found


def ingress_violations(make: Callable[[], object]) -> list[str]:
    found: list[str] = []
    one = make()
    first_run = []
    for intent in _ingress_intents():
        label = f"{intent.response_kind}/{intent.failure_class or intent.profile.value}"
        before = (copy.deepcopy(intent), copy.deepcopy(_config()))
        config = _config()
        verdict = one.evaluate_ingress(intent, config=config)
        first_run.append(verdict)
        if not (isinstance(verdict, IngressGovDecision) and isinstance(verdict.action, GovernanceAction)):
            found.append(f"{label}: verdict is not an IngressGovDecision: {verdict!r}")
            return found
        if intent != before[0] or config != before[1]:
            found.append(f"{label}: the plugin wrote to what it was handed")
        if verdict.action in _BLOCKING:
            again = [_ingress(one, copy.deepcopy(intent)).action for _ in range(3)]
            if any(a not in _BLOCKING for a in again):
                found.append(f"{label}: a deny turned into {[a.value for a in again]} for the same input")
    if _sequence(make, _ingress, _ingress_intents()) != first_run:
        found.append("two fresh instances driven through the same inputs gave different verdicts")
    return found


def _request(offered: list[HitlResolveChoice], *, rid: int = 4) -> EmitHitlRequest:
    return EmitHitlRequest(
        request_id=rid,
        question_type=HitlQuestionType.CONFIRM,
        question="Approve tool call?",
        choices=[a.value.title() for a in offered],
        pending_schedule="TOOL_CALL",
        offered_actions=offered,
        context_data={"op": "TOOL_CALL", "tool": "t", "arguments": {"x": [1]}},
    )


def _requests() -> list[EmitHitlRequest]:
    return [
        _request([_C.ALLOW, _C.BLOCK, _C.SKIP]),
        _request([_C.ALLOW, _C.BLOCK, _C.TERMINATE], rid=9),
        _request([_C.BLOCK]),
        _request([_C.SKIP, _C.BLOCK]),
        _request([_C.SCHEDULE, _C.SKIP]),
    ]


_APPROVING = {_C.ALLOW, _C.SCHEDULE}
_DENYING = {_C.BLOCK, _C.SKIP, _C.TERMINATE}
# What each registered responder promises: the choice family it takes when one is offered.
_DECLARED: dict[str, Callable[[HitlResolveChoice], bool]] = {
    "auto-approve": lambda c: c in _APPROVING,
    "auto_approve": lambda c: c in _APPROVING,
    "auto-deny": lambda c: c in _DENYING,
    "auto_deny": lambda c: c in _DENYING,
}


def responder_violations(make: Callable[[], object], family: Callable[[HitlResolveChoice], bool]) -> list[str]:
    found: list[str] = []
    one = make()
    for request in _requests():
        label = f"request {request.request_id}/{len(request.offered_actions)} offered"
        before = copy.deepcopy(request)
        answer = one.resolve(request)
        if not isinstance(answer, HitlResolve):
            found.append(f"{label}: not a HitlResolve")
            continue
        if request != before:
            found.append(f"{label}: the responder wrote to the request")
        if answer.request_id != request.request_id:
            found.append(f"{label}: answered request {answer.request_id}")
        if answer.resolution not in request.offered_actions:
            found.append(f"{label}: chose {answer.resolution.value}, which was not offered")
        if answer != make().resolve(copy.deepcopy(request)) or answer != one.resolve(copy.deepcopy(request)):
            found.append(f"{label}: the answer to the same request changed")
        if any(family(c) for c in request.offered_actions) and not family(answer.resolution):
            found.append(f"{label}: left its declared family ({answer.resolution.value})")
    return found


# --- the registry is held to them ----------------------------------------------------------------------


def test_the_registry_holds_governance_plugins_and_responders() -> None:
    # Guards the enumeration itself: an empty registry would make every test below vacuous.
    assert len(EGRESS_CASES) >= 8 and len(INGRESS_CASES) >= 4 and len(RESPONDERS) >= 2
    assert DENY_CAPABLE <= {cls.__name__ for cls in GOVERNANCE.values()}


@pytest.mark.parametrize(("name", "variant"), EGRESS_CASES, ids=[f"{n}-{i}" for n, i in EGRESS_CASES])
def test_a_registered_egress_plugin_keeps_the_contract(name: str, variant: int) -> None:
    # Kills: a bad verdict shape, a clock or random choice, a write to the intent or config, a deny that flips.
    kwargs = _variants(name)[variant]
    assert egress_violations(lambda: GOVERNANCE[name](**kwargs)) == []


@pytest.mark.parametrize(("name", "variant"), INGRESS_CASES, ids=[f"{n}-{i}" for n, i in INGRESS_CASES])
def test_a_registered_ingress_plugin_keeps_the_contract(name: str, variant: int) -> None:
    kwargs = _variants(name)[variant]
    assert ingress_violations(lambda: GOVERNANCE[name](**kwargs)) == []


def test_the_corpus_makes_the_deny_capable_plugins_deny() -> None:
    # Kills: a corpus or configuration drift under which "no ALLOW after a deny" passes because nothing is denied.
    denied: set[str] = set()
    for name, variant in EGRESS_CASES:
        plugin = GOVERNANCE[name](**_variants(name)[variant])
        if any(_egress(plugin, i)[0] in _DENY for i in _egress_intents()):
            denied.add(GOVERNANCE[name].__name__)
    for name, variant in INGRESS_CASES:
        plugin = GOVERNANCE[name](**_variants(name)[variant])
        if any(_ingress(plugin, i).action in _BLOCKING for i in _ingress_intents()):
            denied.add(GOVERNANCE[name].__name__)
    assert DENY_CAPABLE <= denied, f"never denied in the corpus: {sorted(DENY_CAPABLE - denied)}"


def test_a_plugin_that_holds_has_its_hold_read_as_allow_once_approved_and_a_deny_still_wins() -> None:
    # Kills: an approval that lifts a deny, or a HITL hold that survives its approval, through the real chain.
    from mas.library.standard.plugins.governance.budget import BudgetPlugin

    asking = GOVERNANCE["sample"](hitl_on_tool=True)
    intent = EgressIntentView(op="TOOL_CALL", destructive=False, correlation_id=2, tool_name="read_file")
    asked = GovernancePluginChain([asking]).evaluate_egress(intent, config=_config())
    assert asked[0] == GovDecision.HITL
    approved = EgressIntentView(**{**intent.__dict__, "hitl_approved": True})
    assert GovernancePluginChain([asking]).evaluate_egress(approved, config=_config())[0] == GovDecision.ALLOW
    deny = BudgetPlugin(max_tool_calls=0)
    for plugins in ([asking, deny], [deny, asking]):
        assert GovernancePluginChain(plugins).evaluate_egress(approved, config=_config())[0] == GovDecision.TERMINATE


class _Verdict:
    def __init__(self, decision: GovDecision) -> None:
        self.decision = decision

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        return self.decision, "fixed", "fixed"


@pytest.mark.parametrize("deny", sorted(_DENY, key=lambda d: d.value), ids=lambda d: d.value)
@pytest.mark.parametrize("other", list(GovDecision), ids=lambda d: d.value)
def test_the_chain_keeps_a_deny_a_deny_whatever_the_other_plugins_say(deny: GovDecision, other: GovDecision) -> None:
    # Kills: a chain where another plugin's verdict outranks a deny, in either order (a chain property).
    for intent in _egress_intents():
        for plugins in ([_Verdict(other), _Verdict(deny)], [_Verdict(deny), _Verdict(other)]):
            decision = GovernancePluginChain(plugins).evaluate_egress(intent, config=_config())[0]
            assert decision in _DENY, (intent, decision)


def test_every_registered_responder_is_declared() -> None:
    # Kills: adding a responder to the registry without stating what it promises.
    assert set(RESPONDERS) <= set(_DECLARED)


@pytest.mark.parametrize("name", list(RESPONDERS))
def test_a_registered_responder_keeps_the_contract(name: str) -> None:
    # Kills: an answer for another request, an unoffered choice, a write to the request, a family change.
    assert responder_violations(lambda: RESPONDERS[name](), _DECLARED[name]) == []


# --- negative control: the checks must catch a bad plugin ---------------------------------------------------


class _MutatesIntent:
    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        if intent.tool_arguments is not None:
            intent.tool_arguments["seen"] = 1
        return GovDecision.ALLOW, "bad", "ok"


class _Nondeterministic:
    n = 0

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        type(self).n += 1
        return GovDecision.ALLOW, "bad", f"call {type(self).n}"


class _FlipsADeny:
    def __init__(self) -> None:
        self.asked = 0

    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        self.asked += 1
        return (GovDecision.BLOCK if self.asked == 1 else GovDecision.ALLOW), "bad", "flip"


class _BadShape:
    def evaluate_egress(self, intent, *, config):  # noqa: ARG002
        return "ALLOW"


class _IngressFlip:
    def __init__(self) -> None:
        self.asked = 0

    def evaluate_ingress(self, intent, *, config):  # noqa: ARG002
        self.asked += 1
        return IngressGovDecision(action=GovernanceAction.BLOCK if self.asked == 1 else GovernanceAction.ALLOW)


class _WrongChoice:
    def resolve(self, request):
        return HitlResolve(request_id=request.request_id + 1, resolution=_C.TERMINATE)


@pytest.mark.parametrize("bad", [_MutatesIntent, _Nondeterministic, _FlipsADeny, _BadShape])
def test_the_egress_checks_catch_a_bad_plugin(bad: type) -> None:
    # Kills: a check that has gone vacuous (it would pass a plugin built to violate it).
    assert egress_violations(bad) != []


def test_the_ingress_checks_catch_a_bad_plugin() -> None:
    assert ingress_violations(_IngressFlip) != []


def test_the_responder_checks_catch_a_bad_responder() -> None:
    assert responder_violations(_WrongChoice, _DECLARED["auto-deny"]) != []
