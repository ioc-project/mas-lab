#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event-driven scheduler with scope gates and resource-class limits."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.keys import RunKey

logger = logging.getLogger(__name__)

Scope = Literal["run", "item", "scenario", "experiment"]
ChildStatus = Literal["ok", "cached", "failed", "skipped"]


@dataclass
class Node:
    name: str
    scope: str
    phase: str
    step: Any
    adapter: LegacyStepAdapter
    depends_on: list[str] = field(default_factory=list)
    parent: str | None = None
    key: RunKey | None = None
    resource: str = "cpu"
    refresh: str = ""
    concurrency: int = 1
    status: str = "pending"
    result: Any = None
    input_digests: list[str] = field(default_factory=list)
    ctx: Any = None


@dataclass
class ScopeGate:
    total: int
    done: int = 0
    statuses: list[ChildStatus] = field(default_factory=list)

    def observe(self, status: ChildStatus) -> bool:
        self.done += 1
        self.statuses.append(status)
        return self.done >= self.total


class ResourcePool:
    def __init__(self, limits: dict[str, int] | None = None) -> None:
        limits = limits or {"judge": 2, "cpu": 8, "io": 8, "net": 8}
        self._sems = {name: asyncio.Semaphore(max(1, n)) for name, n in limits.items()}

    def semaphore(self, resource: str) -> asyncio.Semaphore:
        return self._sems.setdefault(resource, asyncio.Semaphore(8))


class Scheduler:
    def __init__(
        self,
        nodes: list[Node],
        *,
        resources: ResourcePool | None = None,
        on_metric: Callable[[dict], None] | None = None,
        run: Callable[[Node], Any] | None = None,
        max_active: int = 8,
        channel: Any | None = None,
    ) -> None:
        from mas.lab.benchmark.pipeline.fanout import classify_edges

        self.nodes = {n.name: n for n in nodes}
        self.resources = resources or ResourcePool()
        self.on_metric = on_metric
        self._run = run
        self._done: dict[str, asyncio.Event] = {n.name: asyncio.Event() for n in nodes}
        self._started: dict[str, asyncio.Event] = {n.name: asyncio.Event() for n in nodes}
        self._gates: dict[str, ScopeGate] = {}
        self._failed: set[str] = set()
        self._max_active = asyncio.Semaphore(max(1, int(max_active)))
        self._type_sems: dict[str, asyncio.Semaphore] = {}
        self._channel = channel
        self.edges = classify_edges(nodes)

    def mark_done(self, name: str, status: str = "ok") -> None:
        node = self.nodes.get(name)
        if node is None:
            return
        node.status = status
        started = self._started.get(name)
        if started is not None:
            started.set()
        event = self._done.get(name)
        if event is not None:
            event.set()
        self._kick_dependents(name)

    def set_gate(self, parent: str, total: int) -> None:
        self._gates[parent] = ScopeGate(total=total)

    async def mark_run_terminal(self, key: RunKey, status: ChildStatus) -> None:
        item = f"item:{key.scenario}/{key.item}"
        gate = self._gates.get(item)
        fired = False
        if gate is not None:
            fired = gate.observe(status)
            if self.on_metric:
                self.on_metric(
                    {
                        "kind": "scope.progress",
                        "scope": "item",
                        "done": gate.done,
                        "total": gate.total,
                        "partial": True,
                    }
                )
        for node in self.nodes.values():
            if node.scope == "run" and node.key and node.key.stream_id() == key.stream_id():
                if node.phase == "post" or node.phase == "":
                    await self._maybe_start(node, child_status=status)
        if fired:
            await self._fire_parent(item, "item")

    async def _fire_parent(self, gate_id: str, scope: str) -> None:
        for node in self.nodes.values():
            if node.parent == gate_id or (
                node.scope in {scope, "test", "item"} and node.parent == gate_id
            ):
                await self._maybe_start(node)
        if scope == "item" and gate_id.startswith("item:"):
            scenario = gate_id.split(":", 1)[1].split("/")[0]
            next_id = f"scenario:{scenario}"
            gate = self._gates.get(next_id)
            if gate is not None and gate.observe("ok"):
                if self.on_metric:
                    self.on_metric(
                        {
                            "kind": "scope.progress",
                            "scope": "scenario",
                            "done": gate.done,
                            "total": gate.total,
                            "partial": True,
                        }
                    )
                await self._fire_parent(next_id, "scenario")
        elif scope == "scenario":
            gate = self._gates.get("experiment")
            if gate is not None and gate.observe("ok"):
                if self.on_metric:
                    self.on_metric(
                        {
                            "kind": "scope.progress",
                            "scope": "experiment",
                            "done": gate.done,
                            "total": gate.total,
                            "partial": False,
                        }
                    )
                await self._fire_parent("experiment", "experiment")

    async def _maybe_start(self, node: Node, child_status: str | None = None) -> None:
        if node.status not in {"pending"}:
            return
        from mas.lab.benchmark.pipeline.fanout import folds_batches, wait_for_start

        fold_run = folds_batches(node.step) and node.scope == "run" and node.key is not None
        if node.parent and not fold_run:
            gate = self._gates.get(node.parent)
            if gate is not None and gate.done < gate.total:
                return
        consumer_inc = folds_batches(node.step) and (
            len(node.depends_on) == 1 or (node.key is not None and not node.depends_on)
        )
        for dep in node.depends_on:
            if wait_for_start(self.edges, dep, node.name, consumer_inc):
                event = self._started.get(dep)
            else:
                event = self._done.get(dep)
            if event is not None and not event.is_set():
                await event.wait()
            if dep in self._failed:
                node.status = "skipped"
                self._started[node.name].set()
                self._done[node.name].set()
                self._kick_dependents(node.name)
                return
        node.status = "running"
        self._started[node.name].set()
        self._kick_dependents(node.name)
        sem = self.resources.semaphore(node.resource)
        type_lim = max(1, int(node.concurrency or 1))
        type_sem = None
        if type_lim > 1:
            type_key = str(node.step.type or node.name)
            type_sem = self._type_sems.setdefault(type_key, asyncio.Semaphore(type_lim))
        async with self._max_active:
            async with sem:
                if type_sem is not None:
                    async with type_sem:
                        await self._run_body(node)
                else:
                    await self._run_body(node)
        self._done[node.name].set()
        self._kick_dependents(node.name)

    async def _run_body(self, node: Node) -> None:
        try:
            if node.refresh == "progressive" and hasattr(node.step, "partial"):
                metrics = node.step.partial(None, [])
                if self.on_metric:
                    for rec in metrics or []:
                        self.on_metric({**rec, "partial": True})
            node.result = await self._execute_node(node)
            node.status = "ok"
        except Exception as exc:
            from mas.lab.benchmark.pipeline.executor import is_fatal_pipeline_error

            if is_fatal_pipeline_error(exc):
                raise
            logger.error("v2 node %s failed: %s", node.name, exc)
            node.status = "failed"
            node.result = exc
            self._failed.add(node.name)

    async def _execute_node(self, node: Node) -> Any:
        from mas.lab.benchmark.pipeline.channel import get_channel
        from mas.lab.benchmark.pipeline.fanout import folds_batches, pump_stream

        channel = self._channel if self._channel is not None else get_channel()
        if self._run is not None:
            return await self._run(node)
        ctx = node.ctx
        stream_id = _fold_stream_id(node)
        if (
            channel is not None
            and folds_batches(node.step)
            and stream_id
            and _should_pump(channel, node, stream_id, live=bool(self._gates))
        ):
            return await pump_stream(
                channel,
                stream_id,
                node.step,
                ctx,
                key=node.key,
            )
        return await node.adapter.execute(
            ctx, key=node.key or RunKey("e", "s", "i", "r1")
        )

    def _kick_dependents(self, name: str) -> None:
        for other in self.nodes.values():
            if other.status == "pending" and name in other.depends_on:
                asyncio.create_task(self._maybe_start(other))

    async def drain(self) -> dict[str, str]:
        from mas.lab.benchmark.pipeline.fanout import folds_batches

        live = bool(self._gates)
        for node in self.nodes.values():
            if node.status != "pending" or node.depends_on:
                continue
            is_run = node.scope == "run" and node.key is not None
            if live and is_run:
                if folds_batches(node.step):
                    asyncio.create_task(self._maybe_start(node))
                continue
            gated = bool(node.parent and node.parent in self._gates)
            if not gated:
                asyncio.create_task(self._maybe_start(node))
        await asyncio.gather(*(ev.wait() for ev in self._done.values()), return_exceptions=True)
        return {name: node.status for name, node in self.nodes.items()}

    def progressive_never_persists(self, node: Node) -> bool:
        return node.refresh == "progressive"


