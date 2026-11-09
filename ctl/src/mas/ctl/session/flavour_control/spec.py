#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Control spec keys.

Observability and governance live on Control; each is a set of plugins named
by id. A name must be registered, under a type that list accepts, or the Control is refused. Same-transition
order among governance plugins is each attachment's ``priority``.
Flavour ``spec.control`` is the list of Control refs a Flavour attaches.
Leftover ``spec.plugins``, ``spec.control``, and ``spec.impair`` on a Control
document are not plugin lists.
"""

GOVERNANCE = "governance"
OBSERVABILITY = "observability"
CONTROL = "control"
PLUGINS = "plugins"
IMPAIR = "impair"
LEFTOVER_SPEC_KEYS = (CONTROL, PLUGINS, IMPAIR)


def leftover_pipeline_error(label: str, key: str) -> str:
    return f"Control {label} spec.{key} is leftover; the lists are spec.observability and spec.governance"


def leftover_spec_key(spec: dict | None) -> str | None:
    body = spec if isinstance(spec, dict) else {}
    for key in LEFTOVER_SPEC_KEYS:
        if key in body:
            return key
    return None


def empty_list_silences_warning(agent_id: str, key: str) -> str:
    return f"agent {agent_id or '?'!r}: a Control names an empty {key} list, which replaces the agent's own {key} list"


def replaces_warning(label: str, key: str) -> str:
    return f"Control {label} replaces spec.{key}"


def is_replaces_warning(message: str, key: str) -> bool:
    return message.endswith(f"replaces spec.{key}")
