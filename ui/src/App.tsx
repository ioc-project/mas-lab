//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import "@open-ui-kit/core/typography.css";
import "@xyflow/react/dist/style.css";
import { ThemeMode, ThemeProvider } from "@open-ui-kit/core";
import { BrowserRouter as Router } from "react-router";
import { Box, CssBaseline } from "@mui/material";
import { TopBar } from "@/components";
import AppRoutes from "@/routes/routes.tsx";

import { QueryClientProvider } from "@/provider";

function ThemedApp() {
  return (
    <>
      <CssBaseline />
      <Router>
        <Box
          sx={{
            height: "100%",
            width: "100%",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <TopBar />
          <Box sx={{ flex: 1 }}>
            <AppRoutes />
          </Box>
        </Box>
      </Router>
    </>
  );
}

const App = () => {
  return (
    <QueryClientProvider>
      <ThemeProvider defaultMode={ThemeMode.Midnight}>
        <ThemedApp />
      </ThemeProvider>
    </QueryClientProvider>
  );
};

export default App;
