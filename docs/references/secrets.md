#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

# Secrets

A manifest names a secret with a **SecretRef** (`{secret: NAME}`). The value lives in a **secret store**, and code reads it only at the moment it sends it.

## What it guarantees and what it does not

Guaranteed:

- Manifests (infra, services, environments, Flavours, overlays) hold `{secret: NAME}` refs, never values. A literal in a secret slot is a load error that names the field, never the value.
- A value is revealed at the point of use, kept in a local variable, and not stored on a row, a plugin object or a spec.
- A value does not appear in specs, dumps, logs, events, traces, error text or child-process environments. A child gets a secret only when its own `env` names it.

Limits:

- A tool server or peer you send a credential to is trusted with it. Its replies are its own output: if a server repeats the credential in a JSON-RPC error, that text reaches the model. MAS Lab does not search replies for credentials.
- The file store refuses a symlink. Only the owner can write the store directory, so only the owner can swap an entry.
- The controller HTTP API is a local-trust boundary: the jobs a caller starts run as the controller's user and can read the store. See [serve](../cli/serve.md#trust-boundary).
- The controller reads its token file once: rotating it needs a restart. A2A admission tokens are read on every request.

## Quick start

```bash
mas-ctl secrets init                     # <workspace>/.mas/secrets, mode 0700
mas-ctl secrets set openai               # hidden prompt on a terminal
printf %s "$KEY" | mas-ctl secrets set openai   # or stdin, never an argument
```

`infra/openai.yaml`:

```yaml
apiVersion: infra/v1
kind: LLMProxy
metadata: {name: openai-direct}
spec:
  proxy:
    api_base: https://api.openai.com/v1
    api_key: {secret: openai}
```

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml --infra-ref infra/openai.yaml -q "Hello"
```

A required secret that is missing refuses the start and names `name/key` and the `mas-ctl secrets set` command.

## Referencing a secret

| Form | Meaning |
| --- | --- |
| `{secret: NAME}` | `<store>/NAME` (key `value`) |
| `{secret: {name: NAME, key: K, optional: true, prefix: "Bearer "}}` | `key` selects one entry of a keyed secret; `optional: true` reads as absent instead of failing (no auth header is sent); `prefix` is prepended when revealed |

Names and keys match `[a-z0-9][a-z0-9._-]{0,127}`. `prefix` is one auth scheme word and a space (`Bearer `, `Basic `, `DPoP `) or empty.

A ref is accepted in a secret field (`api_key`, `password`, `token`, `secret`, `embed_api_key`, `client_secret`, `private_key`, matched without case, `_` or `-`), in any header map (`headers` or a key ending in `headers`), in an MCP stdio server's `env`, in A2A `a2a.admission.callers` (name to ref), in the otel plugin's OTLP `headers`, and in the `env` of a lab service, fixture or shell step.

Refused at load:

- a literal (or a `file:` string) in a secret field or in `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `Api-Key`, `X-Api-Key`;
- `env:` strings and `*_env` keys (`api_key_env`, `password_env`, ...);
- credentials in a URL: `user:password@`, a scheme-less `user:password@host` string, and the query or fragment parameters `api_key`, `apikey`, `key`, `token`, `access_token`, `password`, `secret`, `sig`, `signature`;
- the `!!set`, `!!omap` and `!!pairs` tags; an unknown tag, as with any safe loader;
- a document over 100 levels deep or 100,000 nodes with every alias expanded (an alias bomb, a cycle). Anchors, aliases and `<<` merge keys load.

## Stores

### File store (default)

One file per secret: `<dir>/<name>`, or `<dir>/<name>/<key>` for a keyed secret. Files are `0400` (any mode without group or other bits passes), directories `0700`, owned by the user running MAS Lab. A value is at most 1 MiB, UTF-8, with no control byte; one trailing line end is stripped. A store directory that does not exist holds no secret.

