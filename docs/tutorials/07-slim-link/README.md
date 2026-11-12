<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 7 — SLIM link

You move the calls between agents onto a chain of **layers** without changing the
MAS or the agents. First the desk runs with the library default (`local`: a direct
in-process call, no medium). Then agents get names on an in-process channel
(`local-group`, still no node). Then you swap that bottom layer for a SLIM **node**,
and split the agents across two processes, which only the node allows.

The scenario is a support desk. `triage` takes a request and hands it to `billing`
(invoices, refunds) or `outage` (service problems). The billing team now wants to
run `billing` themselves, in their own process.

Tutorial 5 solved this with one URL and one server per remote agent. A shared medium
solves it differently: agents join one medium and call each other **by name**. SLIM
(Secure Low-latency Interactive Messaging) is such a medium: a message node that
agents connect to.

Three kinds of file take part, and each one owns one thing:

| File | Owns |
| --- | --- |
| MAS and Agent manifests | Which agents exist, what they say, who works with whom. No protocol, no endpoint. |
| Infra (`kind: Application`) | What exists: each **row** is a server or endpoint named by what it is (a SLIM node with its `url`, `identity` and the *name* of the env var that holds a secret; an A2A server; an in-process channel), never an agent. Its `usage` is what it can do: `connect` (dial agents served there), `listen` (host agents we run). |
| Flavour | The glue: `spec.bind.agent_comm` is the chain of rows every agent rides by default, bottom first; `spec.bind.agents.<id>` gives one agent its own chain, says whether this deployment hosts it (`listen`), reaches it (`connect`), or both, and can override a row's params for it. It defines nothing itself. |

## What you need

- **Packages:** `ctl/` and `library-ioa/`. Step 3 on needs the `slim` extra of
  `library-ioa/`, which also gives you the `mas-slim` command:
  `uv pip install -e "library-ioa[slim]"`.
- **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md) (install, LLM access).
- **Docker**, only from Step 3, for a local SLIM node. Steps 1 and 2 need no node.
- An LLM key for the `run-mas` commands.
- Run every command from the repository root. Everything used here is in this folder.

| File | What it is |
| --- | --- |
| `mas.yaml`, `agents/*/agent.yaml` | The desk. Never edited in this tutorial. |
| `specialists.yaml` | `billing` and `outage` as a MAS of their own (Step 4). |
| `infra/in-process.yaml` | The in-process row `desk-group` (Step 2). |
| `infra/slim-node.yaml` | The node rows `desk-node` and `desk-node-a2a`, and the A2A framing row `desk-a2a` (`protocol: a2a-framing`, Steps 3 to 5). |
| `infra/outage-server.yaml` | The A2A server row `outage-server` (Step 5). |
| `infra/agent-hub.yaml` | One A2A hub row for many agents, with `{agent}` in its url (Step 5). |
| `flavours/*.yaml` | How each deployment links the agents to those rows. |
| `flavours/broken/`, `infra/broken/` | Manifests that are wrong on purpose (Step 6). |

## Step 1 — The desk, with no chain

Run the MAS as it is:

```bash
export MAS_CTL_MODEL=gpt-4o-mini   # or any model your Tutorial 0 setup serves
mas-ctl run-mas docs/tutorials/07-slim-link/mas.yaml --trace \
  -q "I was charged twice for my March invoice. What should I do?"
```

The trace shows the whole flow: `triage` calls the model, the model picks the tool
`delegate_to_billing`, `billing` answers, `triage` relays. The workflow lists the
agents; `triage` is the entry, and it delegates to the nodes it names:

```yaml
workflow:
  entry: triage
  nodes:
    - id: triage
      delegates_to: [billing, outage]
    - id: billing
    - id: outage
```

`triage` delegates only to the agents its node names. `delegates_to` is deprecated
(you will see its warning, as in every tutorial) but it is still the way to declare a
link; with none, there is no link. Now ask infra how these agents talk:

