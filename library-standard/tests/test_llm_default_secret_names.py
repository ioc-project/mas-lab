#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every LLM provider's default secret is a name the env import writes: one name per credential."""

from __future__ import annotations

import importlib
import pkgutil

from mas.runtime.secrets import SecretRef
from mas.runtime.secrets.env_names import KNOWN_ENV_NAMES


def _provider_default_secrets() -> dict[str, str]:
    import mas.library.standard.plugins.llm as package

    found: dict[str, str] = {}
    for info in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            default = getattr(value, "default_secret", None) if isinstance(value, type) else None
            if isinstance(default, SecretRef) and value.__module__ == module.__name__:
                found[f"{value.__module__}.{value.__qualname__}"] = default.name
    return found


def test_every_provider_default_secret_is_a_known_env_secret_name() -> None:
    # kills: a provider whose default secret ('huggingface') is not the name `mas-ctl secrets import-env`
    # writes for its variable ('hf'), so the imported key is never found
    defaults = _provider_default_secrets()
    assert defaults, "no LLM provider class with a default_secret was found"
    known = set(KNOWN_ENV_NAMES.values())
    unknown = {where: name for where, name in defaults.items() if name not in known}
    assert unknown == {}
