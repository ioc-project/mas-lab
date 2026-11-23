#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCP protocol adapters for MAS Lab.

The package contains the client/provider bridge, wire-contract conversions,
the production server factory, and the MCP Tasks extension. See the MCP
quickstart and reference documentation for registration and deployment.
"""

# Import what you need from its module (.provider, .layer, .client, ...). This file imports
# nothing: a package __init__ that imports its submodules makes importing any one of them import
# all of them, and two threads that begin at different submodules then wait on each other's
# module locks ("deadlock detected by _ModuleLock").
