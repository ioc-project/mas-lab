//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Tooltip } from "@open-ui-kit/core";
import type { ReactElement, ReactNode } from "react";

export function ScrollableTooltip({
  title,
  children,
  placement = "right",
}: {
  title: ReactNode;
  children: ReactElement;
  placement?: "right" | "top" | "bottom" | "left";
}) {
  return (
    <Tooltip
      title={
        <div
          style={{
            maxHeight: "50vh",
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            fontSize: "0.75rem",
            lineHeight: 1.5,
          }}
        >
          {title}
        </div>
      }
      placement={placement}
      slotProps={{
        tooltip: {
          sx: {
            maxWidth: 600,
            p: 1,
            bgcolor: "grey.900",
            color: "grey.100",
          },
        },
        popper: {
          modifiers: [
            {
              name: "preventOverflow",
              options: { boundary: "viewport", padding: 16 },
            },
          ],
        },
      }}
    >
      {children}
    </Tooltip>
  );
}
