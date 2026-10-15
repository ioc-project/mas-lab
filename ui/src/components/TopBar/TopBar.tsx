//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  AppBar,
  MenuItem,
  Select,
  Toolbar,
  Typography,
  useTheme,
} from "@mui/material";
import { useNavigate, useLocation } from "react-router";
import { useLibraries } from "@/api/apiCalls";
import type { SelectChangeEvent } from "@mui/material";
import { useCallback, useEffect, useMemo } from "react";

const TopBar = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { data: libraries = [] } = useLibraries();

  const library = useMemo(
    () => location.pathname.split("/")[1] || "",
    [location.pathname],
  );

  useEffect(() => {
    if (libraries.length === 0) return;
    const isValid = libraries.some((lib) => lib.dir === library);
    if (!isValid) {
      const firstLib = libraries[0].dir;
      navigate(`/${firstLib}/applications`, { replace: true });
    }
  }, [libraries, library, navigate]);

  const handleLibraryChange = useCallback(
    (e: SelectChangeEvent<string>) => {
      const newLib = e.target.value;
      if (!library) {
        navigate(`/${newLib}/applications`);
        return;
      }
      const rest = location.pathname.substring(library.length + 1);
      navigate(`/${newLib}${rest}`);
    },
    [library, location.pathname, navigate],
  );

  return (
    <AppBar
      position="static"
      elevation={0}
      sx={{
        backgroundColor: theme.palette.vars.baseBackgroundStrong,
      }}
    >
      <Toolbar
        variant="dense"
        sx={{
          "&.MuiToolbar-root": {
            minHeight: "32px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 36px",
          },
        }}
      >
        <Typography
          variant="subtitle1"
          component="div"
          sx={{
            fontWeight: 700,
            letterSpacing: "0.3px",
            color: theme.palette.vars.baseTextStrong,
          }}
        >
          MAS-LAB
        </Typography>
        {libraries.length > 0 && (
          <Select
            size="small"
            value={
              libraries.some((l) => l.dir === library)
                ? library
                : libraries[0].dir
            }
            onChange={handleLibraryChange}
            variant="outlined"
            sx={{
              minWidth: 180,
              fontSize: "13px",
              height: "28px",
              "& .MuiSelect-select": { py: "2px" },
            }}
          >
            {libraries.map((lib) => (
              <MenuItem key={lib.dir} value={lib.dir} sx={{ fontSize: "13px" }}>
                {lib.name}
              </MenuItem>
            ))}
          </Select>
        )}
      </Toolbar>
    </AppBar>
  );
};

export default TopBar;
export { TopBar };
