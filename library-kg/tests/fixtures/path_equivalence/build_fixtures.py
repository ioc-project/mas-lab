#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regenerate ``delegation-events.jsonl.gz``, ``cut-off-events.jsonl.gz`` and
``categories-events.jsonl.gz``.

Small traces in the native format with explicit State ids (see ``docs/native-telemetry.md``,
"State identity"): a moderator delegates to one worker, then to two workers in a parallel
group joined by an ``aggregate`` ProcessingCall. The cut-off trace is the same run stopped
while the third worker's LLM call is open.

``explicit-events.jsonl.gz`` is not built here: it is the ``events.jsonl`` of a live run
(a trip-planner run on the current emitter), gzipped as the runtime wrote it.

Run from this directory: ``python build_fixtures.py``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

RUN = "r1"
SESSION = "sess-delegation"
T0 = 1_800_000_000.0

_events: list[dict] = []
_clock = [T0]


def _tick() -> float:
    _clock[0] = round(_clock[0] + 0.01, 3)
    return _clock[0]


def _emit(kind: str, agent: str, **fields) -> None:
    _events.append(
        {
            "kind": kind,
            "agent_id": agent,
            "run_id": RUN,
            "session_id": SESSION,
            "mas_id": "demo",
            "app_name": "demo",
            "timestamp": _tick(),
            **fields,
        }
    )


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _pair(kind: str, agent: str, **fields) -> None:
    _emit(f"{kind}_start", agent, **fields)
    _emit(f"{kind}_end", agent, **fields)