```bash
mas-ctl infra resolved docs/tutorials/07-slim-link/mas.yaml
```

```text
agents
  triage  local
  billing  local
  outage  local
```

`local` is a direct call inside the process. There is no medium and no node. Agents
have no name and no address. That is why `billing` cannot run anywhere else.

## Step 2 — Give the agents names on an in-process chain

Declare what exists. One row is the in-process channel; its `identity` is the
three-part `org/ns/service` every agent name on it starts with:

```yaml
# infra/in-process.yaml
apiVersion: infra/v1
kind: Application
metadata: {name: desk-in-process}
spec:
  endpoints:
    desk-group: {protocol: local-group, identity: acme/lab/desk, usage: [connect, listen]}
```

The row says nothing about agents. `usage` is what the row can do: here, both reach
agents (`connect`) and host the agents this process runs (`listen`). A row with no
`usage` can only `connect`, as on every Application row. The Flavour links the agents
to it:

```yaml
# flavours/in-process.yaml
apiVersion: flavour/v1
kind: Flavour
metadata: {name: desk-in-process}
spec:
  bind:
    agent_comm: [desk-group]
    usage: [connect, listen]
```

| Field | Meaning |
| --- | --- |
| `bind.agent_comm` | Row names, bottom first: the default chain of every agent. The first row is the transport. |
| `bind.agents.<id>.agent_comm` | One agent's own chain, instead of the default (Step 5). |
| `bind.usage` | This deployment's default for agents that omit their own usage. Default `connect`. Set `[connect, listen]` to host everyone on the default chain. |
| `bind.agents.<id>.usage` | `connect`: this deployment reaches the agent over its chain; `listen`: this deployment hosts it. Omitted, it is `bind.usage`, else `connect`. It must be within the `usage` of every row of the chain. |
| `bind.agents.<id>.params` | Overrides of a row's params for that agent, by row name (Step 5). |
| Row `protocol` | The layer plugin (`local-group`, `slim`, `http`, `a2a`, `a2a-framing`). The row's other fields are that layer's params. |
| Row `identity` | `org/ns/service`. Each agent on that layer is `<identity>/<agent_id>`. |
| Row `usage` | The row's capability: `connect`, `listen`, or both. Default `connect`. |

`bind` in a Flavour is unrelated to the deprecated `--bind` CLI flag, which is an
alias of `--connect`.

Pass the Flavour and the row to `infra resolved`, then run the desk again:

```bash
mas-ctl infra resolved docs/tutorials/07-slim-link/mas.yaml \
  --flavour docs/tutorials/07-slim-link/flavours/in-process.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/in-process.yaml"

mas-ctl run-mas docs/tutorials/07-slim-link/mas.yaml --trace \
  --flavour docs/tutorials/07-slim-link/flavours/in-process.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/in-process.yaml" \
  -q "I was charged twice for my March invoice. What should I do?"
```

```text
agents
  triage  local-group  acme/lab/desk/triage
  billing  local-group  acme/lab/desk/billing
  outage  local-group  acme/lab/desk/outage
```

Same answer, same trace. Every agent now has a name. That name is the contract
Step 3 relies on: the caller says `billing`, and the chain decides how to reach it.
Every agent in the process gets its own copy of the layers and its own receive;
what they share (here, one in-process fabric) is created once per session.

## Step 3 — Swap the medium for a SLIM node

The node row says where the node is (`url`), the same `identity`, and how to join
(`secret_env`, the *name* of an environment variable that holds a shared secret of
at least 32 bytes; the manifest never holds the secret):

```yaml
# infra/slim-node.yaml
spec:
  endpoints:
    desk-node: {protocol: slim, url: "http://127.0.0.1:46357", identity: acme/lab/desk, secret_env: SLIM_SECRET,
                usage: [connect, listen]}
    desk-a2a: {protocol: a2a-framing, usage: [connect, listen]}
    desk-node-a2a: {protocol: slim, url: "http://127.0.0.1:46357", identity: acme/lab/desk-a2a,
                    secret_env: SLIM_SECRET, usage: [connect, listen]}
```

