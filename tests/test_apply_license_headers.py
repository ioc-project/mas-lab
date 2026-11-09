#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/apply_license_headers.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "apply_license_headers.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("apply_license_headers", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lic():
    return _load_module()


def test_format_python_header(lic):
    header = lic.format_header(lic.HASH)
    assert "Copyright (c) 2026 Cisco Systems, Inc." in header
    assert "SPDX-License-Identifier: Apache-2.0" in header
    assert header.startswith("#  ")


def test_add_header_to_python(lic):
    text = '"""module doc"""\n\nx = 1\n'
    new, action = lic.build_new_content(text, lic.HASH)
    assert action == "add"
    assert new.startswith("#  Copyright")
    assert '"""module doc"""' in new


def test_skip_when_canonical_present(lic):
    text = (
        "#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates\n"
        "#  SPDX-License-Identifier: Apache-2.0\n"
        "\n"
        "x = 1\n"
    )
    new, action = lic.build_new_content(text, lic.HASH)
    assert action == "ok"
    assert new == text


def test_replace_full_apache_block(lic):
    text = (
        "# Copyright 2026 Cisco Systems, Inc. and its affiliates\n"
        "#\n"
        '# Licensed under the Apache License, Version 2.0 (the "License");\n'
        "# you may not use this file except in compliance with the License.\n"
        "#\n"
        "#     http://www.apache.org/licenses/LICENSE-2.0\n"
        "#\n"
        "# Unless required by applicable law or agreed to in writing, software\n"
        '# distributed under the License is distributed on an "AS IS" BASIS,\n'
        "# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
        "# See the License for the specific language governing permissions and\n"
        "# limitations under the License.\n"
        "\n"
        "x = 1\n"
    )
    new, action = lic.build_new_content(text, lic.HASH)
    assert action == "update"
    assert "Licensed under the Apache License" not in new
    assert "SPDX-License-Identifier: Apache-2.0" in new


def test_shebang_preserved(lic):
    text = "#!/usr/bin/env python3\n\nprint('hi')\n"
    new, action = lic.build_new_content(text, lic.HASH)
    assert action == "add"
    assert new.startswith("#!/usr/bin/env python3\n#  Copyright")


def test_typescript_header(lic):
    text = "export const x = 1;\n"
    new, action = lic.build_new_content(text, lic.SLASH)
    assert action == "add"
    assert new.startswith("//  Copyright")
