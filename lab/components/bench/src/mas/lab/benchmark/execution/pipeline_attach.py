#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""CLI pipeline attachment for benchmark experiments."""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_LEVEL_ALIASES = {
    "application": "experiment",  # deprecated pipeline level
    "test": "item",  # deprecated; CLI uses --item
}
_VALID_LEVELS = frozenset({"run", "item", "scenario", "experiment"})
_VALID_PHASES = frozenset({"pre", "post"})
_EXPERIMENT_PHASE_LEVELS = frozenset({"experiment"})


def parse_pipeline_attachment(raw: str) -> Tuple[str, str, str]:
    """Parse ``LEVEL:PHASE:REF`` or ``LEVEL:REF`` (phase defaults to post).

    *REF* may contain colons (e.g. ``kg:pipelines/native-to-kg.yaml``).
    Returns ``(level, phase, ref)``.
    """
    text = str(raw).strip()
    if not text:
        raise ValueError("empty pipeline attachment")
    parts = text.split(":")
    if len(parts) < 2:
        raise ValueError(
            f"invalid pipeline attachment {raw!r} — expected LEVEL:REF or LEVEL:PHASE:REF"
        )
    level = parts[0].strip().lower()
    level = _LEVEL_ALIASES.get(level, level)
    if level not in _VALID_LEVELS:
        raise ValueError(
            f"invalid pipeline level {parts[0]!r} — expected one of "
            f"{sorted(_VALID_LEVELS)} (application aliases experiment; test aliases item)"
        )
    if len(parts) >= 3 and parts[1].strip().lower() in _VALID_PHASES:
        phase = parts[1].strip().lower()
        ref = ":".join(parts[2:]).strip()
    else:
        phase = "post"
        ref = ":".join(parts[1:]).strip()
    if not ref:
        raise ValueError(f"empty pipeline ref in attachment {raw!r}")
    return level, phase, ref


def merge_pipeline_attachments(
    data: Dict[str, Any],
    attachments: Optional[list],
) -> Dict[str, Any]:
    """Append CLI pipeline refs to the experiment dict (mutates *data* in place).

    Each attachment becomes a normal ``{ref: ...}`` entry under
    ``experiment.<level>.<phase>``, as if it were declared in the YAML.
    """
    if not attachments:
        return data

    exp = _experiment_block(data)
    for item in attachments:
        level, phase, ref = parse_pipeline_attachment(item)
        if level in _EXPERIMENT_PHASE_LEVELS:
            phase_list = exp.setdefault(phase, [])
            if not isinstance(phase_list, list):
                raise ValueError(f"experiment.{phase} must be a list")
            phase_list.append({"ref": ref})
            logger.info("CLI pipeline merged: experiment.%s ← %r", phase, ref)
            continue
        level_block = exp.setdefault(level, {})
        if not isinstance(level_block, dict):
            raise ValueError(f"experiment.{level} must be a mapping")
        phase_list = level_block.setdefault(phase, [])
        if not isinstance(phase_list, list):
            raise ValueError(f"experiment.{level}.{phase} must be a list")
        phase_list.append({"ref": ref})
        logger.info("CLI pipeline merged: experiment.%s.%s ← %r", level, phase, ref)
    return data


def _experiment_block(data: Dict[str, Any]) -> Dict[str, Any]:
    exp = data.get("experiment")
    if exp is None:
        if isinstance(data, dict) and (
            data.get("application") is not None or data.get("applications") is not None
        ):
            return data
        raise ValueError("experiment manifest has no experiment: block")
    if not isinstance(exp, dict):
        raise ValueError("experiment block must be a mapping")
    return exp
