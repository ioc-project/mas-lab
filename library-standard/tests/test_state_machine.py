#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""state_machine: the engine pieces, then whole turns against a scripted engine. No model."""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from mas.library.standard.plugins.design_patterns import state_machine as sm_module
from mas.library.standard.plugins.design_patterns.moderated_rounds import build_table
from mas.library.standard.plugins.design_patterns.state_machine import (
    MachineError,
    ReplyError,
    StateMachinePlugin,
    apply_set,
    matches,
    read_json,
    read_word,
    render,
    reply_format,
    validate_definition,
)
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import DpState, QProduct, RunEvent
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import InvokeEngineIo, RaiseBoundaryError
from mas.runtime.schema.ingress import EngineIoReturn, OperatorSteerReceived

# --- engine pieces ------------------------------------------------------------


def test_registered() -> None:
    info = get_registry().resolve("state_machine")
    assert info is not None and info.class_name == "StateMachinePlugin"


def test_render_keeps_raw_values_and_literal_braces() -> None:
    scope = {"plan": {"a": 1}, "n": 2, "names": ["x", "y"], "out": {"text": "hi"}}
    assert render("{plan}", scope) == {"a": 1}
    assert render("{names}", scope) == ["x", "y"]
    assert render("n={n} {out.text}", scope) == "n=2 hi"
    assert render('Answer {"action": "x"} or {...}', scope) == 'Answer {"action": "x"} or {...}'
    assert render({"k": ["{n}", 5]}, scope) == {"k": [2, 5]}
    with pytest.raises(MachineError, match="'missing' is not defined"):
        render("{missing}", scope)


def test_a_substituted_value_is_not_rendered_again() -> None:
    assert render("{text}", {"text": "{secret}", "secret": "no"}) == "{secret}"
    assert render("say {text}", {"text": "{secret}", "secret": "no"}) == "say {secret}"


def test_filters() -> None:
    scope = {"plan": {"keep": 1, "hide": 2, "deep": [{"hide": 3, "k": 4}]}, "hidden": ["hide"], "request": "ask"}
    assert json.loads(render("{plan|without:hidden|json}", scope)) == {"keep": 1, "deep": [{"k": 4}]}
    assert render("{request|with_transcript}", scope) == "ask"
    rows = [{"seq": 1, "from": "a", "to": ["b"], "body": "hello"}]
    out = render("{request|with_transcript}", {**scope, "transcript_rows": rows})
    assert out == "Public transcript so far:\n[1] a -> b: hello\n\nRequest:\nask"
    assert render("{absent|or:request}", scope) == "ask"
    assert render("{empty|or:request}", {**scope, "empty": ""}) == "ask"
    with pytest.raises(MachineError, match="unknown filter"):
        render("{request|shout}", scope)


def test_guards() -> None:
    scope = {"n": 3, "out": {"ok": True, "action": "go", "counts": {"reject": 0}}, "limit": 3}
    assert matches(None, scope)
    assert matches({"out.action": "go", "out.counts.reject": 0}, scope)
    assert not matches({"out.action": "stop"}, scope)
    assert matches({"n": {"gte": "{limit}"}}, scope) and not matches({"n": {"gt": "{limit}"}}, scope)
    assert matches({"out.action": {"in": ["go", "wait"]}}, scope)
    assert matches({"out.action": {"not_in": ["stop"]}}, scope)
    assert matches({"out.nothing": {"exists": False}}, scope) and matches({"out.action": {"exists": True}}, scope)
    assert not matches({"out.nothing": "x"}, scope)  # an undefined path reads as null
    assert not matches({"out.nothing": {"gt": 1}}, scope) and not matches({"out.nothing": {"lte": 1}}, scope)
    with pytest.raises(MachineError, match="cannot compare"):
        matches({"out.action": {"gt": 1}}, scope)  # a string against a number
    with pytest.raises(MachineError, match="in needs a list"):
        matches({"out.action": {"in": "go on"}}, scope)


def test_set_updates() -> None:
    variables: dict = {"n": 1, "log": ["a"]}
    scope = {"out": {"rows": [{"w": "x"}, {"w": "y"}], "one": "z"}, "v": 7}
    apply_set(
        {
            "n": {"add": 2},
            "log": {"append": "{out.one}"},
            "rows": {"extend": "{out.rows}", "as": {"word": "{item.w}", "v": "{v}"}},
            "copy": "{n}",
        },
        variables,
        scope,
    )
    assert variables["n"] == 3 and variables["log"] == ["a", "z"]
    assert variables["rows"] == [{"word": "x", "v": 7}, {"word": "y", "v": 7}]
    assert variables["copy"] == 3  # a later entry sees the earlier one's new value


def test_read_json_and_word() -> None:
    spec = {"fields": {"action": {"enum": ["a", "b"]}, "plan": {"type": "object", "required_if": {"action": "b"}}}}
    assert read_json('```json\n{"action": "a"}\n```', spec) == {"action": "a"}
    assert read_json('Sure. {"action": "b", "plan": {"k": 1, "n": null}}', spec)["plan"] == {"k": 1, "n": None}
    assert read_json('{"action": "a", "plan": null, "extra": 3}', spec) == {"action": "a", "extra": 3}
    for text, why in [
        ('{"action": "b"}', "plan is required when action is 'b'"),  # conditionally required
        ('{"action": "b", "plan": "text"}', "plan: Input should be a valid dictionary"),
        ('{"action": "c"}', "action: Input should be 'a' or 'b'"),
        ("{}", "action: Field required"),  # required unless optional
        ("[1, 2]", "not an object"),
        ("no json here", "no JSON object"),
    ]:
        with pytest.raises(ReplyError, match=why.replace("[", r"\[")):
            read_json(text, spec)
    assert read_word("Confirm. All good.", ["confirm", "reject"]) == ("confirm", "All good.")
    assert read_word("**Reject**: late", ["confirm", "reject"]) == ("reject", "late")
    assert read_word("I would confirm if", ["confirm", "reject"]) is None


def test_the_format_instruction_comes_from_the_same_spec_as_the_parser() -> None:
    spec = {
        "json": {
            "fields": {
                "action": {"enum": ["a", "b"], "description": "what to do"},
                "n": {"type": "integer", "optional": True},
                "plan": {"type": "object", "required_if": {"action": "b"}},
            }
        }
    }
    text = reply_format(spec)
    assert text.startswith("Answer with ONLY a JSON object that matches this JSON Schema")
    assert (
        '"title"' not in text
        and '"enum":["a","b"]' in text
        and '"description":"what to do"' in text
        and '"integer"' in text
    )
    assert "\"plan\" is required when action is 'b'." in text
    assert reply_format({"word": ["yes", "no"]}).startswith("Start your reply with one of these words: yes, no.")


