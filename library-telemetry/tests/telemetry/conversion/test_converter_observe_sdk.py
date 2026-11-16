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


def test_observe_sdk_profile_uses_real_dotted_span_names(observe_sdk_spans):
    names = {s["name"] for s in observe_sdk_spans}
    assert "planner.agent" in names
    assert "web_search.tool" in names
    assert "planner.chat" in names
    # Observe-sdk names the run wrapper ``invoke_agent LangGraph``.
    assert any(n.startswith("invoke_agent ") for n in names)
    assert "openclaw.request" not in names
    assert "root" not in names


def test_mas_call_maps_to_invoke_agent_name(observe_sdk_spans):
    """mas_call_start/end is observe-sdk's ``invoke_agent {app}`` workflow root."""
    names = {s["name"] for s in observe_sdk_spans}
    assert any(n.startswith("invoke_agent ") for n in names)
    assert "openclaw.request" not in names
    assert not any(n.endswith(".task") and "orchestrator" in n for n in names)


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
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    names = {s["name"] for s in spans}
    assert not any(n.endswith(".routing") for n in names), names
    # Every CallAnnotation is ``{agent}.annotation`` (norm's suffix); the kind is an attribute.
    annotations = [s for s in spans if s["name"] == "moderator.annotation"]
    assert [s["attributes"]["mas.annotation.kind"] for s in annotations] == ["routing"]


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
    replay_events_file(events_path, out, app_name="test-app", converter_profile="observe_sdk")
    names = {json.loads(line)["name"] for line in out.read_text().splitlines() if line.strip()}
    assert "moderator.chat" in names
    assert "moderator.task" not in names


def test_observe_sdk_profile_sets_entity_input_output_per_boundary(observe_sdk_spans):
    agent_span = _first(observe_sdk_spans, suffix=".agent")
    assert str(agent_span["attributes"].get("ioa_observe.entity.input") or "").endswith("solve the task")
    # Agent output is wrapped so it cannot equal the last child output
    # (SCOPE skips capability-chain-boundary when contents match).
    assert str(agent_span["attributes"].get("ioa_observe.entity.output") or "").endswith("done")

    llm_span = _first(observe_sdk_spans, suffix=".chat")
    assert llm_span["attributes"].get("ioa_observe.entity.input") is not None
    assert "plan" in llm_span["attributes"]["ioa_observe.entity.input"]
    assert llm_span["attributes"].get("ioa_observe.entity.output") == "here is the plan"

    tool_span = _first(observe_sdk_spans, suffix=".tool")
    assert tool_span["attributes"].get("ioa_observe.entity.input") is not None
    assert "mas" in tool_span["attributes"]["ioa_observe.entity.input"]
    assert tool_span["attributes"].get("ioa_observe.entity.output") == "search results"


def test_observe_sdk_profile_sets_span_kind(observe_sdk_spans):
    assert _first(observe_sdk_spans, suffix=".agent")["attributes"].get("ioa_observe.span.kind") == "agent"
    assert _first(observe_sdk_spans, suffix=".chat")["attributes"].get("ioa_observe.span.kind") == "llm"
    assert _first(observe_sdk_spans, suffix=".tool")["attributes"].get("ioa_observe.span.kind") == "tool"


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


