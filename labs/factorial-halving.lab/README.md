<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Factorial successive-halving

A 2×2 factorial space (`size` × `style`) has four scenarios. After one
scored round, `next_runs` with `successive_halving` keeps the strongest
half and drops the rest. The fixture is the first-round scores, so the
cut runs offline with no model calls.

```bash
mas-lab benchmark pipeline run labs/factorial-halving.lab/pipeline.yaml
```

The `keep-strongest` step plans `experiment.yaml` (four runs), reads the
first-round scores from the fixture, keeps `small-short` and `small-long`
(0.91 and 0.84) and writes `output/round2-runs.json`, a run plan. The next
round runs only those runs:

```bash
mas-lab benchmark run labs/factorial-halving.lab/experiment.yaml \
  --run-plan labs/factorial-halving.lab/output/round2-runs.json
```

The design stays the whole factorial space; the plan file, not an edited
design, narrows the round. The same plan file is what a CI matrix job runs
(see [CI sharding](../../docs/manifests/ci-sharding.md)).

The numbers cannot show whether those two scenarios would still win after
another round, or whether the score is the one the experiment meant to
optimise. They only show the cut.

See [completeness](../../docs/manifests/completeness.md) and
[pipeline assembly](../../docs/manifests/pipeline-assembly.md).
