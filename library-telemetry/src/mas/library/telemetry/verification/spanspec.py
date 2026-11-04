#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``SpanValidator`` — validate OTel SDK spans against a ``.spanspec.yaml`` spec.

Analogous to SHACL for RDF graphs (and to ``library-kg``'s ``kg/verifier.py``):
declarative shape constraints evaluated against a list of OTel spans in SDK
JSONL format.

Conformance model
-----------------
A span is *passing* at level L  ↔  all ``required``    attributes present.
A span is *full*    at level L  ↔  all ``required`` + ``recommended`` attrs present.

Levels (ascending strictness):
  L1  structural     OTel GenAI / envelope structural conventions
  L2  gen_ai_semconv observe-sdk / GenAI attribute contract
  L3  mas_native_roundtrip MAS Framework observability
  L4  full           All recommended fields present (complete KG)

Diagnostics
-----------
- **error**   — required attribute absent, or unknown attribute on a known span
               type when ``strict=True``
- **warning** — recommended attribute absent, or unknown span type encountered
- **info**    — informational; does not affect conformance
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from mas.library.telemetry._dist import extra_hint
from mas.library.telemetry.exceptions import SpecNotFoundError

StrictnessMode = Literal["required", "recommended", "complete"]

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]

# Packaged schemas ship alongside the library in mas/library/telemetry/schemas/.
_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
_BUILTIN_SPEC = _SCHEMA_DIR / "mas.spanspec.yaml"
_OBSERVE_SDK_SPEC = _SCHEMA_DIR / "genai-observe-sdk.spanspec.yaml"

# Optional internal fields on native events.jsonl records (never required).
EVENT_INTERNAL_FIELDS: frozenset[str] = frozenset(
    {
        "layer",
        "block",
        "summand",
        "mealy_symbol",
    }
)

_LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}


# ── Data types ───────────────────────────────────────────────────────────────


@dataclass
class Violation:
    level: str
    severity: str  # "error" | "warning" | "info"
    rule: str
    message: str
    span_name: str = ""
    trace_id: str = ""
    span_id: str = ""
    attribute: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class ValidationReport:
    spec_file: str
    total_spans: int = 0
    known_span_types: Set[str] = field(default_factory=set)
    unknown_span_types: Set[str] = field(default_factory=set)
    _violations: List[Violation] = field(default_factory=list, repr=False)

    def add(self, v: Violation) -> None:
        self._violations.append(v)

    @property
    def violations(self) -> List[Violation]:
        return list(self._violations)

    def errors(self, level: Optional[str] = None) -> List[Violation]:
        vs = [v for v in self._violations if v.severity == "error"]
        return [v for v in vs if v.level == level] if level else vs

    def warnings(self, level: Optional[str] = None) -> List[Violation]:
        vs = [v for v in self._violations if v.severity == "warning"]
        return [v for v in vs if v.level == level] if level else vs

    def conformance(self, level: str) -> str:
        """Return ``'failing'``, ``'passing'``, or ``'full'`` for *level*."""
        if self.errors(level):
            return "failing"
        if self.warnings(level):
            return "passing"
        return "full"

    def summary(self) -> Dict[str, Any]:
        levels = ["L1", "L2", "L3", "L4"]
        return {
            "spec_file": self.spec_file,
            "total_spans": self.total_spans,
            "known_span_types": sorted(self.known_span_types),
            "unknown_span_types": sorted(self.unknown_span_types),
            "conformance": {L: self.conformance(L) for L in levels},
            "counts": {
                "errors": len(self.errors()),
                "warnings": len(self.warnings()),
            },
            "violations": [v.to_dict() for v in self._violations],
        }


# ── Validator ────────────────────────────────────────────────────────────────


