#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance plugin used by ctl tests to check how a Control's paths are read.

Lives outside ``conftest.py`` so its module name stays unique when pytest loads
more than one test tree (each tree's conftest is imported as ``conftest``).
"""


class PathProbe:
    """A governance plugin that takes a file path, to test how a Control's paths are read."""

    def __init__(self, script_file: str = "", **_: object) -> None:
        self.script_file = script_file

    def evaluate_egress(self, intent, *, config):
        from mas.runtime.kernel.coupling import GovDecision

        return GovDecision.ALLOW, "path_probe", "probe"
