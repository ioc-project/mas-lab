# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Infer generic agents using the incoming event's native run."""

import json

import pytest

from tests.conftest import requires_otel


def event(kind, agent, call, run=None, *, nested=False, **fields):
    row = {"kind": kind, "agent_id": agent, "call_id": call, "timestamp": 1.0, **fields}
    if run is not None:
        row.update({"context": {"run_id": run}} if nested else {"run_id": run})
    return row


def assert_contributions(path, expected):
    spans = [json.loads(line) for line in path.read_text().splitlines()]
    parents = {
        row["attributes"]["mas.agent.id"]: row["context"]["span_id"]
        for row in spans
        if row["attributes"].get("mas.boundary") == "AgentCall"
    }
    parts = [row for row in spans if row["attributes"].get("mas.boundary") == "ContextContribution"]
    assert len(parts) == len(expected)
    for row, agent in zip(parts, expected, strict=True):
        attrs = row["attributes"]
        assert attrs["mas.agent.id"] == agent
        assert row["parent_id"] == parents[agent]
        assert attrs["mas.context.part_id"] == "part-" + agent
        assert attrs["mas.context.source"] == "context/" + agent
        assert attrs["mas.context.token_estimate"] == 7


@requires_otel
@pytest.mark.parametrize("profile", ["raw", "observe_sdk"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("explicit_parent", [False, True])
@pytest.mark.parametrize("replay", [False, True])
def test_incoming_run_owns_generic_agent_contribution(tmp_path, profile, nested, explicit_parent, replay):
    from mas.library.telemetry.conversion import replay_events_file
    from mas.library.telemetry.conversion.session import create_otel_export

    events = [
        event("execution_start", agent, agent + "-exec", run, nested=nested)
        for run, agent in (("A", "Alpha"), ("B", "Beta"))
    ]
    for run, agent in (("A", "Alpha"), ("B", "Beta")):
        events.append(
            event(
                "context_part_contributed",
                "agent",
                "part-" + agent,
                run,
                nested=nested,
                parent_call_id=agent + "-exec" if explicit_parent else None,
                source="context/" + agent,
                token_estimate=7,
            )
        )
    output = tmp_path / "spans.jsonl"
    if replay:
        source = tmp_path / "events.jsonl"
        source.write_text("".join(json.dumps(row) + "\n" for row in events))
        replay_events_file(source, output, app_name="example", converter_profile=profile, extensions=True)
    else:
        export = create_otel_export(spans_path=output, app_name="example", converter_profile=profile, extensions=True)
        for row in events:
            export.process_event(row)
        export.close()
    assert_contributions(output, ["Alpha", "Beta"])


@requires_otel
@pytest.mark.parametrize("profile", ["raw", "observe_sdk"])
@pytest.mark.parametrize("run", [None, "", "local", "unknown", "A"])
def test_run_precedence_and_missing_or_sentinel_fallback(tmp_path, profile, run):
    from mas.library.telemetry.conversion.session import create_otel_export

    output = tmp_path / "spans.jsonl"
    export = create_otel_export(spans_path=output, app_name="example", converter_profile=profile, extensions=True)
    for native_run, agent in (("B", "Beta"), ("A", "Alpha")):
        export.process_event(event("execution_start", agent, agent + "-exec", native_run))
    contribution = event(
        "context_part_contributed",
        "agent",
        "part-Alpha",
        run,
        parent_call_id="Alpha-exec",
        source="context/Alpha",
        token_estimate=7,
    )
    if run:
        # A present top-level value wins over context.run_id, even a sentinel.
        contribution["context"] = {"run_id": "B"}
    export.process_event(contribution)
    export.close()
    assert_contributions(output, ["Alpha"])
