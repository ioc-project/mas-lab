#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``retries`` is the number of extra tries; ``max_attempts`` is the total."""

from __future__ import annotations

import pytest
from mas.library.standard.lib.retry.policy import RetryPolicy
from mas.runtime.spec.gov import SpecBindingError


def test_retry_policy_retries_means_extra_tries() -> None:
    assert RetryPolicy.from_mapping({"retries": 0}, field_name="retry").max_attempts == 1
    assert RetryPolicy.from_mapping({"retries": 2}, field_name="retry").max_attempts == 3
    for bad in (-1, True, 1.5):
        with pytest.raises(SpecBindingError, match="retries"):
            RetryPolicy.from_mapping({"retries": bad}, field_name="retry")
    with pytest.raises(SpecBindingError, match="not both"):
        RetryPolicy.from_mapping({"retries": 1, "max_attempts": 2}, field_name="retry")
