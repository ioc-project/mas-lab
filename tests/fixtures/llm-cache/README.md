<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CI LLM cache fixtures

Offline CI (lab-smoke, golden runs, `task verify-chat-smoke`, CLI HITL)
replays `ci.llm-cache.json` via `ci-replay.yaml` in front of
`standard:openai`. A miss with `raise_on_miss: true` fails the run
instead of calling a provider. An empty fixture is a CI failure, not a skip.

Re-record after prompt, tool, or overlay changes (needs a live provider):

```bash
# from repo root, after `echo KEY | mas-ctl secrets set openai`
python scripts/record_ci_llm_cache.py
```

Optional: `MAS_RECORD_PROVIDER` selects the inner bundle (default:
`~/.config/mas/infra/llm-proxy.yaml` if present, else `standard:openai`).

Then commit the updated `ci.llm-cache.json` and recaptured golden events.

Chat-smoke also replays `web-search` from `tests/fixtures/llm-cache/web-search/`
(`MAS_WEB_SEARCH_CACHE`, `MAS_WEB_SEARCH_OFFLINE=1`). Re-record copies
DuckDuckGo hits into that directory. An offline cache miss fails instead of
calling the network.
