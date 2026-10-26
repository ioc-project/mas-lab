#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Exchange trace formatting."""

from __future__ import annotations

from mas.ctl.session.exchange_log import (
    CliTraceExchangePlugin,
    TraceFormatOptions,
    format_exchange,
)
from mas.runtime.driver.driver import ExchangeRecord


def test_format_exchange_timestamps():
    ex = ExchangeRecord(
        kind="llm_request",
        messages=[{"role": "user", "content": "hello"}],
        correlation_id=1,
        op="LLM_CALL",
        model="gpt-4o",
        ts_mono=1.5,
        ts_wall="2026-06-17T12:00:00.000Z",
    )
    out = format_exchange(
        "agent",
        ex,
        fmt=TraceFormatOptions(timestamps=True, turn_start_mono=1.0),
    )
    assert "2026-06-17T12:00:00.000Z" in out
    assert "(+0.500s)" in out
    assert "[user]" in out
    assert "hello" in out
    assert "LLM[gpt-4o]" in out
    assert "AGENT[agent]" in out


def test_format_exchange_summary_does_not_truncate_agent_to_user():
    answer = (
        "The current president of the United States is Joe Biden, who has been "
        "in office since January 20, 2021.\n"
        "- Source: web-search\n"
        "- Confidence: MEDIUM"
    )
    ex = ExchangeRecord(
        kind="user_out",
        text=answer,
        finish_reason="stop",
        ts_mono=1.5,
        ts_wall="2026-06-17T12:00:00.000Z",
    )
    out = format_exchange(
        "qa-agent",
        ex,
        fmt=TraceFormatOptions(
            summary_only=True,
            turn_start_mono=1.0,
            agent_name="qa-agent",
        ),
    )
    assert "..." not in out
    assert "January 20, 2021" in out
    assert "Confidence: MEDIUM" in out
    assert "AGENT[qa-agent] -> USER" in out


def test_format_exchange_engine_io_from_raw_json():
    ex = ExchangeRecord(
        kind="llm_response",
        text="hi",
        correlation_id=1,
        response_kind="MODEL_TEXT",
        next_step="STOP",
        engine_raw='{"next_step": "STOP", "text": "hi"}',
    )
    out = format_exchange("agent", ex, fmt=TraceFormatOptions(engine_io=True))
    assert "engine:" in out
    assert "next_step" in out
    assert "content:" in out


