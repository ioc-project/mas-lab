#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible tool definitions from agent manifest."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from mas.runtime.boundary.delegation import openai_delegation_tools

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mas.runtime.engine.manifest_tool_provider import ManifestToolProvider
else:
    ManifestToolProvider = Any


def _looks_like_scheme_ref(ref: str) -> bool:
    if ref.startswith(("/", "\\")):
        return False
    scheme, sep, _ = ref.partition(":")
    return bool(sep and scheme and "/" not in scheme and "\\" not in scheme)


def _tool_ref_path(ref: str, base_dir: Path) -> Path | None:
    from mas.runtime.package_refs import resolve_path_ref

    root = base_dir.resolve()
    try:
        if ref.startswith("pkg://") or _looks_like_scheme_ref(ref):
            path = resolve_path_ref(ref, root).resolve()
        else:
            path = (root / ref).resolve()
            path.relative_to(root)
    except LookupError:
        raise
    except (ValueError, ModuleNotFoundError) as exc:
        logger.debug("tool ref %r not resolved: %s", ref, exc)
        return None
    except OSError as exc:
        logger.warning("tool ref %r I/O error: %s", ref, exc)
        return None
    except Exception as exc:
        logger.warning("tool ref %r unexpected error: %s", ref, exc)
        return None
    return path if path.is_file() else None


def tool_name_from_ref(ref: str, *, base_dir: Path | None) -> str | None:
    if not ref or not base_dir or (path := _tool_ref_path(ref, base_dir)) is None:
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    if not isinstance(doc, dict):
        return None
    name = (doc.get("metadata") or {}).get("name")
    return str(name) if name else None


def resolve_manifest_tool_refs(
    manifest: dict[str, Any],
    base_dir: Path | None,
    *,
    inplace: bool = False,
) -> dict[str, Any]:
    """Expand ``spec.tools[].ref`` entries to logical tool names."""
    if not base_dir:
        return manifest
    tools = (manifest.get("spec") or {}).get("tools")
    if not isinstance(tools, list):
        return manifest
    resolved: list[Any] = []
    changed = False
    for item in tools:
        if isinstance(item, dict) and item.get("ref") and not (item.get("name") or item.get("id")):
            if name := tool_entry_name(item, base_dir=base_dir):
                resolved.append({**item, "name": name})
                changed = True
                continue
        if inplace:
            resolved.append(item)
        else:
            resolved.append(copy.deepcopy(item) if isinstance(item, dict) else item)
    if not changed:
        return manifest
    if inplace:
        manifest.setdefault("spec", {})["tools"] = resolved
        return manifest
    out = {k: v for k, v in manifest.items() if k != "spec"}
    spec_out = dict(manifest.get("spec") or {})
    spec_out["tools"] = resolved
    out["spec"] = spec_out
    return out


def _manifest_agent_id(manifest: dict | None, agent_id: str | None) -> str | None:
    if agent_id:
        return agent_id
    name = ((manifest or {}).get("metadata") or {}).get("name")
    return str(name) if name else None


def tool_entry_name(item: Any, *, base_dir: Path | None = None) -> str | None:
    """Resolved logical name for a ``spec.tools`` entry (string, ref, or inline dict)."""
    if isinstance(item, str):
        return item or None
    if not isinstance(item, dict):
        return None
    if n := item.get("name") or item.get("id"):
        return str(n)
    ref = item.get("ref")
    return tool_name_from_ref(ref, base_dir=base_dir) if isinstance(ref, str) and ref and base_dir else None


def tools_with_resolved_names(tools: list[Any], base_dir: Path) -> list[Any]:
    """Return a copy of *tools* with ``ref`` entries annotated by logical ``name``."""
    if not tools:
        return []
    scratch = {"spec": {"tools": copy.deepcopy(tools)}}
    resolve_manifest_tool_refs(scratch, base_dir, inplace=True)
    return list(scratch["spec"]["tools"])


