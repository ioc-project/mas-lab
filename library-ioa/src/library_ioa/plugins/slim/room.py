#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A shared room: the members of a SLIM channel post to it and read each other's posts.

A room is a group in which the members talk to each other, not only to the one that asked. An opener
(the starter) creates the channel and publishes an ``open`` message that carries the room's rules; from
then on every member runs a turn on the posts of the last round, publishes its reply to the whole
channel, and does so again when all members have posted. Every member holds the same data (the posts it
received), so each one reaches the same stop decision on its own, with no vote and no coordinator. The
``closer`` publishes the result.

This module is the data and the rules; ``room_link.py`` is the part that talks to the node.
Nothing here names an agent, a task or a vocabulary: the rules arrive in the ``open`` message.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Channel message intents of a room. ``open`` starts it, ``post`` is a member's reply for a round,
# ``result`` is what the closer publishes when the room stops.
OPEN_INTENT = "open"
POST_INTENT = "post"
RESULT_INTENT = "result"
ROOM_INTENTS = frozenset({OPEN_INTENT, POST_INTENT, RESULT_INTENT})

# Names a template may use. Any other brace in a template (a JSON example, say) is left as it is.
_TEMPLATE_NAMES = ("round", "task", "board", "winner", "why", "members", "n", "cap")
_TEMPLATE = re.compile(r"\{(" + "|".join(_TEMPLATE_NAMES) + r")\}")


class RoomError(ValueError):
    """A room's rules are not usable."""


def fill(template: str, **values: object) -> str:
    """``template`` with each ``{name}`` of a known name replaced; other text is untouched."""
    return _TEMPLATE.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)


