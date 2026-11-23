<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Hardening: the production posture

MAS Lab is permissive by default so an experiment starts in one command: no caller is verified, plain
http on a laptop is fine, the LLM cache replays, a human-in-the-loop request can approve itself. None of
that is acceptable once an agent serves other people. This page describes the **production posture**: a
named set of checks that a deployment turns on in one place, and that then refuse to start a session
that fails one.

Terms: a *check* is a small function that reads the resolved session and returns what is wrong, if
anything. A *posture* is how a failed check is treated: `default` reports it, `production` refuses to
start. A *waiver* accepts one failed check and records why. A *flavour* is the glue that links the logic
spec to the infra ([flavour.md](../manifests/flavour.md)).

## Why the default stays permissive

The checks are a security checklist, but a checklist that blocks a notebook run teaches people to switch
it off. So nothing here runs unless a Flavour selects `production`. A session without the selection
starts, logs and behaves exactly as before: no check is evaluated, no line is printed, no spec is
rewritten. You can still read the advice for any manifest with `mas-ctl hardening check`, which exits 0
under the default posture.

## Selecting the posture

The selection lives in the Flavour, next to the other things that make a deployment differ from a laptop
run. The bundled `prod` Flavour sets `hardening.profile: production` (plus `require_usage` and the `with-hardened` Control), so
`--flavour prod` is the production posture; `local`, the default, sets nothing. For your own Flavour file
write the block below; `--override` selects it for one run.

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata: {name: desk-prod}
spec:
  hardening:
    profile: production          # omitted or `default`: nothing runs
    waive:
      - check: plain-http-remote
        reason: the model gateway is on the cluster network
```

`--override '.flavour.spec.hardening.profile=production'` selects it for one run. A malformed block (an
unknown profile, a waiver with no reason or for an unknown check) is an error, never a silent default.

Under `production`:

- **Session start.** Each agent's session is judged before its engine is built. A failed check that no
  waiver covers raises one error that names every failure and its fix. The same check list is judged
  before `expose_agent` binds a listener (`mas-ctl serve` passes the Flavour), so a listener with no
  caller verification never binds.
- **Run time.** The `gov_hardening_profile` plugin (declare it in a Control, see
  [its card](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/hardening-profile.md))
  records the report at the first egress as a `governance_decision` and blocks a tool call whose
  arguments carry a url to a host outside `allowed_hosts`. `mas-ctl` hands it the report; a spec that
  does not declare the plugin is left as it is.
- **Files.** `mas-ctl validate PATH --profile production` runs the checks over manifest files read alone
  (Agent, Application, ToolServerRegistry, LLMProxy) and exits 1 on a failure. `--profile default` only
  prints them.

A waiver is exactly `{check, reason}`; the reason cannot be empty. The report (`mas-ctl hardening check
--json`, the plugin's record) lists every waived check with its reason, so a waiver is visible. A waiver
is the escape hatch of the bundled `prod` Flavour too: a deployment's own Flavour overrides individual
checks explicitly (`--flavour desk-prod.yaml` with a `waive` list), never the whole posture by accident,
and the admission log reports every override: when a report holds a waived check the session start
logs its summary at warning level, naming the waived checks.

## What `--flavour prod` selects

| Part of the posture | Check or behaviour |
| --- | --- |
| Fail-closed exposure | A session or listener that fails any static check does not start or bind. |
| Identity before the model | `listener-without-identity`: a listener off loopback must verify its callers before it binds, so no request reaches the model unauthenticated. |
| Caller-keyed sessions | With `a2a.admission.callers`, a conversation is keyed by (caller, `contextId`). |
| Error text and size caps | Peer text in errors is cleaned and cut to 256 characters in every posture; `sizes-unbounded` refuses a `max_message_size` over the shared limit. |

"Identity before the model" is stated as a requirement on admission, not as a dependency: an exposure
needs an inbound verifier configured on its row (today `a2a.admission.callers`, or a SLIM shared
secret). Until one is configured the check fails closed with its fix. The identity branches of MAS Lab
(broker, layer, gate) provide richer verifiers behind the same requirement; this branch names and
imports none of them.

The bundled `prod` Flavour also attaches the `with-hardened` Control to every agent (`spec.control` with
`agents: $all`), so `ingress-errors-unclassified` passes for an agent with no ingress governance of its
own and a plain `--flavour prod` run starts. Your own Flavour file does not inherit it: attach the Control
there, or waive the check.

## The checks

`mas-ctl hardening list` prints this table from the registry. The `scope` column says where the check is
decided: `static` before the session starts, `runtime` on each call (the report shows it as `deferred`).

| Id | Severity | Scope | Why | Fix |
| --- | --- | --- | --- | --- |
| `listener-without-identity` | critical | static | A listener that verifies no caller lets anyone who can reach the port run the agent and its tools. A listener on loopback is exempt. | Give the A2A row `a2a.admission.callers` (name to `{secret: NAME}`), or serve on loopback only. SLIM counts as verified (its shared secret is required); an MCP listener has no admission policy in this release and fails. |
| `tls-verification-disabled` | critical | static | Without certificate checks a network peer can pose as the model, tool or agent endpoint. | Remove `verify_ssl: false`, `dev: true`, `insecure: true`; use `ca_bundle` for a private CA. |
| `secret-store-env` | high | static | The env store reads process variables; a production session should use the file store. | Set `secrets.store: file` (the default) and `mas-ctl secrets set NAME`. |
| `secret-store-loose-permissions` | high | static | A file store that allows group- or world-readable files leaks secrets on disk. | Remove `allow_loose_permissions` so files stay mode 0400 and the directory 0700. |
| `plain-http-remote` | high | static | Plain http sends prompts, tool arguments and credentials where any hop can read and change them. | Use https, or keep plain http on loopback. |
| `hitl-auto-approve` | high | static | An agent that approves its own human-in-the-loop requests has no human in the loop. | `hitl_mode: interactive`, or `auto-deny` for an unattended run. |
| `llm-cache-replay` | high | static | A cached model answer replays its tool calls: the same side effects without a new decision. | Remove the `llm_cache` step and `cache.read`. |
| `tool-retry-not-idempotent` | high | static | Retrying a tool that already acted can act twice. | Leave `require_idempotent` at its default (true). |
| `egress-host-allowlist` | high | runtime | A tool call can carry a url the model or a peer chose. | Declare `gov_hardening_profile` with `allowed_hosts`. |
| `mcp-tools-wildcard` | medium | static | A server that adds a tool later gives the model that tool with no review. | List the tool names in the row's `tools`. |
| `sizes-unbounded` | medium | static | A message cap that is missing or above the shared limit lets one peer exhaust memory. | `max_message_size` between 1 and 1 048 576, or unset. |
| `ingress-errors-unclassified` | medium | static | Without an ingress plugin an engine ERROR is not classified, so nothing retries, blocks or stops it. | Attach `retry_on_error` (the `with-hardened` Control does). |

What MAS Lab already refuses in every posture is not repeated: plain http for a transport on a
non-loopback host, a SLIM shared secret under 32 bytes, a url with credentials, a header with a control
character, `require_usage`.

## Caller verification and sessions

An A2A row can name its callers:

```yaml
spec:
  endpoints:
    desk:
      protocol: a2a
      usage: listen
      url: https://desk.example.com
      a2a:
        listen: {host: 0.0.0.0, port: 8443}
        admission:
          callers:
            billing: {secret: desk-billing}     # at least 32 bytes
            portal:  {secret: desk-portal}
