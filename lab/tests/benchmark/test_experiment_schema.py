#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""experiment.yaml schema: canonical keys pass, removed keys fail, deprecated keys warn.

Note: ``mas:`` is intentionally NOT included among the removed-key cases below.
It is a soft-deprecated alias of ``application:`` for one more release (see
``docs/schemas/lab/experiment.schema.yaml``'s ``mas:`` def — ``deprecated: true``
but not schema-rejected — and ``lab.deprecations.NOTICES["experiment.mas"]``).
Its warn-but-validate behavior is covered by
``test_deprecations.py::test_legacy_mas_key_warns``.
"""

from __future__ import annotations

import pytest
import yaml

from mas.ctl.validate.deprecations import collect_experiment_deprecations
from mas.ctl.validate.schemas import load_schema
from mas.lab.manifests.validator import ManifestValidationError, validate_manifest


def _canonical() -> dict:
    return yaml.safe_load(
        """
experiment:
  name: canonical
  application:
    app: trip-planner
    configs_dir: ./overlays
  scenarios:
    - id: baseline
  dataset:
    name: queries
  run:
    n_runs: 1
  item:
    post:
      - name: gather-item
        type: gather_level
  post:
    - name: gather-experiment
      type: gather_level
"""
    )


def test_canonical_experiment_validates_without_deprecations() -> None:
    warnings = validate_manifest(
        _canonical(),
        source="experiment.yaml",
        kind="experiment",
        strict=True,
        resolve_refs=False,
    )
    assert warnings == []


@pytest.mark.parametrize(
    "removed_key,payload",
    [
        ("pipeline_bind", "run"),
        ("pipeline", []),
        ("plots", {}),
        ("flavours", []),
        ("output_dir", "./out"),
    ],
)
def test_removed_experiment_keys_fail_schema(removed_key: str, payload) -> None:
    data = _canonical()
    data["experiment"][removed_key] = payload
    with pytest.raises(ManifestValidationError, match=removed_key) as exc:
        validate_manifest(
            data,
            source="experiment.yaml",
            kind="experiment",
            strict=True,
            resolve_refs=False,
        )
    joined = " ".join(exc.value.violations)
    assert "not a valid property" in joined or "removed" in joined


def test_deprecated_applications_and_test_warn_but_validate() -> None:
    data = _canonical()
    exp = data["experiment"]
    exp["applications"] = [exp.pop("application")]
    exp["test"] = exp.pop("item")
    warnings = validate_manifest(
        data,
        source="experiment.yaml",
        kind="experiment",
        strict=True,
        resolve_refs=False,
    )
    joined = " ".join(warnings)
    assert "applications" in joined
    assert "test" in joined


def test_application_as_pipeline_level_is_spotted() -> None:
    data = _canonical()
    data["experiment"]["application"] = {
        "post": [{"name": "gather-experiment", "type": "gather_level"}]
    }
    schema = load_schema("experiment")
    warnings = collect_experiment_deprecations(data, schema)
    assert any("pipeline level" in msg for msg in warnings)


def test_dataset_path_without_name_validates() -> None:
    data = _canonical()
    data["experiment"]["dataset"] = {"path": "./datasets/queries.yaml"}
    warnings = validate_manifest(
        data,
        source="experiment.yaml",
        kind="experiment",
        strict=True,
        resolve_refs=False,
    )
    assert warnings == []


def test_former_and_canonical_dataset_items_validate() -> None:
    pytest.importorskip("jsonschema")
    former = {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": "sre-triage-incidents"},
        "spec": {
            "app": "sre-triage@^v2",
            "items": [
                {
                    "id": "routing-policy-rollback",
                    "prompt": "Triage the edge-gateway regression.",
                    "expectations": {
                        "correct_action": {
                            "service": "edge-gateway",
                            "action": "rollback",
                        }
                    },
                }
            ],
        },
    }
    modern = {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": "sre-triage-incidents"},
        "spec": {
            "app": "sre-triage@^v2",
            "items": [
                {
                    "id": "routing-policy-rollback",
                    "inputs": {"user": "Triage the edge-gateway regression."},
                    "expectations": {
                        "details": {
                            "correct_action": {
                                "service": "edge-gateway",
                                "action": "rollback",
                            }
                        }
                    },
                }
            ],
        },
    }
    validate_manifest(former, source="former.yaml", kind="dataset", strict=True, resolve_refs=False)
    warnings = validate_manifest(
        modern, source="modern.yaml", kind="dataset", strict=True, resolve_refs=False
    )
    assert warnings == []


def test_schedule_max_attempts_validates() -> None:
    data = _canonical()
    data["experiment"]["schedule"] = {"max_attempts": 3, "retry_backoff_s": 2.0}
    warnings = validate_manifest(
        data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
    )
    assert warnings == []


def test_execution_max_attempts_zero_fails_schema() -> None:
    data = _canonical()
    data["experiment"]["execution"] = {"max_attempts": 0}
    with pytest.raises(ManifestValidationError, match="minimum of 1"):
        validate_manifest(
            data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )


def test_one_factor_design_validates_on_canonical_and_legacy_execution() -> None:
    design = {
        "mode": "one_factor",
        "pin_scenarios": ["baseline"],
        "vary": {"axis": "item", "values": ["item-1"]},
    }
    canonical = _canonical()
    canonical["experiment"]["design"] = design
    assert (
        validate_manifest(
            canonical, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )
        == []
    )
    legacy = _canonical()
    legacy["experiment"]["execution"] = {"design": design}
    warnings = validate_manifest(
        legacy, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
    )
    assert any("experiment.execution" in msg for msg in warnings)


def test_one_factor_vary_rejects_unknown_axis() -> None:
    data = _canonical()
    data["experiment"]["design"] = {
        "mode": "one_factor",
        "vary": {"axis": "overlay", "values": ["x"]},
    }
    with pytest.raises(ManifestValidationError, match="overlay"):
        validate_manifest(
            data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )


def test_one_factor_without_vary_fails_schema() -> None:
    data = _canonical()
    data["experiment"]["design"] = {"mode": "one_factor"}
    with pytest.raises(ManifestValidationError, match="vary"):
        validate_manifest(
            data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )


def test_cartesian_rejects_one_factor_keys() -> None:
    data = _canonical()
    data["experiment"]["design"] = {
        "mode": "cartesian",
        "vary": {"axis": "item", "values": ["item-1"]},
    }
    with pytest.raises(ManifestValidationError):
        validate_manifest(
            data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )


def test_dead_pin_key_fails_schema() -> None:
    data = _canonical()
    data["experiment"]["design"] = {"mode": "cartesian", "pin": {"scenarios": ["baseline"]}}
    with pytest.raises(ManifestValidationError, match="pin"):
        validate_manifest(
            data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )


def test_coupled_without_couplings_fails_schema() -> None:
    data = _canonical()
    data["experiment"]["design"] = {"mode": "coupled"}
    with pytest.raises(ManifestValidationError, match="couplings"):
        validate_manifest(
            data, source="experiment.yaml", kind="experiment", strict=True, resolve_refs=False
        )
