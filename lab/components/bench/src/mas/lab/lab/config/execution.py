#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID


@dataclass
class InfraEmulationSpec:
    """L1 infrastructure emulation: which backing resources are live vs controlled.

    In a controlled experiment the researcher varies one parameter while holding
    everything else stable.  When the independent variable lives at a layer above
    infrastructure (e.g. design pattern, governance policy, coordination topology),
    the layers below must be **deterministic**.  ``InfraEmulationSpec`` declares
    how each resource class is handled:

    * ``live``   — real remote call (network latency, non-determinism, cost)
    * ``mock``   — canned in-process response (zero latency, deterministic, free)
    * ``replay`` — play back a recorded response from the trace cache
    * ``stub``   — minimal implementation that satisfies the contract interface
    * ``ephemeral`` — memory that exists only for the duration of a single run

    The lab runner resolves these declarations into concrete plugin bindings
    before each scenario execution.  ``live`` is the default for production;
    ``mock`` / ``replay`` for CI and offline reproducibility.
    """

    llm: str = "live"
    """LLM model access: ``live`` | ``replay``.

    ``replay`` → responses served from llm_cache or the content-addressed trace cache.
    ``live``   → real LLM endpoint.
    """

    tools: str = "live"
    """Tool backends: ``live`` | ``mock`` | ``stub``.

    ``mock`` → tools return fixed synthetic results (e.g. web_search always
    returns a canned snippet).  ``stub`` → tools satisfy the ToolContract
    interface but perform no real work.
    """

    memory: str = "live"
    """Semantic memory & workspace files: ``live`` | ``mock`` | ``snapshot`` | ``seeded`` | ``ephemeral``.

    ``ephemeral``  → in-memory store discarded after each run (InMemoryBackend).
    ``snapshot``   → memory restored from a checkpoint before each run
                     (deterministic starting knowledge base).
    ``seeded``     → memory initialised from a fixture then evolves freely.
    ``mock``       → memory search returns canned results regardless of
                     what was indexed (isolates reasoning from retrieval).
    ``live``       → real persistent storage (production default).
    """

    embeddings: str = "live"
    """Embedding service: ``live`` | ``mock`` | ``replay``.

    ``mock``   → returns a fixed-dimension zero/random vector (deterministic,
                 no network call).  Sufficient when the experiment does not
                 study retrieval quality.
    ``replay`` → embeddings served from a prior recorded trace.
    ``live``   → real embedding endpoint (via flavour).
    """

    state: str = "live"
    """External global state (KG, shared scratchpad, cross-agent blackboard):
    ``live`` | ``snapshot`` | ``seeded`` | ``ephemeral`` | ``mock``.

    Covers any mutable shared state that is *not* the semantic memory store
    (which has its own field).  Examples: knowledge graphs, shared context
    variables, external databases written to by tools.
    """

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InfraEmulationSpec":
        return cls(
            llm=data.get("llm", "live"),
            tools=data.get("tools", "live"),
            memory=data.get("memory", "live"),
            embeddings=data.get("embeddings", "live"),
            state=data.get("state", "live"),
        )


@dataclass
class RuntimeEmulationSpec:
    """L3 execution-engine emulation: how the runtime itself is controlled.

    Controls the runtime execution machinery independently of the resources
    it accesses (L1).  The key mechanism is the **content-addressed trace
    cache**: when ``cache`` is ``content-addressed``, a run that matches a
    previous (manifest, prompt, model, run_idx) fingerprint is skipped
    entirely — the cached trace is symlinked in place.  This is **execution
    replay**, not LLM replay: the entire MAS run (including all delegation,
    tool calls, and governance checks) is replayed from the event stream.

    ``transport`` controls agent-to-agent communication, which can be fully
    emulated in-process (``local``), routed over real gRPC (``grpc``), or
    simulated (``emulated``) with delay injection and message loss.
    """

    transport: str = "local"
    """Agent communication: ``local`` | ``grpc`` | ``emulated``."""

    cache: str = "content-addressed"
    """Trace cache policy: ``content-addressed`` | ``disabled`` | ``forced``.

    ``content-addressed`` — skip runs whose fingerprint already exists
    in the global cache (default, enables incremental experimentation).
    ``disabled`` — always execute live (useful for latency measurement).
    ``forced`` — fail if cache miss (fully offline replay).
    """

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeEmulationSpec":
        return cls(
            transport=data.get("transport", "local"),
            cache=data.get("cache", "content-addressed"),
        )


