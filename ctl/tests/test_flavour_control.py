#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Control kind, Flavour attach, leftover Agent observability still applied."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.cli.obs_flags import resolve_observability_config
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.infra.bind import BindError, bind_session
from mas.ctl.session.flavour import FlavourError, resolve_flavour, resolve_session_flavour
from mas.ctl.session.flavour_control import (
    ControlError,
    apply_controls,
    apply_flavour_controls,
    control_body_for_hash,
    control_lists_for_agent,
    load_control,
    resolve_controls,
)
from mas.ctl.session.flavour_control.plugins import union_plugin_set
from mas.ctl.session.manifest_config import (
    kernel_config_from_manifest,
    observability_config_from_manifest,
    spec_with_control_lists,
)
from mas.ctl.validate import validate_data

_LEFTOVER = ("agent_comm", "tools", "telemetry", "observability")
_SINK = ResolvedInfra(applications={"native": {"protocol": "native", "path": "traces/events.jsonl"}})
_GROUP = {"protocol": "local-group", "identity": "acme/lab/desk", "usage": ["connect", "listen"]}


def _split_control_spec(spec: dict) -> dict:
    spec = dict(spec)
    plugins = spec.pop("plugins", None)
    if plugins is None:
        return spec
    items = list(plugins) if isinstance(plugins, list) else [plugins]
    obs: list = []
    gov: list = []
    for item in items:
        name = item if isinstance(item, str) else (next(iter(item)) if isinstance(item, dict) and item else "")
        short = str(name).rsplit(".", 1)[-1]
        if str(name).startswith("mas.observability.") or short in {"native", "otel"}:
            obs.append(item)
        else:
            gov.append(item)
    if obs:
        spec["observability"] = obs
    if gov:
        spec["governance"] = gov
    return spec


def _control_doc(*, name: str = "baseline", spec: dict | None = None) -> dict:
    return {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": name},
        "spec": spec or {"governance": ["sample_governance"]},
    }


def _write_control(tmp_path: Path, *, name: str = "baseline", spec: dict | None = None) -> Path:
    import yaml

    path = tmp_path / f"{name}.yaml"
    body = _split_control_spec(spec) if spec is not None else None
    path.write_text(yaml.safe_dump(_control_doc(name=name, spec=body)), encoding="utf-8")
    return path


def _lists(controls, *, agent_id: str, entry_id: str = "", delegate_ids: tuple[str, ...] | list[str] = ()):
    named = control_lists_for_agent(controls, agent_id=agent_id, entry_id=entry_id, delegate_ids=delegate_ids)
    return {"observability": [], "governance": [], **named}


def test_control_kind_validates() -> None:
    result = validate_data(_control_doc(), kind="control", strict=True, resolve_refs=False)
    assert result.ok


def test_control_kind_rejects_unknown_spec_key() -> None:
    doc = _control_doc(spec={"governance": ["sample_governance"], "llm": {}})
    result = validate_data(doc, kind="control", strict=True, resolve_refs=False)
    assert not result.ok


def test_control_observability_and_governance_are_the_lists() -> None:
    for key in ("governance", "observability"):
        doc = _control_doc(spec={key: ["sample_governance"]})
        result = validate_data(doc, kind="control", strict=True, resolve_refs=False)
        assert result.ok, key
    for key in ("stack_layer", "pipeline", "plugins", "impair"):
        doc = _control_doc(spec={key: ["sample_governance"]})
        result = validate_data(doc, kind="control", strict=True, resolve_refs=False)
        assert not result.ok, key
    leftover = _control_doc(spec={"governance": ["sample_governance"], "control": {"budget": {"max_llm_calls": 9}}})
    result = validate_data(leftover, kind="control", strict=True, resolve_refs=False)
    assert not result.ok


