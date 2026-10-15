//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { type Theme, type SxProps } from "@mui/material";

export const openDrawerWidth = 264;
export const compactDrawerWidth = 92;

export const sideBarItemLinkStyle = (theme: Theme): React.CSSProperties => {
  return {
    color: theme.palette.vars.baseTextStrong,
    display: "flex",
    width: "100%",
    height: "20px",
    textDecoration: "none",
  };
};

export const sideBarListItemTextStyle = (): React.CSSProperties => {
  return {
    opacity: 1,
    alignSelf: "center",
    display: "flex",
    textTransform: "capitalize",
  };
};

export const siteBarItemStyle = (
  theme: Theme,
  selected: Boolean,
  itemLevel = 0,
) => ({
  listItem: {
    minHeight: "36px",
    borderRadius: "8px",
    backgroundColor: "inherit",
    "&.MuiListItem-root": {
      "&:hover": {
        backgroundColor: `${theme.palette.vars.baseBorderMedium} !important`,
      },
    },
  },
  listItemOpen: {
    width: "40px",
  },

  listItemButton: {
    padding: "8px",
    paddingLeft: `${8 + itemLevel * 12}px`,
    borderRadius: "8px",
    border: "none",
    minHeight: "36px",
    color: `${theme.palette.vars.baseTextStrong} !important`,
    backgroundColor: "inherit",

    "&:hover": {
      backgroundColor: `${theme.palette.vars.baseBorderMedium} !important`,
      "& a[role='sidebar-link']": {
        color: `${theme.palette.vars.baseTextStrong} !important`,
      },
    },
    "&.Mui-selected": {
      backgroundColor: `${theme.palette.vars.baseBorderMedium} !important`,
      "&:hover": {
        backgroundColor: `${theme.palette.vars.baseBorderMedium} !important`,
      },
    },
  } as React.CSSProperties,
  listItemIcon: {
    alignSelf: "center",
    justifyContent: "center",
  } as React.CSSProperties,
  listItemIconNode: {
    fill: selected
      ? theme.palette.vars.controlIconActive
      : theme.palette.vars.controlIconDefault,
  } as React.CSSProperties,
});

export const sideBarPaperStyle = (theme: Theme, compact = false): SxProps => {
  return {
    width: compact ? compactDrawerWidth : openDrawerWidth,
    height: "100%",
    position: "relative",
    boxSizing: "border-box",
    background: theme.palette.vars.baseBackgroundStrong,
    border: "none",
    transition: theme.transitions.create(["width", "height"], {
      easing: theme.transitions.easing.sharp,
      duration: theme.transitions.duration.enteringScreen,
    }),
    overflowX: "hidden",
    zIndex: 80,
    marginTop: "12px",

    "&::-webkit-scrollbar": {
      width: 0,
    },
  };
};

export const sideBarDrawerStyle = (compact = false): SxProps<Theme> => {
  return {
    transition: "all 0.2s ease-in-out",
    width: compact ? compactDrawerWidth : openDrawerWidth,
    height: "100%",
    whiteSpace: "nowrap",
    position: "relative",
    flexShrink: 0,
    "& .MuiDrawer-paper": {
      width: compact ? compactDrawerWidth : openDrawerWidth,
      boxSizing: "border-box",
      borderRadius: 0,
    },
  };
};

export const sideBarTooltip = (theme: Theme) => {
  return {
    ...theme.typography.caption,
    zIndex: 100,
    position: "relative",
  } as React.CSSProperties;
};

export const previewIconStyle: SxProps<Theme> = {
  ".MuiSvgIcon-root": {
    width: 60,
    height: 20,
  },
};
