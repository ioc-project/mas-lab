//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { AppBar, Toolbar, Typography, useTheme } from "@mui/material";
import { useNavigate, useLocation } from "react-router";
import { useCallback, useEffect, useMemo } from "react";

import { LABS_CATALOG_ID, pickDefaultScope, useScopes } from "@/api/scopes";
import { isLabsCatalogPath, scopeHref, useScopeId } from "@/api/scope";
import { useKinds } from "@/api/items";
import { restPathForScope } from "@/routes/kinds";
import { ScopeSelect } from "@/components/ScopeSelect";

const TopBar = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { data: scopes = [] } = useScopes();
  const { data: kinds } = useKinds();
  const currentScopeId = useScopeId();
  const fallback = useMemo(() => pickDefaultScope(scopes), [scopes]);

  const catalog = isLabsCatalogPath(location.pathname);

  useEffect(() => {
    if (scopes.length === 0) return;
    if (location.pathname === "/connect" || catalog) return;
    const isValid = scopes.some((s) => s.id === currentScopeId);
    if (!isValid && fallback) {
      const target = scopes.find((s) => s.id === fallback);
      navigate(scopeHref(fallback, restPathForScope(location.pathname, target, kinds)), { replace: true });
    }
  }, [scopes, currentScopeId, fallback, navigate, location.pathname, kinds, catalog]);

  const handleChange = useCallback(
    (next: string) => {
      if (next === LABS_CATALOG_ID) {
        navigate("/labs");
        return;
      }
      const target = scopes.find((s) => s.id === next);
      navigate(scopeHref(next, restPathForScope(location.pathname, target, kinds)));
    },
    [location.pathname, navigate, scopes, kinds],
  );

  const value = catalog
    ? LABS_CATALOG_ID
    : scopes.some((s) => s.id === currentScopeId)
      ? currentScopeId
      : fallback;

  return (
    <AppBar
      position="static"
      elevation={0}
      sx={{
        backgroundColor: theme.palette.vars.baseBackgroundMedium,
        borderBottom: `1px solid ${theme.palette.vars.baseBorderDefault}`,
        color: theme.palette.text.primary,
      }}
    >
      <Toolbar
        variant="dense"
        sx={{
          minHeight: "32px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: 0,
        }}
      >
        <Typography
          variant="subtitle1"
          component="div"
          onClick={() => navigate("/labs")}
          sx={{
            fontWeight: 700,
            letterSpacing: "0.3px",
            color: theme.palette.vars.baseTextStrong,
            cursor: "pointer",
          }}
        >
          MAS-LAB
        </Typography>
        {scopes.length > 0 && (
          <ScopeSelect
            scopes={scopes}
            value={value}
            onChange={handleChange}
            allowCatalog
          />
        )}
      </Toolbar>
    </AppBar>
  );
};

export default TopBar;
export { TopBar };
