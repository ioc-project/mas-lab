#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""State machine — a design pattern written as data.

The entry agent's behaviour is a table of named states. Each state runs one
*child* and ends with an outcome; the first matching transition of the state
picks the next state. A run ends in an ``end`` state or fails closed.

Children (``run``):

* ``delegate``: send ``input`` to the agents of ``agents``, one at a time, and
  read each reply (``read``). Each agent can itself run any design pattern.
* ``pattern``: any registered design pattern (``pattern: plan_execute``, ``react``,
  ``moderated_rounds``, another ``state_machine``...) run inside the state with
  ``params``, ``input`` as its task and ``agents`` as the agents it may delegate
  to. Its final answer is the state's outcome (``read`` can parse it).
  ``run: react`` is short for ``run: pattern`` with ``pattern: react``.
* ``parallel``: ``regions``, each a small state table of its own, advance at the
  same time (their delegations and tool calls go out as one parallel batch);
  ``join`` is ``all`` or ``any``.
* ``tool``: call one tool.
* ``end``: finish the run with ``result``.

``signals`` are transitions that can fire from any state: at every state
boundary, and after every round of a ``parallel`` state, they are tried before
the state's own ``on`` rows. They see ``elapsed`` (seconds), ``steps``, ``state``,
the variables, the outcome ``out`` at a boundary and ``regions`` in a parallel
state.

Steer. The runtime owns delivery: ``after`` queues the steer for the end of the
run, ``preempt`` and ``replace`` act on a model call that is in flight (and a
steer that finds no model call in flight is queued by the runtime). The pattern
only says what a steer means (:meth:`on_steer`). With ``signals``, a steer that
arrives during a run is kept as ``signal`` (``signal.text``, ``signal.sender``,
``signal.source``, ``signal.intent``) and the run goes on: a matching signal moves
the machine, otherwise the interrupted state starts again. Without ``signals``,
or when no run is going on, a steer restarts the machine on the steer text, like
the other deterministic patterns. ``replace`` always starts a new run.

Nothing here knows an agent name, a protocol or a vocabulary. Agents, texts,
limits and the state graph are all params (``states``, ``start``, ``vars``,
``signals``, ``max_steps``). :mod:`.moderated_rounds` is one such table.

