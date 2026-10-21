//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
// Runtime API URL — overridden by deployments/docker/entrypoint-ui.sh in containers.
window.__MAS_LAB_API_BASE_URL__ = window.__MAS_LAB_API_BASE_URL__ || "";
// Library the UI opens on; the first library when empty.
window.__MAS_LAB_DEFAULT_LIBRARY__ = window.__MAS_LAB_DEFAULT_LIBRARY__ || "";
window.__MAS_LAB_DEFAULT_SCOPE__ = window.__MAS_LAB_DEFAULT_SCOPE__ || window.__MAS_LAB_DEFAULT_LIBRARY__ || "";
window.__MAS_LAB_TOKEN__ = window.__MAS_LAB_TOKEN__ || "";
