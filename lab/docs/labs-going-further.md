<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Extend a lab

Add custom **pipeline steps**, **scenarios**, and **overlays** to a **lab** —
without standalone plotting scripts.

Start with [labs-quickstart.md](labs-quickstart.md). Terms: [glossary.md](../../docs/glossary.md).

## Custom pipeline step

Put the step in a **local library** inside the lab (not on the lab root).
Register that folder in `lab-config.yaml` and give it `library.yaml`:

1. Add `my_lab/steps/my_step.py` — implement `PipelineStep`, `register_step`.
   Name the package after the lab (`my_lab/`, not `lib/`): every library's
   parent directory is on one `sys.path`, so two libraries with the same
   package name shadow each other. The registry fails on such a clash.
2. List the folder and catalog it:

   ```yaml
   # lab-config.yaml
   lab:
     libraries:
       - my_lab/
   ```

   ```yaml
   # my_lab/library.yaml
   apiVersion: mas/v1
   kind: Library
   name: my-lab-lib
   description: Lab-local pipeline figure steps for this lab.
   version: "0.1.0"
   ```

3. Add a step to experiment-level `post:` in `experiment.yaml` (CLI `--depth exp`):

   ```yaml
   post:
     - name: my-step
       type: my_lab.steps.my_step:MyStep
       depends_on: [gather-experiment]
       config:
         output: results/my-figure.png
   ```

Do not create a separate top-level plugins folder. Pipeline steps and
runtime plugins belong *in* the library. Lab vs library:
[labs-and-libraries.md](../../docs/labs-and-libraries.md).

Examples: [lifecycle-control.lab/lifecycle_control_lab/steps/](../../labs/lifecycle-control.lab/lifecycle_control_lab/steps/).

## Scenarios and overlays

- **Scenario** — new entry under `scenarios:` (`id`, `overlays`, optional `tags`)
- **Overlay** — YAML patch in `overlays/`; referenced by **scenario** or CLI `-o`
- **Dataset** — input items in `datasets/`

[overlays.md](../../docs/manifests/overlay.md) · [manifests/experiment.md](../../docs/manifests/experiment.md).

## Figures only via pipeline

Every figure is a **pipeline step** in `experiment.yaml`. Regenerate:

```bash
mas-lab benchmark run …
```

Reference PNGs: `figures/paper/`.

## Sub-experiments

Nested folders (e.g. `02-bit-exactness/`) are separate **labs** with their own
`experiment.yaml`.

## Evaluation

- Structural metrics from **`events.jsonl`**: `extract_trace_stats`, `extract_mealy_stats`
- Semantic metrics: `eval_mce` (judge model — [summarization.md](../../docs/manifests/summarization.md#mce-judge-model))

[library-eval/README.md](../../library-eval/README.md).

## Related

- [pipeline.md](pipeline.md)
- [extensions.lab](../../labs/extensions.lab/)
