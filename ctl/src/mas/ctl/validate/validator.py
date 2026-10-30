#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest validation — ctl responsibility; runtime never validates YAML."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mas.ctl.overlay.normalize import normalize_overlay
from mas.ctl.validate.providers import check_provider_tool_claims
from mas.ctl.validate.refs import check_refs, resolve_refs_enabled
from mas.ctl.validate.schema_errors import humanize_schema_error
from mas.ctl.validate.schemas import declared_kind, lab_schema_registry, load_schema, schema_path_for_kind
from mas.ctl.validate.separation import check_separation


def _path_sort_key(path: Iterable[str | int]) -> tuple[tuple[int, str | int], ...]:
    """Stable sort key for JSON Schema error paths with mixed string and int segments.

    ``jsonschema`` exposes list indexes as integers and mapping keys as strings,
    which cannot be compared directly in Python 3.
    """
    return tuple((0, segment) if isinstance(segment, str) else (1, segment) for segment in path)


@dataclass
class ValidationIssue:
    level: str  # error | warning
    message: str
    path: str = ""


@dataclass
class ValidationResult:
    ok: bool
    kind: str | None = None
    source: str = ""
    issues: list[ValidationIssue] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        errors = [i for i in self.issues if i.level == "error"]
        if errors:
            lines = "\n".join(f"  [{i.level}] {i.message}" for i in errors)
            raise ValueError(f"validation failed for {self.source}:\n{lines}")


def validation_enabled() -> bool:
    return os.environ.get("MAS_MANIFEST_VALIDATE", "1") not in ("0", "false", "False")


def strict_mode() -> bool:
    return os.environ.get("MAS_MANIFEST_STRICT", "1") not in ("0", "false", "False")


def _looks_like_path_ref(value: str) -> bool:
    return "/" in value or "\\" in value or value.endswith((".yaml", ".yml", ".json", ".py"))


def _validate_tool_manifest_semantics(data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return issues

    if "implementation" in spec:
        issues.append(
            ValidationIssue(
                "error",
                "spec.implementation is not supported; use spec.impl",
                path="spec.implementation",
            )
        )

    impl = spec.get("impl")
    if not isinstance(impl, dict):
        issues.append(
            ValidationIssue(
                "error",
                "spec.impl is required and must be an object",
                path="spec.impl",
            )
        )
        return issues

    module_path = impl.get("module_path")
    if not isinstance(module_path, str) or not module_path.strip():
        issues.append(
            ValidationIssue(
                "error",
                "spec.impl.module_path is required and must be a non-empty string",
                path="spec.impl.module_path",
            )
        )

    class_name = impl.get("class_name")
    if class_name is not None and not isinstance(class_name, str):
        issues.append(
            ValidationIssue(
                "error",
                "spec.impl.class_name must be a string or null",
                path="spec.impl.class_name",
            )
        )

    impl_kind = impl.get("kind")
    allowed_kinds = {"python", "remote_tool", "openapi"}
    if impl_kind is not None and impl_kind not in allowed_kinds:
        issues.append(
            ValidationIssue(
                "error",
                "spec.impl.kind must be one of: python, remote_tool, openapi",
                path="spec.impl.kind",
            )
        )

    if "type" in impl:
        issues.append(
            ValidationIssue(
                "error",
                "spec.impl.type is not supported; use spec.impl.kind",
                path="spec.impl.type",
            )
        )

    return issues


_ALLOWED_TOOL_REF_PREFIXES = ("./", "../", "pkg://", "samples:", "standard:", "bundle://")


def _explicit_path_ref_issue(value: str, path: str) -> ValidationIssue | None:
    if _looks_like_path_ref(value) and not value.startswith(_ALLOWED_TOOL_REF_PREFIXES):
        return ValidationIssue(
            "error",
            f"{path} looks like a file path but is not explicit; use './...' (got {value!r})",
            path=path,
        )
    return None


def _validate_agent_tool_refs_semantics(
    spec: dict[str, Any],
    *,
    agent_manifest_dir: Path | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tools = spec.get("tools")
    if not isinstance(tools, list):
        return issues

    for idx, entry in enumerate(tools):
        if isinstance(entry, str):
            if issue := _explicit_path_ref_issue(entry, f"spec.tools.{idx}"):
                issues.append(issue)
            continue

        if not isinstance(entry, dict):
            continue

        ref = entry.get("ref")
        if isinstance(ref, str):
            if issue := _explicit_path_ref_issue(ref, f"spec.tools.{idx}.ref"):
                issues.append(issue)
            if agent_manifest_dir and ref.startswith(("./", "../")):
                target = (agent_manifest_dir / ref).resolve()
                if not target.exists():
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"referenced tool file does not exist: {target}",
                            path=f"spec.tools.{idx}.ref",
                        )
                    )

        module_path = entry.get("module_path")
        if module_path is not None and not isinstance(module_path, str):
            issues.append(
                ValidationIssue(
                    "error",
                    "spec.tools[].module_path must be a string",
                    path=f"spec.tools.{idx}.module_path",
                )
            )

    return issues


