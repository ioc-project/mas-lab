//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { SvgIcon } from '@mui/material';
import type { SvgIconProps } from '@open-ui-kit/core';

export function Applications(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <svg xmlns="http://www.w3.org/2000/svg" width="25" height="25" fill="none">
        <path fill={props.fill} d="M21.125 5.465h-1.8v6.3h1.8v-6.3Z" />
        <path
          fill={props.fill}
          fillRule="evenodd"
          d="M21.125 5.465v6.3h-18v-6.3h18Zm-3.6 3.15a1.35 1.35 0 1 0-2.7 0 1.35 1.35 0 0 0 2.7 0Zm-5.4-.45h-5.4v.9h5.4v-.9ZM19.325 13.465h1.8v6.3h-18v-6.3h16.2Zm-1.8 3.15a1.35 1.35 0 1 0-2.7 0 1.35 1.35 0 0 0 2.7 0Zm-5.4-.45h-5.4v.9h5.4v-.9Z"
          clipRule="evenodd"
        />
      </svg>
    </SvgIcon>
  );
}
