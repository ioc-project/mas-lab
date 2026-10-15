#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run identity and stream identifiers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunKey:
    experiment: str
    scenario: str
    item: str
    run: str
    attempt: int = 1

    def stream_id(self) -> str:
        return f"{self.experiment}/{self.scenario}/{self.item}/{self.run}#{self.attempt}"

    def parent_item(self) -> tuple[str, str, str]:
        return (self.experiment, self.scenario, self.item)

    def parent_scenario(self) -> tuple[str, str]:
        return (self.experiment, self.scenario)

    def artifact_uri(self, name: str) -> str:
        return (
            f"artifact://{self.experiment}/{self.scenario}/{self.item}/"
            f"{self.run}/{name}#{self.attempt}"
        )


def run_key_from_parts(
    *,
    experiment: str = "",
    scenario: str = "",
    item: str = "",
    test: str = "",
    run: str | int = "",
    attempt: int = 1,
) -> RunKey:
    folder = str(item or test or "")
    run_name = f"r{run}" if isinstance(run, int) else str(run)
    return RunKey(
        experiment=str(experiment or "experiment"),
        scenario=str(scenario or "scenario"),
        item=folder,
        run=run_name,
        attempt=int(attempt or 1),
    )
