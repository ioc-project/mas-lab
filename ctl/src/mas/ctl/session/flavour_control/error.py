#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Control load and attach errors."""


class ControlError(ValueError):
    """Raised when a Control file cannot be read, or a Flavour control row is invalid."""