@dataclass
class PipelineEngineSpec:
    """Stream-first pipeline executor settings. ``engine`` / ``shadow`` on old YAML are ignored."""

    engine: str = "v2"
    shadow: bool = False
    store: str = ""
    streams_transport: str = "inproc"
    max_active: int = 8

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None = None) -> "PipelineEngineSpec":
        data = data or {}
        streams = data.get("streams") or {}
        transport = streams.get("transport") if isinstance(streams, dict) else data.get("transport")
        max_active = data.get("max_active")
        if max_active is None and isinstance(streams, dict):
            max_active = streams.get("max_active")
        return cls(
            engine="v2",
            shadow=False,
            store=str(data.get("store") or ""),
            streams_transport=str(transport or "inproc"),
            max_active=max(1, int(max_active or 8)),
        )

    def store_root(self, cache_root: Path | None = None) -> Path:
        if self.store:
            return Path(self.store).expanduser()
        base = Path(cache_root) if cache_root is not None else Path.home() / ".cache" / "mas-lab"
        return Path(base) / "artefacts"


@dataclass
class InterceptSpec:
    """L3–L6 hook-level interception: MITM and fault injection.

    While overlays define *what* is intercepted (via ``capabilities.mitm``
    rules), this block declares the *experimental intent*: that hook-level
    interception is active and which hooks are targeted.  This makes the
    controlled-experiment design explicit in the experiment YAML, not buried
    in overlay files.

    When ``mitm`` is ``true``, the benchmark runner verifies that at least
    one scenario has a MITM overlay — a safety check that the interception
    is intentional, not accidental.
    """

    mitm: bool = False
    """Whether MITM hook interception is active in this experiment."""

    hooks: List[str] = field(default_factory=list)
    """Optional list of targeted hooks (e.g. ``["on_pre_llm_call"]``).

    Informational — used in experiment metadata and reports.
    """

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InterceptSpec":
        return cls(
            mitm=data.get("mitm", False),
            hooks=data.get("hooks", []),
        )


@dataclass
class EmulationSpec:
    """Declares which layers of the execution stack are live vs controlled.

    The MAS-Lab architecture emulates the full execution engine (which in
    production might be Kubernetes, a gRPC mesh, real LLM endpoints, etc.).
    A controlled experiment requires **varying a single parameter** while
    keeping all other layers stable.  ``EmulationSpec`` makes this explicit:

    * **infra** (L1) — backing resources: LLM, tools, memory, embeddings, external state
    * **runtime** (L3) — execution engine: transport, trace cache
    * **intercept** (L3–L6) — hook-level interception: MITM, fault injection

    Layers not listed here (L2 structural, L5 trajectory, L6 governance,
    L7 interpretability) are controlled via the specification
    itself (overlays, patches) or via post-hoc analysis, not via emulation.

    Example — study the effect of a design-pattern change (L5 independent
    variable) while holding infrastructure deterministic::

        emulation:
          infra:
            llm: replay        # L1: deterministic LLM responses from cache
            tools: mock        # L1: deterministic tool outputs
            memory: snapshot   # L1: memory restored from checkpoint
            embeddings: mock   # L1: fixed embedding vectors
          runtime:
            cache: content-addressed
          intercept:
            mitm: false        # no hook interception in this experiment
    """

    infra: InfraEmulationSpec = field(default_factory=InfraEmulationSpec)
    """L1 infrastructure emulation."""

    runtime: RuntimeEmulationSpec = field(default_factory=RuntimeEmulationSpec)
    """L3 execution-engine emulation."""

    intercept: InterceptSpec = field(default_factory=InterceptSpec)
    """L3–L6 hook-level interception."""

    pipeline: "PipelineEngineSpec" = field(default_factory=lambda: PipelineEngineSpec())
    """Stream-first pipeline executor (artefact store + telemetry channel)."""

    faults: Optional[Dict[str, Any]] = None
    """Optional fault-injection configuration (bench posture, not design)."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmulationSpec":
        return cls(
            infra=InfraEmulationSpec.from_dict(data.get("infra", {})),
            runtime=RuntimeEmulationSpec.from_dict(data.get("runtime", {})),
            intercept=InterceptSpec.from_dict(data.get("intercept", {})),
            pipeline=PipelineEngineSpec.from_dict(data.get("pipeline", {})),
            faults=data.get("faults"),
        )


# ---------------------------------------------------------------------------
# Split specs — design vs schedule vs bench emulation
# ---------------------------------------------------------------------------


@dataclass
class ExperimentScheduleSpec:
    """How mas-lab walks the design matrix.

    Changing these fields never changes the meaning of a result run — only
    wall time, resource usage, and failure modes during collection.
    Timeout, retries, workers, and the application runner live on
    :class:`ExperimentExecutorSpec`.
    """

    plugin: Optional[str] = None
    """Registered ``scheduler`` plugin id. Omitted, the generator's default_scheduler."""

    params: Dict[str, Any] = field(default_factory=dict)
    """Plugin attributes (validated by the scheduler)."""

    parallel_scenarios: int = 4
    """Maximum number of concurrent MAS runs."""

    pause_between_runs: float = 1.0
    """Pause in seconds between runs to let resources settle."""

    def as_plan_dict(self) -> Dict[str, Any]:
        """Shape consumed by :func:`mas.lab.benchmark.execution.plan.build_execution_plan`."""
        data: Dict[str, Any] = {"params": dict(self.params), "parallel_scenarios": self.parallel_scenarios}
        if self.plugin:
            data["plugin"] = self.plugin
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, where: str = "experiment") -> "ExperimentScheduleSpec":
        from mas.lab.benchmark.execution.roles import validated_plugin
        from mas.lab.lab.config.normalize import normalize_schedule, peel_executor_keys

        schedule_src, leftover = peel_executor_keys(dict(data or {}))
        if leftover:
            names = ", ".join(sorted(leftover))
            raise ValueError(f"schedule no longer holds {names}; put them under experiment.executor")
        normalized = normalize_schedule(schedule_src, where=where)
        plugin = normalized.get("plugin")
        params = dict(normalized.get("params") or {})
        if plugin:
            validated_plugin("scheduler", str(plugin), params)
        return cls(
            plugin=str(plugin) if plugin else None,
            params=params,
            parallel_scenarios=normalized.get("parallel_scenarios", 4),
            pause_between_runs=normalized.get("pause_between_runs", 1.0),
        )


