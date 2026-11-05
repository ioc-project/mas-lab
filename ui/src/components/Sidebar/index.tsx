//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Drawer } from "@mui/material";
import { Box, Stack, useTheme } from "@open-ui-kit/core";
import { SideBarItemList } from "./SideBarItemList";
import { sideBarDrawerStyle, sideBarPaperStyle } from "./styles";

export const Sidebar = () => {
  const theme = useTheme();

  return (
    <Box sx={sideBarDrawerStyle()}>
      <Drawer
        variant="permanent"
        anchor="left"
        slotProps={{
          paper: {
            sx: sideBarPaperStyle(theme),
          },
        }}
        sx={sideBarDrawerStyle()}
        data-testid="sidebar"
      >
        <Stack direction="column" sx={{ gap: "16px", height: "100%" }}>
          <SideBarItemList />
        </Stack>
      </Drawer>
    </Box>
  );
};
