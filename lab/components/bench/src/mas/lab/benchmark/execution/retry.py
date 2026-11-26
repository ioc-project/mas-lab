#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Retry plumbing shared by the executor, the retrier and the round planner.

The executor runs once. A :class:`~mas.lab.benchmark.execution.roles.Retrier`
decides whether to run again. :func:`run_with_retrier` is the in-process loop
that joins them; ``mas-lab benchmark runs retry`` joins them across rounds.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mas.lab.benchmark.execution.roles import MAX_SPENT_ATTEMPTS, AttemptRecord, Executor, Retrier

_FATAL_RUN_MARKERS = (
    "tls certificate verification failed",
    "http 401",
    "http 403",
    "authentication/authorization",
    "malformed spec",
)
# A failed model call, or a failed send to a peer, comes back as text. A caller that collates its
# peers' replies (a fan-out pattern) puts that text inside its own output, so it can be anywhere
# in the content. The three ``[send] agent`` forms are the strings ``SendTool.asend`` returns
# on AgentCommError (``via <protocol> failed``), KeyError (``not available on bus``), and
# RuntimeError (``failed`` with no protocol). A send that failed because the LLM replay cache
# missed is not a peer that failed its task: the caller may continue, which replay labs do.
_SEND_AGENT_FAILED = re.compile(r"\[send\] agent '[^']*'(?: via \S+)? failed:")
_SEND_AGENT_MISSING = re.compile(r"\[send\] agent '[^']*' not available on bus")
_CACHE_MISS = "llm_cache miss"


def hidden_peer_failure(output: str) -> bool:
    """True when the run's text hides a failed model call or a failed send to a peer."""
    text = str(output)
    if "LLM request failed:" in text:
        return True
    if _SEND_AGENT_MISSING.search(text):
        return True
    for match in _SEND_AGENT_FAILED.finditer(text):
        rest = text[match.end() : match.end() + 400]
        if _CACHE_MISS not in rest:
            return True
    return False


_RETRY_AFTER_MS_RE = re.compile(r"retry-after-ms[:\s]+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RETRY_AFTER_RE = re.compile(r"retry-after[:\s]+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def lab_run_error_is_retryable(error: str, *, status: str) -> bool:
    """True for transient execution failures; false for auth/TLS/config errors."""
    if status != "error":
        return False
    text = (error or "").lower()
    return not any(marker in text for marker in _FATAL_RUN_MARKERS)


def retry_after_seconds(error: str) -> float | None:
    """Seconds an endpoint asked the caller to wait, parsed from the error text."""
    text = error or ""
    match_ms = _RETRY_AFTER_MS_RE.search(text)
    if match_ms:
        return max(0.0, float(match_ms.group(1)) / 1000.0)
    match = _RETRY_AFTER_RE.search(text)
    if match:
        return max(0.0, float(match.group(1)))
    return None


def runner_outcome(result_dict: dict) -> tuple[str, str, str]:
    """``(status, error, output)`` of one runner result."""
    output = result_dict.get("content", str(result_dict))
    if result_dict.get("status") == "error":
        return "error", output or "execution error", output
    if output and hidden_peer_failure(str(output)):
        return "error", str(output), output
    return "ok", "", output


@dataclass(frozen=True)
class RunOutcome:
    result: dict
    status: str
    error: str
    output: str
    attempts: int
    """Attempts spent on this run across every round, including ``attempts_before``."""


async def run_with_retrier(
    executor: Executor,
    retrier: Retrier,
    do_run: Callable[[], dict],
    *,
    attempts_before: int = 0,
    before_attempt: Callable[[], Awaitable[None]] | None = None,
    on_retry: Callable[[int, str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RunOutcome:
    """Run the run until it is complete or the retrier stops.

    ``attempts_before`` counts attempts an earlier round already spent, so a
    retrier's cap holds across rounds.
    """
    history = [AttemptRecord("error", "earlier round")] * max(0, min(int(attempts_before), MAX_SPENT_ATTEMPTS))
    while True:
        done = await executor.attempt(do_run, before_attempt=before_attempt)
        status, error = done.status, done.error
        if status != "error" and not executor.is_complete(done.result, status=status, error=error):
            status, error = "error", error or "incomplete"
        history.append(AttemptRecord(status, error, done.fatal))
        if status != "error":
            return RunOutcome(done.result, status, error, done.output, len(history))
        decision = retrier.decide(history)
        if not decision.retry:
            return RunOutcome(done.result, status, error, done.output, len(history))
        if on_retry is not None:
            on_retry(len(history), error)
        if decision.delay_s > 0:
            await sleep(decision.delay_s)
