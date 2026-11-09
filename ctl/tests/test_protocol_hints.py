#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Protocol hint emission."""

from __future__ import annotations

import io

from mas.ctl.session.protocol_hints import emit_session_protocol_hints


def test_protocol_hints_only_when_verbose():
    err = io.StringIO()
    emit_session_protocol_hints(
        interactive=True,
        hitl_terminal=object(),
        hitl_responder=None,
        verbose=0,
        trace=True,
        err=err,
    )
    assert err.getvalue() == ""

    emit_session_protocol_hints(
        interactive=True,
        hitl_terminal=object(),
        hitl_responder=None,
        verbose=1,
        trace=True,
        err=err,
    )
    out = err.getvalue()
    assert "note: Trace on:" in out
    assert "scripted terminal" in out
    assert "You:" not in out
