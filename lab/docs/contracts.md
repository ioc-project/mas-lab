# Lab contracts

This page lists the three `mas-lab` commands that validate and run a lab. Read it to pick the right one.

Smallest working command: `mas-lab check mas.yaml`.

| Command | Purpose |
|---------|---------|
| `check` | Validates the structure of `mas.yaml` and the manifest tree |
| `check-config` | Checks config hygiene (model and access placement) |
| `benchmark run` | Runs an experiment end to end; scenarios come from the lab `datasets/` |

Scenario execution goes only through the benchmark pipeline and golden runs. MAS specs do not embed `test_knowledge`.
