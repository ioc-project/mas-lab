//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Box, Collapse, ListItemButton, Typography } from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
} from "@mui/icons-material";
import { TreeNode } from "./TreeNode";
import { transparentRowSx } from "../utils";
import type { FileTreeEntry } from "@/api/apiCalls";

export interface AllFilesSectionProps {
  tree: FileTreeEntry[];
  hint: string;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}

/** Collapsed-by-default escape hatch that renders the full recursive tree. */
export function AllFilesSection({
  tree,
  hint,
  selectedPath,
  onSelect,
}: AllFilesSectionProps) {
  const [open, setOpen] = useState(false);
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1.5,
        overflow: "hidden",
      }}
    >
      <ListItemButton
        onClick={() => setOpen((o) => !o)}
        sx={{ py: 0.75, px: 1, gap: "4px", ...transparentRowSx }}
      >
        {open ? (
          <ExpandMoreIcon sx={{ fontSize: 16, color: "text.secondary" }} />
        ) : (
          <ChevronRightIcon sx={{ fontSize: 16, color: "text.secondary" }} />
        )}
        <Typography
          variant="body2"
          sx={{
            fontSize: "13px",
            fontWeight: 600,
            color: "text.secondary",
            flex: 1,
          }}
        >
          All files
        </Typography>
        {!open && hint && (
          <Typography
            variant="caption"
            sx={{ color: "text.disabled", flexShrink: 0 }}
          >
            {hint}
          </Typography>
        )}
      </ListItemButton>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ py: 0.5 }}>
          {tree.map((entry) => (
            <TreeNode
              key={entry.name}
              entry={entry}
              path=""
              selectedPath={selectedPath}
              onSelect={onSelect}
              defaultExpanded={false}
            />
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}