def test_control_refuses_the_same_plugin_id_twice() -> None:
    doc = _control_doc(
        spec={
            "governance": [
                {"budget": {"max_llm_calls": 1}},
                {"budget": {"max_llm_calls": 9}},
            ]
        }
    )
    result = validate_data(doc, kind="control", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("already in the set" in issue.message for issue in result.issues)


def test_a_plugin_name_that_matches_two_modules_is_refused() -> None:
    from mas.ctl.session.flavour_control import plugin_entry

    with pytest.raises(ControlError, match="more than one module"):
        plugin_entry("native")


def test_a_plugin_config_that_is_not_a_map_is_refused() -> None:
    from mas.ctl.session.flavour_control import plugin_entry

    with pytest.raises(ControlError, match="plugin id or a one-key config object"):
        plugin_entry({"budget": 5})


def test_load_control_reads_a_file(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance", "budget"]})
    loaded = load_control(path)
    assert loaded["kind"] == "Control"
    assert loaded["spec"]["governance"] == ["sample_governance", "budget"]


def test_load_control_rejects_a_flavour_file(tmp_path: Path) -> None:
    path = tmp_path / "not-control.yaml"
    path.write_text("apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: x}\nspec: {}\n")
    with pytest.raises(ControlError, match="kind: Control"):
        load_control(path)


def test_flavour_control_list_validates() -> None:
    result = validate_data(
        {
            "apiVersion": "flavour/v1",
            "kind": "Flavour",
            "metadata": {"name": "desk"},
            "spec": {"control": [{"ref": "baseline.yaml", "agents": "$all"}]},
        },
        kind="flavour",
        strict=True,
        resolve_refs=False,
    )
    assert result.ok


def test_flavour_control_row_accepts_one_agent_id() -> None:
    result = validate_data(
        {
            "apiVersion": "flavour/v1",
            "kind": "Flavour",
            "metadata": {"name": "desk"},
            "spec": {"control": [{"ref": "hitl.yaml", "agents": "desk"}]},
        },
        kind="flavour",
        strict=True,
        resolve_refs=False,
    )
    assert result.ok


def test_flavour_control_attaches_to_matching_agent(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    flavour = {"control": [{"ref": path.name, "agents": "$all"}]}
    controls = resolve_controls(flavour, search_dirs=[tmp_path])
    applied, warnings = apply_controls(
        {"spec": {}},
        controls,
        agent_id="desk",
        entry_id="desk",
    )
    assert "governance" not in applied["spec"]
    assert "control" not in applied["spec"]
    assert warnings == []
    assert _lists(controls, agent_id="desk", entry_id="desk")["governance"] == ["sample_governance"]


def test_control_leftover_control_mapping_is_refused(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"], "control": {"retry": {}}})
    with pytest.raises(ControlError, match="spec.control is leftover"):
        resolve_controls({"control": [{"ref": path.name}]}, search_dirs=[tmp_path])


def test_load_control_refuses_leftover_impair(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"impair": ["delay"]})
    with pytest.raises(ControlError, match="spec.impair is leftover"):
        load_control(path)


def test_later_control_budget_wins(tmp_path: Path) -> None:
    first = _write_control(tmp_path, name="first", spec={"plugins": [{"budget": {"max_llm_calls": 9}}]})
    second = _write_control(tmp_path, name="second", spec={"plugins": [{"budget": {"max_llm_calls": 3}}]})
    controls = resolve_controls({"control": [{"ref": first.name}, {"ref": second.name}]}, search_dirs=[tmp_path])
    [item] = _lists(controls, agent_id="desk", entry_id="desk")["governance"]
    assert item["budget"]["max_llm_calls"] == 3


def test_agent_schema_does_not_name_library_plugins() -> None:
    result = validate_data(
        {
            "apiVersion": "mas/v1",
            "kind": "Agent",
            "metadata": {"name": "desk"},
            "spec": {
                "description": "desk",
                "budget_plugin": [{"budget": {"max_llm_calls": 9}}],
            },
        },
        kind="agent",
        strict=True,
        resolve_refs=False,
    )
    assert not result.ok


def test_control_schema_names_no_plugin() -> None:
    result = validate_data(
        _control_doc(spec={"governance": [{"budget": {"max_llm_calls": 9}}, {"any_name_at_all": {}}]}),
        kind="control",
        strict=True,
        resolve_refs=False,
    )
    assert result.ok, result.issues


def test_unknown_plugin_attribute_is_refused() -> None:
    from mas.ctl.session.flavour_control import plugin_entry

    with pytest.raises(ControlError, match="unknown field"):
        plugin_entry({"budget": {"bogus": 1}})


def test_load_control_refuses_unknown_plugin_attribute(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import load_control

    path = _write_control(tmp_path, spec={"plugins": [{"budget": {"bogus": 1}}]})
    with pytest.raises(ControlError, match="unknown field"):
        load_control(path)


def test_apply_controls_does_not_copy_lists_onto_the_agent(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["gov_no_undeclared_tool"]})
    controls = resolve_controls({"control": [{"ref": path.name}]}, search_dirs=[tmp_path])
    applied, warnings = apply_controls(
        {"spec": {"governance": ["sample_governance"]}},
        controls,
        agent_id="desk",
        entry_id="desk",
    )
    assert applied["spec"]["governance"] == ["sample_governance"]
    assert "retry" not in applied["spec"]
    assert warnings == []
    assert _lists(controls, agent_id="desk", entry_id="desk")["governance"] == ["gov_no_undeclared_tool"]


def test_control_delegates_selector_skips_the_entry(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    controls = resolve_controls(
        {"control": [{"ref": path.name, "agents": "$delegates"}]},
        search_dirs=[tmp_path],
    )
    skipped, _ = apply_controls({"spec": {}}, controls, agent_id="desk", entry_id="desk", delegate_ids=["bill"])
    attached, _ = apply_controls({"spec": {}}, controls, agent_id="bill", entry_id="desk", delegate_ids=["bill"])
    assert "governance" not in skipped["spec"]
    assert "governance" not in attached["spec"]
    assert _lists(controls, agent_id="desk", entry_id="desk", delegate_ids=["bill"])["governance"] == []
    assert _lists(controls, agent_id="bill", entry_id="desk", delegate_ids=["bill"])["governance"] == [
        "sample_governance"
    ]


def test_control_entry_selector_skips_delegates(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    controls = resolve_controls(
        {"control": [{"ref": path.name, "agents": "$entry"}]},
        search_dirs=[tmp_path],
    )
    skipped, _ = apply_controls({"spec": {}}, controls, agent_id="bill", entry_id="desk")
    attached, _ = apply_controls({"spec": {}}, controls, agent_id="desk", entry_id="desk")
    assert "governance" not in skipped["spec"]
    assert "governance" not in attached["spec"]
    assert _lists(controls, agent_id="bill", entry_id="desk")["governance"] == []
    assert _lists(controls, agent_id="desk", entry_id="desk")["governance"] == ["sample_governance"]


def test_events_shortcut_does_not_read_flavour_observability() -> None:
    implicit = observability_config_from_manifest(
        {"spec": {}},
        flavour_spec={"observability": ["otel"]},
    )
    assert implicit.plugins == ["native"]
    assert implicit.enabled is True

    flagged = resolve_observability_config(
        events=True,
        events_file=None,
        events_stdout=False,
        events_format=None,
        manifest={"spec": {}},
        flavour_spec={"observability": ["otel"]},
    )
    assert flagged.plugins == ["native"]
    assert flagged.enabled is True


def test_events_file_adds_native_when_leftover_agent_observability_applies() -> None:
    cfg = resolve_observability_config(
        events=None,
        events_file="traces/custom.jsonl",
        events_stdout=False,
        events_format=None,
        manifest={"spec": {"observability": ["otel"]}},
        flavour_spec={"observability": ["native"]},
    )
    assert cfg.plugins == ["otel", "native"]
    assert cfg.events_file == "traces/custom.jsonl"


def test_events_stdout_adds_native_when_leftover_agent_observability_applies(capsys) -> None:
    cfg = resolve_observability_config(
        events=None,
        events_file=None,
        events_stdout=True,
        events_format=None,
        manifest={"spec": {"observability": ["otel"]}},
    )
    assert cfg.plugins == ["otel", "native"]
    assert cfg.events_stdout is True
    assert "CLI --events replaces" not in capsys.readouterr().err


def test_bind_events_adds_its_plugin_when_leftover_agent_observability_applies() -> None:
    cfg = observability_config_from_manifest(
        {"spec": {"observability": ["otel"]}},
        flavour_spec={"bind": {"events": ["native"]}},
        resolved_infra=_SINK,
    )
    assert cfg.plugins == ["otel", "native"]


def test_cli_events_true_keeps_leftover_agent_plugins(capsys) -> None:
    cfg = resolve_observability_config(
        events=True,
        events_file=None,
        events_stdout=False,
        events_format=None,
        manifest={"spec": {"observability": ["otel"]}},
        flavour_spec={"bind": {"events": ["native"]}},
        resolved_infra=_SINK,
    )
    assert cfg.plugins == ["otel", "native"]
    assert "CLI --events replaces" not in capsys.readouterr().err


def test_events_format_is_final_and_ignores_the_bind_events_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["mas.observability.native"]})
    flavour["bind"] = {"events": ["native"]}
    cfg = resolve_observability_config(
        events=None,
        events_file=None,
        events_stdout=False,
        events_format="otel",
        manifest={"spec": {}},
        flavour_spec=flavour,
        resolved_infra=_SINK,
    )
    assert cfg.plugins == ["otel"]
    err = capsys.readouterr().err
    assert "CLI --events-format replaces" in err
    assert "unused" not in err


def test_bind_events_without_an_application_row_is_not_a_plugin_id() -> None:
    with pytest.raises(BindError, match="bind.events names 'native', which is no Application row"):
        observability_config_from_manifest(
            {"spec": {}},
            flavour_spec={"bind": {"events": ["native"]}},
            resolved_infra=ResolvedInfra(),
        )


def test_bind_events_row_needs_protocol() -> None:
    with pytest.raises(BindError, match="needs protocol"):
        observability_config_from_manifest(
            {"spec": {}},
            flavour_spec={"bind": {"events": ["sink"]}},
            resolved_infra=ResolvedInfra(applications={"sink": {"path": "traces/from-row.jsonl"}}),
        )


def test_bind_events_uses_the_application_row_protocol() -> None:
    cfg = observability_config_from_manifest(
        {"spec": {}},
        flavour_spec={"bind": {"events": ["sink"]}},
        resolved_infra=ResolvedInfra(applications={"sink": {"protocol": "native", "path": "traces/from-row.jsonl"}}),
    )
    assert cfg.plugins == ["native"]
    assert cfg.events_file == "traces/from-row.jsonl"


def test_the_default_session_flavour_is_local() -> None:
    assert resolve_session_flavour(None) == resolve_flavour("local")


def test_omitted_flavour_is_local() -> None:
    spec = resolve_session_flavour(None)
    assert spec == resolve_flavour("local")


def test_events_sink_flag_adds_a_row_to_bind_events() -> None:
    spec = resolve_session_flavour(None, event_sinks=("collector", "collector", "archive"))
    assert spec["bind"]["events"] == ["collector", "archive"]


def test_control_flag_attaches_a_control_the_way_a_flavour_row_does(tmp_path: Path) -> None:
    _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    spec = resolve_session_flavour(None, controls=("gov.yaml@$entry",), search_dirs=[tmp_path])
    assert control_body_for_hash(spec) == [{"agents": "$entry", "governance": ["sample_governance"]}]
    assert "control" not in spec


def test_control_flag_takes_an_absolute_path_from_the_command_line(tmp_path: Path) -> None:
    path = _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    spec = resolve_session_flavour(None, controls=(str(path),))
    assert control_body_for_hash(spec) == [{"agents": "$all", "governance": ["sample_governance"]}]


def test_control_flag_with_an_unknown_ref_is_a_flavour_error(tmp_path: Path) -> None:
    with pytest.raises(FlavourError, match="is not a file"):
        resolve_session_flavour(None, controls=("nowhere.yaml",), search_dirs=[tmp_path])


@pytest.mark.parametrize(
    ("text", "ref", "agents"),
    [
        ("gov.yaml", "gov.yaml", None),
        ("gov.yaml@$all", "gov.yaml", "$all"),
        (
            "pkg://mas.library.standard/controls/with-hardened.yaml@$entry",
            "pkg://mas.library.standard/controls/with-hardened.yaml",
            "$entry",
        ),
        ("gov.yaml@desk", "gov.yaml", "desk"),
        ("gov.yaml@desk,bill", "gov.yaml", ["desk", "bill"]),
        ("gov.yaml@$not-entry", "gov.yaml", "$not-entry"),
        ("gov.yaml@.mas.agent[*]", "gov.yaml", ".mas.agent[*]"),
        ("gov.yaml@.mas.agent[desk]", "gov.yaml", ".mas.agent[desk]"),
        ("gov.yaml@.mas.agent[id=desk]", "gov.yaml", ".mas.agent[id=desk]"),
        ("gov.yaml@.app.agent[desk]", "gov.yaml", ".app.agent[desk]"),
        ("gov.yaml@mas:agent[desk]", "gov.yaml", "mas:agent[desk]"),
        (
            "gov.yaml@.mas.agent[desk],.mas.agent[bill]",
            "gov.yaml",
            [".mas.agent[desk]", ".mas.agent[bill]"],
        ),
        ("lib:gov.yaml", "lib:gov.yaml", None),
        ("gov@v2.yaml", "gov@v2.yaml", None),
    ],
)
def test_control_option_syntax(text: str, ref: str, agents: object) -> None:
    from mas.ctl.session.flavour_control import parse_control_option

    assert parse_control_option(text) == (ref, agents)


def test_control_option_refuses_a_bad_selector() -> None:
    from mas.ctl.session.flavour_control import parse_control_option

    with pytest.raises(ControlError, match="selector"):
        parse_control_option("gov.yaml@$bogus,desk")


def test_a_star_selector_is_not_an_agent_id() -> None:
    from mas.ctl.session.flavour_control import agent_matches, parse_control_option

    with pytest.raises(ControlError, match=r"\$all|\.mas\.agent\[\*\]"):
        parse_control_option("gov.yaml@*")
    with pytest.raises(ControlError, match=r"\$all|\.mas\.agent\[\*\]"):
        agent_matches("*", agent_id="desk")


def test_control_option_refuses_a_path_that_is_not_an_agent_path() -> None:
    from mas.ctl.session.flavour_control import parse_control_option

    with pytest.raises(ControlError, match="agent path"):
        parse_control_option("gov.yaml@.mas.infra[x]")


def test_control_option_refuses_an_agent_index() -> None:
    from mas.ctl.session.flavour_control import parse_control_option

    with pytest.raises(ControlError, match="not an index"):
        parse_control_option("gov.yaml@.mas.agent[0]")


def test_control_option_refuses_a_field_after_the_agent() -> None:
    from mas.ctl.session.flavour_control import parse_control_option

    with pytest.raises(ControlError, match="the agent only"):
        parse_control_option("gov.yaml@.mas.agent[desk].spec")


def test_control_option_colon_without_an_agent_path_is_an_id() -> None:
    from mas.ctl.session.flavour_control import parse_control_option

    assert parse_control_option("gov.yaml@host:8080") == ("gov.yaml", "host:8080")


def test_flavour_control_rows_refuse_a_ref_that_walks_up() -> None:
    from mas.ctl.session.flavour_control import flavour_control_rows

    with pytest.raises(ControlError, match="stay under the directory"):
        flavour_control_rows({"control": [{"ref": "../gov.yaml"}]})


def test_a_control_ref_cannot_walk_out_of_the_flavour_directory(tmp_path: Path) -> None:
    outside = tmp_path / "secret.yaml"
    outside.write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: secret}\nspec: {governance: [sample_governance]}\n",
        encoding="utf-8",
    )
    flavour_dir = tmp_path / "flavours"
    flavour_dir.mkdir()
    with pytest.raises(ControlError, match="stay under the directory"):
        resolve_controls({"control": [{"ref": "../secret.yaml"}]}, search_dirs=[flavour_dir])


def test_a_package_control_ref_cannot_walk_up(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import attach_cli_controls

    flavour: dict = {}
    with pytest.raises(ControlError, match="stay inside the package"):
        attach_cli_controls(
            flavour,
            ["pkg://mas.library.standard/controls/../flavours/local.yaml"],
            search_dirs=[tmp_path],
        )


def test_session_flavour_with_controls_refuses_a_non_mapping(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import session_flavour_with_controls

    path = _write_control(tmp_path)
    with pytest.raises(ControlError, match="Flavour spec mapping"):
        session_flavour_with_controls("not-a-flavour", controls=[path.name], search_dirs=[tmp_path])


def test_not_entry_attaches_to_everyone_but_the_entry(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    flavour = {"control": [{"ref": path.name, "agents": "$not-entry"}]}
    skipped = apply_flavour_controls(
        {"spec": {}}, flavour, agent_id="desk", entry_id="desk", search_dirs=[tmp_path], warn=False
    )
    attached = apply_flavour_controls(
        {"spec": {}}, flavour, agent_id="bill", entry_id="desk", search_dirs=[tmp_path], warn=False
    )
    nobody = apply_flavour_controls({"spec": {}}, flavour, agent_id="bill", search_dirs=[tmp_path], warn=False)
    controls = resolve_controls(flavour, search_dirs=[tmp_path])
    assert "governance" not in skipped["spec"]
    assert "governance" not in attached["spec"]
    assert "governance" not in nobody["spec"]
    assert _lists(controls, agent_id="desk", entry_id="desk")["governance"] == []
    assert _lists(controls, agent_id="bill", entry_id="desk")["governance"] == ["sample_governance"]
    assert _lists(controls, agent_id="bill")["governance"] == []


def test_jq_agent_star_attaches_to_every_agent(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    flavour = {"control": [{"ref": path.name, "agents": ".mas.agent[*]"}]}
    for agent_id in ("desk", "bill"):
        applied = apply_flavour_controls(
            {"spec": {}}, flavour, agent_id=agent_id, entry_id="desk", search_dirs=[tmp_path], warn=False
        )
        assert "governance" not in applied["spec"]
        assert _lists(resolve_controls(flavour, search_dirs=[tmp_path]), agent_id=agent_id, entry_id="desk")[
            "governance"
        ] == ["sample_governance"]


def test_jq_agent_id_attaches_to_that_agent(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    for selector in (
        ".mas.agent[desk]",
        ".mas.agent[id=desk]",
        '.mas.agent["desk"]',
        ".agent[desk]",
        ".app.agent[desk]",
        "mas:agent[desk]",
    ):
        flavour = {"control": [{"ref": path.name, "agents": selector}]}
        on_desk = apply_flavour_controls({"spec": {}}, flavour, agent_id="desk", search_dirs=[tmp_path], warn=False)
        on_bill = apply_flavour_controls({"spec": {}}, flavour, agent_id="bill", search_dirs=[tmp_path], warn=False)
        controls = resolve_controls(flavour, search_dirs=[tmp_path])
        assert "governance" not in on_desk["spec"], selector
        assert "governance" not in on_bill["spec"], selector
        assert _lists(controls, agent_id="desk")["governance"] == ["sample_governance"], selector
        assert _lists(controls, agent_id="bill")["governance"] == [], selector


def test_a_path_that_is_not_an_agent_path_is_refused() -> None:
    from mas.ctl.session.flavour_control import agent_matches

    with pytest.raises(ControlError, match="agent path"):
        agent_matches(".mas.infra[x]", agent_id="desk")


def test_prod_refuses_an_agent_with_no_usage_row() -> None:
    spec = resolve_flavour("prod")
    with pytest.raises(BindError, match="require_usage: agent 'desk' has no bind chain or agent-keyed Application row"):
        bind_session(ResolvedInfra(), spec, agent_ids=["desk"])


def test_local_allows_an_agent_with_no_usage_row() -> None:
    spec = resolve_flavour("local")
    bound = bind_session(ResolvedInfra(), spec, agent_ids=["desk"])
    assert bound.agent_chains == {}


def test_bind_events_is_not_an_unknown_key() -> None:
    bound = bind_session(
        ResolvedInfra(applications={"native": {"protocol": "native", "path": "traces/events.jsonl"}}),
        {"bind": {"events": ["native"]}},
        agent_ids=["desk"],
    )
    assert "native" in bound.applications


def test_bundled_prod_resolves() -> None:
    assert resolve_flavour("prod") == {"require_usage": True}


def test_bundled_flavours_have_no_leftover_unread_keys() -> None:
    from importlib.resources import files

    import yaml

    spec = resolve_flavour("local")
    assert all(key not in spec for key in _LEFTOVER)
    text = files("mas.library.standard").joinpath("flavours/local-benchmark.yaml").read_text(encoding="utf-8")
    bench = yaml.safe_load(text)
    assert all(key not in (bench.get("spec") or {}) for key in _LEFTOVER)


def test_require_usage_is_a_declared_field_not_the_name_prod() -> None:
    with pytest.raises(BindError, match="require_usage: agent 'desk' has no bind chain or agent-keyed Application row"):
        bind_session(ResolvedInfra(), {"require_usage": True}, agent_ids=["desk"])
    bound = bind_session(ResolvedInfra(), {"_flavour": "prod"}, agent_ids=["desk"])
    assert bound.agent_chains == {}


def test_bind_tools_is_an_unknown_key() -> None:
    with pytest.raises(BindError, match=r"bind: unknown keys \['tools'\]"):
        bind_session(ResolvedInfra(), {"bind": {"tools": ["mcp-tools"]}}, agent_ids=["desk"])


def test_flavour_attaches_a_control_to_named_agents_the_way_bind_names_chains(tmp_path: Path) -> None:
    desk = _write_control(tmp_path, name="desk-gov", spec={"plugins": ["sample_governance"]})
    bill = _write_control(tmp_path, name="bill-gov", spec={"plugins": ["retry_on_error"]})
    flavour = {
        "control": [
            {"ref": desk.name, "agents": "desk"},
            {"ref": bill.name, "agents": ["bill"]},
        ]
    }
    on_desk = apply_flavour_controls({"spec": {}}, flavour, agent_id="desk", search_dirs=[tmp_path], warn=False)
    on_bill = apply_flavour_controls({"spec": {}}, flavour, agent_id="bill", search_dirs=[tmp_path], warn=False)
    controls = resolve_controls(flavour, search_dirs=[tmp_path])
    assert "governance" not in on_desk["spec"]
    assert "governance" not in on_bill["spec"]
    assert _lists(controls, agent_id="desk")["governance"] == ["sample_governance"]
    assert _lists(controls, agent_id="bill")["governance"] == ["retry_on_error"]


def test_an_empty_agents_list_is_refused(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"agents": [], "plugins": ["sample_governance"]})
    with pytest.raises(ControlError, match="invalid"):
        load_control(path)


def test_overlay_controls_to_manifest_do_not_treat_every_agent_as_entry(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import apply_overlay_controls_to_manifest

    path = _write_control(tmp_path, spec={"agents": "$entry", "plugins": ["sample_governance"]})
    skipped = apply_overlay_controls_to_manifest({"spec": {}}, [path], agent_id="desk", warn=False)
    attached = apply_overlay_controls_to_manifest({"spec": {}}, [path], agent_id="desk", entry_id="desk", warn=False)
    from mas.ctl.session.flavour_control import control_row_from_path

    row = control_row_from_path(path)
    assert "governance" not in skipped["spec"]
    assert "governance" not in attached["spec"]
    assert _lists([row], agent_id="desk")["governance"] == []
    assert _lists([row], agent_id="desk", entry_id="desk")["governance"] == ["sample_governance"]


def test_apply_flavour_controls_does_not_treat_every_agent_as_entry(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    flavour = {"control": [{"ref": path.name, "agents": "$entry"}]}
    skipped = apply_flavour_controls({"spec": {}}, flavour, agent_id="bill", search_dirs=[tmp_path], warn=False)
    attached = apply_flavour_controls(
        {"spec": {}}, flavour, agent_id="desk", entry_id="desk", search_dirs=[tmp_path], warn=False
    )
    controls = resolve_controls(flavour, search_dirs=[tmp_path])
    assert "governance" not in skipped["spec"]
    assert "governance" not in attached["spec"]
    assert _lists(controls, agent_id="bill")["governance"] == []
    assert _lists(controls, agent_id="desk", entry_id="desk")["governance"] == ["sample_governance"]


def test_control_hash_does_not_resolve_a_relative_ref_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ControlError, match="is not a file"):
        control_body_for_hash({"control": [{"ref": control.name, "agents": "$entry"}]})


def test_control_bodies_are_resolved_when_the_flavour_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    flavour_path = tmp_path / "desk.yaml"
    flavour_path.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        "spec:\n  control:\n    - {ref: gov.yaml, agents: $entry}\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec = resolve_flavour(str(flavour_path))
    assert control_body_for_hash(spec) == [{"agents": "$entry", "governance": ["sample_governance"]}]
    (tmp_path / "gov.yaml").write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: gov}\nspec:\n  governance: [retry_on_error]\n",
        encoding="utf-8",
    )
    again = resolve_flavour(str(flavour_path))
    assert control_body_for_hash(again) == [{"agents": "$entry", "governance": ["retry_on_error"]}]


def test_control_ref_next_to_the_flavour_file(tmp_path: Path) -> None:
    _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    flavour_path = tmp_path / "desk.yaml"
    flavour_path.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        "spec:\n  control:\n    - {ref: gov.yaml, agents: $entry}\n",
        encoding="utf-8",
    )
    spec = resolve_flavour(str(flavour_path))
    applied = apply_flavour_controls({"spec": {}}, spec, agent_id="desk", entry_id="desk", warn=False)
    assert "governance" not in applied["spec"]
    assert _lists(resolve_controls(spec), agent_id="desk", entry_id="desk")["governance"] == ["sample_governance"]


def test_control_hash_includes_agents_selectors(tmp_path: Path) -> None:
    _write_control(tmp_path, name="a", spec={"plugins": ["sample_governance"]})
    entry = control_body_for_hash({"control": [{"ref": "a.yaml", "agents": "$entry"}]}, search_dirs=[tmp_path])
    all_agents = control_body_for_hash({"control": [{"ref": "a.yaml", "agents": "$all"}]}, search_dirs=[tmp_path])
    assert entry != all_agents


def test_bind_events_does_not_copy_secret_env() -> None:
    cfg = observability_config_from_manifest(
        {"spec": {}},
        flavour_spec={"bind": {"events": ["sink"]}},
        resolved_infra=ResolvedInfra(
            applications={"sink": {"protocol": "native", "path": "traces/from-row.jsonl", "secret_env": "TOKEN"}}
        ),
    )
    native = cfg.plugin_configs.get("native") or {}
    assert "secret_env" not in native
    assert native.get("path") == "traces/from-row.jsonl"


def test_bind_events_does_not_copy_url() -> None:
    cfg = observability_config_from_manifest(
        {"spec": {}},
        flavour_spec={"bind": {"events": ["sink"]}},
        resolved_infra=ResolvedInfra(
            applications={
                "sink": {
                    "protocol": "native",
                    "path": "traces/from-row.jsonl",
                    "url": "http://collector.example/v1",
                }
            }
        ),
    )
    native = cfg.plugin_configs.get("native") or {}
    assert "url" not in native
    assert native.get("path") == "traces/from-row.jsonl"


def _flavour_with_control(tmp_path: Path, spec: dict, *, agents: str = "$all") -> dict:
    _write_control(tmp_path, name="obs", spec=spec)
    path = tmp_path / "desk.yaml"
    path.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        f"spec:\n  control:\n    - {{ref: obs.yaml, agents: '{agents}'}}\n",
        encoding="utf-8",
    )
    return resolve_flavour(str(path))


def test_control_observability_validates() -> None:
    doc = _control_doc(spec={"observability": ["mas.observability.native", "otel"]})
    assert validate_data(doc, kind="control", strict=True, resolve_refs=False).ok


def test_a_control_selects_the_observability_plugins(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]})
    config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert config.plugins == ["otel"]