`flavours/slim.yaml` binds `[desk-node]`. The MAS and the agents stay as they are.

A SLIM node is a separate program. Start one in Docker, loopback only. This tutorial
was run with `ghcr.io/agntcy/slim:latest`, whose `/slim --version` was 3.4.6. The node's
config goes in a temporary directory, not in the repository:

```bash
SLIM_DIR="$(mktemp -d)"
cat > "$SLIM_DIR/slim-node.yaml" <<'YAML'
runtime: {n_cores: 0, thread_name: slim-data-plane, drain_timeout: 10s}
services:
  slim/0:
    dataplane:
      servers:
        - endpoint: "0.0.0.0:46357"
          tls: {insecure: true}
      clients: []
YAML
docker run -d --name mas-lab-slim-node -p 127.0.0.1:46357:46357 \
  -v "$SLIM_DIR/slim-node.yaml:/config.yaml:ro" \
  ghcr.io/agntcy/slim:latest /slim --config /config.yaml
export SLIM_SECRET='a secret at least 32 bytes long....'
```

Plain HTTP is only allowed on `127.0.0.1`, `localhost`, and `::1`. Any other node
needs HTTPS.

Before involving the MAS, check the node, the secret and the names by hand, with no
model. `mas-slim` joins the node as one agent, on a one-layer `slim` chain. It reads
the secret from standard input (or `--secret-file PATH`), never from the
environment. In a first terminal, `billing` listens:

```bash
printf %s "$SLIM_SECRET" | mas-slim listen --identity acme/lab/desk --agent billing
```

In a second terminal, `triage` sends it a request by name:

```bash
printf %s "$SLIM_SECRET" | mas-slim send --identity acme/lab/desk --as triage billing "I was charged twice"
```

It prints `ack: I was charged twice`, and the first terminal prints
`[billing] I was charged twice`. Sending to the identity itself,
`... mas-slim send --identity acme/lab/desk --as triage acme/lab/desk "anyone?"`, is
**anycast**: the node picks one listener other than the sender. When a MAS run fails
later, this tells you whether the problem is the node and the secret, or the agents.
Stop the listener with Ctrl-C before the next command: the desk registers `billing`
too, and two agents with one name break each other.

Now run the desk against the node:

```bash
mas-ctl run-mas docs/tutorials/07-slim-link/mas.yaml --trace \
  --flavour docs/tutorials/07-slim-link/flavours/slim.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml" \
  -q "I was charged twice for my March invoice. What should I do?"
```

Same answer again. The names are identical (`acme/lab/desk/billing`), but the call
from `triage` to `billing` now travels through the node. All three agents are still
in one process, so you gain nothing yet. Step 4 is where the node pays off.

## Step 4 — Split the desk across two processes

The billing team runs `billing` and `outage` in their own process. The front desk
keeps `triage`. Only the Flavour changes: the front desk dials the specialists but
no longer serves them:

```yaml
# flavours/front-desk.yaml
spec:
  bind:
    agent_comm: [desk-node]
    usage: [connect, listen]
    agents:
      billing: {usage: connect}
      outage: {usage: connect}
```

`infra resolved` confirms it: only `triage` has a name in this process.

```bash
mas-ctl infra resolved docs/tutorials/07-slim-link/mas.yaml \
  --flavour docs/tutorials/07-slim-link/flavours/front-desk.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml"
```

```text
agents
  triage  slim  acme/lab/desk/triage
  billing  slim
  outage  slim
```

In a first terminal, the billing team starts the specialists. `specialists.yaml` is
`billing` and `outage` as a MAS of their own, and `-i` keeps the process alive. With
`flavours/slim.yaml` both agents are served there:

```bash
mas-ctl run-mas docs/tutorials/07-slim-link/specialists.yaml -i \
  --flavour docs/tutorials/07-slim-link/flavours/slim.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml"
```

