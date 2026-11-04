#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Install-hint helper that does not import the runtime at module load."""

from __future__ import annotations


def extra_hint(extra: str, *, editable: bool = False) -> str:
    try:
        from mas._dist import extra_hint as _hint

        return _hint(extra, file=__file__, editable=editable)
    except Exception:  # pragma: no cover - standalone install without runtime
        return extra