def test_a_control_replaces_the_deprecated_agent_observability_with_one_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]})
    agent = {"spec": {"observability": ["native"]}}
    config = observability_config_from_manifest(agent, agent_id="desk", flavour_spec=flavour)
    assert config.plugins == ["otel"]


def test_a_control_for_another_agent_leaves_the_entry_observability_alone(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]}, agents="$delegates")
    config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert config.plugins == ["native"]


def test_observability_does_not_treat_every_agent_as_entry(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]}, agents="$entry")
    skipped = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    attached = observability_config_from_manifest({"spec": {}}, agent_id="desk", entry_id="desk", flavour_spec=flavour)
    assert skipped.plugins == ["native"]
    assert attached.plugins == ["otel"]


def test_the_entry_agent_selects_a_control_observability_for_a_mas_run(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]}, agents="$entry")
    config = observability_config_from_manifest({"spec": {}}, entry_id="desk", flavour_spec=flavour)
    assert config.plugins == ["otel"]
    other = observability_config_from_manifest({"spec": {}}, entry_id="", flavour_spec=flavour)
    assert other.plugins == ["native"]


@pytest.mark.parametrize(
    ("kwargs", "plugins"),
    [
        ({"cli_events_stdout": True}, ["otel", "native"]),
        ({"cli_events_file": "x.jsonl"}, ["otel", "native"]),
        ({"cli_events": True}, ["otel"]),
    ],
)
def test_events_output_flags_keep_a_control_observability(tmp_path: Path, kwargs: dict, plugins: list[str]) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]})
    config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour, **kwargs)
    assert config.plugins == plugins


