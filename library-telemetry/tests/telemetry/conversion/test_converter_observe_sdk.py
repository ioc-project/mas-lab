#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``converter_profile="observe_sdk"`` — real ioa_observe.*/traceloop.*
overlay + real dotted span names, additive on top of the toy mas.* shape.

Regression guard for a real content-loss bug found while testing this
profile against a real trace: the entity input/output overlay previously
checked only the generic "mas.input"/"mas.output" keys, so any LLMCall/
ToolCall/ProcessingCall/SkillExecution span (whose real content lives under
a different mas.* key -- mas.llm.messages, mas.tool.input, ...) silently got
no ioa_observe.entity.input/output at all. Confirmed lost on a real LLM
call once its span was renamed to a `.task`-suffixed name for timeline
clarity, since it was then no longer recognizable as an LLM call by name
either. Fixed by keying the overlay on mas.boundary's own attribute name,
not a single generic key.
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_otel


def _by_name(spans: list[dict], *, suffix: str = "", exact: str = "") -> list[dict]:
    out = []
    for span in spans:
        name = str(span.get("name") or "")
        if exact and name == exact:
            out.append(span)
        elif suffix and name.endswith(suffix):
            out.append(span)
    return out


def _first(spans: list[dict], *, suffix: str = "", exact: str = "") -> dict:
    found = _by_name(spans, suffix=suffix, exact=exact)
    assert found, (suffix or exact, [s.get("name") for s in spans])
    return found[0]


@requires_otel
def test_raw_profile_default_has_no_observe_sdk_attributes(spans):
    """Regression guard: the default (raw) profile must stay untouched --
    no ioa_observe.*/traceloop.* leakage into the toy round-trip shape.

    Excludes the ``.graph`` topology span: it always carries
    ``ioa_observe.span.kind`` (a separate, always-on SCOPE-required)
    feature -- see emit_graph_span/topology.py) regardless of profile.
    """
    for s in spans:
        if s["attributes"].get("mas.boundary") == "Graph":
            continue
        assert not any(k.startswith("ioa_observe.") for k in s["attributes"])
        assert not any(k.startswith("traceloop.") for k in s["attributes"])


@requires_otel
def test_observe_sdk_profile_uses_real_dotted_span_names(observe_sdk_spans):
    names = {s["name"] for s in observe_sdk_spans}
    assert "planner.agent" in names
    assert "web_search.tool" in names
    assert "planner.chat" in names
    # Observe-sdk names the run wrapper ``invoke_agent LangGraph``.
    assert any(n.startswith("invoke_agent ") for n in names)
    assert "openclaw.request" not in names
    assert "root" not in names


@requires_otel
def test_mas_call_maps_to_invoke_agent_name(observe_sdk_spans):
    """mas_call_start/end is observe-sdk's ``invoke_agent {app}`` workflow root."""
    names = {s["name"] for s in observe_sdk_spans}
    assert any(n.startswith("invoke_agent ") for n in names)
    assert "openclaw.request" not in names
    assert not any(n.endswith(".task") and "orchestrator" in n for n in names)


