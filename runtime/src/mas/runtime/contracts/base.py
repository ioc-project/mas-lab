#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Contract taxonomy base classes and registry.

``ContractBase`` subclasses auto-register when they set a non-empty
``contract_id``.  Query them with ``ContractRegistry.get("tool")``.

Shipped ``CapabilityContract`` subclasses (importable from this package or
their modules) are ``ToolContract``, ``MemoryContract``, and
``RecorderContract``.  ``ContextContract`` is a ``BasePlugin``, not a
``CapabilityContract``.  ``OrchestrationContract`` and
``GovernanceContract`` are bases only — they have no shipped subclasses.

Nearby protocols live in other modules (``ControlContract``,
``DelegationContract``, ``DesignPatternContract``, ``EngineContract``).
They are not subclasses of the types in this file.

Plugin dependency metadata
──────────────────────────
Every plugin declares its contract relationships via class variables::

    class MyPlugin(BasePlugin):
        plugin_id    = "my_plugin@v1"
        implements   = ["tool"]
        requires     = ["recorder"]
        governed_by  = ["budget"]
"""

from abc import ABC
import re as _re
from typing import Any, ClassVar, Dict, List, Optional, Type


# ---------------------------------------------------------------------------
# @plugin decorator  (L3 — zero-ceremony plugin declaration)
# ---------------------------------------------------------------------------

def plugin(cls=None, *, plugin_id: Optional[str] = None,
           requires: Optional[List[str]] = None,
           governed_by: Optional[List[str]] = None):
    """Class decorator that auto-infers plugin metadata from the MRO.

    Removes the need for boilerplate ClassVars.  Usage::

        @plugin
        class MyTool(ToolContract):
            def get_name(self): return "my-tool"
            def execute(self, **kw): ...

        # Or with explicit overrides:
        @plugin(requires=["recorder"], governed_by=["budget"])
        class MyTool(ToolContract): ...

    What it infers:
    - ``plugin_id``: snake_case of class name + ``@v1`` (if not set)
    - ``implements``: walks MRO to find parent classes that have a
      non-None ``contract_id``; collects those IDs automatically
    """
    def _apply(cls):
        # --- plugin_id: auto-generate from class name if absent ---
        if not cls.__dict__.get("plugin_id"):
            name = cls.__name__
            # CamelCase → snake_case
            snake = _re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
            snake = _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", snake).lower()
            # Strip trailing _plugin / _tool suffixes for cleaner IDs
            for suffix in ("_plugin", "_tool"):
                if snake.endswith(suffix):
                    snake = snake[: -len(suffix)]
                    break
            cls.plugin_id = f"{snake}@v1"

        # --- implements: infer from contract hierarchy ---
        if not cls.__dict__.get("implements"):
            inferred = []
            for base in cls.__mro__:
                cid = base.__dict__.get("contract_id")
                if cid and cid not in inferred:
                    inferred.append(cid)
            if inferred:
                cls.implements = inferred

        # --- requires / governed_by: apply if given ---
        if requires is not None:
            cls.requires = requires
        if governed_by is not None:
            cls.governed_by = governed_by

        return cls

    if cls is not None:
        # Called as @plugin without arguments
        return _apply(cls)
    # Called as @plugin(...) with keyword arguments
    return _apply


class BasePlugin:
    """Plugin metadata and ``attach_agent``.

    Class variables (declare in subclasses):

    ``plugin_id`` : str
        Unique identifier, e.g. ``"my_tool@v1"``.

    ``implements`` : List[str]
        Contract IDs this plugin fulfills, e.g. ``["tool"]``.

    ``requires`` : List[str]
        Contract IDs this plugin needs injected, e.g. ``["recorder"]``.

    ``governed_by`` : List[str]
        Governance IDs applied to this plugin's operations,
        e.g. ``["budget"]``.
    """

    plugin_id:   ClassVar[Optional[str]]  = None
    implements:  ClassVar[List[str]]      = []
    requires:    ClassVar[List[str]]      = []
    governed_by: ClassVar[List[str]]      = []

    def attach_agent(self, agent: Any) -> None:
        """Attach agent metadata (agent_id, config) to this plugin instance.

        Safe to override in subclasses.  Do NOT pull ``recorder``, ``registry``,
        or other capability objects here — declare them via ``requires`` instead
        and inject them through the plugin chain.
        """
        self.agent = agent
        self.config = getattr(agent, "config", {})
        self.agent_id = getattr(agent, "agent_id", "unknown")


# ---------------------------------------------------------------------------
# Policy exception
# ---------------------------------------------------------------------------

class PolicyViolation(Exception):
    """Raised by a GovernanceContract when a request violates policy.

    Attributes
    ----------
    contract_id : str
        The contract that raised the violation.
    reason : str
        Human-readable explanation.
    details : dict
        Machine-readable context for audit logging.
    """

    def __init__(
        self,
        contract_id: str,
        reason: str,
        details: Optional[Dict] = None,
    ) -> None:
        self.contract_id = contract_id
        self.reason = reason
        self.details = details or {}
        super().__init__(f"[{contract_id}] PolicyViolation: {reason}")


# ---------------------------------------------------------------------------
# Contract registry
# ---------------------------------------------------------------------------

class ContractRegistry:
    """Central registry of all contract classes.

    Contracts register themselves automatically via ``ContractBase.__init_subclass__``.
    The runtime uses this registry to:
    - Validate plugin dependency declarations at startup
    - Generate contract dependency graphs
    - Auto-wire governance contracts to the appropriate hooks

    Do not instantiate — all methods are class-level.
    """

    _registry: ClassVar[Dict[str, Type["ContractBase"]]] = {}

    @classmethod
    def register(cls, contract_id: str, contract_cls: Type["ContractBase"]) -> None:
        """Explicitly register a contract class under a canonical ID."""
        if contract_id in cls._registry and cls._registry[contract_id] is not contract_cls:
            # Allow re-registration of same class (e.g. module reload); log on override
            import logging
            logging.getLogger(__name__).debug(
                "ContractRegistry: overriding '%s' (%s → %s)",
                contract_id,
                cls._registry[contract_id].__name__,
                contract_cls.__name__,
            )
        cls._registry[contract_id] = contract_cls

    @classmethod
    def get(cls, contract_id: str) -> Optional[Type["ContractBase"]]:
        """Return the contract class for a given ID, or None."""
        return cls._registry.get(contract_id)

    @classmethod
    def require(cls, contract_id: str) -> Type["ContractBase"]:
        """Return the contract class or raise KeyError."""
        if contract_id not in cls._registry:
            raise KeyError(
                f"Contract '{contract_id}' is not registered. "
                f"Known contracts: {sorted(cls._registry)}"
            )
        return cls._registry[contract_id]

    @classmethod
    def all_capability(cls) -> List[Type["CapabilityContract"]]:
        """Return all registered CapabilityContract subclasses."""
        return [c for c in cls._registry.values() if issubclass(c, CapabilityContract)]

    @classmethod
    def all_governance(cls) -> List[Type["GovernanceContract"]]:
        """Return all registered GovernanceContract subclasses."""
        return [c for c in cls._registry.values() if issubclass(c, GovernanceContract)]

    @classmethod
    def summary(cls) -> Dict[str, str]:
        """Return {contract_id: contract_type_label} for diagnostics."""
        result = {}
        for cid, ccls in sorted(cls._registry.items()):
            if issubclass(ccls, GovernanceContract):
                label = "governance"
            elif issubclass(ccls, CapabilityContract):
                label = "capability"
            elif issubclass(ccls, OrchestrationContract):
                label = "orchestration"
            else:
                label = "base"
            result[cid] = label
        return result


# ---------------------------------------------------------------------------
# ContractBase
# ---------------------------------------------------------------------------

class ContractBase(ABC):
    """Abstract root of the contract taxonomy.

    All contracts must declare a ``contract_id`` class variable. This ID is
    used by the plugin metadata system (``BasePlugin.implements``,
    ``BasePlugin.requires``) and by the ``ContractRegistry``.

    Subclasses auto-register into ``ContractRegistry`` when their
    ``contract_id`` is non-None and non-empty.
    """

    contract_id: ClassVar[Optional[str]] = None  # e.g. "tool", "memory", "recorder"

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Use cls.__dict__ (not getattr) so a plugin that inherits
        # contract_id="tool" from ToolContract does not re-register as "tool".
        cid = cls.__dict__.get("contract_id")
        if cid and not str(cid).startswith("_"):
            ContractRegistry.register(cid, cls)


# ---------------------------------------------------------------------------
# CapabilityContract
# ---------------------------------------------------------------------------

class CapabilityContract(ContractBase, BasePlugin):
    """Base for contracts that define a CAPABILITY boundary.

    Inherits from both ``ContractBase`` (contract_id auto-registration into
    ``ContractRegistry``) and ``BasePlugin`` (attach_agent, plugin metadata
    ClassVars).  All capability contract classes should inherit from this
    rather than directly from ``BasePlugin``.

    A capability contract describes WHAT the agent can access.  Plugins
    implement it to expose a resource.  The agent consumes it through the
    interface methods.

    Characteristic properties:
    - Has input/output methods (``call_tool``, ``emit``, …)
    - Does NOT enforce policy — it delegates that to GovernanceContract layers

    Lifecycle hook involvement:
    - Methods fire pre_* / post_* hooks at their execution boundary
    - The runtime assembles hook chains from plugin registrations
    """

    contract_id: ClassVar[Optional[str]] = None


# ---------------------------------------------------------------------------
# OrchestrationContract
# ---------------------------------------------------------------------------

class OrchestrationContract(ContractBase):
    """Base for contracts that define CONTROL FLOW over the agent/MAS topology.

    An orchestration contract answers HOW the runtime traverses agents —
    neither what an agent can access (``CapabilityContract``) nor whether an
    operation is permitted (``GovernanceContract``).

    Characteristic properties:
    - Controls invocation count, sequencing, and result stitching across agents
    - Does NOT expose resource methods (no call_tool / send / emit pattern)
    - Does NOT enforce policy (no ``check()`` method)
    - Multiple orchestration strategies are compositionally equivalent; any
      ``OrchestrationContract`` can be combined with any ``DesignPatternContract``
    """

    contract_id: ClassVar[Optional[str]] = None


# ---------------------------------------------------------------------------
# GovernanceContract
# ---------------------------------------------------------------------------

class GovernanceContract(ContractBase):
    """Base for contracts that enforce POLICY over capabilities.

    A governance contract answers "is this operation ALLOWED?".  It is applied
    by the runtime BEFORE capability contracts fire (and sometimes AFTER, for
    accounting).

    Characteristic properties:
    - check() raises ``PolicyViolation`` on denial; returns None on approval
    - Never "approves" explicitly — silence means permitted
    - Counters/state are MONOTONICALLY CONSUMED (budget, call counts)
    - Applied as cross-cutting concerns via hook composition

    Adding or removing a governance contract is topology-neutral: the Mealy
    machine structure is unchanged; only the guards on transitions are
    extended.

    Subclasses MUST implement ``check()``.
    """

    contract_id: ClassVar[Optional[str]] = None

    def check(self, operation: str, context: Dict) -> None:
        """Check whether ``operation`` with ``context`` is allowed.

        Args:
            operation: The operation being requested (e.g. "llm_call",
                       "tool_call:filesystem__read_file", "write:/etc/passwd").
            context:   Operation context dict (model, path, cost estimate…).

        Raises:
            PolicyViolation: if the operation is denied.

        Returns:
            None (silence = permitted).
        """
        pass  # default: permit all (override in concrete governance contracts)