class Visit:
    """One agent visit: a chain of States ``{exec}#0 ... {exec}#n``."""

    def __init__(self, agent: str, exec_id: str, parent: str, task: str) -> None:
        self.agent, self.exec_id, self.parent, self.n = agent, exec_id, parent, 0
        self.content = {0: task}
        _emit(
            "execution_start",
            agent,
            call_id=exec_id,
            parent_call_id=parent,
            state_out=self.state(0),
            state_out_content=task,
        )

    def state(self, n: int) -> str:
        return f"{self.exec_id}#{n}"

    def step(self, content: str) -> tuple[str, str, str]:
        """Advance the chain: returns (state_in, state_in_content, state_out)."""
        prev = self.n
        self.n += 1
        self.content[self.n] = content
        return self.state(prev), self.content[prev], self.state(self.n)

    def context_assembly(self, call_id: str) -> None:
        s_in, c_in, s_out = self.step(f"[context for {self.agent} #{self.n + 1}]")
        _pair(
            "processing_call",
            self.agent,
            call_id=call_id,
            parent_call_id=self.exec_id,
            processing_name="context assembly",
            processing_type="context_assembly",
            state_in=s_in,
            state_in_content=c_in,
            state_out=s_out,
            state_out_content=self.content[self.n],
        )

    def llm(self, call_id: str, output: str, tool_calls: list | None = None, end: bool = True):
        text = _canon(tool_calls) if tool_calls else output
        s_in, c_in, s_out = self.step(text)
        common = {
            "call_id": call_id,
            "parent_call_id": self.exec_id,
            "state_in": s_in,
            "state_in_content": c_in,
            "state_out": s_out,
        }
        _emit("llm_call_start", self.agent, model="openai/gpt-4o", temperature=0.0, **common)
        if end:
            _emit(
                "llm_call_end",
                self.agent,
                state_out_content=text,
                finish_reason="tool_calls" if tool_calls else "stop",
                tokens_used={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                **common,
            )

    def end(self) -> None:
        s = self.state(self.n)
        _emit(
            "execution_end",
            self.agent,
            call_id=self.exec_id,
            parent_call_id=self.exec_id,
            status="ok",
            state_in=s,
            state_in_content=self.content[self.n],
            state_out=s,
        )


def build(cut_off: bool = False) -> list[dict]:
    _events.clear()
    _clock[0] = T0
    _emit("mas_call_start", "mas", call_id="mas-1")
    mod = Visit("moderator", "moderator-exec", "mas-1", "plan a trip to Paris")
    mod.context_assembly("proc-m1")
    mod.llm("llm-m1", "", [{"toolName": "delegate_to_alpha", "arguments": {"task": "route"}}])
    # -- sequential delegation to alpha ----------------------------------------------------
    tool_id = "tool-alpha"
    s_in, c_in, s_out = mod.step("")
    tool = {
        "call_id": tool_id,
        "parent_call_id": mod.exec_id,
        "tool_name": "delegate_to_alpha",
        "state_in": s_in,
        "state_in_content": c_in,
        "state_out": s_out,
    }
    _emit("tool_call_start", "moderator", arguments={"task": "route"}, **tool)
    _worker_visit(mod, tool, "alpha", "route", "route found: A-B")
    _emit("tool_call_end", "moderator", state_out_content="route found: A-B", **tool)
    mod.content[mod.n] = "route found: A-B"
    # -- a parallel group of two delegations ------------------------------------------------
    mod.context_assembly("proc-m2")
    mod.llm(
        "llm-m2",
        "",
        [
            {"toolName": "delegate_to_beta", "arguments": {"task": "hotel"}},
            {"toolName": "delegate_to_gamma", "arguments": {"task": "food"}},
        ],
    )
    group_in, group_in_c = mod.state(mod.n), mod.content[mod.n]
    _emit("parallel_group_start", "moderator", call_id="group-1", parent_call_id=mod.exec_id)
    members = []
    for worker, tool_id, task in (("beta", "tool-beta", "hotel"), ("gamma", "tool-gamma", "food")):
        mod.n += 1
        out = mod.state(mod.n)
        member = {
            "call_id": tool_id,
            "parent_call_id": mod.exec_id,
            "tool_name": f"delegate_to_{worker}",
            "state_in": group_in,
            "state_in_content": group_in_c,
            "state_out": out,
        }
        members.append((worker, task, member))
        _emit("tool_call_start", "moderator", arguments={"task": task}, **member)
    for worker, task, member in members:
        stop = cut_off and worker == "gamma"
        _worker_visit(mod, member, worker, task, f"{worker} done", cut_in_llm=stop)
        if stop:
            return list(_events)
        mod.content[int(member["state_out"].rsplit("#", 1)[1])] = f"{worker} done"
        _emit("tool_call_end", "moderator", state_out_content=f"{worker} done", **member)
    outs = [m["state_out"] for _, _, m in members]
    contents = ["beta done", "gamma done"]
    mod.n += 1
    join_out = mod.state(mod.n)
    mod.content[mod.n] = _canon(contents)
    _emit("parallel_group_end", "moderator", call_id="group-1", parent_call_id=mod.exec_id)
    _pair(
        "processing_call",
        "moderator",
        call_id="join-1",
        parent_call_id=mod.exec_id,
        processing_name="aggregate",
        processing_type="parallel_group",
        state_in=outs,
        state_in_content=_canon(contents),
        state_out=join_out,
        state_out_content=_canon(contents),
    )
    mod.context_assembly("proc-m3")
    mod.llm("llm-m3", "Trip planned: Paris via A-B, hotel and food booked.")
    mod.end()
    _emit(
        "mas_call_end",
        "mas",
        call_id="mas-1",
        status="success",
        state_in=mod.state(0),
        state_in_content=mod.content[0],
        state_out=mod.state(mod.n),
        state_out_content=mod.content[mod.n],
    )
    return list(_events)


def build_categories() -> list[dict]:
    """One agent visit that uses every opt-in category (see ``mas.library.kg`` ``categories``).

    A model call with a reasoning interval (``thinking``), a memory call, a RAG query and a
    skill call (``capabilities``), a governance decision, a context contribution and a point
    annotation (``annotations``), and the worker the process reports (``worker``). The core
    calls of the trace are the same with or without these records.
    """
    _events.clear()
    _clock[0] = T0
    _emit("mas_call_start", "mas", call_id="mas-1")
    agent = Visit("planner", "planner-exec", "mas-1", "plan a trip")
    _emit("infrastructure_info", "planner", parent_call_id=agent.exec_id, worker_id="worker-1")
    agent.context_assembly("proc-1")
    _emit(
        "context_part_contributed",
        "planner",
        call_id="part-1",
        parent_call_id=agent.exec_id,
        llm_call_id="llm-1",
        part_id="sys",
        source="system",
        access_mechanism="inject",
        retained=True,
        token_estimate=12,
    )
    # The model call and the reasoning interval inside it.
    s_in, c_in, s_out = agent.step("search flights")
    llm = {
        "call_id": "llm-1",
        "parent_call_id": agent.exec_id,
        "state_in": s_in,
        "state_in_content": c_in,
        "state_out": s_out,
    }
    _emit("llm_call_start", "planner", model="openai/gpt-4o", **llm)
    think = {
        "call_id": "llm-1#think",
        "parent_call_id": "llm-1",
        "state_in": s_in,
        "state_in_content": c_in,
        "state_out": "llm-1#think#0",
    }
    _emit("thinking_start", "planner", model="openai/gpt-4o", **think)
    _emit("thinking_end", "planner", state_out_content="the user wants flights", **think)
    _emit(
        "llm_call_end", "planner", state_out_content="search flights", finish_reason="stop", **llm
    )
    for kind, call_id, extra in (
        ("memory_store", "mem-1", {"memory_name": "episodic"}),
        ("rag_query", "rag-1", {"collection": "docs", "query": "flights to paris"}),
        ("skill_execution", "skill-1", {"skill_name": "book_flight"}),
    ):
        s_in, c_in, s_out = agent.step(f"{kind} result")
        _pair(
            kind,
            "planner",
            call_id=call_id,
            parent_call_id=agent.exec_id,
            state_in=s_in,
            state_in_content=c_in,
            state_out=s_out,
            state_out_content=f"{kind} result",
            **extra,
        )
    _emit(
        "governance_decision",
        "planner",
        parent_call_id=agent.exec_id,
        decision="ALLOW",
        policy_name="block-destructive",
    )
    _emit("user_input", "planner", parent_call_id=agent.exec_id)
    agent.end()
    _emit(
        "mas_call_end",
        "mas",
        call_id="mas-1",
        status="success",
        state_in=agent.state(0),
        state_in_content=agent.content[0],
        state_out=agent.state(agent.n),
        state_out_content=agent.content[agent.n],
    )
    return list(_events)


def _worker_visit(mod: Visit, tool: dict, worker: str, task: str, answer: str, cut_in_llm=False):
    """The delegate/reply pairs and the worker visit a delegate tool call starts."""
    exec_id = f"{worker}-{tool['call_id']}-exec"
    first = f"{exec_id}#0"
    _pair(
        "processing_call",
        "moderator",
        call_id=f"{tool['call_id']}:delegate",
        parent_call_id=mod.exec_id,
        processing_name="delegate",
        processing_type="agent_boundary",
        state_in=tool["state_in"],
        state_in_content=tool["state_in_content"],
        state_out=first,
        state_out_content=task,
    )
    visit = Visit(worker, exec_id, tool["call_id"], task)
    visit.context_assembly(f"proc-{worker}")
    if cut_in_llm:
        visit.llm(f"llm-{worker}", answer, end=False)
        return visit
    visit.llm(f"llm-{worker}", answer)
    visit.end()
    _pair(
        "processing_call",
        "moderator",
        call_id=f"{tool['call_id']}:reply",
        parent_call_id=mod.exec_id,
        processing_name="reply",
        processing_type="agent_boundary",
        state_in=visit.state(visit.n),
        state_in_content=answer,
        state_out=tool["state_out"],
        state_out_content=answer,
    )
    return visit


def main() -> None:
    here = Path(__file__).resolve().parent
    for name, events in (
        ("delegation-events.jsonl.gz", build(cut_off=False)),
        ("cut-off-events.jsonl.gz", build(cut_off=True)),
        ("categories-events.jsonl.gz", build_categories()),
    ):
        with gzip.open(here / name, "wt", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        print(name, len(events), "events")


if __name__ == "__main__":
    main()
