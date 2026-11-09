#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Filesystem-ref walk skips output sinks and unresolved path templates."""

from mas.ctl.validate.refs import is_path_ref, iter_ref_paths


def test_template_path_is_not_a_filesystem_ref() -> None:
    assert is_path_ref("path", "{output_dir}/fig_trajectory.svg") is False
    assert is_path_ref("path", "overlays/with-guardrail.yaml") is True


def test_artifact_path_templates_are_not_collected() -> None:
    refs = dict(
        iter_ref_paths(
            {
                "application": {
                    "artifacts": {
                        "fig_trajectory": {
                            "type": "plot",
                            "path": "{output_dir}/fig_trajectory.svg",
                        }
                    }
                },
                "mas": {"manifest": "mas.yaml"},
            }
        )
    )
    assert "application.artifacts.fig_trajectory.path" not in refs
    assert refs.get("mas.manifest") == "mas.yaml"


def test_telemetry_path_is_still_an_output_sink() -> None:
    refs = dict(iter_ref_paths({"spec": {"telemetry": {"path": "events.jsonl"}}}))
    assert "spec.telemetry.path" not in refs


def test_checkpoint_storage_path_is_a_runtime_directory_not_an_input_ref() -> None:
    refs = dict(
        iter_ref_paths({"spec": {"checkpoint": {"storage": {"kind": "hybrid", "path": ".mas/t06-checkpoints"}}}})
    )
    assert "spec.checkpoint.storage.path" not in refs


def test_native_observability_path_is_an_output_sink() -> None:
    refs = dict(iter_ref_paths({"spec": {"observability": [{"native": {"path": "traces/events.jsonl"}}]}}))
    assert "spec.observability[0].native.path" not in refs
