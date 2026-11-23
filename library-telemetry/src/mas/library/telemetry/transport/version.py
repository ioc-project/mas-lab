#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SDK version check against a classifier's supported range. Warn once."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from mas.library.telemetry.transport.warnings import warn_once

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:  # packaging is a runtime dep of the repo; keep a soft path
    SpecifierSet = None  # type: ignore[misc, assignment]
    Version = None  # type: ignore[misc, assignment]
    InvalidVersion = Exception  # type: ignore[misc, assignment]


def installed_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def check_sdk_range(
    *,
    classifier_id: str,
    specifier: str,
    distribution: str,
    installed: str | None = None,
) -> bool:
    """Return True if ``installed`` satisfies ``specifier``. Warn once if not."""
    found = installed if installed is not None else installed_version(distribution)
    if not specifier:
        return True
    if found is None:
        warn_once(
            "sdk_missing",
            classifier_id,
            "transport classifier %s: package %s is not installed (range %s)",
            classifier_id,
            distribution,
            specifier,
        )
        return False
    if SpecifierSet is None or Version is None:
        return True
    try:
        ok = Version(found) in SpecifierSet(specifier)
    except InvalidVersion:
        ok = False
    if not ok:
        warn_once(
            "sdk_range",
            classifier_id,
            "transport classifier %s: installed %s %s is outside supported range %s",
            classifier_id,
            distribution,
            found,
            specifier,
        )
    return ok


__all__ = ["check_sdk_range", "installed_version"]
