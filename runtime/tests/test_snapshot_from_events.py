#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.runtime.session.snapshot import SnapshotRef, SnapshotTree


def test_snapshot_tree_from_events_rebuilds_parent_links() -> None:
    root = SnapshotRef.from_state(session_id="s", parent_snapshot_id=None, turn=0, kernel={"n": 0}, working_memory=[])
    child = SnapshotRef.from_state(
        session_id="s", parent_snapshot_id=root.snapshot_id, turn=1, kernel={"n": 1}, working_memory=[]
    )
    events = [
        {
            "kind": "snapshot_recorded",
            "session_id": "s",
            "payload": {
                "snapshot_id": root.snapshot_id,
                "parent_snapshot_id": None,
                "turn": 0,
                "live": True,
            },
        },
        {
            "kind": "snapshot_recorded",
            "session_id": "s",
            "payload": {
                "snapshot_id": child.snapshot_id,
                "parent_snapshot_id": root.snapshot_id,
                "turn": 1,
                "live": True,
            },
        },
        {
            "kind": "checkpoint_navigated",
            "session_id": "s",
            "payload": {"from": child.snapshot_id, "to": root.snapshot_id, "snapshot_id": root.snapshot_id},
        },
    ]
    tree = SnapshotTree.from_events(events)
    nodes = {n.snapshot_id for n in tree.list_nodes("s")}
    assert nodes == {root.snapshot_id, child.snapshot_id}
    assert [c.snapshot_id for c in tree.children(root.snapshot_id)] == [child.snapshot_id]
    assert tree.cursor("s").snapshot_id == root.snapshot_id