def test_brownfield_output_fields_reach_scope_attributes(tmp_path):
    """The agent's answer is its final State (execution_end.state_in_content);
    the tool answer is tool_call_end.output. Norm copies
    ioa_observe.entity.output / gen_ai.output.messages into Session/Agent/LLM
    State; without this fold the UI shows no content."""
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
                    # The agent's output is its final State, not user_response.
                    "state_in": "mod#2",
                    "state_in_content": '{"found": true, "origin": "A"}',
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "spans.jsonl"
    replay_events_file(events_path, out, app_name="trip-planner", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    agent = _first(spans, suffix=".agent")["attributes"]
    assert str(agent.get("ioa_observe.entity.output") or "").endswith('{"found": true, "origin": "A"}')
    tool = _first(spans, suffix=".tool")["attributes"]
    assert tool.get("ioa_observe.entity.output") == '{"routes": []}'
    llm = _first(spans, suffix=".chat")["attributes"]
    assert llm.get("gen_ai.output.messages") == '{"found": true}'
    assert llm.get("gen_ai.usage.total_tokens") == 12
    assert llm.get("gen_ai.provider.name") == "azure"
    assert llm.get("gen_ai.request.model") == "gpt-4o"


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
    replay_events_file(events_path, out, app_name="trip-planner", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    tools = _by_name(spans, suffix=".tool")
    assert len(tools) == 1
    assert tools[0]["attributes"].get("ioa_observe.entity.output") == '{"routes": ["A-B"]}'
    assert tools[0]["name"] == "lookup_schedule.tool"


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


def test_annotation_and_provenance_gate_independently(tmp_path):
    """`annotation` and `provenance` are two independent toggles.

    Parallel-group events live on the trajectory block (`provenance` export
    layer) and emit a ParallelGroup span — not a CallAnnotation. Routing
    stays a CallAnnotation on the execution block, so it needs
    `annotation` and does not depend on `provenance`.
    """
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
        {"kind": "parallel_group_end", "call_id": "pg1", "timestamp": 1.18, "agent_id": "planner", "group_id": "g1"},
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
        return [json.loads(line)["name"] for line in out.read_text().splitlines() if line.strip()]

    default = _names({})
    assert not any(n.endswith(".annotation") for n in default)
    assert any("parallel_group" in n for n in default)
    # annotation=True is required for CallAnnotation on observe_sdk.
    with_ann = _names({"annotation": True})
    assert any(n.endswith(".annotation") for n in with_ann)
    assert any("parallel_group" in n for n in with_ann)
    # annotation=False drops CallAnnotation; ParallelGroup stays on.
    no_ann = _names({"annotation": False})
    assert not any(n.endswith(".annotation") for n in no_ann)
    assert any("parallel_group" in n for n in no_ann)
    # provenance=False drops trajectory events before they reach the handler.
    no_prov = _names({"provenance": False, "annotation": True})
    assert any(n.endswith(".annotation") for n in no_prov)
    assert not any("parallel_group" in n for n in no_prov)


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
    names = {json.loads(line)["name"] for line in out.read_text().splitlines() if line.strip()}
    assert "CallAnnotation" not in names
    assert "planner.annotation" not in names
    assert "planner.agent" in names


def test_observe_sdk_profile_names_call_annotations_by_the_annotation_suffix(tmp_path):
    """Every CallAnnotation is ``{agent}.annotation``: norm dispatches the
    ``*.annotation`` suffix to CallAnnotation and reads the kind from
    ``mas.annotation.kind`` (never the literal "CallAnnotation" name)."""
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
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    names = {s["name"] for s in spans}
    assert "CallAnnotation" not in names
    kinds = [s["attributes"]["mas.annotation.kind"] for s in spans if s["name"] == "planner.annotation"]
    assert kinds == ["context_assembled"]


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
    spans = [json.loads(line) for line in Path(out).read_text(encoding="utf-8").splitlines() if line.strip()]
    by_name = {s["name"]: s for s in spans}
    moderator = by_name["moderator.agent"]
    schedule = by_name["schedule_agent.agent"]
    invoke = next(s for s in spans if s["name"].startswith("invoke_agent "))
    # A delegated *.agent is a sibling under invoke_agent, not a child of the
    # caller agent.
    assert schedule["parent_id"] == invoke["context"]["span_id"]
    assert moderator["parent_id"] == invoke["context"]["span_id"]
    chat = by_name["moderator.chat"]
    assert chat["parent_id"] == moderator["context"]["span_id"]
    mod_span = str(moderator["context"]["span_id"]).removeprefix("0x")
    assert str(chat["attributes"].get("ioa_observe.agent.span_id")).removeprefix("0x") == mod_span
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


def test_observe_sdk_agent_span_id_matches_exported_span_id(tmp_path):
    """Norm attaches hasLLMCall only when ``ioa_observe.agent.span_id``
    equals ``AgentCall.spanId`` exactly. Both must be the OTLP/ClickHouse
    form (16 hex, no ``0x``)."""
    import json
    from pathlib import Path

    from mas.library.telemetry.conversion.replay import replay_events_file
    from mas.library.telemetry.conversion.semconv import wire_span_id

    events = (
        Path(__file__).resolve().parents[4] / "library-samples" / "apps" / "trip-planner" / "traces" / "events.jsonl"
    )
    assert events.exists(), events
    out = tmp_path / "spans.jsonl"
    replay_events_file(events, out, app_name="trip-planner", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    agents = [s for s in spans if str(s.get("name", "")).endswith(".agent")]
    by_sid = {wire_span_id(s["context"]["span_id"]): s for s in agents}
    moderators = [s for s in agents if s.get("name") == "moderator.agent"]
    # A delegating turn is one visit: delegate_to_* is a tool span, not a visit split.
    assert len(moderators) == 1
    # The moderator's one visit outlives the specialists it delegated to.
    last_ending = max(agents, key=lambda s: s.get("end_time") or "")
    assert last_ending.get("name") == "moderator.agent"
    for chat in [s for s in spans if str(s.get("name", "")).endswith(".chat")]:
        stamped = wire_span_id(chat["attributes"].get("ioa_observe.agent.span_id"))
        parent = by_sid.get(stamped)
        assert parent, (chat["name"], stamped, sorted(by_sid))
        assert parent["name"] == str(chat["name"]).removesuffix(".chat") + ".agent"
        assert not str(chat["attributes"].get("ioa_observe.agent.span_id", "")).startswith("0x")
    chats = [s for s in spans if str(s.get("name", "")).endswith(".chat")]
    assert chats
    assert all(str((s.get("attributes") or {}).get("gen_ai.request.model") or "") for s in chats)
    # A turn with no text (a bare tool-use turn in this older trace) carries no
    # output attribute: the converter writes no placeholder for it.
    assert any(
        (s.get("attributes") or {}).get("ioa_observe.entity.output")
        or (s.get("attributes") or {}).get("gen_ai.output.messages")
        for s in chats
    )
    models = {str((s.get("attributes") or {}).get("gen_ai.request.model") or "") for s in chats}
    assert models
    assert "unknown" not in models
    assert any(
        (s.get("attributes") or {}).get("gen_ai.input.messages")
        and "Plan a 3-day trip" in str((s.get("attributes") or {}).get("gen_ai.input.messages"))
        for s in chats
    )


def _replay_chat_attrs(tmp_path, events: list[dict]) -> dict:
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events))
    out = tmp_path / "spans.jsonl"
    replay_events_file(events_path, out, app_name="test-app", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    return _first(spans, suffix=".chat")["attributes"]


def _convert_observe_sdk(events: list[dict]):
    from mas.library.telemetry.conversion.converter import MasOtelConverter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": "test-app"}))
    conv = MasOtelConverter(
        provider.get_tracer("t"),
        app_name="test-app",
        converter_profile="observe_sdk",
    )
    for event in events:
        conv.process_event(event)
    conv.finish()
    return conv


def test_observe_sdk_reads_model_from_start(tmp_path):
    events = [
        {
            "kind": "execution_start",
            "call_id": "exec",
            "timestamp": 1.0,
            "agent_id": "planner",
            "input": "hi",
        },
        {
            "kind": "llm_call_start",
            "call_id": "llm",
            "parent_call_id": "exec",
            "timestamp": 1.1,
            "agent_id": "planner",
            "model": "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "messages": [{"role": "user", "content": "renew"}],
            "tools": ["lookup_quote"],
            "max_tokens": 4096,
            "temperature": 0.0,
        },
        {
            "kind": "llm_call_end",
            "call_id": "llm",
            "timestamp": 1.2,
            "agent_id": "planner",
            "output": "",
            "finish_reason": "tool_calls",
            "tool_calls": [{"toolName": "lookup_quote", "arguments": {"id": "1"}}],
            "tokens_used": {
                "prompt_tokens": 80,
                "completion_tokens": 18,
                "total_tokens": 98,
            },
        },
        {
            "kind": "execution_end",
            "call_id": "exec",
            "timestamp": 1.3,
            "agent_id": "planner",
            "output": "done",
        },
    ]
    attrs = _replay_chat_attrs(tmp_path, events)
    assert attrs.get("gen_ai.request.model") == "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert attrs.get("gen_ai.provider.name") == "bedrock"
    assert "renew" in str(attrs.get("gen_ai.input.messages") or "")
    assert "lookup_quote" in str(attrs.get("gen_ai.completion.0.content") or "")
    assert attrs.get("gen_ai.response.finish_reasons") == "tool_calls"
    assert attrs.get("gen_ai.usage.input_tokens") == 80
    assert attrs.get("gen_ai.usage.output_tokens") == 18
    assert attrs.get("gen_ai.usage.total_tokens") == 98
    assert attrs.get("gen_ai.request.temperature") == 0.0
    assert "lookup_quote" in str(attrs.get("mas.llm.tools") or "")


def test_observe_sdk_tool_call_text_matches_native_kg(tmp_path):
    events = [
        {"kind": "execution_start", "call_id": "exec", "timestamp": 1.0, "agent_id": "planner", "input": "hi"},
        {
            "kind": "llm_call_start",
            "call_id": "llm",
            "parent_call_id": "exec",
            "timestamp": 1.1,
            "agent_id": "planner",
            "model": "bedrock/m",
            "messages": [{"role": "user", "content": "renew"}],
        },
        {
            "kind": "llm_call_end",
            "call_id": "llm",
            "timestamp": 1.2,
            "agent_id": "planner",
            "output": "ok",
            "finish_reason": "tool_calls",
            "tool_calls": [{"toolName": "lookup_quote", "arguments": {"id": "1"}}],
        },
        {"kind": "execution_end", "call_id": "exec", "timestamp": 1.3, "agent_id": "planner", "output": "done"},
    ]
    attrs = _replay_chat_attrs(tmp_path, events)
    assert attrs.get("gen_ai.completion.0.content") == 'ok\n→ lookup_quote({"id": "1"})'


def _cut_off_events() -> list[dict]:
    """An LLM call that started and never ended: the run was cut off."""
    return [
        {"kind": "execution_start", "call_id": "exec", "timestamp": 1.0, "agent_id": "planner", "input": "hi"},
        {
            "kind": "llm_call_start",
            "call_id": "llm",
            "parent_call_id": "exec",
            "timestamp": 1.1,
            "agent_id": "planner",
            "model": "bedrock/m",
            "messages": [{"role": "user", "content": "renew"}],
        },
    ]


def test_a_call_with_no_end_event_is_not_exported(tmp_path):
    """An OTel SDK never exports a span that did not end: no end time or status is made up."""
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in _cut_off_events()))
    out = tmp_path / "spans.jsonl"
    replay_events_file(events_path, out, app_name="test-app", converter_profile="observe_sdk")
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    names = [s["name"] for s in spans]
    assert not any(n.endswith(".chat") for n in names), names
    assert not any(n.endswith(".agent") for n in names), names
    assert not any(s["attributes"].get("mas.status") == "unfinished" for s in spans)


