#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone step: KG structural + SHACL validation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mas.library.kg.artifact import KGArtifact

logger = logging.getLogger(__name__)


class KGValidationError(RuntimeError):
    """Raised by :func:`run_validate_kg` whenever ``error_count`` is nonzero.

    There is deliberately no way to opt out of this (no ``fail_on_error``
    toggle) -- a validation function that can be told to not fail on real
    errors is how a broken "shacl" check silently produced clean-looking
    reports for months (see core/verifier.py's ``KGCheckSkipped``). Callers
    that need the full report even when validation fails (CLIs that print
    it before exiting, pipeline steps that persist it to disk) should catch
    this exception and read its ``report`` attribute -- see
    ``lab_cli``/``bench`` for the pattern.
    """

    def __init__(self, report: Dict[str, Any]) -> None:
        self.report = report
        super().__init__(
            f"KG validation failed: {report['error_count']} error(s), "
            f"{report['warning_count']} warning(s), "
            f"{report.get('skipped_count', 0)} check(s) skipped"
        )

STRUCTURAL_CHECK_NAMES = [
    "unknown_node_types",
    "unknown_edge_types",
    "block_vocabulary",
    "layer_vocabulary",
    "orphaned_edges",
]
ONTOLOGY_CHECK_NAMES = ["shacl"]
DEFAULT_CHECK_NAMES = STRUCTURAL_CHECK_NAMES + ONTOLOGY_CHECK_NAMES
ALL_CHECK_NAMES = tuple(DEFAULT_CHECK_NAMES)
EXTENSION_CHECK_NAMES = ("experiment_extension",)
ALL_EXTENDED_CHECK_NAMES = tuple(DEFAULT_CHECK_NAMES) + EXTENSION_CHECK_NAMES


def _as_artifact(inp: "KGArtifact | str | Path") -> KGArtifact:
    if isinstance(inp, KGArtifact):
        return inp
    return KGArtifact.from_file(inp)


def list_available_checks() -> List[Dict[str, Any]]:
    """Return CLI-facing metadata for KG validation checks."""
    descriptions = {
        "unknown_node_types": "All node_type values must be declared in the ontology vocabulary.",
        "unknown_edge_types": "All edge_type values must be declared in the ontology vocabulary.",
        "block_vocabulary": (
            "Node block values must use the allowed structural/execution/trajectory vocabulary."
        ),
        "layer_vocabulary": "Only State and Transition nodes may carry a layer value.",
        "orphaned_edges": "All edges must reference existing endpoint node IDs in the current KG.",
        "experiment_extension": (
            "Validate optional experiment/scenario/testItem/runLabel extension on Session."
        ),
        "shacl": "Run SHACL validation using canonical ontology and SHACL shapes.",
    }
    return [
        {
            "name": name,
            "default": name in DEFAULT_CHECK_NAMES,
            "category": "ontology"
            if name in ONTOLOGY_CHECK_NAMES
            else ("extension" if name in EXTENSION_CHECK_NAMES else "structural"),
            "description": descriptions[name],
        }
        for name in ALL_EXTENDED_CHECK_NAMES
    ]


