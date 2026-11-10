#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""System tool: inform_user — agent-initiated, non-blocking user status update."""

from __future__ import annotations

from typing import Any

from mas.library.standard.plugins.system_tools._arg_coercion import coerce_json_string_to_dict
from mas.library.standard.plugins.system_tools.wrappers import SystemToolUserUpdateWrapper
from mas.runtime.contracts.tool_contract import ToolContract
from mas.runtime.system_plugins import InstanceSystemPlugin, SessionContext, ToolInstance
from pydantic import BaseModel, Field, create_model, field_validator

#: Default cap on ``message`` length — see ``max_message_length`` on __init__.
DEFAULT_MAX_MESSAGE_LENGTH = 20000


class InformUserTool(ToolContract):
    """Send the user a non-blocking status/progress update.

    This is a **system tool** that lets agents keep the user informed during
    a long-running or multi-round task (e.g. "still negotiating with Finance",
    "waiting on Salesforce data") without pausing the agent's turn and without
    waiting for a response.

    Unlike `request_human_input`, which blocks the agent's turn until the user
    answers, `inform_user` is fire-and-forget: the tool call returns immediately
    and the agent keeps working.

    **Architecture**:
    - The tool raises an `InformUserSignal`
    - The tool execution layer catches the signal and routes it to a side
      channel (an external `UserIOContract`, or the shared HITL resolver
      registry as a fallback)
    - External clients poll/consume this side channel and
      post the message, without any user response flowing back
    - Works across delegation boundaries, same as `request_human_input`

    Example usage in agent context:
    ```
    You can post a non-blocking status update by calling:
    inform_user(
        message="Still reviewing Acme Corp renewal economics with Finance.",
        involved_agents=["finance-agent"],
    )
    ```
    """

    class Input(BaseModel):
        message: str = Field(
            ...,
            description="The status/progress update to show the user",
            min_length=1,
            max_length=DEFAULT_MAX_MESSAGE_LENGTH,
        )
        user_name: str = Field(
            default="",
            description="Optional name/identifier of the user this update is for",
        )
        involved_agents: list[str] = Field(
            default_factory=list,
            description="Other agents relevant to this update (e.g. peers being discussed)",
        )
        metadata: dict[str, Any] = Field(
            default_factory=dict,
            description="Optional contextual data to attach to the update",
        )

        _coerce_metadata = field_validator("metadata", mode="before")(coerce_json_string_to_dict)

    def __init__(self, *, max_message_length: int = DEFAULT_MAX_MESSAGE_LENGTH) -> None:
        """``max_message_length``: configurable via ``spec.system_plugins``:

            - inform_user: {max_message_length: 8000}

        Default matches the class-level ``Input.message`` schema (2000); a
        non-default value rebuilds ``Input`` so the *advertised* tool schema
        (what the model sees) always matches what's actually enforced.
        """
        self.max_message_length = max_message_length
        if max_message_length != DEFAULT_MAX_MESSAGE_LENGTH:
            self.Input = create_model(
                "Input",
                __base__=InformUserTool.Input,
                message=(
                    str,
                    Field(
                        ...,
                        description="The status/progress update to show the user",
                        min_length=1,
                        max_length=max_message_length,
                    ),
                ),
            )

    def get_name(self) -> str:
        return "inform_user"

    def get_description(self) -> str:
        return (
            "Send the user a non-blocking status or progress update. "
            "Use this to keep the user informed during a long-running or "
            "multi-round task without waiting for a response. "
            "Unlike request_human_input, this does not pause your turn."
        )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the non-blocking update by emitting it to the side channel.

        The actual execution flow:
        1. Validate arguments via Input model
        2. Raise InformUserSignal (sentinel exception caught by the tool
           execution layer, converted to a side-channel update)
        3. The wrapper returns a "sent" acknowledgement as the tool result —
           no blocking, no wait for a user response.
        """
        args = self.Input(**kwargs)

        from mas.runtime.system_tools.signal import InformUserSignal

        raise InformUserSignal(
            message=args.message,
            user_name=args.user_name,
            involved_agents=list(args.involved_agents) if args.involved_agents else [],
            metadata=dict(args.metadata) if args.metadata else {},
        )


class InformUserPlugin(InstanceSystemPlugin):
    """System plugin for ``inform_user``. Settings: ``max_message_length``."""

    name = "inform_user"

    def __init__(self, *, max_message_length: int | None = None, **_: Any) -> None:
        super().__init__()
        self.max_message_length = max_message_length

    def make_instance(self, session: SessionContext | None) -> ToolInstance:
        kwargs = {}
        if self.max_message_length is not None:
            kwargs["max_message_length"] = int(self.max_message_length)
        tool = InformUserTool(**kwargs)
        if session is None:
            return tool
        return SystemToolUserUpdateWrapper(tool, user_io_contract=session.user_io_contract)
