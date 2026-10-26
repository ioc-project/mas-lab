#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SkillToolsPlugin — ToolContract for model-driven skill activation.

FSM placement
-------------
These tools fire on the ``tool_execute`` FSM symbol (the stochastic path,
§6.2 of the product model) when the model decides to call one of:

  activate_skill(name)           — load SKILL.md (tier 2)
  list_skill_files(skill)        — enumerate bundled resources
  read_skill_file(skill, path)   — read a specific resource file

When allow_unload is on, activate_skill also advertises an ``unload``
parameter (activate_skill(name, unload=true) unpins the skill).

The plugin gets ``ctx`` (AutoCtxAssembler) passed at call time and reads
``ctx.skill_registry`` (a SkillRegistry populated by SkillCatalogPlugin) to
look up skill paths.  No shared mutable state between calls.

Security
--------
``read_skill_file`` resolves paths under the skill's base directory and
rejects any path that escapes it (directory traversal guard).

Progressive disclosure — tier 2 / tier 3
-----------------------------------------
``activate_skill`` returns the SKILL.md body (frontmatter stripped) wrapped
in ``<skill_content>`` tags, plus a ``<skill_resources>`` listing of bundled
scripts/references/assets — but does NOT eagerly load them (tier 3 is loaded
on-demand by the model via ``read_skill_file``).

See: https://agentskills.io/client-implementation/adding-skills-support#step-4
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentskills import SkillRegistry, parse_skill_frontmatter
from agentskills.lifecycle import SkillSessionState
from mas.runtime.contracts.tool_contract import ToolContract

from .skill_plugin_base import require_str_arg
from .skill_plugin_registry import SkillPluginRegistry, coerce_skill_impl

logger = logging.getLogger(__name__)

_RESOURCE_DIRS = ("scripts", "references", "assets")


def _unload_flag(arguments: dict[str, Any]) -> bool:
    """True when the caller passed unload=true (bool, or common string forms)."""
    if "unload" not in arguments:
        return False
    value = arguments["unload"]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise TypeError(f"'unload' must be a boolean, got {value!r}")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise TypeError(f"'unload' must be a boolean, got {type(value).__name__}")