def test_a_bad_read_spec_is_a_table_problem() -> None:
    def problems(read: object) -> list[str]:
        return validate_definition(
            _table(
                a={"run": "delegate", "agents": ["x"], "input": "i", "read": read, "on": [{"goto": "z"}]},
                z={"run": "end", "result": "ok"},
            )
        )

    assert problems({"word": ["a"]}) == [] and problems({"json": {}}) == [] and problems("text") == []
    assert any("read must be" in p for p in problems({"xml": 1}))
    assert any("read.word" in p for p in problems({"word": []}))
    assert any("read.json must be" in p for p in problems({"json": {"fiels": {}}}))
    assert any("type must be one of" in p for p in problems({"json": {"fields": {"a": {"type": "date"}}}}))
    assert any("rule keys" in p for p in problems({"json": {"fields": {"a": {"one_of": [1]}}}}))
    assert any("enum must be" in p for p in problems({"json": {"fields": {"a": {"enum": []}}}}))


def _table(**states: dict) -> dict:
    return {"start": next(iter(states)), "states": states}


def test_validation_reports_every_kind_of_mistake() -> None:
    end = {"run": "end", "result": "ok"}
    good = _table(a={"run": "tool", "tool": "t", "on": [{"goto": "z"}]}, z=end)
    assert validate_definition(good) == []
    assert validate_definition({"states": {}}) == ["states must be a non-empty mapping"]
    assert "start 'q' is not a state" in validate_definition({**good, "start": "q"})
    assert "no state has run: end" in validate_definition(_table(a={"run": "tool", "tool": "t", "on": [{"goto": "a"}]}))
    bad = validate_definition(_table(a={"run": "nope"}, z=end))
    assert any("run must be one of" in p for p in bad)
    bad = validate_definition(_table(a={"run": "tool", "tool": "t", "typo": 1, "on": [{"goto": "z"}]}, z=end))
    assert any("unknown keys ['typo']" in p for p in bad)
    bad = validate_definition(_table(a={"run": "delegate", "on": [{"goto": "z"}]}, z=end))
    assert any("agents is required" in p for p in bad) and any("input is required" in p for p in bad)
    bad = validate_definition(_table(a={"run": "tool", "tool": "t", "on": [{"goto": "missing"}]}, z=end))
    assert any("goto 'missing' is not a state" in p for p in bad)
    bad = validate_definition(_table(a={"run": "tool", "tool": "t", "on": []}, z=end))
    assert any("at least one transition" in p for p in bad)
    bad = validate_definition(_table(a={"run": "tool", "tool": "t", "on": [{"goto": "z", "oops": 1}]}, z=end))
    assert any("transition has unknown keys ['oops']" in p for p in bad)
    bad = validate_definition(
        _table(a={"run": "tool", "tool": "t", "on": [{"goto": "z", "when": {"x": {"nand": 1}}}]}, z=end)
    )
    assert any("unknown operators ['nand']" in p for p in bad)
    bad = validate_definition(
        _table(a={"run": "delegate", "agents": ["x"], "input": "i", "log": "loud", "on": [{"goto": "z"}]}, z=end)
    )
    assert any("log must be one of" in p for p in bad)


# --- whole turns ----------------------------------------------------------------

TOWN = ["classifier", "billing", "outage"]


def _spec(**params: object) -> dict:
    return {
        "design_pattern": {"type": "state_machine", "params": params},
        "workflow": {
            "entry": "desk",
            "nodes": [{"id": "desk", "delegates_to": TOWN}, *({"id": p} for p in TOWN)],
        },
    }


class _Engine:
    """Answers delegations with ``reply(agent, task)``; LLM calls from ``llm`` in order.

    Records every call and the system prompt of every LLM call.
    """

    def __init__(self, reply: Callable[[str, str], str] | None = None, llm: list[EngineIoReturn] | None = None) -> None:
        self.reply = reply or (lambda agent, task: f"{agent} done")
        self.llm = list(llm or [])
        self.instance: RuntimeInstance | None = None
        self.calls: list[tuple[str, dict]] = []
        self.systems: list[str] = []
        self.react_lines_during_calls: list[bool] = []
        self.batch_sizes: list[int] = []
        self.on_call: Callable[[], None] | None = None

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "LLM_CALL":
            ctx = self.instance.driver.ctx
            messages = assemble_llm_messages(ctx, manifest={})
            self.systems.append(next((m["content"] for m in messages if m["role"] == "system"), ""))
            answer = self.llm.pop(0)
            return answer.model_copy(update={"correlation_id": io.correlation_id})
        q = self.instance.kernel.q
        name, args = q.pending_tools_by_cid.get(io.correlation_id) or (q.pending_tool_name, q.pending_tool_args)
        args = dict(args)
        self.calls.append((name, args))
        self.batch_sizes.append(len(q.pending_tools_by_cid))
        if self.on_call:
            self.on_call()
        self.react_lines_during_calls.append("state_machine_lines" in q.dp_data)
        if name.startswith("delegate_to_"):
            text = self.reply(name.removeprefix("delegate_to_"), str(args.get("task") or ""))
        else:
            text = f"{name} ran"
        return EngineIoReturn(
            correlation_id=io.correlation_id, response_kind="TOOL_RESULT", next_step="STOP", text=text
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)


def _instance(engine: _Engine, _parallel: bool = True, **params: object) -> RuntimeInstance:
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(agent_spec=_spec(**params), agent_id="desk", parallel_tool_calls=_parallel),
        engine=engine,
        enable_observability=False,
        enable_coordination=False,
    )
    instance.kernel.plugin = StateMachinePlugin()
    instance.driver.ctx.pattern_plugin_id = "state_machine"
    engine.instance = instance
    return instance


def _run(engine: _Engine, task: str = "My invoice is wrong", _parallel: bool = True, **params: object) -> str:
    trace = _instance(engine, _parallel, **params).run_user_text(task)
    assert trace.client_responses, "the turn produced no response"
    return trace.client_responses[-1].content


def _model_text(text: str, next_step: str = "STOP", **kw: object) -> EngineIoReturn:
    return EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step=next_step, text=text, **kw)  # type: ignore[arg-type]


ROUTE = {
    "start": "classify",
    "vars": {"kind": ""},
    "states": {
        "classify": {
            "run": "delegate",
            "agents": ["classifier"],
            "input": 'Which team? Answer JSON {"team": "billing" | "outage"}.\nCustomer: {task}',
            "read": {"json": {"fields": {"team": {"enum": ["billing", "outage"]}}}},
            "retry": 1,
            "on": [
                {"when": {"out.ok": False}, "goto": "failed"},
                {"when": {"out.team": "billing"}, "goto": "billing"},
                {"when": {"out.team": "outage"}, "goto": "outage"},
            ],
        },
        "billing": {
            "run": "delegate",
            "agents": ["billing"],
            "input": "{task}",
            "on": [{"set": {"answer": "{out.text}"}, "goto": "done"}],
        },
        "outage": {
            "run": "delegate",
            "agents": ["outage"],
            "input": "{task}",
            "on": [{"set": {"answer": "{out.text}"}, "goto": "done"}],
        },
        "failed": {"run": "end", "result": "could not classify"},
        "done": {"run": "end", "result": {"team": "{last.agent|or:kind}", "answer": "{answer}"}},
    },
}