def test_no_events_turns_a_control_observability_off(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]})
    config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour, cli_events=False)
    assert config.enabled is False


def test_the_plugin_pipeline_is_in_the_hash(tmp_path: Path) -> None:
    a = control_body_for_hash(
        _flavour_with_control(tmp_path, {"governance": ["sample_governance"], "observability": ["otel"]})
    )
    native = {"governance": ["sample_governance"], "observability": ["mas.observability.native"]}
    b = control_body_for_hash(_flavour_with_control(tmp_path, native))
    assert a == [{"agents": "$all", "governance": ["sample_governance"], "observability": ["otel"]}]
    assert b == [{"agents": "$all", "governance": ["sample_governance"], "observability": native["observability"]}]
    assert a != b


def test_the_native_categories_filter_is_in_the_hash(tmp_path: Path) -> None:
    spec = {"observability": [{"mas.observability.native": {"categories": ["llm_call"]}}]}
    body = control_body_for_hash(_flavour_with_control(tmp_path, spec))
    assert body == [{"agents": "$all", "observability": [{"mas.observability.native": {"categories": ["llm_call"]}}]}]


def test_native_categories_include_exclude_is_in_the_hash(tmp_path: Path) -> None:
    native = {"mas.observability.native": {"categories": {"include": ["llm_call"], "exclude": ["tool"]}}}
    spec = {"observability": [native]}
    body = control_body_for_hash(_flavour_with_control(tmp_path, spec))
    assert body == [{"agents": "$all", "observability": [native]}]


