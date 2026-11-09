//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { SvgIcon } from "@mui/material";
import type { SvgIconProps } from "@open-ui-kit/core";

export default function ArrowLeft(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <svg
        width="24"
        height="25"
        viewBox="0 0 24 25"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        data-testid="arrow-left-icon"
      >
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M15.7957 4.2045C16.2351 4.64384 16.2351 5.35616 15.7957 5.7955L9.09123 12.5L15.7957 19.2045C16.2351 19.6438 16.2351 20.3562 15.7957 20.7955C15.3564 21.2348 14.6441 21.2348 14.2047 20.7955L6.70475 13.2955C6.26541 12.8562 6.26541 12.1438 6.70475 11.7045L14.2047 4.2045C14.6441 3.76517 15.3564 3.76517 15.7957 4.2045Z"
          fill="#E8E9EA"
        />
      </svg>
    </SvgIcon>
  );
}
