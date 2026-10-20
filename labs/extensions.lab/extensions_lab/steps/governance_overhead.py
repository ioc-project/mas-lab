#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""governance_overhead — 4-panel figure: plugin overhead is negligible.

Panel A  (top-left, log scale)
    Horizontal bar chart of mean time spent per (plugin, contract) pair,
    expressed as a percentage of total agent session time.  X-axis is
    log₁₀ scale so that sub-0.01 % governance bars remain visible next
    to the dominant "LLM I/O + routing" segment.  Bars are stacked by
    plugin within each contract group.

Panel B  (top-right)
    Stacked horizontal bars: fraction of session time broken down by
    contract family per scenario.  Shows which contract consumes the most
    waiting time across conditions.

Panel C  (bottom-left)
    Average number of plugin calls per question per agent, with 95 % CI,
    grouped by plugin (sorted descending).

Panel D  (bottom-right)
    Average per-call latency (ms) per plugin, with 95 % CI (log scale).
    Governance plugins are highlighted.

Hook → contract mapping
-----------------------
pre_llm_call / post_llm_call          → ModelContract  (LLM access)
pre_tool_call / post_tool_call        → ToolContract
pre_execution / post_execution        → ObservabilityLifecycle
post_context_assembly                 → ContextContract
pre_agent_comm / post_agent_comm      → RoutingContract
user_output                           → SurfaceAdapter

Configuration
-------------
data          str   ``@<step-name>`` reference to the sys_stats DataFrame.
output        str   Output PNG path.
title         str   Optional overall figure title.
governance_plugins list  Plugin names considered "governance".
scenarios     list  Optional filter.  Default: all scenarios.
figsize       list  [width, height] in inches.  Default: [16, 12].
dpi           int   Default: 150.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cisco-Outshift brand colours (from project SVG conventions)
_BLUE   = "#005073"   # primary brand
_TEAL   = "#00bceb"   # accent
_GREEN  = "#6fc24b"   # positive / governance-passed
_ORANGE = "#ff7300"   # warning
_GREY   = "#c8c9c7"   # neutral / other
_RED    = "#e2231a"   # error / denied

_GOVERNANCE_COLOUR = _BLUE
_OTHER_PLUGIN_COLOUR = _TEAL

# Hook → contract family mapping (derived from runtime contract definitions)
_HOOK_CONTRACT: dict[str, str] = {
    "pre_llm_call":              "ModelContract",
    "post_llm_call":             "ModelContract",
    "pre_tool_call":             "ToolContract",
    "post_tool_call":            "ToolContract",
    "pre_execution":             "ObservabilityLifecycle",
    "post_execution":            "ObservabilityLifecycle",
    "post_context_assembly":     "ContextContract",
    "pre_agent_communication":   "RoutingContract",
    "post_agent_communication":  "RoutingContract",
    "user_output":               "SurfaceAdapter",
}

# Visual ordering for contracts in Panel B (most specific → infrastructure)
_CONTRACT_ORDER = [
    "ModelContract",
    "ToolContract",
    "ContextContract",
    "RoutingContract",
    "SurfaceAdapter",
    "ObservabilityLifecycle",
]

# Distinct colours per contract family (brand-consistent)
_CONTRACT_COLOUR: dict[str, str] = {
    "ModelContract":          _BLUE,
    "ToolContract":           _GREEN,
    "ContextContract":        _TEAL,
    "RoutingContract":        "#9b59b6",   # purple
    "SurfaceAdapter":         _ORANGE,
    "ObservabilityLifecycle": _GREY,
}


def _resolve_df(source: str, ctx: Any):
    import pandas as pd
    if source.startswith("@"):
        step_name = source[1:]
        out = ctx.step_outputs.get(step_name)
        if out is None:
            raise ValueError(
                f"GovernanceOverheadStep: step '{step_name}' not in step_outputs"
            )
        data = getattr(out, "data", out) or {}
        if "df" in data and data["df"] is not None:
            return data["df"]
        # Try CSV path fallback
        if "path" in data:
            return pd.read_csv(data["path"])
        raise ValueError(
            f"GovernanceOverheadStep: no DataFrame in step '{step_name}'"
        )
    return __import__("pandas").read_csv(source)  # a confined, resolved path


