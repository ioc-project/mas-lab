#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared CLI observability overrides.

Precedence: agent/MAS manifest ``spec.observability`` (per-agent authored) >
active flavour ``spec.observability`` (deployment-posture default) > these
CLI flags, which override whichever of the above resolved.
"""

from __future__ import annotations

from typing import Any, Callable

import click

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.session.manifest_config import observability_config_from_manifest


def observability_options(fn: Callable) -> Callable:
    """Optional CLI overrides for manifest-declared observability plugins."""
    fn = click.option(
        "--events/--no-events",
        default=None,
        help="Override manifest observability (on/off)",
    )(fn)
    fn = click.option(
        "--events-file",
        default=None,
        type=click.Path(),
        help="Override JSONL path (default from manifest or traces/events.jsonl)",
    )(fn)
    fn = click.option(
        "--events-stderr",
        "--events-stdout",
        "events_stdout",
        is_flag=True,
        default=False,
        help="Override: also stream transformed events as JSONL on stderr "
        "(keeps stdout clean for the answer). --events-stdout is a legacy alias.",
    )(fn)
    fn = click.option(
        "--events-format",
        default=None,
        type=click.Choice(["native", "boundary", "both", "otel"], case_sensitive=False),
        help="Override transform chain",
    )(fn)
    return fn


def build_observability_flavour_overlay(
    flavour_spec: dict | None,
    *,
    events: bool | None,
    events_file: str | None,
) -> dict | None:
    """Express ``--events``/``--events-file`` as a ``mas/v1`` Overlay patch on the
    active flavour's ``observability`` plugin list — FT7
    (docs/design/flavour-boundary.md): one mechanism for the trace-file
    location instead of two (plugin config vs a CLI-only field).

    Returns ``None`` when neither flag was explicitly set — the flavour's own
    ``observability`` declaration is left unpatched. ``--events-stdout`` and
    ``--events-format=boundary`` have no representation in
    ``observability-binding.schema.yaml`` (a stdout toggle and a plugin-internal
    transform mode aren't "which plugins run") and stay pure runtime overrides
    on :class:`ObservabilityConfig` — see the design doc's "scope what
    overlays can express" section.
    """
    if events is None and events_file is None:
        return None

    from mas.ctl.manifest.spec_bindings import parse_observability

    base_plugins = list(parse_observability((flavour_spec or {}).get("observability")).plugins)

    if events is False:
        plugins: list[str] = []
    else:
        plugins = list(base_plugins) or ["native"]
        if events_file and "native" not in plugins:
            plugins.append("native")

    entries: list[Any] = [
        {"native": {"path": events_file}} if (name == "native" and events_file) else name
        for name in plugins
    ]

    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "cli-observability-overlay"},
        "spec": {
            "target": {"kind": "Flavour"},
            "patch": {"observability": entries},
        },
    }


def resolve_observability_config(
    *,
    events: bool | None,
    events_file: str | None,
    events_stdout: bool,
    events_format: str | None,
    agent_id: str = "",
    mas_id: str = "",
    manifest: dict | None = None,
    deployment: dict | None = None,
    flavour_spec: dict | None = None,
) -> ObservabilityConfig:
    """Resolve the effective observability config.

    ``events``/``events_file`` are first folded into ``flavour_spec`` via
    :func:`build_observability_flavour_overlay` + ``merge_flavour_overlay``,
    so the flavour-fallback branch of :func:`observability_config_from_manifest`
    sees a flavour spec that already reflects the CLI's choice. They're *also*
    still passed through as ``cli_events``/``cli_events_file`` below —
    preserved for callers (e.g. ``mas.ctl.benchmark.runner.bench_obs_config``)
    that need an unconditional override regardless of what any manifest
    declares, and for the case where the agent/MAS manifest (not the flavour)
    is what declares ``spec.observability``, where the overlay-patched
    flavour spec is correctly skipped by that function's existing
    manifest-first precedence.
    """
    effective_flavour_spec = flavour_spec
    cli_overlay = build_observability_flavour_overlay(flavour_spec, events=events, events_file=events_file)
    if cli_overlay is not None:
        from mas.ctl.overlay.merge import merge_overlay

        effective_flavour_spec = (
            merge_overlay({"kind": "Flavour", "spec": flavour_spec or {}}, cli_overlay).get("spec") or {}
        )

    return observability_config_from_manifest(
        manifest,
        deployment=deployment,
        agent_id=agent_id,
        mas_id=mas_id,
        cli_events=events,
        cli_events_file=events_file,
        cli_events_stdout=events_stdout,
        cli_events_format=events_format,
        flavour_spec=effective_flavour_spec,
    )
