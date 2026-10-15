//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Typography, Stack, Box, type SxProps } from "@mui/material";
import { Breadcrumbs, type BreadcrumbsProps } from "@open-ui-kit/core";
import React from "react";
import { useLocation } from "react-router";
import { toTitleCase } from "@/utils/string";

interface PageWithTitleProps {
  children: React.ReactNode;
  actions?: React.ReactNode[];
  breadcrumbItems?: BreadcrumbsProps["items"];
  /** Set to false to hide the breadcrumbs entirely (default: true). */
  showBreadcrumbs?: boolean;
  title: React.ReactNode;
  subTitle?: string;
  moduloMaxWidth?: number;
  sx?: SxProps;
}

export const PageWithTitle = ({
  breadcrumbItems,
  showBreadcrumbs = true,
  children,
  title,
  subTitle,
  actions,
  sx = {},
}: PageWithTitleProps) => {
  const location = useLocation();

  const autoBreadcrumbItems: BreadcrumbsProps["items"] = React.useMemo(() => {
    const allSegments = location.pathname.split("/").filter(Boolean);
    const pathSegments = allSegments.slice(1);
    if (pathSegments.length === 0) return [];

    const libraryPrefix = allSegments[0] ? `/${allSegments[0]}` : "";

    const prettyNameMap: Record<string, string> = {
      dashboard: "Dashboard",
    };

    const isUuid = (text: string) =>
      /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
        text,
      );

    return pathSegments.map((segment, index) => {
      const link =
        libraryPrefix + "/" + pathSegments.slice(0, index + 1).join("/");
      const decoded = decodeURIComponent(segment);
      const text =
        prettyNameMap[decoded] ??
        (isUuid(decoded) ? decoded : toTitleCase(decoded));
      return { text, link } as NonNullable<BreadcrumbsProps["items"]>[number];
    });
  }, [location.pathname]);

  const breadcrumbsToRender =
    breadcrumbItems && breadcrumbItems.length > 0
      ? breadcrumbItems
      : autoBreadcrumbItems;

  return (
    <Stack
      direction="column"
      sx={{
        height: "100%",
        width: "100%",
        padding: "32px",
        overflow: "auto",
        gap: "24px",
        ...sx,
      }}
    >
      <Stack direction="row" sx={{ justifyContent: "space-between" }}>
        <Stack direction="column" sx={{ width: "100%" }}>
          {showBreadcrumbs && breadcrumbsToRender.length > 1 && (
            <Breadcrumbs
              items={breadcrumbsToRender}
              maximumNumberOfVisibleBreadcrumbs={breadcrumbsToRender.length}
            />
          )}
          {title}
          {subTitle && <Typography variant="subtitle2">{subTitle}</Typography>}
        </Stack>
        {actions}
      </Stack>

      <Box
        sx={{
          flex: 1,
          width: "100%",
          position: "relative",
        }}
      >
        {children}
      </Box>
    </Stack>
  );
};