def tool_names_from_manifest(manifest: dict | None, *, base_dir: Path | None = None) -> list[str]:
    return [
        n
        for t in ((manifest or {}).get("spec") or {}).get("tools") or []
        if (n := tool_entry_name(t, base_dir=base_dir))
    ]


def openai_tools(
    manifest: dict | None,
    *,
    base_dir: Path | None = None,
    agent_id: str | None = None,
    tool_provider: ManifestToolProvider | None = None,
    peer_descriptions: dict[str, str] | None = None,
    ctx: Any = None,
) -> list[dict[str, Any]]:
    """Build OpenAI ``tools`` from loaded manifest tools and MAS delegation topology.

    ``ctx`` is forwarded to ``tool_provider.list_openai_tools()`` so tools
    whose valid-argument set is only known at runtime can build an accurate
    schema (see ManifestToolProvider.list_tools's docstring).
    """
    aid = _manifest_agent_id(manifest, agent_id)
    out: list[dict[str, Any]] = list(
        openai_delegation_tools(manifest, agent_id=aid, peer_descriptions=peer_descriptions)
    )
    seen = {t["function"]["name"] for t in out if t.get("function")}
    if tool_provider is not None:
        for tool in tool_provider.list_openai_tools(ctx=ctx):
            name = tool.get("function", {}).get("name")
            if name and name not in seen:
                out.append(tool)
                seen.add(name)
    return out


SPAWN_SUBAGENT_TOOL = "spawn_subagent"
CREATE_SUBAGENT_TOOL = "create_subagent"
SPAWN_SUBAGENT_NAMES = frozenset({SPAWN_SUBAGENT_TOOL, CREATE_SUBAGENT_TOOL})
CONTROL_TOOLS = frozenset(
    {
        "pause_session",
        "list_checkpoints",
        "inspect_session",
        "navigate_checkpoint",
        "cancel_inflight",
        "run_control_script",
    }
)


def current_spec_for_advertise(ctx: Any | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Advertise/execute from the live spec revision when a session has one."""
    if ctx is not None:
        spec_log = getattr(ctx, "spec_log", None)
        session_id = getattr(ctx, "session_id", None)
        session = getattr(ctx, "managed_session", None)
        if spec_log is None and session is not None:
            spec_log = getattr(session, "spec_log", None)
            session_id = session_id or getattr(session, "session_id", None)
        if spec_log is not None and session_id:
            current = spec_log.current_spec(str(session_id))
            if current:
                return current
        cached = getattr(ctx, "current_spec", None)
        if isinstance(cached, dict) and cached:
            return dict(cached)
    return dict(fallback or {})


def disabled_tool_names(spec: dict[str, Any] | None) -> frozenset[str]:
    names: set[str] = set()
    for tool in (spec or {}).get("tools") or []:
        if isinstance(tool, dict) and tool.get("enabled") is False:
            name = str(tool.get("name") or "").strip()
            if name:
                names.add(name)
    return frozenset(names)


def is_spawn_subagent_enabled(spec: dict[str, Any] | None) -> bool:
    """True when the bound spec grants the subagents system plugin."""
    return spawn_subagent_params(spec) is not None


def spawn_subagent_params(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Settings of the granted subagents plugin, or ``None``.

    Name ``subagents`` (or its ``create_subagent`` shortcut) in
    ``spec.system_plugins``. No grant means the agent cannot spawn.
    Settings (``templates``, ``max_spawns``, ``max_depth``) live on that grant.
    """
    from mas.runtime.system_plugins.load import settings_for_capability

    return settings_for_capability(spec, "orchestration")


def is_control_tools_enabled(spec: dict[str, Any] | None) -> bool:
    """True when the bound spec grants the control system plugin."""
    from mas.runtime.system_plugins.load import settings_for_capability

    return settings_for_capability(spec, "control") is not None
