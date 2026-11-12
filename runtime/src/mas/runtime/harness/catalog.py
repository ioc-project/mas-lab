#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Closed boundary slots, kernel ops, and a DAG of harness compositions.

Layer 0 (kernel ops) is frozen. Layer 1 (boundary plugin *types*) is a
closed set — a new type is a new envelope slot, which is how you get 14
hooks. Layer 2 (harness) is an open DAG whose leaves must be kernel ops
or boundary slots. Layer 3 (library / lab / product) is open and never
stepped by the envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

PluginLayer = Literal["kernel", "boundary", "harness", "library", "product"]

KERNEL_OPS = frozenset(
    {
        "materialize",
        "teardown",
        "step",
        "spawn",
        "snapshot",
        "persist",
        "navigate",
        "pause",
        "resume",
        "steer",
        "undo",
        "spec_delta",
        "branch",
        "promote",
    }
)

# Envelope / spec slots. Adding one is a kernel change, not a plugin.
BOUNDARY_SLOTS = frozenset(
    {
        "design_pattern",
        "governance",
        "context_manager",
        "summarizer",
        "assembler",
        "observability",
        "memory",
        "tool_provider",
        "llm_provider",
        "agent_comm",
        "system_plugin",
        "hitl_contract",
        "user_io_contract",
        "engine_tool_provider",
    }
)

LIBRARY_TYPES = frozenset(
    {
        "skill_catalog",
        "skill_tools",
        "skill_shell",
        "skill_impl",
        "step",
        "codec",
        "artifact",
        "agent_expose",
        "webserver",
        "harness",
        "related_state",
        "execute_sandbox",
        "circuit_breaker",
        "retry",
        "layer",
        "stack_layer",
        "infra_middleware",
        "hitl_responder",
        "control_protocol",
        "checkpoint_store",
        "eval_metric",
    }
)

# Spec slots: the plugin types an agent spec key names.
SPEC_IDENTITY_TYPES = frozenset(
    {
        "design_pattern",
        "context_manager",
        "summarizer",
        "assembler",
        "memory",
        "tool_provider",
        "llm_provider",
        "agent_comm",
        "engine_tool_provider",
        "hitl_contract",
        "user_io_contract",
        "governance",
        "observability",
        "system_plugin",
    }
)


class UnknownBoundarySlotError(ValueError):
    """Tried to register a new envelope slot. That is a kernel change."""


class IllegalHarnessLeafError(ValueError):
    """A harness composition named a leaf that is not an op, slot, or harness."""


class CyclicHarnessError(ValueError):
    """Harness ``requires`` formed a cycle."""


def classify_plugin_type(plugin_type: str) -> PluginLayer:
    name = str(plugin_type or "").strip().lower().replace("-", "_")
    if name in KERNEL_OPS:
        return "kernel"
    if name in BOUNDARY_SLOTS:
        return "boundary"
    if name == "harness":
        return "harness"
    if name in LIBRARY_TYPES or name.startswith("skill_"):
        return "library"
    if name in {"step", "codec", "artifact"}:
        return "product"
    return "library"


def assert_boundary_slot(plugin_type: str) -> str:
    name = str(plugin_type or "").strip().lower().replace("-", "_")
    if name not in BOUNDARY_SLOTS:
        raise UnknownBoundarySlotError(
            f"{plugin_type!r} is not a boundary slot. The envelope alphabet is "
            f"closed ({sorted(BOUNDARY_SLOTS)}). A new slot is a 15th hook. "
            "Register a harness composition whose leaves are kernel ops instead."
        )
    return name


@dataclass(frozen=True)
class HarnessComposition:
    """Layer-2 plugin: named combination of kernel ops and/or other harnesses."""

    name: str
    uses: tuple[str, ...]
    requires: tuple[str, ...] = ()
    description: str = ""
    layer: PluginLayer = "harness"