The directory is, in order: `--secrets-dir DIR` (on `mas-ctl` or `mas-lab`), the workspace `secrets.dir` (relative to the workspace root), else `<workspace>/.mas/secrets`. Workspace `config.yaml`:

```yaml
secrets:
  dir: .mas/secrets
  store: file                    # or env; --secret-store overrides
  allow_loose_permissions: false
```

`allow_loose_permissions: true` turns off the owner and mode checks only; the symlink, regular-file and size checks stay. On Windows the store reads only with it set. Production hardening fails it (`secret-store-loose-permissions`).

A refusal names the ref and a reason (`is a symlink`, `is group or world accessible`, `too large`, ...), never the path or the value.

### Env store (development only)

`--secret-store env` or `secrets.store: env`. `{secret: openai}` reads `MAS_SECRET_OPENAI`; `{secret: {name: mcp-upstream, key: bearer}}` reads `MAS_SECRET_MCP_UPSTREAM__BEARER` (upper case, `-` becomes `_`, `__` before the key). Names with `.`, `_` or `--` are refused. It cannot be written or listed, does not reach lab spawn workers, and production hardening fails it (`secret-store-env`).

### Docker

The store is `<workspace>/.mas/secrets` on the workspace mount (`/workspace/.mas/secrets` in the containers), so it stays on the host. Containers get only the variables `compose.yaml` lists; a key in your shell or `docker/.env` reaches none.

```bash
docker compose run --rm -T cli mas-ctl secrets init
printf %s "$OPENAI_API_KEY" | docker compose run --rm -T cli mas-ctl secrets set openai
```

**CI.** Write the store from the CI secret manager before the run: `printf %s "$KEY" | mas-ctl secrets set openai`, or create the directory (`0700`) and one file per secret (`0400`) yourself.

**Lab processes.** A `mas-lab` process uses one store, chosen once from its flags and the workspace. Spawn workers rebuild the same file store. The controller daemon serves the store it started with and refuses a job that names another: restart it (`mas-lab control stop`).

## Commands