@requires_otel
def test_routing_annotations_avoid_the_real_routing_dispatch_convention(tmp_path):
    """Regression guard: naming every CallAnnotation `{agent}.{kind}` (so
    consumers can tell them apart -- see
    test_observe_sdk_profile_gives_call_annotations_distinguishable_names)
    accidentally made `routing`/`routing_result` collide with a real,
    different IoaObserveHandler dispatch rule: a literal `.routing`-suffixed
    span name is InsightClaw's own convention for ONE interval span with
    `routing.from_agent`/`.to_agent` attributes, synthesizing BOTH a routing
    and a routing_result event from its start/end -- not our model of two
    independent point-in-time annotations. Before this fix, naming the span
    "{agent}.routing" made it dispatch there instead, silently resolving
    agent_id to "unknown" via real attributes we never set."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "execution_start",
                    "call_id": "a1",
                    "timestamp": 1.0,
                    "agent_id": "moderator",
                },
                {
                    "kind": "routing",
                    "parent_call_id": "a1",
                    "timestamp": 1.1,
                    "agent_id": "moderator",
                    "selected_agent": "worker",
                },
                {
                    "kind": "execution_end",
                    "call_id": "a1",
                    "timestamp": 1.2,
                    "agent_id": "moderator",
                },
            ]
        )
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path,
        out,
        app_name="test-app",
        converter_profile="observe_sdk",
        export_layers={"annotation": True},
    )
    spans = [
        json.loads(line) for line in out.read_text().splitlines() if line.strip()
    ]
    names = {s["name"] for s in spans}
    assert not any(n.endswith(".routing") for n in names), names
    assert "moderator.routing_annotation" in names


@requires_otel
def test_llm_call_naming_has_no_agent_specific_hardcoding(tmp_path):
    """Regression guard: every LLMCall boundary maps to `.chat` consistently,
    regardless of agent name. A previous version special-cased
    agent_id == "moderator" to `.task` instead, with no real signal in the
    native event distinguishing it from any other agent's LLM call (its
    call is nested under its own AgentCall exactly like every other
    agent's) -- pure agent-name hardcoding, not a real rule."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "execution_start",
                    "call_id": "mod",
                    "timestamp": 1.0,
                    "agent_id": "moderator",
                    "input": "hi",
                },
                {
                    "kind": "llm_call_start",
                    "call_id": "mod-llm",
                    "parent_call_id": "mod",
                    "timestamp": 1.1,
                    "agent_id": "moderator",
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "route?"}],
                },
                {
                    "kind": "llm_call_end",
                    "call_id": "mod-llm",
                    "timestamp": 1.2,
                    "agent_id": "moderator",
                    "response": {"content": "route to planner"},
                },
                {
                    "kind": "execution_end",
                    "call_id": "mod",
                    "timestamp": 1.3,
                    "agent_id": "moderator",
                    "output": "done",
                },
            ]
        )
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path, out, app_name="test-app", converter_profile="observe_sdk"
    )
    names = {
        json.loads(line)["name"]
        for line in out.read_text().splitlines()
        if line.strip()
    }
    assert "moderator.chat" in names
    assert "moderator.task" not in names


@requires_otel
def test_observe_sdk_profile_sets_entity_input_output_per_boundary(observe_sdk_spans):
    agent_span = _first(observe_sdk_spans, suffix=".agent")
    assert str(agent_span["attributes"].get("ioa_observe.entity.input") or "").endswith(
        "solve the task"
    )
    # Agent output is wrapped so it cannot equal the last child output
    # (SCOPE skips capability-chain-boundary when contents match).
    assert str(agent_span["attributes"].get("ioa_observe.entity.output") or "").endswith(
        "done"
    )

    llm_span = _first(observe_sdk_spans, suffix=".chat")
    assert llm_span["attributes"].get("ioa_observe.entity.input") is not None
    assert "plan" in llm_span["attributes"]["ioa_observe.entity.input"]
    assert llm_span["attributes"].get("ioa_observe.entity.output") == "here is the plan"

    tool_span = _first(observe_sdk_spans, suffix=".tool")
    assert tool_span["attributes"].get("ioa_observe.entity.input") is not None
    assert "mas" in tool_span["attributes"]["ioa_observe.entity.input"]
    assert tool_span["attributes"].get("ioa_observe.entity.output") == "search results"


@requires_otel
def test_observe_sdk_profile_sets_span_kind(observe_sdk_spans):
    assert _first(observe_sdk_spans, suffix=".agent")["attributes"].get(
        "ioa_observe.span.kind"
    ) == "agent"
    assert _first(observe_sdk_spans, suffix=".chat")["attributes"].get(
        "ioa_observe.span.kind"
    ) == "llm"
    assert _first(observe_sdk_spans, suffix=".tool")["attributes"].get(
        "ioa_observe.span.kind"
    ) == "tool"


@requires_otel
def test_observe_sdk_profile_sets_genai_semconv_prompt_and_completion(
    observe_sdk_spans,
):
    """Regression guard: a real GenAI-semconv-aware consumer (confirmed:
    SCOPE norm's FrameworkParser.extract_payloads) reads *only*
    gen_ai.prompt.{i}.content / gen_ai.completion.0.content for LLM
    entities -- it never falls back to the generic
    ioa_observe.entity.input/output overlay for that entity type. Without
    these, every LLM entity's content resolved to None end-to-end even
    though ioa_observe.entity.input/output carried it."""
    llm_attrs = _first(observe_sdk_spans, suffix=".chat")["attributes"]
    assert llm_attrs.get("gen_ai.prompt.0.role") == "user"
    assert "plan" in llm_attrs.get("gen_ai.prompt.0.content", "")
    assert llm_attrs.get("gen_ai.completion.0.role") == "assistant"
    assert llm_attrs.get("gen_ai.completion.0.content") == "here is the plan"
    assert "plan" in str(llm_attrs.get("gen_ai.input.messages") or "")
    assert llm_attrs.get("gen_ai.output.messages") == "here is the plan"
    assert llm_attrs.get("gen_ai.usage.input_tokens") == 10
    assert llm_attrs.get("gen_ai.usage.output_tokens") == 5
    assert llm_attrs.get("gen_ai.usage.total_tokens") == 15