@dataclass
class _PluginBlock:
    """A ``{plugin, params}`` block naming one registered role plugin (default ``native``)."""

    BLOCK: ClassVar[str]
    """Experiment key, for messages."""
    ROLE: ClassVar[str]
    """Plugin type the ``plugin`` id is resolved in."""

    plugin: str = "native"
    params: Dict[str, Any] = field(default_factory=dict)
    """Plugin attributes, validated by the plugin at config load."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None, *, where: str = "experiment"):
        from mas.lab.benchmark.execution.roles import validated_plugin
        from mas.lab.lab.config.normalize import normalize_plugin_block

        block = normalize_plugin_block(data, what=cls.BLOCK, where=where, default="native")
        validated_plugin(cls.ROLE, block["plugin"], block["params"])
        return cls(plugin=block["plugin"], params=dict(block["params"]))


@dataclass
class ExperimentRetrySpec(_PluginBlock):
    """Whether a failed run runs again. The executor runs a run once; the retrier decides about
    another attempt, in this process or in a later CI round. ``native``: ``max_attempts``, ``backoff_s``."""

    BLOCK: ClassVar[str] = "retry"
    ROLE: ClassVar[str] = "retrier"


@dataclass
class ExperimentAnalyzerSpec(_PluginBlock):
    """Which analyzer reads the results tree (default ``native``: a completeness summary)."""

    BLOCK: ClassVar[str] = "analyzer"
    ROLE: ClassVar[str] = "analyzer"


@dataclass
class ExperimentExecutorSpec:
    """How each planned run is invoked (retry, timeout, workers, runner).

    Distinct from the application runner id: ``runner`` picks how one MAS is
    started inside a run. ``plugin`` names the lab executor (default ``native``).
    The batch host still owns cache, resume, and result.json.
    """

    plugin: str = "native"
    """Registered ``executor`` plugin id."""

    params: Dict[str, Any] = field(default_factory=dict)
    """Plugin attributes (validated by the executor)."""

    timeout: int = 300
    """Per-run timeout in seconds (worker pool)."""

    runner: str = DEFAULT_LAB_RUNNER_ID
    """Application runner registry id. Not the executor plugin id."""

    reset_state: bool = False
    """Reset emulation/runtime state between runs when true."""

    worker_processes: int = 1
    """Process workers for MAS runs. ``1`` (default) uses threads in this process."""

    worker_max_runs: Optional[int] = None
    """Recycle each worker after this many runs. ``None`` keeps the worker for the batch."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None, *, where: str = "experiment") -> "ExperimentExecutorSpec":
        from mas.lab.benchmark.execution.roles import validated_plugin
        from mas.lab.benchmark.schedule.run_batch.workers import validate_worker_max_runs, validate_worker_processes
        from mas.lab.lab.config.normalize import normalize_executor

        normalized = normalize_executor(data, where=where)
        plugin = str(normalized.get("plugin") or "native")
        params = dict(normalized.get("params") or {})
        validated_plugin("executor", plugin, params)
        timeout_raw = 300 if normalized.get("timeout") is None else normalized["timeout"]
        if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, int) or timeout_raw < 1:
            raise ValueError(f"executor.timeout must be an integer >= 1, got {timeout_raw!r}")
        return cls(
            plugin=plugin,
            params=params,
            timeout=timeout_raw,
            runner=str(normalized.get("runner") or DEFAULT_LAB_RUNNER_ID),
            reset_state=bool(normalized.get("reset_state", False)),
            worker_processes=validate_worker_processes(normalized.get("worker_processes", 1)),
            worker_max_runs=validate_worker_max_runs(normalized.get("worker_max_runs")),
        )


