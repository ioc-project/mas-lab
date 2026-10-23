# Release Notes

## Unreleased

The public GitHub org and docs host are now `ioc-project/mas-lab`
(`https://github.com/ioc-project/mas-lab`,
`https://ioc-project.github.io/mas-lab/`). Docs name OXP as SCOPE.

The web UI talks only to the controller (`/api/scopes`, items, runs, artifacts).
`mas-lab serve` gains `--cors-origin`, `--token-file`, `--state-dir`, and `--workspace`.
`/api/libraries`, `/api/jobs`, and `/api/experiments` are deprecated.

## v0.2.0

This branch is the MAS-Lab v0.2 release-prep branch after rebasing onto the
latest upstream `main`. The content below reflects the current state of the
rebased branch and the release metadata/docs refreshed to match that code.

### Overview

MAS-Lab v0.2 is the protocol-integration release: it makes transport concerns
first-class infrastructure concerns instead of core agent logic. The release keeps
agent reasoning stable while enabling remote tool access and remote peer
communication through the IOA layer.

The most important features in this release are the first-class MCP and A2A
integration paths, the agentskills.io-compatible skill layer, and the continued
hardening of runtime validation and observability.

### What is new in v0.2

#### IOA and transport integration

- `library-ioa` is the dedicated integration layer for protocol and transport
  adapters.
- MCP support is provided through `mas-mcp` and the library-ioa MCP bridge,
  allowing tool serving and invocation without rewriting the agent contract.
- A2A support is available via the runtime and CLI (`mas-ctl serve` and the
  `a2a` commands), so agent exposure and remote delegation work as explicit
  infrastructure configuration rather than hardcoded agent implementation.
- `library-skills` adds the agentskills.io-compatible execution layer for
  discoverable, swappable skills and sandboxed script execution.

#### User-facing runtime and HITL work

- Human-in-the-loop patterns and user communication contracts are integrated into
  the runtime boundary to support approval-gated flows and structured user
  interaction.
- The runtime keeps tool calls, delegation boundaries, and user-visible signals
  cleanly separated from business logic so the same agent can be exercised with
  different UX and integration surfaces.

#### Validation, governance, and runtime safety

- `mas-ctl compile` makes the fully resolved agent/MAS spec available for
  auditing and debugging.
- Semantic validation and overlay safety checks improve manifest correctness and
  reduce ambiguous runtime behavior.
- Registry-driven plugin and governance behavior continue to move from implicit
  environment assumptions toward explicit declaration and validation.
- Provider compatibility and tool error handling have been hardened to make long
  and multi-agent runs more robust.

#### Observability and traceability

- Trace integrity remains central to the framework, especially around delegation,
  tool routing, and provider boundaries.
- The lab and runtime continue to improve the fidelity of execution traces so
  reproduction and debugging remain actionable.

#### Protocol compliance and conformance

- MCP conformance was measured with the official
  [MCP conformance suite](https://github.com/modelcontextprotocol/conformance):
  `2025-11-25` passed `92.9%` of required checks, and the newer, stricter
  stateless `2026-07-28` revision passed `98.3%`, both progressing toward full
  required-check coverage.
- A2A was tested with the official
  [A2A TCK](https://github.com/a2aproject/a2a-tck) using a deterministic fixture
  independent of LLM output, across JSON-RPC, HTTP+JSON, and gRPC. Separate
  profiles cover positive and negative capability states, including Agent Card
  discovery, task lifecycle, streaming, artifacts, push notifications, and
  extensions.
- The A2A executed-test result was `100%`: `99` requirements passed and `0`
  failed. Official specification coverage was `79.8%`, with `25` requirements
  not exercised because the current TCK provides no mapped tests for them. The
  partial conformance figure therefore reflects test-suite coverage limitations,
  not failures in those untestable implementation areas.

#### Docker image refresh for the release

The release includes a Docker image refresh for the shipped UI and backend
images. The tagged release workflow rebuilds and pushes both images to GHCR so
that the release artifacts remain aligned with the same code version as the Python
packages.

Container images published by the release pipeline:

- `ghcr.io/ioc-project/mas-lab/ui`
- `ghcr.io/ioc-project/mas-lab/backend`

### Release scope and branch state

This release-prep branch reflects the current rebased state of the repository and
is aligned with the latest upstream `main` as of the release branch update.

The remaining release work is operational rather than speculative: confirm the
final version metadata, validate the release checks on this branch, and then cut
and publish the tagged release artifacts when the branch is ready.

### Breaking changes / migration notes

- Prefer explicit model-slot configuration over the legacy `spec.llm` field.
- Prefer registry-driven plugin declaration over implicit environment access.
- Review any custom governance logic for exceptions that should now be surfaced
  explicitly rather than silently accepted.

### Release checklist before tagging

1. Confirm the final v0.2 version metadata and release docs on the rebased
   release branch.
2. Validate the Docker image build and release artifacts against the current
   branch state.
3. Re-run the release validation gates (`Taskfile.yml` / CI) against the final
   branch.
4. Create the final tag and publish the built packages and container images.

### Related resources

- [CHANGELOG.md](CHANGELOG.md)
- [README.md](README.md)
- [.github/workflows/publish.yml](.github/workflows/publish.yml)
- [.github/workflows/docker.yml](.github/workflows/docker.yml)
- [scripts/version_manager.py](scripts/version_manager.py)
