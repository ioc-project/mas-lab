#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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

    Changing these fields never changes the meaning of a result cell — only
    wall time, resource usage, and failure modes during collection.
    """

    parallel_scenarios: int = 4
    """Maximum number of concurrent MAS runs."""

    timeout: int = 300
    """Per-run timeout in seconds."""

    max_attempts: int = 3
    """Re-issue a failed scenario×item×run this many times (including the first)."""

    retry_backoff_s: float = 2.0
    """Base delay in seconds before the first lab-level reattempt (then ×2)."""

    pause_between_runs: float = 1.0
    """Pause in seconds between runs to let resources settle."""

    ordering: str = "coverage"
    """Walk order: ``coverage`` (breadth-first) or ``depth`` (depth-first)."""

    runner: str = DEFAULT_LAB_RUNNER_ID
    """Application runner registry id."""

    reset_state: bool = False
    """Reset emulation/runtime state between runs when true."""

    @property
    def strategy(self) -> str:
        """Deprecated alias for :attr:`ordering`."""
        return self.ordering

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentScheduleSpec":
        ordering = data.get("ordering", data.get("strategy", "coverage"))
        return cls(
            parallel_scenarios=data.get("parallel_scenarios", 4),
            timeout=data.get("timeout", 300),
            max_attempts=max(1, int(data["max_attempts"] if data.get("max_attempts") is not None else 3)),
            retry_backoff_s=max(
                0.0,
                float(data["retry_backoff_s"] if data.get("retry_backoff_s") is not None else 2.0),
            ),
            pause_between_runs=data.get("pause_between_runs", 1.0),
            ordering=ordering or "coverage",
            runner=data.get("runner", DEFAULT_LAB_RUNNER_ID),
            reset_state=bool(data.get("reset_state", False)),
        )


@dataclass
class ExperimentDesignSpec:
    """What conditions are being compared.

    Lives next to ``scenarios[]``, dataset, evaluation, and ``run.n_runs``.
    Changing these fields *does* change the label or meaning of a result cell.
    """

    mode: str = "cartesian"
    """``cartesian``, ``coupled``, or ``one_factor``."""

    max_executions: Optional[int] = None
    """Fail when the planned grid exceeds this limit."""

    couplings: List[Dict[str, Any]] = field(default_factory=list)
    vary: Optional[Dict[str, Any]] = None
    pin_scenarios: Optional[List[str]] = None
    pin_items: Optional[List[Any]] = None

    def as_plan_dict(self) -> Dict[str, Any]:
        """Shape consumed by :func:`mas.lab.benchmark.execution.plan.build_execution_plan`."""
        data: Dict[str, Any] = {"mode": self.mode}
        if self.max_executions is not None:
            data["max_executions"] = self.max_executions
        if self.couplings:
            data["couplings"] = self.couplings
        if self.vary:
            data["vary"] = self.vary
        if self.pin_scenarios is not None:
            data["pin_scenarios"] = self.pin_scenarios
        if self.pin_items is not None:
            data["pin_items"] = self.pin_items
        return data

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ExperimentDesignSpec":
        data = data or {}
        if "pin" in data:
            raise ValueError(
                "design.pin is not read; use pin_scenarios / pin_items with one_factor"
            )
        return cls(
            mode=data.get("mode", "cartesian"),
            max_executions=data.get("max_executions"),
            couplings=list(data.get("couplings") or []),
            vary=data.get("vary"),
            pin_scenarios=data.get("pin_scenarios"),
            pin_items=data.get("pin_items"),
        )


def split_legacy_execution(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Map a legacy ``execution:`` mapping onto the split keys.

    ``n_runs`` is dropped — replication belongs under ``run.n_runs``.
    """
    out: Dict[str, Any] = {}
    schedule: Dict[str, Any] = {}
    for key in (
        "parallel_scenarios",
        "timeout",
        "pause_between_runs",
        "runner",
        "reset_state",
        "max_attempts",
        "retry_backoff_s",
    ):
        if key in execution:
            schedule[key] = execution[key]
    if "ordering" in execution:
        schedule["ordering"] = execution["ordering"]
    elif "strategy" in execution:
        schedule["ordering"] = execution["strategy"]
    if schedule:
        out["schedule"] = schedule
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

    Prefer ``MASExperimentConfig.schedule``, ``.design``, and
    ``.bench_emulation``.  This object is synthesized during dual-read so
    leftover consumers of ``exp.execution`` keep working for one release.
    """

    n_runs: int = 3
    """Replication count.  Canonical home is ``run.n_runs`` (design)."""

    parallel_scenarios: int = 4
    """Maximum number of concurrent MAS runs."""

    timeout: int = 300
    """Per-run timeout in seconds."""

    max_attempts: int = 3
    """Re-issue a failed scenario×item×run this many times (including the first).

    Independent of ``run.n_runs`` (statistical repeats). Default 3 so an
    LLM HTTP failure can recover after in-call retries are spent. Set 1 to
    disable lab-level reattempts.
    """

    retry_backoff_s: float = 2.0
    """Base delay in seconds before the first lab-level reattempt (then ×2)."""

    pause_between_runs: float = 1.0
    """Pause in seconds between runs to let resources settle."""

    strategy: str = "coverage"
    """Deprecated alias of ``schedule.ordering``."""

    runner: str = DEFAULT_LAB_RUNNER_ID
    """Application runner registry id."""

    reset_state: bool = False
    """Reset emulation/runtime state between runs when true."""

    design: Optional[Dict[str, Any]] = None
    """Experiment design mode (cartesian, coupled, one_factor) and guards."""

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
        strategy = data.get("strategy", data.get("ordering", "coverage"))
        return cls(
            n_runs=data.get("n_runs", 3),
            parallel_scenarios=data.get("parallel_scenarios", 4),
            timeout=data.get("timeout", 300),
            max_attempts=max(1, int(data["max_attempts"] if data.get("max_attempts") is not None else 3)),
            retry_backoff_s=max(
                0.0,
                float(data["retry_backoff_s"] if data.get("retry_backoff_s") is not None else 2.0),
            ),
            pause_between_runs=data.get("pause_between_runs", 1.0),
            strategy=strategy or "coverage",
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
        bench_emulation: EmulationSpec,
        replay: Optional["ReplaySpec"],
        n_runs: int,
    ) -> "MASExecutionSpec":
        return cls(
            n_runs=n_runs,
            parallel_scenarios=schedule.parallel_scenarios,
            timeout=schedule.timeout,
            max_attempts=schedule.max_attempts,
            retry_backoff_s=schedule.retry_backoff_s,
            pause_between_runs=schedule.pause_between_runs,
            strategy=schedule.ordering,
            runner=schedule.runner,
            reset_state=schedule.reset_state,
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

