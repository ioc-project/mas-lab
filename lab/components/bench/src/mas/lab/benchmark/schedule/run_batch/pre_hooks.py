#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Level ``pre`` hooks: prepare each level right before the runs beneath it.

``pre`` of a level runs before any run under it starts, and only when a run
beneath it will actually execute (a run served from the trace cache never
reaches the hook):

* ``experiment.pre`` — once, before the first run (``prepare``; not here).
* ``scenario.pre``   — once per scenario, before its first executing run.
* ``item.pre``       — once per item, before its first executing run.
* ``run.pre``        — before every attempt of every executing run.

Steps are expanded for the exact node they belong to — never by scanning
run folders on disk.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from mas.lab.benchmark.schedule.pipeline import (
    PipelineExecutionError,
    _effective_scope,
    make_node,
    node_scope,
    run_pipeline_phase,
)

logger = logging.getLogger(__name__)

_HOOK_SCOPES = ("scenario", "item", "run")


class LevelPreHooks:
    """Runs ``scenario`` / ``item`` / ``run`` ``pre`` steps for one benchmark batch."""

    def __init__(
        self,
        *,
        exp: Any,
        experiment_yaml: Path,
        output_dir: Path,
        specs: list,
        scenario_ids: list[str],
        infra_name: Optional[str],
        step_overrides: Optional[dict],
        data_cache_dir: Optional[Path] = None,
        progress: bool = True,
    ) -> None:
        self._kw = dict(
            exp=exp,
            experiment_yaml=experiment_yaml,
            output_dir=output_dir,
            scenario_ids=list(scenario_ids),
            infra_name=infra_name,
            step_overrides=step_overrides,
            data_cache_dir=data_cache_dir,
            progress=progress,
        )
        self._output_dir = Path(output_dir)
        self._specs = {
            level: [
                s
                for s in specs or []
                if getattr(s, "phase", "post") == "pre" and node_scope(_effective_scope(s)) == node_scope(level)
            ]
            for level in _HOOK_SCOPES
        }
        self._once: dict[tuple[str, str, str], "asyncio.Future[None]"] = {}

    @property
    def active(self) -> bool:
        return any(self._specs.values())

    async def _run_level(self, level: str, node, label: str) -> None:
        try:
            await run_pipeline_phase(
                phase="pre",
                specs=self._specs[level],
                scopes={level},
                nodes={level: [node]},
                label=label,
                **self._kw,
            )
        except PipelineExecutionError as exc:
            raise PipelineExecutionError(f"{level}.pre failed for {label}: {exc}") from exc

    async def _once_per(self, key: tuple[str, str, str], factory: Callable[[], Awaitable[None]]) -> None:
        fut = self._once.get(key)
        if fut is None:
            fut = asyncio.ensure_future(factory())
            self._once[key] = fut
        await fut

    async def before_attempt(self, *, scenario: str, test: str, run: str) -> None:
        """Run the applicable ``pre`` steps for one attempt of ``scenario/test/run``.

        ``scenario`` / ``test`` are folder names (``item3``), as on disk. A failure in
        a scenario or item ``pre`` is raised for every run beneath it.
        """
        if not self.active:
            return
        if self._specs["scenario"]:
            node = make_node("scenario", self._output_dir / scenario, scenario)
            await self._once_per(
                ("scenario", scenario, ""),
                lambda: self._run_level("scenario", node, scenario),
            )
        if self._specs["item"]:
            node = make_node("item", self._output_dir / scenario / test, scenario, test)
            await self._once_per(
                ("item", scenario, test),
                lambda: self._run_level("item", node, f"{scenario}-{test}"),
            )
        if self._specs["run"]:
            node = make_node("run", self._output_dir / scenario / test / run, scenario, test, run)
            await self._run_level("run", node, f"{scenario}-{test}-{run}")
