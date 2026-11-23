#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The live dashboard's ingest token is the secret ``ui-ingest`` of the bound store, or nothing."""

from __future__ import annotations

from mas.lab.ui_ingest import ui_ingest_token
from secret_fakes import bound_secrets


def test_the_token_is_the_bound_secret() -> None:
    with bound_secrets({"ui-ingest": "tok"}):
        assert ui_ingest_token() == "tok"


def test_no_secret_is_no_token() -> None:
    with bound_secrets({}):
        assert ui_ingest_token() == ""
