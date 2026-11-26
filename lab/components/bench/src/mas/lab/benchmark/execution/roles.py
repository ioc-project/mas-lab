#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab role contracts: generator, scheduler, executor, retrier, analyzer.

Each role is a small Protocol with no knowledge of the others. They meet only in
:class:`PlannedRun` (what to run) and in the results tree (what ran), both of which have a
file form in :mod:`mas.lab.benchmark.execution.wire`, so any role can run in its own
process or on its own machine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from mas.runtime.registry import get_registry

MAX_SPENT_ATTEMPTS = 1000
"""Cap on an attempt count read from a file, so a hostile count cannot build a huge history."""


class PlannedRun(NamedTuple):
    """One planned execution. A 3-tuple of ``(scenario_id, item, run_idx)``."""

    scenario_id: str
    item: dict
    run_idx: int

    @property
    def item_id(self) -> Any:
        """The item's ``id``: the folder name and the result row key."""
        return self.item.get("id", 0)

    def row(self) -> dict[str, Any]:
        """The JSON form of the run (an RPC reply, a step output)."""
        return {"scenario_id": self.scenario_id, "item": self.item, "run_idx": self.run_idx}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PlannedRun":
        return cls(str(row["scenario_id"]), dict(row.get("item") or {}), int(row.get("run_idx") or 0))

    @property
    def id(self) -> str:
        """Stable id of the run: the ``run_id`` a result row carries."""
        return f"{self.scenario_id}__item{self.item_id}__r{self.run_idx + 1}"


@dataclass(frozen=True)
class DesignContext:
    """Inputs a scenario generator receives. It never reads the experiment file."""

    scenario_ids: list[str]
    dataset_items: list[dict]
    n_runs: int
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduleContext:
    """Inputs a scheduler receives."""

    params: dict[str, Any] = field(default_factory=dict)
    scenario_ids: list[str] = field(default_factory=list)
    parallel_scenarios: int = 4


class ScenarioGenerator(Protocol):
    plugin_id: str
    default_scheduler: str

    def validate_params(self, params: dict[str, Any]) -> None: ...

    def generate(self, ctx: DesignContext) -> list[PlannedRun]: ...


class Scheduler(Protocol):
    plugin_id: str

    def validate_params(self, params: dict[str, Any]) -> None: ...

    def order(self, runs: list[PlannedRun], ctx: ScheduleContext) -> list[PlannedRun]: ...


@dataclass(frozen=True)
class AttemptResult:
    """What one invoke produced. ``fatal`` marks a failure no retry may repair."""

    result: dict
    status: str
    error: str = ""
    output: str = ""
    fatal: bool = False


class Executor(Protocol):
    """Runs a run exactly once. It never retries: a :class:`Retrier` decides that.

    The host owns cache, resume, and ``result.json``.
    """

    plugin_id: str

    def validate_params(self, params: dict[str, Any]) -> None: ...

    def is_complete(self, result_dict: dict, *, status: str, error: str = "") -> bool:
        """True only when this invoke may be reported complete and passed to a batch stage."""

    async def attempt(
        self,
        do_run: Callable[[], dict],
        *,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
    ) -> AttemptResult: ...


@dataclass(frozen=True)
class AttemptRecord:
    """One finished attempt, as a retrier sees it."""

    status: str
    error: str = ""
    fatal: bool = False


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_s: float = 0.0
    reason: str = ""


class Retrier(Protocol):
    """Decides, from the attempts so far, whether a run runs again and after how long.

    Pure: the same history gives the same decision. The batch host calls it between
    attempts inside one process; ``mas-lab benchmark runs retry`` calls it between
    rounds (a CI job re-run, a second dispatch) from the attempts recorded in
    ``result.json``.
    """

    plugin_id: str

    def validate_params(self, params: dict[str, Any]) -> None: ...

    def decide(self, history: Sequence[AttemptRecord]) -> RetryDecision: ...


@dataclass(frozen=True)
class AnalysisContext:
    """Inputs an analyzer receives: where the results are and what was planned."""

    results_root: Path
    runs: list[PlannedRun]
    dir_names: Mapping[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


class Analyzer(Protocol):
    """Reads a results tree and writes a report. It never runs a run.

    Any process that has the merged tree may run it, so analysis does not need the
    machine that executed the runs.
    """

    plugin_id: str

    def validate_params(self, params: dict[str, Any]) -> None: ...

    def analyze(self, ctx: AnalysisContext) -> dict[str, Any]: ...


def reject_unknown_keys(params: dict[str, Any], allowed: set[str], *, plugin_id: str) -> None:
    extra = sorted(set(params or {}) - allowed)
    if extra:
        raise ValueError(f"{plugin_id} params has unknown key {extra[0]!r}")


def resolve_role_plugin(plugin_type: str, plugin_id: str) -> Any:
    """Load a lab role plugin by type and id. Unknown ids fail with the registered list."""
    registry = get_registry()
    info = registry.resolve_by_type(plugin_type, plugin_id)
    if info is None:
        known = registry.list_names(plugin_type)
        raise ValueError(f"unknown {plugin_type.replace('_', ' ')} {plugin_id!r}; registered: {sorted(known)}")
    return info.load_class()()


def validated_plugin(plugin_type: str, plugin_id: str, params: dict[str, Any]) -> Any:
    """Load a role plugin and check its params, as config load does.

    ``validate_params`` is also where a plugin takes its params: the instance returned here is the
    configured one (each load builds a new instance).
    """
    plugin = resolve_role_plugin(plugin_type, plugin_id)
    plugin.validate_params(params)
    return plugin


class NoParams:
    """Mixin for a plugin that takes no params."""

    plugin_id: str

    def validate_params(self, params: dict[str, Any]) -> None:
        reject_unknown_keys(params or {}, set(), plugin_id=self.plugin_id)


def resolve_scenario_generator(plugin_id: str) -> ScenarioGenerator:
    return resolve_role_plugin("scenario_generator", plugin_id)


def resolve_scheduler(plugin_id: str) -> Scheduler:
    return resolve_role_plugin("scheduler", plugin_id)


def resolve_executor(plugin_id: str) -> Executor:
    return resolve_role_plugin("executor", plugin_id)