Commands (`init`, `set`, `list`, `import-env`) are in the [mas-ctl reference](../cli/mas-ctl.md#secrets). `mas-ctl infra show` prints each ref as `secret:name/key present=… size=…`.

## Providers and keys

- An LLM layer has a default secret named for its provider (`openai`, `anthropic`, `gemini`, `groq`, ...). It is used only when `api_base` is empty or has the origin of the provider's default endpoint. Judges, embeddings and dataset steps that speak the OpenAI wire follow the same rule.
- Any other endpoint (a gateway, a proxy, a local server) needs an explicit `api_key`. A keyless server takes an optional ref:

  ```yaml
  proxy:
    api_base: http://localhost:11434/v1
    api_key: {secret: {name: ollama, optional: true}}
  ```

- `OPENAI_BASE_URL` (or `OPENAI_API_BASE`) supplies `api_base` only when the infra leaves it empty; that base is not a default endpoint, so the leaf needs `api_key`.
- ClickHouse and Neo4j passwords are declared refs (`password: {secret: clickhouse}`); none is assumed.
- An override (Flavour `bind` params, a later infra row with the same id, a CLI `--override`) that changes the `url`, `api_base`, `endpoint`, `uri`, `host`, `port`, `identity`, `command` or `args` of a row holding refs is refused unless it restates the credentials.

## Network rules

- A secret header goes only to the origin (scheme, host, port) of the row's `url`. An A2A row may list `allowed_origins` for other origins its agent card names. A request elsewhere has the header removed and fails, naming both origins.
- A secret goes only over https (or wss) or to a loopback host (`localhost`, `127.0.0.0/8`, `::1`). There is no setting to allow plain http.
- No redirect is followed with a secret, except an http-to-https upgrade or a move within the same origin.
- A request to a loopback host does not go through the environment's proxy.
- A failure that carries an HTTP response says `<Type>: HTTP <status>`; the response body, the provider's message and request id are never shown. A failed A2A call names a fixed reason and the JSON-RPC code. A tool server's own JSON-RPC error stays the tool's result.

## Host environment

- Every entry point (`mas-ctl`, `mas-lab`, the controller daemon and its API, `mas-mcp`, `mas-slim`) calls `mas.runtime.entrypoint.install_host_environment()` first. It copies the declared keys into `mas.runtime.hostenv` and reduces `os.environ` to the child allowlist, so an API key exported in the shell is gone from the process.
- `hostenv` keeps the `MAS_*` namespace (not `MAS_SECRET_*`) and the fixed list `mas.runtime.envkeys.DECLARED_KEYS` (path, locale, temp dirs, proxies, CA bundles, lab service addresses, OpenTelemetry settings). `MAS_SECRET_*` and the installer index URLs (`PIP_INDEX_URL`, `UV_INDEX`, ...) sit in a private compartment that only ctl reads (`hostenv.private`).
- `child_env(extra, lab=False)` builds every child's environment: run keys, CA bundles and proxies, plus (with `lab`) the lab keys, plus what the caller declares. It raises in a process with no installed host.
- A proxy, OTLP endpoint or `NEO4J_URI` variable that holds `user:password@` or a credential parameter, or does not parse, stops the entry point.
- `.env` sets only the `MAS_LLM_*` scheduler and model keys, is not exported, and loses to the shell.
- A service, fixture or shell step declares its child's `env`: plain strings, or `{secret: NAME}` revealed into that child only. A plain value is refused under a provider key name (`OPENAI_API_KEY`, ...), a `MAS_SECRET_*` name or an installer index variable. A shell step may copy service-exported variables with `passthrough: [NAME]`.
- An embedding program must call `install_host_environment()` first, or keep credentials out of its environment: third-party SDKs read some (`OPENAI_ORG_ID`, `OTEL_EXPORTER_OTLP_HEADERS`) from `os.environ`.

## Migrating from environment variables

`env:` strings, `*_env` keys and keys in `.env` are no longer read.

Before:

```yaml
proxy:
  api_base: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
headers:
  Authorization: env:UPSTREAM_TOKEN
```

After:

```yaml
proxy:
  api_base: https://api.openai.com/v1
  api_key: {secret: openai}
headers:
  Authorization: {secret: {name: upstream, prefix: "Bearer "}}
```

Move the values once with `mas-ctl secrets import-env .env`. It knows the provider variables (`OPENAI_API_KEY` as `openai`, `ANTHROPIC_API_KEY` as `anthropic`, `HF_TOKEN` as `hf`, `NEO4J_PASSWORD` as `neo4j`, ...) and the env store's `MAS_SECRET_*` names; set any other with `mas-ctl secrets set NAME`, then delete the keys from `.env`.

## Rules for plugin authors

- Take a SecretRef in your params, never a string. Reveal it with `current_resolver().reveal(ref)` (or `reveal_or_none` for an optional ref) right before the request, and keep the result in a local variable, never on `self` or a row.
- Send it with the transport helpers of `mas.runtime.secrets.transport`: `check_plain_http(url, what=...)`, `reveal_headers_for`, and `httpx_client_kwargs_for`, `requests_session_for` or `opener_for` (no redirects, loopback off the proxy).
- Do not read `os.environ`, `getenv` or a dotenv file: read `hostenv.get(name)`, and start children with `env=child_env(...)`.
- In an error, name the ref (`str(ref)` is `secret:name/key`) or the origin, never a value or a response body.
- `tests/test_no_host_reads_outside_ctl.py` enforces this over every `src` tree: no environment reads outside ctl and the entry points, no spawn without `child_env`, no bare YAML load of a document that may hold a secret, no hand-built `Authorization` or `x-api-key` header. A justified exception is one line in its table (`ENTRY_POINTS`, `SPAWNS`, `YAML_READERS`, `HTTP_SENDERS`) with the reason.
