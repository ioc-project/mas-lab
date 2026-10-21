<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# ADR 0003 — UI talks only to the controller (scopes, ids, no paths)

**Status:** Accepted
**Date:** 2026-10-19

## Context

The studio used to list folders as "libraries", open applications by
`metadata.name`, and compose refs such as tool files from folder
conventions. That breaks versioned applications, treats a lab as a library,
and cannot run the UI on a different machine from the controller.

## Decision

- The UI renders only what `GET /api/scopes`, `/api/kinds`,
  `/api/scopes/{scope_id}/items`, `/api/choices`, `/api/runs`, and
  `/api/artifacts` return.
- Identity is a **scope id** (`library:<name>` / `lab:<name>`) plus an **item
  id** (`name` or `name@version`). The UI never derives an id from
  `metadata.name` and never displays a server path.
- A **lab is not a library**. Lab-local libraries are separate library scopes
  with `origin: lab`.
- Writes go through `KindLayout` (`manifest_layout.py`). Locate from the
  catalog; create uses the kind's default layout.
- Runs persist under `--state-dir`. Artifacts are read by opaque id.
- Auth is optional bearer (`--token-file`); CORS is explicit
  (`--cors-origin`) off loopback.

## Consequences

Deprecated `/api/libraries/**`, `/api/jobs/**`, and `/api/experiments/**`
remain for one release as adapters. The UI grep gate forbids folder
conventions and hardcoded model ids in `ui/src`.
