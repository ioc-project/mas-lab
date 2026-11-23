#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load .env for ctl CLI and lab runs."""

from __future__ import annotations

import logging
from pathlib import Path

from mas.runtime import hostenv
from mas.runtime.envkeys import LLM_SCHEDULER_KEYS

logger = logging.getLogger(__name__)


def huggingface_token(
    *,
    token: str | None = None,
    token_file: str | Path | None = None,
) -> str:
    """Resolve a Hugging Face token. ``--token`` wins, then ``--token-file``, then secret ``hf``."""
    explicit = (token or "").strip()
    if explicit:
        return explicit
    if token_file:
        path = Path(token_file).expanduser()
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"token file {path} is empty")
        return text
    from mas.runtime.secrets import SecretMissing, SecretRef, current_resolver

    try:
        return current_resolver().reveal(SecretRef(name="hf"))
    except SecretMissing as exc:
        raise ValueError("Hugging Face token is required (--token, --token-file, or secret hf)") from exc


_warned_overrides: set[tuple[str, str]] = set()


def warn_env_override(name: str, value: str, *, replaces: str) -> None:
    """Log once per value when an environment variable supersedes declared configuration."""
    if (name, value) in _warned_overrides:
        return
    _warned_overrides.add((name, value))
    logger.warning(
        "Environment override %s=%s replaces %s. Declare it in the manifest or "
        "workspace config.yaml, or pass the CLI flag, to keep runs reproducible.",
        name,
        value,
        replaces,
    )


_ENV_MODEL_VARS = ("MAS_CTL_MODEL", "MAS_LLM_MODEL")


def env_model_override(*, replaces: str = "the declared model") -> str | None:
    """Last-resort model id from ``MAS_CTL_MODEL``, then ``MAS_LLM_MODEL``.

    Empty values are ignored. Logs once per value via :func:`warn_env_override`.
    """
    for env_name in _ENV_MODEL_VARS:
        token = hostenv.get(env_name, "").strip()
        if token:
            warn_env_override(env_name, token, replaces=replaces)
            return token
    return None


_warned_endpoints: set[tuple[str, str]] = set()


def warn_endpoint_from_env(var: str, value: str) -> None:
    """Warn once that an address came from the shell, so the API key follows it to that host."""
    from urllib.parse import urlparse

    host = urlparse(value).netloc.rsplit("@", 1)[-1] or "(unparsed)"
    key = (var, host)
    if key in _warned_endpoints:
        return
    _warned_endpoints.add(key)
    logger.warning(
        "LLM address comes from the environment variable %s (host %s): the API key for this endpoint is "
        "sent to that host. Unset %s to use the address in the file.",
        var,
        host,
        var,
    )


def load_dotenv(
    *,
    cwd: Path | None = None,
    manifest_dir: Path | None = None,
    explicit: Path | None = None,
    override: bool = False,
) -> Path | None:
    """Locate a dotenv file. Does not export values into ``os.environ``.

    Non-secret ``MAS_LLM_*`` keys overlay into hostenv for the scheduler.
    Use ``mas-ctl secrets import-env`` to copy secrets into the file store.
    """
    del override  # previously forwarded to python-dotenv; values must not enter the process env
    seen: set[Path] = set()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    base = cwd or Path.cwd()
    candidates.append(base / ".env")
    if manifest_dir:
        candidates.append(manifest_dir / ".env")
        for parent in [manifest_dir, *manifest_dir.parents]:
            if (parent / ".git").exists() or parent == parent.parent:
                candidates.append(parent / ".env")
                break

    for path in candidates:
        resolved = path.resolve()
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        _overlay_non_secret_dotenv(resolved)
        return resolved
    return None


def _overlay_non_secret_dotenv(path: Path) -> None:
    from dotenv import dotenv_values
    from mas.ctl.session.llm_scheduler_env import refresh_env_overrides

    parsed = dotenv_values(path)
    # Only the non-secret LLM scheduler keys; the shell wins over the file, as with any dotenv loader.
    overlay = {k: v for k, v in parsed.items() if k in LLM_SCHEDULER_KEYS and v and hostenv.get(k) is None}
    if not overlay:
        return
    hostenv.set_overlay(overlay)
    refresh_env_overrides()
