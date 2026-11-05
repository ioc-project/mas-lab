#!/usr/bin/env python3
# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Checkout a pinned A2A TCK and apply the two registry/test fixes CI needs.

The official suite is cloned unpinned. CORE-SEND-003 never declared
``expected_error``, so a correct ContentTypeNotSupportedError is scored as a
failed send. HTTP+JSON error responses use ``application/a2a+json``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PIN = "70f6f73c30208e80c889141907e094899e6e9f05"
TCK_DIR = Path(__file__).resolve().parent / "vendor" / "a2a-tck"


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{path.name}: patch did not apply")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    head = subprocess.check_output(["git", "-C", str(TCK_DIR), "rev-parse", "HEAD"], text=True).strip()
    if head != PIN:
        subprocess.run(["git", "-C", str(TCK_DIR), "fetch", "--depth", "1", "origin", PIN], check=True)
        subprocess.run(["git", "-C", str(TCK_DIR), "checkout", "--detach", PIN], check=True)
    core = TCK_DIR / "tck" / "requirements" / "core_operations.py"
    _replace(
        core,
        "    TASK_NOT_FOUND_ERROR,\n    UNSUPPORTED_OPERATION_ERROR,\n",
        "    TASK_NOT_FOUND_ERROR,\n    CONTENT_TYPE_NOT_SUPPORTED_ERROR,\n    UNSUPPORTED_OPERATION_ERROR,\n",
    )
    _replace(
        core,
        '        expected_behavior="ContentTypeNotSupportedError returned",\n'
        '        spec_url=f"{SPEC_BASE}311-send-message",\n',
        '        expected_behavior="ContentTypeNotSupportedError returned",\n'
        "        expected_error=CONTENT_TYPE_NOT_SUPPORTED_ERROR,\n"
        '        spec_url=f"{SPEC_BASE}311-send-message",\n',
    )
    _replace(
        TCK_DIR / "tests" / "compatibility" / "core_operations" / "test_error_handling.py",
        '        if "application/json" not in ct:\n',
        '        if "application/json" not in ct and "application/a2a+json" not in ct:\n',
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
