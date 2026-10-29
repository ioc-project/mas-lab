#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load .env for ctl CLI and lab runs."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_HF_TOKEN_ENV = "HF_TOKEN"


def huggingface_token(
    *,
    token: str | None = None,
    token_file: str | Path | None = None,
) -> str:
    """Resolve a Hugging Face token. ``--token`` wins, then ``--token-file``, then ``HF_TOKEN``.

    Lab commands pass the flags here. This module is the only place that reads
    the environment for that token.
    """
    explicit = (token or "").strip()
    if explicit:
        return explicit
    if token_file:
        path = Path(token_file).expanduser()
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"token file {path} is empty")
        return text
    env = os.environ.get(_HF_TOKEN_ENV, "").strip()
    if env:
        return env
    raise ValueError(
        "Hugging Face token is required (--token, --token-file, or HF_TOKEN)"
    )

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


def load_dotenv(
    *,
    cwd: Path | None = None,
    manifest_dir: Path | None = None,
    explicit: Path | None = None,
    override: bool = False,
) -> Path | None:
    """Load dotenv from explicit path, CWD, manifest directory, and repo ancestors."""
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        return None

    seen: set[Path] = set()
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    base = cwd or Path.cwd()
    candidates.append(base / ".env")
    if manifest_dir:
        candidates.append(manifest_dir / ".env")
        for parent in [manifest_dir, *manifest_dir.parents]:
            if (parent / ".git").exists() or parent == parent.parent:
                candidates.append(parent / ".env")
                break

    loaded: Path | None = None
    for path in candidates:
        resolved = path.resolve()
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        _load(resolved, override=override)
        loaded = resolved

    if loaded is not None:
        os.environ.setdefault("MAS_DOTENV_LOADED", str(loaded))
    return loaded
