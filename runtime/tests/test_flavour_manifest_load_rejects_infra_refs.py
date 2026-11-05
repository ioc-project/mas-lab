#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import yaml
from mas.ctl.validate.separation import FlavourSeparationValidator


def test_flavour_load_rejects_infra_refs(tmp_path):
    flavour = tmp_path / "local.yaml"
    flavour.write_text(
        """
apiVersion: flavour/v1
kind: Flavour
metadata:
  name: local
spec:
  infra_refs:
    - team:base
""".strip()
        + "\n",
        encoding="utf-8",
    )

    data = yaml.safe_load(flavour.read_text(encoding="utf-8"))
    violations = FlavourSeparationValidator.collect_violations(data)
    assert any("infra_refs is forbidden" in v for v in violations)