def test_apply_controls_does_not_copy_observability_or_governance(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"plugins": ["otel", "sample_governance"]})
    controls = resolve_controls({"control": [{"ref": path.name}]}, search_dirs=[tmp_path])
    applied, _ = apply_controls({"spec": {}}, controls, agent_id="desk", entry_id="desk")
    assert "governance" not in applied["spec"]
    assert "observability" not in applied["spec"]
    lists = _lists(controls, agent_id="desk", entry_id="desk")
    assert lists["governance"] == ["sample_governance"]
    assert lists["observability"] == ["otel"]


def test_two_controls_union_governance_and_observability(tmp_path: Path) -> None:
    first = _write_control(tmp_path, name="first", spec={"plugins": ["sample_governance"]})
    second = _write_control(tmp_path, name="second", spec={"plugins": ["gov_no_undeclared_tool"]})
    controls = resolve_controls(
        {"control": [{"ref": first.name}, {"ref": second.name}]},
        search_dirs=[tmp_path],
    )
    applied, warnings = apply_controls({"spec": {}}, controls, agent_id="desk", entry_id="desk")
    assert "governance" not in applied["spec"]
    assert warnings == []
    assert _lists(controls, agent_id="desk", entry_id="desk")["governance"] == [
        "gov_no_undeclared_tool",
        "sample_governance",
    ]


def test_control_lists_do_not_replace_agent_spec_keys(tmp_path: Path) -> None:
    first = _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    second = _write_control(tmp_path, name="events", spec={"plugins": ["otel"]})
    controls = resolve_controls(
        {"control": [{"ref": first.name}, {"ref": second.name}]},
        search_dirs=[tmp_path],
    )
    applied, warnings = apply_controls(
        {"spec": {"governance": ["retry_on_error"], "observability": ["native"]}},
        controls,
        agent_id="desk",
        entry_id="desk",
    )
    assert applied["spec"]["governance"] == ["retry_on_error"]
    assert applied["spec"]["observability"] == ["native"]
    assert warnings == []
    lists = _lists(controls, agent_id="desk", entry_id="desk")
    assert lists["governance"] == ["sample_governance"]
    assert lists["observability"] == ["otel"]


def test_leftover_agent_governance_still_applies_without_a_control() -> None:
    spec = spec_with_control_lists(
        {"governance": [{"sample_governance": {"hitl_on_tool": True}}], "observability": ["otel"]},
        None,
    )
    assert spec["governance"] == [{"sample_governance": {"hitl_on_tool": True}}]
    assert spec["observability"] == ["otel"]
    kernel = kernel_config_from_manifest({"spec": {"governance": [{"sample_governance": {"hitl_on_tool": True}}]}})
    assert kernel.egress_governance_plugin is not None


def test_leftover_agent_list_is_unread_only_for_a_list_a_matching_control_names(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["sample_governance"]})
    spec = spec_with_control_lists(
        {"governance": [{"retry_on_error": {}}], "observability": ["otel"]},
        flavour,
        agent_id="desk",
        entry_id="desk",
    )
    assert spec["governance"] == ["sample_governance"]
    # The Control names governance only: the agent's own observability stays in force.
    assert spec["observability"] == ["otel"]


def test_leftover_agent_lists_still_apply_when_no_control_matches(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["sample_governance"]}, agents="$entry")
    leftover = {"governance": [{"sample_governance": {"hitl_on_tool": True}}], "observability": ["otel"]}
    spec = spec_with_control_lists(leftover, flavour, agent_id="other", entry_id="desk")
    assert spec["governance"] == leftover["governance"]
    assert spec["observability"] == leftover["observability"]


def test_leftover_agent_observability_is_unread_when_a_control_matches(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["mas.observability.native"]})
    cfg = observability_config_from_manifest(
        {"spec": {"observability": ["otel"]}},
        flavour_spec=flavour,
        agent_id="desk",
        entry_id="desk",
    )
    assert cfg.plugins == ["native"]


def test_control_overlay_op_add_same_plugin_id_last_wins() -> None:
    from mas.ctl.overlay.merge import merge_overlay

    base = {
        "apiVersion": "control/v1",
        "kind": "Control",
        "metadata": {"name": "gov"},
        "spec": {"governance": [{"budget": {"max_llm_calls": 1}}]},
    }
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "caps"},
        "spec": {
            "target": {"kind": "Control", "name": "gov"},
            "patch": {"governance": {"$op": {"add": [{"budget": {"max_llm_calls": 9}}]}}},
        },
    }
    merged = merge_overlay(base, overlay)
    assert merged["spec"]["governance"] == [{"budget": {"max_llm_calls": 9}}]


def test_two_controls_same_governance_id_last_config_wins(tmp_path: Path) -> None:
    first = _write_control(tmp_path, name="first", spec={"governance": [{"sample_governance": {"hitl_on_tool": True}}]})
    second = _write_control(
        tmp_path, name="second", spec={"governance": [{"sample_governance": {"hitl_on_tool_result": True}}]}
    )
    controls = resolve_controls(
        {"control": [{"ref": first.name}, {"ref": second.name}]},
        search_dirs=[tmp_path],
    )
    item = _lists(controls, agent_id="desk", entry_id="desk")["governance"][0]
    name, cfg = next(iter(item.items()))
    assert name == "sample_governance"
    assert cfg["hitl_on_tool_result"] is True
    assert "hitl_on_tool" not in cfg


def test_governance_priority_orders_the_set(tmp_path: Path) -> None:
    first = _write_control(tmp_path, name="first", spec={"governance": [{"sample_governance": {"priority": 10}}]})
    second = _write_control(tmp_path, name="second", spec={"governance": ["gov_no_undeclared_tool"]})
    controls = resolve_controls(
        {"control": [{"ref": first.name}, {"ref": second.name}]},
        search_dirs=[tmp_path],
    )
    items = _lists(controls, agent_id="desk", entry_id="desk")["governance"]
    names = [item if isinstance(item, str) else next(iter(item)) for item in items]
    assert names == ["sample_governance", "gov_no_undeclared_tool"]


