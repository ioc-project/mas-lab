//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState, type ReactNode } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
} from "@mui/icons-material";

export interface CollapsibleSectionProps {
  icon: ReactNode;
  label: string;
  hint?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

/** A collapsible left-panel section with an iconed, uppercase header. */
export function CollapsibleSection({
  icon,
  label,
  hint,
  defaultOpen = true,
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        onClick={() => setOpen((o) => !o)}
        sx={{
          px: 1,
          pt: 1.75,
          pb: 0.5,
          cursor: "pointer",
          userSelect: "none",
          "&:hover .section-label": { color: "text.primary" },
        }}
      >
        {open ? (
          <ExpandMoreIcon sx={{ fontSize: 16, color: "text.disabled" }} />
        ) : (
          <ChevronRightIcon sx={{ fontSize: 16, color: "text.disabled" }} />
        )}
        {icon}
        <Typography
          className="section-label"
          variant="overline"
          sx={{
            color: "text.secondary",
            fontWeight: 700,
            lineHeight: 1.4,
            letterSpacing: "0.07em",
          }}
        >
          {label}
        </Typography>
        {hint && (
          <Typography
            variant="overline"
            sx={{
              color: "text.disabled",
              fontWeight: 500,
              letterSpacing: "0.05em",
            }}
          >
            · {hint}
          </Typography>
        )}
      </Stack>
      <Collapse in={open} unmountOnExit>
        {children}
      </Collapse>
    </Box>
  );
}