def _classifier(team: str) -> Callable[[str, str], str]:
    return lambda agent, task: json.dumps({"team": team}) if agent == "classifier" else f"{agent} handles it"


def test_a_state_graph_routes_on_what_an_agent_says() -> None:
    for team in ("billing", "outage"):
        engine = _Engine(_classifier(team))
        out = json.loads(_run(engine, **ROUTE))
        assert out["answer"] == f"{team} handles it"
        assert [n for n, _ in engine.calls] == ["delegate_to_classifier", f"delegate_to_{team}"]
    assert "Customer: My invoice is wrong" in engine.calls[0][1]["task"]


def test_unreadable_reply_is_asked_once_more_then_fails_the_state() -> None:
    engine = _Engine(lambda agent, task: "hmm, hard to say")
    assert _run(engine, **ROUTE) == "could not classify"
    assert [n for n, _ in engine.calls] == ["delegate_to_classifier"] * 2

    answers = iter(["hmm", json.dumps({"team": "billing"})])
    engine = _Engine(lambda agent, task: next(answers) if agent == "classifier" else "ok")
    assert json.loads(_run(engine, **ROUTE))["answer"] == "ok"


def test_a_failed_delegation_is_an_outcome_not_a_crash() -> None:
    class _Failing(_Engine):
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            return EngineIoReturn(
                correlation_id=io.correlation_id, response_kind="ERROR", next_step="STOP", text="boom"
            )

    assert _run(_Failing(), **ROUTE) == "could not classify"


def test_the_agent_is_told_the_format_and_what_was_wrong_with_its_last_reply() -> None:
    table = {
        "start": "ask",
        "states": {
            "ask": {
                "run": "delegate",
                "agents": ["classifier"],
                "input": "Say yes or no.",
                "read": {"word": ["yes", "no"]},
                "retry": 1,
                "error_detail": "{agent} would not answer ({error})",
                "on": [{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "ok"}],
            },
            "bad": {"run": "end", "result": "{last.detail}"},
            "ok": {"run": "end", "result": "counts={last.counts}"},
        },
    }
    engine = _Engine(lambda a, t: "perhaps")
    assert _run(engine, **table) == "classifier would not answer (the reply must start with one of: yes, no)"
    first, second = (args["task"] for _, args in engine.calls)
    assert first == "Say yes or no.\n\nStart your reply with one of these words: yes, no. Then give your reason."
    assert second == "Your last reply could not be used (the reply must start with one of: yes, no). " + first
    assert _run(_Engine(lambda a, t: "Yes, of course"), **table) == 'counts={"yes": 1, "no": 0}'


def test_instruct_false_leaves_the_prompt_to_the_author() -> None:
    table = {
        "start": "ask",
        "states": {
            "ask": {
                "run": "delegate",
                "agents": ["classifier"],
                "input": "Say yes or no.",
                "read": {"word": ["yes", "no"]},
                "instruct": False,
                "on": [{"goto": "z"}],
            },
            "z": {"run": "end", "result": "ok"},
        },
    }
    engine = _Engine(lambda a, t: "yes")
    _run(engine, **table)
    assert engine.calls[0][1]["task"] == "Say yes or no."


def test_log_each_and_after_all_build_the_public_transcript() -> None:
    table = {
        "start": "talk",
        "states": {
            "talk": {
                "run": "delegate",
                "agents": ["billing", "outage"],
                "input": "{task}|{transcript}",
                "log": "each",
                "log_from": "desk",
                "log_body": "{task}",
                "on": [{"goto": "vote"}],
            },
            "vote": {
                "run": "delegate",
                "agents": ["billing", "outage"],
                "input": "{transcript}",
                "read": {"word": ["yes", "no"]},
                "log": "after_all",
                "log_from": "desk",
                "log_body": "Agree?",
                "log_reply": "{item.word}: {item.reason}",
                "on": [{"goto": "done"}],
            },
            "done": {"run": "end", "result": "{transcript}"},
        },
    }
    engine = _Engine(lambda agent, task: "yes: fine" if "|" not in task else f"{agent} says hi")
    out = _run(engine, task="go", **table)
    assert out.splitlines() == [
        "[1] desk -> billing: go",
        "[2] billing -> desk: billing says hi",
        "[3] desk -> outage: go",
        "[4] outage -> desk: outage says hi",
        "[5] desk -> billing, outage: Agree?",
        "[6] billing -> desk: yes: fine",
        "[7] outage -> desk: yes: fine",
    ]
    # In ``each``, a speaker sees the transcript before its own turn; in ``after_all`` nobody sees the others' answers.
    asks = [args["task"] for _, args in engine.calls]
    assert asks[0] == "go|" and "[2] billing -> desk" in asks[1]
    assert asks[2] == asks[3] and "Agree?" not in asks[2]


# --- fail closed ----------------------------------------------------------------


def _one(**state: object) -> dict:
    return {
        "start": "a",
        "states": {
            "a": {"run": "delegate", "agents": ["billing"], "input": "x", **state},
            "z": {"run": "end", "result": "ok"},
        },
    }


def test_no_matching_transition_ends_the_run() -> None:
    out = _run(_Engine(), **_one(on=[{"when": {"out.text": "never"}, "goto": "z"}]))
    assert out.startswith("state_machine: state 'a': no transition matches the outcome")


def test_an_unknown_agent_is_refused_before_anything_runs() -> None:
    engine = _Engine()
    out = _run(
        engine,
        **{
            **_one(on=[{"goto": "z"}]),
            "vars": {"who": ["nobody"]},
            "states": {
                **_one(on=[{"goto": "z"}])["states"],
                "a": {"run": "delegate", "agents": "{who}", "input": "x", "on": [{"goto": "z"}]},
            },
        },
    )
    assert "nobody" in out and not engine.calls


def test_an_agent_named_in_a_later_state_is_refused_before_the_first_state_runs() -> None:
    table = {
        "start": "a",
        "states": {
            "a": {"run": "delegate", "agents": ["billing"], "input": "x", "on": [{"goto": "b"}]},
            "b": {"run": "delegate", "agents": ["nobody"], "input": "x", "on": [{"goto": "z"}]},
            "z": {"run": "end", "result": "ok"},
        },
    }
    engine = _Engine()
    assert "nobody" in _run(engine, **table) and not engine.calls


def test_an_unknown_agent_chosen_at_run_time_is_refused() -> None:
    table = {
        "start": "ask",
        "states": {
            "ask": {
                "run": "delegate",
                "agents": ["classifier"],
                "input": "who?",
                "on": [{"set": {"who": ["{out.text}"]}, "goto": "then"}],
            },
            "then": {"run": "delegate", "agents": "{who}", "input": "x", "on": [{"goto": "z"}]},
            "z": {"run": "end", "result": "ok"},
        },
    }
    engine = _Engine(lambda a, t: "ghost")
    out = _run(engine, **table)
    assert "state 'then'" in out and "ghost" in out and [n for n, _ in engine.calls] == ["delegate_to_classifier"]