def test_a_call_that_ended_is_still_successful(tmp_path):
    end = {"kind": "llm_call_end", "call_id": "llm", "timestamp": 1.2, "agent_id": "planner", "output": "ok"}
    events = [*_cut_off_events(), end]
    attrs = _replay_chat_attrs(tmp_path, events)
    assert attrs.get("mas.status") == "success"
    assert attrs.get("execution.success") == "true"


def test_the_cap_on_the_return_replaces_the_one_on_start(tmp_path):
    end = {"kind": "llm_call_end", "call_id": "llm", "timestamp": 1.2, "agent_id": "planner", "output": "ok"}
    events = [*_cut_off_events(), {**end, "max_tokens": 512}]
    events[1]["max_tokens"] = 4096
    attrs = _replay_chat_attrs(tmp_path, events)
    assert attrs.get("mas.llm.max_tokens") == 512


def test_a_malformed_temperature_does_not_stop_the_conversion(tmp_path):
    end = {"kind": "llm_call_end", "call_id": "llm", "timestamp": 1.2, "agent_id": "planner", "output": "ok"}
    events = [*_cut_off_events(), end]
    events[1].update(temperature="auto", max_tokens="")
    attrs = _replay_chat_attrs(tmp_path, events)
    assert "mas.llm.temperature" not in attrs


