//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { ReactNode } from "react";
import type { SxProps } from "@open-ui-kit/core";
import { Box } from "@open-ui-kit/core";

interface TabPanelProps {
  children?: ReactNode;
  index: number | string;
  value: number | string;
  sx?: SxProps;
}
export const TabPanel = (props: TabPanelProps) => {
  const { children, value, index, sx, ...other } = props;

  return (
    <Box
      role="tabpanel"
      hidden={value !== index}
      id={`tabpanel-${index}`}
      aria-labelledby={`tab-${index}`}
      sx={{
        display: value === index ? "flex" : "none",
        flexDirection: "column",
        minHeight: 0,
        ...sx,
      }}
      {...other}
    >
      {children}
    </Box>
  );
};
