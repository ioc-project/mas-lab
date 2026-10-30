#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Design-pattern plugins (OSS)."""

from mas.library.standard.plugins.design_patterns.linear import (  # noqa: F401
    DeterministicLinearPlugin,
)
from mas.library.standard.plugins.design_patterns.moderated_rounds import (  # noqa: F401
    ModeratedRoundsPlugin,
)
from mas.library.standard.plugins.design_patterns.parallel import (  # noqa: F401
    DeterministicParallelPlugin,
)
from mas.library.standard.plugins.design_patterns.single import (  # noqa: F401
    DeterministicSingleAgentPlugin,
)