def test_a_loop_stops_at_max_steps() -> None:
    table = _one(on=[{"goto": "a"}])
    table["max_steps"] = 5
    engine = _Engine()
    assert "step limit of 5" in _run(engine, **table)
    assert len(engine.calls) == 5


def test_a_missing_variable_ends_the_run_with_its_name() -> None:
    out = _run(_Engine(), **_one(input="{nope}", on=[{"goto": "z"}]))
    assert out == "state_machine: state 'a': 'nope' is not defined"


def test_a_bad_table_is_refused_with_the_first_problem() -> None:
    engine = _Engine()
    out = _run(engine, start="a", states={"a": {"run": "end"}})
    assert out == "state_machine: state 'a' (end): result is required" and not engine.calls


# --- other children ----------------------------------------------------------------


def test_a_tool_state_calls_the_tool_with_rendered_arguments() -> None:
    table = {
        "start": "log",
        "vars": {"tag": "audit"},
        "states": {
            "log": {"run": "tool", "tool": "write_{tag}", "args": {"task": "{task}", "n": 2}, "on": [{"goto": "z"}]},
            "z": {"run": "end", "result": "{last.text}"},
        },
    }
    engine = _Engine()
    assert _run(engine, task="hello", **table) == "write_audit ran"
    assert engine.calls == [("write_audit", {"task": "hello", "n": 2})]


def test_a_react_state_runs_the_entry_agents_own_loop_with_the_states_task() -> None:
    table = {
        "start": "think",
        "vars": {"style": "terse"},
        "states": {
            "think": {
                "run": "react",
                "instructions": "Be {style}.",
                "input": "Summarise for the customer: {task}",
                "on": [{"when": {"out.ok": False}, "goto": "bad"}, {"set": {"summary": "{out.text}"}, "goto": "ask"}],
            },
            "ask": {
                "run": "delegate",
                "agents": ["billing"],
                "input": "Summary: {summary}",
                "on": [{"goto": "done"}],
            },
            "bad": {"run": "end", "result": "llm failed: {last.detail}"},
            "done": {"run": "end", "result": "{last.text}"},
        },
    }
    engine = _Engine(llm=[_model_text("Invoice 7 is wrong.")])
    assert _run(engine, task="invoice?", **table) == "billing done"
    # The model saw the state's own task, and the next state got the model's answer.
    assert "Be terse." in engine.systems[0] and "Summarise for the customer: invoice?" in engine.systems[0]
    assert engine.calls == [("delegate_to_billing", {"task": "Summary: Invoice 7 is wrong."})]


def test_a_react_state_can_call_tools_before_it_answers() -> None:
    table = {
        "start": "think",
        "states": {
            "think": {"run": "react", "input": "Look it up.", "on": [{"goto": "done"}]},
            "done": {"run": "end", "result": "{last.text}"},
        },
    }
    engine = _Engine(
        llm=[
            _model_text("", next_step="TOOL_CALL", tool_name="lookup", tool_arguments={"id": 7}),
            _model_text("It is paid."),
        ]
    )

    class _Tooling(_Engine):
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            out = super().invoke(io)
            if io.op == "TOOL_CALL":  # after a tool result a ReAct loop asks the model again
                return out.model_copy(update={"next_step": "LLM_CALL"})
            return out

    engine.__class__ = _Tooling
    assert _run(engine, **table) == "It is paid."
    assert engine.calls == [("lookup", {"id": 7})]
    assert len(engine.systems) == 2 and all("Look it up." in s for s in engine.systems)


def test_the_react_task_does_not_leak_into_later_states() -> None:
    table = {
        "start": "think",
        "states": {
            "think": {"run": "react", "input": "PRIVATE-TASK", "on": [{"goto": "ask"}]},
            "ask": {"run": "delegate", "agents": ["billing"], "input": "x", "on": [{"goto": "done"}]},
            "done": {"run": "end", "result": "ok"},
        },
    }
    engine = _Engine(llm=[_model_text("fine")])
    instance = _instance(engine, **table)
    instance.run_user_text("hi")
    assert engine.react_lines_during_calls == [False]  # the delegate state ran without the react task
    assert "state_machine_lines" not in instance.kernel.q.dp_data


def test_a_react_llm_error_is_an_outcome() -> None:
    table = {
        "start": "think",
        "states": {
            "think": {
                "run": "react",
                "input": "go",
                "on": [{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "done"}],
            },
            "bad": {"run": "end", "result": "failed: {last.detail}"},
            "done": {"run": "end", "result": "ok"},
        },
    }
    engine = _Engine(
        llm=[EngineIoReturn(correlation_id=1, response_kind="ERROR", next_step="STOP", text="rate limited")]
    )
    assert _run(engine, **table) == "failed: rate limited"


# --- any registered design pattern as a child ------------------------------------


def _work(**state: object) -> dict:
    return {
        "start": "work",
        "states": {
            "work": {"run": "pattern", "input": "{task}", "on": [{"goto": "done"}], **state},
            "done": {"run": "end", "result": "{last.text}"},
        },
    }


def test_a_deterministic_pattern_runs_as_a_child() -> None:
    engine = _Engine(lambda agent, task: f"{agent}<{task}>")
    out = _run(engine, task="go", **_work(pattern="deterministic_linear", agents=["billing", "outage"]))
    assert out == "outage<billing<go>>"  # linear: each agent gets the previous reply
    assert [n for n, _ in engine.calls] == ["delegate_to_billing", "delegate_to_outage"]


def test_a_child_sees_only_its_own_replies_not_the_earlier_states() -> None:
    table = {
        "start": "first",
        "states": {
            "first": {"run": "delegate", "agents": ["classifier"], "input": "x", "on": [{"goto": "work"}]},
            "work": {
                "run": "pattern",
                "pattern": "deterministic_parallel",  # concatenates every reply it can see
                "agents": ["billing", "outage"],
                "input": "{task}",
                "on": [{"goto": "done"}],
            },
            "done": {"run": "end", "result": "{last.text}"},
        },
    }
    engine = _Engine(lambda agent, task: "NOISE" if agent == "classifier" else f"{agent} ok")
    out = _run(engine, **table)
    assert "NOISE" not in out and "=== 1 ===" in out and "=== 2 ===" in out and "=== 3 ===" not in out
    assert engine.batch_sizes[-2:] == [2, 2]  # the child's two calls went out as one parallel batch


def test_a_child_uses_the_states_params_filled_from_variables() -> None:
    table = _work(
        pattern="moderated_rounds",
        agents=["classifier", "billing"],
        params={"decider": "{judge}", "order": ["billing"], "max_rounds": 2},
        read={"json": {"fields": {"status": {"enum": ["committed", "withheld", "escalated"]}}}},
    )
    table["vars"] = {"judge": "classifier"}
    table["states"]["done"]["result"] = "{last.status}: {last.reason}"

    def reply(agent: str, task: str) -> str:
        return json.dumps({"action": "escalate", "reason": "a human must decide"}) if agent == "classifier" else "ok"

    assert _run(_Engine(reply), **table) == "escalated: decider_decision"


