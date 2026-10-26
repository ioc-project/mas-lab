#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared CLI help epilogs for mas-ctl commands."""

COMPILE_EPILOG = """
Examples:
  mas-ctl compile agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml
  mas-ctl compile agent.yaml -o overlays/tools.yaml -O compiled-agent.yaml
  mas-ctl compile mas.yaml -o overlays/linear.yaml -O ./compiled/
  mas-ctl compile mas.yaml -o overlays/linear.yaml --layout bundle -O team.yaml

Layouts:
  auto    YAML file or stdout → one document; directory → mas.yaml + agents/
  tree    Always a folder (MAS keeps one file per agent; Agent writes agent.yaml)
  bundle  Always one YAML file (MAS inlines agents into agency.agents)

``compose`` emits EffectiveBind + placement. ``compile`` dumps the resolved spec.
"""

CHAT_EPILOG = """
Interactive session commands (at the You: prompt):
  /quit, /exit, /q     End the session
  /reset               Clear working memory and turn history; restore system prompt
  /steer <text>        Inject operator steering mid-run (updates context)

Session id (A2A contextId) is printed at start. Chat advertises control in
the platform runtime directory; attach with: mas-ctl control attach SESSION_ID
Use --no-control to skip. Override the directory with --control-dir or MAS_CONTROL_DIR.

Governance / HITL (when spec.governance.hitl_on_tool is set):
  Interactive mode (-i) prompts on stderr: SCHEDULE (allow), BLOCK, SKIP
  Batch (-q) auto-approves unless hitl_mode: auto-deny in overlay
  Full-screen HITL: mas-ctl tui (same overlays)

Context model (see docs/design/context-sources.md):
  Working memory  — τ ledger + ctx assembly for the current turn
  Chat history    — prior user/agent turns (ctl session; optional plugin feed)
  Persistent memory — memory plugin (semantic / episodic backends)

Examples:
  mas-ctl chat agent.yaml -i -o overlays/tools.yaml --trace
  mas-ctl chat agent.yaml -i -o overlays/tools.yaml --trace full
  mas-ctl chat agent.yaml -i -o overlays/tools.yaml --trace --trace-color

LLM response cache is on by default (read and write). A repeat run can return
in tens of milliseconds with identical wording. Pass -v to log cache hits
(and other internals such as summarization). Disable reads with
--no-cache-read or MAS_LLM_CACHE_READ=false.

Trace (--trace, or mas_ctl.trace in config.yaml):
  Human exchange log on stderr (stdout stays the conversation). Bare --trace is
  summary: headers + timestamps, untruncated AGENT→USER. --trace full dumps
  payloads. --trace-color is opt-in (never the default). --no-trace disables a
  config.yaml default. Machines use events.jsonl / mas-lab telemetry, not this
  log. -vv or --trace-engine adds raw InvokeEngineIo / EngineIoReturn JSON.
"""
