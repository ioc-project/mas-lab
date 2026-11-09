//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar/TopBar";
import { Outlet } from "react-router";
import { Box, Stack } from "@open-ui-kit/core";

const LayoutWithSideNav = () => {
  return (
    <Stack direction={"column"} sx={{ height: "100%", width: "100%" }}>
      {/* Rendered inside the route tree so useParams() sees :scopeType/:scope. */}
      <TopBar />
      <Stack direction={"row"} sx={{ flex: 1, minHeight: 0 }}>
        <Sidebar />

        <Box sx={{ overflow: "hidden", width: "100%" }}>
          <Outlet />
        </Box>
      </Stack>
    </Stack>
  );
};

export default LayoutWithSideNav;
