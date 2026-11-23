#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Deprecated entry — use ``mas-ctl`` (control plane), not ``mas-runtime``.

``mas-runtime`` is the embeddable Mealy kernel library only. Session bootstrap and
CLIs live in ``mas-ctl``; benchmarks in ``mas-lab``.
"""

from __future__ import annotations

import sys

from mas.runtime.entrypoint import install_host_environment


def main() -> None:
    install_host_environment()
    sys.stderr.write(
        "The mas-runtime CLI was removed. Use mas-ctl instead:\n"
        '  mas-ctl chat <manifest> -q "..."\n'
        "  mas-ctl validate <manifest>\n"
        "Library API: mas.runtime + mas.ctl.session.bootstrap\n"
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
