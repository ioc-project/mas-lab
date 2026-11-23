#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# python -m mas.lab.controller runs the controller daemon (socket IPC and HTTP).
# Prefer: mas-lab control start  or  mas-lab serve
from mas.lab.controller.daemon import main

raise SystemExit(main())
