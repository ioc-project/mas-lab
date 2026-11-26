<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Write an executor

An **executor** answers *how a run is run once*: timeout, workers, and which
application runner starts the MAS. It never retries. Whether a failed run
runs again is the [retrier](retrier.md)'s decision.

The batch host still owns cache hits, resume, `result.json`, what-if
checkpoints, and the environment. The executor plugin wraps one MAS invoke.
The application runner id (`executor.runner`) is a different registry: it
picks how one MAS is started inside a run, not which executor plugin runs.

```yaml
experiment:
  executor:
    plugin: native
    timeout: 300
    worker_processes: 1
    runner: native
    params: {}
```

Omit `plugin` and `native` is used. Unknown ids fail at config load with the
registered list. Params are validated then too. `max_attempts` and
`retry_backoff_s` are not executor keys: they are `retry.params.max_attempts`
and `retry.params.backoff_s`.

Timeout, workers, and runner still load from `schedule` for one release, with
an `experiment.schedule.executor` deprecation warning, and are rewritten onto
`executor`. Giving the same host key on both blocks is an error.

## Interface

```python
from mas.lab.benchmark.execution.roles import AttemptResult, reject_unknown_keys

class Direct:
    plugin_id = "direct"

    def validate_params(self, params: dict) -> None:
        reject_unknown_keys(params or {}, set(), plugin_id=self.plugin_id)

    def is_complete(self, result_dict, *, status, error=""):
        from mas.lab.benchmark.execution.completeness import invoke_is_complete

        return invoke_is_complete(status=status, error=error)

    async def attempt(self, do_run, *, before_attempt=None) -> AttemptResult:
        ...  # run do_run() once; return AttemptResult(result, status, error, output, fatal=False)
```

`is_complete` must never report an incomplete run as complete. The host
marks that run `error` and the retrier may schedule it again. See
[completeness](completeness.md). `fatal=True` on a result (a failed `pre`
step, for instance) tells every retrier that no retry can repair it.

The plugin never reads the experiment file. It never writes `result.json`.

## Register it

```yaml
types:
  - executor
plugins:
  - type: executor
    name: direct
    module: my_lab_lib.executors
    class: Direct
```

See [experiment.md](experiment.md#executor) for the shipped plugin and host keys.
