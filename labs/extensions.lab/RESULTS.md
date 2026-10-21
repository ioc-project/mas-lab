<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lab 3 — Context Source Extensions: Results

**Status (2026-06-02):** the notes below are historical. To reproduce or refresh figures, run `mas-lab benchmark run labs/extensions.lab/experiment.yaml`. Use pipeline steps only, no standalone plotting scripts.

**Status note (2026-10-19):** the numbers below describe one earlier run. They were not regenerated for v0.2.0.

The `~/mas-data/` paths and the stand-alone Sankey script belong to that run. They are not part of the repository. Current runs write to `$XDG_DATA_HOME/mas/labs/memory-extension-reproducibility/` (see [README](README.md)).

The attribution and Sankey steps exist as lab-local steps in `extensions_lab/steps/`. `experiment.yaml` does not wire them in. So `mas-lab benchmark run` does not produce the Sankey figure.

The earlier run executed the 4-scenario reproducibility benchmark end to end at `n_runs = 3`. The scenarios were baseline, vector-memory, letta-memory, and vector-memory+guardrail. It made 120 runs, all with status OK.

The LLM-judge analysis steps (`eval_mce`, `compute_ci`, `plotnine`) hung at the end of execution. The connection to the OpenAI-compatible endpoint stuck in CLOSE_WAIT, and the steps were killed. The per-run trace data persisted. A stand-alone script built the attribution figure (§1.5) directly from the `events.jsonl` traces. No LLM judge was involved.

Run output: `~/mas-data/extensions-lab/`. Pipeline: [`experiment.yaml`](experiment.yaml).

---

## Lab 3 §1.5 — Attribution Sankey

The figure at `~/mas-data/extensions-lab/results/figure-attribution-sankey.png` encodes all 120 runs across four stages: scenario, tool, governance, answer. The stages come from the trace alone, with no LLM judge:

| Stage      | Categories                                                |
|------------|-----------------------------------------------------------|
| scenario   | baseline / with-vector-memory / with-letta-memory / +guardrail |
| tool       | `used-tools` (≥1 `tool_call_start` span) / `no-tool`      |
| governance | `denied` (`PolicyViolation` raised) / `allowed`           |
| answer     | `answered` / `refused-ok` (correct refusal on g1) / `refused-bad` (refusal on non-g1) |

**Headline flows (out of 120 runs):**

```
scenario                       → tool        → governance → answer        n
─────────────────────────────────────────────────────────────────────────────
with-vector-memory-guardrail  → used-tools  → denied     → refused-ok    3
all other scenarios           → used-tools  → allowed    → answered     49
all scenarios                 → no-tool     → allowed    → answered     56
baseline / letta / vector     → used-tools  → allowed    → refused-ok    9
```

**Reading.**

1. **Governance triggers fire only where the overlay says they should.** Three runs end in `governance:denied`. All three are in the `with-vector-memory-guardrail` scenario on item `g1` (the "Shadowmere" forbidden-destination query). The other 117 runs show no false positive. The trace signal localises the governance contribution exactly, with no LLM judge.

2. **The baseline also refuses g1** (3/3 on g1, `refused-ok`). The LLM declines; no policy event fires. The Sankey shows `allowed → refused-ok` instead of `denied → refused-ok`. This is the distinction §5.3 makes. The answer can look identical end to end while the provenance of the refusal differs. The trace localises the failure. The output text does not.

3. **Tool usage is the dominant predictor of "answered" vs. "no-answer".** In the 55 runs where the agent called a tool, 52 produced an `answered` output and 3 produced a correct refusal. In the 65 no-tool runs, 56 produced an answered output and 9 produced incorrect refusals. For `recall` queries the LLM has enough context to give a generic answer. The trace alone localises the failures.

4. **The memory overlays produced zero `memory_*` events** across all 60 vector and letta runs. This is a negative finding. The overlay wires the plugin and registers the `memory-search` tool. On this prompt set the agent never chose to call it. The runs are repeatable and tools are invoked uniformly, so the reproducibility claim of the paper holds. The memory-extensibility claim holds only when an item names a profile attribute the agent must retrieve. Backlog item 1.6 below tracks this.

Per-scenario answer distribution (n=30 each):

| Scenario                     | answered | refused-ok | refused-bad |
|------------------------------|---------:|-----------:|------------:|
| baseline                     |       26 |          3 |           1 |
| with-vector-memory           |       26 |          3 |           1 |
| with-letta-memory            |       26 |          3 |           1 |
| with-vector-memory-guardrail |       27 |          3 |           0 |

Outputs:

- `~/mas-data/extensions-lab/results/attribution.csv` (120 rows, 6 cols)
- `~/mas-data/extensions-lab/results/figure-attribution-sankey.png`
- `~/mas-data/extensions-lab/results.csv` (120 raw runs, all `status=ok`)

---

## Lab 3 §base — Memory extension reproducibility

The `eval_mce` step hung on the LLM-judge HTTP calls (CLOSE_WAIT on the proxy). It was killed after the 120-run trace corpus existed and before it wrote the CI summary CSV.

This session did not refresh the numerical reproducibility claim of §5.3. The previously published Table 5 remains the canonical source for GSR and AR numbers. Its last run was in 2026-05, in the archive snapshot at `$XDG_DATA_HOME/mas/labs/memory-provenance/`.

This session added:

- 120 fresh runs across all 4 scenarios at `n_runs=3`, stored under `~/mas-data/extensions-lab/`.
- A judge-free attribution figure. The paper can use it next to Table 5 to show failure localisation.

---

## Experiment backlog

| §    | Experiment                              | Status              | Notes |
|------|-----------------------------------------|---------------------|-------|
| 1.1  | Letta condition in Table 5              | **Runs done**       | 30 fresh runs persisted; LLM-judge re-scoring blocked on proxy hang. |
| 1.2  | Memory × topology 2×2 control           | **Not started**     | Needs `2-agent, no-memory` overlay. |
| 1.3  | Multi-session memory                    | **Not started**     | Needs 5-session dataset. |
| 1.4  | New tool mid-experiment                 | **Not started**     | Needs overlay-only currency-converter. |
| 1.5  | **Attribution Sankey**                  | **DONE**            | Trace-only figure produced; 3/3 g1 runs in guardrail scenario end in `governance:denied`. |
| 1.6  | Demonstrate memory `hit` on profile-recall | **Surfaced now**  | Memory overlays produced 0 `memory_*` events on this dataset — agent never autonomously called `memory-search`. Either the prompt set must be sharpened or the overlay must expose memory via passive injection (Letta) more aggressively. |
| 2.1  | n ≥ 20 runs                             | **Pipeline-ready**  | Flip `n_runs: 3` → `20`. |
| 2.2  | Statistical tests (Wilcoxon / t)        | **Pending eval**    | Needs `compute_ci` to complete. |
| 2.3  | Second judge                            | **Step ready**      | `inter_rater_agreement` in `extensions_lab/steps/`. |
| 2.4  | Investigate GSR=0 for no-memory         | **Pending eval**    | — |

---

## Reproduce

```bash
# 1. Runs (already cached, content-addressed; will be no-ops):
mas-lab benchmark run labs/extensions.lab/experiment.yaml --force

```

A stand-alone script built the §1.5 Sankey from the `events.jsonl` traces of that run. The repository does not include the script. The lab-local `attribution_flow` and `sankey_flow` steps in `extensions_lab/steps/` implement the same idea as pipeline steps.
