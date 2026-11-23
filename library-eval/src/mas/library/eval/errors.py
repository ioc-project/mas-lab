#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a failed metric or judge call may say in a result or a log.

An LLM SDK error's text is the provider's body, which may echo the request, its key included. A metric
``error`` field and a log line therefore carry the exception's type name plus ``HTTP <status>``
(see :func:`mas.runtime.reliability.classify.error_summary`), never its text, except for a
:class:`JudgeCallFailed` and a ``ClassifiedFailure``, whose texts are built by our classifiers.
"""

from __future__ import annotations

from mas.runtime.reliability.classes import ClassifiedFailure
from mas.runtime.reliability.classify import error_summary


class JudgeCallFailed(RuntimeError):
    """A judge call that failed; its text is built from :func:`judge_error_text` and our own words only."""


def judge_error_text(exc: BaseException) -> str:
    """``str(exc)`` for a :class:`JudgeCallFailed` or a ``ClassifiedFailure``, else the type name plus
    ``HTTP <status>``."""
    if isinstance(exc, (JudgeCallFailed, ClassifiedFailure)):
        return str(exc)
    return error_summary(exc)