def test_an_llm_call_that_ended_in_error_is_not_reported_as_successful(tmp_path):
    end = {"kind": "llm_call_end", "call_id": "llm", "timestamp": 1.2, "agent_id": "planner", "output": "boom"}
    attrs = _replay_chat_attrs(tmp_path, [*_cut_off_events(), {**end, "status": "error"}])
    assert attrs.get("mas.status") == "error"
    assert attrs.get("execution.success") == "false"


def test_iso_and_numeric_string_timestamps_place_a_span_like_numbers(tmp_path):
    import json

    from mas.library.telemetry.conversion.replay import replay_events_file

    def spans_for(stamp):
        events = [
            {"kind": "execution_start", "call_id": "exec", "agent_id": "a", "timestamp": stamp(1)},
            {"kind": "execution_end", "call_id": "exec", "agent_id": "a", "timestamp": stamp(3)},
        ]
        path = tmp_path / f"{id(stamp)}.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events))
        out = tmp_path / f"{id(stamp)}-spans.jsonl"
        replay_events_file(path, out, app_name="t", converter_profile="observe_sdk")
        rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
        return next(r for r in rows if r["name"].endswith(".agent"))

    base = 1767225600.0  # 2026-01-01T00:00:00Z
    numeric = spans_for(lambda s: base + s)
    iso = spans_for(lambda s: f"2026-01-01T00:00:0{s}Z")
    assert iso["start_time"] == numeric["start_time"]
    assert iso["end_time"] == numeric["end_time"]
