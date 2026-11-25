#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The conversation id: owner and client id in, one stable id out, owner readable from it."""

from __future__ import annotations

import pytest
from mas.library.standard.lib.stack import conversation_id

ALICE = "own:" + "a" * 40
MALLORY = "own:" + "b" * 40


def test_golden_vectors() -> None:
    assert conversation_id(ALICE, "c1") == "own." + "a" * 40 + ".21fad58f37ec955abfaf9ba8b5b0c54c"


def test_shape_and_length() -> None:
    found = conversation_id(ALICE, "c1")
    assert len(found) == 77
    assert set(found) <= set("abcdef0123456789.own")


def test_owner_and_client_id_both_matter() -> None:
    assert conversation_id(ALICE, "c1") != conversation_id(MALLORY, "c1")
    assert conversation_id(ALICE, "c1") != conversation_id(ALICE, "c2")
    assert conversation_id(ALICE, "c1") == conversation_id(ALICE, "c1")


def test_a_client_sending_an_internal_id_gets_its_own_namespace() -> None:
    victim = conversation_id(ALICE, "c1")
    assert conversation_id(MALLORY, victim) != victim
    assert conversation_id(MALLORY, victim) != conversation_id(ALICE, "c1")


def test_lone_surrogate_does_not_raise() -> None:
    assert len(conversation_id(ALICE, "x\ud800y")) == 77


def test_domain_separation_from_a_bare_digest() -> None:
    import hashlib

    bare = hashlib.sha256(f"{ALICE}c1".encode()).hexdigest()[:32]
    assert not conversation_id(ALICE, "c1").endswith(bare)


@pytest.mark.parametrize("bad", ["", "alice", "own:short", "own:" + "a" * 41])
def test_a_non_owner_key_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        conversation_id(bad, "c1")
