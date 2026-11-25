<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Identity

Identity is one contract, `identity-contract/0 (provisional)`, in `mas.library.standard.lib.identity.contract`. It holds
types and Protocols and no logic. A **record** names a scheme and its params; the scheme builds a **wallet**: a holder
of identities. A harness asks the wallet which identity to show an audience for a task, and shows it. How a scheme
obtains, delegates and exposes an identity on a protocol is the scheme's own business. Nothing in MAS Lab calls the
contract until a harness is bound to it; absent, nothing changes.

Who does what: the *holder* (a workload) shows evidence; the *issuer* enrols it and is outside the contract; the
*relying party* verifies; a *policy* reads facts and decides; a *harness* applies the contract on the caller and the
server side; a *human principal* may be the subject.

## The wallet

| Method | Question it answers | Required |
|---|---|---|
| `from_spec(params)` | Build the wallet from the record. Params are validated; a secret param is a `SecretRef`. | yes |
| `identities()` | Which identities does it hold? Value-free `IdentityInfo`: id, kind, subject, labels, status, grant, parent. | yes |
| `select(req)` | Which identity would it show this audience for this task, or none? Presents nothing. | yes |
| `present(req)` | What do I show? As `req["identity"]`, or the selected one. Phases `initiate`, `challenge`, `sign`. | yes |

A wallet of one identity is the common case: `identities()` lists one and `select` answers it. An identity is `active`,
`expired` or `revoked`. `present` is deterministic under a fake clock and a fake endpoint: two fresh instances answer
alike, one instance asked twice answers alike, the request is not mutated, and the clock is `req["now"]`, never the wall
clock. State (a cache, a refresh token) may speed a call up; a real token endpoint may hand out a new token.

Optional interfaces, each declared by subclassing it **and** by listing its name in `capabilities` (the kit fails when
they disagree):

| Capability | Interface | Question it answers |
|---|---|---|
| `verify` | `Verifier.verify(inbound)` | Who is calling, and is it valid? May raise `ChallengeRequired`. |
| `describe` | `Describer.describe(ref)` | What may a policy know about this identity without a proof? |
| `revoke` | `Revoker.revoke(ref)` | Stop using this identity now, and everything derived from it. |
| `handshake` | `Handshaker.handshake(ctx)` | What does the peer need inside the open session? |
| `delegate` | `Delegator.delegate(delegation)` | Derive a narrower identity into the wallet. |
| `sign` | a phase of `present` | Bind the evidence to this exact request. Called last, never cached. |

## Delegation

`Delegator.delegate` returns a new `IdentityInfo` whose `parent` names the identity it came from and whose `grant` is
inside the parent's. A `Grant` is value-free and closed: `scopes`, `audience`, `valid_until`, `depth`. A delegation only
narrows (`check_narrowing`): an absent axis means unknown, not everything; the child's depth is the parent's plus one;
a widening is `refused:delegation_widens`. The derived identity is shown with `present` like any other, asking the same
request twice gives the same identity, and revoking its parent revokes it.

A parent that is not in the wallet (the verified caller of a session, another record) arrives as
`Delegation.parent_evidence`: credential material to exchange or attenuate, never to echo. A scheme that needs it and
gets none refuses with `unsatisfiable:needs_parent_evidence`; it never presents its own credential in the parent's place.
`Verified.credential_keys` names the headers and `meta` keys that carried a caller's credential so a harness can build
that evidence from exactly those.

## Secrets

A parameter marked `"x-secret": true` in `PARAMS_SCHEMA` arrives in `from_spec` as a `SecretRef`
(`{secret: NAME}` in a manifest, see [Secrets](secrets.md)). The scheme reveals it with
`current_resolver().reveal(ref)` only inside the call that uses it, into a local variable. The object keeps the
reference, never the value. `from_spec` reads no environment, file or store. `validate(params, PARAMS_SCHEMA)` refuses a
plain value in a secret slot.

Credential material lives in one place, `Evidence`, which goes to the wire and nowhere else. Errors, reprs, public views,
challenges, grants and listed identities are value-free: `IdentityError` is a code and a short slug, and `str(error)` is
those and nothing else.

## What goes where