def _validate_overlay_semantics(
    data: dict[str, Any],
    *,
    source: Path | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if source and source.exists():
        text = source.read_text(encoding="utf-8")
        if "!append" in text:
            issues.append(
                ValidationIssue(
                    "error",
                    "unsupported YAML tag '!append'; use $op.add instead",
                    path="spec.patch",
                )
            )

    patch = (data.get("spec") or {}).get("patch")
    if not isinstance(patch, dict):
        return issues

    agents = patch.get("agents")
    if not isinstance(agents, dict):
        return issues

    for agent_id, agent_patch in agents.items():
        if not isinstance(agent_patch, dict):
            continue
        context = agent_patch.get("context")
        if not isinstance(context, dict):
            continue
        role = context.get("role")
        if role is not None and not isinstance(role, (str, list, dict)):
            issues.append(
                ValidationIssue(
                    "error",
                    "context.role must be string, list, or $op object",
                    path=f"spec.patch.agents.{agent_id}.context.role",
                )
            )
    return issues


def validate_data(
    data: dict[str, Any],
    *,
    source: str = "",
    kind: str | None = None,
    strict: bool | None = None,
    base_dir: Path | None = None,
    resolve_refs: bool | None = None,
) -> ValidationResult:
    """Validate manifest dict against JSON Schema Draft-07, binding shapes, separation, refs."""
    if strict is None:
        strict = strict_mode()
    resolved_kind = kind or declared_kind(data)
    result = ValidationResult(ok=True, kind=resolved_kind, source=source)

    if resolved_kind is None:
        result.issues.append(
            ValidationIssue("error", "manifest missing kind (expected explicit kind or lab envelope key)")
        )
        result.ok = False
        return result

    if schema_path_for_kind(resolved_kind) is None:
        result.issues.append(ValidationIssue("warning", f"no schema file for kind {resolved_kind!r}"))
        return result

    try:
        import jsonschema
    except ImportError as exc:
        result.issues.append(ValidationIssue("error", f"jsonschema required: {exc}"))
        result.ok = False
        return result

    schema = load_schema(resolved_kind)
    validator = jsonschema.Draft7Validator(schema, registry=lab_schema_registry())
    for err in sorted(validator.iter_errors(data), key=lambda e: _path_sort_key(e.path)):
        path = ".".join(str(p) for p in err.path) or "(root)"
        level = "error" if strict else "warning"
        result.issues.append(ValidationIssue(level, humanize_schema_error(err), path=path))

    if resolved_kind == "experiment":
        from mas.ctl.validate.deprecations import collect_experiment_deprecations

        for msg in collect_experiment_deprecations(data, schema):
            result.issues.append(ValidationIssue("warning", msg, path="experiment"))

    if resolved_kind in {"mas", "workflow", "overlay"}:
        from mas.ctl.validate.deprecations import collect_delegates_to_deprecation

        for path, msg in collect_delegates_to_deprecation(data):
            result.issues.append(ValidationIssue("warning", msg, path=path))

    if resolved_kind == "infra":
        from mas.ctl.validate.deprecations import collect_endpoint_usage_deprecation

        for path, msg in collect_endpoint_usage_deprecation(data):
            result.issues.append(ValidationIssue("warning", msg, path=path))

    if any(i.level == "error" for i in result.issues):
        result.ok = False

    from mas.ctl.validate.identity import collect_identity_issues

    result.issues.extend(collect_identity_issues(data, resolved_kind))

    if resolved_kind == "agent":
        try:
            from mas.ctl.manifest.spec_bindings import validate_agent_spec_bindings

            validate_agent_spec_bindings(data.get("spec"))
        except Exception as exc:
            result.issues.append(ValidationIssue("error", str(exc), path="spec"))
            result.ok = False
        agent_spec = data.get("spec")
        result.issues.extend(
            _validate_agent_tool_refs_semantics(
                agent_spec if isinstance(agent_spec, dict) else {},
                agent_manifest_dir=base_dir,
            )
        )

    if resolved_kind == "tool":
        result.issues.extend(_validate_tool_manifest_semantics(data))

    if resolved_kind == "overlay":
        result.issues.extend(
            _validate_overlay_semantics(
                data,
                source=Path(source) if source else None,
            )
        )

    if resolved_kind == "deployment":
        spec = data.get("spec") or {}
        runtime_id = spec.get("runtime_id")
        if runtime_id:
            try:
                from mas.ctl.registry.catalog import validate_runtime_id

                validate_runtime_id(str(runtime_id))
            except KeyError as exc:
                result.issues.append(ValidationIssue("error", str(exc), path="spec.runtime_id"))
                result.ok = False

    for msg in check_separation(data, resolved_kind):
        result.issues.append(ValidationIssue("error" if strict else "warning", msg))
    if strict and any(i.level == "error" for i in result.issues):
        result.ok = False

    do_refs = resolve_refs if resolve_refs is not None else resolve_refs_enabled()
    if do_refs:
        ref_base = base_dir
        if ref_base is None and source:
            ref_base = Path(source).parent
        for msg in check_refs(data, resolved_kind, ref_base):
            result.issues.append(ValidationIssue("error" if strict else "warning", msg))

    claim_base = ref_base if do_refs else base_dir
    if claim_base is None and source:
        claim_base = Path(source).parent
    for msg in check_provider_tool_claims(data, resolved_kind, claim_base):
        result.issues.append(ValidationIssue("error" if strict else "warning", msg))

    if any(i.level == "error" for i in result.issues):
        result.ok = False
    return result


def validate_file(
    path: Path,
    *,
    kind: str | None = None,
    strict: bool | None = None,
    resolve_refs: bool | None = None,
) -> ValidationResult:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = str(exc)
        if "!append" in msg:
            msg = "unsupported YAML tag '!append'; use $op.add in overlays"
        return ValidationResult(
            ok=False,
            kind=kind,
            source=str(path),
            issues=[ValidationIssue("error", msg)],
        )
    if not isinstance(raw, dict):
        # Not a mapping — not a MAS manifest; skip gracefully.
        return ValidationResult(ok=True, kind=None, source=str(path), issues=[])
    if declared_kind(raw) is None and kind is None:
        return ValidationResult(ok=True, kind=None, source=str(path), issues=[])
    if (kind == "overlay") or (kind is None and declared_kind(raw) == "overlay"):
        try:
            raw = normalize_overlay(raw, name=path.stem)
        except Exception as exc:
            return ValidationResult(
                ok=False,
                kind="overlay",
                source=str(path),
                issues=[ValidationIssue("error", str(exc))],
            )
    return validate_data(
        raw,
        source=str(path),
        kind=kind,
        strict=strict,
        base_dir=path.parent,
        resolve_refs=resolve_refs,
    )


def validate_tree(
    root: Path,
    *,
    strict: bool | None = None,
    resolve_refs: bool | None = None,
) -> list[ValidationResult]:
    """Validate all *.yaml under root."""
    results: list[ValidationResult] = []
    for path in sorted(root.rglob("*.yaml")):
        if path.name.startswith("."):
            continue
        results.append(validate_file(path, strict=strict, resolve_refs=resolve_refs))
    return results