def test_two_controls_concatenate_event_plugins(tmp_path: Path) -> None:
    first = _write_control(tmp_path, name="first", spec={"plugins": ["mas.observability.native"]})
    second = _write_control(tmp_path, name="second", spec={"plugins": ["otel"]})
    flavour = {
        "control": [{"ref": first.name}, {"ref": second.name}],
        "_controls": [
            {
                "ref": first.name,
                "agents": "$all",
                "spec": {"observability": ["mas.observability.native"]},
                "name": "first",
            },
            {"ref": second.name, "agents": "$all", "spec": {"observability": ["otel"]}, "name": "second"},
        ],
    }
    config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert config.plugins == ["native", "otel"]


def test_bind_events_overlays_path_onto_control_native(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["mas.observability.native"]})
    flavour["bind"] = {"events": ["sink"]}
    cfg = observability_config_from_manifest(
        {"spec": {}},
        agent_id="desk",
        flavour_spec=flavour,
        resolved_infra=ResolvedInfra(applications={"sink": {"protocol": "native", "path": "traces/from-row.jsonl"}}),
    )
    assert cfg.plugins == ["native"]
    assert cfg.events_file == "traces/from-row.jsonl"


def test_a_bind_events_row_adds_its_plugin_to_a_control_selection(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["otel"]})
    flavour["bind"] = {"events": ["native"]}
    cfg = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour, resolved_infra=_SINK)
    assert cfg.plugins == ["otel", "native"]


def test_attach_overlay_controls_refuses_a_missing_flavour(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import attach_overlay_controls

    path = _write_control(tmp_path)
    with pytest.raises(ControlError, match="Flavour"):
        attach_overlay_controls(None, [path])


def test_a_bind_events_row_adds_its_sink_and_keeps_native() -> None:
    infra = ResolvedInfra(applications={"collector": {"protocol": "otel", "usage": ["connect"]}})
    cfg = observability_config_from_manifest(
        {"spec": {}}, agent_id="desk", flavour_spec={"bind": {"events": ["collector"]}}, resolved_infra=infra
    )
    assert cfg.plugins == ["otel", "native"]
    assert cfg.enabled is True


def test_no_events_ignores_the_bind_events_rows() -> None:
    infra = ResolvedInfra(applications={"collector": {"protocol": "otel", "usage": ["connect"]}})
    cfg = observability_config_from_manifest(
        {"spec": {}}, flavour_spec={"bind": {"events": ["collector"]}}, resolved_infra=infra, cli_events=False
    )
    assert cfg.enabled is False


def test_minus_o_control_attaches_observability(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import attach_overlay_controls

    path = _write_control(tmp_path, spec={"plugins": ["otel"]})
    flavour: dict = {}
    attach_overlay_controls(flavour, [path])
    cfg = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert cfg.plugins == ["otel"]


def test_control_files_from_an_experiment_stack_attach_without_a_minus_o_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mas.ctl.session.flavour_control import attach_control_files

    path = _write_control(tmp_path, spec={"plugins": ["sample_governance"]})
    flavour: dict = {}
    attach_control_files(flavour, [path])
    applied = apply_flavour_controls({"spec": {}}, flavour, agent_id="desk", warn=False)
    assert "governance" not in applied["spec"]
    assert _lists(resolve_controls(flavour), agent_id="desk")["governance"] == ["sample_governance"]
    err = capsys.readouterr().err
    assert "deprecated" not in err
    assert "-o" not in err


def test_a_relative_path_not_listed_in_path_params_is_refused(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"path_probe": {"script_file": "break.gdb"}}]})
    with pytest.raises(ControlError, match="path_params"):
        load_control(path)


def test_native_run_path_stays_relative_to_the_run(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"observability": [{"native": {"path": "traces/events.jsonl"}}]})
    load_control(path)
    flavour = {"control": [{"ref": path.name}]}
    item = _lists(resolve_controls(flavour, search_dirs=[tmp_path]), agent_id="desk")["observability"][0]
    assert item["native"]["path"] == "traces/events.jsonl"


def test_a_governance_plugin_from_a_control_resolves_paths_against_the_control_directory(tmp_path: Path) -> None:
    control_dir = tmp_path / "controls"
    control_dir.mkdir()
    path = _write_control(control_dir, name="debug", spec={"plugins": [{"gdb": {"script_file": "break.gdb"}}]})
    controls = resolve_controls({"control": [{"ref": path.name}]}, search_dirs=[control_dir])
    applied, _ = apply_controls({"spec": {}}, controls, agent_id="desk", entry_id="desk")
    assert "runtime" not in applied["spec"]
    assert "governance" not in applied["spec"]
    item = _lists(controls, agent_id="desk", entry_id="desk")["governance"][0]["gdb"]
    assert Path(item["script_file"]).resolve() == (control_dir / "break.gdb").resolve()
    assert "manifest_dir" not in item


def test_an_absolute_path_in_a_control_is_refused(tmp_path: Path) -> None:
    path = _write_control(tmp_path, name="abs", spec={"plugins": [{"path_probe": {"script_file": "/etc/break.gdb"}}]})
    with pytest.raises(ControlError, match="absolute path '/etc/break.gdb'"):
        load_control(path)


def test_a_control_path_that_walks_up_is_refused(tmp_path: Path) -> None:
    path = _write_control(tmp_path, name="up", spec={"plugins": [{"path_probe": {"script_file": "../break.gdb"}}]})
    with pytest.raises(ControlError, match="must stay under the directory that holds it"):
        load_control(path)


def test_an_absolute_ref_in_a_flavour_control_row_is_refused(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import flavour_control_rows

    with pytest.raises(ControlError, match="absolute path"):
        flavour_control_rows({"control": [{"ref": str(tmp_path / "gov.yaml")}]})


def test_urls_are_not_absolute_paths(tmp_path: Path) -> None:
    path = _write_control(tmp_path, name="url", spec={"plugins": [{"otel": {"otlp_endpoint_env": "X"}}]})
    assert load_control(path)["kind"] == "Control"


def test_observability_config_does_not_warn_that_control_replaced_governance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["sample_governance"]})
    observability_config_from_manifest(
        {"spec": {"governance": ["retry_on_error"]}},
        agent_id="desk",
        flavour_spec=flavour,
    )
    assert "replaces spec.governance" not in capsys.readouterr().err


def test_two_controls_selecting_event_plugins_stay_concatenated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_control(tmp_path, name="a", spec={"plugins": ["otel"]})
    _write_control(tmp_path, name="b", spec={"plugins": ["mas.observability.native"]})
    path = tmp_path / "desk.yaml"
    path.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        "spec:\n  control:\n    - {ref: a.yaml}\n    - {ref: b.yaml}\n",
        encoding="utf-8",
    )
    flavour = resolve_flavour(str(path))
    for _ in range(3):
        config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert config.plugins == ["otel", "native"]
    assert "replaces spec.observability" not in capsys.readouterr().err


def test_attaching_the_same_control_twice_does_not_warn_it_replaced_its_own_observability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mas.ctl.session.flavour_control import attach_cli_controls

    flavour = _flavour_with_control(tmp_path, {"plugins": ["mas.observability.native"]})
    attach_cli_controls(flavour, ["obs.yaml"], search_dirs=[tmp_path])
    config = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert config.plugins == ["native"]
    assert "replaces" not in capsys.readouterr().err


def test_applying_the_same_control_twice_does_not_warn_it_replaced_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flavour = _flavour_with_control(tmp_path, {"plugins": ["sample_governance"]})
    agent = {"spec": {}}
    once = apply_flavour_controls(agent, flavour, agent_id="desk", entry_id="desk")
    assert "replaces spec.governance" not in capsys.readouterr().err
    apply_flavour_controls(once, flavour, agent_id="desk", entry_id="desk")
    assert "replaces spec.governance" not in capsys.readouterr().err


