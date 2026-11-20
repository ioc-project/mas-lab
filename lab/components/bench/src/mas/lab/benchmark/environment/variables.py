#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve ``${fixture.name.port}`` / ``${item.id}`` templates. No env-var reads."""

from __future__ import annotations

import re
from typing import Any, Mapping

_TOKEN = re.compile(r"\$\$|\$\{([A-Za-z0-9_.-]+)\}|\{([A-Za-z0-9_.-]+)\}")


class Variables(dict[str, str]):
    """Flat string map used by environment steps and fixture commands."""

    def interpolate(self, text: str) -> str:
        """One pass: a substituted value is never scanned again, so it cannot inject a template."""

        def replace(match: re.Match[str]) -> str:
            if match.group(0) == "$$":
                return "$"
            key = match.group(1) or match.group(2)
            if key not in self and key.startswith("fixtures."):
                key = "fixture." + key[len("fixtures.") :]
            if key not in self:
                raise ValueError(f"unknown environment variable {key!r}")
            return self[key]

        return _TOKEN.sub(replace, str(text))

    def interpolate_any(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.interpolate(value)
        if isinstance(value, list):
            return [self.interpolate_any(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.interpolate_any(item) for item in value)
        if isinstance(value, Mapping):
            return {str(k): self.interpolate_any(v) for k, v in value.items()}
        return value
