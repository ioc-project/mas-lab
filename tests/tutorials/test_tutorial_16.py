#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 16 — Identity: the commands on the manifests, and a gated tool call through a real turn.

Everything runs offline: the issuer key is a throwaway entry of a file secret store, the signed badge is a fake with
the shape of a real credential, and the model is the simulated engine.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import pytest
from tutorial_support import T16, run_cli

AGENT = T16 / "agents" / "desk" / "agent.yaml"
KEY = "a-throwaway-issuer-key-0123456789abcdef"


@pytest.fixture
def secrets(tmp_path: Path) -> Path:
    from mas.ctl.secrets import init_secrets_dir, write_file_secret

    directory = (tmp_path / "secrets").resolve()
    init_secrets_dir(directory)
    write_file_secret(directory, "desk-issuer-key", KEY)
    return directory


def ctl(tmp_path: Path, secrets: Path, *args: str, store: Path | None = None):
    """``mas-ctl`` with its own secret store and none of the host's user config."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".data")}
    return run_cli(["mas-ctl", "--secrets-dir", str(store or secrets), *args], cwd=T16, extra_env=env)


def session(infra: str = "tools-identity.yaml", *, identity: str = "identity.yaml") -> list[str]:
    return [
        "mas.yaml",
        "--flavour",
        "flavours/gated.yaml",
        "--infra-ref",
        f"infra/{infra}",
        "--infra-ref",
        f"infra/{identity}",
    ]


def test_the_manifests_validate_and_the_agent_never_names_an_identity() -> None:
    for name in ("mas.yaml", "agents/desk/agent.yaml"):
        assert run_cli(["mas-ctl", "validate", name], cwd=T16).returncode == 0
    refused = run_cli(["mas-ctl", "validate", "broken/agent-with-identity.yaml"], cwd=T16)
    assert refused.returncode != 0 and "identity concern" in refused.stdout + refused.stderr


def test_list_shows_the_record_its_scheme_and_who_uses_it(tmp_path: Path, secrets: Path) -> None:
    done = ctl(tmp_path, secrets, "identity", "list", *session(), "--json")
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    (row,) = payload["identities"]
    assert (row["id"], row["scheme"], row["secret_params"]) == ("desk-badge", "signed_badge", ["key"])
    assert payload["agents"] == {"desk": ["desk-badge"]}
    assert KEY not in done.stdout and "desk-issuer-key" not in done.stdout  # not the value, not even the reference


def test_check_passes_with_the_secret_set_and_the_gate_in_place(tmp_path: Path, secrets: Path) -> None:
    done = ctl(tmp_path, secrets, "identity", "check", *session(), "--require-gate")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("OK")


def test_check_names_the_secret_that_is_not_set_and_exits_1(tmp_path: Path, secrets: Path) -> None:
    empty = tmp_path / "empty-store"
    from mas.ctl.secrets import init_secrets_dir

    init_secrets_dir(empty.resolve())
    done = ctl(tmp_path, secrets, "identity", "check", *session(), store=empty)
    assert done.returncode == 1
    assert "desk-issuer-key" in done.stdout and "mas-ctl secrets set desk-issuer-key" in done.stdout
    assert KEY not in done.stdout + done.stderr


def test_check_asks_for_a_gate_when_the_flavour_has_none(tmp_path: Path, secrets: Path) -> None:
    args = [a for a in session() if a not in ("--flavour", "flavours/gated.yaml")]
    warn = ctl(tmp_path, secrets, "identity", "check", *args)
    assert warn.returncode == 0 and "no identity-gate" in warn.stdout
    strict = ctl(tmp_path, secrets, "identity", "check", *args, "--require-gate")
    assert strict.returncode == 1 and "no identity-gate" in strict.stdout


def test_describe_prints_the_public_view_and_no_credential(tmp_path: Path, secrets: Path) -> None:
    done = ctl(tmp_path, secrets, "identity", "describe", "desk-badge", *session(), "--json")
    assert done.returncode == 0, done.stderr
    (item,) = json.loads(done.stdout)["identities"]
    assert item["view"]["facts"]["group"] == "infraadmin"
    assert KEY not in done.stdout


def test_a_literal_key_is_refused_and_the_message_names_the_field_not_the_value(tmp_path: Path, secrets: Path) -> None:
    done = ctl(tmp_path, secrets, "identity", "check", *session(identity="../broken/literal-secret.yaml"))
    assert done.returncode == 1
    assert "$.key: secret_ref" in done.stdout
    assert "a-literal-secret-value" not in done.stdout + done.stderr


def test_a_record_no_row_declares_is_a_bind_error_that_names_it(tmp_path: Path, secrets: Path) -> None:
    args = session()
    args[args.index("infra/tools-identity.yaml")] = "broken/dangling.yaml"
    done = ctl(tmp_path, secrets, "identity", "list", *args)
    assert done.returncode != 0
    assert "no-such-badge" in done.stdout + done.stderr and "desk-badge" in done.stdout + done.stderr


def test_without_an_identity_row_the_tool_server_is_untouched(tmp_path: Path, secrets: Path) -> None:
    plain = ["mas.yaml", "--infra-ref", "infra/tools.yaml"]
    done = ctl(tmp_path, secrets, "identity", "list", *plain, "--json")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"agents": {}, "identities": []}


# --- a gated tool call through a real turn --------------------------------------------------------


FARES = ("get_fares", {"route_id": "CV"})


def _turn(
    group: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent: Path = AGENT,
    control: str = "fares-gate.yaml",
    call: tuple[str, dict[str, Any]] = FARES,
) -> tuple[Any, str, list[dict[str, Any]]]:
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
    from mas.ctl.ui.stdout import StdoutConversationDisplay
    from mas.library.standard.lib.identity import holders, observe
    from mas.library.standard.lib.identity.caller import Caller
    from mas.library.standard.plugins.identity.gate import IdentityGate
    from mas.library.standard.testing.identity_fakes import bound_secrets
    from mas.library.standard.testing.identity_signed_badge import SignedBadge
    from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream
    from mas.runtime.engine.simulated import SimulatedEngine
    from overlay_support import load_agent_and_flavour

    gates: list[IdentityGate] = []
    build = IdentityGate.__init__

    @functools.wraps(build)
    def remember(self: IdentityGate, *args: Any, **kwargs: Any) -> None:
        build(self, *args, **kwargs)
        gates.append(self)

    monkeypatch.setattr(IdentityGate, "__init__", remember)
    manifest, flavour = load_agent_and_flavour(agent, T16 / "flavours" / "controls" / control)
    manifest["spec"]["tools"] = [{"ref": "tools/get_fares.tool.yaml"}]  # the agent's ref is relative to its own folder
    engine = SimulatedEngine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: call if cid == 1 else ("", {}),
        stop_text="The fare is 24.",
    )
    events: list[dict[str, Any]] = []
    stream = EventStream()
    stream.subscribe(events.append)
    token = set_event_stream(stream)
    from mas.runtime.secrets import SecretRef

    with bound_secrets({"desk-issuer-key": KEY}):
        wallet = SignedBadge.from_spec(
            {"key": SecretRef(name="desk-issuer-key"), "subject": "desk", "claims": {"group": group}}
        )
        caller = Caller("desk-badge", wallet)  # what the identity layer builds from the record, held by the agent
        holders.register("desk", "desk-badge", caller)
        try:
            instance, _store = instantiate_runtime(
                InstantiationOptions(
                    agent_manifest=manifest,
                    manifest_dir=T16,
                    validate_manifests=False,
                    engine=engine,
                    flavour_spec=flavour,
                )
            )
            assert bool(gates) == (control == "fares-gate.yaml")  # only that Control puts an identity-gate in
            for gate in gates:  # the first tool call of a session decides on a view that is already there
                gate.prefetch("desk")
                gate.settle()
            controller = SessionController(
                instance=instance,
                display=StdoutConversationDisplay(show_labels=False, verbose=0),
                config=ConversationConfig(single_turn=True),
            )
            result = controller.run_turn("What is the fare for route CV?")
            close_observability(controller)
        finally:
            holders.reset()
            reset_event_stream(token)
    for event in events:
        observe.validate_event(event)
    return instance, result.text, [e for e in events if e["kind"] == "identity.gate"]


def test_a_call_the_identity_covers_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, _text, decisions = _turn("infraadmin", monkeypatch)
    tool = [d for d in decisions if d["operation"] == "tools/call:get_fares"]
    assert tool and tool[0]["outcome"] == "ok" and tool[0]["code"] == "covered"
    assert "[simulated tool result" in "\n".join(str(getattr(e, "text", "")) for e in instance.driver.kernel.run.events)


def test_a_call_the_identity_does_not_cover_is_refused_before_it_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, _text, decisions = _turn("dev", monkeypatch)
    tool = [d for d in decisions if d["operation"] == "tools/call:get_fares"]
    assert tool and tool[0]["outcome"] == "denied" and tool[0]["code"] == "scope_uncovered:group"
    assert "[simulated tool result" not in "\n".join(
        str(getattr(e, "text", "")) for e in instance.driver.kernel.run.events
    )


# --- Step 5: the agent manages its own identities, and the guard shuts the calls that change them -----------------


MANAGER = T16 / "agents" / "desk" / "manager.yaml"


def test_a_read_of_its_own_identities_passes_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, _text, decisions = _turn(
        "infraadmin", monkeypatch, agent=MANAGER, control="wallet-guard.yaml", call=("identity_list", {})
    )
    text = "\n".join(str(getattr(e, "text", "")) for e in instance.driver.kernel.run.events)
    assert "[simulated tool result" in text  # the call went out (the simulated engine stands in for the tool)
    assert decisions == []  # a read tool is not covered by the guard: no decision, no event


def test_a_delegation_the_model_asks_for_is_blocked_by_the_guard_before_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _text, decisions = _turn(
        "infraadmin",
        monkeypatch,
        agent=MANAGER,
        control="wallet-guard.yaml",
        call=("identity_delegate", {"identity": "desk-badge", "scopes": ["read"]}),
    )
    (decision,) = decisions
    assert decision["operation"] == "tools/call:identity_delegate"
    assert (decision["outcome"], decision["code"]) == ("denied", "guard_denied")
    assert "badge-d" not in "\n".join(str(getattr(e, "text", "")) for e in instance.driver.kernel.run.events)
