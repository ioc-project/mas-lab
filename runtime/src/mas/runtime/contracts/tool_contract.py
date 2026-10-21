#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tool Contract - External tool execution interface.

Paper Reference: Section 4.2 - "Tool Contract (Invocation + Result)"

The Tool Contract guarantees:
1. Uniform invocation schema across all frameworks
2. Idempotency markers for safe retries
3. Structured result types with error mapping
4. Governance enforcement point (primary checkpoint)

This is the PRIMARY governance boundary where:
- Rate limiters operate
- Authorization checks run
- Cost controls apply
- Tool allowlists enforce

Hook Usage:
- pre_tool_call: Validate, authorize, log BEFORE tool execution
- post_tool_call: Process results, filter, audit AFTER tool execution

Implementations live in library packages (``ToolContract`` subclasses such as
``SkillToolsPlugin`` and the sample tools under ``library-samples/tools/``).

Pydantic Input pattern (recommended for new tools)
--------------------------------------------------
Declare a ``class Input(BaseModel)`` inside the tool class.  The runtime
picks it up automatically:

  1. ``get_parameters_schema()``   → JSON Schema derived from ``Input.model_json_schema()``
  2. ``call_tool()``               → ``Input(**arguments)`` validates args before ``execute()``
  3. tool-server ``inputSchema``          → identical JSON Schema served to any tool-server client

Example::

    from pydantic import BaseModel, Field
    from mas.runtime.contracts import ToolContract

    class CheckHealthTool(ToolContract):
        class Input(BaseModel):
            service_name: str = Field(..., description="Service name or URL")
            timeout_seconds: int = Field(5, ge=1, le=300,
                                         description="Max wait time in seconds")

        def get_name(self) -> str:
            return "check-service-health"

        def get_description(self) -> str:
            return (
                "Check the health of a running service. "
                "Returns {healthy: bool, latency_ms: float}."
            )

        def execute(self, **kwargs) -> dict:
            args = self.Input(**kwargs)    # validated; Pydantic raises on bad types
            # ... call actual service ...
            return {"healthy": True, "latency_ms": 42.0}