@dataclass
class ExperimentDesignSpec:
    """What conditions are being compared.

    Lives next to ``scenarios[]``, dataset, evaluation, and ``run.n_runs``.
    Changing these fields *does* change the label or meaning of a result run.
    """

    plugin: str = "cartesian"
    """Registered ``scenario_generator`` plugin id."""

    max_executions: Optional[int] = None
    """Fail when the planned grid exceeds this limit."""

    params: Dict[str, Any] = field(default_factory=dict)
    """Plugin attributes (validated by the generator)."""

    def as_plan_dict(self) -> Dict[str, Any]:
        """Shape consumed by :func:`mas.lab.benchmark.execution.plan.build_execution_plan`."""
        data: Dict[str, Any] = {"plugin": self.plugin, "params": dict(self.params)}
        if self.max_executions is not None:
            data["max_executions"] = self.max_executions
        return data

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]], *, where: str = "experiment") -> "ExperimentDesignSpec":
        from mas.lab.benchmark.execution.roles import validated_plugin
        from mas.lab.lab.config.normalize import normalize_design

        normalized = normalize_design(data, where=where)
        plugin = str(normalized.get("plugin") or "cartesian")
        params = dict(normalized.get("params") or {})
        validated_plugin("scenario_generator", plugin, params)
        return cls(
            plugin=plugin,
            max_executions=normalized.get("max_executions"),
            params=params,
        )


