//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { SvgIcon } from '@mui/material';
import type { SvgIconProps } from '@open-ui-kit/core';

export function Transport(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M28 9H4V23H28V9ZM4 6C2.34315 6 1 7.34315 1 9V23C1 24.6569 2.34315 26 4 26H28C29.6569 26 31 24.6569 31 23V9C31 7.34315 29.6569 6 28 6H4Z"
          fill="currentColor"
        />
        <path
          d="M25 16C25 17.1046 24.1046 18 23 18C21.8954 18 21 17.1046 21 16C21 14.8954 21.8954 14 23 14C24.1046 14 25 14.8954 25 16Z"
          fill="currentColor"
        />
      </svg>
    </SvgIcon>
  );
}
