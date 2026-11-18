#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The rules of a shared room (``room.py``): no node, no link."""

from __future__ import annotations

import json

import pytest
from library_ioa.plugins.slim.room import PendingResult, Room, RoomError, RoomSpec, fill, last_json_object

MEMBERS = ("b", "c", "d")


def _spec(**over: object) -> RoomSpec:
    data = {
        "members": list(MEMBERS),
        "closer": "b",
        "round_cap": 3,
        "post_timeout_s": 5,
        "task": "pick",
        "turn": "R{round} {task}\n{board}",
        "agree_on": "plan",
        "accept": {"stance": "accept"},
        "agreed": "ok {winner} r{round} by {members}",
        "none": "none {why}",
    }
    data.update(over)
    return RoomSpec.from_dict(data, channel="room")


def _room(**over: object) -> Room:
    return Room(spec=_spec(**over), room_id="r1", opener="a", me="b")


def _post(room: Room, round_no: int, plans: dict[str, str], stance: str = "accept") -> None:
    for member, plan in plans.items():
        room.add(round_no, member, "text\n" + json.dumps({"plan": plan, "stance": stance}))


def test_last_json_object_is_the_closing_one_and_skips_nested_objects() -> None:
    text = 'see {"not": "last"} then\n```json\n{"plan": "p1", "ratings": {"p1": 0.9}}\n```'
    assert last_json_object(text) == {"plan": "p1", "ratings": {"p1": 0.9}}
    assert last_json_object("no object { here") is None
    assert last_json_object("[1, 2]") is None


def test_fill_replaces_only_known_names() -> None:
    assert fill('R{round} {"plan": "x"} {unknown}', round=2) == 'R2 {"plan": "x"} {unknown}'


def test_a_post_counts_once_and_only_from_a_member() -> None:
    room = _room()
    assert room.add(1, "b", "x") is True
    assert room.add(1, "b", "again") is False
    assert room.add(1, "z", "x") is False
    assert room.add(0, "c", "x") is False
    assert room.posts[1]["b"].text == "x"
    assert room.missing(1) == ["c", "d"]
    assert room.complete(1) is False


def test_posts_for_a_later_round_are_kept_until_it_starts() -> None:
    room = _room()
    room.add(2, "c", "early")
    assert room.posts[2]["c"].text == "early"


def test_agreement_needs_every_member_to_accept_the_same_plan() -> None:
    room = _room()
    _post(room, 1, {"b": "p1", "c": "p1", "d": "p1"})
    verdict = room.verdict(1)
    assert (verdict.kind, verdict.winner) == ("agreed", "p1")
    assert room.result_text(verdict, 1) == "ok p1 r1 by b, c, d"


def test_no_agreement_on_a_split_plan_or_a_block() -> None:
    room = _room()
    _post(room, 1, {"b": "p1", "c": "p2", "d": "p1"})
    assert room.verdict(1) is None
    _post(room, 2, {"b": "p1", "c": "p1", "d": "p1"}, stance="block")
    assert room.verdict(2) is None


def test_a_member_without_a_position_blocks_agreement() -> None:
    room = _room()
    _post(room, 1, {"b": "p1", "c": "p1"})
    room.add(1, "d", "prose only")
    assert room.verdict(1) is None


def test_the_round_cap_ends_the_room() -> None:
    room = _room(round_cap=2)
    _post(room, 2, {"b": "p1", "c": "p2", "d": "p3"})
    verdict = room.verdict(2)
    assert verdict.kind == "none"
    assert room.result_text(verdict, 2) == "none no plan accepted by all 3 members within 2 rounds"


def test_a_failed_member_ends_the_room_at_once() -> None:
    room = _room()
    room.add(1, "b", "x")
    room.add(1, "c", "", ok=False)
    room.add(1, "d", "x")
    assert room.verdict(1).why == "c failed to answer in round 1"


def test_silence_is_judged_against_the_round_start() -> None:
    room = _room(post_timeout_s=10)
    room.round, room.round_started = 1, 100.0
    room.add(1, "b", "x")
    assert room.silent(now=105.0) is None
    verdict = room.silent(now=111.0)
    assert verdict.why == "no post from c, d in round 1 within 10s"
    room.closed = True
    assert room.silent(now=999.0) is None


def test_the_turn_input_carries_the_task_and_the_last_rounds_board() -> None:
    room = _room()
    assert room.turn_input(1) == "R1 pick\nNo posts yet: this is the first round."
    room.add(1, "b", "from b")
    room.add(1, "c", "from c")
    assert room.turn_input(2) == "R2 pick\n[b]\nfrom b\n\n[c]\nfrom c"


@pytest.mark.parametrize(
    "over, message",
    [
        ({"members": ["b"]}, "at least two"),
        ({"members": ["b", "b"]}, "distinct"),
        ({"closer": "z"}, "not a member"),
        ({"round_cap": 0}, "round_cap"),
        ({"post_timeout_s": 0}, "post_timeout_s"),
        ({"round_cap": "x"}, "numbers"),
        ({"turn": " "}, "turn template"),
        ({"accept": ["stance"]}, "accept"),
    ],
)
def test_unusable_rules_are_refused(over: dict, message: str) -> None:
    with pytest.raises(RoomError, match=message):
        _spec(**over)


def test_the_rules_survive_the_wire() -> None:
    spec = _spec()
    assert RoomSpec.from_dict(json.loads(json.dumps(spec.to_dict())), channel="room") == spec


def test_a_pending_result_keeps_the_first_answer() -> None:
    pending = PendingResult()
    pending.set("b", "first")
    pending.set("c", "second")
    assert (pending.sender, pending.text, pending.done.is_set()) == ("b", "first", True)
