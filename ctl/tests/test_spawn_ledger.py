#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import pytest
from mas.ctl.executor.spawn_ledger import SpawnLedger


def test_spawn_ledger_enforces_session_wide_count_and_branch_depth():
    ledger = SpawnLedger(max_depth=2, max_spawns=2)
    assert ledger.allow_spawn("s", "root")
    ledger.enter("s", child_agent_id="c1", parent_agent_id="root")
    assert ledger.current_depth("s") == 1
    assert ledger.agent_depth("s", "c1") == 1
    assert ledger.spawn_count("s") == 1

    # A grandchild is one level deeper and still fits max_depth=2.
    assert ledger.allow_spawn("s", "c1")
    ledger.enter("s", child_agent_id="c2", parent_agent_id="c1")
    assert ledger.agent_depth("s", "c2") == 2
    assert ledger.current_depth("s") == 2

    # Count cap is session-wide and outlives the branch it was spent on.
    assert not ledger.allow_spawn("s", "root")
    ledger.exit("s", child_agent_id="c2")
    assert ledger.allow_spawn("s", "root") is False
    ledger.exit("s", child_agent_id="c1")
    assert ledger.current_depth("s") == 0


def test_concurrent_siblings_are_checked_independently():
    """A live sibling must not make the next one look like a grandchild."""
    ledger = SpawnLedger(max_depth=1, max_spawns=4)

    ledger.enter("s", child_agent_id="root.worker.1", parent_agent_id="root")

    # First sibling is still running; the second is still a child of root.
    assert ledger.allow_spawn("s", "root")
    ledger.enter("s", child_agent_id="root.worker.2", parent_agent_id="root")

    assert ledger.agent_depth("s", "root.worker.1") == 1
    assert ledger.agent_depth("s", "root.worker.2") == 1
    # max_depth=1 still refuses a real grandchild.
    assert not ledger.allow_spawn("s", "root.worker.1")


def test_spawn_ledger_siblings_stay_independent_under_asyncio_gather():
    """allow_spawn/enter stay await-free, so two sibling checks cannot race."""
    import asyncio

    ledger = SpawnLedger(max_depth=1, max_spawns=4)

    async def spawn_one(child_id: str) -> int:
        assert ledger.allow_spawn("s", "root")
        ledger.enter("s", child_agent_id=child_id, parent_agent_id="root")
        await asyncio.sleep(0.01)
        return ledger.agent_depth("s", child_id)

    async def _run():
        return await asyncio.gather(spawn_one("root.worker.1"), spawn_one("root.worker.2"))

    depths = asyncio.run(_run())
    assert list(depths) == [1, 1]
    assert ledger.spawn_count("s") == 2


def test_spawn_ledger_scopes_budgets_and_ids_by_parent_template():
    ledger = SpawnLedger(max_depth=1, max_spawns=1)
    first_id = ledger.mint_agent_id("root", "worker")
    second_id = ledger.mint_agent_id("root", "worker")

    assert first_id == "root.worker.1"
    assert second_id == "root.worker.2"
    ledger.enter("session-a", child_agent_id=first_id, parent_agent_id="root")
    assert not ledger.allow_spawn("session-a", "root")
    assert ledger.allow_spawn("session-b", "root")


def test_spawn_ledger_rejects_invalid_bounds_and_underflow():
    with pytest.raises(ValueError, match="max_depth"):
        SpawnLedger(max_depth=0)
    ledger = SpawnLedger()
    with pytest.raises(RuntimeError, match="underflow"):
        ledger.exit("never-entered", child_agent_id="ghost")


def test_spawn_ledger_snapshot_round_trips_counts_and_ids():
    ledger = SpawnLedger(max_depth=2, max_spawns=4)
    child = ledger.mint_agent_id("root", "w")
    ledger.enter("s", child_agent_id=child, parent_agent_id="root")
    restored = SpawnLedger.from_snapshot(ledger.snapshot())
    assert restored.spawn_count("s") == 1
    assert restored.agent_depth("s", child) == 1
    assert restored.mint_agent_id("root", "w") == "root.w.2"


def test_spawn_ledger_checks_child_depth_type_and_boundary_values():
    ledger = SpawnLedger(max_depth=3)

    assert ledger.allows_child_depth(0)
    assert ledger.allows_child_depth(2)
    assert not ledger.allows_child_depth(3)
    assert not ledger.allows_child_depth("2")
    assert not ledger.allows_child_depth(2.9)
    assert not ledger.allows_child_depth(-1)
    assert not ledger.allows_child_depth(True)
