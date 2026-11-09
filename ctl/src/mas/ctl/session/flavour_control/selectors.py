#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Which agents a Control row attaches to."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mas.ctl.overrides.parser import parse_agent_target
from mas.ctl.session.flavour_control.error import ControlError

_AGENT_ALIASES = frozenset({"$all", "$entry", "$delegates", "$not-entry"})
_PATH_PREFIXES = ("agent.", "mas.", "app.", "workflow.", "agent:", "mas:", "app:", "workflow:")


def split_selector_parts(text: str) -> list[str]:
    """Comma-split a selector, ignoring commas inside ``[]`` (``.mas.agent[a],.mas.agent[b]``)."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            part = text[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    part = text[start:].strip()
    if part:
        parts.append(part)
    return parts


def looks_like_agent_path(text: str) -> bool:
    """True when *text* is an ``--override`` agent path, not a bare id or ``$`` alias."""
    stripped = text.strip()
    if stripped.startswith(".") or "[" in stripped:
        return True
    lowered = stripped.lower()
    return lowered.startswith(_PATH_PREFIXES)


def parse_selector_path(text: str) -> tuple[bool, str | None]:
    try:
        return parse_agent_target(text)
    except ValueError as exc:
        raise ControlError(str(exc)) from exc


def path_picks_agent(selector: str, agent_id: str) -> bool | None:
    """True/False when *selector* is an ``--override`` agent path; None when it is a bare id."""
    text = selector.strip()
    if not looks_like_agent_path(text):
        return None
    wildcard, name = parse_selector_path(text)
    return True if wildcard else agent_id == name


def agent_matches(
    selector: Any,
    *,
    agent_id: str,
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
) -> bool:
    """Whether ``selector`` includes ``agent_id``.

    Overlay aliases: ``$all`` (default), ``$entry``, ``$not-entry``, ``$delegates``.
    Ids: one name or a list. Agent path: the same targeting as ``--override``
    (``.mas.agent[id]`` / ``.mas.agent[*]``).
    """
    if selector in (None, "$all"):
        return True
    if selector == "$entry":
        return bool(entry_id) and agent_id == entry_id
    if selector == "$not-entry":
        return bool(entry_id) and agent_id != entry_id
    if selector == "$delegates":
        return agent_id in set(delegate_ids)
    if isinstance(selector, list):
        return any(
            agent_matches(item, agent_id=agent_id, entry_id=entry_id, delegate_ids=delegate_ids) for item in selector
        )
    text = str(selector)
    if text == "*":
        raise ControlError("Control agents '*' is not a selector; use $all or .mas.agent[*]")
    picked = path_picks_agent(text, agent_id)
    if picked is not None:
        return picked
    return agent_id == text


def parse_control_option(text: str) -> tuple[str, Any]:
    """``--control REF[@SELECTOR]`` as ``(ref, agents)``.

    SELECTOR is ``$all``, ``$entry``, ``$not-entry``, ``$delegates``, agent ids,
    or the same agent path as ``--override`` (``.mas.agent[id]`` / ``.mas.agent[*]``).
    """
    ref, sep, selector = text.strip().rpartition("@")
    if not sep or not ref or "/" in selector or "\\" in selector or selector.endswith((".yaml", ".yml")):
        return text.strip(), None
    ids = split_selector_parts(selector)
    if not ids:
        raise ControlError(f"--control {text!r}: the selector after @ is empty")
    if any(item == "*" for item in ids):
        raise ControlError(f"--control {text!r}: '*' is not a selector; use $all or .mas.agent[*]")
    if len(ids) == 1 and ids[0] in _AGENT_ALIASES:
        return ref, ids[0]
    if any(item.startswith("$") for item in ids):
        raise ControlError(
            f"--control {text!r}: a selector is $all, $entry, $not-entry, $delegates, "
            "agent ids, or the same agent path as --override (.mas.agent[id] / .mas.agent[*])"
        )
    for item in ids:
        if looks_like_agent_path(item):
            parse_selector_path(item)
    return ref, ids if len(ids) > 1 else ids[0]
