#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab kg`` CLI component.

All commands here call ``mas.library.kg`` step functions directly. The library
registers this component via ``mas.lab.cli.components`` so ``mas-lab kg ...``
is the supported CLI surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from mas.library.kg.infra import resolve_neo4j_conn as _resolve_neo4j_conn


def _span_attrs(span: dict[str, Any]) -> dict[str, Any]:
    attrs = span.get("attributes")
    if isinstance(attrs, dict):
        return attrs
    clickhouse_attrs = span.get("SpanAttributes")
    if isinstance(clickhouse_attrs, dict):
        return clickhouse_attrs
    return {}


def _infer_run_id_from_events(events_path: str, session_id: str | None) -> str:
    if session_id:
        return session_id

    path = Path(events_path).expanduser().resolve()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        return str(
            event.get("run_id")
            or event.get("session_id")
            or (event.get("context") or {}).get("session_id")
            or path.stem
        )

    return path.stem


def _infer_run_id_from_otel(spans_path: str, session_id: str | None) -> str:
    if session_id:
        return session_id

    from mas.library.kg.observability.helpers import load_spans

    path = Path(spans_path).expanduser().resolve()
    payload = load_spans(path)
    if isinstance(payload, list) and payload:
        span = payload[0]
        attrs = _span_attrs(span)
        context = span.get("context") if isinstance(span.get("context"), dict) else {}
        return str(
            attrs.get("mas.session.id")
            or attrs.get("session.id")
            or attrs.get("mas.run.id")
            or span.get("session_id")
            or span.get("TraceId")
            or context.get("trace_id")
            or path.stem
        )

    return path.stem


def _emit_artifact(
    artifact: Any, output_path: str | None, output_dir: str | None, dry_run: bool
) -> None:
    if output_path and not dry_run:
        saved = artifact.save(output_path)
        click.echo(
            json.dumps(
                {
                    "output": str(saved),
                    "node_count": artifact.node_count,
                    "edge_count": artifact.edge_count,
                    "session_id": artifact.session_id(),
                    "run_id": artifact.run_id(),
                },
                indent=2,
            )
        )
        return

    if output_dir and not dry_run:
        saved = Path(output_dir).expanduser().resolve() / "kg.jsonld"
        click.echo(json.dumps({
            "output": str(saved),
            "node_count": artifact.node_count,
            "edge_count": artifact.edge_count,
            "session_id": artifact.session_id(),
            "run_id": artifact.run_id(),
        }, indent=2))
        return

    click.echo(json.dumps(artifact.to_doc(), indent=2))


@click.group("kg")
def kg_group() -> None:
    """Knowledge-graph utilities: normalize, validate, push, dump."""


