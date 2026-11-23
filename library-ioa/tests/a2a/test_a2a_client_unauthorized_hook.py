#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The 401 response hook of the A2A HTTP client acts only for a send that has a layer above it."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from library_ioa.plugins.a2a.client import _UNAUTHORIZED, _note_unauthorized


def _response(status: int) -> Mock:
    response = Mock()
    response.status_code = status
    response.aread = AsyncMock()
    response.content = b'{"error": {"challenge": {"nonce": "n"}}}'
    response.headers = {"www-authenticate": 'Challenge nonce="n"'}
    return response


async def test_an_unarmed_send_is_left_alone() -> None:
    """Kills: a hook that reads the body of every 401, for a caller that never asked for it."""
    seen: dict = {"armed": False}
    token = _UNAUTHORIZED.set(seen)
    try:
        response = _response(401)
        await _note_unauthorized(response)
    finally:
        _UNAUTHORIZED.reset(token)
    response.aread.assert_not_awaited()
    assert seen == {"armed": False}


async def test_an_armed_send_notes_a_401_and_nothing_else() -> None:
    seen: dict = {"armed": True}
    token = _UNAUTHORIZED.set(seen)
    try:
        await _note_unauthorized(_response(200))
        assert "status" not in seen
        await _note_unauthorized(_response(401))
    finally:
        _UNAUTHORIZED.reset(token)
    assert seen["status"] == 401 and b"nonce" in seen["body"]


async def test_outside_any_send_the_hook_does_nothing() -> None:
    response = _response(401)
    await _note_unauthorized(response)
    response.aread.assert_not_awaited()
