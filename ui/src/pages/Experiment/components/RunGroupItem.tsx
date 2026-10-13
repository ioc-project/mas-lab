//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Box, Collapse, ListItemButton, Typography } from "@mui/material";
import {
  FolderOpen as FolderIcon,
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
} from "@mui/icons-material";
import { RunRow } from "./RunRow";
import { TreeNode } from "./TreeNode";
import { RUNS_PREVIEW_CAP, transparentRowSx } from "../utils";
import type { RunGroup } from "../utils";

export interface RunGroupItemProps {
  group: RunGroup;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}

/** A collapsed group of runs (typically one scenario) with a run count. */
export function RunGroupItem({
  group,
  selectedPath,
  onSelect,
}: RunGroupItemProps) {
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const isSelfSingleRun =
    group.runs.length === 1 && group.runs[0].path === group.path;
  const shownRuns = showAll
    ? group.runs
    : group.runs.slice(0, RUNS_PREVIEW_CAP);
  const remaining = group.runs.length - shownRuns.length;

  return (
    <Box>
      <ListItemButton
        onClick={() => setOpen((o) => !o)}
        sx={{
          py: 0.5,
          pl: 2,
          pr: 1,
          borderRadius: 1,
          gap: "4px",
          ...transparentRowSx,
        }}
      >
        {open ? (
          <ExpandMoreIcon sx={{ fontSize: 16, color: "text.secondary" }} />
        ) : (
          <ChevronRightIcon sx={{ fontSize: 16, color: "text.secondary" }} />
        )}
        <FolderIcon sx={{ fontSize: 16, color: "warning.main" }} />
        <Typography
          variant="body2"
          title={group.path}
          sx={{
            fontSize: "13px",
            fontWeight: 500,
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {group.name}
        </Typography>
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", flexShrink: 0 }}
        >
          {group.count} run{group.count === 1 ? "" : "s"}
        </Typography>
      </ListItemButton>
      <Collapse in={open} unmountOnExit>
        {isSelfSingleRun ? (
          (group.entry.children ?? []).map((child) => (
            <TreeNode
              key={child.name}
              entry={child}
              path={group.path}
              selectedPath={selectedPath}
              onSelect={onSelect}
              depth={1}
              defaultExpanded={false}
            />
          ))
        ) : (
          <>
            {shownRuns.map((run) => (
              <RunRow
                key={run.path}
                run={run}
                selectedPath={selectedPath}
                onSelect={onSelect}
              />
            ))}
            {!showAll && remaining > 0 && (
              <ListItemButton
                onClick={() => setShowAll(true)}
                sx={{
                  py: 0.25,
                  pl: 4,
                  pr: 1,
                  borderRadius: 1,
                  ...transparentRowSx,
                }}
              >
                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                  … {remaining} more
                </Typography>
              </ListItemButton>
            )}
          </>
        )}
      </Collapse>
    </Box>
  );
}
