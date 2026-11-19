#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.library.telemetry.transport.version import check_sdk_range, installed_version
from mas.library.telemetry.transport.warnings import reset


def test_missing_and_out_of_range_sdks_warn_once() -> None:
    reset()
    assert installed_version("mas-lab-no-such-distribution") is None
    assert check_sdk_range(classifier_id="C", specifier="", distribution="x") is True
    assert check_sdk_range(classifier_id="C", specifier=">=1", distribution="mas-lab-no-such-distribution") is False
    assert check_sdk_range(classifier_id="C", specifier=">=9", distribution="x", installed="1.0") is False
    assert check_sdk_range(classifier_id="C", specifier=">=1", distribution="x", installed="not-a-version") is False
    assert check_sdk_range(classifier_id="C", specifier=">=1", distribution="x", installed="1.2") is True
    reset()
