#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``scripts/version_manager.py`` keeps accepting the short package names that CI and Taskfile.yml pass."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "version_manager.py"

# Short names used by .github/workflows/*.yml and Taskfile.yml → the wheel name that keys PACKAGES.
SHORT_NAMES = {
    "runtime": "mas-lab-runtime",
    "library": "mas-lab-library-standard",
    "ctl": "mas-lab-ctl",
    "core": "mas-lab-component-core",
    "bench": "mas-lab-component-bench",
    "controller": "mas-lab-component-controller",
    "telemetry": "mas-lab-library-telemetry",
    "kg": "mas-lab-library-kg",
    "lab": "mas-lab",
}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=REPO_ROOT)


class ShortPackageNames(unittest.TestCase):
    def test_get_accepts_every_short_name(self) -> None:
        for short, wheel in SHORT_NAMES.items():
            with self.subTest(package=short):
                got = _run("get", short)
                want = _run("get", wheel)
                self.assertEqual(got.returncode, 0, got.stderr)
                self.assertEqual(want.returncode, 0, want.stderr)
                self.assertEqual(got.stdout, want.stdout)

    def test_get_without_package_reads_the_lab_version(self) -> None:
        got = _run("get")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(got.stdout, _run("get", "mas-lab").stdout)

    def test_every_alias_points_at_a_real_package(self) -> None:
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import version_manager
        finally:
            sys.path.remove(str(SCRIPT.parent))
        for short, wheel in version_manager.ALIASES.items():
            with self.subTest(package=short):
                self.assertIn(wheel, version_manager.PACKAGES)


if __name__ == "__main__":
    unittest.main()