Legacy API (JSON Schema in get_parameters_schema) continues to work unchanged.
"""

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional

from mas.runtime.contracts.base import CapabilityContract  # L3->L3
from pydantic import BaseModel, BeforeValidator, ConfigDict, create_model

logger = logging.getLogger(__name__)


@dataclass
class ToolEvent:
    """Canonical runtime event emitted by a stream or session tool."""

    name: str
    payload: Any = None
    sequence: Optional[int] = None
    timestamp: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": self.name, "payload": self.payload}
        if self.sequence is not None:
            data["sequence"] = self.sequence
        if self.timestamp is not None:
            data["timestamp"] = self.timestamp
        if self.metadata:
            data["metadata"] = self.metadata
        return data


@dataclass
class ToolResultEnvelope:
    """Normalized tool result envelope across inline, stream, and session modes.

    Daily-ops fields: ``status``, ``result``, ``is_error``. Everything else is
    optional and omitted from :meth:`to_dict` when unset so existing callers
    keep seeing the same payload.
    """

    result_mode: str = "inline"
    execution_mode: str = "sync"
    status: str = "ok"
    result: Any = None
    events: List[ToolEvent] = field(default_factory=list)
    session_id: Optional[str] = None
    session_status: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    content: List[Dict[str, Any]] = field(default_factory=list)
    structured_content: Any = None
    result_type: str = "complete"
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def inline(cls, result: Any, *, execution_mode: str = "sync", is_error: bool = False) -> "ToolResultEnvelope":
        return cls(result_mode="inline", execution_mode=execution_mode, result=result, is_error=is_error)

    @classmethod
    def stream(
        cls,
        events: List[ToolEvent],
        *,
        result: Any = None,
        execution_mode: str = "async",
        is_error: bool = False,
    ) -> "ToolResultEnvelope":
        return cls(
            result_mode="stream",
            execution_mode=execution_mode,
            result=result,
            events=list(events),
            is_error=is_error,
        )

    @classmethod
    def session(
        cls,
        session_id: str,
        *,
        events: Optional[List[ToolEvent]] = None,
        result: Any = None,
        execution_mode: str = "realtime",
        session_status: str = "open",
        is_error: bool = False,
    ) -> "ToolResultEnvelope":
        return cls(
            result_mode="session",
            execution_mode=execution_mode,
            result=result,
            events=list(events or []),
            session_id=session_id,
            session_status=session_status,
            is_error=is_error,
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "status": self.status,
            "result_mode": self.result_mode,
            "execution_mode": self.execution_mode,
            "is_error": self.is_error,
        }
        if self.result is not None:
            data["result"] = self.result
        if self.events:
            data["events"] = [event.to_dict() for event in self.events]
        if self.session_id is not None:
            data["session_id"] = self.session_id
        if self.session_status is not None:
            data["session_status"] = self.session_status
        if self.metadata:
            data["metadata"] = self.metadata
        if self.content:
            data["content"] = list(self.content)
        if self.structured_content is not None:
            data["structured_content"] = self.structured_content
        if self.result_type and self.result_type != "complete":
            data["result_type"] = self.result_type
        if self.meta:
            data["meta"] = dict(self.meta)
        return data


def invoke_call_tool(
    fn: Any,
    tool_name: str,
    arguments: Dict[str, Any],
    **options: Any,
) -> Any:
    """Call ``fn(tool_name, arguments, ...)`` dropping options the callee rejects.

    Optional protocol options (timeout, progress, elicitation, ``ctx``,
    ``user``, …) are passed only when the callable declares them or takes
    ``**kwargs``.
    """
    if not options:
        return fn(tool_name, arguments)
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(tool_name, arguments)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return fn(tool_name, arguments, **options)
    accepted = {name: value for name, value in options.items() if name in signature.parameters}
    return fn(tool_name, arguments, **accepted)


def overlay_tool_advertise(runtime_spec: Dict[str, Any], yaml_contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a ``kind: Tool`` contract dict onto a runtime ``list_tools`` entry.

    ``name`` / ``description`` / ``parameters`` keep the historical YAML-wins
    rule. Additional optional attributes are copied only when set.
    """
    merged = dict(runtime_spec)
    if not yaml_contract:
        return merged
    if "name" in yaml_contract:
        merged["name"] = yaml_contract.get("name", merged.get("name"))
    if "description" in yaml_contract:
        merged["description"] = yaml_contract.get("description", merged.get("description", ""))
    if "parameters" in yaml_contract:
        merged["parameters"] = yaml_contract.get("parameters", merged.get("parameters", {}))
    skip = {"name", "description", "parameters"}
    for key, value in yaml_contract.items():
        if key in skip or value is None or value == "" or value == [] or value == {}:
            continue
        merged[key] = value
    return merged


def is_tool_event_stream(value: Any) -> bool:
    """Return True when *value* is a generator-like stream of events."""
    if isinstance(value, (str, bytes, dict, ToolEvent, ToolResultEnvelope)):
        return False
    return inspect.isgenerator(value) or inspect.isasyncgen(value)


def coerce_tool_event(value: Any, sequence: Optional[int] = None) -> ToolEvent:
    """Normalize arbitrary event payloads to ``ToolEvent``."""
    if isinstance(value, ToolEvent):
        event = value
    elif isinstance(value, dict) and "name" in value:
        event = ToolEvent(
            name=str(value.get("name", "event")),
            payload=value.get("payload"),
            sequence=value.get("sequence"),
            timestamp=value.get("timestamp"),
            metadata=dict(value.get("metadata") or {}),
        )
    else:
        event = ToolEvent(name="event", payload=value)
    if event.sequence is None and sequence is not None:
        event.sequence = sequence
    return event


# ---------------------------------------------------------------------------
# Argument coercion helpers (used by ToolContract.call_tool)
# ---------------------------------------------------------------------------


def _to_json_str(v: Any) -> str:
    """Coerce any value to a string; dicts/lists become JSON, others use str()."""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


_JSON_STR = Annotated[str, BeforeValidator(_to_json_str)]

