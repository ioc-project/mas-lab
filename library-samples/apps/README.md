# Sample apps

`apps/qa-mas/` is a small question-answering MAS. `apps/trip-planner/` is the
canonical sample described below.

## Trip planner sample app

The registry id **`trip-planner`** resolves to `apps/trip-planner/` — the canonical four-agent Arborian Network MAS (moderator coordinates specialists). Use `app: trip-planner` in experiment YAML.

**Topology and design-pattern variants** (linear pipeline, parallel, single-agent, CoT, ReAct, …) are **not** separate app folders. Topology MAS variants are `apps/trip-planner/mas-topologies/*.mas.yaml` (registered as `trip-planner-topo-*` in `library.yaml`), and design-pattern overlays live under `labs/` (e.g. `labs/design-space.lab/02-topologies/overlays/`) or in `library-samples/overlays/`.

Shared tools also live under `library-samples/tools/`; the app folder may ship app-local `tools/`.

Plugin feature examples (small Agent/MAS scenarios that pin a failure mode)
live in `library-standard/examples/<category>/`, not here.