| Piece | Home |
|---|---|
| Types, Protocols, errors (`contract`) | `library-standard`, `lib/identity/contract.py` |
| Rules a host applies to what a scheme returns, and helpers for scheme authors (`validate`, `check_evidence`, ...) | `lib/identity/rules.py` |
| The explicit "no identity" scheme `none` (a wallet of one that accepts everyone) | `library-standard`, plugin `identity_scheme/none` |
| Base class for a server that only verifies (`VerifierOnly`) | `lib/identity/verifier_only.py` |
| Conformance kit, fakes and a scripted double | `library-standard`, `testing/identity_*` |
| Every other scheme (bearer, SPIFFE, DPoP, credentials, ...) | its own library |

What a protocol adapter supports is the adapter's to declare, not the contract's: the `advertise` sections, the
protocols whose message text a scheme may wrap with `Evidence.payload`, and the keys that carry a challenge. The rules
that need them take them as arguments.

## Writing a scheme

Subclass `IdentityScheme` and the optional interfaces you need; set `scheme`, `capabilities` and `PARAMS_SCHEMA`.

```python
class MyScheme(IdentityScheme, Describer):
    scheme = "my"
    capabilities = frozenset({"describe"})
    PARAMS_SCHEMA = {
        "type": "object",
        "required": ["token"],
        "additionalProperties": False,
        "properties": {"token": {"x-secret": True}},
    }

    @classmethod
    def from_spec(cls, params):
        if validate(params, cls.PARAMS_SCHEMA):
            raise IdentityError("invalid", "params")
        return cls(params["token"])  # a SecretRef

    def identities(self):
        return [IdentityInfo(id="main", kind="agent", subject="", labels=[], status="active")]

    def select(self, req):
        return "main"

    async def present(self, req):
        token = current_resolver().reveal(self._token)
        return Evidence(headers={"authorization": f"Bearer {token}"}, valid_until=req["now"] + 60, reusable=True)
```

Rules a scheme follows: use `req["now"]`, never the clock; return only `Evidence` keys; headers pass the caps and
deny-list (no `host`, `content-length`, `transfer-encoding`, `connection`); every `meta` key contains `/`; `valid_until`
absent means not cached; `reusable` defaults to false; `advertise` only on `initiate`; `payload` only on a message
protocol and never `reusable`. A request-binding scheme (a DPoP proof, an HTTP signature) uses phase `sign` and reads
the method, URL, body digest and `RequestView.request_id` the host makes unique per request, so a unique value per
request is derived from the id and the clock, never from randomness.

A verifier that unwraps message text sets `RETURNS_PAYLOAD = True`; one that verifies anyone on purpose sets
`ANONYMOUS_OK = True`; one that tells a caller what to present without issuing anything (a `Bearer` realm, an RFC 9421
`Accept-Signature`) sets `ADVISORY_CHALLENGE = True` and raises a static hint with no nonce. Any other challenge carries
a nonce, is single use and is bound to the audience.

## Conformance