@kg_group.command("normalize")
@click.argument("events_path", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--otel", "otel_path", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--run-id", default=None)
@click.option("--session-id", default=None)
@click.option("--split-session-id/--no-split-session-id", default=True, show_default=True)
@click.option("--app-name", default=None, help="Override Session.appName in normalized output.")
@click.option(
    "--application-node/--no-application-node",
    default=False,
    show_default=True,
    help="Emit an Application node and hasSession edges in normalized output.",
)
@click.option(
    "--fill-synthesized-processing-defaults/--no-fill-synthesized-processing-defaults",
    default=True,
    show_default=True,
    help="Populate recommended fields on synthesized ProcessingCall nodes.",
)
@click.option("--output-dir", "output_dir", default=None)
@click.option("--output", "output_path", "-o", default=None)
@click.option("--ontology-path", default=None)
@click.option("--dry-run", is_flag=True)
@click.option("--include-infrastructure/--no-include-infrastructure", default=False)
@click.option("--include-trajectory/--no-include-trajectory", default=True)
@click.option("--include-provenance/--no-include-provenance", default=False)
@click.option(
    "--include-governance/--no-include-governance",
    default=False,
    help="Include the governance category (default: off, same as OTel export).",
)
@click.option(
    "--include-annotation/--no-include-annotation",
    default=False,
    help="Include CallAnnotation kinds (default: off, same as observe-sdk).",
)
def _normalize(
    events_path: str | None,
    otel_path: str | None,
    run_id: str | None,
    session_id: str | None,
    split_session_id: bool,
    app_name: str | None,
    application_node: bool,
    fill_synthesized_processing_defaults: bool,
    output_dir: str | None,
    output_path: str | None,
    ontology_path: str | None,
    dry_run: bool,
    include_infrastructure: bool,
    include_trajectory: bool,
    include_provenance: bool,
    include_governance: bool,
    include_annotation: bool,
) -> None:
    if bool(events_path) == bool(otel_path):
        raise click.UsageError("Pass either EVENTS_PATH or --otel SPANS_PATH.")

    if otel_path:
        from mas.library.kg.steps.normalize_otel import run_normalize_otel

        effective_run_id = run_id or _infer_run_id_from_otel(otel_path, session_id)
        artifact = run_normalize_otel(
            spans_path=otel_path,
            run_id=effective_run_id,
            output_dir=output_dir if not output_path else None,
            ontology_path=ontology_path,
            split_session_id=split_session_id,
            app_name=app_name,
            application_node=application_node,
            fill_synthesized_processing_defaults=fill_synthesized_processing_defaults,
            write_events=False,
            dry_run=dry_run,
        )
        _emit_artifact(artifact, output_path, output_dir, dry_run)
        return

    assert events_path is not None
    from mas.library.kg.steps.normalize import run_normalize

    effective_run_id = run_id or _infer_run_id_from_events(events_path, session_id)
    artifact = run_normalize(
        events_path=events_path,
        run_id=effective_run_id,
        output_dir=output_dir if not output_path else None,
        ontology_path=ontology_path,
        split_session_id=split_session_id,
        app_name=app_name,
        application_node=application_node,
        fill_synthesized_processing_defaults=fill_synthesized_processing_defaults,
        dry_run=dry_run,
        include_infrastructure=include_infrastructure,
        include_trajectory=include_trajectory,
        include_provenance=include_provenance,
        include_governance=include_governance,
        include_annotation=include_annotation,
    )
    _emit_artifact(artifact, output_path, output_dir, dry_run)


