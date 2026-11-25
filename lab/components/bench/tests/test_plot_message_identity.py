#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plot context identity hashes part lists, not captions."""

from __future__ import annotations

from mas.lab.plots.multilevel_trajectory.records import _context_parts_from_messages, _normalize_context_messages


def test_two_images_with_the_same_caption_are_new_context():
    a = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "see"},
                {
                    "type": "image",
                    "media_type": "image/png",
                    "sha256": "aaa",
                    "name": "chart.png",
                    "bytes": 1,
                },
            ],
        }
    ]
    b = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "see"},
                {
                    "type": "image",
                    "media_type": "image/png",
                    "sha256": "bbb",
                    "name": "chart.png",
                    "bytes": 1,
                },
            ],
        }
    ]
    prev = _normalize_context_messages(a)
    parts = _context_parts_from_messages(_normalize_context_messages(b), prev_messages=prev)
    assert parts
    assert parts[0]["highlight"] is True
