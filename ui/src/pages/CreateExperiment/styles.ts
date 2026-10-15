//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { type Theme } from "@mui/material";
import { keyframes } from "@mui/material/styles";
import type { SystemStyleObject } from "@mui/system";

// Expanding ring used to draw attention to a button.
// The ring colour comes from the --pulse-color custom property set in sx.
const pulseRing = keyframes`
  0% { box-shadow: 0 0 0 0 var(--pulse-color); }
  70% { box-shadow: 0 0 0 8px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
`;

export const pulseRingStyle = (theme: Theme): SystemStyleObject<Theme> => ({
  "--pulse-color": theme.palette.vars.interactivePrimaryDefaultDefault,
  animation: `${pulseRing} 1.8s ease-out infinite`,
  "@media (prefers-reduced-motion: reduce)": {
    animation: "none",
  },
});
