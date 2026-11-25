from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any
from uuid import uuid4

from a2a.helpers import (
    new_data_artifact_update_event,
    new_data_message,
    new_raw_artifact_update_event,
    new_text_artifact_update_event,
    new_text_message,
    new_text_status_update_event,
    new_url_artifact_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from mas.library.standard.lib.stack import PeerChallenge

from library_ioa.plugins.a2a.server.owner import VerifiedOwner

logger = logging.getLogger(__name__)


@dataclass
class _ActiveExecution:
    task: asyncio.Task[Any]
    cancel_event: Event


class MasLabAgentExecutor(AgentExecutor):
    """Minimal SDK-compatible executor implementation for local MAS Lab agents."""

    def __init__(self, handler: Callable[..., Any] | None = None) -> None:
        self._handler = handler or (lambda *args, **kwargs: {"text": ""})
        self._active_executions: dict[str, _ActiveExecution] = {}

    async def _invoke_handler(self, context: RequestContext, *, message: str) -> Any:
        task_id = context.task_id or ""
        cancel_event = Event()
        execution_task = asyncio.current_task()
        if execution_task is None:
            raise RuntimeError("A2A handler must run inside an asyncio task")
        if task_id:
            self._active_executions[task_id] = _ActiveExecution(execution_task, cancel_event)
        try:
            response = await asyncio.to_thread(
                self._call_handler,
                message,
                context,
                cancel_event,
            )
            if inspect.isawaitable(response):
                response = await response
            return response
        finally:
            if task_id:
                self._active_executions.pop(task_id, None)

    def _call_handler(
        self,
        message: str,
        context: RequestContext,
        cancel_event: Event,
    ) -> Any:
        kwargs: dict[str, Any] = {"context": context}
        try:
            parameters = inspect.signature(self._handler).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "cancel_event" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            kwargs["cancel_event"] = cancel_event
        return self._handler(message, **kwargs)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            message = context.get_user_input()
            result = await self._invoke_handler(context, message=message)
            if result is None:
                return
            is_stream = hasattr(result, "__aiter__") or (
                hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict))
            )
            if is_stream:
                await self._emit_stream(context, event_queue, result)
                return
            if isinstance(result, (Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent)):
                await event_queue.enqueue_event(result)
                return
            await self._emit_result(context, event_queue, result)
        except PeerChallenge as refused:
            # a layer refused the caller before the agent ran: tell it what would satisfy this side, on this task
            await self._emit_auth_required(context, event_queue, refused)
        except Exception:
            logger.exception("A2A agent execution failed")
            await self._emit_failure(context, event_queue)

    async def _emit_auth_required(
        self, context: RequestContext, event_queue: EventQueue, refused: PeerChallenge
    ) -> None:
        """The task is auth-required; its status message carries the challenge where the verifier asked.

        ``carry`` picks the place: ``a2a.metadata_key`` the message metadata, ``a2a.data_key`` the key of the data
        part; the default is a data part ``{"challenge": {...}}`` (the stack's own shape). When the scheme gave a
        serialized ``wire`` challenge it goes verbatim under the ``carry`` key (with no key, next to the structured
        challenge under ``wire``). Empty: none to offer.
        """
        metadata_key = refused.carry.get("a2a.metadata_key")
        data_key = refused.carry.get("a2a.data_key")
        wire = refused.wire
        data: dict[str, Any] = {}
        metadata: dict[str, Any] = {}
        if wire is not None and metadata_key:
            metadata[metadata_key] = wire  # verbatim: the peer's own serialized challenge
        elif wire is not None and data_key:
            data[data_key] = wire
        else:
            if refused.challenge and metadata_key:
                metadata[metadata_key] = refused.challenge
            elif refused.challenge:
                data[data_key or "challenge"] = refused.challenge
            if wire is not None:
                data["wire"] = wire
        message = new_data_message(data, context_id=context.context_id or None, task_id=context.task_id or None)
        if metadata:
            message.metadata.update(metadata)
        status = TaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED, message=message)
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(id=context.task_id or "", context_id=context.context_id or "", status=status)
            )
            return
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or "", context_id=context.current_task.context_id, status=status
            )
        )

    async def _emit_failure(self, context: RequestContext, event_queue: EventQueue) -> None:
        current_task = context.current_task
        if current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id or "",
                    context_id=context.context_id or "",
                    status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
                )
            )
            return
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=context.task_id or "",
                context_id=current_task.context_id,
                state=TaskState.TASK_STATE_FAILED,
                text="Agent execution failed",
            )
        )

    async def _emit_stream(self, context: RequestContext, event_queue: EventQueue, stream: Any) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id or "",
                context_id=context.context_id or "",
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=context.task_id or "",
                context_id=context.context_id or "",
                state=TaskState.TASK_STATE_WORKING,
                text="Task started",
            )
        )
        artifact_id = str(uuid4())
        has_previous_chunk = False
        if hasattr(stream, "__aiter__"):
            async for chunk in stream:
                await self._emit_stream_chunk(context, event_queue, chunk, artifact_id, has_previous_chunk)
                has_previous_chunk = True
        else:
            for chunk in stream:
                await self._emit_stream_chunk(context, event_queue, chunk, artifact_id, has_previous_chunk)
                has_previous_chunk = True
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=context.task_id or "",
                context_id=context.context_id or "",
                state=TaskState.TASK_STATE_COMPLETED,
                text="Task completed",
            )
        )

    async def _emit_stream_chunk(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        chunk: Any,
        artifact_id: str,
        append: bool,
    ) -> None:
        value = chunk.get("text") if isinstance(chunk, dict) else chunk
        await event_queue.enqueue_event(
            new_text_artifact_update_event(
                task_id=context.task_id or "",
                context_id=context.context_id or "",
                name="stream",
                text=str(value or ""),
                append=append,
                artifact_id=artifact_id,
            )
        )

    async def _emit_result(self, context: RequestContext, event_queue: EventQueue, result: Any) -> None:
        text = result.get("text") if isinstance(result, dict) else result
        artifacts = result.get("artifacts") if isinstance(result, dict) else None
        task_state = result.get("task_state") if isinstance(result, dict) else None
        if context.call_context is not None and isinstance(context.call_context.user, VerifiedOwner):
            # the handler's id is the owner's own conversation, internal; the wire carries what the caller sent (a
            # send that names only its task gets a fresh id from the SDK: the task's is the one it belongs to)
            task = context.current_task
            context_id = (task.context_id if task is not None else context.context_id) or ""
        else:
            context_id = str(result.get("context_id") or "") if isinstance(result, dict) else ""
            context_id = context_id or context.context_id or ""
        if artifacts:
            for index, artifact in enumerate(artifacts):
                if not isinstance(artifact, dict):
                    continue
                event_args = {
                    "task_id": context.task_id or "",
                    "context_id": context_id,
                    "name": str(artifact.get("name") or f"artifact-{index + 1}"),
                }
                kind = str(artifact.get("kind") or "text")
                if kind == "data":
                    event = new_data_artifact_update_event(data=artifact.get("data") or {}, **event_args)
                elif kind == "file":
                    event = new_raw_artifact_update_event(
                        raw=str(artifact.get("text") or "").encode(),
                        media_type=str(artifact.get("media_type") or "text/plain"),
                        filename=str(artifact.get("filename") or "output.txt"),
                        **event_args,
                    )
                elif kind == "url":
                    event = new_url_artifact_update_event(
                        url=str(artifact.get("url") or ""),
                        media_type=str(artifact.get("media_type") or "text/plain"),
                        filename=str(artifact.get("filename") or "output.txt"),
                        **event_args,
                    )
                else:
                    event = new_text_artifact_update_event(text=str(artifact.get("text") or ""), **event_args)
                await event_queue.enqueue_event(event)
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task_id=context.task_id or "",
                    context_id=context_id,
                    state=self._task_state(task_state) or TaskState.TASK_STATE_COMPLETED,
                    text=str(text or ""),
                )
            )
            return
        if task_state:
            state = self._task_state(task_state)
            if state is None:
                raise ValueError(f"Unsupported A2A task state: {task_state!r}")
            if context.current_task is None:
                configuration = getattr(context, "configuration", None)
                if state == TaskState.TASK_STATE_COMPLETED and getattr(configuration, "return_immediately", False):
                    state = TaskState.TASK_STATE_WORKING
                await event_queue.enqueue_event(
                    Task(
                        id=context.task_id or "",
                        context_id=context_id,
                        status=TaskStatus(state=state),
                    )
                )
            else:
                await event_queue.enqueue_event(
                    new_text_status_update_event(
                        task_id=context.task_id or "",
                        context_id=context.current_task.context_id,
                        state=state,
                        text=str(text or ""),
                    )
                )
            return
        if context.current_task is not None:
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task_id=context.task_id,
                    context_id=context.current_task.context_id,
                    state=TaskState.TASK_STATE_COMPLETED,
                    text=str(text or ""),
                )
            )
            return
        await event_queue.enqueue_event(
            new_text_message(
                text=str(text),
                context_id=context_id,
                task_id=context.task_id,
            )
        )

    @staticmethod
    def _task_state(value: Any) -> TaskState | None:
        return {
            "working": TaskState.TASK_STATE_WORKING,
            "completed": TaskState.TASK_STATE_COMPLETED,
            "input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
        }.get(str(value or ""))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id:
            execution = self._active_executions.get(context.task_id)
            if execution is not None:
                execution.cancel_event.set()
                execution.task.cancel()
        if context.task_id and context.context_id:
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    state=TaskState.TASK_STATE_CANCELED,
                    text="Task canceled",
                )
            )