`SchemeConformance` (in `mas.library.standard.testing.identity_conformance`) is a mixin: subclass it, set `make_scheme`,
`good_params` and, for a secret, `secrets` (the kit binds them as the process secret store, so the scheme reveals the
kit's canary), and pytest runs every check. A missing hook is a failed check, not a skipped one. The checks: the wallet
lists value-free, unique identities and `select` names a listed one; `present` is deterministic and shows the identity it
is asked for; what a scheme returns passes the rules; a verifier accepts its valid inbound, refuses it with the credential
removed or made for another audience, and echoes no credential; challenges are single use, audience-bound and size
capped; a delegating scheme delegates into its wallet, refuses without a parent it needs, derives from the parent, only
narrows and loses what it derived when the parent is revoked; no secret in any output or kept on the object (instance dict, slots and class attributes are scanned); a signing scheme's evidence changes when the request's body digest, url or request id does (`sign_binds_request = False` for a double that returns canned evidence); a holder never answers a challenge bound to another audience; capability
honesty both ways; a revoked identity refuses everything. Most checks are proved by deliberately broken schemes that must
turn them red.

## The host: caller and edge hooks

A wallet is never called directly. A host asks a `Caller` (`mas.library.standard.lib.identity.caller`), which makes every
answer a checked value or an `IdentityError`:

- a scheme that raises anything but an `IdentityError` is `unavailable`, with no text and no cause;
- every scheme call is bounded (30 seconds by default, `unavailable:timeout`): a scheme that waits on a network never
  holds a call, a session setup or a request open;
- what a scheme returns passes the rules, and an evidence already past its `valid_until` (within the clock skew) is
  `expired`; the clock is the caller's and reaches the scheme as `now`;
- a wallet that says `revoked` from `present`, `handshake`, `describe`, `delegate` or `revoke` is revoked from then on
  for that identity and what derives from it: nothing is presented for it again, its cache is dropped and every listener
  is told, so the sessions opened with it are closed. A `revoked` from `verify` is about the peer's credential, not this
  wallet, and revokes nothing here;
- every `revoke` call that ends with the identity revoked publishes exactly one `identity.revoke` event for it, also when the
  wallet answers `revoked` because it held it as revoked already; a listener hears a transition, not a repeat;
- a `delegate` result is checked to be inside its parent's grant (`refused:delegation_widens`). The parent is the identity named,
  else the one the wallet selects, and it must not be revoked. When `parent_evidence` is given it is the only bound: a parent
  outside the wallet whose evidence carries no grant refuses (`delegation_widens`), the wallet's own grant is never the
  fallback (a confused deputy);
- a wallet that cannot `select` refuses (`unavailable`); it is never read as "the whole wallet";
- an answer to a peer's challenge (`phase="challenge"`) is made only for the audience the challenge names: the challenge and
  its `wire` are checked like a verifier's, and another audience is `refused:wrong_audience` before the scheme is asked.
  A challenge a peer carries inside the session (an MCP `initialize` result, an A2A auth-required task) is the scheme's own
  shape: it must be a mapping with bounded `wire`, a string `audience` in it must be the target, and its other keys are not
  held to the `Challenge` grammar;
- a handshake is for the identity the session was opened with (`HandshakeContext.identity`, else the selected one), refuses
  when it is revoked, and returns `meta` only: `headers` are refused (`invalid:handshake_headers`);
- an A2A server does not keep what carried the credential: the `mas.headers` metadata and the metadata keys named by
  `Verified.credential_keys` are removed from the message before the SDK's task store sees it, so `GetTask` returns none;
  a refusal of a slimrpc call is kept under a key of the server's own for that RPC, never the caller's message id;
- an event cuts every id, operation, host and code to the rulebook's grammar and size, whatever a peer supplied;
- an inbound `meta` a peer chose is bounded (JSON values, at most 16 kB) before a verifier reads it, and a malformed challenge
  from a verifier is a denial that publishes its `identity.verify` event;
- the sync twin (`present_blocking`) hands the work to the runtime's loop when the runtime called the sync method on a
  worker thread, runs a loop of its own when this thread has none, and refuses a call made from a thread that runs a
  loop (`unsatisfiable:sync_in_loop`). Signing works on it the same way.

The `identity` layer (a `layer` plugin of `library-standard`) puts a wallet on a stack at the protocol edge. Its
parameters are `identity` (the record id), `scheme`, `params` (a secret parameter is a `SecretRef`), `audience`, `use`
(the wallet identity to present; absent, the wallet selects) and `scope`: `agent` (the default: the layers of one agent built from equal parameters share one wallet) or `process` (layers of any
agent do, and the wallet is held by no agent in particular, so no agent's model tools manage it). Either way one revocation
reaches every layer that shares the wallet.

A wallet is shared by every layer of one bind that names the same record and parameters (per agent, or per bind with
`process`): one revocation reaches all of them, and the gate and the tools see one wallet, not several. A gate reads the
wallets of the agent and the wallets no agent in particular holds (a stack shared by every agent), and a revoked one
of them denies; the gate only reads a shared wallet. The model's tools manage the agent's own wallets only,
never a shared one. A layer refuses a parameter it does not know (`agent_id` and `holder_scope`, stamped by the host, excepted).

Wallets are kept per bind. `mas-ctl` stamps one token per bind that uses identity into each identity layer (`holder_scope`)
and into each agent's spec (`identity_scope`); the gate and the identity tools look wallets up under that token. Two runs
in one process that name the same agent and record never read each other's wallet, a `process` wallet is shared only
within one bind, and outside `mas-ctl` (no token) behaviour is the unscoped one.

| Stack | Where it sits | What it does |
| --- | --- | --- |
| MCP, LLM (call stack) | below the protocol layer, on the HTTP call transport | presents for every request; answers one 401 with `phase="challenge"`; a 403 is a denial (a request to another place than the evidence was made for is refused before it is sent: another origin by the call transport, and for a signing scheme another path of the same origin too, since the signature covers the path); a header of the row that
names an evidence header is refused (`invalid:header_collision`) and the row's headers are on the request before it is signed; an
MCP session hook advertises, runs the handshake and closes the session when the identity is revoked |
| A2A over HTTP or slimrpc (send stack) | above the protocol layer | presents with the message, answers a peer's challenge once, signs on `phase="sign"` |
| inbound (A2A) | in front of the handler | verifies the caller; the handler never runs on a refusal; the conversation id is derived from the verified owner |

**Where inbound verification runs.** Verification of callers is enforced where the host puts the layers' inbound chain in
front of its handler: an A2A server over slimrpc (an `a2a-rpc` layer under an identity layer, built from the agent's chain),
and, through the library API only, the A2A HTTP server (`A2AExposure(inbound=...)`) and the MCP tool server
(`MCPToolServerFactory(inbound=...)`, streamable-http only: `run()` refuses an inbound chain on any other transport).
`mas-ctl serve` and `mas-mcp serve` do not build that chain from a row's pipeline, so a listen row that names a verifier
there is refused (`mas-ctl`: "cannot put in front of its handler") instead of serving callers unverified. A server shares one
wallet across the sessions it serves: what it keeps per caller is the conversation (its id is derived from the verified
owner), not the wallet's state.

A refusal reaches the model as `identity: <code>` and nothing else, so a refused session setup is a closed refusal class,
not an `unclassified` task group. A protocol declares what it carries (the `advertise` sections, the protocols whose
message text a scheme may wrap, the challenge `carry` keys); the contract names none of them.

### Events

The layer and the caller publish plain MAS Lab events, one small closed vocabulary, never a value: `identity.present`,
`identity.handshake`, `identity.verify`, `identity.challenge` (`issued` or `answered`), `identity.revoke`, `identity.gate`
(the gate's decision: `ok`, `denied` or `hold`) and `identity.startup` (a record whose secret is not set). A refusal
is an event of the kind that was refused with `outcome: denied` and a `code`. Fields: `actor`, `record`, `identity`,
`scheme`, `protocol`, `host` (host and port only), `operation`, `call` (the kernel's correlation id of the tool call in
flight), `phase`, `direction`, `outcome`, `code`, `latency_ms` and a grant summary. No subject, session, nonce, header,
claim or message text, not even as a digest. With no identity configured nothing publishes.

## Binding, the gate and the commands

**Binding.** Identity is infra. An `Identity` row holds records (a scheme and its params), an `identity` layer on a stack or
a chain names one, and the Flavour that binds the stack gives the layer the record and the agent id
([Identity infra row](../manifests/infra.md#identity)). The Agent and MAS manifests never name an identity. A record is read
by the layer when a session starts; a `{secret: NAME}` param is checked for presence without being read, and a missing one
stops the start with one `identity.startup` event (`refused:missing_secret`, the record and the scheme, no value).

**The gate.** `identity-gate` is a governance plugin that refuses a tool call before it starts, from the public view of the
wallet the agent holds. The layer registers its wallet for the agent and the record, and the gate asks `describe`:

```yaml
spec:
  governance:
    - identity-gate:
        max_age_s: 30
        requirements:
          get_fares:
            identity: desk-badge          # the record; absent: the records the agent holds
            require:
              - {fact: group, equals: infraadmin}
```

A wallet covers a requirement when any one of its identities does; `task` or `audience` names one identity. The grammar is
`equals`, `in`, `contains`, `min`, `max`, `exists` and `fresh_within_s` over the view's `facts`, plus `principal.chain`,
`principal.chain_depth` and `principal.actor` from the acting principal (an unknown chain is absent, never "direct").
Outcomes: allow; block with `no_identity`, `no_credential` (the wallet has nothing to show for the requirement), `credential_revoked`, `scope_uncovered:<name>`, `gate_stale` or `gate_error`; hold
with `interaction_required` when a human step is pending and the kernel has a human in the loop. Fail closed. The gate is
advisory: the layer enforces, and a call whose identity cannot be presented is never sent.

The gate never does I/O on the turn's loop: every egress starts a refresh behind the decision, so a missing view or one older
than `max_age_s` is `gate_stale` (a model call before the tool call warms it). `first_view_wait_s` (default off) makes a decision
with no view yet wait for the refresh, at most that long, on the thread that runs the turn. Every decision is an
`identity.gate` event (`events: all`, `tools` or `off`) with the actor, the record, the operation, `ok`, `denied` or `hold`,
and a code; never a fact. `parse_requirements` and `GateConfigError`
(`mas.library.standard.lib.identity.requirements`) are the public way for a scheme library to read and check requirements
in its own tooling.

**Commands.** `mas-ctl identity list | check | describe` ([mas-ctl](../cli/mas-ctl.md#mas-ctl-identity)); `check --require-gate`
makes an agent that holds a record but has no gate an error.

## The agent's own identities: the surface and the guard

An agent can manage its own identities, and be given the same ability as tools. `Surface`
(`mas.library.standard.lib.identity.surface`) is a plain Python facade over the wallets the identity layer registered for the
agent: `identities()`, `describe(identity)`, `delegate(identity, scopes, ttl_s=...)` and `revoke(identity)`. A plugin or a
host calls it directly; it keeps no state of its own. Everything it returns is value-free: ids, kinds, opaque subject
references, status, grant summaries and the `facts` of a public view (the view's attestation is not passed on). Evidence,
headers and secrets stay inside the caller.

The `identity` system plugin gives the model four tools over a `Surface`: `identity_list`, `identity_describe`,
`identity_delegate` and `identity_revoke`. They work on identity ids. No tool returns a credential, so a prompt that tries to
get one out finds none to call, and an argument the schema does not name is refused before anything runs. A delegation names
the scopes and an optional lifetime; the surface builds the rest of the grant from the parent's (its audience and expiry, one
more hop of depth), the wallet narrows it and the caller checks that it did. A refusal is `identity: <code>`, a code and never
a value. The plugin is off unless `spec.system_plugins` names it; `tools:` limits it to some of the four.

```yaml
spec:
  system_plugins:
    - identity: {tools: [identity_list, identity_describe]}   # read-only
```

Delegation and revocation change what the agent can do, so each is an ordinary tool call that governance sees, and
`identity-guard` shuts them by default. It is the opposite of the gate: the gate passes a call that has no requirement, the
guard blocks the wallet's own tools unless a policy opens them. The tools do not depend on the guard being there: each of
`identity_delegate` and `identity_revoke` runs only for a call the `identity-guard` of the same agent allowed (a permit
keyed on the kernel's correlation id of that call, used once), or whose hold a human approved: a held call gets its permit
when the kernel asks the guard again after the human's yes (one `hold` event, then one `ok`). Otherwise it refuses with
`identity: refused:no_guard`, so an agent with no guard configured cannot use them at all. With `hitl_once_per_turn` a human's
yes is remembered for the turn and a later hold would pass without asking: the guard then blocks a hold
(`guard_hold_unavailable`) instead of letting it through. `mas-ctl identity check` reports an agent that uses the plugin with no guard covering them.

```yaml
spec:
  governance:
    - identity-guard:
        allow: {identity_delegate: true, identity_revoke: hold}   # absent: blocked
```

`allow` maps a tool to `true` or `hold` (ask a human through the kernel's human-in-the-loop; with none configured the call is
blocked, never allowed). `protect` changes the tools the guard covers (`identity_delegate` and `identity_revoke` by default).
A decision about a covered tool is one `identity.gate` event with the tool as its operation and the code `guard_allowed`,
`guard_hold`, `guard_denied` or `guard_hold_unavailable`.

Which identity the edge presents for a given peer stays an infra choice (the layer's `use`), not a tool: a tool that retargeted
it at run time would need new plumbing in the layer.

## Not covered

- Delegation across hosts (a verified caller's credential kept for a session, a cascade through parents): the wallet's
  `delegate` and the check on its result are all that is here.
- Multi-round exchanges over plain HTTP: a handshake needs a `Channel`, which MCP and A2A sessions give.
- Revocation the issuer pushes without the wallet being asked anything: a wallet that learns of it reports `revoked` from its
  next call, or the host calls `Caller.notify_revoked`.
- A policy language. `describe` returns opaque facts; a policy reads them with a vocabulary of its own.

## Examples

Schemes this contract was designed against (each fits the four operations; the ones that need a parent supplied by the
harness use `Delegation.parent_evidence`): a signed badge, a SPIFFE JWT-SVID, HTTP message signatures (RFC 9421), OAuth
client credentials, token exchange (RFC 8693), an identity assertion grant, DPoP (RFC 9449), SD-JWT verifiable
credentials (holder selection inside the wallet) and a multi-round presentation exchange (a handshake over a channel).