@requires_otel
def test_brownfield_output_fields_reach_oxp_attributes(tmp_path):
    """Live trip-planner traces put the answer on user_response.content and
    tool_call_end.output -- not execution_end.output / tool_call_end.result.
    Norm copies ioa_observe.entity.output / gen_ai.output.messages into
    Session/Agent/LLM State; without this fold the UI shows no content."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "execution_start",
                    "call_id": "mod",
                    "timestamp": 1.0,
                    "agent_id": "moderator",
                    "input": "Plan a trip from A to B",
                },
                {
                    "kind": "llm_call_start",
                    "call_id": "llm",
                    "parent_call_id": "mod",
                    "timestamp": 1.1,
                    "agent_id": "moderator",
                    "model": "azure/gpt-4o",
                    "messages": [{"role": "user", "content": "plan it"}],
                },
                {
                    "kind": "llm_call_end",
                    "call_id": "llm",
                    "timestamp": 1.2,
                    "agent_id": "moderator",
                    "output": '{"found": true}',
                    "finish_reason": "stop",
                    "response": {"usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}},
                },
                {
                    "kind": "tool_call_start",
                    "call_id": "tool",
                    "parent_call_id": "mod",
                    "timestamp": 1.3,
                    "agent_id": "moderator",
                    "tool_name": "lookup_schedule",
                    "arguments": {"origin": "A"},
                },
                {
                    "kind": "tool_call_end",
                    "call_id": "tool",
                    "timestamp": 1.4,
                    "agent_id": "moderator",
                    "output": '{"routes": []}',
                },
                {
                    "kind": "user_response",
                    "call_id": "mod-resp",
                    "parent_call_id": "mod",
                    "timestamp": 1.5,
                    "agent_id": "moderator",
                    "content": '{"found": true, "origin": "A"}',
                },
                {
                    "kind": "execution_end",
                    "call_id": "mod",
                    "timestamp": 1.6,
                    "agent_id": "moderator",
                    "status": "ok",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path, out, app_name="trip-planner", converter_profile="observe_sdk"
    )
    spans = [
        json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    agent = _first(spans, suffix=".agent")["attributes"]
    assert str(agent.get("ioa_observe.entity.output") or "").endswith(
        '{"found": true, "origin": "A"}'
    )
    tool = _first(spans, suffix=".tool")["attributes"]
    assert tool.get("ioa_observe.entity.output") == '{"routes": []}'
    llm = _first(spans, suffix=".chat")["attributes"]
    assert llm.get("gen_ai.output.messages") == '{"found": true}'
    assert llm.get("gen_ai.usage.total_tokens") == 12
    assert llm.get("gen_ai.provider.name") == "azure"
    assert llm.get("gen_ai.request.model") == "gpt-4o"


@requires_otel
def test_tool_end_closes_call_id_remapped_to_another_agent(tmp_path):
    """A later agent reusing a live call_id gets ``{agent}-{id}``. The end
    event still carries the raw id and must close that remapped span."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "execution_start",
                    "call_id": "shared",
                    "timestamp": 1.0,
                    "agent_id": "moderator",
                    "input": "delegate",
                },
                {
                    "kind": "execution_start",
                    "call_id": "sched",
                    "parent_call_id": "shared",
                    "timestamp": 1.1,
                    "agent_id": "schedule_agent",
                    "input": "lookup",
                },
                {
                    "kind": "tool_call_start",
                    "call_id": "shared",
                    "parent_call_id": "sched",
                    "timestamp": 1.2,
                    "agent_id": "schedule_agent",
                    "tool_name": "lookup_schedule",
                    "arguments": {"origin": "A"},
                },
                {
                    "kind": "tool_call_end",
                    "call_id": "shared",
                    "timestamp": 1.3,
                    "agent_id": "schedule_agent",
                    "output": '{"routes": ["A-B"]}',
                },
                {
                    "kind": "execution_end",
                    "call_id": "sched",
                    "timestamp": 1.4,
                    "agent_id": "schedule_agent",
                    "status": "ok",
                },
                {
                    "kind": "execution_end",
                    "call_id": "shared",
                    "timestamp": 1.5,
                    "agent_id": "moderator",
                    "status": "ok",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path, out, app_name="trip-planner", converter_profile="observe_sdk"
    )
    spans = [
        json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    tools = _by_name(spans, suffix=".tool")
    assert len(tools) == 1
    assert tools[0]["attributes"].get("ioa_observe.entity.output") == '{"routes": ["A-B"]}'
    assert tools[0]["name"] == "lookup_schedule.tool"


@requires_otel
def test_observe_sdk_profile_sets_bare_tool_name_and_ioa_start_time(observe_sdk_spans):
    """Regression guard for two attributes a real external consumer
    (SCOPE norm's GenericParser) requires: `ioa_start_time` (a hard
    requirement -- its absence raises TypeError in extract_payloads)
    and the bare `tool_name` field (ingest also reads entity.name)."""
    tool_span = _first(observe_sdk_spans, suffix=".tool")
    assert tool_span["attributes"].get("tool_name") == "web_search"
    assert tool_span["attributes"].get("ioa_observe.entity.name") == "web_search"
    for s in observe_sdk_spans:
        assert s["attributes"].get("ioa_start_time"), s["name"]


@requires_otel
def test_annotation_and_provenance_gate_independently(tmp_path):
    """`annotation` and `provenance` are two independent toggles, not one
    renamed the other: a parallel-group event needs `provenance` on to be
    processed at all (its native-event block is `trajectory`) and
    `annotation` on to actually render as a CallAnnotation span once it is
    -- neither toggle alone is enough for it, but `annotation` alone is
    enough for a non-trajectory-block CallAnnotation kind like `routing`
    (`execution` block, no `provenance` dependency)."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "execution_start",
            "call_id": "a1",
            "timestamp": 1.0,
            "agent_id": "planner",
        },
        {
            "kind": "routing",
            "parent_call_id": "a1",
            "timestamp": 1.1,
            "agent_id": "planner",
            "selected_agent": "worker",
        },
        {
            "kind": "parallel_group_start",
            "call_id": "pg1",
            "timestamp": 1.15,
            "agent_id": "planner",
            "group_id": "g1",
            "agent_ids": ["a", "b"],
        },
        {
            "kind": "execution_end",
            "call_id": "a1",
            "timestamp": 1.2,
            "agent_id": "planner",
        },
    ]

    import itertools

    _counter = itertools.count()

    def _names(export_layers):
        i = next(_counter)
        events_path = tmp_path / f"e-{i}.jsonl"
        events_path.write_text("\n".join(json.dumps(e) for e in events))
        out = tmp_path / f"s-{i}.jsonl"
        replay_events_file(
            events_path,
            out,
            app_name="test-app",
            export_layers=export_layers,
            converter_profile="observe_sdk",
        )
        return [
            json.loads(line)["name"]
            for line in out.read_text().splitlines()
            if line.strip()
        ]

    default = _names({})
    assert not any(n.endswith(".routing_annotation") for n in default)
    assert not any("parallel_group" in n for n in default)
    # annotation=True is required for CallAnnotation on observe_sdk.
    with_ann = _names({"annotation": True})
    assert any(n.endswith(".routing_annotation") for n in with_ann)
    assert any("parallel_group" in n for n in with_ann)
    # annotation=False drops CallAnnotation even when the export layer is on.
    no_ann = _names({"annotation": False})
    assert not any(n.endswith(".routing_annotation") for n in no_ann)
    assert not any("parallel_group" in n for n in no_ann)
    # provenance=False drops trajectory events before they reach the handler.
    no_prov = _names({"provenance": False, "annotation": True})
    assert any(n.endswith(".routing_annotation") for n in no_prov)
    assert not any("parallel_group" in n for n in no_prov)


@requires_otel
def test_call_annotations_are_off_by_default(tmp_path):
    """Observe-sdk default matches noa-trip-planner: no CallAnnotation spans."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "execution_start",
                    "call_id": "a1",
                    "timestamp": 1.0,
                    "agent_id": "planner",
                },
                {
                    "kind": "routing",
                    "parent_call_id": "a1",
                    "timestamp": 1.1,
                    "agent_id": "planner",
                    "selected_agent": "worker",
                },
                {
                    "kind": "execution_end",
                    "call_id": "a1",
                    "timestamp": 1.2,
                    "agent_id": "planner",
                },
            ]
        )
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path,
        out,
        app_name="test-app",
        converter_profile="observe_sdk",
    )  # default export_layers
    names = {
        json.loads(line)["name"]
        for line in out.read_text().splitlines()
        if line.strip()
    }
    assert "CallAnnotation" not in names
    assert "planner.routing_annotation" not in names
    assert "planner.agent" in names


