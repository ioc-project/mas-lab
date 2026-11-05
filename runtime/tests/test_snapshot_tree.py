#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.runtime.session.snapshot import SnapshotRef, SnapshotTree


def test_snapshot_tree_branches_and_cursor_independent_of_live() -> None:
    tree = SnapshotTree()
    root = SnapshotRef.from_state(session_id="s", parent_snapshot_id=None, turn=0, kernel={"n": 0}, working_memory=[])
    a = SnapshotRef.from_state(
        session_id="s", parent_snapshot_id=root.snapshot_id, turn=1, kernel={"n": 1}, working_memory=[]
    )
    b = SnapshotRef.from_state(
        session_id="s", parent_snapshot_id=root.snapshot_id, turn=1, kernel={"n": 2}, working_memory=[]
    )
    tree.record(root)
    tree.record(a, live=True)
    tree.record(b, live=False)
    tree.set_cursor("s", b.snapshot_id)
    assert tree.live("s").snapshot_id == a.snapshot_id
    assert tree.cursor("s").snapshot_id == b.snapshot_id
    branches = tree.all_branches("s")
    leaf_ids = {path[-1].snapshot_id for path in branches}
    assert leaf_ids == {a.snapshot_id, b.snapshot_id}
    tree.clear_session("s")
    assert tree.list_nodes("s") == []