def test_an_unreadable_child_answer_is_an_outcome_not_a_guess() -> None:
    table = _work(
        pattern="deterministic_single", agents=["billing"], read={"json": {"fields": {"x": {"type": "string"}}}}
    )
    table["states"]["work"]["on"] = [{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "done"}]
    table["states"]["bad"] = {"run": "end", "result": "unreadable: {last.text}"}
    assert _run(_Engine(lambda a, t: "plain words"), **table) == "unreadable: plain words"


def test_a_state_machine_runs_as_a_child_of_a_state_machine() -> None:
    inner = {
        "start": "ask",
        "states": {
            "ask": {"run": "delegate", "agents": ["{who}"], "input": "inner {task}", "on": [{"goto": "z"}]},
            "z": {"run": "end", "result": "inner got {last.text}"},
        },
    }
    table = _work(pattern="state_machine", params=inner, input="outer {task}")
    table["states"]["work"]["with"] = {"who": "{target}"}
    table["vars"] = {"target": "billing"}
    engine = _Engine(lambda a, t: f"{a} saw [{t}]")
    assert _run(engine, task="T", **table) == "inner got billing saw [inner outer T]"


def test_a_failing_child_machine_fails_its_state() -> None:
    inner = {"start": "a", "states": {"a": {"run": "end", "result": "{nope}"}}}
    table = _work(pattern="state_machine", params=inner)
    table["states"]["work"]["on"] = [{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "done"}]
    table["states"]["bad"] = {"run": "end", "result": "child failed: {last.detail}"}
    assert "child failed: state_machine: state 'a': 'nope' is not defined" in _run(_Engine(), **table)


def test_machines_nested_too_deep_are_refused() -> None:
    table: dict = {"start": "a", "states": {"a": {"run": "end", "result": "bottom"}}}
    for _ in range(7):
        table = _work(pattern="state_machine", params=table)
        table["states"]["work"]["on"] = [{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "done"}]
        table["states"]["bad"] = {"run": "end", "result": "{last.detail}"}
    assert "nested deeper than" in _run(_Engine(), **table)


def test_an_unknown_pattern_or_child_agent_is_refused_before_anything_runs() -> None:
    engine = _Engine()
    assert "pattern 'nope' is not a registered design pattern" in _run(engine, **_work(pattern="nope"))
    out = _run(engine, **_work(pattern="deterministic_linear", agents=["ghost"]))
    assert "ghost" in out and not engine.calls
    later = _work(pattern="nope")
    later["states"]["first"] = {"run": "delegate", "agents": ["billing"], "input": "x", "on": [{"goto": "work"}]}
    later["start"] = "first"
    assert "'nope' is not a registered design pattern" in _run(engine, **later) and not engine.calls


def test_the_child_pattern_adds_its_own_protocol_lines_to_the_prompt() -> None:
    table = _work(pattern="react", instructions="Be brief.")
    engine = _Engine(llm=[_model_text("fine")])
    _run(engine, **table)
    assert "Be brief." in engine.systems[0]


# --- parallel regions ----------------------------------------------------------------


def _region(agent: str, *more: str) -> dict:
    """A region that asks ``agent`` (then each of ``more``) and ends with what the last said."""
    states: dict = {}
    names = [agent, *more]
    for i, who in enumerate(names):
        nxt = f"s{i + 1}" if i + 1 < len(names) else "end"
        states[f"s{i}"] = {"run": "delegate", "agents": [who], "input": f"{who}: {{task}}", "on": [{"goto": nxt}]}
    states["end"] = {"run": "end", "result": {"said": "{last.text}", "who": names[-1]}}
    return {"start": "s0", "states": states}


def _fan(join: str = "all", **regions: dict) -> dict:
    return {
        "start": "fan",
        "states": {
            "fan": {"run": "parallel", "join": join, "regions": regions, "on": [{"goto": "done"}]},
            "done": {"run": "end", "result": "{last}"},
        },
    }


def test_regions_advance_together_and_join() -> None:
    engine = _Engine(lambda a, t: f"{a} done")
    out = json.loads(_run(engine, **_fan(bill=_region("billing"), out=_region("outage"))))
    assert out["ok"] is True and out["finished"] == ["bill", "out"] and out["unfinished"] == []
    assert out["regions"]["bill"]["data"] == {"said": "billing done", "who": "billing"}
    assert out["regions"]["out"]["data"]["said"] == "outage done"
    assert engine.batch_sizes == [2, 2]  # both delegations went out as one parallel batch


def test_regions_of_different_length_keep_going_alone() -> None:
    engine = _Engine(lambda a, t: f"{a} done")
    out = json.loads(_run(engine, **_fan(long=_region("billing", "classifier", "outage"), short=_region("outage"))))
    assert out["regions"]["long"]["data"]["who"] == "outage" and out["regions"]["short"]["ok"]
    assert [n for n, _ in engine.calls].count("delegate_to_outage") == 2
    assert engine.batch_sizes == [2, 2, 0, 0]  # a batch of two, then the long region alone, one call at a time


def test_without_parallel_tool_calls_the_regions_take_turns() -> None:
    engine = _Engine(lambda a, t: f"{a} done")
    out = json.loads(_run(engine, _parallel=False, **_fan(a=_region("billing"), b=_region("outage"))))
    assert out["finished"] == ["a", "b"]
    assert [n for n, _ in engine.calls] == ["delegate_to_billing", "delegate_to_outage"]
    assert set(engine.batch_sizes) == {0}  # one call at a time
    engine = _Engine(lambda a, t: f"{a} done")
    _run(engine, _parallel=False, **_fan(a=_region("billing", "classifier"), b=_region("outage", "classifier")))
    assert [n for n, _ in engine.calls] == ["delegate_to_billing", "delegate_to_outage"] + [
        "delegate_to_classifier"
    ] * 2


def test_join_any_ends_the_state_when_the_first_region_is_done() -> None:
    engine = _Engine(lambda a, t: f"{a} done")
    out = json.loads(_run(engine, **_fan("any", slow=_region("billing", "outage"), fast=_region("classifier"))))
    assert out["finished"] == ["fast"] and out["unfinished"] == ["slow"]
    assert len(engine.calls) == 2  # the slow region's second call never went out


def test_a_region_that_fails_is_an_outcome_of_the_parallel_state() -> None:
    bad = _region("billing")
    bad["vars"] = {"nothing": "{missing}"}
    engine = _Engine(lambda a, t: f"{a} done")
    out = json.loads(_run(engine, **_fan(good=_region("outage"), bad=bad)))
    assert out["ok"] is False and out["regions"]["good"]["ok"] and "missing" in out["regions"]["bad"]["detail"]