def _short(name: str) -> str:
    return (name.replace("Plugin", "").replace("Engine", "")
               .replace("NativeObservability", "NativeObs."))


def _build_figure(
    df,
    output_path: Path,
    *,
    title: str,
    governance_plugins: List[str],
    scenarios: Optional[List[str]],
    figsize: List[float],
    dpi: int,
) -> None:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import pandas as pd

    if scenarios:
        df = df[df["scenario"].isin(scenarios)].copy()

    if df.empty:
        logger.warning("GovernanceOverheadStep: empty DataFrame — skipping figure")
        return

    plugin_df = df[df["plugin"] != ""].copy()

    # Annotate contract family from hook name
    plugin_df["contract"] = plugin_df["hook"].map(
        lambda h: _HOOK_CONTRACT.get(h, "Unknown")
    )

    # ------------------------------------------------------------------ #
    # Shared aggregations
    # ------------------------------------------------------------------ #
    # Run-level totals (one row per scenario/item/run/agent)
    run_summary = (
        df.groupby(["scenario", "item", "run", "agent_id"], as_index=False)
        .agg(
            run_elapsed_ms     = ("run_elapsed_ms",     "first"),
            plugin_overhead_ms = ("plugin_overhead_ms", "first"),
        )
    )
    run_summary["nonpro_pct"] = (
        (run_summary["run_elapsed_ms"] - run_summary["plugin_overhead_ms"])
        / run_summary["run_elapsed_ms"] * 100
    ).clip(lower=0.0)

    # Per-run / per-plugin aggregation (sum over hooks)
    plugin_per_run = (
        plugin_df
        .groupby(["scenario", "item", "run", "agent_id", "plugin"], as_index=False)
        .agg(
            total_ms_plugin    = ("total_ms", "sum"),
            calls_plugin       = ("calls",    "sum"),
            run_elapsed_ms     = ("run_elapsed_ms",    "first"),
            plugin_overhead_ms = ("plugin_overhead_ms","first"),
        )
    )
    plugin_per_run["time_pct"] = (
        plugin_per_run["total_ms_plugin"] / plugin_per_run["run_elapsed_ms"] * 100
    ).fillna(0.0)

    # Per-run / per-contract aggregation (sum over plugins×hooks in same contract)
    contract_per_run = (
        plugin_df
        .groupby(["scenario", "item", "run", "agent_id", "contract"], as_index=False)
        .agg(
            total_ms_contract  = ("total_ms", "sum"),
            calls_contract     = ("calls",    "sum"),
            run_elapsed_ms     = ("run_elapsed_ms", "first"),
        )
    )
    contract_per_run["time_pct"] = (
        contract_per_run["total_ms_contract"] / contract_per_run["run_elapsed_ms"] * 100
    ).fillna(0.0)

    scenarios_sorted = sorted(run_summary["scenario"].unique().tolist())

    # Role colour map (ContractBinding roles)
    _ROLE_COLOUR = {
        "witness":    _GREY,
        "transform":  _TEAL,
        "governance": _BLUE,
    }

    # Assign stable colours per plugin.
    # Priority: governance_plugins list → role column (if present) → contract colour.
    _has_role = "role" in plugin_df.columns
    all_plugins = sorted(plugin_df["plugin"].unique().tolist())
    plugin_colours: dict = {}
    # Derive dominant role per plugin (most common non-empty value)
    plugin_role: dict = {}
    if _has_role:
        for p in all_plugins:
            rg_vals = plugin_df[plugin_df["plugin"] == p]["role"]
            rg_vals = rg_vals[rg_vals.notna() & (rg_vals != "")]
            plugin_role[p] = rg_vals.mode().iloc[0] if len(rg_vals) > 0 else ""

    for p in all_plugins:
        if p in governance_plugins:
            plugin_colours[p] = _GOVERNANCE_COLOUR
        elif _has_role and plugin_role.get(p):
            plugin_colours[p] = _ROLE_COLOUR.get(plugin_role[p], _OTHER_PLUGIN_COLOUR)
        else:
            hook_sample = plugin_df[plugin_df["plugin"] == p]["hook"].iloc[0] if len(plugin_df[plugin_df["plugin"] == p]) else ""
            contract_name = _HOOK_CONTRACT.get(hook_sample, "Unknown")
            plugin_colours[p] = _CONTRACT_COLOUR.get(contract_name, _OTHER_PLUGIN_COLOUR)

    # ------------------------------------------------------------------ #
    # Figure layout: A (top-left, log), B (top-right), C (bot-left), D (bot-right)
    # ------------------------------------------------------------------ #
    fig = plt.figure(figsize=figsize)
    fig.suptitle(title, fontsize=13, fontweight="bold", y=1.02)
    gs = fig.add_gridspec(2, 2, hspace=0.55, wspace=0.50,
                          height_ratios=[1.15, 1.0])
    ax_a = fig.add_subplot(gs[0, 0])  # per-plugin time pct, log scale
    ax_b = fig.add_subplot(gs[0, 1])  # per-contract time breakdown by scenario
    ax_c = fig.add_subplot(gs[1, 0])  # avg calls per question per plugin
    ax_d = fig.add_subplot(gs[1, 1])  # avg latency per call per plugin, log scale

    # ---- Panel A: per-plugin mean time % across all scenarios (log scale) ---
    plugin_time = (
        plugin_per_run
        .groupby("plugin", as_index=False)
        .agg(
            mean_pct = ("time_pct", "mean"),
            sem_pct  = ("time_pct", "sem"),
        )
    )
    plugin_time["ci95"] = plugin_time["sem_pct"].fillna(0.0) * 1.96
    plugin_time = plugin_time.sort_values("mean_pct", ascending=True)

    # Append "LLM I/O + routing" as the dominant bar
    nonpro_mean = run_summary["nonpro_pct"].mean()
    nonpro_sem  = run_summary["nonpro_pct"].sem()
    nonpro_row = pd.DataFrame([{
        "plugin": "LLM I/O + routing",
        "mean_pct": nonpro_mean,
        "sem_pct": nonpro_sem,
        "ci95": nonpro_sem * 1.96,
    }])
    plugin_time_all = pd.concat([plugin_time, nonpro_row], ignore_index=True)
    plugin_time_all = plugin_time_all.sort_values("mean_pct", ascending=True)

    y_a = np.arange(len(plugin_time_all))
    colours_a = [
        plugin_colours.get(p, _GREY) if p != "LLM I/O + routing" else _GREY
        for p in plugin_time_all["plugin"]
    ]
    ax_a.barh(y_a, plugin_time_all["mean_pct"].values, 0.6,
              color=colours_a,
              xerr=plugin_time_all["ci95"].values,
              error_kw={"ecolor": "#555", "capsize": 3, "linewidth": 1.0})
    ax_a.set_xscale("log")
    ax_a.set_yticks(y_a)
    ax_a.set_yticklabels(
        [_short(p) for p in plugin_time_all["plugin"]], fontsize=9)
    ax_a.set_xlabel("Mean % of agent session time (log scale)", fontsize=9)
    ax_a.set_title(
        "Panel A — Waiting time per plugin\n(fraction of total session, log₁₀)",
        fontsize=9.5, loc="left",
    )
    # Value annotations
    for i, v in enumerate(plugin_time_all["mean_pct"].values):
        ax_a.text(v * 1.15, i, f"{v:.3f}%", va="center", ha="left", fontsize=8)
    # Role badge annotations (WIT / GOV / TRF) when data available
    if _has_role:
        _badge = {"witness": "WIT", "governance": "GOV", "transform": "TRF"}
        for i, p in enumerate(plugin_time_all["plugin"]):
            rg = plugin_role.get(p, "")
            if rg:
                ax_a.text(
                    ax_a.get_xlim()[0], i,
                    _badge.get(rg, rg[:3].upper()),
                    va="center", ha="right", fontsize=7,
                    color=_ROLE_COLOUR.get(rg, _GREY),
                    fontweight="bold",
                )
    ax_a.spines["top"].set_visible(False)
    ax_a.spines["right"].set_visible(False)

    # ---- Panel B: per-contract fraction per scenario (stacked bars) -----
    contract_by_sc = (
        contract_per_run
        .groupby(["scenario", "contract"], as_index=False)
        .agg(mean_pct=("time_pct", "mean"))
    )
    nonpro_by_sc = (
        run_summary.groupby("scenario", as_index=False)
        .agg(nonpro_pct=("nonpro_pct", "mean"))
    )
    y_b = np.arange(len(scenarios_sorted))
    bar_h = 0.55
    lefts_b = np.zeros(len(scenarios_sorted))

    ordered_contracts = [c for c in _CONTRACT_ORDER if c in contract_by_sc["contract"].values]
    remaining = [c for c in contract_by_sc["contract"].unique() if c not in ordered_contracts]

    for contract_name in ordered_contracts + remaining:
        sub = (
            contract_by_sc[contract_by_sc["contract"] == contract_name]
            .set_index("scenario")
            .reindex(scenarios_sorted)["mean_pct"]
            .fillna(0.0).values
        )
        colour = _CONTRACT_COLOUR.get(contract_name, _GREY)
        ax_b.barh(y_b, sub, bar_h, left=lefts_b, color=colour,
                  label=contract_name, alpha=0.88)
        lefts_b += sub

    # Non-plugin remainder
    nonpro_b = (
        nonpro_by_sc.set_index("scenario")
        .reindex(scenarios_sorted)["nonpro_pct"].fillna(0.0).values
    )
    ax_b.barh(y_b, nonpro_b, bar_h, left=lefts_b, color=_GREY,
              label="LLM I/O + routing", alpha=0.88)

    # Annotate plugin total %
    for i in range(len(scenarios_sorted)):
        total_plugin = lefts_b[i]
        ax_b.text(lefts_b[i] + nonpro_b[i] + 0.3, i,
                  f"plugins: {total_plugin:.2f}%",
                  va="center", ha="left", fontsize=8, color=_ORANGE)

    ax_b.set_xlim(0, 106)
    ax_b.set_yticks(y_b)
    ax_b.set_yticklabels(scenarios_sorted, fontsize=9)
    ax_b.set_xlabel("Mean % of agent session time", fontsize=9)
    ax_b.set_title(
        "Panel B — Time allocation by contract per scenario\n"
        "(contracts defined by MAS plugin hook API)",
        fontsize=9.5, loc="left",
    )
    ax_b.legend(loc="lower right", fontsize=7.5, framealpha=0.85, ncol=1)
    ax_b.axvline(x=100, color=_GREY, linestyle="--", linewidth=0.7, alpha=0.4)
    ax_b.spines["top"].set_visible(False)
    ax_b.spines["right"].set_visible(False)

    # Shared legend patches for C & D
    gov_patch   = mpatches.Patch(color=_GOVERNANCE_COLOUR, label="Governance plugin")
    other_patch = mpatches.Patch(color=_OTHER_PLUGIN_COLOUR, label="Other plugin")

    # ---- Panel C: avg calls per question per plugin ---------------------
    panel_c = (
        plugin_per_run
        .groupby("plugin", as_index=False)
        .agg(
            mean_calls = ("calls_plugin", "mean"),
            sem_calls  = ("calls_plugin", "sem"),
        )
    )
    panel_c["ci95"] = panel_c["sem_calls"].fillna(0.0) * 1.96
    panel_c = panel_c.sort_values("mean_calls", ascending=True)

    y_c = np.arange(len(panel_c))
    colours_c = [plugin_colours.get(p, _OTHER_PLUGIN_COLOUR) for p in panel_c["plugin"]]
    ax_c.barh(y_c, panel_c["mean_calls"].values, 0.6,
              color=colours_c,
              xerr=panel_c["ci95"].values,
              error_kw={"ecolor": "#555", "capsize": 3, "linewidth": 1.0})
    x_max_c = panel_c["mean_calls"].max()
    for i, v in enumerate(panel_c["mean_calls"].values):
        ax_c.text(v + x_max_c * 0.025, i, f"{v:.1f}", va="center", ha="left", fontsize=8.5)
    ax_c.set_yticks(y_c)
    ax_c.set_yticklabels([_short(p) for p in panel_c["plugin"]], fontsize=9)
    ax_c.set_xlabel("Mean calls per agent per question (±95% CI)", fontsize=9)
    ax_c.set_title("Panel C — Avg calls per question\nper plugin (per agent)",
                   fontsize=9.5, loc="left")
    ax_c.legend(handles=[gov_patch, other_patch], fontsize=7.5, framealpha=0.85)
    ax_c.spines["top"].set_visible(False)
    ax_c.spines["right"].set_visible(False)

    # ---- Panel D: avg latency per call, log scale -----------------------
    panel_d = (
        plugin_df
        .groupby("plugin", as_index=False)
        .agg(
            mean_avg_ms = ("avg_ms", "mean"),
            sem_avg_ms  = ("avg_ms", "sem"),
        )
    )
    panel_d["ci95"] = panel_d["sem_avg_ms"].fillna(0.0) * 1.96
    panel_d = panel_d.sort_values("mean_avg_ms", ascending=True)

    y_d = np.arange(len(panel_d))
    colours_d = [plugin_colours.get(p, _OTHER_PLUGIN_COLOUR) for p in panel_d["plugin"]]
    # clip error bars so they don't cross zero on log scale
    xerr_d = np.minimum(panel_d["ci95"].values, panel_d["mean_avg_ms"].values * 0.95)
    ax_d.barh(y_d, panel_d["mean_avg_ms"].values, 0.6,
              color=colours_d,
              xerr=xerr_d,
              error_kw={"ecolor": "#555", "capsize": 3, "linewidth": 1.0})
    ax_d.set_xscale("log")
    for i, v in enumerate(panel_d["mean_avg_ms"].values):
        ax_d.text(v * 1.15, i, f"{v:.3f} ms", va="center", ha="left", fontsize=8.5)
    ax_d.set_yticks(y_d)
    ax_d.set_yticklabels([_short(p) for p in panel_d["plugin"]], fontsize=9)
    ax_d.set_xlabel("Mean per-call latency (ms, log scale, ±95% CI)", fontsize=9)
    ax_d.set_title("Panel D — Avg latency per call\n(log₁₀ scale)",
                   fontsize=9.5, loc="left")
    ax_d.legend(handles=[gov_patch, other_patch], fontsize=7.5, framealpha=0.85)
    ax_d.spines["top"].set_visible(False)
    ax_d.spines["right"].set_visible(False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    logger.info("GovernanceOverheadStep: figure saved → %s", output_path)


try:
    from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type
    from mas.lab.benchmark.pipeline.artifact_paths import (
        resolve_step_artifact_path,
        resolve_step_input_path,
    )

    class GovernanceOverheadStep(PipelineStep):
        """Render the governance-overhead dual-panel figure."""

        type = "governance_overhead"

        async def execute(self, ctx: "Any") -> StepOutput:  # noqa: F821
            cfg = self.config
            data_source: str = cfg.get("data", "")
            if not data_source:
                raise ValueError("GovernanceOverheadStep requires 'data' config key")

            output_path = resolve_step_artifact_path(
                ctx, None, self.name, "fig_governance_overhead.png", cfg,
                default_subdir="results", name_keys=(), mkdir=True,
            )

            if not data_source.startswith("@"):
                data_source = str(resolve_step_input_path(ctx, self.name, cfg, "data"))
            df = _resolve_df(data_source, ctx)

            _build_figure(
                df,
                output_path,
                title=cfg.get("title", "Governance overhead — Mealy machine plugin dispatch"),
                governance_plugins=cfg.get("governance_plugins", ["GovernancePolicyEngine"]),
                scenarios=cfg.get("scenarios"),
                figsize=cfg.get("figsize", [16, 12]),
                dpi=cfg.get("dpi", 150),
            )

            return StepOutput(
                data={"path": str(output_path)},
                files=[output_path],
                metadata={},
            )

except ImportError:
    GovernanceOverheadStep = None  # type: ignore[misc,assignment]
