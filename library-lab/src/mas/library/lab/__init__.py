#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas.library.lab package."""

from pathlib import Path

from mas._dist import installed_version


def package_root() -> Path:
    """Return the directory containing ``library.yaml`` for this manifest library."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "library.yaml").is_file():
            return parent
    return here


__version__ = installed_version(file=__file__)

from mas.library.lab.plots import execution_chain_graph  # noqa: E402

__all__ = ["execution_chain_graph"]