def test_governance_from_a_control_decides_the_hitl_responder(tmp_path: Path) -> None:
    from mas.ctl.session.hitl_config import resolve_hitl_from_manifest

    flavour = _flavour_with_control(
        tmp_path,
        {"plugins": [{"sample_governance": {"hitl_on_tool": True, "hitl_mode": "auto_approve"}}]},
    )
    agent = {"apiVersion": "mas/v1", "kind": "Agent", "metadata": {"name": "desk"}, "spec": {}}
    assert resolve_hitl_from_manifest(agent, session_interactive=False) == (None, None)
    responder, _ = resolve_hitl_from_manifest(
        agent, session_interactive=False, flavour_spec=flavour, agent_id="desk", entry_id="desk"
    )
    assert responder is not None


def test_validate_refuses_an_absolute_path_in_a_control_and_a_flavour_row() -> None:
    control = _control_doc(spec={"governance": [{"path_probe": {"script_file": "/etc/x.gdb"}}]})
    result = validate_data(control, kind="control", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("absolute path '/etc/x.gdb'" in issue.message for issue in result.issues)
    flavour = {
        "apiVersion": "flavour/v1",
        "kind": "Flavour",
        "metadata": {"name": "desk"},
        "spec": {"control": [{"ref": "/etc/gov.yaml"}]},
    }
    result = validate_data(flavour, kind="flavour", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("spec.control[0].ref" == issue.path for issue in result.issues)
    flavour["spec"]["control"] = [{"ref": "../gov.yaml"}]
    result = validate_data(flavour, kind="flavour", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("must stay under the directory that holds it" in issue.message for issue in result.issues)


def test_the_same_control_file_on_different_agents_is_two_attachments(tmp_path: Path) -> None:
    _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    spec = resolve_session_flavour(None, controls=("gov.yaml@desk", "gov.yaml@bill"), search_dirs=[tmp_path])
    assert [row["agents"] for row in spec["_controls"]] == ["desk", "bill"]
    for agent in ("desk", "bill"):
        applied = apply_flavour_controls({"spec": {}}, spec, agent_id=agent, entry_id="desk", warn=False)
        assert "governance" not in applied["spec"], agent
        assert _lists(resolve_controls(spec), agent_id=agent, entry_id="desk")["governance"] == ["sample_governance"], (
            agent
        )


def test_the_same_control_file_on_the_same_agents_is_one_attachment(tmp_path: Path) -> None:
    _write_control(tmp_path, name="gov", spec={"plugins": ["sample_governance"]})
    spec = resolve_session_flavour(None, controls=("gov.yaml@desk,bill", "gov.yaml@bill,desk"), search_dirs=[tmp_path])
    assert len(spec["_controls"]) == 1


@pytest.mark.parametrize("flag", [{"cli_events_file": "/tmp/x.jsonl"}, {"cli_events_stdout": True}])
def test_an_events_file_or_stream_means_native_records_next_to_the_sink(flag: dict) -> None:
    infra = ResolvedInfra(applications={"collector": {"protocol": "otel", "usage": ["connect"]}})
    cfg = observability_config_from_manifest(
        {"spec": {}}, flavour_spec={"bind": {"events": ["collector"]}}, resolved_infra=infra, **flag
    )
    assert cfg.plugins == ["otel", "native"]


def test_the_bench_style_call_keeps_native_when_the_flavour_names_a_sink(tmp_path: Path) -> None:
    infra = ResolvedInfra(applications={"collector": {"protocol": "otel", "usage": ["connect"]}})
    cfg = observability_config_from_manifest(
        {"spec": {}},
        cli_events=True,
        cli_events_file=str(tmp_path / "traces" / "events.jsonl"),
        flavour_spec={"bind": {"events": ["collector"]}},
        resolved_infra=infra,
    )
    assert "native" in cfg.plugins
    assert cfg.events_file == str(tmp_path / "traces" / "events.jsonl")


@pytest.mark.parametrize("bad", ["high", True, 1.5])
def test_a_non_integer_priority_is_refused_at_load(tmp_path: Path, bad: object) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"gov_no_undeclared_tool": {"priority": bad}}]})
    with pytest.raises(ControlError, match="priority must be an integer"):
        load_control(path)


def test_a_control_naming_only_observability_keeps_the_agents_leftover_governance() -> None:
    from mas.ctl.session.manifest_config import spec_with_control_lists

    spec = {"governance": ["gov_no_undeclared_tool"], "observability": ["otel"]}
    obs_only = {"_controls": [{"ref": "c", "agents": "$all", "spec": {"observability": ["mas.observability.native"]}}]}
    gov_only = {"_controls": [{"ref": "c", "agents": "$all", "spec": {"governance": ["budget"]}}]}
    got = spec_with_control_lists(spec, obs_only, agent_id="a", entry_id="a")
    assert got["governance"] == ["gov_no_undeclared_tool"]
    assert got["observability"] == ["mas.observability.native"]
    got = spec_with_control_lists(spec, gov_only, agent_id="a", entry_id="a")
    assert got["governance"] == ["budget"]
    assert got["observability"] == ["otel"]


def test_agent_budget_and_control_keys_are_deprecated_with_a_readable_message() -> None:
    from mas.ctl.validate.deprecations import collect_agent_control_keys_deprecation

    agent = {
        "kind": "Agent",
        "spec": {"budget": {"max_llm_calls": 1}, "control": {"rate_limiter": {"requests_per_minute": 1}}},
    }
    messages = [message for _where, message in collect_agent_control_keys_deprecation(agent)]
    assert any("spec.budget is deprecated and still enforced" in m for m in messages)
    assert any("spec.control.rate_limiter is deprecated and read by no plugin" in m for m in messages)
    assert not any("{" in m for m in messages)
    # An Overlay's spec.control is the attach list of Control refs, not a leftover.
    overlay = {"kind": "Overlay", "spec": {"target": {"kind": "Agent"}, "control": ["gov.yaml"], "patch": {}}}
    assert collect_agent_control_keys_deprecation(overlay) == []


def test_a_control_list_takes_only_the_plugins_it_names_a_type_for(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import load_control

    cases = [
        ("governance", "circuit_breaker", "registered as type layer; this list takes"),
        ("governance", "mas.observability.native", "registered as type observability; this list takes"),
        ("observability", "budget", "registered as type governance; this list takes"),
    ]
    for index, (key, name, message) in enumerate(cases):
        path = _write_control(tmp_path, name=f"bad-{index}", spec={key: [name]})
        with pytest.raises(ControlError, match=message):
            load_control(path)


def test_a_control_plugin_name_must_be_registered(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import load_control

    path = _write_control(tmp_path, spec={"governance": ["no_such_plugin_anywhere"]})
    with pytest.raises(ControlError, match="unknown plugin 'no_such_plugin_anywhere'"):
        load_control(path)


def test_a_control_has_no_budget_key(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import load_control

    path = _write_control(tmp_path, spec={"budget": {"max_llm_calls": 5}})
    with pytest.raises(ControlError, match="invalid"):
        load_control(path)


def test_the_budget_plugin_is_named_in_governance(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"budget": {"max_llm_calls": 5}}]})
    controls = resolve_controls({"control": [{"ref": path.name}]}, search_dirs=[tmp_path])
    [item] = _lists(controls, agent_id="desk", entry_id="desk")["governance"]
    assert item["budget"]["max_llm_calls"] == 5


def test_the_list_says_which_shortcut_is_meant_and_a_name_is_matched_exactly(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import load_control

    # `native` is the name of two plugins of different types (observability, skill engine): the list picks one.
    ok = _write_control(tmp_path, name="ok", spec={"observability": ["native"], "governance": ["budget"]})
    load_control(ok)
    # A URN's last segment is not a name.
    bad = _write_control(tmp_path, name="suffix", spec={"governance": ["no_undeclared_tool_plugin"]})
    with pytest.raises(ControlError, match="unknown plugin"):
        load_control(bad)


def _settings_flavour(path: Path) -> dict:
    return {
        "control": [
            {"ref": path.name},
            {"ref": path.name, "agents": "bill", "settings": {"budget": {"max_llm_calls": 5}}},
        ]
    }


def test_one_control_file_carries_other_settings_on_one_agent(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"budget": {"max_llm_calls": 50, "max_tool_calls": 7}}]})
    controls = resolve_controls(_settings_flavour(path), search_dirs=[tmp_path])
    assert len(controls) == 2  # same file, other settings: another attachment
    [desk] = _lists(controls, agent_id="desk", entry_id="desk")["governance"]
    [bill] = _lists(controls, agent_id="bill", entry_id="desk")["governance"]
    assert desk["budget"]["max_llm_calls"] == 50
    assert bill["budget"]["max_llm_calls"] == 5  # the row for bill wins
    assert bill["budget"]["max_tool_calls"] == 7  # settings merge over the plugin's own, they do not replace it


