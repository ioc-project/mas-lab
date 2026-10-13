//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { ListItemButton, Typography } from "@mui/material";
import {
  fileIconFor,
  formatSize,
  prettyPath,
  transparentRowSx,
} from "../utils";
import type { FlatFile } from "../utils";

export interface OutputFileRowProps {
  file: FlatFile;
  selected: boolean;
  onSelect: (path: string) => void;
}

/** A single, selectable output-file row with type icon, path, and size. */
export function OutputFileRow({
  file,
  selected,
  onSelect,
}: OutputFileRowProps) {
  const { Icon, color } = fileIconFor(file.name);
  const size = formatSize(file.size);
  return (
    <ListItemButton
      selected={selected}
      onClick={() => onSelect(file.path)}
      sx={{ py: 0.25, pl: 2, borderRadius: 1, gap: "6px", ...transparentRowSx }}
    >
      <Icon sx={{ fontSize: 16, color, flexShrink: 0 }} />
      <Typography
        variant="body2"
        title={file.path}
        sx={{
          fontSize: "13px",
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {prettyPath(file.path)}
      </Typography>
      {size && (
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", flexShrink: 0 }}
        >
          {size}
        </Typography>
      )}
    </ListItemButton>
  );
}
