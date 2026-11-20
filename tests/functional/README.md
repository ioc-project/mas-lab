# Functional tests

Tests here need something CI does not provide: network access, a Docker daemon, or API keys.
They carry the `functional` pytest marker, which the default run excludes
(`addopts = -m "not functional and not perf"` in the root `pyproject.toml`), so CI never collects them.

| File | Needs |
| --- | --- |
| `test_huggingface_live.py` | network and the `datasets` package |
| `test_demo_e2e.py` | a running demo server on `localhost:8090` |
| `test_docker_scenarios.py` | Docker daemon and the `cli` image: `docker compose --profile tools -f docker/compose.yaml build cli` |

Run them locally, from the repository root:

```bash
task test-functional
# or
pytest tests/functional -m functional
```

Docker steps that call a live LLM also read `docker/.env` (and `OPENAI_API_KEY` where the scenario needs it).