In a second terminal, the front desk runs with the same prompt as before:

```bash
mas-ctl run-mas docs/tutorials/07-slim-link/mas.yaml --trace \
  --flavour docs/tutorials/07-slim-link/flavours/front-desk.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml" \
  -q "I was charged twice for my March invoice. What should I do?"
```

The answer comes back, and `billing`'s reply also prints in the first terminal:
`billing` handled the request in its own process. Neither process knew the other's
address. Both joined `acme/lab/desk` on the same node. Stop the first terminal with
Ctrl-D.

## Step 5 — Mix transports

A desk does not have to send everything one way. Say `outage` is already an A2A
agent, as in [Tutorial 5](../05-a2a-agents/README.md). Its server is one more
endpoint row:

```yaml
# infra/outage-server.yaml
spec:
  endpoints:
    outage-server: {protocol: a2a, url: "http://127.0.0.1:9003", usage: [connect, listen]}
```

The outage team's Flavour serves `outage` there (`usage: listen`); the desk's Flavour
dials it there (`usage: connect`) and keeps everyone else on the node:

```yaml
# flavours/outage-server.yaml       # flavours/mixed.yaml
spec:                               spec:
  bind:                               bind:
    agents:                             agent_comm: [desk-node]
      outage:                             usage: [connect, listen]
        agent_comm: [outage-server]     agents:
        usage: listen                     outage:
                                            agent_comm: [outage-server]
                                            usage: connect
```

A chain of one A2A server row is the A2A client of Tutorial 5: the agent is reached
at that url. Serve `outage` in a first terminal, then run the desk in a second one:

```bash
mas-ctl serve docs/tutorials/07-slim-link/agents/outage/agent.yaml \
  --flavour docs/tutorials/07-slim-link/flavours/outage-server.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/outage-server.yaml"

mas-ctl run-mas docs/tutorials/07-slim-link/mas.yaml --trace \
  --flavour docs/tutorials/07-slim-link/flavours/mixed.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml" \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/outage-server.yaml" \
  -q "The API has returned 503 for ten minutes. Is there an outage?"
```

`triage` delegates to `outage` over A2A. Ask the billing question instead and the
same desk delegates to `billing` over SLIM. `delegate_to_outage` and
`delegate_to_billing` are the same tools either way. `--connect outage=a2a://...`
still works too: it writes a row keyed by the agent, as before Flavours linked
agents.

### A2A over the node

The chain can also speak A2A *over* the node: `flavours/a2a-over-slim.yaml` binds
`[desk-node, desk-a2a]`, the `a2a-framing` row above the node row.

Two ids name the two A2A roles. `protocol: a2a` is the A2A SDK client of one url, always
the bottom of a chain (`outage-server` above). `protocol: a2a-framing` frames each send
as an A2A request for the row below it, so it never sits at the bottom (`desk-a2a` here).

```bash
mas-ctl run-mas docs/tutorials/07-slim-link/mas.yaml --trace \
  --flavour docs/tutorials/07-slim-link/flavours/a2a-over-slim.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml" \
  -q "I was charged twice for my March invoice. What should I do?"
```

Each send now leaves as an A2A `SendMessage` JSON-RPC request inside the SLIM frame:
the text is a message part, the context id is the message `contextId`, and the call
ids ride the message metadata. On the serving side the `a2a-framing` layer reads the request
before `billing`'s turn runs and sends the answer back as an A2A agent message. A
turn that fails comes back as a failed A2A task, and the caller's tool returns a
failure. A2A has no inform/ask, so on this chain only plain delegation (`task`) is
sent; other intents are refused before anything leaves. The JSON-RPC text rides
inside MAS Lab's own SLIM request frame: this is MAS Lab's JSON-RPC, not the A2A SDK
riding SLIM, so both ends must be MAS Lab sessions with the `a2a-framing` row above the
node row.

