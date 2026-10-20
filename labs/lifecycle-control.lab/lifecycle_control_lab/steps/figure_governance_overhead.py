#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline figure step (migrated from lab script)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

from ._helpers import execute_csv_figure

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

def render(output_path: Path, trace_stats_path: Path) -> None:
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    from pathlib import Path

    # ── Data ──────────────────────────────────────────────────────────────────────

    df = pd.read_csv(trace_stats_path)

    # ── Stacking order (semantic: each step adds one layer) ──────────────────────
    STACK = [
        ("baseline",        "Baseline\n(no governance)",        "#888888"),
        ("governance-off",  "SM + OTel\n(no policy)",           "#4C9BE8"),
        ("with-guardrail",  "+ Guardrail\n(block forbidden)",   "#5DBB63"),
        ("with-budget",     "+ Budget\n(normal threshold)",     "#F5A623"),
        ("with-budget-low", "+ Budget\n(low — fires often)",    "#E05252"),
    ]

    # ── Per-item matched computation ──────────────────────────────────────────────
    records = []
    for item in df["item"].unique():
        row = {"item": item}
        for scen, _, _ in STACK:
            sub = df[(df["scenario"] == scen) & (df["item"] == item)]
            if not sub.empty:
                row[scen + "_dur"]     = sub["duration_s"].values[0]
                row[scen + "_nchecks"] = sub["n_governance_checks"].values[0]
                row[scen + "_nfired"]  = sub["n_governance_fired"].values[0]
                row[scen + "_nllm"]    = sub["n_llm_calls"].values[0]
        records.append(row)
    wide = pd.DataFrame(records)

    # Keep only items present in ALL stacking scenarios
    needed_cols = [s + "_dur" for s, _, _ in STACK]
    wide = wide.dropna(subset=needed_cols)

    results = []
    for _, row in wide.iterrows():
        base_dur   = row["baseline_dur"]
        govoff_dur = row["governance-off_dur"]
        govoff_chk = row["governance-off_nchecks"]

        for scen, label, color in STACK:
            scen_dur    = row[scen + "_dur"]
            scen_checks = row[scen + "_nchecks"]
            scen_fired  = row[scen + "_nfired"]
            scen_nllm   = row[scen + "_nllm"]

            # System overhead: SM+OTel vs baseline, normalised by govoff n_checks
            sys_delta    = govoff_dur - base_dur
            sys_per_call = sys_delta / govoff_chk if govoff_chk > 0 else 0.0

            # Plugin overhead: this scenario vs govoff, normalised by scenario n_checks
            plugin_delta    = scen_dur - govoff_dur
            plugin_per_call = plugin_delta / scen_checks if scen_checks > 0 else 0.0

            # Checks per LLM call (normalised call frequency)
            checks_per_llm = scen_checks / scen_nllm if scen_nllm > 0 else 0.0

            results.append({
                "item": row["item"], "scenario": scen, "label": label, "color": color,
                "duration_s":       scen_dur,
                "n_checks":         scen_checks,
                "n_fired":          scen_fired,
                "n_llm":            scen_nllm,
                "sys_per_call_s":   sys_per_call,
                "plugin_per_call_s":plugin_per_call,
                "checks_per_llm":   checks_per_llm,
            })

    res = pd.DataFrame(results)

    def agg(df_sub, col):
        vals = df_sub[col].dropna().values
        m  = np.mean(vals)
        se = np.std(vals, ddof=1) / np.sqrt(len(vals)) if len(vals) > 1 else 0
        return m, 1.96 * se

    agg_rows = []
    for scen, label, color in STACK:
        sub = res[res["scenario"] == scen]
        dur_m,  dur_e  = agg(sub, "duration_s")
        chk_m,  chk_e  = agg(sub, "n_checks")
        cpl_m,  cpl_e  = agg(sub, "checks_per_llm")
        sys_m,  sys_e  = agg(sub, "sys_per_call_s")
        plg_m,  plg_e  = agg(sub, "plugin_per_call_s")
        agg_rows.append(dict(
            scenario=scen, label=label, color=color,
            dur_m=dur_m, dur_e=dur_e,
            chk_m=chk_m, chk_e=chk_e,
            cpl_m=cpl_m, cpl_e=cpl_e,
            sys_m=sys_m, sys_e=sys_e,
            plg_m=plg_m, plg_e=plg_e,
        ))
    agg_df = pd.DataFrame(agg_rows)

    # ── Plot ──────────────────────────────────────────────────────────────────────
    fig, axes = plt.subplots(3, 1, figsize=(10, 12),
                             gridspec_kw={"height_ratios": [2.2, 2.8, 1.5]},
                             constrained_layout=True)
    fig.suptitle("Governance overhead as layers are stacked\n(lifecycle-control.lab — 13 items × 5 scenarios)",
                 fontsize=13, fontweight="bold")

    x           = np.arange(len(STACK))
    w           = 0.55
    colors_list = agg_df["color"].tolist()

    # ── Panel 1 : Absolute latency ────────────────────────────────────────────────
    ax1 = axes[0]
    bars = ax1.bar(x, agg_df["dur_m"], width=w, color=colors_list,
                   alpha=0.85, edgecolor="white", linewidth=0.8)
    ax1.errorbar(x, agg_df["dur_m"], yerr=agg_df["dur_e"],
                 fmt="none", color="black", capsize=4, linewidth=1.2)
    ref_base   = agg_df.loc[agg_df["scenario"] == "baseline",       "dur_m"].values[0]
    ref_govoff = agg_df.loc[agg_df["scenario"] == "governance-off", "dur_m"].values[0]
    ax1.axhline(ref_base,   color="#888888", linestyle="--", linewidth=1.0,
                label=f"Baseline ({ref_base:.1f}s)")
    ax1.axhline(ref_govoff, color="#4C9BE8", linestyle=":",  linewidth=1.0,
                label=f"SM+OTel ref ({ref_govoff:.1f}s)")
    ax1.set_xticks(x); ax1.set_xticklabels(agg_df["label"], fontsize=8.5)
    ax1.set_ylabel("Mean latency / item (s)", fontsize=9)
    ax1.set_title("(a) Total latency per scenario", fontsize=10, fontweight="bold")
    ax1.legend(fontsize=8); ax1.set_ylim(0)
    for bar, (_, r) in zip(bars, agg_df.iterrows()):
        ax1.text(bar.get_x() + w/2, bar.get_height() + 0.4,
                 f"{r['dur_m']:.1f}s", ha="center", va="bottom", fontsize=8.5)

    # ── Panel 2 : Per-call overhead — plugin scenarios vs govoff reference ────────
    # Only show the 3 plugin scenarios (govoff = reference line at y=0).
    # The system overhead (govoff − baseline) is within single-run noise (~-3.7s
    # total) and cannot be decomposed reliably without multiple runs.
    ax2 = axes[1]

    plugin_scenarios = ["governance-off", "with-guardrail", "with-budget", "with-budget-low"]
    plugin_labels    = ["SM+OTel\n(ref = 0)", "+ Guardrail\n(block)", "+ Budget\n(normal)", "+ Budget\n(low — fires)"]
    plugin_colors    = ["#4C9BE8",           "#5DBB63",               "#F5A623",             "#E05252"]

    xp = np.arange(len(plugin_scenarios))

    plg_vals_p, plg_errs_p = [], []
    for scen in plugin_scenarios:
        row = agg_df[agg_df["scenario"] == scen].iloc[0]
        plg_vals_p.append(row["plg_m"])
        plg_errs_p.append(row["plg_e"])
    plg_vals_p = np.array(plg_vals_p)
    plg_errs_p = np.array(plg_errs_p)

    bars2 = ax2.bar(xp, plg_vals_p, width=w, color=plugin_colors, alpha=0.85, edgecolor="white")
    ax2.errorbar(xp, plg_vals_p, yerr=plg_errs_p,
                 fmt="none", color="black", capsize=4, linewidth=1.2)

    for i, (tot, err) in enumerate(zip(plg_vals_p, plg_errs_p)):
        sign = "+" if tot >= 0 else ""
        ypos = tot + max(err, 0.001) + 0.001 if tot >= 0 else tot - max(err, 0.001) - 0.02
        va = "bottom" if tot >= 0 else "top"
        ax2.text(i, ypos, f"{sign}{tot*1000:.0f} ms/call",
                 ha="center", va=va, fontsize=8.5, fontweight="bold")

    ax2.axhline(0, color="black", linewidth=0.7)
    ax2.axhline(0, color="#4C9BE8", linestyle=":", linewidth=1.0, alpha=0.6,
                label="govoff reference (SM+OTel, no policy)")
    ax2.set_xticks(xp); ax2.set_xticklabels(plugin_labels, fontsize=8.5)
    ax2.set_ylabel("Plugin overhead / governance trigger (s/call)", fontsize=9)
    ax2.set_title("(b) Per-call overhead above SM+OTel reference\n"
                  "(positive = policy adds latency per trigger, negative = within noise)",
                  fontsize=10, fontweight="bold")
    ax2.legend(fontsize=8, loc="upper left")

    # ── Panel 3 : Governance triggers per LLM call ────────────────────────────────
    ax3 = axes[2]
    ax3.bar(x, agg_df["cpl_m"], width=w, color=colors_list,
            alpha=0.75, edgecolor="white")
    ax3.errorbar(x, agg_df["cpl_m"], yerr=agg_df["cpl_e"],
                 fmt="none", color="black", capsize=4, linewidth=1.2)
    ax3.set_xticks(x); ax3.set_xticklabels(agg_df["label"], fontsize=8.5)
    ax3.set_ylabel("Gov. triggers / LLM call", fontsize=9)
    ax3.set_title("(c) Governance call frequency (normalised by LLM calls)", fontsize=10, fontweight="bold")
    ax3.set_ylim(0)
    for i, (m, e) in enumerate(zip(agg_df["cpl_m"], agg_df["cpl_e"])):
        ax3.text(i, m + e + 0.02, f"{m:.1f}", ha="center", va="bottom", fontsize=8.5)

    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved → {output_path}")

class FigureGovernanceOverheadStep(PipelineStep):
    type = "figure_governance_overhead"

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        return execute_csv_figure(
            ctx,
            self,
            input_key="trace_stats",
            input_default="results/trace_stats.csv",
            output_name="fig-governance-overhead-percall.png",
            render=render,
        )

register_step_type("figure_governance_overhead", FigureGovernanceOverheadStep)
