"""Validate native events.jsonl against events.schema.json + events.spec.yaml.

Mirrors ``mas.library.telemetry.verification.spanspec.SpanValidator`` for OTel spans.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from mas.library.kg._dist import extra_hint

StrictnessMode = Literal["required", "recommended", "complete"]

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]

from mas.library.kg.core.event_mappings import KIND_TO_CLASS  # noqa: E402

_INTERVAL_SUFFIX = re.compile(r"_(start|end)$")


def _find_schema(name: str) -> Path:
    """Resolve bundled schema files (dev tree or wheel force-include)."""
    for parent in Path(__file__).resolve().parents:
        path = parent / "schemas" / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"schema not found: {name}")


_BUILTIN_SPEC = _find_schema("events.spec.yaml")
_BUILTIN_JSON_SCHEMA = _find_schema("events.schema.json")


@dataclass
class EventViolation:
    level: str
    severity: str  # error | warning | info
    rule: str
    message: str
    kind: str = ""
    line: int = 0
    field: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class EventValidationReport:
    spec_file: str
    total_events: int = 0
    known_kinds: Set[str] = field(default_factory=set)
    unknown_kinds: Set[str] = field(default_factory=set)
    legacy_kinds: Set[str] = field(default_factory=set)
    _violations: List[EventViolation] = field(default_factory=list, repr=False)

    def add(self, v: EventViolation) -> None:
        self._violations.append(v)

    @property
    def violations(self) -> List[EventViolation]:
        return list(self._violations)

    def errors(self, level: Optional[str] = None) -> List[EventViolation]:
        vs = [v for v in self._violations if v.severity == "error"]
        return [v for v in vs if v.level == level] if level else vs

    def warnings(self, level: Optional[str] = None) -> List[EventViolation]:
        vs = [v for v in self._violations if v.severity == "warning"]
        return [v for v in vs if v.level == level] if level else vs

    def conformance(self, level: str) -> str:
        if self.errors(level):
            return "failing"
        if self.warnings(level):
            return "passing"
        return "full"

    def summary(self) -> Dict[str, Any]:
        levels = ["L1", "L2", "L3"]
        return {
            "spec_file": self.spec_file,
            "total_events": self.total_events,
            "known_kinds": sorted(self.known_kinds),
            "unknown_kinds": sorted(self.unknown_kinds),
            "legacy_kinds": sorted(self.legacy_kinds),
            "conformance": {L: self.conformance(L) for L in levels},
            "counts": {
                "errors": len(self.errors()),
                "warnings": len(self.warnings()),
            },
            "violations": [v.to_dict() for v in self._violations],
        }


class EventValidator:
    """Validate events against events.spec.yaml semantic constraints."""

    def __init__(self, spec_path: Optional[str | Path] = None) -> None:
        path = Path(spec_path) if spec_path else _BUILTIN_SPEC
        if _yaml is None:
            raise ImportError("PyYAML required for EventValidator")
        self.spec_file = str(path)
        self._spec = _yaml.safe_load(path.read_text(encoding="utf-8"))
        self._legacy = set(self._spec.get("legacy_kinds", {}).get("kinds", []))
        self._shapes = self._spec.get("shapes", [])
        self._global = self._spec.get("global_attributes", [])
        self._interval = self._spec.get("interval_kinds", {})

    def validate(
        self,
        events: List[Dict[str, Any]],
        *,
        strictness: StrictnessMode = "required",
        line_offset: int = 1,
    ) -> EventValidationReport:
        report = EventValidationReport(spec_file=self.spec_file, total_events=len(events))
        for i, ev in enumerate(events):
            lineno = i + line_offset
            kind = str(ev.get("kind", ""))
            if not kind:
                report.add(
                    EventViolation(
                        level="L1",
                        severity="error",
                        rule="kind_present",
                        message="missing kind",
                        line=lineno,
                    )
                )
                continue

            if kind in self._legacy or KIND_TO_CLASS.get(kind) is None:
                report.legacy_kinds.add(kind)
                continue

            if kind not in KIND_TO_CLASS:
                report.unknown_kinds.add(kind)
                report.add(
                    EventViolation(
                        level="L1",
                        severity="warning",
                        rule="known_kind",
                        message=f"unknown kind '{kind}'",
                        kind=kind,
                        line=lineno,
                    )
                )
            else:
                report.known_kinds.add(kind)

            self._check_globals(ev, report, lineno, strictness)
            self._check_interval(ev, report, lineno, strictness)
            self._check_shape(ev, report, lineno, strictness)

        return report

    def _check_globals(
        self,
        ev: Dict[str, Any],
        report: EventValidationReport,
        lineno: int,
        strictness: StrictnessMode,
    ) -> None:
        kind = str(ev.get("kind", ""))
        for block in self._global:
            level = block.get("level", "L1")
            for req_field in block.get("required", []):
                if not _has_field(ev, req_field):
                    report.add(
                        EventViolation(
                            level=level,
                            severity="error",
                            rule="global_required",
                            message=f"missing required field '{req_field}'",
                            kind=kind,
                            line=lineno,
                            field=req_field,
                        )
                    )
            if strictness in ("recommended", "complete"):
                for rec_field in block.get("recommended", []):
                    if not _has_field(ev, rec_field):
                        report.add(
                            EventViolation(
                                level=level,
                                severity="warning",
                                rule="global_recommended",
                                message=f"missing recommended field '{rec_field}'",
                                kind=kind,
                                line=lineno,
                                field=rec_field,
                            )
                        )

    def _check_interval(
        self,
        ev: Dict[str, Any],
        report: EventValidationReport,
        lineno: int,
        strictness: StrictnessMode,
    ) -> None:
        kind = str(ev.get("kind", ""))
        if not _INTERVAL_SUFFIX.search(kind):
            return
        level = "L2"
        for req_field in self._interval.get("required", []):
            if not _has_field(ev, req_field):
                report.add(
                    EventViolation(
                        level=level,
                        severity="error",
                        rule="interval_required",
                        message=f"interval kind requires '{req_field}'",
                        kind=kind,
                        line=lineno,
                        field=req_field,
                    )
                )
        if strictness in ("recommended", "complete"):
            for rec_field in self._interval.get("recommended", []):
                if not _has_field(ev, rec_field):
                    report.add(
                        EventViolation(
                            level=level,
                            severity="warning",
                            rule="interval_recommended",
                            message=f"interval kind recommends '{rec_field}'",
                            kind=kind,
                            line=lineno,
                            field=rec_field,
                        )
                    )

    def _check_shape(
        self,
        ev: Dict[str, Any],
        report: EventValidationReport,
        lineno: int,
        strictness: StrictnessMode,
    ) -> None:
        kind = str(ev.get("kind", ""))
        shape = _match_shape(kind, self._shapes)
        if shape is None:
            return
        for block in shape.get("levels", []):
            level = block.get("level", "L3")
            for req_field in block.get("required", []):
                if not _has_field(ev, req_field):
                    report.add(
                        EventViolation(
                            level=level,
                            severity="error",
                            rule="shape_required",
                            message=f"kind '{kind}' requires '{req_field}'",
                            kind=kind,
                            line=lineno,
                            field=req_field,
                        )
                    )
            if strictness in ("recommended", "complete"):
                for rec_field in block.get("recommended", []):
                    if not _has_field(ev, rec_field):
                        report.add(
                            EventViolation(
                                level=level,
                                severity="warning",
                                rule="shape_recommended",
                                message=f"kind '{kind}' recommends '{rec_field}'",
                                kind=kind,
                                line=lineno,
                                field=rec_field,
                            )
                        )


def _has_field(ev: Dict[str, Any], name: str) -> bool:
    val = ev.get(name)
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    return True


def _match_shape(kind: str, shapes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for shape in shapes:
        kinds = shape.get("kinds") or []
        if kind in kinds:
            return shape
        for prefix in shape.get("kind_prefixes") or []:
            if kind.startswith(prefix):
                return shape
    return None


class JsonSchemaUnavailable(Exception):
    """jsonschema isn't installed; the JSON-Schema check could not run.

    Distinct from "ran and found nothing": returning [] here (as this used
    to) is indistinguishable from a clean pass to any caller that just
    checks truthiness, which is exactly how the analogous pyshacl-missing
    case in mas.library.kg.core.verifier went unnoticed (see
    KGCheckSkipped there for the full story).
    """


def validate_event_json_schema(
    event: Dict[str, Any],
    schema: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Validate one event dict against events.schema.json.

    Raises:
        JsonSchemaUnavailable: if jsonschema is not installed.
    """
    try:
        import jsonschema
    except ImportError as exc:
        raise JsonSchemaUnavailable(
            f"jsonschema not installed ({extra_hint('verify')})"
        ) from exc

    if schema is None:
        if not _BUILTIN_JSON_SCHEMA.exists():
            return [f"json_schema_missing: {_BUILTIN_JSON_SCHEMA}"]
        schema = json.loads(_BUILTIN_JSON_SCHEMA.read_text(encoding="utf-8"))

    errors: List[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(event), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{path}: {err.message}")
    return errors


def validate_events_json_schema(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate all events against events.schema.json.

    ``ok`` is ``None`` (not ``True``) when ``skipped`` is set -- the check
    never ran, so it must never look like a clean pass.
    """
    per_line: List[Dict[str, Any]] = []
    total_errors = 0
    try:
        for i, ev in enumerate(events):
            errs = validate_event_json_schema(ev)
            if errs:
                per_line.append(
                    {
                        "index": i,
                        "kind": ev.get("kind", ""),
                        "errors": errs,
                    }
                )
                total_errors += len(errs)
    except JsonSchemaUnavailable as exc:
        return {
            "ok": None,
            "skipped": True,
            "reason": str(exc),
            "schema_file": str(_BUILTIN_JSON_SCHEMA),
            "total_events": len(events),
            "error_count": 0,
            "event_errors": [],
        }
    return {
        "ok": total_errors == 0,
        "skipped": False,
        "schema_file": str(_BUILTIN_JSON_SCHEMA),
        "total_events": len(events),
        "error_count": total_errors,
        "event_errors": per_line[:20],
    }


def load_events(path: Path | str) -> List[Dict[str, Any]]:
    """Load native events from a JSONL file."""
    events: List[Dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def check_interval_pairing(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Warn when *_start and *_end share call_id but kinds mismatch."""
    by_call: Dict[str, List[Dict[str, Any]]] = {}
    warnings: List[str] = []
    for i, ev in enumerate(events):
        cid = ev.get("call_id")
        kind = ev.get("kind", "")
        if not cid or not _INTERVAL_SUFFIX.search(str(kind)):
            continue
        by_call.setdefault(str(cid), []).append({**ev, "_line": i})

    for cid, group in by_call.items():
        kinds = {str(g.get("kind", "")) for g in group}
        bases = {k.rsplit("_", 1)[0] for k in kinds if _INTERVAL_SUFFIX.search(k)}
        if len(bases) > 1:
            warnings.append(f"call_id {cid}: mixed interval bases {sorted(bases)}")
        starts = [g for g in group if str(g.get("kind", "")).endswith("_start")]
        ends = [g for g in group if str(g.get("kind", "")).endswith("_end")]
        if starts and not ends:
            warnings.append(f"call_id {cid}: start without matching end")
        if ends and not starts:
            warnings.append(f"call_id {cid}: end without matching start")

    return {"ok": len(warnings) == 0, "warnings": warnings}
