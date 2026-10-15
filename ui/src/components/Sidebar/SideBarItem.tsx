//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  useTheme,
} from "@mui/material";
import { Link } from "react-router";
import {
  sideBarItemLinkStyle,
  sideBarListItemTextStyle,
  siteBarItemStyle,
  previewIconStyle,
} from "./styles";
import { LegacyIcons } from "@open-ui-kit/core";
import type { SideBarProps } from "@/routes/types.ts";

const { Preview } = LegacyIcons;

export interface SideBarItemProps extends SideBarProps {
  selected: boolean;
  to: string;
  itemLevel?: number;
  compact?: boolean;
}

export const SideBarItem = (props: SideBarItemProps) => {
  const {
    selected,
    icon: Icon,
    title,
    to,
    disabled,
    hidden,
    preview,
    itemLevel = 0,
    compact = false,
  } = props;

  const theme = useTheme();

  const style = siteBarItemStyle(theme, selected, itemLevel);

  return (
    <ListItem
      disablePadding
      key={title}
      sx={{
        display: hidden ? "none" : "block",
        ...style.listItem,
      }}
    >
      <ListItemButton
        selected={selected}
        sx={style.listItemButton}
        disableRipple
        disabled={disabled}
        role="sidebar-list-item"
      >
        <Link
          to={to}
          style={sideBarItemLinkStyle(theme)}
          role="sidebar-link"
          title={compact ? title : undefined}
        >
          <ListItemIcon sx={style.listItemIcon}>
            {Icon && <Icon sx={style.listItemIconNode} />}
          </ListItemIcon>
          {!compact && (
            <ListItemText
              primary={
                <Typography variant={"captionSemibold"}>{title}</Typography>
              }
              sx={sideBarListItemTextStyle()}
            />
          )}
        </Link>
        {preview && (
          <ListItemIcon sx={previewIconStyle}>
            <Preview />
          </ListItemIcon>
        )}
      </ListItemButton>
    </ListItem>
  );
};