@kg_group.command("validate")
@click.argument("kg_path", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--ontology-path", default=None)
@click.option("--strict", is_flag=True)
@click.option(
    "--warning-verbosity",
    type=click.Choice(["full", "summary", "none"], case_sensitive=False),
    default="summary",
    show_default=True,
    help="Control warning detail level in validate output.",
)
@click.option(
    "--enable-extension-layer",
    "enable_extension_layers",
    multiple=True,
    type=click.Choice(["experiment"], case_sensitive=False),
    help="Enable optional extension-layer validation checks.",
)
@click.option(
    "--list-checks",
    is_flag=True,
    help="List available checks and show which ones run by default.",
)
@click.option(
    "--check",
    "include_checks",
    multiple=True,
    help="Run only this check. Repeat to run a subset.",
)
@click.option(
    "--skip-check",
    multiple=True,
    help="Disable one default check. Repeat to skip multiple checks.",
)
def _validate(
    kg_path: str | None,
    ontology_path: str | None,
    strict: bool,
    warning_verbosity: str,
    enable_extension_layers: tuple[str, ...],
    list_checks: bool,
    include_checks: tuple[str, ...],
    skip_check: tuple[str, ...],
) -> None:
    from mas.library.kg.steps.validate_kg import (
        ALL_EXTENDED_CHECK_NAMES,
        DEFAULT_CHECK_NAMES,
        KGValidationError,
        list_available_checks,
        run_validate_kg,
    )

    requested_checks = list(include_checks)
    unknown_requested = sorted(
        {
            name
            for name in requested_checks + list(skip_check)
            if name not in ALL_EXTENDED_CHECK_NAMES
        }
    )
    if unknown_requested:
        raise click.UsageError(
            "Unknown check(s): "
            + ", ".join(unknown_requested)
            + ". Use --list-checks to see valid names."
        )

    if list_checks:
        click.echo(json.dumps({
            "checks": list_available_checks(),
            "usage": {
                "run_default": "mas-lab kg validate KG.jsonld",
                "run_one": "mas-lab kg validate KG.jsonld --check shacl",
                "run_subset": (
                    "mas-lab kg validate KG.jsonld "
                    "--check unknown_node_types --check shacl"
                ),
                "skip_one": "mas-lab kg validate KG.jsonld --skip-check shacl",
            },
        }, indent=2))
        if kg_path is None:
            return

    if kg_path is None:
        raise click.UsageError("KG_PATH is required unless --list-checks is used.")

    if requested_checks:
        selected_checks = requested_checks
    else:
        selected_checks = [name for name in DEFAULT_CHECK_NAMES if name not in set(skip_check)]

    try:
        result = run_validate_kg(
            artifact=kg_path,
            ontology_path=ontology_path,
            strict=strict,
            checks=selected_checks,
            extension_layers=[layer.lower() for layer in enable_extension_layers],
            warning_verbosity=warning_verbosity.lower(),
        )
    except KGValidationError as exc:
        result = exc.report
    click.echo(json.dumps(result, indent=2))
    if result.get("error_count"):
        raise SystemExit(1)


@kg_group.command("verify")
@click.argument("events_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--strictness",
    type=click.Choice(["required", "recommended", "complete"]),
    default="required",
)
def _verify(events_path: str, strictness: str) -> None:
    from mas.library.kg.steps.verify_events import run_verify_events

    result = run_verify_events(
        events_path=events_path,
        strictness=strictness,
        fail_on_error=False,
    )
    click.echo(json.dumps(result, indent=2))
    if result.get("error_count"):
        raise SystemExit(1)


@kg_group.command("otel-to-events")
@click.argument("spans_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--run-id", required=True)
@click.option("--output-dir", "output_dir", default=".")
@click.option("--no-strict", is_flag=True)
@click.option("--no-synthesize", is_flag=True)
def _otel_to_events(
    spans_path: str,
    run_id: str,
    output_dir: str,
    no_strict: bool,
    no_synthesize: bool,
) -> None:
    del run_id, no_strict, no_synthesize
    from mas.library.kg.observability.helpers import load_spans
    from mas.library.kg.observability.otel_via_norm import detect_span_shape, to_clickhouse_spans

    spans_file = Path(spans_path).expanduser().resolve()
    spans = load_spans(spans_file)
    otel_format = detect_span_shape(spans)
    converted = to_clickhouse_spans(spans)

    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "otel_clickhouse.jsonl"
    out_file.write_text("\n".join(json.dumps(row) for row in converted), encoding="utf-8")
    click.echo(
        json.dumps(
            {
                "spans_path": str(out_file),
                "span_count": len(converted),
                "otel_format": otel_format,
            },
            indent=2,
        )
    )


@kg_group.command("otel-to-kg")
@click.argument("spans_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--run-id", required=True)
@click.option("--output-dir", "output_dir", default=".")
@click.option("--ontology-path", default=None)
@click.option("--no-strict", is_flag=True)
@click.option("--no-synthesize", is_flag=True)
@click.option(
    "--no-fill-synthesized-processing-defaults",
    is_flag=True,
    help="Leave synthesized ProcessingCall recommended fields empty.",
)
@click.option("--write-events", is_flag=True)
@click.option("--dry-run", is_flag=True)
def _otel_to_kg(
    spans_path: str,
    run_id: str,
    output_dir: str,
    ontology_path: str | None,
    no_strict: bool,
    no_synthesize: bool,
    no_fill_synthesized_processing_defaults: bool,
    write_events: bool,
    dry_run: bool,
) -> None:
    from mas.library.kg.steps.normalize_otel import run_normalize_otel

    artifact = run_normalize_otel(
        spans_path=spans_path,
        run_id=run_id,
        output_dir=output_dir,
        ontology_path=ontology_path,
        strict=not no_strict,
        synthesize_llm_gaps=not no_synthesize,
        fill_synthesized_processing_defaults=not no_fill_synthesized_processing_defaults,
        write_events=write_events,
        dry_run=dry_run,
    )
    click.echo(json.dumps(artifact.to_doc(), indent=2))


@kg_group.command("push")
@click.argument("kg_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--uri", default=None)
@click.option("--username", default=None)
@click.option("--password-env", default=None)
@click.option("--database", default=None)
@click.option("--app-name", default="")
@click.option("--infra", default=None)
@click.option("--batch-size", type=int, default=200)
@click.option(
    "--enable-extension-layer",
    "enable_extension_layers",
    multiple=True,
    type=click.Choice(["experiment"], case_sensitive=False),
    help="Enable optional ontology extension layer(s) on push. Repeat option.",
)
@click.option("--clear-session", is_flag=True)
@click.option("--dry-run", is_flag=True)
def _push(
    kg_path: str,
    uri: str | None,
    username: str | None,
    password_env: str | None,
    database: str | None,
    app_name: str,
    infra: str | None,
    batch_size: int,
    enable_extension_layers: tuple[str, ...],
    clear_session: bool,
    dry_run: bool,
) -> None:
    from mas.library.kg.steps.neo4j_push import run_neo4j_push

    conn = _resolve_neo4j_conn(
        infra=infra,
        uri=uri,
        username=username,
        password_env=password_env,
        database=database,
    )
    result = run_neo4j_push(
        kg_path,
        uri=conn["uri"],
        username=conn["username"],
        password_env=conn["password_env"],
        database=conn["database"],
        batch_size=batch_size,
        app_name=app_name,
        extension_layers=[layer.lower() for layer in enable_extension_layers],
        clear_session=clear_session,
        dry_run=dry_run,
    )
    click.echo(json.dumps(result, indent=2))


kg_group.add_command(_push, name="neo4j-push")


@kg_group.command("dump")
@click.option("--session-id", default=None)
@click.option("--run-id", default=None)
@click.option("--output", "output_path", "-o", default=None)
@click.option("--infra", default=None)
@click.option("--uri", default=None)
@click.option("--username", default=None)
@click.option("--password-env", default=None)
@click.option("--database", default=None)
def _dump(
    session_id: str | None,
    run_id: str | None,
    output_path: str | None,
    infra: str | None,
    uri: str | None,
    username: str | None,
    password_env: str | None,
    database: str | None,
) -> None:
    from mas.library.kg.steps.neo4j_dump import run_neo4j_dump

    if bool(session_id) == bool(run_id):
        raise click.UsageError("Pass exactly one of --session-id or --run-id.")

    conn = _resolve_neo4j_conn(
        infra=infra,
        uri=uri,
        username=username,
        password_env=password_env,
        database=database,
    )
    artifact = run_neo4j_dump(
        session_id=session_id,
        run_id=run_id,
        output_path=output_path,
        uri=conn["uri"],
        username=conn["username"],
        password_env=conn["password_env"],
        database=conn["database"],
    )

    if output_path:
        click.echo(
            json.dumps(
                {
                    "output": str(Path(output_path).expanduser().resolve()),
                    "node_count": artifact.node_count,
                    "edge_count": artifact.edge_count,
                    "session_id": artifact.session_id(),
                    "run_id": artifact.run_id(),
                },
                indent=2,
            )
        )
        return

    click.echo(artifact.to_json())


class KgCliComponent:
    """CLI component registered via the ``mas.lab.cli.components`` entry point."""

    def register(self, app: "click.Group") -> str | None:
        if kg_group.name in app.commands:
            return None
        app.add_command(kg_group)
        return kg_group.name
