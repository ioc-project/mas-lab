#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Helpers for context plugins and ``mas-ctl compile``. Runtime does not import this."""

from mas.library.standard.lib.context.committed_history import (
    apply_request_view,
    compact_committed_history,
    request_history,
)
from mas.library.standard.lib.context.compaction import (
    apply_working_memory_compaction,
    context_manager_binding_from_compaction,
    resolve_working_memory_context_manager,
)
from mas.library.standard.lib.context.history_budget import (
    assembly_trimmer_params,
    context_manager_history_budget_hint,
    fill_context_manager_defaults,
)
from mas.library.standard.lib.context.payload import (
    sanitize_provider_messages,
    split_user_turns,
    start_of_tool_group,
)
from mas.library.standard.lib.context.spec import context_manager_spec, is_summarising_context_manager
from mas.library.standard.lib.context.working_memory import (
    bounded_working_memory_tail,
    working_memory_slice_limit,
)

__all__ = [
    "apply_request_view",
    "apply_working_memory_compaction",
    "assembly_trimmer_params",
    "bounded_working_memory_tail",
    "compact_committed_history",
    "context_manager_binding_from_compaction",
    "context_manager_history_budget_hint",
    "context_manager_spec",
    "fill_context_manager_defaults",
    "is_summarising_context_manager",
    "request_history",
    "resolve_working_memory_context_manager",
    "sanitize_provider_messages",
    "split_user_turns",
    "start_of_tool_group",
    "working_memory_slice_limit",
]
