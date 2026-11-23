#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The production posture refuses, the default posture does not; waivers are recorded."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from mas.ctl.cli.main import app
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.hardening import (
    Exposure,
    HardeningError,
    HardeningInput,
    apply_posture,
    enforce,
    evaluate,
    posture_of,
)
from mas.ctl.hardening.posture import with_report
from mas.ctl.session.bootstrap import InstantiationOptions, _enforce_hardening, instantiate_runtime
from mas.ctl.session.exposure import expose_agent

PROD = {"hardening": {"profile": "production"}}
OPEN_LISTENER = {"protocol": "a2a", "usage": "listen", "url": "http://0.0.0.0:8123"}
ADMITTED_LISTENER = {**OPEN_LISTENER, "a2a": {"admission": {"callers": {"alice": {"secret": "alice"}}}}}
AGENT = {
    "apiVersion": "mas/v1",
    "kind": "Agent",
    "metadata": {"name": "solo"},
    "spec": {"description": "t", "models": [{"model": "m"}], "governance": ["retry_on_error"]},
}


def _options(flavour: dict | None, applications: dict | None = None) -> InstantiationOptions:
    return InstantiationOptions(
        agent_manifest=json.loads(json.dumps(AGENT)),
        manifest_dir=Path("."),
        resolved_infra=ResolvedInfra(applications=applications or {}),
        validate_manifests=False,
        flavour_spec=flavour,
    )


def test_selection_lives_in_the_flavour_and_the_default_is_no_posture() -> None:
    # kills: production as the default, or a missing block read as production
    assert posture_of(None) == ("default", ())
    assert posture_of({}) == ("default", ())
    assert posture_of({"hardening": {}}) == ("default", ())
    assert posture_of(PROD)[0] == "production"


@pytest.mark.parametrize(
    "block",
    [
        {"profile": "strict"},
        {"profile": "production", "extra": 1},
        {"waive": [{"check": "secret-store-env"}]},
        {"waive": [{"check": "secret-store-env", "reason": "  "}]},
        {"waive": [{"check": "no-such-check", "reason": "x"}]},
        {"waive": [{"check": "secret-store-env", "reason": "x", "until": "never"}]},
        {"waive": "secret-store-env"},
        "production",
    ],
)
def test_a_malformed_block_is_an_error_not_a_silent_default(block) -> None:
    # kills: a typo'd profile, a waiver with no reason or for no check silently doing nothing
    with pytest.raises(HardeningError):
        posture_of({"hardening": block})


def test_waiver_is_recorded_with_its_reason_and_only_covers_what_it_names() -> None:
    # kills: a waiver that hides the failure from the report, covers another check, or loses its reason
    inp = HardeningInput(
        agents={"a": {}},
        exposures=(Exposure("x", "a2a", "http://0.0.0.0:1", "0.0.0.0", "none"),),
    )
    _, waivers = posture_of(
        {
            "hardening": {
                "profile": "production",
                "waive": [{"check": "listener-without-identity", "reason": "behind the mesh"}],
            }
        }
    )
    report = evaluate(inp, "production", waivers)
    by = {r.check: r for r in report.results}
    assert by["listener-without-identity"].outcome == "waived"
    assert by["listener-without-identity"].reason == "behind the mesh"
    assert by["listener-without-identity"].problems
    assert by["ingress-errors-unclassified"].outcome == "failed"  # not covered
    assert by["egress-host-allowlist"].outcome == "deferred"
    assert not report.ok
    dumped = report.as_dict()
    assert dumped["waived"] == 1 and any(r.get("waiver_reason") == "behind the mesh" for r in dumped["results"])
    enforce(
        evaluate(
            HardeningInput(agents={"a": {"governance": ["retry_on_error"]}}, exposures=inp.exposures),
            "production",
            waivers,
        )
    )


def test_production_refuses_and_says_what_to_fix_default_only_logs(caplog) -> None:
    # kills: production that only warns, a refusal without the fix, a default posture that raises
    report = evaluate(HardeningInput(agents={"a": {}}), "production")
    with pytest.raises(HardeningError) as raised:
        enforce(report)
    assert "ingress-errors-unclassified" in str(raised.value) and "fix:" in str(raised.value)
    soft = evaluate(HardeningInput(agents={"a": {}}), "default")
    with caplog.at_level(logging.WARNING):
        enforce(soft)
    assert "ingress-errors-unclassified" in caplog.text


