//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Navigate, useLocation, useParams } from "react-router";
import { Box, Spinner } from "@open-ui-kit/core";

import { useScopes } from "@/api/scopes";
import { scopeHref } from "@/api/scope";
import Four0Four from "@/pages/404/404";

/** Old `/:library/...` URLs redirect to `/libraries/:scope/...` or `/labs/:scope/...`. */
export function LegacyScopeRedirect() {
  const { library = "" } = useParams();
  const location = useLocation();
  const { data: scopes, isLoading } = useScopes();

  // Already scoped (`/labs/...`, `/libraries/...`) but no route matched: show a
  // 404 rather than a blank page.
  if (library === "libraries" || library === "labs") {
    return <Four0Four />;
  }

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%" }}>
        <Spinner size={32} />
      </Box>
    );
  }

  const match =
    scopes?.find((s) => s.id === `library:${library}`) ||
    scopes?.find((s) => s.id === `lab:${library}`) ||
    scopes?.find((s) => s.name === library);

  const rest = location.pathname.split("/").slice(2).join("/");
  if (match) {
    return <Navigate to={scopeHref(match.id, rest ? `/${rest}` : "")} replace />;
  }
  return <Navigate to="/" replace />;
}