The run state (current state, variables, public transcript) is plain data. It
lives on the plugin for the turn, and the task text of the current child is
mirrored to ``q.dp_data`` for the prompt (``protocol_lines``, which also adds the
child's own lines).

Templates see the run's variables, ``task`` (the user's text), ``transcript``,
``steps``, ``out`` (the outcome of the state that just ended; in ``on`` only) and
``last`` (that outcome, in the next state's templates).

Fail closed: an unreadable reply is asked again ``retry`` times, then the
state's outcome has ``ok: false``. A state with no matching transition, a
missing variable, an unknown agent or pattern and more than ``max_steps`` state
entries all end the run with a ``state_machine: ...`` message. No guess decides
anything.
"""

from __future__ import annotations

import dataclasses
import json
import operator
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.egress_gate import emit_scheduled_egress, schedule_tool_egress
from mas.runtime.kernel.parallel_tools import schedule_parallel_tools_egress
from mas.runtime.kernel.state import DpState, QProduct, RunEvent, RunLedger
from mas.runtime.registry import get_registry
from mas.runtime.schema.egress import EgressSymbol, EmitClientResponse
from mas.runtime.schema.ingress import ToolCallSpec
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator, model_validator

from .base import _DeterministicBase, _TurnState

DEFAULT_MAX_STEPS = 50
MAX_DEPTH = 4  # a state machine run as a child of a state machine, and so on

# Names the engine fills in itself; a table's own variables may not use them.
RESERVED_NAMES = frozenset(
    {
        "task",
        "steps",
        "state",
        "elapsed",
        "transcript",
        "transcript_rows",
        "out",
        "last",
        "signal",
        "item",
        "agent",
        "error",
        "regions",
    }
)
# What an agent's reply may not overwrite in an outcome.
RESERVED_REPLY_KEYS = frozenset(
    {"agent", "text", "ok", "replies", "counts", "detail", "unreadable", "regions", "finished", "unfinished"}
)
MAX_REPLY_CHARS = 200_000  # a longer reply is not read

RUN_KINDS = ("delegate", "react", "pattern", "parallel", "tool", "end")
REGION_KINDS = ("delegate", "tool", "end")  # what a region can run: calls that can go out as one batch
JOIN_MODES = ("all", "any")
STATE_KEYS = {
    "delegate": {
        "run",
        "agents",
        "input",
        "read",
        "retry",
        "instruct",
        "error_detail",
        "log",
        "log_from",
        "log_body",
        "log_reply",
        "on",
    },
    "react": {"run", "input", "instructions", "on"},
    "pattern": {"run", "pattern", "params", "with", "input", "instructions", "agents", "read", "on"},
    "parallel": {"run", "regions", "join", "on"},
    "tool": {"run", "tool", "args", "on"},
    "end": {"run", "result"},
}
TRANSITION_KEYS = {"when", "set", "goto"}
OPERATORS = ("eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "exists")
LOG_MODES = ("none", "each", "after_all")
FILTERS = ("json", "without", "with_transcript", "or")

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w.]*)((?:\|\w+(?::[\w.]+)?)*)\}")
_FILTER = re.compile(r"\|(\w+)(?::([\w.]+))?")
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


class MachineError(Exception):
    """The machine cannot continue; the run ends with this message."""


# --- definition ---------------------------------------------------------------


def normalize_table(defn: Any) -> Any:
    """The table as the engine reads it.

    YAML 1.1 (what ``yaml.safe_load`` reads) turns an unquoted ``on`` key into
    the boolean ``True``; it is the key ``on`` here. Regions are normalized too.
    """
    if not isinstance(defn, dict):
        return defn
    states = defn.get("states")
    if not isinstance(states, dict):
        return defn
    fixed: dict[str, Any] = {}
    for name, spec in states.items():
        if isinstance(spec, dict):
            spec = {("on" if key is True else key): value for key, value in spec.items()}
            if isinstance(spec.get("regions"), dict):
                spec["regions"] = {n: normalize_table(t) for n, t in spec["regions"].items()}
        fixed[name] = spec
    return {**defn, "states": fixed}


def validate_definition(defn: dict[str, Any], *, kinds: tuple[str, ...] = RUN_KINDS) -> list[str]:
    """Every problem with a state table (empty when it is well formed)."""
    defn = normalize_table(defn)
    problems: list[str] = []
    states = defn.get("states")
    if not isinstance(states, dict) or not states:
        return ["states must be a non-empty mapping"]
    start = defn.get("start")
    if not isinstance(start, str) or start not in states:
        problems.append(f"start {start!r} is not a state")
    if not any(isinstance(s, dict) and s.get("run") == "end" for s in states.values()):
        problems.append("no state has run: end")
    for key in ("max_steps",):
        if key in defn and not (isinstance(defn[key], int) and not isinstance(defn[key], bool) and defn[key] >= 1):
            problems.append(f"{key} must be a whole number of at least 1")
    if not isinstance(defn.get("vars", {}), dict):
        problems.append("vars must be a mapping")
    else:
        reserved = sorted(set(defn.get("vars") or {}) & RESERVED_NAMES)
        if reserved:
            problems.append(f"vars {reserved} are names the engine fills in; pick other names")
    for name, spec in states.items():
        if not isinstance(spec, dict) or spec.get("run") not in kinds:
            problems.append(f"state {name!r}: run must be one of {list(kinds)}")
            continue
        kind = spec["run"]
        unknown = sorted(str(k) for k in set(spec) - STATE_KEYS[kind])
        if unknown:
            problems.append(f"state {name!r} ({kind}): unknown keys {unknown}")
        required = {
            "delegate": ("agents", "input"),
            "react": ("input",),
            "pattern": ("pattern", "input"),
            "parallel": ("regions",),
            "tool": ("tool",),
            "end": ("result",),
        }[kind]
        problems += [f"state {name!r} ({kind}): {key} is required" for key in required if key not in spec]
        if kind in ("delegate", "pattern"):
            problems += [f"state {name!r}: {p}" for p in _read_problems(spec.get("read"))]
        if kind == "delegate":
            if spec.get("log", "none") not in LOG_MODES:
                problems.append(f"state {name!r}: log must be one of {list(LOG_MODES)}")
            retry = spec.get("retry", 0)
            if not (isinstance(retry, int) and not isinstance(retry, bool) and retry >= 0):
                problems.append(f"state {name!r}: retry must be a whole number, 0 or more")
        if kind == "pattern":
            problems += [
                f"state {name!r}: {key} must be a mapping"
                for key in ("params", "with")
                if not isinstance(spec.get(key, {}), dict)
            ]
        if kind == "tool" and not isinstance(spec.get("args", {}), dict):
            problems.append(f"state {name!r}: args must be a mapping")
        if kind == "parallel":
            problems += _region_problems(name, spec)
        if kind == "end":
            continue
        problems += _transition_problems(f"state {name!r}", spec.get("on"), states)
    problems += _transition_problems("signals", defn.get("signals"), states, required=False)
    return problems


def _read_problems(read: Any) -> list[str]:
    if read in (None, "text"):
        return []
    if not isinstance(read, dict) or len(read) != 1 or not set(read) <= {"json", "word"}:
        return ["read must be 'text', {json: {fields: ...}} or {word: [...]}"]
    if "word" in read:
        words = read["word"]
        ok = isinstance(words, list) and words and all(isinstance(w, str) and w for w in words)
        return [] if ok else ["read.word must be a non-empty list of words"]
    spec = read["json"]
    if not isinstance(spec, dict) or set(spec) - {"fields"} or not isinstance(spec.get("fields", {}), dict):
        return ["read.json must be {fields: {name: rule}}"]
    problems: list[str] = []
    fields = spec.get("fields") or {}
    for field_name, rule in fields.items():
        if not isinstance(field_name, str) or not _FIELD_NAME.match(field_name) or hasattr(BaseModel, field_name):
            problems.append(
                f"read field {field_name!r}: use a plain name (letters, digits, underscore), "
                "not one that pydantic models already have"
            )
        elif field_name in RESERVED_REPLY_KEYS:
            problems.append(f"read field {field_name!r}: the engine fills in this name; pick another")
        elif not isinstance(rule, dict) or set(rule) - FIELD_KEYS:
            problems.append(f"read field {field_name!r}: rule keys must be among {sorted(FIELD_KEYS)}")
        elif rule.get("type", "any") not in _TYPES:
            problems.append(f"read field {field_name!r}: type must be one of {list(_TYPES)}")
        elif "enum" in rule and not (isinstance(rule["enum"], list) and rule["enum"]):
            problems.append(f"read field {field_name!r}: enum must be a non-empty list")
        elif not isinstance(rule.get("required_if", {}), dict):
            problems.append(f"read field {field_name!r}: required_if must be a mapping")
        elif set(rule.get("required_if") or {}) - (set(fields) - {field_name}):
            problems.append(f"read field {field_name!r}: required_if must name other declared fields")
    return problems


def _region_problems(name: str, spec: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if spec.get("join", "all") not in JOIN_MODES:
        problems.append(f"state {name!r}: join must be one of {list(JOIN_MODES)}")
    regions = spec.get("regions")
    if not isinstance(regions, dict) or not regions:
        return [*problems, f"state {name!r}: regions must be a non-empty mapping"]
    for region, table in regions.items():
        if not isinstance(table, dict):
            problems.append(f"state {name!r}: region {region!r} must be a state table")
            continue
        extra = sorted(set(table) - {"start", "vars", "states", "max_steps"})
        if extra:
            problems.append(f"state {name!r}: region {region!r} has unknown keys {extra}")
        problems += [f"state {name!r}: region {region!r}: {p}" for p in validate_definition(table, kinds=REGION_KINDS)]
    return problems


def _transition_problems(where: str, rows: Any, states: dict[str, Any], *, required: bool = True) -> list[str]:
    problems: list[str] = []
    if rows is None and not required:
        return []
    if not isinstance(rows, list) or not rows:
        return [f"{where}: {'on' if required else 'it'} must list at least one transition"]
    for row in rows:
        if not isinstance(row, dict):
            problems.append(f"{where}: a transition must be a mapping, not {row!r}")
            continue
        target = row.get("goto")
        if not isinstance(target, str) or target not in states:
            problems.append(f"{where}: transition goto {target!r} is not a state")
            continue
        extra = sorted(str(k) for k in set(row) - TRANSITION_KEYS)
        if extra:
            problems.append(f"{where}: transition has unknown keys {extra}")
        for key in ("when", "set"):
            if not isinstance(row.get(key, {}) or {}, dict):
                problems.append(f"{where}: transition {key} must be a mapping")
        when = row.get("when") if isinstance(row.get("when"), dict) else {}
        for key, cond in when.items():
            if isinstance(cond, dict) and not set(cond) <= set(OPERATORS):
                problems.append(f"{where}: when {key!r} has unknown operators {sorted(set(cond) - set(OPERATORS))}")
    return problems


# --- templates ----------------------------------------------------------------


def lookup(scope: dict[str, Any], path: str) -> Any:
    """``a.b.c`` through nested mappings; MachineError when a step is missing."""
    value: Any = scope
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise MachineError(f"{path!r} is not defined")
    return value


def _strip_keys(value: Any, hidden: list[str]) -> Any:
    if isinstance(value, dict):
        return {k: _strip_keys(v, hidden) for k, v in value.items() if k not in hidden}
    if isinstance(value, list):
        return [_strip_keys(v, hidden) for v in value]
    return value


def render_transcript(rows: list[dict[str, Any]]) -> str:
    """``[seq] from -> to: body`` lines, the public transcript every party sees."""
    return "\n".join(f"[{row['seq']}] {row['from']} -> {', '.join(row['to'])}: {row['body']}" for row in rows)


def _apply_filter(name: str, arg: str | None, value: Any, scope: dict[str, Any]) -> Any:
    if name == "json":
        return json.dumps(value, indent=2)
    if name == "without":
        hidden = lookup(scope, arg or "") if arg else []
        if not isinstance(hidden, list):
            raise MachineError(f"without:{arg} must name a list")
        return _strip_keys(value, [str(h) for h in hidden])
    if name == "or":
        return value if value not in (None, "", [], {}) else lookup(scope, arg or "")
    if name == "with_transcript":
        rows = scope.get("transcript_rows") or []
        if not rows:
            return value
        return f"Public transcript so far:\n{render_transcript(rows)}\n\nRequest:\n{value}"
    raise MachineError(f"unknown filter {name!r}; use one of {list(FILTERS)}")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def render(template: Any, scope: dict[str, Any]) -> Any:
    """Fill ``{name}`` placeholders.

    A string that is exactly one placeholder gives the value itself (a list
    stays a list). Otherwise each placeholder is replaced by its text. Only
    ``{identifier}`` forms are placeholders, so JSON braces stay literal, and a
    substituted value is never rendered again. Mappings and lists render
    recursively; other values are returned as they are.
    """
    if isinstance(template, dict):
        return {k: render(v, scope) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, scope) for v in template]
    if not isinstance(template, str):
        return template
    whole = _PLACEHOLDER.fullmatch(template)
    if whole:
        return _resolve(whole, scope)
    return _PLACEHOLDER.sub(lambda m: _text(_resolve(m, scope)), template)


def _resolve(match: re.Match[str], scope: dict[str, Any]) -> Any:
    filters = _FILTER.findall(match.group(2))
    try:
        value = lookup(scope, match.group(1))
    except MachineError:
        if not filters or filters[0][0] != "or":
            raise
        value = None  # ``|or:name`` is how a template says the path may be absent
    for name, arg in filters:
        value = _apply_filter(name, arg or None, value, scope)
    return value


# --- guards and updates --------------------------------------------------------


_COMPARE = {
    "eq": operator.eq,
    "ne": operator.ne,
    "in": lambda actual, want: actual in want,
    "not_in": lambda actual, want: actual not in want,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}


def _holds(cond: Any, actual: Any, scope: dict[str, Any]) -> bool:
    if not isinstance(cond, dict):
        return actual == render(cond, scope)
    for op, operand in cond.items():
        want = render(operand, scope)
        if op == "exists":
            ok = bool(want)
            if ok != (actual is not None):
                return False
            continue
        if op in ("in", "not_in") and not isinstance(want, list):
            raise MachineError(f"{op} needs a list, not {want!r}")
        if actual is None and op in ("gt", "gte", "lt", "lte"):
            return False  # a value that is not there is not greater or smaller than anything
        try:
            ok = _COMPARE[op](actual, want)
        except TypeError as exc:
            raise MachineError(f"cannot compare {actual!r} {op} {want!r}") from exc
        if not ok:
            return False
    return True


def matches(when: dict[str, Any] | None, scope: dict[str, Any]) -> bool:
    """Every condition of ``when`` holds. A path that is not defined reads as null."""
    for path, cond in (when or {}).items():
        try:
            actual = lookup(scope, path)
        except MachineError:
            actual = None
        if not _holds(cond, actual, scope):
            return False
    return True


def apply_set(updates: dict[str, Any] | None, variables: dict[str, Any], scope: dict[str, Any]) -> None:
    """``set`` entries: a value, ``{literal: x}`` (kept as written), ``{add: n}``, ``{append: x}``,
    or ``{extend: list, as: item_template}``.

    Entries apply in order, and a later entry sees the earlier ones' new values.
    """
    for name, spec in (updates or {}).items():
        if isinstance(spec, dict) and set(spec) == {"literal"}:
            variables[name] = spec["literal"]  # text kept as written, never filled in
        elif isinstance(spec, dict) and set(spec) & {"add", "append", "extend"}:
            current = variables.get(name)
            if "add" in spec:
                variables[name] = (current or 0) + render(spec["add"], scope)
            elif "append" in spec:
                variables[name] = [*(current or []), render(spec["append"], scope)]
            else:
                items = render(spec["extend"], scope)
                if "as" in spec:
                    items = [render(spec["as"], {**scope, "item": item}) for item in items]
                variables[name] = [*(current or []), *items]
        else:
            variables[name] = render(spec, scope)
        scope[name] = variables[name]


# --- readers -------------------------------------------------------------------


class ReplyError(Exception):
    """A reply cannot be read as declared; the message says why, and goes back to the agent on a retry."""


_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict[str, Any],
    "array": list[Any],
    "any": Any,
}
FIELD_KEYS = {"type", "enum", "optional", "required_if", "description"}


def build_reader(fields: dict[str, Any]) -> type[BaseModel]:
    """The pydantic model of a ``read: {json: {fields: ...}}`` spec.

    A field is required unless it is ``optional`` or has ``required_if:
    {other_field: value}`` (then it is required when ``other_field`` is
    ``value``). ``type`` is ``string``, ``integer``, ``number``, ``boolean``,
    ``object``, ``array`` or ``any``; ``enum`` lists the allowed values. Types
    are strict: ``"3"`` is not an integer and ``true`` is not ``1``. Other
    fields in a reply are kept.
    """
    defs: dict[str, Any] = {}
    validators: dict[str, Any] = {}

    def exact(allowed: list[Any]) -> Any:
        def check_type(value: Any) -> Any:
            equal = [item for item in allowed if value == item]
            if equal and not any(type(value) is type(item) for item in equal):  # True is not 1
                raise ValueError(f"Input should be one of: {', '.join(repr(i) for i in allowed)}")
            return value

        return check_type

    for name, rule in fields.items():
        if "enum" in rule:
            validators[f"enum_{name}"] = field_validator(name, mode="before")(exact(list(rule["enum"])))
        note = rule.get("description")
        kind: Any = Literal[tuple(rule["enum"])] if "enum" in rule else _TYPES[rule.get("type", "any")]
        if rule.get("optional") or "required_if" in rule:
            defs[name] = (Optional[kind], Field(None, description=note))
        else:
            defs[name] = (kind, Field(..., description=note))

    def check(model: BaseModel) -> BaseModel:
        for name, rule in fields.items():
            cond = rule.get("required_if")
            if cond and getattr(model, name) is None and all(getattr(model, k, None) == v for k, v in cond.items()):
                when = " and ".join(f"{k} is {v!r}" for k, v in cond.items())
                raise ValueError(f"{name} is required when {when}")
        return model

    return create_model(
        "Reply",
        __config__=ConfigDict(extra="allow", strict=True),
        __validators__={**validators, "conditional": model_validator(mode="after")(check)},
        **defs,
    )


def _first_errors(exc: ValidationError) -> str:
    rows = []
    for e in exc.errors()[:3]:
        where = ".".join(str(p) for p in e["loc"])
        msg = e["msg"].removeprefix("Value error, ")
        rows.append(f"{where}: {msg}" if where else msg)
    return "; ".join(rows)


def _without_titles(schema: Any) -> Any:
    """The JSON Schema minus pydantic's generated ``title`` keys (they only cost tokens)."""
    if isinstance(schema, dict):
        return {k: _without_titles(v) for k, v in schema.items() if k != "title" or not isinstance(v, str)}
    if isinstance(schema, list):
        return [_without_titles(v) for v in schema]
    return schema


def read_json(text: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The first JSON object in ``text`` that fits ``spec['fields']`` (see :func:`build_reader`).

    The reply is tried whole, as the contents of a code fence, and from its
    first ``{``; in each, the first JSON value is the one checked. Raises
    :class:`ReplyError` with the reason when none fits. Fields that are absent or
    null are left out of the result.
    """
    model = build_reader(spec.get("fields") or {})
    if len(text) > MAX_REPLY_CHARS:
        raise ReplyError(f"the reply is too long to read ({len(text)} characters)")
    stripped = text.strip()
    fence = _FENCE.search(stripped)
    candidates = [fence.group(1).strip()] if fence else []
    candidates.append(stripped)
    brace = stripped.find("{")
    if brace >= 0:
        candidates.append(stripped[brace:])
    error = "the reply has no JSON object"
    for candidate in candidates:
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
            if not isinstance(value, dict):
                error = "the JSON in the reply is not an object"
                continue
            return {k: v for k, v in model.model_validate(value).model_dump().items() if v is not None}
        except ValidationError as exc:
            error = _first_errors(exc)
        except (ValueError, RecursionError):
            continue
    raise ReplyError(error)


def reply_format(read: Any) -> str:
    """The instruction that tells an agent how to answer so that ``read`` can parse it."""
    if "word" in read:
        words = ", ".join(str(w) for w in read["word"])
        return f"Start your reply with one of these words: {words}. Then give your reason."
    model = build_reader(read["json"].get("fields") or {})
    schema = json.dumps(_without_titles(model.model_json_schema()), separators=(",", ":"))
    conditions = [
        f'"{name}" is required when {" and ".join(f"{k} is {v!r}" for k, v in rule["required_if"].items())}.'
        for name, rule in (read["json"].get("fields") or {}).items()
        if rule.get("required_if")
    ]
    extra = f" {' '.join(conditions)}" if conditions else ""
    return f"Answer with ONLY a JSON object that matches this JSON Schema, and no other text.{extra}\n{schema}"


def read_word(text: str, words: list[str]) -> tuple[str, str] | None:
    """``(word, reason)`` from a reply that starts with one of ``words`` (any case), else None.

    The word must stand alone: ``Confirmation denied`` does not start with ``confirm``.
    """
    pattern = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    match = re.match(rf"^\W*({pattern})\b[\s:.,\-*_]*(.*)$", text.strip(), re.I | re.S)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).strip()


# --- the plugin ----------------------------------------------------------------


class _ScopedRun:
    """The run ledger as one child sees it: only the rows added after ``base``.

    Everything else (allocating correlation ids, appending) is the real ledger.
    """

    def __init__(self, run: RunLedger, base: int) -> None:
        self._run = run
        self._base = base

    @property
    def events(self) -> list[RunEvent]:
        return [row for row in self._run.events if row.seq > self._base]

    @property
    def records(self) -> list[RunEvent]:
        return self.events

    def latest(self) -> RunEvent | None:
        rows = self.events
        return max(rows, key=lambda row: row.seq) if rows else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._run, name)


class _OneRow:
    """What a region's machine sees of the ledger: the one reply it is waiting for."""

    def __init__(self, row: RunEvent | None) -> None:
        self.events = [row] if row is not None else []

    def latest(self) -> RunEvent | None:
        return self.events[0] if self.events else None


@dataclass
class _MachineState(_TurnState):
    definition: dict[str, Any] = field(default_factory=dict)
    current: str = ""
    entered: int = 0
    vars: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    awaiting: str = ""
    retries: int = 0
    last_out: dict[str, Any] | None = None
    configured: bool = False
    started: float = 0.0
    child: Any = None  # the design pattern a ``pattern`` state is running
    child_config: Any = None
    child_base: int = 0
    regions: dict[str, StateMachinePlugin] = field(default_factory=dict)
    batch: list[tuple[str, int | None]] = field(default_factory=list)  # region, correlation id, in flight
    turn: int = 0  # how many rounds of regions have been dispatched
    steer: dict[str, Any] | None = None  # a steer that arrived during the run, until a signal or a restart takes it


class StateMachinePlugin(_DeterministicBase):
    """Run the state table in ``design_pattern.params`` (see the module docstring)."""

    plugin_id = "state_machine@v1"
    mode = "state_machine"
    # The runtime delivers (queues, or acts on a model call in flight); this pattern says what a steer means.
    STEER_MODES = frozenset({"after", "preempt", "replace"})
    params_are_templates = True  # when run as a child, ``params`` is a table that fills in its own templates
    raw_params: frozenset[str] = frozenset()  # params a parent leaves as written when ``params_are_templates`` is false
    _clock = staticmethod(time.monotonic)

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0  # how deep this machine is nested in other machines
        self._table: dict[str, Any] | None = None  # set when this machine is one region of a parallel state
        self._inherit: dict[str, Any] = {}
        self._capture: list[ToolCallSpec] | None = None
        self._scratch: QProduct | None = None
        self._failure = ""
        self._result = ""
        self._finished = False

    def _make_state(self) -> _MachineState:
        return _MachineState()

    def _reset_state(self, participants: list[str], task: str) -> _MachineState:  # type: ignore[override]
        self._failure, self._result, self._finished = "", "", False
        return super()._reset_state(participants, task)  # type: ignore[return-value]

    def _state(self) -> _MachineState:  # type: ignore[override]
        return self._turn_state  # type: ignore[return-value]

    def _key(self, name: str) -> str:
        return f"state_machine_{name}" + (f"_{self._depth}" if self._depth else "")

    def protocol_lines(self, q: QProduct | None) -> list[str]:
        """The task text of the child that is running, then that child's own protocol lines."""
        if q is None:
            return []
        lines = [str(line) for line in q.dp_data.get(self._key("lines")) or []]
        name = q.dp_data.get(self._key("child"))
        if name:
            child = _instantiate(str(name))
            if isinstance(child, StateMachinePlugin):
                child._depth = self._depth + 1
            fn = getattr(child, "protocol_lines", None)
            if callable(fn):
                lines += [str(line) for line in fn(q) or []]
        return lines

    # --- definition (overridden by presets) ---

    def definition(self, config: KernelConfig, participants: list[str]) -> dict[str, Any]:
        if self._table is not None:
            return self._table
        spec = getattr(config, "agent_spec", None) or {}
        binding = spec.get("design_pattern") if isinstance(spec, dict) else None
        params = binding.get("params") if isinstance(binding, dict) else None
        return dict(params) if isinstance(params, dict) else {}

    # --- turn start ---

    def _configure(self, st: _MachineState, config: KernelConfig) -> str | None:
        """Load and check the table, fill the variables; return an error message or None."""
        try:
            defn = normalize_table(self.definition(config, st.participants))
        except (ValueError, TypeError) as exc:
            return f"state_machine: {exc}"
        problems = validate_definition(defn, kinds=REGION_KINDS if self._table is not None else RUN_KINDS)
        if problems:
            return f"state_machine: {problems[0]}"
        st.definition = defn
        try:
            variables: dict[str, Any] = dict(self._inherit)
            apply_set(defn.get("vars"), variables, {**self._inherit, "task": st.original_task})
        except MachineError as exc:
            return f"state_machine: vars: {exc}"
        st.vars = variables
        unknown = sorted(set(self._static_agents(defn, variables)) - set(st.participants))
        if unknown:
            return f"state_machine: {unknown} are not agents this entry can delegate to ({st.participants})."
        for name in sorted(self._static_patterns(defn)):
            if get_registry().resolve_by_type("design_pattern", name) is None:
                return f"state_machine: pattern {name!r} is not a registered design pattern."
        st.current = ""
        st.started = self._clock()
        st.configured = True
        return None

    @classmethod
    def _static_agents(cls, defn: dict[str, Any], variables: dict[str, Any]) -> list[str]:
        """Agents the table names with a literal list or a variable known at the start."""
        out: list[str] = []
        for spec in defn["states"].values():
            for table in (spec.get("regions") or {}).values():
                out += cls._static_agents(table, variables)
            try:
                agents = render(spec.get("agents"), variables)
            except MachineError:
                continue  # depends on a state's outcome; checked when the state runs
            if isinstance(agents, list):
                out += [str(a) for a in agents]
        return out

    @classmethod
    def _static_patterns(cls, defn: dict[str, Any]) -> set[str]:
        names = {"react"} if any(s.get("run") == "react" for s in defn["states"].values()) else set()
        return names | {str(s["pattern"]) for s in defn["states"].values() if s.get("run") == "pattern"}

    def _scope(self, st: _MachineState, out: dict[str, Any] | None = None) -> dict[str, Any]:
        scope: dict[str, Any] = {
            **st.vars,
            "task": st.original_task,
            "steps": st.entered,
            "state": st.current,
            "elapsed": round(self._clock() - st.started, 3),
            "transcript": render_transcript(st.transcript),
            "transcript_rows": st.transcript,
        }
        if st.last_out is not None:
            scope["last"] = st.last_out
        if out is not None:
            scope["out"] = out
        if st.steer is not None:
            scope["signal"] = st.steer
        return scope

    # --- driver ---

    def evaluate_next(self, q: QProduct, run: RunLedger, *, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        if not st.participants:
            return self._no_participants(q)
        try:
            if not st.configured:
                error = self._configure(st, config)
                if error:
                    return self._stop(q, error, failed=True)
                return self._enter(q, run, config, st.definition["start"])
            if st.steer is not None:
                return self._on_steer_signal(q, run, config)
            return self._on_return(q, run, config)
        except (MachineError, ValueError, TypeError) as exc:
            return self._stop(q, f"state_machine: state {st.current!r}: {exc}", failed=True)

    def on_steer(self, ctx: QProduct, event: object, *, config: KernelConfig) -> DpState:
        """A steer during a run, with ``signals``, is a signal; otherwise the machine restarts on its text."""
        st = self._state()
        running = st.configured and not self._finished and ctx.dp != DpState.IDLE
        if not (running and st.definition.get("signals")):
            return super().on_steer(ctx, event, config=config)
        st.steer = {
            "text": str(getattr(event, "context_text", "") or ""),
            "sender": str(getattr(event, "sender", "") or ""),
            "source": str(getattr(event, "source", "") or ""),
            "intent": str(getattr(event, "intent", "") or ""),
        }
        return DpState.CTX_BUILD

    def _on_steer_signal(self, q: QProduct, run: Any, config: KernelConfig) -> list[EgressSymbol]:
        """A steer arrived: a matching signal moves the machine; otherwise the interrupted state starts again."""
        st = self._state()
        scope = self._scope(st)
        row = self._fire(st, scope)
        target = st.current
        if row is not None:
            apply_set(row.get("set"), st.vars, scope)
            target = row["goto"]
        st.steer = None
        self._abandon(q)
        return self._enter(q, run, config, target)

    def _fire(self, st: _MachineState, scope: dict[str, Any]) -> dict[str, Any] | None:
        for row in st.definition.get("signals") or []:
            if matches(row.get("when"), scope):
                return row
        return None

    def _abandon(self, q: QProduct) -> None:
        """Drop whatever the current state was running."""
        st = self._state()
        st.child, st.child_config, st.regions, st.batch = None, None, {}, []
        for key in list(q.dp_data):
            for base in ("state_machine_lines", "state_machine_child"):
                if key == base or key.startswith(f"{base}_"):
                    depth = int(key.rsplit("_", 1)[1]) if key != base else 0
                    if depth >= self._depth:  # this machine's keys and those of anything nested in it
                        q.dp_data.pop(key, None)

    def _stop(self, q: QProduct, message: str, *, failed: bool = False) -> list[EgressSymbol]:
        self._abandon(q)
        self._finished = True
        self._result = message
        if failed:
            self._failure = message
        return self._finish(q, message)

    def _dispatch(
        self, q: QProduct, run: Any, config: KernelConfig, name: str, args: dict[str, Any]
    ) -> list[EgressSymbol]:
        """Send one tool call; a region only records it (its parallel state sends the batch)."""
        if self._capture is not None:
            self._capture.append(ToolCallSpec(tool_name=name, tool_arguments=dict(args)))
            return []
        q.pending_tool_name = name
        q.pending_tool_args = dict(args)
        return schedule_tool_egress(q, run, config)

    def _enter(self, q: QProduct, run: Any, config: KernelConfig, name: str) -> list[EgressSymbol]:
        st = self._state()
        limit = int(st.definition.get("max_steps") or DEFAULT_MAX_STEPS)
        if st.entered >= limit:
            raise MachineError(f"step limit of {limit} state entries reached")
        st.entered += 1
        st.current = name
        st.results, st.retries, st.awaiting = [], 0, ""
        self._abandon(q)
        spec = st.definition["states"][name]
        kind = spec["run"]
        scope = self._scope(st)
        if kind == "end":
            result = render(spec["result"], scope)
            return self._stop(q, result if isinstance(result, str) else json.dumps(result, indent=2))
        if kind == "tool":
            st.awaiting = "<tool>"
            return self._dispatch(
                q, run, config, str(render(spec["tool"], scope)), dict(render(spec.get("args") or {}, scope))
            )
        if kind in ("react", "pattern"):
            return self._start_child(q, run, config, spec, scope)
        if kind == "parallel":
            return self._start_regions(q, run, config, spec)
        agents = render(spec["agents"], scope)
        if not isinstance(agents, list) or not agents:
            raise MachineError("agents must be a non-empty list")
        unknown = sorted({str(a) for a in agents} - set(st.participants))
        if unknown:
            raise MachineError(f"{unknown} are not agents this entry can delegate to ({st.participants})")
        st.pending = [str(a) for a in agents]
        return self._next_agent(q, run, config, spec)

    # --- pattern child ---

    def _start_child(
        self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any], scope: dict[str, Any]
    ) -> list[EgressSymbol]:
        st = self._state()
        name = "react" if spec["run"] == "react" else str(spec["pattern"])
        if self._depth >= MAX_DEPTH:
            raise MachineError(f"state machines nested deeper than {MAX_DEPTH}")
        task = str(render(spec["input"], scope))
        agents = [str(a) for a in render(spec["agents"], scope)] if "agents" in spec else list(st.participants)
        unknown = sorted(set(agents) - set(st.participants))
        if unknown:
            raise MachineError(f"{unknown} are not agents this entry can delegate to ({st.participants})")
        st.awaiting = f"<{name}>"
        lines = [task]
        if spec.get("instructions"):
            lines.insert(0, str(render(spec["instructions"], scope)))
        q.dp_data[self._key("lines")] = lines
        q.dp_data[self._key("child")] = name
        child = _instantiate(name)
        # A child state machine's params are its table; its templates are its own, not ours.
        params = spec.get("params") or {}
        if not getattr(child, "params_are_templates", False):
            keep = getattr(child, "raw_params", frozenset())
            params = {k: (v if k in keep else render(v, scope)) for k, v in params.items()}
        if isinstance(child, StateMachinePlugin):
            child._depth = self._depth + 1
            child._inherit = dict(render(spec.get("with") or {}, scope))
        reset = getattr(child, "_reset_state", None)
        if callable(reset):
            reset(agents, task)
        spec_dict = dict(getattr(config, "agent_spec", None) or {})
        spec_dict["design_pattern"] = {"type": name, "params": params}
        st.child = child
        st.child_config = dataclasses.replace(config, agent_spec=spec_dict)
        st.child_base = run.max_seq() if hasattr(run, "max_seq") else 0
        view = _ScopedRun(run, st.child_base)
        next_dp, scheduled = child.on_context_complete(q)
        if scheduled is not None:  # an LLM-driven pattern starts with a model call
            q.dp, q.scheduled_egress = next_dp, scheduled
            return emit_scheduled_egress(q, view, st.child_config)
        return self._advance_child(q, run, config, spec)

    def _advance_child(self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any]) -> list[EgressSymbol]:
        st = self._state()
        q.dp = DpState.EVALUATING
        symbols = st.child.evaluate_next(q, _ScopedRun(run, st.child_base), config=st.child_config)
        final = next((s for s in symbols if isinstance(s, EmitClientResponse)), None)
        if final is None:
            return symbols
        q.dp = DpState.EVALUATING
        failure = getattr(st.child, "_failure", "") or (final.content if final.finish_reason == "error" else "")
        if failure:
            return self._complete(q, run, config, {"ok": False, "text": final.content, "detail": failure})
        out: dict[str, Any] = {"ok": True, "text": final.content}
        read = spec.get("read") or "text"
        if read != "text":
            try:
                out.update(self._read_fields(read, final.content))
            except ReplyError as exc:
                detail = f"{st.awaiting} gave no readable answer: {exc}"
                return self._complete(
                    q, run, config, {"ok": False, "text": final.content, "detail": detail, "unreadable": True}
                )
        return self._complete(q, run, config, out)

    # --- parallel regions ---

    def region_start(
        self, table: dict[str, Any], inherit: dict[str, Any], task: str, participants: list[str], config: KernelConfig
    ) -> ToolCallSpec | None:
        """Make this machine one region: it runs ``table`` and only records the call it wants next."""
        self._table, self._inherit = table, dict(inherit)
        self._capture, self._scratch = [], QProduct()
        self._reset_state(participants, task)
        return self._region_step(None, config)

    def region_deliver(self, row: RunEvent | None, config: KernelConfig) -> ToolCallSpec | None:
        """The reply to the call this region asked for; returns the next call it wants (None when done)."""
        return self._region_step(row, config)

    def _region_step(self, row: RunEvent | None, config: KernelConfig) -> ToolCallSpec | None:
        assert self._capture is not None and self._scratch is not None
        self._capture.clear()
        self.evaluate_next(self._scratch, _OneRow(row), config=config)  # type: ignore[arg-type]
        return self._capture[-1] if self._capture and not self._finished else None

    def region_outcome(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": not self._failure, "text": self._result, "vars": dict(self._state().vars)}
        if self._failure:
            out["detail"] = self._failure
        try:
            data = json.loads(self._result)
        except ValueError:
            data = None
        if isinstance(data, dict):
            out["data"] = data
        return out

    def _start_regions(self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any]) -> list[EgressSymbol]:
        st = self._state()
        st.awaiting = "<parallel>"
        st.regions, st.batch, st.turn = {}, [], 0
        for name, table in spec["regions"].items():
            region = StateMachinePlugin()
            region._depth = self._depth + 1
            region.region_start(table, st.vars, st.original_task, st.participants, config)
            st.regions[name] = region
        return self._pump_regions(q, run, config, spec)

    def _pump_regions(self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any]) -> list[EgressSymbol]:
        """Join if the regions are done; else send the next round of their calls."""
        st = self._state()
        done = [name for name, region in st.regions.items() if region._finished]
        if len(done) == len(st.regions) or (spec.get("join", "all") == "any" and done):
            out = {
                "ok": all(st.regions[n].region_outcome()["ok"] for n in done),
                "regions": {n: st.regions[n].region_outcome() for n in done},
                "finished": done,
                "unfinished": [n for n in st.regions if n not in done],
            }
            return self._complete(q, run, config, out)
        scope = {**self._scope(st), "regions": self._region_view()}
        row = self._fire(st, scope)
        if row is not None:
            apply_set(row.get("set"), st.vars, scope)
            return self._enter(q, run, config, row["goto"])
        waiting = [name for name, region in st.regions.items() if not region._finished]
        if config.parallel_tool_calls and len(waiting) > 1:
            chosen = waiting
        else:  # one call at a time, the regions taking turns
            chosen = [waiting[st.turn % len(waiting)]]
        st.turn += 1
        wanted = [self._wanted(st.regions[name]) for name in chosen]
        symbols = schedule_parallel_tools_egress(q, run, config, wanted)
        cids: list[int | None] = list(q.pending_tools_by_cid) if len(wanted) > 1 else [None]
        if len(cids) != len(chosen):  # governance or HITL stopped the batch: hand its answer to the kernel as it is
            st.batch = []
            return symbols
        st.batch = list(zip(chosen, cids, strict=True))
        return symbols

    @staticmethod
    def _wanted(region: StateMachinePlugin) -> ToolCallSpec:
        assert region._capture
        return region._capture[-1]

    def _region_view(self) -> dict[str, Any]:
        st = self._state()
        return {
            name: {
                "state": region._state().current,
                "done": region._finished,
                **(region.region_outcome() if region._finished else {}),
            }
            for name, region in st.regions.items()
        }

    def _on_regions_return(
        self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any]
    ) -> list[EgressSymbol]:
        st = self._state()
        batch, st.batch = st.batch, []
        for name, cid in batch:
            row = run.latest() if cid is None else next((r for r in run.events if r.correlation_id == cid), None)
            st.regions[name].region_deliver(row, config)
        return self._pump_regions(q, run, config, spec)

    # --- delegation ---

    def _next_agent(
        self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any], note: str = ""
    ) -> list[EgressSymbol]:
        st = self._state()
        agent = st.pending[0]
        st.awaiting = agent
        scope = {**self._scope(st), "agent": agent}
        task = str(render(spec["input"], scope))
        if st.retries == 0 and spec.get("log") == "each":
            self._log(st, spec, scope, [agent], str(render(spec.get("log_body", spec["input"]), scope)), sender=True)
        read = spec.get("read") or "text"
        if read != "text" and spec.get("instruct", True):
            task = f"{task}\n\n{reply_format(read)}"
        return self._dispatch(q, run, config, self._delegate_tool_name(agent), {"task": f"{note}{task}"})

    def _log(
        self,
        st: _MachineState,
        spec: dict[str, Any],
        scope: dict[str, Any],
        to: list[str],
        body: str,
        *,
        sender: bool,
        party: str = "",
    ) -> None:
        origin = str(render(spec["log_from"], scope)) if "log_from" in spec else "user"
        row_from, row_to = (origin, to) if sender else (party, [origin])
        st.transcript.append({"seq": len(st.transcript) + 1, "from": row_from, "to": row_to, "body": body.strip()})

    def _on_return(self, q: QProduct, run: Any, config: KernelConfig) -> list[EgressSymbol]:
        st = self._state()
        spec = st.definition["states"][st.current]
        kind = spec["run"]
        if kind in ("react", "pattern"):
            return self._advance_child(q, run, config, spec)
        if kind == "parallel":
            return self._on_regions_return(q, run, config, spec)
        latest = run.latest()
        # Replies already collected stay in the outcome of a state that fails part way.
        got = {"agent": st.awaiting, "replies": list(st.results)}
        if latest is not None and latest.placeholder:  # the kernel skipped or blocked the call: it never ran
            detail = f"the call to {st.awaiting} was not made: {latest.text.strip() or 'skipped'}"
            return self._complete(q, run, config, {"ok": False, "detail": detail, "text": "", "skipped": True, **got})
        if latest is not None and latest.response_kind == "ERROR":
            detail = f"{st.awaiting} failed: {latest.text.strip()}"
            return self._complete(q, run, config, {"ok": False, "detail": detail, "text": latest.text.strip(), **got})
        reply = latest.text.strip() if latest is not None and latest.response_kind == "TOOL_RESULT" else None
        if not reply:  # nothing came back: no row, or a call that was skipped or blocked and has no text
            return self._complete(
                q, run, config, {"ok": False, "detail": f"no reply from {st.awaiting}", "text": "", **got}
            )
        if kind == "tool":
            return self._complete(q, run, config, {"ok": True, "text": reply})
        return self._on_reply(q, run, config, spec, reply)

    def _on_reply(
        self, q: QProduct, run: Any, config: KernelConfig, spec: dict[str, Any], reply: str
    ) -> list[EgressSymbol]:
        st = self._state()
        agent = st.awaiting
        item: dict[str, Any] = {"agent": agent, "text": reply}
        read = spec.get("read") or "text"
        if read != "text":
            try:
                item.update(self._read_fields(read, reply))
            except ReplyError as exc:
                if st.retries < int(spec.get("retry") or 0):
                    st.retries += 1
                    return self._next_agent(q, run, config, spec, note=f"Your last reply could not be used ({exc}). ")
                detail = render(
                    spec.get("error_detail", "{agent} gave no readable reply: {error}"),
                    {**self._scope(st), "agent": agent, "error": str(exc)},
                )
                return self._complete(
                    q,
                    run,
                    config,
                    {
                        "ok": False,
                        "detail": detail,
                        "agent": agent,
                        "text": reply,
                        "unreadable": True,
                        "replies": list(st.results),
                    },
                )
        st.retries = 0
        st.pending.pop(0)
        st.results.append(item)
        if spec.get("log") == "each":
            scope = {**self._scope(st), "item": item}
            self._log(
                st, spec, scope, [], str(render(spec.get("log_reply", "{item.text}"), scope)), sender=False, party=agent
            )
        if st.pending:
            return self._next_agent(q, run, config, spec)
        return self._complete(q, run, config, self._outcome(st, spec))

    @staticmethod
    def _read(read: Any, reply: str) -> dict[str, Any]:
        if "json" in read:
            return read_json(reply, read["json"])
        words = [str(w).lower() for w in read["word"]]
        answer = read_word(reply, words)
        if answer is None:
            raise ReplyError(f"the reply must start with one of: {', '.join(words)}")
        return {"word": answer[0], "reason": answer[1]}

    @classmethod
    def _read_fields(cls, read: Any, reply: str) -> dict[str, Any]:
        """What ``read`` finds in a reply, without the names an outcome keeps for the engine."""
        return {k: v for k, v in cls._read(read, reply).items() if k not in RESERVED_REPLY_KEYS}

    @staticmethod
    def _outcome(st: _MachineState, spec: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "replies": list(st.results), "text": st.results[-1]["text"]}
        out.update({k: v for k, v in st.results[-1].items() if k not in out and k != "agent"})
        words = [r["word"] for r in st.results if "word" in r]
        if words:
            out["counts"] = {w: words.count(w) for w in (str(w).lower() for w in spec["read"]["word"])}
        return out

    def _complete(self, q: QProduct, run: Any, config: KernelConfig, out: dict[str, Any]) -> list[EgressSymbol]:
        """The state ended with ``out``: log after_all, try the signals, pick the transition, enter the next state."""
        st = self._state()
        spec = st.definition["states"][st.current]
        if spec["run"] == "delegate" and spec.get("log") == "after_all" and out.get("ok") and out.get("replies"):
            scope = self._scope(st)
            agents = [r["agent"] for r in out["replies"]]
            self._log(
                st,
                spec,
                scope,
                agents,
                str(render(spec.get("log_body", spec["input"]), {**scope, "agent": ""})),
                sender=True,
            )
            for item in out["replies"]:
                scope = {**self._scope(st), "item": item}
                self._log(
                    st,
                    spec,
                    scope,
                    [],
                    str(render(spec.get("log_reply", "{item.text}"), scope)),
                    sender=False,
                    party=item["agent"],
                )
        scope = self._scope(st, out)
        row = self._fire(st, scope) or next((r for r in spec["on"] if matches(r.get("when"), scope)), None)
        if row is None:
            raise MachineError(f"no transition matches the outcome {json.dumps(out, default=str)[:300]}")
        apply_set(row.get("set"), st.vars, scope)
        st.last_out = out
        return self._enter(q, run, config, row["goto"])


def _instantiate(name: str) -> Any:
    try:
        return get_registry().instantiate_by_type("design_pattern", name)
    except KeyError as exc:
        raise MachineError(f"pattern {name!r} is not a registered design pattern") from exc