def test_default_posture_does_no_work_at_all() -> None:
    # kills: the checks running (cost, stderr noise, a possible refusal) without the Flavour selecting production
    def explode() -> HardeningInput:
        raise AssertionError("the input must not even be built")

    assert apply_posture(None, explode) is None
    assert apply_posture({"hardening": {"profile": "default"}}, explode) is None
    assert apply_posture({"require_usage": True}, explode) is None


def test_session_start_refuses_a_listener_with_no_identity_under_production() -> None:
    # kills: the session starting with an open non-loopback listener under production
    options = _options(PROD, {"solo": OPEN_LISTENER})
    with pytest.raises(HardeningError, match="listener-without-identity"):
        _enforce_hardening(options, dict(AGENT["spec"]))
    with pytest.raises(HardeningError, match="listener-without-identity"):
        instantiate_runtime(options)


def test_session_start_accepts_an_admitted_listener_and_a_waived_one() -> None:
    spec = dict(AGENT["spec"])
    assert _enforce_hardening(_options(PROD, {"solo": ADMITTED_LISTENER}), spec) is not None
    waived = {
        "hardening": {"profile": "production", "waive": [{"check": "listener-without-identity", "reason": "mesh"}]}
    }
    _enforce_hardening(_options(waived, {"solo": OPEN_LISTENER}), spec)


def test_without_the_production_flavour_the_session_spec_is_returned_untouched() -> None:
    # kills: the default path rewriting the spec (the byte-identical guarantee)
    spec = dict(AGENT["spec"])
    for flavour in (None, {}, {"require_usage": False}, {"hardening": {"profile": "default"}}):
        assert _enforce_hardening(_options(flavour, {"solo": OPEN_LISTENER}), spec) is spec


def test_the_report_reaches_a_declared_plugin_and_only_a_declared_one() -> None:
    # kills: the report not handed to the plugin; the spec rewritten when the plugin is not declared
    report = evaluate(HardeningInput(agents={"a": {}}), "production")
    plain = {"governance": ["budget"]}
    assert with_report(plain, report) is plain
    spec = {"governance": ["budget", "gov_hardening_profile"]}
    out = with_report(spec, report)
    params = out["governance"][1]["gov_hardening_profile"]
    assert params["profile"] == "production" and {r["check"] for r in params["report"]} >= {"secret-store-env"}
    spec2 = {"governance": [{"hardening_profile": {"allowed_hosts": ["a.example.com"]}}]}
    params2 = with_report(spec2, report)["governance"][0]["hardening_profile"]
    assert params2["allowed_hosts"] == ["a.example.com"] and params2["report"]
    assert spec2["governance"][0]["hardening_profile"].get("report") is None  # input not mutated


def test_expose_agent_refuses_before_it_binds_under_production() -> None:
    # kills: a listener started under production with no admission policy, and a refusal that binds first
    manifest = {"metadata": {"name": "solo"}, "spec": {}}
    with pytest.raises(HardeningError, match="listener-without-identity"):
        expose_agent(object(), manifest, OPEN_LISTENER, flavour_spec=PROD)


def test_only_the_bundled_prod_flavour_selects_production() -> None:
    # kills: prod no longer selecting production, or local (the default) turning the posture on
    from mas.ctl.session.flavour import resolve_session_flavour

    assert posture_of(resolve_session_flavour("prod")) == ("production", ())
    assert posture_of(resolve_session_flavour("local"))[0] == "default"
    assert posture_of(resolve_session_flavour(None))[0] == "default"


def test_prod_flavour_refuses_an_exposure_without_an_inbound_verifier_and_accepts_one_with() -> None:
    # kills: prod admitting an unauthenticated listener, or refusing one whose callers are configured
    from mas.ctl.session.flavour import resolve_session_flavour

    prod = resolve_session_flavour("prod")
    with pytest.raises(HardeningError, match="listener-without-identity"):
        _enforce_hardening(_options(prod, {"solo": OPEN_LISTENER}), dict(AGENT["spec"]))
    with pytest.raises(HardeningError, match="listener-without-identity"):
        expose_agent(object(), {"metadata": {"name": "solo"}, "spec": {}}, OPEN_LISTENER, flavour_spec=prod)
    _enforce_hardening(_options(prod, {"solo": ADMITTED_LISTENER}), dict(AGENT["spec"]))