def split_legacy_execution(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Map a legacy ``execution:`` mapping onto the split keys.

    ``n_runs`` is dropped — replication belongs under ``run.n_runs``.
    """
    from mas.lab.lab.config.normalize import EXECUTOR_HOST_KEYS, peel_retry_keys

    out: Dict[str, Any] = {}
    schedule: Dict[str, Any] = {}
    executor: Dict[str, Any] = {}
    for key in ("parallel_scenarios", "pause_between_runs"):
        if key in execution:
            schedule[key] = execution[key]
    for key in EXECUTOR_HOST_KEYS:
        if key in execution:
            executor[key] = execution[key]
    if "plugin" in execution:
        schedule["plugin"] = execution["plugin"]
    elif "ordering" in execution:
        schedule["plugin"] = execution["ordering"]
    elif "strategy" in execution:
        schedule["plugin"] = execution["strategy"]
    if schedule:
        out["schedule"] = schedule
    if executor:
        out["executor"] = executor
    _, retry_params = peel_retry_keys(execution)
    if retry_params:
        out["retry"] = {"params": retry_params}
    if execution.get("design"):
        out["design"] = execution["design"]
    emulation = dict(execution.get("emulation") or {})
    if execution.get("faults") is not None:
        emulation["faults"] = execution["faults"]
    if emulation:
        out["bench_emulation"] = emulation
    if execution.get("replay"):
        out["replay"] = execution["replay"]
    return out


# ---------------------------------------------------------------------------
# ExecutionSpec — compatibility view of the split blocks
# ---------------------------------------------------------------------------


@dataclass
class MASExecutionSpec:
    """Compatibility view of split schedule / design / bench emulation.

    Prefer ``MASExperimentConfig.schedule``, ``.design``, ``.executor``, and
    ``.bench_emulation``.  This object is synthesized during dual-read so
    leftover consumers of ``exp.execution`` keep working for one release.
    """

    n_runs: int = 3
    """Replication count.  Canonical home is ``run.n_runs`` (design)."""

    parallel_scenarios: int = 4
    """Maximum number of concurrent MAS runs."""

    timeout: int = 300
    """Per-run timeout in seconds."""

    pause_between_runs: float = 1.0
    """Pause in seconds between runs to let resources settle."""

    scheduler: Optional[str] = None
    """Walk plugin id from ``schedule.plugin`` (compat view)."""

    runner: str = DEFAULT_LAB_RUNNER_ID
    """Application runner registry id."""

    reset_state: bool = False
    """Reset emulation/runtime state between runs when true."""

    design: Optional[Dict[str, Any]] = None
    """Experiment design (scenario generator plugin, params, guards)."""

    replay: Optional["ReplaySpec"] = None
    """Multi-turn replay configuration.  When set, dataset items with a
    ``turns`` list are executed as multi-turn conversations."""

    emulation: EmulationSpec = field(default_factory=EmulationSpec)
    """Layered emulation configuration.

    Declares which layers of the execution stack are live vs controlled
    (mocked / replayed / intercepted).  This is the mechanism that enables
    controlled experiments: vary one parameter, keep the rest stable.
    """

    faults: Optional[Dict[str, Any]] = None
    """Optional fault-injection configuration (legacy ``execution.faults``)."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MASExecutionSpec":
        replay_data = data.get("replay")
        emulation_data = data.get("emulation", {})
        scheduler = data.get("strategy", data.get("ordering", data.get("plugin")))
        return cls(
            n_runs=data.get("n_runs", 3),
            parallel_scenarios=data.get("parallel_scenarios", 4),
            timeout=data.get("timeout", 300),
            pause_between_runs=data.get("pause_between_runs", 1.0),
            scheduler=scheduler or None,
            runner=data.get("runner", DEFAULT_LAB_RUNNER_ID),
            reset_state=bool(data.get("reset_state", False)),
            design=data.get("design"),
            replay=ReplaySpec.from_dict(replay_data) if replay_data else None,
            emulation=EmulationSpec.from_dict(emulation_data),
            faults=data.get("faults"),
        )

    @classmethod
    def from_split(
        cls,
        *,
        schedule: ExperimentScheduleSpec,
        design: ExperimentDesignSpec,
        executor: "ExperimentExecutorSpec",
        bench_emulation: EmulationSpec,
        replay: Optional["ReplaySpec"],
        n_runs: int,
    ) -> "MASExecutionSpec":
        return cls(
            n_runs=n_runs,
            parallel_scenarios=schedule.parallel_scenarios,
            timeout=executor.timeout,
            pause_between_runs=schedule.pause_between_runs,
            scheduler=schedule.plugin,
            runner=executor.runner,
            reset_state=executor.reset_state,
            design=design.as_plan_dict(),
            replay=replay,
            emulation=bench_emulation,
            faults=getattr(bench_emulation, "faults", None),
        )


# ---------------------------------------------------------------------------
# ReplaySpec — multi-turn conversation replay configuration
# ---------------------------------------------------------------------------


@dataclass
class ReplaySpec:
    """Controls how multi-turn dataset items are replayed.

    When a dataset item contains a ``turns`` list, the benchmark runner
    feeds each turn sequentially to the same MAS runtime instance (preserving
    session context).  The ``replay`` block in the experiment YAML controls
    additional replay behaviour.

    Modes
    -----
    ``scripted``  (default)
        Every turn in ``item.turns`` is replayed exactly as written.
        HITL responses are injected from the dataset.

    ``cache-and-diverge``
        Uses the trace-cache from a previous run.  Replays cached turns until
        data diverges, then continues live.

    HITL source
    -----------
    ``dataset``
        HITL responses are read from the ``turns`` list.

    ``recording``
        HITL responses are loaded from a recording file (``mas-runtime run-agent --record``).
    """

    mode: str = "scripted"
    """Replay mode: ``scripted`` or ``cache-and-diverge``."""

    hitl_source: str = "dataset"
    """Source for HITL responses: ``dataset`` or ``recording``."""

    recording_path: Optional[str] = None
    """Path to a recording JSON (when hitl_source='recording')."""

    diverge_from_cache: bool = True
    """In cache-and-diverge mode, continue live after first divergence."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplaySpec":
        return cls(
            mode=data.get("mode", "scripted"),
            hitl_source=data.get("hitl_source", "dataset"),
            recording_path=data.get("recording_path"),
            diverge_from_cache=data.get("diverge_from_cache", True),
        )
