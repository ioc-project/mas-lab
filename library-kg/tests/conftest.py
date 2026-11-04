#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Root test configuration for the knowledge-graph library."""

import sys
from pathlib import Path

# Ensure src/ is on the path for editable-install-free test runs
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
