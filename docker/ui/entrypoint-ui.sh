#!/bin/sh
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
set -eu

API_URL="${VITE_API_BASE_URL:-${VITE_API_URL:-http://localhost:8090}}"

cat > /usr/share/nginx/html/env-config.js <<EOF
window.__MAS_LAB_API_BASE_URL__ = "${API_URL}";
window.__MAS_LAB_DEFAULT_LIBRARY__ = "${MAS_LAB_DEFAULT_LIBRARY:-}";
window.__MAS_LAB_DEFAULT_SCOPE__ = "${MAS_LAB_DEFAULT_SCOPE:-${MAS_LAB_DEFAULT_LIBRARY:-}}";
window.__MAS_LAB_TOKEN__ = "${MAS_LAB_TOKEN:-}";
EOF

exec nginx -g "daemon off;"
