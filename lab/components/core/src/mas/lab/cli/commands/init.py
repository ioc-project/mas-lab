#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-lab init — bootstrap user config + optional default LLM infra."""

from __future__ import annotations

import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import click
import yaml
from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME
from mas.runtime.xdg import mas_config_dir, mas_infra_dir, mas_user_config_file

_DEFAULT_INFRA_NAME = "llmprovider"
_DEFAULT_API_BASE = "https://api.openai.com/v1"


_DEFAULT_MODEL_ALIAS = "generic-model"
_DEFAULT_TARGET_MODEL = "gpt-4o-mini"


def _templates_root() -> Traversable:
    """Bundled under ``mas.lab/templates`` — no library-samples checkout required.

    ``mas.lab`` is a namespace package spanning every ``mas-lab-*`` editable
    install, so ``resources.files()`` returns a ``MultiplexedPath`` searching
    all of them. Round-tripping that through ``Path(str(...))`` corrupts it
    into the object's repr text (not a filesystem path) — use the
    Traversable's own ``/`` and ``read_text`` instead of converting it.
    """
    return resources.files("mas.lab") / "templates"


def _load_template(name: str) -> str:
    return (_templates_root() / "init" / name).read_text(encoding="utf-8")


def _load_infra_template(name: str) -> str:
    return (_templates_root() / "infra" / name).read_text(encoding="utf-8")


def _read_existing_config(path: Path | None = None) -> dict[str, Any]:
    """Parse an existing config file; return {} if absent or unreadable."""
    path = path or mas_user_config_file()
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _read_existing_infra(infra_name: str, infra_dir: Path | None = None) -> dict[str, Any]:
    """Parse existing infra yaml; return {} if absent or unreadable.

    Accepts either a bare name ("llmprovider") or a full/expandable path
    ("~/.config/mas/infra/llm-proxy.yaml").
    """
    candidate = Path(infra_name).expanduser()
    if not candidate.is_absolute():
        candidate = (infra_dir or mas_infra_dir()) / f"{infra_name}.yaml"
    if not candidate.exists():
        return {}
    try:
        return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _existing_infra_name(cfg: dict[str, Any]) -> str | None:
    """Extract first infra_refs entry from a parsed config.yaml, or None.

    Handles both bare names ("llmprovider") and full paths
    ("~/.config/mas/infra/llm-proxy.yaml") — returns the stem in both cases.
    """
    refs = cfg.get("infra_refs")
    if not isinstance(refs, list) or not refs:
        return None
    raw = str(refs[0])
    # Full path or .yaml extension → strip to stem
    if "/" in raw or raw.endswith(".yaml"):
        return Path(raw).stem or None
    return raw or None


def _existing_infra_defaults(infra: dict[str, Any]) -> dict[str, str]:
    """Extract api_base and first model mapping from a parsed infra yaml."""
    proxy = (infra.get("spec") or {}).get("proxy") or {}
    mappings = ((infra.get("spec") or {}).get("models") or {}).get("mappings") or {}
    alias, model = (list(mappings.items()) + [("", "")])[0]
    return {
        "api_base": str(proxy.get("api_base") or ""),
        "model_alias": str(alias),
        "target_model": str(model),
    }


def _files_with_removed_forms(config_path: Path, cfg: dict[str, Any], infra_dir: Path, infra_name: str) -> list[Path]:
    """The existing config and infra files that still use a removed ``env:`` or ``*_env`` form."""
    from mas.ctl.secrets.migrate import has_old_forms

    candidates = [config_path, infra_dir / f"{infra_name}.yaml"]
    for key in ("infra_refs", "default_infra"):
        value = cfg.get(key)
        for ref in value if isinstance(value, list) else [value]:
            if isinstance(ref, str) and ref:
                candidates.append(Path(ref).expanduser())
    seen: list[Path] = []
    for path in candidates:
        path = path.resolve() if path.is_file() else path
        if path.is_file() and path not in seen and has_old_forms(path.read_text(encoding="utf-8")):
            seen.append(path)
    return seen