def test_format_exchange_summary_llm_request_does_not_dump_prompt():
    system = "You are a helpful assistant.\n" + ("x" * 400)
    ex = ExchangeRecord(
        kind="llm_request",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "Who is POTUS?"},
        ],
        tools=[{"type": "function", "function": {"name": "web-search"}}],
        correlation_id=1,
        op="LLM_CALL",
        ts_mono=1.5,
        ts_wall="2026-06-17T12:00:00.000Z",
    )
    out = format_exchange(
        "qa-agent",
        ex,
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "AGENT[qa-agent] -> LLM" in out
    assert "Who is POTUS?" in out
    assert "You are a helpful assistant" not in out
    assert "xxxx" not in out
    assert "[system]" not in out
    assert "[tools]" not in out


def test_format_exchange_summary_tool_call_uses_arguments():
    ex = ExchangeRecord(
        kind="tool_call",
        tool_name="web-search",
        tool_arguments={"query": "current POTUS"},
        correlation_id=2,
        op="TOOL_CALL",
        ts_mono=2.0,
        ts_wall="2026-06-17T12:00:01.000Z",
    )
    out = format_exchange(
        "qa-agent",
        ex,
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "AGENT[qa-agent] -> TOOL[web-search]" in out
    assert "current POTUS" in out
    assert "tool=" not in out
    assert "args=" not in out


def test_format_exchange_summary_user_in_and_tool_result():
    user_in = format_exchange(
        "qa-agent",
        ExchangeRecord(kind="user_in", text="Who is POTUS?", ts_mono=1.0, ts_wall="t"),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "USER -> AGENT[qa-agent]" in user_in
    assert "Who is POTUS?" in user_in

    result = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="tool_result",
            tool_name="web-search",
            text='{"summary": "Joe Biden is POTUS"}',
            ts_mono=2.0,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "TOOL[web-search] -> AGENT[qa-agent]" in result
    assert "Joe Biden" in result


def test_format_exchange_summary_reports_model_skill_and_tool():
    llm = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="llm_request",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            ts_mono=1.0,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "AGENT[qa-agent] -> LLM[gpt-4o]" in llm

    skill = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="tool_call",
            tool_name="activate_skill",
            semantics={"concept": "skill", "op": "activate", "subject": "answer-formatting"},
            tool_arguments={"name": "answer-formatting"},
            ts_mono=1.5,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "AGENT[qa-agent] -> TOOL[activate_skill] SKILL[answer-formatting]" in skill

    memory = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="tool_call",
            tool_name="memory_store",
            semantics={"concept": "memory", "op": "write"},
            tool_arguments={"content": "likes tea"},
            ts_mono=1.6,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "AGENT[qa-agent] -> TOOL[memory_store] MEMORY[write]" in memory

    chosen = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="llm_response",
            model="gpt-4o",
            next_step="TOOL_CALL",
            tool_name="web-search",
            tool_arguments={"query": "POTUS"},
            ts_mono=1.2,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "LLM[gpt-4o] -> TOOL[web-search]" in chosen

    chosen_skill = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="llm_response",
            model="gpt-4o",
            next_step="TOOL_CALL",
            tool_name="activate_skill",
            semantics={"concept": "skill", "op": "activate", "subject": "answer-formatting"},
            ts_mono=1.3,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "LLM[gpt-4o] -> TOOL[activate_skill] SKILL[answer-formatting]" in chosen_skill

    concept_only = format_exchange(
        "qa-agent",
        ExchangeRecord(
            kind="tool_call",
            tool_name="memory_store",
            semantics={"concept": "memory"},
            ts_mono=1.7,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "TOOL[memory_store] MEMORY" in concept_only
    assert "MEMORY[" not in concept_only

    unnamed = format_exchange(
        "qa-agent",
        ExchangeRecord(kind="tool_call", semantics={"concept": "memory", "op": "write"}, ts_mono=1.8, ts_wall="t"),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent"),
    )
    assert "AGENT[qa-agent] -> TOOL MEMORY[write]" in unnamed

    no_model = format_exchange(
        "qa-agent",
        ExchangeRecord(kind="llm_request", messages=[{"role": "user", "content": "hi"}], ts_mono=1.0, ts_wall="t"),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="qa-agent", llm_name=""),
    )
    assert "AGENT[qa-agent] -> LLM" in no_model
    assert "LLM[" not in no_model


def test_format_exchange_dump_pretty_prints_last():
    ex = ExchangeRecord(
        kind="tool_call",
        tool_name="web-search",
        tool_arguments={"query": "current POTUS"},
        correlation_id=2,
        op="TOOL_CALL",
    )
    out = format_exchange("qa-agent", ex)
    assert "── AGENT[qa-agent] → TOOL[web-search] ──" in out
    assert "tool=web-search" in out
    assert "current POTUS" in out
    assert "correlation_id=2" in out


def test_format_exchange_summary_delegated_turn_labels_caller_not_user():
    """A delegated turn's USER edge is really the calling agent (see
    KernelDriver.caller_agent_id) -- the trace must say so, not "USER"."""
    inbound = format_exchange(
        "finance",
        ExchangeRecord(
            kind="user_in",
            text="What is the Q3 revenue?",
            caller_agent_id="moderator",
            ts_mono=1.0,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="finance"),
    )
    assert "AGENT[moderator] -> AGENT[finance]" in inbound
    assert "USER" not in inbound

    outbound = format_exchange(
        "finance",
        ExchangeRecord(
            kind="user_out",
            text="Q3 revenue was $4.2M.",
            caller_agent_id="moderator",
            finish_reason="stop",
            ts_mono=2.0,
            ts_wall="t",
        ),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="finance"),
    )
    assert "AGENT[finance] -> AGENT[moderator]" in outbound
    assert "USER" not in outbound


def test_format_exchange_entry_agent_turn_still_labeled_user():
    """No caller_agent_id (the MAS entry agent) keeps the plain USER label."""
    out = format_exchange(
        "moderator",
        ExchangeRecord(kind="user_in", text="hi", ts_mono=1.0, ts_wall="t"),
        fmt=TraceFormatOptions(summary_only=True, turn_start_mono=1.0, agent_name="moderator"),
    )
    assert "USER -> AGENT[moderator]" in out


def test_format_exchange_gov_block_prints_agent_to_gov():
    ex = ExchangeRecord(
        kind="gov_block",
        text="Restricted destination: Shadowmere",
        policy_name="forbidden-destination",
    )
    dump = format_exchange("moderator", ex)
    assert "AGENT[moderator] → GOV[forbidden-destination]" in dump
    assert "Restricted destination: Shadowmere" in dump
    summary = format_exchange(
        "moderator",
        ex,
        fmt=TraceFormatOptions(summary_only=True, agent_name="moderator"),
    )
    assert "AGENT[moderator] -> GOV[forbidden-destination]" in summary
    assert "Shadowmere" in summary


def test_format_exchange_llm_response_marks_cache_hit():
    ex = ExchangeRecord(
        kind="llm_response",
        text="hi",
        correlation_id=1,
        op="LLM_CALL",
        response_kind="MODEL_TEXT",
        cache_status="hit",
        cache_layer="engine",
    )
    out = format_exchange("agent", ex)
    assert "cache=hit layer=engine" in out


def test_cache_hit_note_only_with_verbose():
    import io

    hit = ExchangeRecord(kind="llm_response", text="hi", cache_status="hit", cache_layer="engine")
    quiet = io.StringIO()
    plugin = CliTraceExchangePlugin(err=quiet)
    plugin.configure(trace=False, verbose=0, agent_id="agent", fmt=TraceFormatOptions())
    plugin.on_exchange(hit)
    assert quiet.getvalue() == ""

    traced = io.StringIO()
    plugin = CliTraceExchangePlugin(err=traced)
    plugin.configure(
        trace=True, verbose=0, agent_id="agent", fmt=TraceFormatOptions(summary_only=True)
    )
    plugin.on_exchange(hit)
    dump = traced.getvalue()
    assert dump  # --trace still prints the hop
    assert "served from cache" not in dump

    verbose = io.StringIO()
    plugin = CliTraceExchangePlugin(err=verbose)
    plugin.configure(trace=False, verbose=1, agent_id="agent", fmt=TraceFormatOptions())
    plugin.on_exchange(hit)
    note = verbose.getvalue()
    assert "LLM response served from cache (layer=engine)" in note
    assert "MAS_LLM_CACHE_READ=false" in note

    miss = io.StringIO()
    plugin = CliTraceExchangePlugin(err=miss)
    plugin.configure(trace=False, verbose=1, agent_id="agent", fmt=TraceFormatOptions())
    plugin.on_exchange(ExchangeRecord(kind="llm_response", text="hi", cache_status="miss"))
    assert miss.getvalue() == ""
