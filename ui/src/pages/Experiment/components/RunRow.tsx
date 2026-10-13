//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Box, Collapse, ListItemButton, Typography } from "@mui/material";
import { InsertDriveFileOutlined as FileIcon } from "@mui/icons-material";
import { TreeNode } from "./TreeNode";
import { runFileHint, transparentRowSx } from "../utils";
import type { RunEntry } from "../utils";

export interface RunRowProps {
  run: RunEntry;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}

/** A single run inside a group; expands to the run's file tree. */
export function RunRow({ run, selectedPath, onSelect }: RunRowProps) {
  const [open, setOpen] = useState(false);
  const hint = runFileHint(run.entry);
  return (
    <Box>
      <ListItemButton
        onClick={() => setOpen((o) => !o)}
        sx={{
          py: 0.25,
          pl: 4,
          pr: 1,
          borderRadius: 1,
          gap: "6px",
          ...transparentRowSx,
        }}
      >
        <FileIcon
          sx={{ fontSize: 15, color: "text.disabled", flexShrink: 0 }}
        />
        <Typography variant="body2" sx={{ fontSize: "13px", flexShrink: 0 }}>
          {run.name}
        </Typography>
        {hint && (
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            · {hint}
          </Typography>
        )}
      </ListItemButton>
      <Collapse in={open} unmountOnExit>
        {(run.entry.children ?? []).map((child) => (
          <TreeNode
            key={child.name}
            entry={child}
            path={run.path}
            selectedPath={selectedPath}
            onSelect={onSelect}
            depth={2}
            defaultExpanded={false}
          />
        ))}
      </Collapse>
    </Box>
  );
}