def last_json_object(text: str) -> dict[str, Any] | None:
    """The last top-level JSON object in ``text`` (bare, or in a code fence), else ``None``.

    A member ends its reply with its position as one object, so the last one is the position; the
    objects nested inside it are not top-level and are skipped.
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    index = 0
    while index < len(text):
        if text[index] != "{":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index += 1
            continue
        if isinstance(value, dict):
            found = value
        index = end
    return found


@dataclass(frozen=True)
class RoomSpec:
    """The rules of one room, as the opener publishes them."""

    channel: str
    members: tuple[str, ...]
    closer: str
    round_cap: int
    post_timeout_s: float
    task: str
    turn: str
    agree_on: str
    accept: dict[str, Any]
    agreed: str
    none: str

    @classmethod
    def from_dict(cls, data: Any, *, channel: str) -> RoomSpec:
        if not isinstance(data, dict):
            raise RoomError("a room needs its rules")
        members = tuple(str(m).strip() for m in data.get("members") or () if str(m).strip())
        if len(members) < 2 or len(set(members)) != len(members):
            raise RoomError("a room needs at least two distinct members")
        closer = str(data.get("closer") or members[0]).strip()
        if closer not in members:
            raise RoomError(f"the closer {closer!r} is not a member")
        try:
            cap = int(data.get("round_cap"))
            timeout = float(data.get("post_timeout_s"))
        except (TypeError, ValueError):
            raise RoomError("round_cap and post_timeout_s must be numbers") from None
        if cap < 1 or timeout <= 0:
            raise RoomError("round_cap must be at least 1 and post_timeout_s positive")
        turn = str(data.get("turn") or "")
        if not turn.strip():
            raise RoomError("a room needs a turn template")
        accept = data.get("accept") or {}
        if not isinstance(accept, dict):
            raise RoomError("accept must be a mapping of field to value")
        return cls(
            channel=channel,
            members=members,
            closer=closer,
            round_cap=cap,
            post_timeout_s=timeout,
            task=str(data.get("task") or ""),
            turn=turn,
            agree_on=str(data.get("agree_on") or ""),
            accept=dict(accept),
            agreed=str(data.get("agreed") or "{winner}"),
            none=str(data.get("none") or "{why}"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": list(self.members),
            "closer": self.closer,
            "round_cap": self.round_cap,
            "post_timeout_s": self.post_timeout_s,
            "task": self.task,
            "turn": self.turn,
            "agree_on": self.agree_on,
            "accept": dict(self.accept),
            "agreed": self.agreed,
            "none": self.none,
        }


@dataclass(frozen=True)
class Post:
    text: str
    ok: bool = True


@dataclass(frozen=True)
class Verdict:
    """Why a room stops: ``agreed`` on ``winner``, or ``none`` for ``why``."""

    kind: str
    winner: str = ""
    why: str = ""


@dataclass
class Room:
    """One member's view of a room: the rules and the posts it has received."""

    spec: RoomSpec
    room_id: str
    opener: str
    me: str
    round: int = 0
    round_started: float = 0.0
    closed: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    posts: dict[int, dict[str, Post]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def add(self, round_no: int, member: str, text: str, *, ok: bool = True) -> bool:
        """Record ``member``'s post for ``round_no``. The first post of a member for a round stands."""
        if member not in self.spec.members or round_no < 1:
            return False
        with self.lock:
            row = self.posts.setdefault(round_no, {})
            if member in row:
                return False
            row[member] = Post(text, ok)
            return True

    def complete(self, round_no: int) -> bool:
        with self.lock:
            return set(self.posts.get(round_no, {})) >= set(self.spec.members)

    def missing(self, round_no: int) -> list[str]:
        with self.lock:
            return [m for m in self.spec.members if m not in self.posts.get(round_no, {})]

    def board(self, round_no: int) -> str:
        """The round's posts as ``[member]`` blocks, in member order."""
        with self.lock:
            row = self.posts.get(round_no, {})
            return "\n\n".join(f"[{m}]\n{row[m].text}" for m in self.spec.members if m in row)

    def verdict(self, round_no: int) -> Verdict | None:
        """The stop decision after a complete round, or ``None`` to go on. Every member computes the same one."""
        spec = self.spec
        with self.lock:
            row = dict(self.posts.get(round_no, {}))
        failed = [m for m in spec.members if m in row and not row[m].ok]
        if failed:
            return Verdict("none", why=f"{', '.join(failed)} failed to answer in round {round_no}")
        objects = [last_json_object(row[m].text) if m in row else None for m in spec.members]
        if spec.agree_on and all(isinstance(o, dict) for o in objects):
            if all(all(o.get(k) == v for k, v in spec.accept.items()) for o in objects):
                values = {json.dumps(o.get(spec.agree_on), sort_keys=True) for o in objects}
                winner = objects[0].get(spec.agree_on)
                if len(values) == 1 and winner not in (None, "", [], {}):
                    return Verdict("agreed", winner=str(winner))
        if round_no >= spec.round_cap:
            return Verdict(
                "none", why=f"no plan accepted by all {len(spec.members)} members within {spec.round_cap} rounds"
            )
        return None

    def silent(self, now: float | None = None) -> Verdict | None:
        """The stop decision when the current round has waited longer than ``post_timeout_s``."""
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.closed or self.round < 1 or self.complete(self.round):
                return None
            if now - self.round_started < self.spec.post_timeout_s:
                return None
            names = ", ".join(self.missing(self.round))
        return Verdict(
            "none", why=f"no post from {names} in round {self.round} within {self.spec.post_timeout_s:g}s"
        )

    def turn_input(self, round_no: int) -> str:
        """What a member is asked in ``round_no``: the turn template with the last round's posts."""
        board = self.board(round_no - 1) if round_no > 1 else "No posts yet: this is the first round."
        return fill(self.spec.turn, round=round_no, task=self.spec.task, board=board, cap=self.spec.round_cap)

    def result_text(self, verdict: Verdict, round_no: int) -> str:
        template = self.spec.agreed if verdict.kind == "agreed" else self.spec.none
        return fill(
            template,
            round=round_no,
            winner=verdict.winner,
            why=verdict.why,
            members=", ".join(self.spec.members),
            n=len(self.spec.members),
            cap=self.spec.round_cap,
            task=self.spec.task,
        )


class PendingResult:
    """What the opener waits for: the closer's result post."""

    def __init__(self) -> None:
        self.text = ""
        self.sender = ""
        self.done = threading.Event()

    def set(self, sender: str, text: str) -> None:
        if not self.done.is_set():
            self.sender, self.text = sender, text
            self.done.set()
