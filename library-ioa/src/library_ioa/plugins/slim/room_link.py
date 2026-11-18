#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The link's side of a shared room (see ``room.py``): open it, post to it, follow its rounds.

A member never waits inside a frame handler. The ``open`` frame and each post that completes a round
start the member's turn on a worker thread, so the link keeps reading the other members' posts while the
turn runs. The opener (a link that is not a member) creates the channel, publishes ``open`` and waits
for the closer's ``result``.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import threading
import time
import uuid
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError

from library_ioa.plugins.slim.inbound import is_idle_timeout, member_of
from library_ioa.plugins.slim.room import (
    OPEN_INTENT,
    POST_INTENT,
    RESULT_INTENT,
    PendingResult,
    Room,
    RoomError,
    RoomSpec,
    Verdict,
)

logger = logging.getLogger(__name__)
# The longest a post that arrived before its room's ``open`` is kept, in frames per channel.
MAX_EARLY_POSTS = 256


class SlimRooms:
    """Shared-room behaviour of ``SlimAgentComm``. Needs ``_apublish``, ``_run_channel_turn`` and the actor."""

    def _init_rooms(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._room_waits: dict[str, PendingResult] = {}
        self._early_posts: dict[str, list[tuple[str, int, str, str, bool]]] = {}
        self._rooms_lock = threading.Lock()

    # ---- opener

    def open_room(
        self,
        channel: str,
        task: str,
        *,
        members: list[str],
        rules: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Create ``channel`` if needed, open a room on it with ``task`` and return the closer's result.

        ``rules`` are the room's rules (see ``RoomSpec``); ``members`` are the agents that post. This link
        is the opener: it posts nothing else and counts nothing. Raises ``AgentCommError`` when the rules
        are unusable or no result arrives within ``timeout`` seconds.
        """
        try:
            spec = RoomSpec.from_dict({**rules, "members": members, "task": task}, channel=channel)
        except RoomError as exc:
            raise AgentCommError(f"room {channel!r}: {exc}") from exc
        if channel not in self._groups:
            self.create_channel(channel, list(spec.members), {})
        room_id = uuid.uuid4().hex
        pending = PendingResult()
        self._room_waits[room_id] = pending
        wait = float(timeout)
        try:
            self._actor.submit(self._aopen(spec, room_id, pending, headers, wait), timeout=wait + self._send_timeout)
        finally:
            self._room_waits.pop(room_id, None)
        if not pending.done.is_set():
            raise AgentCommError(f"room {channel!r}: no result within {wait:g}s")
        return pending.text

    async def _aopen(
        self, spec: RoomSpec, room_id: str, pending: PendingResult, headers: dict[str, str] | None, wait: float
    ) -> None:
        await self._apublish(
            spec.channel,
            spec.task,
            headers=headers,
            extra={"intent": OPEN_INTENT, "room_id": room_id, "room": spec.to_dict()},
        )
        session = await self._agroup(spec.channel)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while not pending.done.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            if not session.is_initiator():  # an adopted session is read by its drain loop
                await asyncio.sleep(min(remaining, 0.02))
                continue
            try:
                msg = await self._call(
                    session.get_message_async(timeout=datetime.timedelta(seconds=min(remaining, self.timeout_s)))
                )
            except AgentCommError as exc:
                if is_idle_timeout(exc, self._sb):
                    continue
                raise
            self._take_result(msg)

    def _take_result(self, msg: Any) -> None:
        raw = msg.payload if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload).encode("utf-8")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if isinstance(envelope, dict) and envelope.get("kind") == "message" and envelope.get("intent") == RESULT_INTENT:
            self._on_result(envelope, member_of(msg.context.source_name, self._actor))

    def _on_result(self, envelope: dict[str, Any], member: str) -> None:
        pending = self._room_waits.get(str(envelope.get("room_id") or ""))
        if pending is not None:
            pending.set(member, str(envelope.get("payload") or ""))

    # ---- member

    def _room_frame(self, intent: str, envelope: dict[str, Any], member: str, sender: str, headers: dict[str, str]) -> None:
        """One room frame arrived on a channel this link is in."""
        if intent == RESULT_INTENT:
            self._on_result(envelope, member)
            return
        if intent == OPEN_INTENT:
            self._on_open(envelope, sender, headers)
        elif intent == POST_INTENT:
            self._on_post(envelope, member)

    def _is_me(self, member: str) -> bool:
        return member in {self.agent_id, self.local_agent_id}

    def _on_open(self, envelope: dict[str, Any], sender: str, headers: dict[str, str]) -> None:
        channel = str(envelope.get("channel") or "")
        try:
            spec = RoomSpec.from_dict(envelope.get("room"), channel=channel)
        except RoomError:
            logger.warning("slim room %r: the open message has unusable rules", channel)
            return
        if not (self.agent_id in spec.members or self.local_agent_id in spec.members):
            return  # the opener itself, or a link that is not a member
        me = self.agent_id if self.agent_id in spec.members else self.local_agent_id
        room_id = str(envelope.get("room_id") or "")
        room = Room(spec=spec, room_id=room_id, opener=sender, me=me, headers=dict(headers))
        room.round, room.round_started = 1, time.monotonic()
        with self._rooms_lock:
            self._rooms[channel] = room
            early = [row for row in self._early_posts.pop(channel, []) if row[0] == room_id]
        for _id, round_no, member, text, ok in early:
            room.add(round_no, member, text, ok=ok)
        threading.Thread(target=self._room_turn, args=(room, 1), daemon=True, name=f"room-{channel}-{me}").start()
        threading.Thread(target=self._room_watch, args=(room,), daemon=True, name=f"room-watch-{channel}-{me}").start()

    def _on_post(self, envelope: dict[str, Any], member: str) -> None:
        channel = str(envelope.get("channel") or "")
        room_id = str(envelope.get("room_id") or "")
        try:
            round_no = int(envelope.get("round"))
        except (TypeError, ValueError):
            return
        text, ok = str(envelope.get("payload") or ""), envelope.get("ok", True) is not False
        if self._is_me(member):
            return  # a member records its own post when it makes it
        with self._rooms_lock:
            room = self._rooms.get(channel)
            if room is None or room.room_id != room_id:
                early = self._early_posts.setdefault(channel, [])
                if len(early) < MAX_EARLY_POSTS:
                    early.append((room_id, round_no, member, text, ok))
                return
        if room.add(round_no, member, text, ok=ok):
            self._room_advance(room)

    def _room_turn(self, room: Room, round_no: int) -> None:
        """Run this member's turn for ``round_no``, post its answer, and move the room on if the round is full."""
        ok, text = self._run_channel_turn(
            room.turn_input(round_no), room.opener, room.headers, room.spec.channel
        )
        with room.lock:
            if room.closed:
                return
            room.add(round_no, room.me, text if ok else "", ok=ok)
        try:
            self._run(
                self._apublish(
                    room.spec.channel,
                    text if ok else "",
                    headers=room.headers,
                    extra={"intent": POST_INTENT, "room_id": room.room_id, "round": round_no, "ok": ok},
                )
            )
        except Exception:  # a link that stopped under the turn, or a node error: the others time out on this post
            logger.exception("slim room %r: a post could not be published", room.spec.channel)
        self._room_advance(room)

    def _room_advance(self, room: Room) -> None:
        """If the current round is complete, stop the room or start the next round."""
        with room.lock:
            if room.closed or room.round < 1:
                return
            round_no = room.round
            if not room.complete(round_no):
                return
            verdict = room.verdict(round_no)
            if verdict is None:
                room.round = round_no + 1
                room.round_started = time.monotonic()
            else:
                room.closed = True
        if verdict is None:
            threading.Thread(
                target=self._room_turn,
                args=(room, round_no + 1),
                daemon=True,
                name=f"room-{room.spec.channel}-{room.me}",
            ).start()
        else:
            self._room_finish(room, verdict, round_no)

    def _room_watch(self, room: Room) -> None:
        """Stop the room, naming the silent members, when a round waits longer than ``post_timeout_s``."""
        step = max(0.05, min(0.5, room.spec.post_timeout_s / 4))
        while not room.closed and not self._stopped:
            time.sleep(step)
            verdict = room.silent()
            if verdict is None:
                continue
            with room.lock:
                if room.closed:
                    return
                room.closed = True
                round_no = room.round
            self._room_finish(room, verdict, round_no)
            return

    def _room_finish(self, room: Room, verdict: Verdict, round_no: int) -> None:
        """The closer publishes the result; every other member just stops."""
        if room.me != room.spec.closer:
            return
        try:
            self._run(
                self._apublish(
                    room.spec.channel,
                    room.result_text(verdict, round_no),
                    headers=room.headers,
                    extra={"intent": RESULT_INTENT, "room_id": room.room_id, "verdict": verdict.kind},
                )
            )
        except Exception:
            logger.exception("slim room %r: the result could not be published", room.spec.channel)