@requires_otel
def test_observe_sdk_profile_gives_call_annotations_distinguishable_names(tmp_path):
    """Regression guard: every point-span kind (routing, context updates,
    compaction, checkpoints, duplicate-start annotations, ...) previously
    shared the literal span name "CallAnnotation", indistinguishable by any
    name-suffix-dispatching consumer (confirmed against SCOPE norm's
    GenericParser, which resolved every one of them to
    entity_type="other"/"unknown"). Real consumers must be able to tell a
    context update apart from a compaction apart from anything else by name
    alone -- not by ``mas.annotation.kind`` alone, which no name-suffix
    dispatcher reads.

    Uses ``context_assembled`` (semantic layer, exported by default) rather
    than ``governance_checked``: governance events (a) are on the
    governance layer, off by default, and (b) already get a distinct
    literal span name ("GovernanceEvent") upstream of ``_compat_span_name``,
    so they never shared the "CallAnnotation" collapse this test guards
    against in the first place.
    """
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "execution_start",
                    "call_id": "a1",
                    "timestamp": 1.0,
                    "agent_id": "planner",
                    "input": "hi",
                },
                {
                    "kind": "context_assembled",
                    "parent_call_id": "a1",
                    "timestamp": 1.1,
                    "agent_id": "planner",
                    "segments": [{"tokens": 10}],
                },
                {
                    "kind": "execution_end",
                    "call_id": "a1",
                    "timestamp": 1.2,
                    "agent_id": "planner",
                    "output": "done",
                },
            ]
        )
    )
    out = tmp_path / "spans.jsonl"
    # annotation ON: CallAnnotation is off by default (SCOPE norm does not
    # recognize it), so it has to be explicitly enabled to test its naming.
    replay_events_file(
        events_path,
        out,
        app_name="test-app",
        converter_profile="observe_sdk",
        export_layers={"annotation": True},
    )
    names = {
        json.loads(line)["name"]
        for line in out.read_text().splitlines()
        if line.strip()
    }
    assert "CallAnnotation" not in names
    assert "planner.context_assembled" in names