The same framing works over HTTP: a row `{protocol: http, url: ...}` below the `a2a-framing`
row posts the request to an A2A server such as the `outage` one above. An `http`
chain only connects; `usage: listen` on it is refused, because serving over HTTP is
the job of `mas-ctl serve`.

### One row for many agents, and an override for one

A hub that serves many agents, each at its own path, is one row: `{agent}` in a
string param is replaced by each agent's id. It is the only placeholder, and it takes
ids of letters, digits, `.`, `_` and `-` as they are (the url grammar; SLIM is
stricter, see "Names cannot contain `.`" at the end).

```yaml
# infra/agent-hub.yaml
spec:
  endpoints:
    agent-hub: {protocol: http, url: "https://agents.example.test/a2a/{agent}", usage: connect}
```

`flavours/hub.yaml` reaches `billing` and `outage` there, with A2A framing above the
HTTP transport, and gives `outage` a url of its own:

```yaml
spec:
  bind:
    agent_comm: [desk-node]
    usage: [connect, listen]
    agents:
      billing:
        agent_comm: [agent-hub, desk-a2a]
        usage: connect
      outage:
        agent_comm: [agent-hub, desk-a2a]
        usage: connect
        params:
          agent-hub: {url: "https://outage.example.test/a2a"}
```

`billing` is reached at `https://agents.example.test/a2a/billing`, `outage` at
`https://outage.example.test/a2a`. The hub only reaches agents someone else hosts,
so its `usage` is `connect`. Omitting `usage` for `billing` is `connect` too (the
default). Set `usage: listen` to host `billing` there, which is refused:

```text
Flavour bind.agents.billing: usage listen is not a capability of row 'agent-hub' (its usage is ['connect'])
```

The hosts above are placeholders; `infra resolved` prints the chains without dialling:

```bash
mas-ctl infra resolved docs/tutorials/07-slim-link/mas.yaml \
  --flavour docs/tutorials/07-slim-link/flavours/hub.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml" \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/agent-hub.yaml"
```

### One agent on its own chain

`flavours/billing-over-a2a.yaml` keeps the desk on the node and puts only `billing`
on A2A framing:

```yaml
spec:
  bind:
    agent_comm: [desk-node]
    usage: [connect, listen]
    agents:
      billing:
        agent_comm: [desk-node-a2a, desk-a2a]
```

`desk-node-a2a` is a second connection to the same node under its own identity,
`acme/lab/desk-a2a`. Two chains may not share a bottom row: `triage` talks to both
`billing` and `outage`, so it joins both chains, and on one row it would register the
same name twice.

```bash
mas-ctl infra resolved docs/tutorials/07-slim-link/mas.yaml \
  --flavour docs/tutorials/07-slim-link/flavours/billing-over-a2a.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml"
```

```text
agents
  triage  slim  acme/lab/desk/triage, acme/lab/desk-a2a/triage
  billing  slim, a2a-framing  acme/lab/desk-a2a/billing
  outage  slim  acme/lab/desk/outage
```

A send from `triage` to `billing` leaves as an A2A request; a send to `outage`
leaves as a plain SLIM frame. Run the desk with this Flavour as in Step 3.

## Step 6 — What is refused, and what protects the node

Everything is checked before anything connects. Each case exits 1 with one line:

| Case | Error |
| --- | --- |
| `flavours/broken/unknown-agent.yaml`: `billng`, a typo | `Flavour bind.agents names 'billng', which is no agent of this session` |
| `flavours/broken/unknown-row.yaml`: `desk-nod`, a typo | `Flavour bind.agent_comm names 'desk-nod', which is no Application row` |
| `flavours/broken/wrong-first-layer.yaml`: the `a2a-framing` row first, the node after it | `Flavour bind.agent_comm: the first layer must be a transport; 'a2a-framing' needs a layer below it` |
| `infra/broken/bad-identity.yaml`: `acme/desk`, two parts | `identity must be a three-part org/ns/service name, got 'acme/desk'` |