def run_validate_kg(
    artifact: Union[KGArtifact, str, Path],
    *,
    ontology_path: Optional[str] = None,
    strict: bool = False,
    checks: Optional[List[str]] = None,
    extension_layers: Optional[List[str]] = None,
    warning_verbosity: str = "full",
) -> Dict[str, Any]:
    """Validate a KG against structural invariants.

    Always raises :class:`KGValidationError` when ``error_count`` is nonzero
    -- there is no ``fail_on_error`` switch to opt out of that. Callers that
    need the report even on failure (to print it, persist it, or decide
    per-pipeline whether to keep going) should catch
    :class:`KGValidationError` and read its ``report`` attribute.

    Args:
        artifact: The KG to validate.  Accepts a :class:`KGArtifact`, a path
            string, or a :class:`~pathlib.Path` to a ``kg.jsonld`` file.
        ontology_path: Optional path to ``mas-ontology.ttl``.  If omitted,
            resolved via ``scope_ontology``.
        strict: Treat recommended-attribute gaps as errors.
        checks: Optional subset of check names to run.  If *None*, all checks
            run. Available: ``unknown_node_types``, ``unknown_edge_types``,
            ``block_vocabulary``, ``layer_vocabulary``, ``shacl``.
        extension_layers: Optional extension-layer checks to enable.
            Supported: ``["experiment"]`` enables ``experiment_extension``.
        warning_verbosity: Controls warning rows in ``results`` while keeping
            warning counts intact. Allowed values:
            - ``"full"``: one row per warning (default)
            - ``"summary"``: one aggregated warning row per check
            - ``"none"``: no warning rows

    Returns:
        Dict with keys:
            ``error_count``   — total error count
            ``warning_count`` — total warning count
            ``skipped_count`` — checks that could not run in this environment
                (e.g. "shacl" without pyshacl/rdflib installed) -- distinct
                from a pass, and never silently folded into one
            ``results``       — list of ``{"check": str, "status": str, "detail": str}``,
                ``status`` one of ``"pass"``, ``"warning"``, ``"error"``, ``"skipped"``

    Raises:
        KGValidationError: if ``error_count`` is nonzero. Its ``.report``
            attribute holds the full report dict described above. A skipped
            check does not by itself raise -- it is a distinct, visible
            outcome, not an error -- but CI must install the ``[validation]``
            extra so "shacl" is never legitimately skipped there.
    """
    from mas.library.kg.core.verifier import (
        KGCheckSkipped,
        check_block_vocabulary,
        check_experiment_extension,
        check_layer_vocabulary,
        check_orphaned_edges,
        check_unknown_edge_types,
        check_unknown_node_types,
        run_shacl_validation,
    )

    kg = _as_artifact(artifact)
    kg.to_doc()
    nodes: List[Dict[str, Any]] = kg.nodes
    edges: List[Dict[str, Any]] = kg.edges

    results: List[Dict[str, Any]] = []
    error_count = 0
    warning_count = 0
    skipped_count = 0
    if warning_verbosity not in {"full", "summary", "none"}:
        raise ValueError("warning_verbosity must be one of: 'full', 'summary', 'none'")
    # Resolved lazily, inside the per-check try/except below, only when the
    # "shacl" check actually runs -- it's the only check that needs it. A
    # missing scope_ontology/rdflib must not crash every OTHER check (and
    # must still respect strict), which an eager resolution here would have
    # bypassed entirely.

    _ALL_CHECKS = {
        "unknown_node_types":   lambda: check_unknown_node_types(nodes, edges),
        "unknown_edge_types":   lambda: check_unknown_edge_types(nodes, edges),
        "block_vocabulary":     lambda: check_block_vocabulary(nodes, edges),
        "layer_vocabulary":     lambda: check_layer_vocabulary(nodes, edges),
        "orphaned_edges":       lambda: check_orphaned_edges(nodes, edges),
        "experiment_extension": lambda: check_experiment_extension(nodes, edges),
    }

    enabled_layers = {
        str(layer).strip().lower() for layer in (extension_layers or []) if str(layer).strip()
    }
    run_checks = checks if checks else list(DEFAULT_CHECK_NAMES)
    if "experiment" in enabled_layers and "experiment_extension" not in run_checks:
        run_checks.append("experiment_extension")

    for name in run_checks:
        if name not in _ALL_CHECKS and name != "shacl":
            # An unrecognized check name is a validation-config mistake (a
            # typo in `checks:`, most likely) -- surface it as an error
            # rather than silently skipping, so it doesn't quietly do
            # nothing forever.
            error_count += 1
            results.append(
                {"check": name, "status": "error", "detail": f"Unknown check: {name!r}"}
            )
            logger.warning("Unknown check %r — recorded as an error", name)
            continue
        try:
            if name == "shacl":
                from mas.library.kg.ontology import resolve_mas_ontology_path

                try:
                    resolved_ontology_path = resolve_mas_ontology_path(ontology_path)
                except ImportError:
                    # scope_ontology not installed. run_shacl_validation
                    # already degrades gracefully on its own (its own
                    # pyshacl/rdflib import check) and resolves SHACL shapes
                    # independently of this value -- it's only a fallback
                    # hint if that independent resolution comes up empty, so
                    # a missing package here means "no hint available", not
                    # "cannot run the check".
                    resolved_ontology_path = None

                raw_result = run_shacl_validation(
                    nodes,
                    edges,
                    resolved_ontology_path,
                    kg.run_id() or "kg-validate",
                    strict=strict,
                )
            else:
                raw_result = _ALL_CHECKS[name]()

            # Structural verifier checks return ``(passed, violations)`` while
            # SHACL may return a summary dict or a ``(passed, violations, warnings)``
            # tuple for detailed result rows.
            passed: Optional[bool] = None
            violations: Any = raw_result
            warnings: List[Any] = []
            if (
                isinstance(raw_result, tuple)
                and len(raw_result) == 2
                and isinstance(raw_result[0], bool)
            ):
                passed, violations = raw_result
            elif (
                isinstance(raw_result, tuple)
                and len(raw_result) == 3
                and isinstance(raw_result[0], bool)
            ):
                passed, violations, warnings = raw_result
            elif isinstance(raw_result, dict) and {"passed", "conforms"} & set(raw_result):
                passed = bool(raw_result.get("passed", raw_result.get("conforms", False)))
                if passed:
                    violations = []
                else:
                    violations = [raw_result.get("report") or "SHACL validation failed"]

            if violations is None:
                violation_items: List[Any] = []
            elif isinstance(violations, list):
                violation_items = violations
            else:
                violation_items = [violations]

            warning_items = (
                warnings if isinstance(warnings, list) else ([warnings] if warnings else [])
            )

            if passed is True and not violation_items:
                results.append({"check": name, "status": "pass", "detail": ""})
            elif not violation_items:
                results.append({"check": name, "status": "pass", "detail": ""})
            else:
                for v in violation_items:
                    sev = getattr(v, "severity", "error")
                    if sev == "error":
                        error_count += 1
                    else:
                        warning_count += 1
                    results.append(
                        {
                            "check": name,
                            "status": sev,
                            "detail": str(getattr(v, "message", v)),
                        }
                    )

            for w in warning_items:
                warning_count += 1
            if warning_items and warning_verbosity == "full":
                for w in warning_items:
                    results.append(
                        {
                            "check": name,
                            "status": "warning",
                            "detail": str(getattr(w, "message", w)),
                        }
                    )
            elif warning_items and warning_verbosity == "summary":
                results.append(
                    {
                        "check": name,
                        "status": "warning",
                        "detail": (
                            f"{len(warning_items)} warning(s) suppressed; "
                            "rerun with warning_verbosity='full' for details."
                        ),
                    }
                )
        except KGCheckSkipped as exc:
            skipped_count += 1
            results.append({"check": name, "status": "skipped", "detail": str(exc)})
            logger.warning("Check %r skipped: %s", name, exc)
        except Exception as exc:
            error_count += 1
            results.append({"check": name, "status": "error", "detail": str(exc)})
            logger.exception("Check %r raised:", name)

    report = {
        "error_count": error_count,
        "warning_count": warning_count,
        "skipped_count": skipped_count,
        "results": results,
    }
    if error_count:
        raise KGValidationError(report)
    return report