def _offer_migration(stale: list[Path], *, yes: bool) -> bool:
    """Say which files use removed forms and, on a yes, rewrite them (a .bak copy is kept). False if declined."""
    from mas.ctl.secrets.migrate import migrate_file

    click.echo(
        "Your config uses the old env: / *_env form for an API key:\n"
        + "".join(f"  {path}\n" for path in stale)
        + "\nWhy it changed\n"
        "  A key in the shell environment reaches child processes, logs and traces, and lab workers cannot\n"
        "  read it.\n"
        "\nHow keys are stored now\n"
        "  In a private file store: one file per secret, readable only by you (directory 0700, files 0400),\n"
        "  in <workspace>/.mas/secrets, or the `secrets.dir` of your config.yaml. A config names the secret\n"
        "  and never holds the value: `api_key_env: OPENAI_API_KEY` becomes `api_key: {secret: openai}`.\n"
        "\nWhat to do\n"
        "  1. Rewrite the files above (a .bak copy is kept): the question below.\n"
        "  2. Store the key once (the command is printed after the rewrite).\n",
        err=True,
    )
    if not yes and not click.confirm("Rewrite these files now (a .bak copy is kept)?", default=True):
        click.echo("Not rewritten. Later: mas-ctl secrets migrate --write FILE", err=True)
        return False
    secrets: dict[str, str] = {}
    for path in stale:
        result = migrate_file(path, write=True)
        secrets.update(result.secrets)
        for line in result.left:
            click.echo(f"  {path}: needs a person: {line}", err=True)
    for secret, variable in sorted(secrets.items()):
        click.echo(f'\nThen store the key once:  printf %s "${variable}" | mas-ctl secrets set {secret}\n', err=True)
    return True


def _render_config_yaml(*, infra_name: str | None, local: bool = False) -> str:
    rendered = _load_template("config.yaml")
    if infra_name:
        rendered = rendered.replace("__INFRA_NAME__", infra_name)
        if local:
            rendered = rendered.replace("~/.config/mas/infra/", "infra/")
        return rendered
    # Strip the entire infra block (comments + infra_refs + default_infra).
    # The block starts at the sentinel comment line and ends after default_infra.
    stripped = re.sub(
        r"\n# Included when infra setup.*?^default_infra:.*?\n",
        "\n",
        rendered,
        flags=re.DOTALL | re.MULTILINE,
    )
    return stripped.rstrip() + "\n"


def _render_infra_yaml(
    *,
    name: str,
    api_base: str,
    model_alias: str,
    target_model: str,
) -> str:
    return (
        _load_infra_template("llmprovider.yaml")
        .replace("__INFRA_NAME__", name)
        .replace("__API_BASE_URL__", api_base)
        .replace("__MODEL_ALIAS__", model_alias)
        .replace("__TARGET_MODEL__", target_model)
    )


def _confirm_overwrite(path: Path, *, label: str, yes: bool) -> bool:
    if not path.exists():
        return True
    if yes:
        raise click.ClickException(f"{label} already exists at {path}; rerun without --yes to confirm overwrite.")
    return click.confirm(
        f"{label} already exists at {path}. Overwrite?",
        default=False,
        show_default=True,
    )


