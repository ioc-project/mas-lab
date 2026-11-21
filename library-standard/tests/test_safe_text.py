#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``safe_text`` makes text from a peer one short printable line."""

from __future__ import annotations

import pytest
from mas.library.standard.lib.comm.text import MAX_ERROR_TEXT, safe_text


def test_control_and_format_characters_are_dropped() -> None:
    # kills: a sanitizer that keeps NUL, ESC, bidi override, zero-width space or a line separator
    assert safe_text("a\x00b\x1b[31mc‮d​e f\x7fg") == "ab[31mcdefg"


def test_line_and_tab_breaks_become_one_space_so_a_log_line_cannot_be_forged() -> None:
    # kills: dropping "\n" outright (words fuse) or keeping it (a second, forged log line)
    assert safe_text("first\nERROR forged\r\n\tend") == "first ERROR forged   end"
    assert "\n" not in safe_text("x\ny")


def test_long_text_is_cut_to_the_limit_and_says_so() -> None:
    # kills: no truncation, or a result longer than the limit
    out = safe_text("x" * 10_000)
    assert len(out) == MAX_ERROR_TEXT
    assert out.endswith("…")
    assert safe_text("x" * MAX_ERROR_TEXT) == "x" * MAX_ERROR_TEXT


def test_the_cut_follows_the_cleaning_not_the_other_way_round() -> None:
    # kills: truncating before dropping controls (the result would come out short)
    assert safe_text("\x00" * 500 + "ab", limit=5) == "ab"


def test_non_text_is_read_through_str_and_a_bad_limit_is_refused() -> None:
    assert safe_text(ValueError("boom")) == "boom"
    assert safe_text(None) == "None"
    with pytest.raises(ValueError):
        safe_text("x", limit=0)
