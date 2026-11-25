#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Turn-prefix fingerprints hash part lists as JSON, not Python repr."""

from __future__ import annotations

from mas.library.standard.plugins.context.conversation import SummarizingConversation

_PARTS = [
    {"type": "text", "text": "see"},
    {
        "type": "image",
        "media_type": "image/png",
        "sha256": "abc",
        "name": "chart.png",
        "bytes": 4,
    },
]


def test_fingerprint_of_part_lists_is_stable_and_not_repr():
    turns = [[{"role": "user", "content": _PARTS}]]
    a = SummarizingConversation._fingerprint(turns)
    b = SummarizingConversation._fingerprint([[{"role": "user", "content": list(_PARTS)}]])
    assert a == b
    repr_fp = SummarizingConversation._fingerprint([[{"role": "user", "content": repr(_PARTS)}]])
    assert a != repr_fp
    str_fp = SummarizingConversation._fingerprint([[{"role": "user", "content": "see"}]])
    assert a != str_fp