@dataclass
class HarnessCatalog:
    """Validated DAG of harness compositions."""

    _items: dict[str, HarnessComposition] = field(default_factory=dict)

    def register(self, item: HarnessComposition) -> HarnessComposition:
        self._check_leaves(item)
        self._items[item.name] = item
        self._assert_acyclic()
        return item

    def get(self, name: str) -> HarnessComposition:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"unknown harness composition {name!r}") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def names(self) -> list[str]:
        return sorted(self._items)

    def depends(self, name: str) -> list[str]:
        """Transitive ``requires`` (dependencies first)."""
        seen: list[str] = []
        visiting: set[str] = set()

        def walk(node: str) -> None:
            if node in seen:
                return
            if node in visiting:
                raise CyclicHarnessError(f"cycle involving {node!r}")
            visiting.add(node)
            item = self.get(node)
            for req in item.requires:
                walk(req)
            visiting.remove(node)
            seen.append(node)

        walk(name)
        return seen

    def uses_closed(self, name: str) -> frozenset[str]:
        """All kernel ops / boundary slots reachable from ``name``."""
        ops: set[str] = set()
        for node in self.depends(name):
            item = self.get(node)
            for leaf in item.uses:
                if leaf in KERNEL_OPS or leaf in BOUNDARY_SLOTS:
                    ops.add(leaf)
                elif leaf in self._items:
                    ops.update(self.uses_closed(leaf))
        return frozenset(ops)

    def _check_leaves(self, item: HarnessComposition) -> None:
        for leaf in item.uses:
            if leaf in KERNEL_OPS or leaf in BOUNDARY_SLOTS or leaf in self._items:
                continue
            raise IllegalHarnessLeafError(
                f"harness {item.name!r} names {leaf!r}, which is not a kernel "
                f"op, boundary slot, or harness composition. Leaves must be "
                f"in KERNEL_OPS or BOUNDARY_SLOTS (got a fourth path)."
            )

    def _assert_acyclic(self) -> None:
        for name, item in list(self._items.items()):
            if any(
                req not in self._items and req not in KERNEL_OPS and req not in BOUNDARY_SLOTS for req in item.requires
            ):
                continue
            self.depends(name)


def builtin_compositions() -> tuple[HarnessComposition, ...]:
    return (
        HarnessComposition(
            name="react",
            uses=("step", "governance"),
            description="One agent, tools, envelope. The default loop.",
        ),
        HarnessComposition(
            name="subagents",
            uses=("spawn", "step", "teardown"),
            requires=("react",),
            description="Child instance plus parent id; not a second runtime.",
        ),
        HarnessComposition(
            name="recovery",
            uses=("snapshot", "undo", "steer"),
            description="OpenClaw-style: snapshot on decision, backtrack, steer.",
        ),
        HarnessComposition(
            name="whatif",
            uses=("snapshot", "branch"),
            description="N sibling branches from one node; discard by default.",
        ),
        HarnessComposition(
            name="detective",
            uses=("pause", "navigate", "snapshot", "branch", "steer"),
            requires=("whatif",),
            description="Stop, walk the tree, fork an investigation, put back.",
        ),
        HarnessComposition(
            name="plan_mode",
            uses=("spec_delta", "pause"),
            requires=("react",),
            description="Disable write tools + HITL; live spec, not a new loop.",
        ),
        HarnessComposition(
            name="evolution",
            uses=("snapshot", "branch", "spec_delta", "promote", "persist"),
            requires=("whatif", "detective"),
            description="Population = N branches × spec deltas × inspect/promote.",
        ),
    )


def default_catalog(extra: Iterable[HarnessComposition] = ()) -> HarnessCatalog:
    catalog = HarnessCatalog()
    for item in (*builtin_compositions(), *extra):
        catalog.register(item)
    # Second pass: forward requires now resolve.
    for item in list(catalog._items.values()):
        for req in item.requires:
            if req not in catalog._items and req not in KERNEL_OPS and req not in BOUNDARY_SLOTS:
                raise IllegalHarnessLeafError(f"harness {item.name!r} requires unknown {req!r}")
        catalog._assert_acyclic()
    return catalog
