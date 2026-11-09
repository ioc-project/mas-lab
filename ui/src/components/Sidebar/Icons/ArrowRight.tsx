//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { SvgIcon } from "@mui/material";
import type { SvgIconProps } from "@open-ui-kit/core";

export default function ArrowRight(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <svg
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        data-testid="arrow-right-icon"
      >
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M8.20475 3.7045C8.64409 3.26517 9.3564 3.26517 9.79574 3.7045L17.2957 11.2045C17.7351 11.6438 17.7351 12.3562 17.2957 12.7955L9.79574 20.2955C9.3564 20.7348 8.64409 20.7348 8.20475 20.2955C7.76541 19.8562 7.76541 19.1438 8.20475 18.7045L14.9093 12L8.20475 5.2955C7.76541 4.85616 7.76541 4.14384 8.20475 3.7045Z"
          fill="#E8E9EA"
        />
      </svg>
    </SvgIcon>
  );
}
