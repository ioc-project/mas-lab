<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 16 — Identity

A tool server can ask who is calling. You add that without touching the agent or the code that calls the tool: an
identity is a **record** (which scheme produces it, with which params), a **layer** on the way to the tool server that
presents it, and, when you want a policy on top, a **Control** that refuses a call before it starts.

The scenario is a fare desk. The agent `desk` calls the tool `get_fares` on an MCP server. You make the calls carry the
desk's identity, check the setup before running anything, and gate the tool on what the identity covers.

Each kind of file owns one thing:

| File | Owns |
| --- | --- |
| MAS and Agent manifests | Which agents exist and which tools they may call. No identity, no credential: `mas-ctl validate` refuses one. |
| Infra `kind: Identity` | The **records**: an id, a scheme and its params. A secret param is a `{secret: NAME}` reference, never a value. |
| The tool server row's `pipeline` | Where the identity layer sits on the way to the server, and which record it presents. |
| Control | The policy: which facts of the identity a tool needs. |

## What you need

- **Packages:** `ctl/` and `library-standard/` (the standard install).
- **Prerequisite:** [Tutorial 4](../04-mcp-tools/README.md) (tool servers). No LLM key and no network: the issuer key is a
  throwaway entry of a local secret store, and the tests run the model as a script.
- Run every command from the repository root. `pytest tests/tutorials/test_tutorial_16.py` runs this tutorial offline.

**A stand-in, in plain words.** The scheme here, `signed_badge`, is a small fake with the shape of a real credential: the
holder presents a subject, some claims and a signature in one header, and its claims are what the gate may read. It
interoperates with nothing. A real scheme is a separate plugin that passes the same conformance kit ([Identity](../../references/identity.md)).

| File | What it is |
| --- | --- |
| `mas.yaml`, `agents/desk/agent.yaml`, `tools/get_fares.tool.yaml` | The desk. Never edited. |
| `infra/tools.yaml` | The tool server as it was (Step 1). |
| `infra/tools-identity.yaml` | The same server with the identity layer on its row (Step 2). |
| `infra/identity.yaml` | The record `desk-badge`: scheme `signed_badge`, issuer key `{secret: desk-issuer-key}`, group `infraadmin`. |
| `flavours/gated.yaml`, `flavours/controls/fares-gate.yaml` | The Control: `get_fares` needs group `infraadmin` (Step 3). |
| `agents/desk/manager.yaml`, `flavours/controls/wallet-guard.yaml` | The desk that manages its own identities, and the guard that shuts the calls that change them (Step 5). |
| `broken/` | Manifests that are wrong on purpose. |

## Step 1 — No identity

The server row says where the server is and how to talk to it. Nothing says who may call it.

```bash
cat docs/tutorials/16-identity/infra/tools.yaml
mas-ctl identity list docs/tutorials/16-identity/mas.yaml --infra-ref infra/tools.yaml
```

The list is empty: no `Identity` row, and no identity code runs.

## Step 2 — Give the desk an identity

The record names a scheme and its params. The key is a reference to the secret store, so the file holds no secret. The
server row lists the identity layer in its `pipeline`: it sits between the HTTP transport and MCP and names the record.

```bash
mas-ctl --secrets-dir /tmp/t16-secrets secrets init
printf %s "a-throwaway-issuer-key-0123456789abcdef" | mas-ctl --secrets-dir /tmp/t16-secrets secrets set desk-issuer-key

ARGS="docs/tutorials/16-identity/mas.yaml --infra-ref infra/tools-identity.yaml --infra-ref infra/identity.yaml"
mas-ctl --secrets-dir /tmp/t16-secrets identity list $ARGS
mas-ctl --secrets-dir /tmp/t16-secrets identity check $ARGS
mas-ctl --secrets-dir /tmp/t16-secrets identity describe desk-badge $ARGS
```

`list` shows the record, its scheme, the names of its secret params and who uses it, never a value. `check` is the
preflight to run before a session: it fails, and says how to fix it, when a scheme is not installed, a param is invalid
or a secret is not set. `describe` prints the **public view** of the identity: what a policy may know without a proof.

Run `check` against an empty store to see the missing-secret message. A session that starts with that secret missing stops
before the first call, with one `identity.startup` event and no value in it.

## Step 3 — Gate a call on what the identity covers

The gate is policy, not identity. It reads the public view and refuses the call before it starts:

```yaml
spec:
  governance:
    - identity-gate:
        max_age_s: 30
        requirements:
          get_fares:
            identity: desk-badge
            require:
              - {fact: group, equals: infraadmin}
```

```bash
mas-ctl --secrets-dir /tmp/t16-secrets identity check $ARGS --flavour docs/tutorials/16-identity/flavours/gated.yaml --require-gate
```

With group `infraadmin` the call runs. With group `dev` the gate blocks it with `scope_uncovered:group`, and the event it
publishes says so (`identity.gate`, `denied`) without saying what the group was. The identity layer still enforces on its
own: a call whose identity cannot be presented is never sent, whatever the gate said.

## Step 4 — Wrong on purpose

| File | What `mas-ctl` says |
| --- | --- |
| `broken/dangling.yaml` | The layer names a record no `Identity` row declares: the message names the step and the known records. |
| `broken/literal-secret.yaml` | The key is a value, not a `{secret: NAME}`: `check` names the field (`$.key: secret_ref`), never the value. |
| `broken/agent-with-identity.yaml` | An Agent names an identity: `mas-ctl validate` refuses it. Identity is infra. |

## Step 5 — Let the agent manage its own identities

An agent can look at its own identities and ask for a narrower one. Naming the `identity` system plugin gives the model four
tools that work on identity ids: it never sees a credential, and the records stay in infra.

```bash
cat docs/tutorials/16-identity/agents/desk/manager.yaml
cat docs/tutorials/16-identity/flavours/controls/wallet-guard.yaml
```

`identity_list` and `identity_describe` change nothing and pass. `identity_delegate` and `identity_revoke` change what the
agent can do, so the guard blocks them until a policy allows them: the event is `identity.gate`, `denied`, `guard_denied`, with
the tool as its operation, before the call starts. `allow: {identity_revoke: hold}` would ask a human first.

## Extensions

A real scheme is a plugin of its own: it subclasses the wallet contract in `mas.library.standard.lib.identity.contract`,
passes `SchemeConformance`, and appears in an `Identity` record by name. Nothing in this tutorial changes.

## Recap

- An identity is a record in infra and a layer on the way to a server; the agent never names one.
- A secret is a reference until the moment the scheme uses it.
- `mas-ctl identity check` finds the mistakes before a session does.
- A gate refuses a call from what an identity covers, and fails closed.
- The agent can manage its own identities through four id-only tools, and the guard shuts the ones that change them.