def test_settings_naming_a_plugin_the_control_does_not_list_are_refused(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": ["gov_no_undeclared_tool"]})
    flavour = {"control": [{"ref": path.name, "settings": {"budget": {"max_llm_calls": 5}}}]}
    with pytest.raises(ControlError, match="name plugin 'budget', which Control .* does not list"):
        resolve_controls(flavour, search_dirs=[tmp_path])


def test_settings_are_validated_like_the_plugins_own(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"budget": {"max_llm_calls": 50}}]})
    flavour = {"control": [{"ref": path.name, "settings": {"budget": {"bogus": 1}}}]}
    with pytest.raises(ControlError, match="unknown field"):
        resolve_controls(flavour, search_dirs=[tmp_path])


def test_settings_change_the_run_hash_body(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"budget": {"max_llm_calls": 50}}]})
    plain = control_body_for_hash({"control": [{"ref": path.name}]}, search_dirs=[tmp_path])
    tuned = control_body_for_hash(
        {"control": [{"ref": path.name, "settings": {"budget": {"max_llm_calls": 5}}}]}, search_dirs=[tmp_path]
    )
    assert plain != tuned


def test_a_flavour_settings_row_must_map_plugins_to_settings(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"budget": {}}]})
    with pytest.raises(ControlError, match="settings maps a plugin name to its settings"):
        resolve_controls({"control": [{"ref": path.name, "settings": ["budget"]}]}, search_dirs=[tmp_path])


def test_retry_on_error_and_its_urn_are_one_plugin_id(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": ["retry_on_error", "mas.gov.retry_on_error"]})
    with pytest.raises(ControlError, match="already in the set"):
        load_control(path)


def test_file_url_in_a_control_is_an_absolute_path(tmp_path: Path) -> None:
    path = _write_control(tmp_path, spec={"governance": [{"path_probe": {"script_file": "file:///etc/break.gdb"}}]})
    with pytest.raises(ControlError, match="absolute path"):
        load_control(path)


def test_empty_control_observability_does_not_fill_native(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"observability": []})
    cfg = observability_config_from_manifest({"spec": {}}, agent_id="desk", flavour_spec=flavour)
    assert cfg.plugins == []


def test_validate_chat_session_reads_control_hitl_not_leftover(tmp_path: Path) -> None:
    import click
    from mas.ctl.session.session_flags import validate_chat_session

    flavour = _flavour_with_control(
        tmp_path,
        {"governance": [{"sample_governance": {"hitl_on_tool": True, "hitl_mode": "interactive"}}]},
    )
    leftover = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "desk"},
        "spec": {"governance": [{"sample_governance": {"hitl_on_tool": True, "hitl_mode": "auto-approve"}}]},
    }
    with pytest.raises(click.UsageError, match="interactive") as err:
        validate_chat_session(
            interactive=False,
            single_turn=True,
            scripted_turns=[],
            manifest=leftover,
            flavour_spec=flavour,
            agent_id="desk",
            entry_id="desk",
            is_tty=False,
        )
    message = str(err.value)
    assert "Control" in message
    assert "overlay" not in message.lower()


def test_from_spec_no_events_clears_leftover_observability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.runtime.driver.instance import RuntimeInstance

    monkeypatch.chdir(tmp_path)  # native writes traces/events.jsonl under the working directory
    spec = {"observability": ["native"]}
    leftover = RuntimeInstance.from_spec(spec, enable_observability=True)
    assert leftover.obs_plugin_set is not None
    assert leftover.obs_plugin_set.plugins
    cleared = RuntimeInstance.from_spec(spec, enable_observability=True, obs_from_cli=True, obs_binding_override=None)
    assert cleared.obs_plugin_set is None or not cleared.obs_plugin_set.plugins


def test_control_overlay_duplicate_plugin_id_is_refused(tmp_path: Path) -> None:
    from mas.ctl.session.flavour_control import apply_control_overlays, attach_control_files

    control = _write_control(tmp_path, name="gov", spec={"governance": ["sample_governance"]})
    overlay = tmp_path / "dup.yaml"
    overlay.write_text(
        "apiVersion: mas/v1\nkind: Overlay\nmetadata: {name: dup}\n"
        "spec:\n  target: {kind: Control}\n  patch:\n    governance: [sample_governance, sample_governance]\n",
        encoding="utf-8",
    )
    flavour: dict = {}
    attach_control_files(flavour, [control])
    with pytest.raises(ControlError, match="already in the set"):
        apply_control_overlays(flavour, [overlay])


def test_an_empty_control_governance_list_warns_when_it_silences_the_agent_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flavour = _flavour_with_control(tmp_path, {"governance": []})
    spec = {"governance": ["sample_governance"]}
    out = spec_with_control_lists(spec, flavour, agent_id="desk")
    assert out["governance"] == []
    assert "empty governance list" in capsys.readouterr().err


def test_a_shared_name_and_its_urn_are_one_plugin_in_a_union() -> None:
    assert len(union_plugin_set([["native"], ["mas.observability.native"]])) == 1


def test_settings_and_overlay_remove_match_a_plugin_by_any_of_its_names(tmp_path: Path) -> None:
    """``budget``, ``budget@v1`` and ``mas.gov.budget`` are one plugin: settings and removals find it."""
    (tmp_path / "gov.yaml").write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: gov}\n"
        "spec:\n  governance:\n    - mas.gov.budget: {max_llm_calls: 9}\n",
        encoding="utf-8",
    )
    (tmp_path / "desk.yaml").write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        "spec:\n  control:\n    - ref: gov.yaml\n      agents: $all\n"
        "      settings:\n        budget@v1: {max_llm_calls: 2}\n",
        encoding="utf-8",
    )
    flavour = resolve_flavour(str(tmp_path / "desk.yaml"))
    spec = spec_with_control_lists({}, flavour, agent_id="desk")
    [(name, params)] = [next(iter(item.items())) for item in spec["governance"]]
    assert name == "mas.gov.budget"
    assert params["max_llm_calls"] == 2
    assert union_plugin_set([["gdb@v1"], ["gdb"], ["mas.gov.gdb"]], "governance") == ["mas.gov.gdb"]


@pytest.mark.parametrize("ref", ["file:///etc/controls/x.yaml", "FILE:///x.yaml", "~/x.yaml", "/x.yaml", "C:\\x.yaml"])
def test_a_flavour_control_ref_that_is_an_absolute_path_or_file_url_is_refused(tmp_path: Path, ref: str) -> None:
    path = tmp_path / "desk.yaml"
    path.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        f"spec:\n  control:\n    - {{ref: '{ref}', agents: $all}}\n",
        encoding="utf-8",
    )
    with pytest.raises(FlavourError, match="absolute path"):
        resolve_flavour(str(path))


def test_a_path_value_inside_a_control_that_is_a_file_url_is_refused(tmp_path: Path) -> None:
    doc = _control_doc(spec={"governance": [{"gdb": {"script_file": "file:///etc/passwd"}}]})
    result = validate_data(doc, kind="control", strict=True, resolve_refs=False)
    assert not result.ok or any("absolute path" in str(e) for e in getattr(result, "errors", []))


def test_a_control_that_matches_no_agent_changes_nothing(tmp_path: Path) -> None:
    flavour = _flavour_with_control(tmp_path, {"governance": ["sample_governance"]}, agents="ghost")
    agent = {"governance": ["no_undeclared_tool"]}
    out = spec_with_control_lists(agent, flavour, agent_id="desk")
    assert out["governance"] == ["no_undeclared_tool"]
    assert spec_with_control_lists(agent, flavour, agent_id="ghost")["governance"] == ["sample_governance"]