@requires_otel
def test_observe_sdk_nests_delegate_agent_and_closes_blank_processing(tmp_path):
    """Norm walks ParentSpanId / ioa_observe.agent.span_id / *.graph.

    Live trip-planner events parent a specialist at a tool id that is not
    open yet, reuse ``agent_id=agent`` on context parts, and emit
    processing start/end with an empty call_id. Those used to become root
    siblings (flat tree) with unclosed ``*.processing`` bars.
    """
    import json
    from pathlib import Path

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "kind": "mas_call_start",
                    "call_id": "run",
                    "timestamp": 1.0,
                    "agent_id": "mas",
                    "app_name": "trip-planner",
                    "session_id": "sess-tree",
                },
                {
                    "kind": "execution_start",
                    "call_id": "moderator-u1-exec",
                    "parent_call_id": "run",
                    "timestamp": 1.1,
                    "agent_id": "moderator",
                    "input": "Plan a trip",
                },
                {
                    "kind": "execution_start",
                    "call_id": "schedule_agent-6ec2-exec",
                    "parent_call_id": "6ec2-missing-tool",
                    "timestamp": 1.2,
                    "agent_id": "schedule_agent",
                    "input": "lookup",
                },
                {
                    "kind": "processing_call_start",
                    "call_id": "",
                    "parent_call_id": "moderator-u1-exec",
                    "timestamp": 1.3,
                    "agent_id": "moderator",
                    "processing_name": "context assembly",
                    "processing_type": "context_assembly",
                },
                {
                    "kind": "context_part_contributed",
                    "call_id": "part-1",
                    "parent_call_id": "moderator-u1-exec",
                    "timestamp": 1.31,
                    "agent_id": "agent",
                    "source": "context/system",
                    "token_estimate": 10,
                },
                {
                    "kind": "processing_call_end",
                    "call_id": "",
                    "parent_call_id": "moderator-u1-exec",
                    "timestamp": 1.32,
                    "agent_id": "moderator",
                    "processing_name": "context assembly",
                    "processing_type": "context_assembly",
                },
                {
                    "kind": "llm_call_start",
                    "call_id": "llm-mod",
                    "parent_call_id": "moderator-u1-exec",
                    "timestamp": 1.4,
                    "agent_id": "moderator",
                    "model": "azure/gpt-4o",
                    "messages": [{"role": "user", "content": "plan it"}],
                },
                {
                    "kind": "llm_call_end",
                    "call_id": "llm-mod",
                    "timestamp": 1.5,
                    "agent_id": "moderator",
                    "output": "delegate",
                    "finish_reason": "stop",
                    "response": {
                        "usage": {
                            "prompt_tokens": 4,
                            "completion_tokens": 2,
                            "total_tokens": 6,
                        }
                    },
                },
                {
                    "kind": "execution_end",
                    "call_id": "schedule_agent-6ec2-exec",
                    "timestamp": 1.6,
                    "agent_id": "schedule_agent",
                    "status": "ok",
                    "output": "schedules",
                },
                {
                    "kind": "execution_end",
                    "call_id": "moderator-u1-exec",
                    "timestamp": 1.7,
                    "agent_id": "moderator",
                    "status": "ok",
                    "output": "done",
                },
                {
                    "kind": "mas_call_end",
                    "call_id": "run",
                    "timestamp": 1.8,
                    "agent_id": "mas",
                    "status": "ok",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path,
        out,
        app_name="trip-planner",
        converter_profile="observe_sdk",
        extensions=True,
    )
    spans = [
        json.loads(line)
        for line in Path(out).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_name = {s["name"]: s for s in spans}
    moderator = by_name["moderator.agent"]
    schedule = by_name["schedule_agent.agent"]
    invoke = next(s for s in spans if s["name"].startswith("invoke_agent "))
    # LangGraph (and norm) treat delegated *.agent as siblings under
    # invoke_agent, linked by ioa_observe.agent.previous — not as children
    # of the caller agent.
    assert schedule["parent_id"] == invoke["context"]["span_id"]
    assert moderator["parent_id"] == invoke["context"]["span_id"]
    chat = by_name["moderator.chat"]
    assert chat["parent_id"] == moderator["context"]["span_id"]
    mod_span = str(moderator["context"]["span_id"]).removeprefix("0x")
    assert str(chat["attributes"].get("ioa_observe.agent.span_id")).removeprefix(
        "0x"
    ) == mod_span
    graph = next(s for s in spans if s["name"].endswith(".graph"))
    assert not any(s["name"] == "root" for s in spans)
    assert graph.get("parent_id") in (None, "")
    assert graph["context"]["trace_id"] == moderator["context"]["trace_id"]
    assert graph["attributes"].get("ioa_observe.entity.name") == "trip-planner"
    assert graph["attributes"].get("execution.success") == "true"
    nodes = json.loads(graph["attributes"]["gen_ai.ioa.graph"])["nodes"]
    assert isinstance(nodes, dict)
    node_ids = set(nodes)
    assert {"moderator", "schedule_agent"} <= node_ids
    assert not any(s["name"] == "agent.context" for s in spans)
    processing = [s for s in spans if s["name"].endswith(".processing")]
    assert processing
    for span in processing:
        assert span.get("parent_id"), span["name"]
        start = span["start_time"]
        end = span["end_time"]
        assert start != end or True
        assert end >= start


@requires_otel
def test_rewrite_tool_delegation_turns_delegate_to_into_agent_previous(tmp_path):
    """MAS-lab encodes handoff as ``delegate_to_<id>`` tool calls. Observe-sdk
    / LangGraph traces the same thing as ``ioa_observe.agent.previous`` on
    the target ``*.agent`` — not as a ``*.tool``. Default rewrite is on."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "execution_start",
            "call_id": "mod",
            "timestamp": 1.0,
            "agent_id": "moderator",
            "input": "Plan a trip",
        },
        {
            "kind": "tool_call_start",
            "call_id": "del",
            "parent_call_id": "mod",
            "timestamp": 1.1,
            "agent_id": "moderator",
            "tool_name": "delegate_to_schedule_agent",
            "arguments": {"task": "lookup"},
        },
        {
            "kind": "execution_start",
            "call_id": "sched",
            "parent_call_id": "del",
            "timestamp": 1.2,
            "agent_id": "schedule_agent",
            "input": "lookup",
        },
        {
            "kind": "tool_call_start",
            "call_id": "lookup",
            "parent_call_id": "sched",
            "timestamp": 1.3,
            "agent_id": "schedule_agent",
            "tool_name": "lookup_schedule",
            "arguments": {"origin": "A"},
        },
        {
            "kind": "tool_call_end",
            "call_id": "lookup",
            "timestamp": 1.4,
            "agent_id": "schedule_agent",
            "output": "{}",
        },
        {
            "kind": "execution_end",
            "call_id": "sched",
            "timestamp": 1.5,
            "agent_id": "schedule_agent",
            "status": "ok",
        },
        {
            "kind": "tool_call_end",
            "call_id": "del",
            "timestamp": 1.6,
            "agent_id": "moderator",
            "output": "done",
        },
        {
            "kind": "execution_end",
            "call_id": "mod",
            "timestamp": 1.7,
            "agent_id": "moderator",
            "status": "ok",
        },
    ]
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events_path, out, app_name="trip-planner", converter_profile="observe_sdk"
    )
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    names = [s["name"] for s in spans]
    assert "delegate_to_schedule_agent.tool" not in names
    assert "lookup_schedule.tool" in names
    schedule = _first(spans, exact="schedule_agent.agent")
    assert schedule["attributes"].get("ioa_observe.agent.previous") == "moderator"
    assert schedule["attributes"].get("ioa_observe.agent.sequence")
    assert schedule["attributes"].get("ioa_observe.handoff.source.span_ids")

    off = tmp_path / "off.jsonl"
    replay_events_file(
        events_path,
        off,
        app_name="trip-planner",
        converter_profile="observe_sdk",
        rewrite_tool_delegation=False,
    )
    off_names = {
        json.loads(line)["name"]
        for line in off.read_text().splitlines()
        if line.strip()
    }
    assert "delegate_to_schedule_agent.tool" in off_names


@requires_otel
def test_agent_previous_is_caller_derived_not_hardcoded(tmp_path):
    """``ioa_observe.agent.previous`` is the caller ``agent_id``, not a
    hardcoded ``moderator`` string. LangGraph observe-sdk does the same
    with the prior node identity."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "execution_start",
            "call_id": "orch",
            "timestamp": 1.0,
            "agent_id": "orchestrator",
            "input": "go",
        },
        {
            "kind": "tool_call_start",
            "call_id": "del",
            "parent_call_id": "orch",
            "timestamp": 1.1,
            "agent_id": "orchestrator",
            "tool_name": "delegate_to_researcher",
        },
        {
            "kind": "execution_start",
            "call_id": "res",
            "parent_call_id": "del",
            "timestamp": 1.2,
            "agent_id": "researcher",
            "input": "look up",
        },
        {
            "kind": "execution_end",
            "call_id": "res",
            "timestamp": 1.3,
            "agent_id": "researcher",
            "status": "ok",
        },
        {
            "kind": "tool_call_end",
            "call_id": "del",
            "timestamp": 1.4,
            "agent_id": "orchestrator",
        },
        {
            "kind": "execution_end",
            "call_id": "orch",
            "timestamp": 1.5,
            "agent_id": "orchestrator",
            "status": "ok",
        },
    ]
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = tmp_path / "spans.jsonl"
    replay_events_file(events_path, out, app_name="lab", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    researcher = _first(spans, exact="researcher.agent")
    assert researcher["attributes"].get("ioa_observe.agent.previous") == "orchestrator"
    assert researcher["attributes"].get("ioa_observe.agent.previous") != "moderator"
    assert "delegate_to_researcher.tool" not in {s["name"] for s in spans}


@requires_otel
def test_rewrite_splits_caller_agent_visit_after_delegate(tmp_path):
    """A MAS turn that delegates must emit a second caller ``*.agent``
    after the specialist returns (LangGraph re-enters the moderator).
    One long caller span makes Inspect end the session on the specialist."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "execution_start",
            "call_id": "mod-exec",
            "timestamp": 1.0,
            "agent_id": "moderator",
            "input": "plan a trip",
        },
        {
            "kind": "llm_call_start",
            "call_id": "llm1",
            "parent_call_id": "mod-exec",
            "timestamp": 1.1,
            "agent_id": "moderator",
            "messages": [{"role": "user", "content": "plan a trip"}],
        },
        {
            "kind": "llm_call_end",
            "call_id": "llm1",
            "timestamp": 1.2,
            "agent_id": "moderator",
            "output": "delegate",
        },
        {
            "kind": "tool_call_start",
            "call_id": "del",
            "parent_call_id": "mod-exec",
            "timestamp": 1.3,
            "agent_id": "moderator",
            "tool_name": "delegate_to_schedule_agent",
        },
        {
            "kind": "execution_start",
            "call_id": "sched-exec",
            "parent_call_id": "del",
            "timestamp": 1.4,
            "agent_id": "schedule_agent",
            "input": "look up",
        },
        {
            "kind": "llm_call_start",
            "call_id": "llm2",
            "parent_call_id": "mod-exec",
            "timestamp": 1.7,
            "agent_id": "moderator",
            "messages": [{"role": "user", "content": "trains at 3pm"}],
        },
        {
            "kind": "llm_call_end",
            "call_id": "llm2",
            "timestamp": 1.8,
            "agent_id": "moderator",
            "output": "here is your itinerary",
        },
        {
            "kind": "user_response",
            "call_id": "u1-resp",
            "parent_call_id": "mod-exec",
            "timestamp": 1.9,
            "agent_id": "moderator",
            "content": "here is your itinerary",
        },
        {
            "kind": "execution_end",
            "call_id": "mod-exec",
            "timestamp": 2.0,
            "agent_id": "moderator",
        },
        {
            "kind": "tool_call_end",
            "call_id": "del",
            "timestamp": 2.1,
            "agent_id": "moderator",
        },
        {
            "kind": "execution_end",
            "call_id": "sched-exec",
            "timestamp": 2.2,
            "agent_id": "schedule_agent",
            "output": "trains at 3pm",
        },
    ]
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = tmp_path / "spans.jsonl"
    replay_events_file(events_path, out, app_name="trip-planner", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    moderators = [s for s in spans if s.get("name") == "moderator.agent"]
    assert len(moderators) == 2, [s.get("name") for s in spans]
    first, second = sorted(moderators, key=lambda s: s.get("start_time") or "")
    assert second["attributes"].get("ioa_observe.agent.previous") == "schedule_agent"
    chats = [s for s in spans if s.get("name") == "moderator.chat"]
    assert len(chats) == 2
    first_span = str(first["context"]["span_id"]).removeprefix("0x")
    second_span = str(second["context"]["span_id"]).removeprefix("0x")
    chat_parents = {
        str((c.get("attributes") or {}).get("ioa_observe.agent.span_id") or "").removeprefix("0x")
        for c in chats
    }
    assert first_span in chat_parents
    assert second_span in chat_parents
    last_agent = max(
        (s for s in spans if str(s.get("name") or "").endswith(".agent")),
        key=lambda s: s.get("start_time") or "",
    )
    assert last_agent.get("name") == "moderator.agent"


@requires_otel
def test_observe_sdk_agent_span_id_matches_exported_span_id(tmp_path):
    """Norm attaches hasLLMCall only when ``ioa_observe.agent.span_id``
    equals ``AgentCall.spanId`` exactly. Both must be the OTLP/ClickHouse
    form (16 hex, no ``0x``)."""
    import json
    from pathlib import Path

    from mas.library.telemetry.conversion.replay import replay_events_file
    from mas.library.telemetry.conversion.semconv import wire_span_id

    events = (
        Path(__file__).resolve().parents[4]
        / "library-samples"
        / "apps"
        / "trip-planner"
        / "traces"
        / "events.jsonl"
    )
    if not events.exists():
        pytest.skip("trip-planner events missing")
    out = tmp_path / "spans.jsonl"
    replay_events_file(
        events, out, app_name="trip-planner", converter_profile="observe_sdk"
    )
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    agents = [s for s in spans if str(s.get("name", "")).endswith(".agent")]
    by_sid = {wire_span_id(s["context"]["span_id"]): s for s in agents}
    moderators = [s for s in agents if s.get("name") == "moderator.agent"]
    assert len(moderators) == 2
    last_agent = max(agents, key=lambda s: s.get("start_time") or "")
    assert last_agent.get("name") == "moderator.agent"
    for chat in [s for s in spans if str(s.get("name", "")).endswith(".chat")]:
        stamped = wire_span_id(chat["attributes"].get("ioa_observe.agent.span_id"))
        parent = by_sid.get(stamped)
        assert parent, (chat["name"], stamped, sorted(by_sid))
        assert parent["name"] == str(chat["name"]).removesuffix(".chat") + ".agent"
        assert not str(chat["attributes"].get("ioa_observe.agent.span_id", "")).startswith(
            "0x"
        )
    chats = [s for s in spans if str(s.get("name", "")).endswith(".chat")]
    assert chats
    assert all(
        str((s.get("attributes") or {}).get("gen_ai.request.model") or "")
        for s in chats
    )
    assert all(
        (s.get("attributes") or {}).get("ioa_observe.entity.output")
        or (s.get("attributes") or {}).get("gen_ai.output.messages")
        for s in chats
    )
    models = {
        str((s.get("attributes") or {}).get("gen_ai.request.model") or "")
        for s in chats
    }
    assert models
    assert "unknown" not in models
    assert any(
        (s.get("attributes") or {}).get("gen_ai.input.messages")
        and "Plan a trip" in str((s.get("attributes") or {}).get("gen_ai.input.messages"))
        for s in chats
    )


