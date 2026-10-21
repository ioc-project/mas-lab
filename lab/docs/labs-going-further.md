<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Extend a lab

This page shows how to add custom **pipeline steps**, **scenarios**, and **overlays** to a **lab**. Read it after you have run a lab once. Do not write standalone plotting scripts.

Smallest working change: add one entry under `scenarios:` in `experiment.yaml`, then run `mas-lab benchmark run experiment.yaml --dry-run`.

Start with [labs-quickstart.md](labs-quickstart.md). Terms: [glossary.md](../../docs/glossary.md).

## Custom pipeline step

Put the step in a **local library** inside the lab, not at the lab root. A library is a folder of reusable steps and plugins described by a `library.yaml`. Register the folder in `lab-config.yaml`.

Follow these steps:

1. Add `my_lab/steps/my_step.py`. Implement `PipelineStep` and call `register_step`.
   Name the package after the lab (`my_lab/`, not `lib/`). Every library's parent directory is on one `sys.path`. Two libraries with the same package name shadow each other. The registry fails on such a clash.
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

Do not create a separate top-level plugins folder. Pipeline steps and runtime plugins belong in the library. Lab vs library:
[labs-and-libraries.md](../../docs/labs-and-libraries.md).

Examples: [lifecycle-control.lab/lifecycle_control_lab/steps/](../../labs/lifecycle-control.lab/lifecycle_control_lab/steps/).

## Scenarios and overlays

- **Scenario**: a new entry under `scenarios:` (`id`, `overlays`, optional `tags`)
- **Overlay**: a YAML patch in `overlays/`. A **scenario** or the CLI `-o` flag references it.
- **Dataset**: the input items, in `datasets/`.

[overlays.md](../../docs/manifests/overlay.md) · [manifests/experiment.md](../../docs/manifests/experiment.md).

## Figures only via pipeline

Every figure is a **pipeline step** in `experiment.yaml`. Regenerate figures with:

```bash
mas-lab benchmark run …
```

Reference PNGs: `figures/paper/`.

## Sub-experiments

Nested folders (for example `02-bit-exactness/`) are separate **labs**. Each has its own `experiment.yaml`.

## Evaluation

- Structural metrics from **`events.jsonl`**: `extract_trace_stats`, `extract_mealy_stats`.
- Semantic metrics: `eval_mce` (judge model, see [summarization.md](../../docs/manifests/summarization.md#mce-judge-model)).

[library-eval/README.md](../../library-eval/README.md).

## Related

- [pipeline.md](pipeline.md)
- [extensions.lab](../../labs/extensions.lab/)