def test_plain_prod_flavour_runs_an_agent_with_no_governance_of_its_own() -> None:
    # kills: prod refusing every agent on ingress-errors-unclassified, or the Control not reaching the agent
    from mas.ctl.session.flavour import resolve_session_flavour
    from mas.ctl.session.manifest_config import spec_with_control_lists

    prod = resolve_session_flavour("prod")
    bare = {"description": "t", "models": [{"model": "m"}]}
    with pytest.raises(HardeningError, match="ingress-errors-unclassified"):
        _enforce_hardening(_options(prod), dict(bare))
    effective = spec_with_control_lists(dict(bare), prod, agent_id="solo", entry_id="solo", delegate_ids=[])
    assert "retry_on_error" in str(effective["governance"])
    _enforce_hardening(_options(prod), effective)


def test_default_flavour_still_accepts_the_open_listener_and_leaves_the_spec_alone() -> None:
    # kills: the default Flavour starting to enforce anything
    from mas.ctl.session.flavour import resolve_session_flavour

    spec = dict(AGENT["spec"])
    local = resolve_session_flavour("local")
    assert _enforce_hardening(_options(local, {"solo": OPEN_LISTENER}), spec) is spec


def test_a_waiver_override_is_reported_in_the_admission_log(caplog) -> None:
    # kills: an explicit override that passes silently
    waived = {
        "hardening": {"profile": "production", "waive": [{"check": "listener-without-identity", "reason": "mesh"}]}
    }
    with caplog.at_level(logging.WARNING):
        _enforce_hardening(_options(waived, {"solo": OPEN_LISTENER}), dict(AGENT["spec"]))
    assert "waived listener-without-identity" in caplog.text


def _write(tmp_path: Path, name: str, doc: dict) -> str:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_cli_check_is_advisory_by_default_and_fails_closed_under_production(tmp_path) -> None:
    # kills: an exit code that blocks the default posture, or lets production through
    agent = _write(tmp_path, "agent.yaml", {**AGENT, "spec": {"description": "t", "models": [{"model": "m"}]}})
    runner = CliRunner()
    advisory = runner.invoke(app, ["hardening", "check", agent])
    assert advisory.exit_code == 0, advisory.output
    assert "posture: default" in advisory.output and "FAIL ingress-errors-unclassified" in advisory.output
    strict = runner.invoke(app, ["hardening", "check", agent, "--profile", "production", "--json"])
    assert strict.exit_code == 1, strict.output
    report = json.loads(strict.output)
    assert report["posture"] == "production" and not report["ok"] and report["failed"] >= 1
    fixed = _write(tmp_path, "fixed.yaml", AGENT)
    assert runner.invoke(app, ["hardening", "check", fixed, "--profile", "production"]).exit_code == 0


def test_cli_list_names_every_check_and_validate_profile_is_opt_in(tmp_path) -> None:
    from mas.ctl.hardening import CHECKS

    runner = CliRunner()
    listed = json.loads(runner.invoke(app, ["hardening", "list", "--json"]).output)
    assert [row["id"] for row in listed] == [c.id for c in CHECKS]
    infra = _write(
        tmp_path,
        "app.yaml",
        {
            "apiVersion": "infra/v1",
            "kind": "Application",
            "metadata": {"name": "x"},
            "spec": {"endpoints": {"a": OPEN_LISTENER}},
        },
    )
    plain = runner.invoke(app, ["validate", infra])
    assert "hardening" not in plain.output
    advisory = runner.invoke(app, ["validate", infra, "--profile", "default"])
    assert advisory.exit_code == plain.exit_code and "[warning] listener-without-identity" in advisory.output
    strict = runner.invoke(app, ["validate", infra, "--profile", "production"])
    assert strict.exit_code == 1 and "[error] listener-without-identity" in strict.output
