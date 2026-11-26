#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A run's infra files may name the run's fixtures and the run variables."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark.environment.attach import render_infra_refs
from mas.lab.benchmark.environment.variables import Variables

ROWS = """\
apiVersion: infra/v1
kind: ToolServerRegistry
metadata: {name: payments}
spec:
  tool_servers:
  - id: payments-mcp
    protocol: mcp
    usage: connect
    transport: streamable-http
    url: ${fixture.payments.url}/mcp
    params: {run: "${run.key}"}
"""


def test_a_file_naming_a_fixture_is_rendered_for_the_run(tmp_path: Path) -> None:
    src = tmp_path / "tools.infra.yaml"
    src.write_text(ROWS, encoding="utf-8")
    plain = tmp_path / "plain.infra.yaml"
    plain.write_text("kind: Identity\nspec: {x: '{not_a_template}'}\n", encoding="utf-8")
    vars_ = Variables({"fixture.payments.url": "http://127.0.0.1:5001", "run.key": "e/s/item1/r1#1"})
    out = render_infra_refs([str(src), str(plain)], vars_, tmp_path / "out")
    assert out[1] == str(plain)  # no template: used as it is
    row = yaml.safe_load(Path(out[0]).read_text(encoding="utf-8"))["spec"]["tool_servers"][0]
    assert row["url"] == "http://127.0.0.1:5001/mcp"
    assert row["params"] == {"run": "e/s/item1/r1#1"}
    assert "${" in src.read_text(encoding="utf-8")  # the source is never rewritten


def test_two_runs_get_their_own_copy(tmp_path: Path) -> None:
    src = tmp_path / "tools.infra.yaml"
    src.write_text(ROWS, encoding="utf-8")
    a = render_infra_refs([str(src)], Variables({"fixture.payments.url": "http://h:1", "run.key": "a"}), tmp_path / "a")
    b = render_infra_refs([str(src)], Variables({"fixture.payments.url": "http://h:2", "run.key": "b"}), tmp_path / "b")
    assert a != b and "http://h:1" in Path(a[0]).read_text() and "http://h:2" in Path(b[0]).read_text()


def test_a_fixture_that_is_not_up_is_named_in_the_error(tmp_path: Path) -> None:
    src = tmp_path / "tools.infra.yaml"
    src.write_text(ROWS, encoding="utf-8")
    with pytest.raises(ValueError, match=r"tools\.infra\.yaml.*fixture\.payments\.url"):
        render_infra_refs([str(src)], Variables({"run.key": "r"}), tmp_path / "out")


def test_a_substituted_value_is_not_scanned_again(tmp_path: Path) -> None:
    src = tmp_path / "x.infra.yaml"
    src.write_text("a: ${run.key}\n", encoding="utf-8")
    out = render_infra_refs([str(src)], Variables({"run.key": "${fixture.x.url}"}), tmp_path / "out")
    assert yaml.safe_load(Path(out[0]).read_text())["a"] == "${fixture.x.url}"