def _fold_stream_id(node: Node) -> str | None:
    """Stream a fold node consumes: the sole upstream, or the run's own channel."""
    if len(node.depends_on) == 1:
        return node.depends_on[0]
    if node.key is not None and not node.depends_on:
        return node.key.stream_id()
    return None


def _should_pump(channel: Any, node: Node, stream_id: str, *, live: bool) -> bool:
    """Pump when an upstream is streaming, the run is live, or the stream is sealed."""
    if len(node.depends_on) == 1:
        return True
    if live and node.key is not None and not node.depends_on:
        return True
    return bool(channel.is_sealed(stream_id) or channel.replay(stream_id))


def scope_parent_id(scope: str, cfg: dict[str, Any] | None, key: RunKey | None) -> str | None:
    """Parent gate id for a node. Gates themselves are installed from plan keys."""
    cfg = cfg or {}
    if scope == "run" and key is not None:
        return f"item:{key.scenario}/{key.item}"
    if scope in {"item", "test"}:
        scenario = cfg.get("scenario")
        item = cfg.get("test") or cfg.get("item")
        if scenario and item:
            return f"item:{scenario}/{item}"
    if scope == "scenario":
        scenario = cfg.get("scenario")
        if scenario:
            return f"scenario:{scenario}"
    if scope in {"experiment", "application"}:
        return "experiment"
    return None


def install_scope_gates(sched: Scheduler, keys: list[RunKey] | None) -> None:
    """MAS-completion barriers: item ← runs, scenario ← items, experiment ← scenarios.

    Artifact dataflow still uses ``depends_on`` (and stream seal). Gates only
    mean “every child *run* at this folder is terminal”, so an item gather
    that reads run-scope post still waits on those step names.
    """
    if not keys:
        return
    item_runs: dict[str, int] = {}
    items_per_scenario: dict[str, set[str]] = {}
    scenarios: set[str] = set()
    for key in keys:
        item_id = f"item:{key.scenario}/{key.item}"
        item_runs[item_id] = item_runs.get(item_id, 0) + 1
        items_per_scenario.setdefault(key.scenario, set()).add(key.item)
        scenarios.add(key.scenario)
    for item_id, total in item_runs.items():
        sched.set_gate(item_id, total)
    for scenario, items in items_per_scenario.items():
        sched.set_gate(f"scenario:{scenario}", len(items))
    sched.set_gate("experiment", max(1, len(scenarios)))
    for node in sched.nodes.values():
        if node.parent:
            continue
        cfg = node.step.config or {}
        parent = scope_parent_id(node.scope, cfg, node.key)
        if parent:
            node.parent = parent