@click.command("init")
@click.option("--yes", is_flag=True, default=False, help="Use defaults without prompts.")
@click.option(
    "--local",
    "local_config",
    is_flag=True,
    default=False,
    help="Write config.yaml and infra/<name>.yaml in the current project.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    default=False,
    help="Print nothing once the files are written (prompts and errors still show).",
)
def init_cmd(yes: bool, local_config: bool, quiet: bool) -> None:
    """Initialize MAS configuration under XDG or in the current project.

    By default, creates user-level files under $XDG_CONFIG_HOME/mas. With
    ``--local``, creates ``config.yaml`` and ``infra/<name>.yaml`` in the
    current project so the folder is self-contained and portable.
    """

    project_root = Path.cwd()
    cfg_dir = project_root if local_config else mas_config_dir()
    infra_dir = project_root / "infra" if local_config else mas_infra_dir()
    config_path = project_root / WORKSPACE_CONFIG_FILENAME if local_config else mas_user_config_file()

    # Load existing values to pre-fill prompts
    existing_cfg = _read_existing_config(config_path)
    existing_infra_name = _existing_infra_name(existing_cfg) or _DEFAULT_INFRA_NAME
    existing_infra = _read_existing_infra(existing_infra_name, infra_dir)
    existing_infra_vals = _existing_infra_defaults(existing_infra)

    stale = _files_with_removed_forms(config_path, existing_cfg, infra_dir, existing_infra_name)
    if stale:
        if not _offer_migration(stale, yes=yes):
            raise click.exceptions.Exit(1)
        existing_cfg = _read_existing_config(config_path)
        existing_infra = _read_existing_infra(existing_infra_name, infra_dir)
        existing_infra_vals = _existing_infra_defaults(existing_infra)

    create_infra = True
    if not yes:
        if existing_infra_vals.get("api_base"):
            click.echo(
                f"Current configuration: {existing_infra_name}"
                f"\n  URL    : {existing_infra_vals['api_base']}"
                f"\n  API key: secret openai"
            )
            create_infra = click.confirm(
                "Update LLM infra configuration?",
                default=True,
                show_default=True,
            )
        else:
            click.echo("No LLM provider is configured yet.")
            create_infra = click.confirm(
                "Create a default LLM infra manifest to be able to use an LLM?",
                default=True,
                show_default=True,
            )

    infra_name = existing_infra_name
    api_base = existing_infra_vals.get("api_base") or _DEFAULT_API_BASE
    model_alias = existing_infra_vals.get("model_alias") or _DEFAULT_MODEL_ALIAS
    target_model = existing_infra_vals.get("target_model") or _DEFAULT_TARGET_MODEL

    if create_infra and not yes:
        infra_name = (
            click.prompt(
                "Infra name",
                default=infra_name,
                show_default=True,
            ).strip()
            or infra_name
        )
        # Reload infra defaults if name changed
        if infra_name != existing_infra_name:
            existing_infra = _read_existing_infra(infra_name, infra_dir)
            existing_infra_vals = _existing_infra_defaults(existing_infra)
            api_base = existing_infra_vals.get("api_base") or _DEFAULT_API_BASE
            model_alias = existing_infra_vals.get("model_alias") or _DEFAULT_MODEL_ALIAS
            target_model = existing_infra_vals.get("target_model") or _DEFAULT_TARGET_MODEL

        api_base = click.prompt(
            "LLM provider API base URL",
            default=api_base,
            show_default=True,
        ).strip()
        model_alias = (
            click.prompt(
                "Generic model alias used in agent manifests",
                default=model_alias,
                show_default=True,
            ).strip()
            or model_alias
        )
        target_model = (
            click.prompt(
                "Provider model mapped from this alias",
                default=target_model,
                show_default=True,
            ).strip()
            or target_model
        )

    infra_path: Path | None = None
    if create_infra:
        infra_path = infra_dir / f"{infra_name}.yaml"

    should_write_config = _confirm_overwrite(config_path, label="config", yes=yes)
    should_write_infra = True
    if infra_path is not None:
        should_write_infra = _confirm_overwrite(infra_path, label="infra", yes=yes)

    cfg_dir.mkdir(parents=True, exist_ok=True)
    if should_write_config:
        config_path.write_text(
            _render_config_yaml(
                infra_name=infra_name if create_infra else None,
                local=local_config,
            ),
            encoding="utf-8",
        )

    if create_infra and infra_path is not None and should_write_infra:
        infra_dir.mkdir(parents=True, exist_ok=True)
        infra_path.write_text(
            _render_infra_yaml(
                name=infra_name,
                api_base=(api_base or "").strip() or _DEFAULT_API_BASE,
                model_alias=model_alias,
                target_model=target_model,
            ),
            encoding="utf-8",
        )

    if quiet:
        return

    _TRIP = "library-samples/apps/trip-planner"
    click.echo()
    click.echo("Initialized MAS project configuration." if local_config else "Initialized MAS user configuration.")
    click.echo()
    click.echo(f"  config  {config_path}")
    if infra_path is not None:
        click.echo(f"  infra   {infra_path}")
        click.echo(f"          {model_alias} -> {target_model} at {api_base}")
    else:
        click.echo("  infra   skipped")

    click.echo()
    click.echo("Next:")
    click.echo()
    step = 1
    if infra_path is not None:
        click.echo(f"  {step}. Set your API key:")
        click.echo("       echo '<your-key>' | mas-ctl secrets set openai")
        click.echo()
        step += 1
    click.echo(f"  {step}. Run a first multi-agent system:")
    click.echo(
        f"       mas-ctl run-mas {_TRIP}/mas.yaml --infra-ref standard:openai "
        '--trace -q "Plan a trip from Celestia to Verdantia"'
    )
    click.echo()
    click.echo(f"  {step + 1}. Look at what happened:")
    click.echo(f"       mas-lab telemetry show {_TRIP}/traces/events.jsonl")
    click.echo(f"       mas-lab plot trajectory {_TRIP}/traces/events.jsonl --format html -o trajectory.html")
    click.echo()
    click.echo("Tips:")
    click.echo()
    click.echo("  --trace         readable exchange log on stderr   (--trace full: payloads)")
    click.echo("  --events-file   choose where the trace is written")
    if not local_config:
        click.echo("  init --local    config in the current project instead of your user folder")
    click.echo("  init --quiet    print nothing")
    click.echo()
    click.echo("Tutorials: docs/user-guide.md")
