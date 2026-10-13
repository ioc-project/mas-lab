//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0

/**
 * Transparent row styling that matches the original file-tree rows. The app
 * theme fills ListItem/ListItemButton with a solid background by default (and a
 * stronger one on hover/selection); this restores the subtle, see-through look
 * the file tree had, using theme action tokens so it stays correct in
 * light/dark. Spread into a ListItemButton's `sx`.
 */
export const transparentRowSx = {
  bgcolor: "transparent",
  "&:hover": { bgcolor: "action.hover" },
  "&.Mui-focusVisible": { bgcolor: "action.hover" },
  "&.Mui-selected": { bgcolor: "action.selected" },
  "&.Mui-selected:hover": { bgcolor: "action.selected" },
} as const;
