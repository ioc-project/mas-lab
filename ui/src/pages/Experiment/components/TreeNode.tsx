//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Box, Typography } from "@mui/material";
import {
  FolderOpen as FolderIcon,
  InsertDriveFileOutlined as FileIcon,
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
} from "@mui/icons-material";
import type { FileTreeEntry } from "@/api/apiCalls";

export interface TreeNodeProps {
  entry: FileTreeEntry;
  path: string;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  depth?: number;
  defaultExpanded?: boolean;
}

/** A recursive, expandable row for a single file-tree entry. */
export function TreeNode({
  entry,
  path,
  selectedPath,
  onSelect,
  depth = 0,
  defaultExpanded = true,
}: TreeNodeProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const isDir = entry.type === "directory";
  const fullPath = path ? `${path}/${entry.name}` : entry.name;
  const isSelected = selectedPath === fullPath;

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          pl: `${12 + depth * 24}px`,
          pr: "8px",
          py: "3px",
          cursor: "pointer",
          borderRadius: "4px",
          backgroundColor: isSelected ? "action.selected" : "transparent",
          "&:hover": { backgroundColor: "action.hover" },
          userSelect: "none",
        }}
        onClick={() => {
          if (isDir) {
            setExpanded(!expanded);
          } else {
            onSelect(fullPath);
          }
        }}
      >
        {isDir ? (
          expanded ? (
            <ExpandMoreIcon sx={{ fontSize: 16, color: "text.secondary" }} />
          ) : (
            <ChevronRightIcon sx={{ fontSize: 16, color: "text.secondary" }} />
          )
        ) : (
          <Box sx={{ width: 16 }} />
        )}
        {isDir ? (
          <FolderIcon sx={{ fontSize: 16, color: "warning.main" }} />
        ) : (
          <FileIcon sx={{ fontSize: 16, color: "text.secondary" }} />
        )}
        <Typography
          variant="body2"
          sx={{
            fontSize: "13px",
            fontWeight: isDir ? 500 : 400,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {entry.name}
        </Typography>
      </Box>
      {isDir && expanded && entry.children && (
        <Box>
          {entry.children.map((child) => (
            <TreeNode
              key={child.name}
              entry={child}
              path={fullPath}
              selectedPath={selectedPath}
              onSelect={onSelect}
              depth={depth + 1}
              defaultExpanded={defaultExpanded}
            />
          ))}
        </Box>
      )}
    </Box>
  );
}