def test_a_region_sees_the_variables_of_the_machine_but_cannot_change_them() -> None:
    region = _region("billing")
    region["states"]["s0"]["on"] = [{"set": {"mine": "region"}, "goto": "end"}]
    region["states"]["s0"]["input"] = "{shared}"
    table = _fan(r=region)
    table["vars"] = {"shared": "from the machine", "mine": "machine"}
    table["states"]["done"]["result"] = {"mine": "{mine}", "inside": "{last.regions.r.vars.mine}"}
    engine = _Engine(lambda a, t: t)
    out = json.loads(_run(engine, **table))
    assert out == {"mine": "machine", "inside": "region"} and engine.calls[0][1]["task"] == "from the machine"


def test_a_region_may_only_hold_calls_that_can_go_out_as_a_batch() -> None:
    region = {
        "start": "a",
        "states": {
            "a": {"run": "react", "input": "x", "on": [{"goto": "z"}]},
            "z": _region("billing")["states"]["end"],
        },
    }
    out = _run(_Engine(), **_fan(r=region))
    assert "region 'r'" in out and "run must be one of ['delegate', 'tool', 'end']" in out


# --- signals ------------------------------------------------------------------------------


def _loop(**extra: object) -> dict:
    return {
        "start": "a",
        "states": {
            "a": {"run": "delegate", "agents": ["billing"], "input": "x", "on": [{"goto": "a"}]},
            "wrap": {"run": "end", "result": "wrapped after {steps} states; {why|or:task}"},
        },
        "max_steps": 20,
        **extra,
    }


def test_a_signal_on_the_step_budget_leaves_a_loop_from_any_state() -> None:
    engine = _Engine()
    out = _run(engine, **_loop(signals=[{"when": {"steps": {"gte": 3}}, "goto": "wrap"}]))
    assert out == "wrapped after 4 states; My invoice is wrong" and len(engine.calls) == 3


def test_a_signal_on_elapsed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(StateMachinePlugin, "_clock", staticmethod(lambda: now[0]))
    engine = _Engine()
    engine.on_call = lambda: now.__setitem__(0, now[0] + 100)
    out = _run(engine, **_loop(signals=[{"when": {"elapsed": {"gte": 150}}, "goto": "wrap"}]))
    assert out.startswith("wrapped after 3 states") and len(engine.calls) == 2


def test_signals_are_tried_before_the_states_own_transitions() -> None:
    table = _loop(signals=[{"when": {"out.text": "STOP"}, "set": {"why": "told to stop"}, "goto": "wrap"}])
    engine = _Engine(lambda a, t: "STOP" if len(engine.calls) == 2 else "go on")
    assert _run(engine, **{**table, "vars": {"why": ""}}) == "wrapped after 3 states; told to stop"


