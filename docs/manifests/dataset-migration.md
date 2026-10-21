<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Dataset envelope migration

This page maps deprecated dataset item shapes to the current envelope.
The **envelope** is the `kind: Dataset` wrapper plus the `inputs` / `expectations` split of each item.
Read this page when `mas-lab` warns about a dataset file, or when you convert an old dataset by hand.

A dataset item is `inputs` (what the run receives) plus `expectations` (what you score against). Target shape:

```yaml
spec:
  items:
    - id: "001"
      inputs:
        user: Plan a three-day trip to Paris.
      expectations:
        ground_truth: A three-day Paris itinerary.
        details:
          max_budget_eur: 900   # app-specific ground truth
```

Loaders still accept the deprecated shapes below. They warn once per file.
A future breaking release will remove them.

## Migrate with the script

The migrator rewrites files **in place**, so commit or back up first:

```bash
python scripts/migrate_dataset_envelope.py                  # this repo's datasets (run from the repo root)
python scripts/migrate_dataset_envelope.py path/to/datasets  # any kind: Dataset file or directory
```

The migrator also collapses a one-key `tool_fixtures` pointer (`{scene: file.yaml}`) to the path.
It drops `spec.item_schema` (use `spec.schemas`).
Ambiguous inputs fail with the item id. Examples: several `tool_fixtures` keys, or a key already in `details`.

## Still accepted, removed later

Each load-time warning names one of these codes.

| Code | Still loads | Use instead |
|---|---|---|
| `dataset.legacy_item` | Item-level `prompt` / `query` / `question` / `text` / `input` / `user`, top-level `turns`, `expected_answer`, `ground_truth` | `inputs.user` + `expectations.ground_truth` |
| `dataset.role_list_user` | `inputs.user: [{role, content}, …]` | `inputs.user` as a string or list of strings |
| `dataset.legacy_expectations` | App-specific keys on `expectations` (`correct_action`, `governance`, …) | `expectations.details.<key>` |
| `dataset.bare_list` | Bare YAML list of items (no `kind: Dataset`) | `apiVersion: lab/v1` / `kind: Dataset` / `spec.items` |

A clash is an error, not a warning. Example: `correct_action` both at the top of `expectations` and under `details`.

Legacy lab and experiment wiring that is not a dataset item also still loads:

| Still loads | Use instead / notes |
|---|---|
| `experiment.dataset.path` (no `name`) | Still a supported locator. Prefer `dataset.name` (+ `locator`) for catalog datasets. |
| `lab.default_flavour`, `lab.output_dir`, `scenario.user_prompt` | Interactive lab-config only. `output_dir` stays **rejected** on MAS experiments. |

## Field map

| Before | After | Load |
|---|---|---|
| `prompt: "..."` | `inputs.user: "..."` (a **string**, not a role list) | warn `dataset.legacy_item` |
| `turns: [{role: user, ...}, {role: hitl, ...}]` | user contents → `inputs.user` (string or string list); HITL contents → `inputs.hitl` (string list). Do **not** invent `inputs.turns`. | warn `dataset.legacy_item` |
| `hitl_responses` | `expectations.details.hitl_responses` (eval), or `inputs.hitl` if they are run inputs | warn `dataset.legacy_item` |
| `memory_seeds` | `inputs.memory_seeds` (inline list, `{ref:}`, or bare path shorthand) | warn `dataset.legacy_item` |
| `session_id` | `inputs.session_id` | warn `dataset.legacy_item` |
| `expected_answer` / `ground_truth` | `expectations.ground_truth` | warn `dataset.legacy_item` |
| app-specific `expectations.<key>` | `expectations.details.<key>` | warn `dataset.legacy_expectations` |
| app-specific keys inside a fixture YAML used as ground truth | `expectations.details` on the **item** | migrate |
| overlay `params.<fixture key>` pointing at a fixture file | `inputs.tool_fixtures` on the item (`{ref:}` or bare path) | migrate |
| `inputs.user: [{role: user, content: "..."}]` | `inputs.user: "..."` (string) or a list of strings | warn `dataset.role_list_user` |
| `tool_fixtures` mapping keys other than `by_tool` | `inputs.tool_fixtures: {ref: path}` or `by_tool` | **error** |
| inline payload directly under `tool_fixtures` | `tool_fixtures: {by_tool: {"*": <payload>}}` | **error** |
| tools reading fixture files or environment variables | `mas.runtime.contracts.tool_fixture(ctx, tool_name)`; overlay params in `ctx.runtime_params` | — |
| bare YAML list of items (no Dataset kind) | `apiVersion: lab/v1` / `kind: Dataset` / `spec.items` | warn `dataset.bare_list` |

A string on `user` is the prompt text.
A string on `tool_fixtures` or `memory_seeds` is a path, shorthand for `{ref: that-string}`.
Do not mix the two.
`{ref: relative/path}` is the only file-pointer syntax on `user`.


## Couplings

`experiment.design.mode: coupled` binds each `scenario` to a dataset item **`id`** (see [experiment.md](experiment.md#design)):

```yaml
design:
  mode: coupled
  couplings:
    - scenario: order-42-refund
      items: [order-42-refund]
```

Do not rename item ids when you migrate.
Cartesian and one-factor experiments that filter by `id`, `group`, or `category` keep using those metadata keys.

## Labs already on the envelope

- library-samples trip-planner: `inputs.user` + `expectations.ground_truth`.

Evaluators read app-specific ground truth from `expectations.details` on the Dataset item, never from tool-fixture YAML.
Overlay-only files are not a ground-truth home. If a run needs scoring, make it a Dataset item.

## Generate-dataset pipeline step

The `generate_dataset` pipeline step (`GenerateDatasetStep`, `library-lab`) writes `kind: Dataset` items with `inputs.user`, not `prompt:`.

## See also

- [dataset.md](dataset.md): the current manifest format
- [experiment.md](experiment.md): `dataset` and `design.couplings`