_JSONSCHEMA_TO_PY: Dict[str, Any] = {
    "string": _JSON_STR,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_args_model(schema: Dict[str, Any]) -> "type[BaseModel] | None":
    """Build a Pydantic model from a JSON Schema *object* spec.

    Returns ``None`` when no ``properties`` are defined (no coercion needed).
    Pydantic will coerce incoming values to the declared types, raising a
    ``ValidationError`` on genuine type mismatches (e.g. list where int expected).
    String fields accept any scalar/dict/list and serialise to JSON.
    """
    props = schema.get("properties")
    if not props:
        return None
    required = set(schema.get("required", []))
    fields: Dict[str, Any] = {}
    for name, spec in props.items():
        py_type = _JSONSCHEMA_TO_PY.get(spec.get("type", ""), Any)
        if name in required:
            fields[name] = (py_type, ...)
        else:
            fields[name] = (Optional[py_type], None)
    return create_model("_ToolArgs", __config__=ConfigDict(extra="allow"), **fields)


class ToolContract(CapabilityContract):
    contract_id = "tool"
    """Base interface for tool execution using hooks.

    Two implementation styles are supported:

    **Style A — Pydantic Input (recommended)**::

        class MyTool(ToolContract):
            class Input(BaseModel):
                param: str = Field(..., description="...")

            def get_name(self) -> str: return "my-tool"
            def get_description(self) -> str: return "..."
            def execute(self, **kwargs) -> dict:
                args = self.Input(**kwargs)   # validated by Pydantic
                ...

    ``get_parameters_schema()`` is auto-derived from ``Input.model_json_schema()``.
    No need to write JSON Schema by hand.

    **Style B — Legacy JSON Schema**::

        class MyTool(ToolContract):
            def get_name(self) -> str: return "my-tool"
            def get_parameters_schema(self) -> dict:
                return {"type": "object", "properties": {"param": {"type": "string"}}}
            def execute(self, **kwargs) -> dict: ...

    For composite providers (multi-tool), override ``list_tools()`` and
    ``call_tool()`` directly.

    ``call_tool(tool_name, arguments)`` is the required invocation. Optional
    protocol options are keyword-only / ``**kwargs`` (timeout, progress,
    elicitation, request meta). Advertise extras (title, output schema,
    annotations, icons, …) are optional on ``list_tools()`` entries and on
    ``kind: Tool`` YAML. When unset, ``list_tools`` emits
    ``{name, description, parameters}`` and ``call_tool`` runs
    ``execute(**arguments)``.
    """

    # ------------------------------------------------------------------
    # Single-tool helper API (subclasses implementing one tool)
    # ------------------------------------------------------------------

    def get_name(self) -> str:  # noqa: D401
        raise NotImplementedError(f"{self.__class__.__name__} must implement get_name()")

    def get_description(self) -> str:
        return ""

    def get_parameters_schema(self) -> Dict[str, Any]:
        """Return JSON Schema for the tool's input parameters.

        When the subclass defines ``class Input(BaseModel)``, this default
        implementation auto-derives the schema from
        ``Input.model_json_schema()`` — no need to override.
        Override explicitly only for legacy JSON-Schema-only tools.
        """
        input_cls = getattr(self, "Input", None)
        if input_cls is not None and hasattr(input_cls, "model_json_schema"):
            schema = input_cls.model_json_schema()
            # model_json_schema() may add $defs — strip for LLM clarity
            schema.pop("$defs", None)
            schema.pop("title", None)
            return schema
        return {}

    def get_title(self) -> Optional[str]:
        """Optional display name distinct from the call name."""
        return None

    def get_output_schema(self) -> Dict[str, Any]:
        """Optional JSON Schema for the tool result. Empty = unspecified."""
        return {}

    def get_icons(self) -> List[Dict[str, Any]]:
        """Optional icon descriptors ``{src, mime_type, sizes, theme}``."""
        return []

    def get_task_support(self) -> Optional[str]:
        """Optional task support: ``forbidden`` | ``optional`` | ``required``."""
        return None

    def get_meta(self) -> Dict[str, Any]:
        """Optional advertise ``_meta``. Empty = unspecified."""
        return {}

    def get_semantics(self) -> Dict[str, Any]:
        """Optional interpretation for traces and metrics. Empty = unspecified.

        Shape: ``{concept, op?, subject_arg?}``. Composite providers that
        advertise several call names set ``semantics`` on each ``list_tools``
        entry instead of this getter.
        """
        return {}

    def is_idempotent(self) -> bool:
        return False

    def is_read_only(self) -> Optional[bool]:
        """Optional hint. ``None`` means unspecified (not ``False``)."""
        return None

    def is_destructive(self) -> Optional[bool]:
        """Optional hint. ``None`` means unspecified (not ``False``)."""
        return None

    def is_open_world(self) -> Optional[bool]:
        """Optional hint. ``None`` means unspecified (not ``False``)."""
        return None

    def get_timeout_seconds(self) -> Optional[float]:
        """Optional per-tool default timeout. ``None`` = caller/provider default."""
        return None

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError(f"{self.__class__.__name__} must implement execute()")

    def _advertise_entry(self, name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Build a ``list_tools`` entry, emitting optional attributes only when set."""
        entry: Dict[str, Any] = {"name": name, "description": description, "parameters": parameters}
        title = self.get_title()
        if title:
            entry["title"] = title
        output = self.get_output_schema()
        if output:
            entry["output_schema"] = output
        icons = self.get_icons()
        if icons:
            entry["icons"] = icons
        task_support = self.get_task_support()
        if task_support:
            entry["task_support"] = task_support
        meta = self.get_meta()
        if meta:
            entry["meta"] = meta
        semantics = self.get_semantics()
        if semantics:
            entry["semantics"] = dict(semantics)
        if self.is_idempotent():
            entry["idempotent"] = True
        read_only = self.is_read_only()
        if read_only is not None:
            entry["read_only"] = read_only
        destructive = self.is_destructive()
        if destructive is not None:
            entry["destructive"] = destructive
        open_world = self.is_open_world()
        if open_world is not None:
            entry["open_world"] = open_world
        timeout = self.get_timeout_seconds()
        if timeout is not None:
            entry["timeout_seconds"] = timeout
        annotations: Dict[str, Any] = {}
        if entry.get("idempotent"):
            annotations["idempotentHint"] = True
        if read_only is True:
            annotations["readOnlyHint"] = True
        elif read_only is False:
            annotations["readOnlyHint"] = False
        if destructive is True:
            annotations["destructiveHint"] = True
        elif destructive is False:
            annotations["destructiveHint"] = False
        if open_world is True:
            annotations["openWorldHint"] = True
        elif open_world is False:
            annotations["openWorldHint"] = False
        if annotations:
            entry["annotations"] = annotations
        return entry

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        """Execute a tool by name with ``arguments``.

        This is the basic invocation. Daily callers pass only
        ``(tool_name, arguments)``. Optional protocol options (timeout,
        progress_callback, cancel_event, meta, input_responses, request_state,
        allow_input_required, allow_claimed, ctx, user) are ignored by this
        default implementation; plugins that speak a richer wire protocol
        override and consume them.
        """
        try:
            own = self.get_name()
        except NotImplementedError:
            own = None

        if own and tool_name == own:
            try:
                _schema = self.get_parameters_schema()
            except (NotImplementedError, AttributeError):
                _schema = {}
            _model = _build_args_model(_schema)
            if _model:
                _validated = _model.model_validate(arguments)
                arguments = _validated.model_dump(exclude_unset=True)
            return self.execute(**arguments)

        raise NotImplementedError(
            f"{self.__class__.__name__} must implement call_tool(). "
            "This method is invoked within the hook pipeline after pre_tool_call passes."
        )

    def list_tools(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """List available tools with metadata.

        Required keys: ``name``, ``description``, ``parameters``. Optional keys
        (title, output_schema, annotations/hints, icons, task_support, meta,
        semantics, timeout_seconds) are omitted when unspecified. ``kwargs``
        such as ``cursor`` / ``ctx`` are ignored here; composite providers
        may use them.
        """
        try:
            name = self.get_name()
        except NotImplementedError:
            raise NotImplementedError(f"{self.__class__.__name__} must implement list_tools()") from None
        try:
            desc = self.get_description()
        except (NotImplementedError, AttributeError):
            desc = ""
        try:
            params = self.get_parameters_schema()
        except (NotImplementedError, AttributeError):
            params = {}
        return [self._advertise_entry(name, desc, params)]

    def to_tool_spec(self) -> Dict[str, Any]:
        """Return the tool-server-compatible tool spec dict.

        Convenience wrapper around ``list_tools()[0]``. Required keys are
        ``name``, ``description``, ``inputSchema``. Optional advertise fields
        are copied when present.
        """
        tools = self.list_tools()
        if not tools:
            raise ValueError(f"{self.__class__.__name__}.list_tools() returned empty list")
        t = tools[0]
        spec: Dict[str, Any] = {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "inputSchema": t.get("parameters", {}),
        }
        for src, dest in (
            ("title", "title"),
            ("output_schema", "outputSchema"),
            ("annotations", "annotations"),
            ("icons", "icons"),
            ("task_support", "task_support"),
            ("meta", "_meta"),
            ("idempotent", "idempotent"),
            ("read_only", "read_only"),
            ("destructive", "destructive"),
            ("open_world", "open_world"),
            ("timeout_seconds", "timeout_seconds"),
        ):
            if src in t:
                spec[dest] = t[src]
        return spec

    def get_execution_mode(self, tool_name: Optional[str] = None) -> str:
        """Return the declared execution mode for a tool when available."""
        specs = self.list_tools()
        if tool_name is not None:
            for spec in specs:
                if spec.get("name") == tool_name:
                    return str(spec.get("execution_mode") or "sync")
        return "sync"

    def get_result_mode(self, tool_name: Optional[str] = None) -> str:
        """Return the declared result mode for a tool when available."""
        specs = self.list_tools()
        if tool_name is not None:
            for spec in specs:
                if spec.get("name") == tool_name:
                    return str(spec.get("result_mode") or "inline")
        return "inline"

    # Hook Implementations for Runtime Integration

    def on_collect_tools(self, **kwargs) -> List[Dict[str, Any]]:
        """Hook: Collect tools from this plugin."""
        try:
            return self.list_tools(**kwargs)
        except TypeError:
            return self.list_tools()

    def on_execute_tool(self, tool_name: str, arguments: Dict[str, Any], **kwargs) -> Dict[str, Any] | None:
        """Hook: Execute tool if this plugin owns it."""
        is_supported = False
        try:
            tools = self.list_tools()
            for t in tools:
                if t.get("name") == tool_name:
                    is_supported = True
                    break
        except Exception:
            logger.warning(
                "Tool support check failed for %r; treating as unsupported.",
                tool_name,
                exc_info=True,
            )

        if is_supported:
            return invoke_call_tool(self.call_tool, tool_name, arguments, **kwargs)
        return None

    # Hook methods (optional overrides for governance)

    def pre_tool_call(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called BEFORE tool execution.

        Override this to implement:
        - Authorization checks (is tool allowed?)
        - Rate limiting (quota exceeded?)
        - Cost tracking (log estimated cost)
        - Input validation (sanitize arguments)

        Args:
            context: {
                "tool_name": str,
                "arguments": dict,
                "agent_id": str,
                "session_id": str,
            }

        Returns:
            Modified context (or raise exception to block)
        """
        return context

    def post_tool_call(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Hook called AFTER tool execution.

        Override this to implement:
        - Result filtering (redact sensitive data)
        - Quality scoring (evaluate tool output)
        - Retry logic (on transient failures)
        - Audit logging (record execution details)

        Args:
            context: {
                "tool_name": str,
                "arguments": dict,
                "result": dict,
                "execution_time_ms": float,
                "agent_id": str,
            }

        Returns:
            Modified context with potentially modified result
        """
        return context


def tool_fixture(ctx: Any, tool_name: str) -> Any:
    """Fixture payload the run harness supplied for *tool_name*, or ``None``.

    Harnesses set ``ctx.tool_fixtures`` to ``{"by_tool": {name | "*": payload}}``
    resolved from the dataset item's ``inputs.tool_fixtures``. The payload is
    free-form and owned by the tool.
    """
    fixtures = getattr(ctx, "tool_fixtures", None)
    if fixtures is None:
        return None
    by_tool = fixtures.get("by_tool") if isinstance(fixtures, dict) else None
    if not isinstance(by_tool, dict):
        raise TypeError('ctx.tool_fixtures must be {"by_tool": {...}}')
    return by_tool[tool_name] if tool_name in by_tool else by_tool.get("*")
