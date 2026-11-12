#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a call layer says about itself. The composer reads these attributes directly."""

from __future__ import annotations

from typing import Any, ClassVar

from mas.runtime.layers.lifecycle import Lifecycle


class LayerBase(Lifecycle):
    """Base of every call layer plugin.

    A layer states where it sits with class attributes; an empty one means "use the library row, else the
    default". ``wrap(inner, params, *, call_kinds=)`` builds the layer over the next call.
    """

    #: ``all`` (one shared copy for the engine) or ``destination`` (one per leaf). Empty: from the library row.
    per: ClassVar[str] = ""
    #: Call kinds the layer applies to by default (``llm``, ``tool``, ``send``). Empty: from the library row.
    call_kinds: ClassVar[tuple[str, ...]] = ()
    #: Higher wraps outside. ``None``: from the library row, else 0.
    priority: ClassVar[int | None] = None
    stores_llm_responses: ClassVar[bool] = False
    paces_llm_calls: ClassVar[bool] = False

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> Any:
        raise NotImplementedError(f"{cls.__name__} must implement wrap(inner, params, *, call_kinds=)")
