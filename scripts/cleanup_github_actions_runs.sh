#!/usr/bin/env bash
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# Delete all workflow runs (requires gh auth with repo admin).
#
# Usage: ./scripts/cleanup_github_actions_runs.sh [owner/repo]
set -euo pipefail

REPO="${1:-ioc-project/mas-lab}"
deleted=0

while true; do
  mapfile -t ids < <(gh api "repos/${REPO}/actions/runs?per_page=100" -q '.workflow_runs[].id')
  [[ ${#ids[@]} -eq 0 ]] && break
  for id in "${ids[@]}"; do
    if gh api --method DELETE "repos/${REPO}/actions/runs/${id}" -f confirm=true >/dev/null 2>&1; then
      deleted=$((deleted + 1))
      printf '\rDeleted %s runs...' "$deleted"
    fi
  done
done

echo ""
echo "Done. Deleted ${deleted} run(s). Remaining: $(gh api "repos/${REPO}/actions/runs" -q '.total_count')"
