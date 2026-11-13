# Protocol compliance

This directory is intentionally separate from MAS Lab runtime dependencies. It
contains optional third-party checks for protocol interoperability.

## A2A

The official A2A TCK is not published on PyPI. Install its GitHub project into
the compliance environment:

```bash
task --dir library-ioa/compliance/a2a install
task --dir library-ioa/compliance/a2a validate
task --dir library-ioa/compliance/a2a install-tck
```

The optional Git dependency pins the TCK project and its test dependencies. The
checkout is required for `run_tck.py` because the upstream wheel currently omits
that top-level runner. The TCK fetches `/.well-known/agent-card.json`, selects the declared transport
interfaces, and writes reports under its `reports/` directory. The project task
runs MUST, SHOULD, and MAY requirements across every declared transport.

The official A2A CLI is a Go binary named `a2a`, not the unrelated PyPI package
named `a2a-cli`:

```bash
brew tap a2aproject/a2a-cli https://github.com/a2aproject/a2a-cli
brew install a2a

a2a card get http://127.0.0.1:9017
a2a send -a http://127.0.0.1:9017 "What is the capital of France?"
```

### Deterministic TCK fixture

Do not use the LLM-backed QA agent to score fixture semantics. The deterministic
fixture routes TCK prefixes such as `tck-artifact-text` to exact protocol
responses while exercising the same SDK server, AgentCard, task store, and
event executor used by MAS Lab.

History acknowledgements avoid repeating the TCK's user-message markers because its ordering check currently matches text without distinguishing message roles. Agent echoes are valid A2A behaviour. This fixture adjustment avoids a test false positive without changing runtime history handling.

```bash
MAS_LIBRARY_PATHS="$PWD" ../../../.venv/bin/mas-ctl serve \
  "$PWD/agents/a2a-tck-agent.yaml" \
  --infra-ref "$PWD/infra/a2a-tck-positive.yaml"
cd vendor/a2a-tck
.venv/bin/python run_tck.py --sut-host http://127.0.0.1:9021
```