```bash
mas-ctl infra resolved docs/tutorials/07-slim-link/mas.yaml \
  --flavour docs/tutorials/07-slim-link/flavours/broken/unknown-agent.yaml \
  --infra-ref "$PWD/docs/tutorials/07-slim-link/infra/slim-node.yaml"
```

A row named like an agent of the desk, a `usage` beyond a row's capability, a
placeholder other than `{agent}`, and a key named `secret`, `password`, `token`,
`api_key` or `authorization` are refused too. A
missing or short secret stops the run before it connects.

The secret is the access control. Anyone who holds `SLIM_SECRET` can register as any
agent name on the node. Start the `billing` listener again and, from a second
terminal, send with a different secret:

```bash
printf %s 'another secret, also long enough!!' | \
  mas-slim send --identity acme/lab/desk --as triage billing "I was charged twice"
```

```text
mas-slim: no peer answers to 'billing'
```

The sender connects without an error but cannot reach the desk's agents. Treat the
secret like a key.

## Step 7 — Clean up

```bash
docker rm -f mas-lab-slim-node
rm -rf "$SLIM_DIR"
```

Run the MAS with no `--flavour` and no `--infra-ref` and you are back to Step 1:
same MAS, same agents, in one process.

## Limits to know

- The tests in the repository (`tests/support/slim_node_fake.py`) use an in-process fake of the SLIM
  bindings. Real-node tests run with the pytest marker `slim` (`slim-bindings` 2.2
  and the Step 3 node).
- **Every entry point reads `bind`.** `run-mas`, `chat`, `tui`, `serve`, `infra resolved`
  and benchmark runs apply the same Flavour the same way. A one-agent `chat` or `tui`
  session joins a chain only when the Flavour serves it there (`listen`).
- **Two chains, two bottom rows.** Chains that share a bottom row are refused.
- **`mas-ctl serve` does not host an agent on a SLIM chain.** It exits and tells you
  to use `run-mas`, as in Step 4. It does host an agent the Flavour links to an A2A
  server row, as in Step 5.
- **Names cannot contain `.`.** A service or an agent id with a dot would collide on
  the node, so it is refused at bind time, on a `slim` row only; `local-group` and
  `http` rows accept such an id.
- **`local-group` is one process.** It cannot split a desk; only a node can.
- `--connect` stays `a2a://` and `mcp://`. A SLIM node is an infra row, not a CLI
  scheme.

## What you learned

- The MAS and the agents name no protocol. Infra rows declare servers and what they
  can do (`usage`); a Flavour links each agent to them and says what this deployment
  does with it.
- `bind.agent_comm` is the default chain; `bind.agents.<id>` gives one agent its own
  chain, its `usage` here (`connect`, `listen`, or both; default `connect`, within the rows' capability)
  and per-row `params` overrides. `{agent}` in a row lets one row serve many agents.
- Agents get a name on the chain, `<identity>/<agent_id>`. Callers use the name, and
  the chain decides how to reach it.
- Swapping `local-group` for `slim` changes one row name in the Flavour. A node lets
  you split agents across processes with no per-agent URL.
- A row above the transport changes every send without touching the agents:
  `a2a-framing` frames it as an A2A request. A chain of one `a2a` server row is the A2A
  SDK client.
- A mistake in a Flavour or a row fails before anything connects, and only ctl reads
  the secret from the environment.

Reference: [Flavour bind](../../manifests/flavour.md#bind),
[Rows a Flavour binds](../../manifests/infra.md#rows-a-flavour-binds),
[Communication stack](../../references/comm-stack.md),
[slim plugin card](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/src/library_ioa/plugins/slim/slim.md),
[Tutorial 5](../05-a2a-agents/README.md) for the one-URL-per-agent route, `mas-slim --help`.

## Next

[Tutorial 8 — Telemetry](../08-telemetry/README.md), or
[Tutorial 6 — Agent skills](../06-agent-skills/README.md).