def test_a_signal_can_fire_between_the_rounds_of_a_parallel_state(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(StateMachinePlugin, "_clock", staticmethod(lambda: now[0]))
    table = _fan(slow=_region("billing", "outage", "classifier"), fast=_region("classifier"))
    table["signals"] = [
        {
            "when": {"elapsed": {"gte": 150}, "regions.slow.done": False},
            "set": {"stuck_in": "{regions.slow.state}"},
            "goto": "late",
        }
    ]
    table["states"]["late"] = {"run": "end", "result": "too late: {stuck_in}"}
    engine = _Engine()
    engine.on_call = lambda: now.__setitem__(0, now[0] + 100)
    # Round one (two calls) takes 200 seconds; the signal fires before the slow region's next call goes out.
    assert _run(engine, **table) == "too late: s1" and len(engine.calls) == 2


def _machine(table: dict, config: KernelConfig | None = None) -> tuple[StateMachinePlugin, KernelConfig]:
    """A machine driven by hand: it records the call it wants next instead of sending it."""
    machine = StateMachinePlugin()
    config = config or KernelConfig(agent_spec=_spec(), agent_id="desk")
    machine.region_start(table, {}, "the task", TOWN, config)
    return machine, config


def test_a_bad_signal_row_is_a_table_problem() -> None:
    assert any(
        "signals" in p and "goto 'nowhere'" in p for p in validate_definition(_loop(signals=[{"goto": "nowhere"}]))
    )
    assert any("signals" in p for p in validate_definition(_loop(signals={"goto": "wrap"})))
    assert validate_definition(_loop(signals=[{"when": {"elapsed": {"gt": 5}}, "goto": "wrap"}])) == []


def test_an_unknown_agent_inside_a_region_is_refused_before_anything_runs() -> None:
    table = _fan(r=_region("ghost"))
    table["states"]["first"] = {"run": "delegate", "agents": ["billing"], "input": "x", "on": [{"goto": "fan"}]}
    table["start"] = "first"
    engine = _Engine()
    assert "ghost" in _run(engine, **table) and not engine.calls


def test_the_default_detail_says_why_a_reply_could_not_be_read() -> None:
    table = _one(read={"word": ["yes", "no"]}, on=[{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "z"}])
    table["states"]["bad"] = {"run": "end", "result": "{last.detail}"}
    assert _run(_Engine(lambda a, t: "maybe"), **table) == (
        "billing gave no readable reply: the reply must start with one of: yes, no"
    )


def test_an_unreadable_child_answer_says_why() -> None:
    table = _work(pattern="deterministic_single", agents=["billing"], read={"word": ["yes", "no"]})
    table["states"]["work"]["on"] = [{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "done"}]
    table["states"]["bad"] = {"run": "end", "result": "{last.detail}"}
    out = _run(_Engine(lambda a, t: "maybe"), **table)
    assert out == "<deterministic_single> gave no readable answer: the reply must start with one of: yes, no"


# --- review findings: manifests, hostile replies, malformed tables ---------------------


def test_a_table_loaded_from_yaml_works_although_yaml_reads_on_as_true() -> None:
    table = yaml.safe_load(
        textwrap.dedent(
            """
            start: ask
            states:
              ask:
                run: delegate
                agents: [billing]
                input: "{task}"
                on:
                  - {goto: fan}
              fan:
                run: parallel
                regions:
                  r:
                    start: s
                    states:
                      s: {run: delegate, agents: [outage], input: x, on: [{goto: e}]}
                      e: {run: end, result: done}
                on:
                  - {goto: done}
              done: {run: end, result: "{last.finished}"}
            """
        )
    )
    assert True in table["states"]["ask"] and "on" not in table["states"]["ask"]  # what safe_load really gives
    assert validate_definition(table) == []
    assert json.loads(_run(_Engine(), **table)) == ["r"]


def _doc_tables() -> list[tuple[str, dict]]:
    text = (Path(__file__).resolve().parents[2] / "docs" / "manifests" / "agent.md").read_text()
    section = text[text.index("#### state_machine") : text.index("### ContextManager")]
    end = {"run": "end", "result": "x"}
    tables = []
    for i, block in enumerate(re.findall(r"```yaml\n(.*?)```", section, re.S)):
        data = yaml.safe_load(textwrap.dedent(block))
        if "design_pattern" in data:
            binding = data["design_pattern"]
            params = binding["params"]
            if binding["type"] == "moderated_rounds":
                params = build_table(params, ["planner", "finance", "ops"])
            tables.append((f"block {i}", params))
            continue
        gotos = set(re.findall(r"goto: (\w+)", block))
        if "signals" in data:  # a fragment: the signals of some table
            states = {name: end for name in gotos | {"s"}}
            tables.append((f"block {i}", {"start": "s", "states": states, **data}))
        else:  # a fragment: some states of a table
            states = {**{name: end for name in gotos}, **data}
            tables.append((f"block {i}", {"start": next(iter(data)), "states": states}))
    return tables


def test_every_yaml_example_in_the_docs_is_a_valid_table() -> None:
    tables = _doc_tables()
    assert len(tables) >= 5
    for name, table in tables:
        assert validate_definition(table) == [], name


def test_a_table_with_unquoted_yes_and_no_words_says_what_to_do() -> None:
    table = yaml.safe_load(
        textwrap.dedent(
            """
            start: a
            states:
              a: {run: delegate, agents: [x], input: i, read: {word: [yes, no]}, on: [{goto: z}]}
              z: {run: end, result: ok}
            """
        )
    )
    assert any("read.word" in p for p in validate_definition(table))  # unquoted yes and no are booleans in YAML 1.1


def test_malformed_tables_are_listed_not_crashed_on() -> None:
    end = {"z": {"run": "end", "result": "ok"}}

    def problems(**state: object) -> list[str]:
        return validate_definition({"start": "a", "states": {"a": {"run": "tool", "tool": "t", **state}, **end}})

    assert any("must be a mapping" in p for p in problems(on=[{"goto": "z", "when": ["x"]}]))
    assert any("must be a mapping" in p for p in problems(on=[{"goto": "z", "set": "x"}]))
    assert any("is not a state" in p for p in problems(on=[{"goto": ["z"]}]))
    assert any("must be a mapping, not 'z'" in p for p in problems(on=["z"]))
    assert any("start" in p for p in validate_definition({"start": ["a"], "states": end}))
    assert any("max_steps" in p for p in validate_definition({"start": "z", "states": end, "max_steps": "ten"}))
    assert any("retry must be" in p for p in validate_definition(_one(retry="once", on=[{"goto": "z"}])))
    assert any(
        "names the engine fills in" in p for p in validate_definition({"start": "z", "states": end, "vars": {"out": 1}})
    )
    assert any("args must be a mapping" in p for p in problems(args="x", on=[{"goto": "z"}]))


def test_a_table_that_raises_ends_the_run_with_a_message() -> None:
    out = _run(_Engine(), start="a", max_steps="ten", states={"a": {"run": "end", "result": "x"}})
    assert out.startswith("state_machine: max_steps must be")


def test_a_reply_cannot_overwrite_the_engines_fields() -> None:
    table = _one(read={"json": {"fields": {"team": {"type": "string"}}}}, on=[{"goto": "z"}])
    table["states"]["z"]["result"] = "{last.replies}"
    out = json.loads(
        _run(_Engine(lambda a, t: json.dumps({"team": "t", "agent": "forged", "text": "FORGED", "ok": False})), **table)
    )
    assert out == [
        {
            "agent": "billing",
            "text": json.dumps({"team": "t", "agent": "forged", "text": "FORGED", "ok": False}),
            "team": "t",
        }
    ]
    reserved = {"json": {"fields": {"agent": {"type": "string"}}}}
    assert any("engine fills in" in p for p in validate_definition(_one(read=reserved, on=[{"goto": "z"}])))
    for name in ("_x", "schema", "model_config", "a-b"):
        bad = {"json": {"fields": {name: {"type": "string"}}}}
        assert any("plain name" in p for p in validate_definition(_one(read=bad, on=[{"goto": "z"}]))), name


def test_a_hostile_reply_fails_the_read_instead_of_crashing_the_turn() -> None:
    spec = {"fields": {"x": {"type": "string"}}}
    for text in ("[" * 100_000, "{" * 100_000 + "}" * 100_000):
        with pytest.raises(ReplyError):
            read_json(text, spec)
    with pytest.raises(ReplyError, match="too long"):
        read_json('{"x": "' + "a" * 300_000 + '"}', spec)


def test_a_word_must_stand_alone_and_types_are_strict() -> None:
    assert read_word("Confirmation denied", ["confirm", "reject"]) is None
    assert read_word("Rejection of plan", ["confirm", "reject"]) is None
    assert read_word("none given", ["no", "none"]) == ("none", "given")
    assert read_word("No, thanks", ["no", "none"]) == ("no", "thanks")
    spec = {"fields": {"n": {"type": "integer"}, "b": {"type": "boolean"}, "e": {"enum": [1, 2]}}}
    assert read_json('{"n": 3, "b": true, "e": 2}', spec) == {"n": 3, "b": True, "e": 2}
    for bad in (
        '{"n": "3", "b": true, "e": 1}',
        '{"n": true, "b": true, "e": 1}',
        '{"n": 3, "b": "yes", "e": 1}',
        '{"n": 3, "b": true, "e": true}',
        '{"n": 3.0, "b": true, "e": 1}',
    ):
        with pytest.raises(ReplyError):
            read_json(bad, spec)


def test_required_if_must_name_another_declared_field() -> None:
    for fields in ({"a": {"required_if": {"zzz": 1}}}, {"a": {"required_if": {"a": 1}}}):
        problems = validate_definition(_one(read={"json": {"fields": fields}}, on=[{"goto": "z"}]))
        assert any("other declared fields" in p for p in problems)


def test_an_empty_reply_is_not_a_reply() -> None:
    table = _one(on=[{"when": {"out.ok": False}, "goto": "bad"}, {"goto": "z"}])
    table["states"]["bad"] = {"run": "end", "result": "{last.detail}"}
    assert _run(_Engine(lambda a, t: "  "), **table) == "no reply from billing"


def test_a_failed_state_keeps_the_replies_it_had() -> None:
    table = {
        "start": "a",
        "states": {
            "a": {
                "run": "delegate",
                "agents": ["billing", "outage"],
                "input": "x",
                "read": {"word": ["yes", "no"]},
                "on": [{"goto": "z"}],
            },
            "z": {"run": "end", "result": "{last.replies}"},
        },
    }
    out = json.loads(_run(_Engine(lambda a, t: "yes" if a == "billing" else "perhaps"), **table))
    assert [r["agent"] for r in out] == ["billing"]


def test_a_boundary_error_in_the_middle_of_a_batch_reaches_the_kernel_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sm_module,
        "schedule_parallel_tools_egress",
        lambda *a, **k: [RaiseBoundaryError(code="GOV_DENY", recoverable=True)],
    )
    trace = _instance(_Engine(), **_fan(a=_region("billing"), b=_region("outage"))).run_user_text("go")
    assert trace.boundary_errors and {e.code for e in trace.boundary_errors} == {"GOV_DENY"}  # not a PLUGIN_ERROR