```

Every request but the public agent card needs `Authorization: Bearer <token>` of one entry. The caller
is the **name in this map**, never anything the request says about itself (a header, a body field, a
claim inside the token). Values are SecretRefs, revealed on every request through the secret resolver,
so a rotated token is used at once; a missing or unreadable token, one under 32 bytes, or one two callers
share admits no one (401). Tokens are compared in constant time over the whole table. An empty map, a
literal token and `grpc_port` next to `admission` are refused at start: an empty policy is never read as
open, and the gRPC listener does not enforce admission.

A conversation is keyed by (caller, `contextId`). The `contextId` is chosen by whoever sends it, so keyed
by it alone a second caller that learned an id joined the first caller's conversation and its history.
With a verified caller the key holds both, the caller still sees the id it chose, and the A2A SDK's task
store is scoped by the same caller. A listener that verifies no one has no caller and keys by the id as
before (it cannot tell callers apart, which is what `listener-without-identity` reports); such a listener
refuses an id shaped like a keyed one.

## Peer text in errors and logs

Text a peer, a server or a library sent is one short printable line wherever MAS Lab quotes it:
`mas.library.standard.lib.comm.text.safe_text` turns line and tab breaks into a space, drops every other
control, format (bidi, zero-width) and line-separator character, and cuts to 256 characters. It is
applied to A2A error objects and failed-task text, A2A client and probe errors, SLIM binding errors,
the HTTP transport error, MCP tool errors (the tool result the model sees) and the MCP failure log
lines. The text is cleaned and bounded, not removed: an A2A peer's error message still reaches the
caller, because tools and tests read it. A test fails when an `AgentCommError` in `library-ioa` quotes a
raw exception again.

## Not covered

- No redaction hook or redactor exists in MAS Lab, so there is no check for one.
- An MCP stdio server runs with the MCP SDK's default environment (a small safe set), not the full
  environment, so no check flags it.
- Admission covers HTTP A2A only. SLIM verifies by its shared secret; MCP and the webserver have no
  admission policy.