class SpanValidator:
    """Validate a list of OTel SDK spans against a SpanSpec.

    Parameters
    ----------
    spec_file:
        Path to a ``.spanspec.yaml`` file.  Defaults to the built-in
        ``mas.spanspec.yaml`` bundled with this package.

    Examples
    --------
    >>> v = SpanValidator()
    >>> report = v.validate_file("path/to/otel_sdk_spans.jsonl")
    >>> print(report.conformance("L2"))
    'passing'
    """

    def __init__(self, spec_file: Optional[str | Path] = None) -> None:
        if _yaml is None:
            raise RuntimeError(
                "pyyaml is required for SpanValidator. Install the verify extra: "
                f"{extra_hint('verify')}"
            )
        path = Path(spec_file) if spec_file else _BUILTIN_SPEC
        if not path.exists():
            raise SpecNotFoundError(path)
        spec = _yaml.safe_load(path.read_text(encoding="utf-8"))
        self._spec_file = str(path)

        # span_name → {level_id → {"required": [...], "recommended": [...], "optional": [...]}}
        self._shapes: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        # observe-sdk span_kind (agent/llm/tool/…) → same level map
        self._kind_shapes: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        for shape in spec.get("shapes", []):
            levels_map: Dict[str, Dict[str, List[str]]] = {}
            field_lists = shape.get("levels") or shape.get("fields") or []
            for lspec in field_lists:
                lid = str(lspec.get("level") or "")
                if not lid:
                    continue
                bucket = levels_map.setdefault(
                    lid, {"required": [], "recommended": [], "optional": []}
                )
                bucket["required"].extend(lspec.get("required") or [])
                bucket["recommended"].extend(lspec.get("recommended") or [])
                bucket["optional"].extend(lspec.get("optional") or [])
            name = shape.get("span_name")
            if name:
                self._shapes[name] = levels_map
            kinds = shape.get("span_kind")
            if kinds:
                for kind in str(kinds).split(","):
                    kind = kind.strip()
                    if kind:
                        self._kind_shapes[kind] = levels_map

        # Suffix shapes: matched by SpanName suffix (ioa_observe %.agent, %.chat, …)
        self._suffix_shapes: List[tuple[str, Dict[str, Dict[str, List[str]]]]] = []
        for shape in spec.get("suffix_shapes", []):
            suffix = shape["span_name_suffix"]
            levels_map: Dict[str, Dict[str, List[str]]] = {}
            for lspec in shape.get("levels", []):
                levels_map[lspec["level"]] = {
                    "required": lspec.get("required", []),
                    "recommended": lspec.get("recommended", []),
                    "optional": lspec.get("optional", []),
                }
            self._suffix_shapes.append((suffix, levels_map))
        self._suffix_shapes.sort(key=lambda t: -len(t[0]))

        # Global attributes: level_id → {"required": [...], "recommended": [...]}
        self._global: Dict[str, Dict[str, List[str]]] = {}
        for g in spec.get("global_attributes", []):
            self._global[g["level"]] = {
                "required": g.get("required", []),
                "recommended": g.get("recommended", []),
            }

        self._consistency_rules: List[Dict[str, Any]] = spec.get(
            "consistency_rules", []
        )
        self._attribute_types: Dict[str, Dict[str, Any]] = (
            spec.get("attribute_types") or {}
        )

    @property
    def spec_file(self) -> str:
        return self._spec_file

    def declared_levels(self) -> Set[str]:
        """Level ids that appear in this spec's shapes or globals."""
        levels: Set[str] = set(self._global)
        for shape in self._shapes.values():
            levels.update(shape)
        for shape in self._kind_shapes.values():
            levels.update(shape)
        for _, shape in self._suffix_shapes:
            levels.update(shape)
        return levels

    @staticmethod
    def observe_span_kind(span: Dict[str, Any]) -> str:
        """Dispatch key used by genai-observe-sdk.spanspec.yaml."""
        attrs = span.get("attributes") or {}
        kind = str(
            attrs.get("ioa_observe.span.kind")
            or attrs.get("traceloop.span.kind")
            or ""
        ).strip()
        if kind:
            return kind
        name = str(span.get("name") or "")
        suffix_map = {
            ".agent": "agent",
            ".task": "agent",
            ".tool": "tool",
            ".chat": "llm",
            ".graph": "workflow",
            ".workflow": "workflow",
            ".routing": "routing",
            ".memory": "memory",
            ".rag": "rag",
            ".skill": "skill",
            ".processing": "processing",
            ".governance": "governance",
            ".context": "context",
        }
        for suffix, mapped in suffix_map.items():
            if name.endswith(suffix):
                return mapped
        lowered = name.lower()
        if any(
            token in lowered
            for token in ("chat", "completion", "generate", "messages", "invoke")
        ):
            return "llm"
        return ""

    @staticmethod
    def looks_like_observe_sdk(spans: List[Dict[str, Any]]) -> bool:
        """True when the dump uses observe-sdk dotted names, not the raw mas shape."""
        for span in spans:
            name = str(span.get("name") or "")
            if name == "openclaw.request" or name.startswith("invoke_agent "):
                return True
            if name.endswith(
                (
                    ".agent",
                    ".chat",
                    ".tool",
                    ".task",
                    ".workflow",
                    ".processing",
                    ".governance",
                    ".context",
                    ".memory",
                    ".rag",
                    ".skill",
                )
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        spans: List[Dict[str, Any]],
        *,
        strictness: StrictnessMode = "required",
    ) -> ValidationReport:
        """Validate a list of span dicts (OTel SDK JSONL format)."""
        report = ValidationReport(spec_file=self._spec_file)
        report.total_spans = len(spans)
        for span in spans:
            self._validate_span(span, report, strictness=strictness)
        self._validate_references(spans, report)
        self._run_consistency_rules(spans, report)
        return report

    def validate_file(
        self,
        path: str | Path,
        *,
        strictness: StrictnessMode = "required",
    ) -> ValidationReport:
        """Load an OTel SDK spans JSONL file and validate it."""
        return self.validate(load_spans(path), strictness=strictness)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _attrs(span: Dict[str, Any]) -> Dict[str, Any]:
        return span.get("attributes") or {}

    @staticmethod
    def _span_id(span: Dict[str, Any]) -> str:
        ctx = span.get("context") or {}
        return ctx.get("span_id", "") or span.get("span_id", "")

    @staticmethod
    def _trace_id(span: Dict[str, Any]) -> str:
        ctx = span.get("context") or {}
        return ctx.get("trace_id", "") or span.get("trace_id", "")

    def _validate_span(
        self,
        span: Dict[str, Any],
        report: ValidationReport,
        *,
        strictness: StrictnessMode = "required",
    ) -> None:
        name = span.get("name", "")
        attrs = self._attrs(span)
        span_id = self._span_id(span)
        trace_id = self._trace_id(span)

        self._validate_attribute_types(span, attrs, report)

        # ── Global per-level constraints (every span) ──────────────────
        for level_id, constraints in self._global.items():
            for attr in constraints.get("required", []):
                if not self._attr_present(attrs, attr):
                    report.add(
                        Violation(
                            level=level_id,
                            severity="error",
                            rule="global_required_attr",
                            span_name=name,
                            trace_id=trace_id,
                            span_id=span_id,
                            attribute=attr,
                            message=f"'{attr}' is required on every span",
                        )
                    )
            for attr in constraints.get("recommended", []):
                if not self._attr_present(attrs, attr):
                    report.add(
                        Violation(
                            level=level_id,
                            severity=self._missing_severity(
                                strictness, tier="recommended"
                            ),
                            rule="global_recommended_attr",
                            span_name=name,
                            trace_id=trace_id,
                            span_id=span_id,
                            attribute=attr,
                            message=f"'{attr}' is recommended on every span",
                        )
                    )

        # ── Per-span-type shape constraints ────────────────────────────
        shape = self._shapes.get(name)
        if shape is not None:
            report.known_span_types.add(name)
        else:
            suffix_shape: Optional[Dict[str, Dict[str, List[str]]]] = None
            for suffix, levels_map in self._suffix_shapes:
                if name.endswith(suffix):
                    suffix_shape = levels_map
                    report.known_span_types.add(name)
                    break
            if suffix_shape is not None:
                shape = suffix_shape
            else:
                kind = self.observe_span_kind(span)
                kind_shape = self._kind_shapes.get(kind) if kind else None
                if kind_shape is not None:
                    shape = kind_shape
                    report.known_span_types.add(name or kind)
                else:
                    if name:
                        report.unknown_span_types.add(name)
                        report.add(
                            Violation(
                                level="L3",
                                severity="warning",
                                rule="unknown_span_type",
                                span_name=name,
                                trace_id=trace_id,
                                span_id=span_id,
                                message=f"Unknown span type '{name}' — not defined in spec",
                            )
                        )
                    return

        for level_id, constraints in shape.items():
            for attr in constraints.get("required", []):
                if not self._attr_present(attrs, attr):
                    report.add(
                        Violation(
                            level=level_id,
                            severity="error",
                            rule="missing_required_attr",
                            span_name=name,
                            trace_id=trace_id,
                            span_id=span_id,
                            attribute=attr,
                            message=f"Required attribute '{attr}' is missing",
                        )
                    )
            for attr in constraints.get("recommended", []):
                if not self._attr_present(attrs, attr):
                    report.add(
                        Violation(
                            level=level_id,
                            severity=self._missing_severity(
                                strictness, tier="recommended"
                            ),
                            rule="missing_recommended_attr",
                            span_name=name,
                            trace_id=trace_id,
                            span_id=span_id,
                            attribute=attr,
                            message=f"Recommended attribute '{attr}' is absent",
                        )
                    )
            if strictness == "complete":
                for attr in constraints.get("optional", []):
                    if not self._attr_present(attrs, attr):
                        report.add(
                            Violation(
                                level=level_id,
                                severity="error",
                                rule="missing_optional_attr",
                                span_name=name,
                                trace_id=trace_id,
                                span_id=span_id,
                                attribute=attr,
                                message=f"Optional attribute '{attr}' is required in complete strictness",
                            )
                        )

    @staticmethod
    def _missing_severity(strictness: StrictnessMode, *, tier: str) -> str:
        if tier == "recommended" and strictness in {"recommended", "complete"}:
            return "error"
        return "warning"

    @staticmethod
    def _attr_present(attrs: Dict[str, Any], attr: str) -> bool:
        if attr not in attrs:
            return False
        value = attrs.get(attr)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    def _validate_attribute_types(
        self,
        span: Dict[str, Any],
        attrs: Dict[str, Any],
        report: ValidationReport,
    ) -> None:
        if not self._attribute_types:
            return
        name = span.get("name", "")
        span_id = self._span_id(span)
        trace_id = self._trace_id(span)
        for attr, spec in self._attribute_types.items():
            if attr not in attrs:
                continue
            err = self._check_attr_type(attr, attrs[attr], spec)
            if err:
                report.add(
                    Violation(
                        level=str(spec.get("level", "L3")),
                        severity="error",
                        rule="attribute_type",
                        span_name=name,
                        trace_id=trace_id,
                        span_id=span_id,
                        attribute=attr,
                        message=err,
                    )
                )

    def _check_attr_type(
        self, attr: str, value: Any, spec: Dict[str, Any]
    ) -> str | None:
        expected = spec.get("type")
        if expected == "string":
            if not isinstance(value, str):
                return f"'{attr}' must be a string, got {type(value).__name__}"
            min_len = spec.get("min_length")
            if min_len is not None and len(value.strip()) < int(min_len):
                return f"'{attr}' must have length >= {min_len}"
            enum = spec.get("enum")
            if enum and value not in enum:
                return f"'{attr}' must be one of {enum}, got {value!r}"
        elif expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                return f"'{attr}' must be an integer, got {type(value).__name__}"
        elif expected == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"'{attr}' must be a number, got {type(value).__name__}"
        elif expected == "boolean":
            if not isinstance(value, bool):
                return f"'{attr}' must be a boolean, got {type(value).__name__}"
        return None

    def _validate_references(
        self, spans: List[Dict[str, Any]], report: ValidationReport
    ) -> None:
        span_by_id: Dict[str, Dict[str, Any]] = {}
        call_ids_by_trace: Dict[str, Set[str]] = {}
        null_parent = {"", "None", "0x0000000000000000"}

        for span in spans:
            sid = self._span_id(span)
            if sid:
                span_by_id[sid] = span
            trace_id = self._trace_id(span)
            call_id = self._attrs(span).get("mas.call.id")
            if trace_id and call_id:
                call_ids_by_trace.setdefault(trace_id, set())
                if call_id in call_ids_by_trace[trace_id]:
                    report.add(
                        Violation(
                            level="L3",
                            severity="error",
                            rule="duplicate_call_id",
                            span_name=span.get("name", ""),
                            trace_id=trace_id,
                            span_id=sid,
                            attribute="mas.call.id",
                            message=f"Duplicate mas.call.id '{call_id}' within trace",
                        )
                    )
                call_ids_by_trace[trace_id].add(str(call_id))

        for span in spans:
            parent_id = span.get("parent_id") or ""
            if not parent_id or parent_id in null_parent:
                continue
            name = span.get("name", "")
            trace_id = self._trace_id(span)
            span_id = self._span_id(span)
            parent = span_by_id.get(parent_id)
            if parent is None:
                report.add(
                    Violation(
                        level="L3",
                        severity="error",
                        rule="missing_parent_span",
                        span_name=name,
                        trace_id=trace_id,
                        span_id=span_id,
                        message=f"parent_id '{parent_id}' not found in trace",
                    )
                )
                continue
            if self._trace_id(parent) != trace_id:
                report.add(
                    Violation(
                        level="L3",
                        severity="error",
                        rule="parent_trace_mismatch",
                        span_name=name,
                        trace_id=trace_id,
                        span_id=span_id,
                        message="parent span belongs to a different trace_id",
                    )
                )

            parent_call = self._attrs(parent).get("mas.call.id")
            child_parent_call = self._attrs(span).get("mas.call.parent")
            if (
                child_parent_call
                and parent_call
                and str(child_parent_call) != str(parent_call)
            ):
                report.add(
                    Violation(
                        level="L3",
                        severity="warning",
                        rule="call_parent_mismatch",
                        span_name=name,
                        trace_id=trace_id,
                        span_id=span_id,
                        attribute="mas.call.parent",
                        message=(
                            f"mas.call.parent '{child_parent_call}' "
                            f"does not match parent mas.call.id '{parent_call}'"
                        ),
                    )
                )

    def _run_consistency_rules(
        self, spans: List[Dict[str, Any]], report: ValidationReport
    ) -> None:
        span_by_id: Dict[str, Dict[str, Any]] = {
            sid: s for s in spans if (sid := self._span_id(s))
        }
        for rule in self._consistency_rules:
            check = rule.get("check")
            level = rule.get("level", "L3")
            severity = rule.get("severity", "warning")

            if check == "group_unique":
                group_by = rule["group_by"]
                unique_field = rule.get("unique_field", "trace_id")
                groups: Dict[str, Set[str]] = {}
                for span in spans:
                    key = self._attrs(span).get(group_by, "")
                    if not key:
                        continue
                    val = span.get(unique_field) or self._trace_id(span)
                    groups.setdefault(key, set()).add(val)
                for key, vals in groups.items():
                    if len(vals) > 1:
                        report.add(
                            Violation(
                                level=level,
                                severity=severity,
                                rule=rule["name"],
                                message=(
                                    f"{rule['description']} "
                                    f"(group '{key}' has {len(vals)} "
                                    f"distinct '{unique_field}' values)"
                                ),
                            )
                        )

            elif check == "has_ancestor_named":
                target_name = rule.get("target_span_name")
                ancestor_names = set(rule.get("ancestor_names", []))
                for span in spans:
                    if span.get("name") != target_name:
                        continue
                    if not self._has_ancestor(span, ancestor_names, span_by_id):
                        report.add(
                            Violation(
                                level=level,
                                severity=severity,
                                rule=rule["name"],
                                span_name=span.get("name", ""),
                                trace_id=self._trace_id(span),
                                span_id=self._span_id(span),
                                message=rule["description"],
                            )
                        )

            elif check == "span_present":
                # A span whose name matches (exact or suffix) must exist per
                # trace.  Used to make the ``<app>.graph`` topology span mandatory.
                suffix = rule.get("span_name_suffix")
                exact = rule.get("span_name")
                traces = {self._trace_id(s) for s in spans if self._trace_id(s)} or {""}
                for trace_id in traces:
                    trace_spans = (
                        [s for s in spans if self._trace_id(s) == trace_id]
                        if trace_id
                        else spans
                    )
                    present = any(
                        (suffix and str(s.get("name", "")).endswith(suffix))
                        or (exact and s.get("name") == exact)
                        for s in trace_spans
                    )
                    if not present:
                        report.add(
                            Violation(
                                level=level,
                                severity=severity,
                                rule=rule["name"],
                                trace_id=trace_id,
                                message=rule["description"],
                            )
                        )

    @staticmethod
    def _has_ancestor(
        span: Dict[str, Any],
        names: Set[str],
        span_by_id: Dict[str, Dict[str, Any]],
        depth: int = 0,
    ) -> bool:
        if depth > 32:
            return False
        parent_id = span.get("parent_id") or ""
        if not parent_id or parent_id in ("None", "0x0000000000000000"):
            return False
        parent = span_by_id.get(parent_id)
        if parent is None:
            return False
        if parent.get("name") in names:
            return True
        return SpanValidator._has_ancestor(parent, names, span_by_id, depth + 1)


# ── Loading helpers ──────────────────────────────────────────────────────────


def load_spans(path: Path | str) -> List[Dict[str, Any]]:
    """Load OTel SDK spans from a JSONL file (silently skips bad lines)."""
    spans: List[Dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            spans.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return spans


def get_trace_id(span: Dict[str, Any]) -> str:
    ctx = span.get("context") or {}
    return str(ctx.get("trace_id") or span.get("trace_id") or "")


def get_attrs(span: Dict[str, Any]) -> Dict[str, Any]:
    attrs = span.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def get_name(span: Dict[str, Any]) -> str:
    return str(span.get("name") or "")


__all__ = [
    "SpanValidator",
    "ValidationReport",
    "Violation",
    "StrictnessMode",
    "load_spans",
    "get_trace_id",
    "get_attrs",
    "get_name",
    "EVENT_INTERNAL_FIELDS",
]