def test_state_from_a_failed_turn_does_not_follow_the_plugin_into_the_next() -> None:
    plugin = StateMachinePlugin()
    plugin._failure, plugin._result, plugin._finished = "boom", "boom", True
    plugin._reset_state(TOWN, "again")
    assert (plugin._failure, plugin._result, plugin._finished) == ("", "", False)


def test_abandoning_a_state_clears_the_lines_of_nested_machines_too() -> None:
    machine = StateMachinePlugin()
    q = QProduct()
    q.dp_data.update(
        state_machine_lines=["a"],
        state_machine_child="react",
        state_machine_lines_1=["b"],
        state_machine_child_2="x",
        other=1,
    )
    machine._abandon(q)
    assert q.dp_data == {"other": 1}
    inner = StateMachinePlugin()
    inner._depth = 1
    q.dp_data.update(state_machine_lines=["keep"], state_machine_lines_1=["drop"])
    inner._abandon(q)
    assert q.dp_data == {"other": 1, "state_machine_lines": ["keep"]}


def test_a_signal_on_an_outcome_that_is_not_there_does_not_end_the_run() -> None:
    table = _fan(r=_region("billing"))
    table["signals"] = [{"when": {"out.score": {"gt": 5}}, "goto": "late"}]
    table["states"]["late"] = {"run": "end", "result": "late"}
    assert json.loads(_run(_Engine(), **table))["finished"] == ["r"]


def test_moderated_rounds_with_a_version_in_its_text_works_as_a_child() -> None:
    table = _work(
        pattern="moderated_rounds",
        agents=["classifier", "billing"],
        params={
            "decider": "classifier",
            "order": ["billing"],
            "max_rounds": 2,
            "rejected_request": "Version {version} failed.",
        },
    )
    decisions = iter([{"action": "propose", "plan": {"p": 1}}, {"action": "withhold", "reason": "no deal"}])

    def reply(agent: str, task: str) -> str:
        if agent == "classifier" and '"action"' in task:
            return json.dumps(next(decisions))
        return "reject: no" if "confirm" in task.lower() and "plan version" in task.lower() else "ok"

    engine = _Engine(reply)
    _run(engine, **table)
    assert any("Version 1 failed." in args["task"] for _, args in engine.calls)


def test_a_type_error_while_running_ends_the_run_with_a_message() -> None:
    table = _one(on=[{"set": {"n": {"add": "x"}}, "goto": "z"}])
    out = _run(_Engine(), **table)
    assert out.startswith("state_machine: state 'a': unsupported operand")


def test_a_call_the_kernel_skipped_or_blocked_fails_the_state_not_a_reply() -> None:
    table = _loop()
    machine, config = _machine(table)
    note = RunEvent(
        correlation_id=1,
        response_kind="TOOL_RESULT",
        next_step="LLM_CALL",
        text="Tool call suppressed after operator HITL block.",
        placeholder=True,
        seq=1,
    )
    machine._capture.clear()
    machine.region_deliver(note, config)
    st = machine._state()
    assert st.last_out["ok"] is False and st.last_out["skipped"] is True
    assert "was not made: Tool call suppressed" in st.last_out["detail"]
    assert st.last_out["text"] == ""  # the note for the model is not the agent's answer


def test_a_row_of_a_real_reply_is_not_a_placeholder() -> None:
    machine, config = _machine(_loop())
    machine._capture.clear()
    machine.region_deliver(
        RunEvent(correlation_id=1, response_kind="TOOL_RESULT", next_step="STOP", text="fine", seq=1), config
    )
    assert machine._state().last_out["ok"] is True


def test_a_steer_after_a_finished_run_starts_a_new_run_on_the_steer_text() -> None:
    table = _one(input="{task}", on=[{"goto": "z"}])
    table["signals"] = [{"when": {"steps": {"gte": 99}}, "goto": "z"}]  # signals do not change what a steer does
    engine = _Engine(lambda a, t: f"{a} got {t}")
    instance = _instance(engine, **table)
    instance.run_user_text("first")
    trace = instance.feed(
        OperatorSteerReceived(steer_id="s1", context_text="second", sender="ops", source="peer", intent="steer")
    )
    assert [args["task"] for _, args in engine.calls] == ["first", "second"]
    assert trace.client_responses and trace.client_responses[-1].content == "ok"


def _steer(text: str, intent: str = "") -> OperatorSteerReceived:
    return OperatorSteerReceived(steer_id="s1", context_text=text, sender="ops", source="peer", intent=intent)


def _mid_run() -> QProduct:
    """What the kernel holds when a steer arrives during a run that was preempted: not idle."""
    q = QProduct()
    q.dp = DpState.CTX_BUILD
    return q


def test_the_pattern_declares_every_steer_mode_and_leaves_delivery_to_the_runtime() -> None:
    assert StateMachinePlugin.STEER_MODES == frozenset({"after", "preempt", "replace"})


def test_a_steer_during_a_run_is_a_signal_that_moves_the_machine() -> None:
    table = _loop(signals=[{"when": {"signal.intent": "wrap_up"}, "set": {"why": "{signal.text}"}, "goto": "wrap"}])
    table["vars"] = {"why": ""}
    machine, config = _machine(table)
    assert machine._capture and machine._capture[-1].tool_name == "delegate_to_billing"
    assert machine.on_steer(_mid_run(), _steer("please wrap up", "wrap_up"), config=config) == DpState.CTX_BUILD
    machine._capture.clear()
    machine.evaluate_next(machine._scratch, _OneRowNone(), config=config)
    assert machine._finished and machine._result == "wrapped after 2 states; please wrap up"
    assert machine._state().steer is None


def test_a_steer_that_matches_no_signal_starts_the_interrupted_state_again() -> None:
    machine, config = _machine(_loop(signals=[{"when": {"signal.intent": "wrap_up"}, "goto": "wrap"}]))
    machine.on_steer(_mid_run(), _steer("just chatting"), config=config)
    machine._capture.clear()
    machine.evaluate_next(machine._scratch, _OneRowNone(), config=config)
    assert not machine._finished and machine._capture[-1].tool_name == "delegate_to_billing"
    assert machine._state().steer is None and machine._state().entered == 2


def test_without_signals_a_steer_restarts_the_machine_on_the_steer_text() -> None:
    machine, config = _machine(_loop())
    assert machine.on_steer(_mid_run(), _steer("new request"), config=config) == DpState.CTX_BUILD
    assert machine._state().original_task == "new request" and not machine._state().configured


def test_an_idle_kernel_restarts_even_when_the_table_has_signals() -> None:
    machine, config = _machine(_loop(signals=[{"when": {"steps": {"gte": 9}}, "goto": "wrap"}]))
    machine.on_steer(QProduct(), _steer("later"), config=config)  # dp is IDLE: no run to signal
    assert machine._state().original_task == "later" and machine._state().steer is None


class _OneRowNone:
    events: list = []

    def latest(self) -> None:
        return None
