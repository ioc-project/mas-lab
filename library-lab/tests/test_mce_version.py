#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.library.lab.steps.eval.mce import _metric_impl_version


def test_metric_impl_version_reads_eval_distribution() -> None:
    assert _metric_impl_version()