class SkillToolsPlugin(ToolContract):
    """ToolContract providing activate_skill, list_skill_files, read_skill_file.

    When ``allow_unload`` is on, ``activate_skill`` also advertises ``unload``.

    Loaded once per agent session by ManifestToolProvider (via the YAML ref in
    ``spec.tools``).  The registry is read from ``ctx.skill_registry`` at each
    tool call — no constructor dependency on bootstrap ordering.

    When constructed with an explicit ``registry``, ``list_tools()`` includes
    the valid skill names in the ``activate_skill`` tool description, helping
    the model avoid hallucinating nonexistent skill names.
    """

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        impl: str = "native",
        base_dir: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._static_registry = registry  # optional: populated by tests or direct use
        self._impl = coerce_skill_impl(impl)
        self._base_dir = Path(base_dir).resolve() if base_dir else None
        self._local_backend_plugin: Any | None = None

    # ------------------------------------------------------------------
    # ToolContract — list_tools and dispatch
    # ------------------------------------------------------------------

    def list_tools(
        self,
        registry: SkillRegistry | None = None,
        activated: set[str] | None = None,
        *,
        allow_unload: bool = False,
    ) -> list[dict[str, Any]]:
        # Build a dynamic description and optional enum including valid skill names.
        # `registry` (from ctx.skill_registry, the live per-session registry --
        # see on_collect_tools) takes priority over the constructor-time
        # `_static_registry`, which the real model-facing tool path never sets
        # (skill-access.tool.yaml constructs this class with no arguments and
        # reads ctx.skill_registry per-call instead -- see on_execute_tool).
        reg = registry if registry is not None else self._static_registry
        valid = list(reg.names()) if reg else []
        # Exclude already-activated skills from the enum whenever at least one
        # skill still isn't activated — unless unload is on, because then the
        # same tool must still be able to name a pinned skill (unload=true).
        if activated and not allow_unload:
            remaining = [n for n in valid if n not in activated]
            valid = remaining or valid
        name_hint = f" Valid names: {valid}." if valid else ""

        name_schema: dict[str, Any] = {
            "type": "string",
            "description": "Skill name as listed in the catalog.",
        }
        if valid:
            name_schema["enum"] = valid

        properties: dict[str, Any] = {"name": name_schema}
        if allow_unload:
            description = (
                "Load a named skill listed in the catalog, or unpin one. "
                "Call with name only to load the body before composing the "
                "user-visible answer when a catalog skill matches. "
                "Call with unload=true to unpin a previously loaded skill; "
                "the catalog listing remains and prior tool results in the "
                "transcript are not rewritten."
                f"{name_hint}"
            )
            properties["unload"] = {
                "type": "boolean",
                "description": (
                    "When true, unpin the named skill instead of loading it."
                ),
                "default": False,
            }
        else:
            description = (
                "Load the full instructions for a named skill listed in the "
                "catalog. Call this before composing the user-visible answer "
                "when a catalog skill matches; the catalog description is "
                "when-to-use only, not the procedure. Returns the skill body "
                "and lists bundled resource files."
                f"{name_hint}"
            )

        return [
            {
                "name": "activate_skill",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": ["name"],
                },
                "semantics": {"concept": "skill", "op": "activate", "subject_arg": "name"},
            },
            {
                "name": "list_skill_files",
                "description": (
                    "List all files inside a skill's directory "
                    "(references, scripts, assets, etc.). "
                    "Use to discover supporting resources before reading them."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {"type": "string", "description": "Skill name."},
                    },
                    "required": ["skill"],
                },
                "semantics": {"concept": "skill", "op": "list", "subject_arg": "skill"},
            },
            {
                "name": "read_skill_file",
                "description": (
                    "Read a file from a skill's directory. "
                    "Path is relative to the skill directory "
                    "(e.g. 'references/rules.md', 'scripts/lint.py'). "
                    "Use after activate_skill lists resources in <skill_resources>."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {"type": "string", "description": "Skill name."},
                        "path": {
                            "type": "string",
                            "description": "Relative path within the skill directory.",
                        },
                    },
                    "required": ["skill", "path"],
                },
                "semantics": {"concept": "skill", "op": "read", "subject_arg": "skill"},
            },
        ]

    def on_execute_tool(
        self, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        """Route to the appropriate handler; extract ctx from kwargs."""
        ctx = kwargs.get("ctx")
        registry = _registry_from_ctx(ctx)

        try:
            if tool_name == "activate_skill":
                name = require_str_arg(arguments, "name")
                if _unload_flag(arguments):
                    return self._deactivate_skill(name, registry, ctx=ctx)
                return self._activate_skill(name, registry, ctx=ctx)
            if tool_name == "list_skill_files":
                return self._list_skill_files(require_str_arg(arguments, "skill"), registry, ctx=ctx)
            if tool_name == "read_skill_file":
                return self._read_skill_file(
                    require_str_arg(arguments, "skill"),
                    require_str_arg(arguments, "path"),
                    registry,
                    ctx=ctx,
                )
        except TypeError as exc:
            return {"error": str(exc)}
        return None  # not our tool

    def on_collect_tools(self, *, ctx: Any = None, **_: Any) -> list[dict[str, Any]]:
        # Prefer the live per-session registry (see ManifestToolProvider.list_tools's
        # ctx threading) over the constructor-time _static_registry -- this is what
        # gives activate_skill's "name" parameter a real enum constraint in the
        # actual model-facing tool path (skill-access.tool.yaml never sets
        # _static_registry). Without it, the model has no schema-level guard
        # against a name that doesn't exactly match a registered skill.
        ctx_registry = _registry_from_ctx(ctx)
        reg = ctx_registry if ctx_registry is not None else self._static_registry
        # Do not advertise skill tools when no skill is registered (default:
        # nothing from the skills system is exposed to the LLM).
        if not reg:
            return []
        allow_unload = bool(getattr(ctx, "skill_allow_unload", False)) if ctx is not None else False
        session = getattr(ctx, "skill_session_state", None) if ctx is not None else None
        activated = set(session.activated_names()) if session is not None else None
        return self.list_tools(reg, activated=activated, allow_unload=allow_unload)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _activate_skill(self, name: str, registry: SkillRegistry | None, ctx: Any = None) -> dict[str, Any]:
        """Tier-2 progressive disclosure: return body + resource listing.

        Deduplication (agentskills.io Step 5):
        If the skill has already been activated this session, return a compact
        notice instead of re-loading the full body.  This prevents duplicate
        skill instructions from accumulating in the conversation context.
        """
        if not name:
            return {"error": "name is required"}

        record = registry.get(name) if registry else None
        if record is None:
            available = registry.names() if registry else []
            return {
                "error": (
                    f"Skill {name!r} not found in registry. "
                    f"Available: {available}"
                )
            }

        backend_plugin = _backend_plugin_from_ctx(
            ctx,
            impl=self._impl,
            base_dir=self._base_dir,
            local_cache=self,
        )

        # Deduplication — check session state.
        #
        # NOTE: dedup is only possible when `ctx.skill_session_state` is set
        # (populated by SkillCatalogPlugin during bootstrap). If it's None
        # (e.g. this plugin is exercised standalone/in a test without the
        # full context stack), this check is a no-op and `activate_skill`
        # will re-return the full body on every call — this is intentional
        # graceful degradation, not a bug, since without session state there
        # is nowhere to record "already activated" across calls anyway.
        session: SkillSessionState | None = getattr(ctx, "skill_session_state", None)
        if session is not None and session.is_activated(name):
            session.note_reactivation_attempt(name)
            rec = session.get(name)
            turn_info = f" (activated at turn {rec.turn})" if rec and rec.turn else ""
            logger.debug("activate_skill(%r): already in session context — skipping re-load", name)
            return {
                "notice": (
                    f"Skill '{name}' instructions are already in context{turn_info}. "
                    "Follow the earlier skill content for the full instructions."
                ),
                "skill": name,
                "already_activated": True,
            }

        resources: list[str] = []
        if backend_plugin is not None:
            try:
                activation = backend_plugin.activate(name)
            except Exception as exc:
                return {"error": f"Cannot activate skill {name!r}: {exc}"}
            body = activation.body
            resources = sorted(str(r) for r in activation.resources.keys())
        else:
            try:
                raw = record.path.read_text(encoding="utf-8")
            except OSError as exc:
                return {"error": f"Cannot read skill {name!r}: {exc}"}

            _meta, body = parse_skill_frontmatter(raw)

            # Enumerate bundled resources (tier 3 — listed but not yet loaded)
            for sub in _RESOURCE_DIRS:
                sub_dir = record.base_dir / sub
                if sub_dir.is_dir():
                    for f in sorted(sub_dir.iterdir()):
                        if f.is_file():
                            resources.append(f"{sub}/{f.name}")
            for f in sorted(record.base_dir.iterdir()):
                if f.is_file() and f.name != "SKILL.md":
                    rel = f.name
                    if rel not in resources:
                        resources.append(rel)

        content_parts = [f'<skill_content name="{name}">', body]
        if resources:
            content_parts.append("\n<skill_resources>")
            for r in resources:
                content_parts.append(f"  <file>{r}</file>")
            content_parts.append("</skill_resources>")
        content_parts.append(f"\nSkill directory: {record.base_dir}")
        content_parts.append("</skill_content>")

        content = "\n".join(content_parts)

        # Mark as activated in session state
        if session is not None:
            turn = getattr(ctx, "turn_index", 0) or 0
            session.mark_activated(name, turn=turn)
            logger.debug("activate_skill(%r): activated at turn %d", name, turn)

        # Register body in ActivatedSkillsContextPlugin for compaction protection
        # unless this harness profile asked not to pin the full body.
        pin = getattr(ctx, "skill_pin_activated", True) if ctx is not None else True
        activated_plugin = getattr(ctx, "activated_skills_plugin", None) if ctx is not None else None
        if pin and activated_plugin is not None:
            try:
                activated_plugin.add_activated(name, body)
            except Exception:  # pragma: no cover
                pass  # never block activation on compaction-protection failure

        result: dict[str, Any] = {
            "content": content,
            "skill": name,
            "base_dir": str(record.base_dir),
            "chunk": {
                "pin_policy": "never",
                "semantic": "instruction",
                "source": "activate_skill",
                "section_id": f"skills/activated/{name}",
            },
        }
        # Include compatibility note so the model can check environment requirements
        if record.compatibility:
            result["compatibility"] = record.compatibility
        return result

    def _deactivate_skill(
        self, name: str, registry: SkillRegistry | None, ctx: Any = None
    ) -> dict[str, Any]:
        """Unpin a previously activated skill. Catalog listing is unchanged.

        Prior tool results that still sit in the transcript are not rewritten.
        """
        if not name:
            return {"error": "name is required"}

        record = registry.get(name) if registry else None
        if record is None:
            available = registry.names() if registry else []
            return {
                "error": (
                    f"Skill {name!r} not found in registry. "
                    f"Available: {available}"
                )
            }

        allow_unload = bool(getattr(ctx, "skill_allow_unload", False)) if ctx is not None else False
        if not allow_unload:
            return {
                "error": (
                    "Skill unload is disabled. activate_skill(name, unload=true) "
                    "is not part of the Agent Skills spec and is not advertised "
                    "unless spec.context_sources native.allow_unload is true. "
                    f"Skill {name!r} stays in force."
                ),
                "skill": name,
                "deactivated": False,
            }

        session: SkillSessionState | None = getattr(ctx, "skill_session_state", None) if ctx is not None else None
        was_active = session.is_activated(name) if session is not None else False
        if session is not None:
            session.mark_deactivated(name)

        activated_plugin = getattr(ctx, "activated_skills_plugin", None) if ctx is not None else None
        if activated_plugin is not None:
            try:
                activated_plugin.remove_activated(name)
            except Exception:  # pragma: no cover
                pass

        logger.debug("activate_skill(%r, unload=true): deactivated=%s", name, was_active)
        return {
            "notice": (
                f"Skill '{name}' is no longer in force. "
                "The catalog listing remains. Call activate_skill again to reload."
                if was_active
                else f"Skill '{name}' was not in force."
            ),
            "skill": name,
            "deactivated": was_active,
            "already_activated": False,
        }

    def _list_skill_files(
        self,
        skill: str,
        registry: SkillRegistry | None,
        *,
        ctx: Any = None,
    ) -> dict[str, Any]:
        """List all files inside the skill's directory."""
        if not skill:
            return {"error": "skill is required"}

        record = registry.get(skill) if registry else None
        if record is None:
            return {"error": f"Skill {skill!r} not found"}

        backend_plugin = _backend_plugin_from_ctx(
            ctx,
            impl=self._impl,
            base_dir=self._base_dir,
            local_cache=self,
        )
        if backend_plugin is not None:
            try:
                activation = backend_plugin.activate(skill)
            except Exception as exc:
                return {"error": f"Cannot list resources for {skill!r}: {exc}"}
            files = sorted(str(r) for r in activation.resources.keys())
            return {"skill": skill, "files": files, "base_dir": str(record.base_dir)}

        files: list[str] = []
        try:
            for f in sorted(record.base_dir.rglob("*")):
                if f.is_file() and f.name != "SKILL.md":
                    try:
                        files.append(str(f.relative_to(record.base_dir)))
                    except ValueError:
                        pass
        except OSError as exc:
            return {"error": f"Cannot list skill directory: {exc}"}

        return {"skill": skill, "files": files, "base_dir": str(record.base_dir)}

    def _read_skill_file(
        self,
        skill: str,
        path: str,
        registry: SkillRegistry | None,
        *,
        ctx: Any = None,
    ) -> dict[str, Any]:
        """Read a file from the skill's directory (directory-traversal safe)."""
        if not skill:
            return {"error": "skill is required"}
        if not path:
            return {"error": "path is required"}

        record = registry.get(skill) if registry else None
        if record is None:
            return {"error": f"Skill {skill!r} not found"}

        backend_plugin = _backend_plugin_from_ctx(
            ctx,
            impl=self._impl,
            base_dir=self._base_dir,
            local_cache=self,
        )
        if backend_plugin is not None:
            try:
                content = backend_plugin.read_resource(skill, path)
            except Exception as exc:
                return {"error": f"Cannot read {path!r}: {exc}"}
            return {"skill": skill, "path": path, "content": content}

        base = record.base_dir.resolve()
        target = (base / path).resolve()

        # Security: reject any path that escapes the skill directory
        try:
            target.relative_to(base)
        except ValueError:
            return {"error": f"Path {path!r} escapes the skill directory — access denied"}

        if not target.is_file():
            return {"error": f"File {path!r} not found in skill {skill!r}"}

        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {"error": f"Cannot read {path!r}: {exc}"}

        return {"skill": skill, "path": path, "content": content}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _registry_from_ctx(ctx: Any) -> SkillRegistry | None:
    """Extract SkillRegistry from the assembly context object, if present."""
    return getattr(ctx, "skill_registry", None)


def _backend_plugin_from_ctx(
    ctx: Any,
    *,
    impl: str,
    base_dir: Path | None,
    local_cache: SkillToolsPlugin,
) -> Any | None:
    plugin = getattr(ctx, "skill_backend_plugin", None) if ctx is not None else None
    if plugin is not None:
        return plugin
    if impl == "native":
        return None
    if local_cache._local_backend_plugin is None:
        resolved_base = (base_dir or Path.cwd()).resolve()
        plugin = SkillPluginRegistry(impl=impl).get_plugin(base_dir=resolved_base)
        plugin.discover(resolved_base)
        local_cache._local_backend_plugin = plugin
    return local_cache._local_backend_plugin
