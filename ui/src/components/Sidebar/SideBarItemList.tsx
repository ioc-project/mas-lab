//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { List, Stack } from "@mui/material";
import { SideBarItem } from "./SideBarItem";
import {
  authenticatedRoutes,
  PATHS,
  unauthenticatedRoutes,
} from "@/routes/routes.tsx";
import { pathForKind } from "@/routes/kinds";
import { useLocation } from "react-router";
import type { AppRoute } from "@/routes/types.ts";
import { parseScopeId, useScopeId } from "@/api/scope";
import { useScope } from "@/api/scopes";

export const SideBarItemList = () => {
  const { pathname } = useLocation();
  const scopeId = useScopeId();
  const { data: scope } = useScope(scopeId);
  const { kind, name } = parseScopeId(scopeId);
  const scopeType = kind === "lab" ? "labs" : "libraries";

  const allRoutes = [...unauthenticatedRoutes, ...authenticatedRoutes];

  const allowedPaths = new Set(
    (scope?.kinds ?? []).map((k) => pathForKind(k)).filter(Boolean),
  );
  // Playground and control panel stay available.
  allowedPaths.add("playground");
  allowedPaths.add("control-panel");

  const resolvePath = (routePath: string) =>
    routePath.replace(":scopeType", scopeType).replace(":scope", encodeURIComponent(name));

  const isParentRouteSelected = (route: AppRoute) => {
    if (route.path === PATHS.labsIndex) {
      return pathname === "/" || pathname === "/labs";
    }
    const resolved = resolvePath(route.path);
    if (resolved === "/") {
      return pathname === "/";
    }
    return pathname.startsWith(resolved);
  };

  return (
    <List
      sx={{
        gap: "4px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {allRoutes
        .map((parentRoute) => {
          if (!parentRoute.sideBarProps) return null;
          const isLabsHome = parentRoute.path === PATHS.labsIndex;
          const slug = parentRoute.path.split("/").filter(Boolean).pop() ?? "";
          if (!isLabsHome) {
            if (!scopeId) return null;
            if (allowedPaths.size && !allowedPaths.has(slug)) {
              return null;
            }
          }
          return (
            <Stack
              direction={"column"}
              sx={{ gap: "4px" }}
              key={parentRoute.sideBarProps.title}
            >
              <SideBarItem
                {...parentRoute.sideBarProps}
                selected={isParentRouteSelected(parentRoute)}
                to={isLabsHome ? PATHS.labsIndex : resolvePath(parentRoute.path)}
              />
            </Stack>
          );
        })
        .filter(Boolean)}
    </List>
  );
};
