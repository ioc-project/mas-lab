#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The input prompt is for a person at a terminal; piped input gets none, so no ``You: You:``."""

from __future__ import annotations

import io

import pytest
from mas.ctl.session.operator_console import OperatorConsole


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(("stdin", "prompted"), [(io.StringIO("hello\n"), False), (_Terminal("hello\n"), True)])
def test_the_prompt_is_written_only_to_a_terminal(monkeypatch: pytest.MonkeyPatch, stdin, prompted: bool) -> None:
    err = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    assert OperatorConsole(err=err)._read_line("You: ") == "hello"
    assert err.getvalue() == ("You: " if prompted else "")
