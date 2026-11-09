#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared CLI observability overrides.

``--events*`` is a shortcut on the agent or MAS, not a Flavour overlay.
``--no-events`` and ``--events-format`` ``native`` / ``otel`` / ``both``
replace the plugin set. ``--events-stdout`` and ``--events-file`` add native
when it is not selected; they do not replace a plugin. ``bind.events`` adds a
sink and keeps native unless a Control named the observability list (including
``[]``) or leftover agent observability named plugins.
"""

from __future__ import annotations

from collections.abc import Callable

import click
from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.session.manifest_config import observability_config_from_manifest


def observability_options(fn: Callable) -> Callable:
    """Optional CLI overrides for manifest-declared observability plugins."""
    fn = click.option(
        "--events/--no-events",
        default=None,
        help="Turn recording on or off. --no-events turns it off. --events does not replace the plugin set.",
    )(fn)
    fn = click.option(
        "--events-file",
        default=None,
        type=click.Path(),
        help="Write the native JSONL log here. Adds native when it is not in the plugin set; never replaces a plugin.",
    )(fn)
    fn = click.option(
        "--events-stderr",
        "--events-stdout",
        "events_stdout",
        is_flag=True,
        default=False,
        help="Also stream native JSONL on stderr (keeps stdout clean for the answer). "
        "Adds native when it is not in the plugin set; never replaces a plugin. --events-stdout is a legacy alias.",
    )(fn)
    fn = click.option(
        "--events-format",
        default=None,
        type=click.Choice(["native", "boundary", "both", "otel"], case_sensitive=False),
        help="native, otel, and both replace the plugin set. boundary only sets the transform chain.",
    )(fn)
    return fn


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
    resolved_infra: ResolvedInfra | None = None,
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> ObservabilityConfig:
    """Resolve the effective observability config.

    ``--events*`` is a shortcut on the agent or MAS. Leftover Flavour
    ``observability`` is unread. ``bind.events`` rows add the plugins they name
    and keep native unless a Control named the observability list (including
    ``[]``) or leftover agent observability named plugins.
    ``--events-file`` and ``--events-stdout`` add native when it is not selected.
    """
    return observability_config_from_manifest(
        manifest,
        deployment=deployment,
        agent_id=agent_id,
        mas_id=mas_id,
        cli_events=events,
        cli_events_file=events_file,
        cli_events_stdout=events_stdout,
        cli_events_format=events_format,
        flavour_spec=flavour_spec,
        resolved_infra=resolved_infra,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
    )
