#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A schema is checked against disk once per file, not once per ``$ref`` that points at the file."""

from __future__ import annotations

from mas.ctl.validate import schemas


def test_a_resolved_schema_keeps_one_stamp_per_file() -> None:
    schemas.clear_schema_caches()
    schemas.load_schema("overlay")
    for stamps, _doc in schemas._RESOLVED_SCHEMA.values():
        paths = [path for path, _digest in stamps]
        assert len(paths) == len(set(paths))
    # the overlay schema reaches dozens of fragments; a stamp per $ref would be tens of thousands
    assert len(schemas._RESOLVED_SCHEMA[next(iter(schemas._RESOLVED_SCHEMA))][0]) < 200
