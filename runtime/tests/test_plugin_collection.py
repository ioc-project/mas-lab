#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for PluginCollection and ContextPart.skills() shorthand."""

from __future__ import annotations

from mas.runtime.boundary.context.plugin_collection import PluginCollection
from mas.runtime.contracts.context_contract import ContextPart, ContextPlacement

# ---------------------------------------------------------------------------
# PluginCollection — collect_results
# ---------------------------------------------------------------------------


class _Plugin:
    def __init__(self, results):
        self._results = results

    def on_collect_context(self):
        return self._results


class _RaisingPlugin:
    def on_collect_context(self):
        raise RuntimeError("plugin failure")


class _NoHookPlugin:
    pass  # doesn't define on_collect_context


def test_collect_results_empty_collection():
    col = PluginCollection()
    assert col.collect_results("collect_context") == []


def test_collect_results_single_plugin_list():
    col = PluginCollection()
    parts = [ContextPart.skills("catalog text")]
    col.register(_Plugin(parts))
    result = col.collect_results("collect_context")
    assert result == parts


def test_collect_results_flattens_multiple_plugins():
    col = PluginCollection()
    p1 = [ContextPart.skills("skill A")]
    p2 = [ContextPart.memory("some memory")]
    col.register(_Plugin(p1))
    col.register(_Plugin(p2))
    result = col.collect_results("collect_context")
    assert len(result) == 2
    assert result[0] in p1
    assert result[1] in p2


def test_collect_results_skips_none_return():
    col = PluginCollection()
    col.register(_Plugin(None))
    assert col.collect_results("collect_context") == []


def test_collect_results_isolates_plugin_errors(caplog):
    import logging

    col = PluginCollection()
    col.register(_RaisingPlugin())
    col.register(_Plugin([ContextPart.skills("ok")]))
    with caplog.at_level(logging.WARNING):
        result = col.collect_results("collect_context")
    assert len(result) == 1
    assert "plugin failure" in caplog.text


def test_collect_results_skips_plugin_without_hook():
    col = PluginCollection()
    col.register(_NoHookPlugin())
    col.register(_Plugin([ContextPart.skills("found")]))
    result = col.collect_results("collect_context")
    assert len(result) == 1


def test_get_plugins_by_type():
    from mas.runtime.contracts.context_contract import ContextContract

    class _CCPlugin(ContextContract):
        def collect_context(self):
            return []

    col = PluginCollection()
    col.register(_NoHookPlugin())
    cc = _CCPlugin()
    col.register(cc)
    result = col.get_plugins_by_type(ContextContract)
    assert result == [cc]


def test_repr_shows_plugin_names():
    col = PluginCollection()
    col.register(_Plugin([]))
    assert "_Plugin" in repr(col)


def test_len_and_bool():
    col = PluginCollection()
    assert len(col) == 0
    assert not col
    col.register(_Plugin([]))
    assert len(col) == 1
    assert col


# ---------------------------------------------------------------------------
# ContextPart.skills() shorthand
# ---------------------------------------------------------------------------


def test_skills_shorthand_placement():
    part = ContextPart.skills("catalog content")
    assert part.placement == ContextPlacement.SYSTEM_SKILLS
    assert part.priority == 40
    assert part.pinned is True
    assert part.source == "skills"
    assert part.content == "catalog content"


def test_skills_shorthand_provenance():
    part = ContextPart.skills("catalog")
    prov = part.provenance
    assert getattr(prov, "mechanism", None) == "inject"
    assert getattr(prov, "source_type", None) == "skill"


def test_skills_shorthand_custom_source():
    part = ContextPart.skills("catalog", source="my-source")
    assert part.source == "my-source"


def test_skills_shorthand_pinned_override():
    part = ContextPart.skills("catalog", pinned=False)
    assert part.pinned is False


# ---------------------------------------------------------------------------
# _inject_context_plugins orders parts by placement + priority
# ---------------------------------------------------------------------------


def test_inject_context_plugins_ordering(tmp_path):
    """Parts from multiple plugins are sorted by placement before injection."""
    from mas.library.standard.plugins.context.assembler import inject_context_plugins

    memory_part = ContextPart.memory("memory content")  # priority 100
    skills_part = ContextPart.skills("skills catalog")  # priority 40

    class _FakeCtx:
        class plugin_collection:
            @staticmethod
            def collect_results(hook_name):
                # Return in wrong order — memory before skills
                return [memory_part, skills_part]

            def __bool__(self):
                return True

    ctx = _FakeCtx()
    system_parts: list[str] = []
    inject_context_plugins(ctx, system_parts)

    assert len(system_parts) == 2
    # Skills (SYSTEM_SKILLS, priority 40) should come before memory (SYSTEM_MEMORY, priority 100)
    assert system_parts[0] == "skills catalog"
    assert system_parts[1] == "memory content"
