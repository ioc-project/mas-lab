#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The bearer token of the live dashboard's ingest and command plane (secret ``ui-ingest``)."""

from __future__ import annotations

UI_INGEST_SECRET = "ui-ingest"


def ui_ingest_token() -> str:
    """The value of the secret ``ui-ingest`` in the bound store, or ``""`` when it is not set."""
    from mas.runtime.secrets import SecretRef, current_resolver

    return current_resolver().reveal_or_none(SecretRef(name=UI_INGEST_SECRET, optional=True)) or ""
