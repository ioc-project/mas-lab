#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The named hardening checks.

Each check is a pure function over :class:`HardeningInput`: it returns the problems it found, one
plain sentence each, and an empty list when the session is fine. A problem names the thing that is
wrong and never repeats a secret, a token or a value read from a peer.

What MAS Lab already refuses (an HTTP url for a transport on a non-loopback host, a chain secret under
32 bytes, ``require_usage``, a header with a control character) is not repeated here.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from mas.ctl.hardening.model import (
    CRITICAL,
    HIGH,
    MEDIUM,
    RUNTIME,
    STATIC,
    Check,
    HardeningInput,
)
from mas.library.standard.lib.comm.payload import MAX_MESSAGE_SIZE
from mas.runtime.secrets import check_plain_http
from mas.runtime.secrets.ref import is_ref
from mas.runtime.secrets.transport import PlainHttpRefused

_INGRESS_PLUGINS = {"retry_on_error", "backtrack_on_error"}


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every ``(path, value)`` of a nested mapping and list, the containers included.

    A SecretRef object is one node: do not walk into ``secret`` / ``name`` / ``key``.
    """
    yield path, value
    if is_ref(value):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, (*path, str(index)))


def _where(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _governance_names(spec: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    raw = spec.get("governance")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                names.add(item.strip())
            elif isinstance(item, dict):
                names.update(str(key).strip() for key in item)
    return {name.removeprefix("gov_") for name in names}


def listener_without_identity(inp: HardeningInput) -> list[str]:
    problems = []
    for item in inp.exposures:
        if item.loopback or item.identity == "verified":
            continue
        if item.identity == "empty":
            problems.append(f"{item.protocol} listener {item.name!r} has an admission policy that names no caller")
        else:
            problems.append(f"{item.protocol} listener {item.name!r} on {item.host} verifies no caller")
    return problems


def ingress_errors_unclassified(inp: HardeningInput) -> list[str]:
    return [
        f"agent {agent!r} has no ingress governance plugin, so an engine ERROR reaches the run unclassified"
        for agent, spec in sorted(inp.agents.items())
        if not (_governance_names(spec) & _INGRESS_PLUGINS)
    ]


def hitl_auto_approve(inp: HardeningInput) -> list[str]:
    problems = []
    for agent, spec in sorted(inp.agents.items()):
        for path, value in _walk(spec.get("governance")):
            if path and path[-1] == "hitl_mode" and str(value).strip().lower().replace("_", "-") == "auto-approve":
                problems.append(f"agent {agent!r} approves every human-in-the-loop request itself (hitl_mode)")
                break
    return problems


def llm_cache_replay(inp: HardeningInput) -> list[str]:
    problems = []
    for entry in inp.llm:
        for step in entry.get("steps") or []:
            params = step.get("params") or {}
            if step.get("layer") == "llm_cache" and params.get("allow_read") is not False:
                problems.append(
                    f"LLM destination {entry.get('name')!r} replays cached model answers, tool calls included"
                )
                break
    cache = inp.engine_cache
    if cache.get("enabled") is not False and cache.get("read"):
        problems.append("the runtime engine turns the LLM response cache on for reads")
    return problems


def plain_http_remote(inp: HardeningInput) -> list[str]:
    problems = []
    for where, url in inp.urls:
        parts = urlsplit(str(url))
        if parts.scheme.lower() not in {"http", "ws"}:
            continue
        try:
            check_plain_http(url, what="traffic")
        except PlainHttpRefused:
            problems.append(f"{where} reaches {parts.hostname or 'a host'} over {parts.scheme}, not TLS")
    return problems


def tls_verification_disabled(inp: HardeningInput) -> list[str]:
    problems = []
    for where, mapping in inp.settings:
        for path, value in _walk(mapping):
            if not path:
                continue
            key = path[-1]
            if (key in {"verify_ssl", "verify", "tls_verify"} and value is False) or (
                key in {"dev", "insecure"} and value is True
            ):
                problems.append(f"{where} turns TLS certificate checks off ({_where(path)})")
    return problems


def mcp_tools_wildcard(inp: HardeningInput) -> list[str]:
    return [
        f"MCP tool server {name!r} offers every tool it advertises; list the tool names"
        for name, row in sorted(inp.tool_servers.items())
        if row.get("protocol") == "mcp" and row.get("tools") in (None, "*", ["*"])
    ]


def secret_store_env(inp: HardeningInput) -> list[str]:
    if str(inp.secret_store or "").strip() == "env":
        return ["the secret store is env; use the file store in production"]
    return []


def secret_store_loose_permissions(inp: HardeningInput) -> list[str]:
    """On Windows the file store reads only with ``allow_loose_permissions``, so this fires there too."""
    if str(inp.secret_store or "file").strip() == "file" and inp.allow_loose_permissions:
        if sys.platform == "win32":
            return ["the file secret store runs without permission checks (Windows)"]
        return ["the file secret store allows loose permissions"]
    return []


def sizes_unbounded(inp: HardeningInput) -> list[str]:
    problems = []
    for where, mapping in inp.settings:
        for path, value in _walk(mapping):
            if path and path[-1] == "max_message_size":
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > MAX_MESSAGE_SIZE:
                    problems.append(f"{where} sets max_message_size outside 1..{MAX_MESSAGE_SIZE}")
    return problems


def tool_retry_not_idempotent(inp: HardeningInput) -> list[str]:
    problems = []
    for entry in inp.tool:
        for step in entry.get("steps") or []:
            if step.get("layer") == "retry" and (step.get("params") or {}).get("require_idempotent") is False:
                problems.append(f"tool destination {entry.get('name')!r} retries calls that may have side effects")
    return problems


def egress_host_allowlist(inp: HardeningInput) -> list[str]:
    """Checked on each tool call by ``gov_hardening_profile``; nothing to read before the run."""
    return []


CHECKS: tuple[Check, ...] = (
    Check(
        "listener-without-identity",
        CRITICAL,
        STATIC,
        "A listener that verifies no caller lets anyone who can reach the port run the agent and its tools.",
        "Give the A2A row `a2a.admission.callers` (name to `{secret: NAME}`), or serve on loopback only.",
        listener_without_identity,
    ),
    Check(
        "tls-verification-disabled",
        CRITICAL,
        STATIC,
        "Without certificate checks a network peer can pose as the model, tool or agent endpoint.",
        "Remove `verify_ssl: false`, `dev: true` and `insecure: true`; use `ca_bundle` for a private CA.",
        tls_verification_disabled,
    ),
    Check(
        "secret-store-env",
        HIGH,
        STATIC,
        "The env store reads process variables; a production session should use the file store.",
        "Set `secrets.store: file` (the default) and `mas-ctl secrets set NAME`.",
        secret_store_env,
    ),
    Check(
        "secret-store-loose-permissions",
        HIGH,
        STATIC,
        "A file store that allows group- or world-readable files leaks secrets on disk.",
        "Remove `allow_loose_permissions` so files stay mode 0400 and the directory 0700.",
        secret_store_loose_permissions,
    ),
    Check(
        "plain-http-remote",
        HIGH,
        STATIC,
        "Plain http sends prompts, tool arguments and credentials where any hop can read and change them.",
        "Use an https url, or keep plain http on loopback.",
        plain_http_remote,
    ),
    Check(
        "hitl-auto-approve",
        HIGH,
        STATIC,
        "An agent that approves its own human-in-the-loop requests has no human in the loop.",
        "Use `hitl_mode: interactive`, or `auto-deny` for an unattended run.",
        hitl_auto_approve,
    ),
    Check(
        "llm-cache-replay",
        HIGH,
        STATIC,
        "A cached model answer replays its tool calls: the same side effects without a new decision.",
        "Remove the `llm_cache` step and `cache.read` from the deployment.",
        llm_cache_replay,
    ),
    Check(
        "tool-retry-not-idempotent",
        HIGH,
        STATIC,
        "Retrying a tool that already acted can act twice (a second payment, a second message).",
        "Leave `require_idempotent` at its default (true) on tool retry.",
        tool_retry_not_idempotent,
    ),
    Check(
        "mcp-tools-wildcard",
        MEDIUM,
        STATIC,
        "A server that adds a tool later gives the model that tool with no review.",
        "List the tool names in the row's `tools`.",
        mcp_tools_wildcard,
    ),
    Check(
        "sizes-unbounded",
        MEDIUM,
        STATIC,
        "A message cap that is missing or above the shared limit lets one peer exhaust memory.",
        f"Set `max_message_size` between 1 and {MAX_MESSAGE_SIZE}, or leave it unset.",
        sizes_unbounded,
    ),
    Check(
        "ingress-errors-unclassified",
        MEDIUM,
        STATIC,
        "Without an ingress plugin, an engine ERROR is not classified, so nothing retries, blocks or stops it.",
        "Attach `retry_on_error` (the `with-hardened` Control does).",
        ingress_errors_unclassified,
    ),
    Check(
        "egress-host-allowlist",
        HIGH,
        RUNTIME,
        "A tool call can carry a url the model or a peer chose; an allow-list stops it reaching other hosts.",
        "Declare `gov_hardening_profile` in a Control with `allowed_hosts`.",
        egress_host_allowlist,
    ),
)

BY_ID: dict[str, Check] = {check.id: check for check in CHECKS}
