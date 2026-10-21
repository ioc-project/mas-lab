//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import "@open-ui-kit/core/typography.css";
import "@xyflow/react/dist/style.css";
import { ThemeMode, ThemeProvider } from "@open-ui-kit/core";
import { BrowserRouter as Router, useLocation, useNavigate } from "react-router";
import { Box, CssBaseline } from "@mui/material";
import { TopBar } from "@/components";
import AppRoutes from "@/routes/routes.tsx";
import { useEffect } from "react";

import { QueryClientProvider } from "@/provider";
import { fetchApiInfo } from "@/api/info";

function ThemedApp() {
  const location = useLocation();
  const navigate = useNavigate();
  const hideChrome = location.pathname === "/connect";

  useEffect(() => {
    const goConnect = () => {
      if (location.pathname !== "/connect") navigate("/connect");
    };
    window.addEventListener("mas-lab:unauthorized", goConnect);
    window.addEventListener("mas-lab:unreachable", goConnect);
    return () => {
      window.removeEventListener("mas-lab:unauthorized", goConnect);
      window.removeEventListener("mas-lab:unreachable", goConnect);
    };
  }, [location.pathname, navigate]);

  useEffect(() => {
    if (location.pathname === "/connect") return;
    let cancelled = false;
    fetchApiInfo().catch(() => {
      if (!cancelled) navigate("/connect");
    });
    return () => {
      cancelled = true;
    };
  }, [location.pathname, navigate]);

  return (
    <>
      <CssBaseline />
      <Box
        sx={{
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {!hideChrome && <TopBar />}
        <Box sx={{ flex: 1 }}>
          <AppRoutes />
        </Box>
      </Box>
    </>
  );
}

const App = () => {
  return (
    <QueryClientProvider>
      <ThemeProvider defaultMode={ThemeMode.Midnight}>
        <Router>
          <ThemedApp />
        </Router>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

export default App;
