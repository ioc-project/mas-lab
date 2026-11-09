//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { SvgIcon } from '@mui/material';
import type { SvgIconProps } from '@open-ui-kit/core';

export function Search(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none">
        <path
          fill={props.fill}
          fillRule="evenodd"
          d="M16.293 16.293a1 1 0 0 1 1.414 0l4 4a1 1 0 0 1-1.414 1.414l-4-4a1 1 0 0 1 0-1.414ZM10 8a3 3 0 0 1 3-3h6a1 1 0 1 1 0 2h-6a1 1 0 1 0 0 2 3 3 0 1 1 0 6H6.5a1 1 0 1 1 0-2H13a1 1 0 1 0 0-2 3 3 0 0 1-3-3Z"
          clipRule="evenodd"
        />
        <path
          fill={props.fill}
          fillRule="evenodd"
          d="M13.561 3.543a7.794 7.794 0 0 0-4.61-.362 7.715 7.715 0 0 0-3.985 2.306 7.456 7.456 0 0 0-1.91 4.118 7.362 7.362 0 0 0 .846 4.435 7.554 7.554 0 0 0 3.3 3.153 7.781 7.781 0 0 0 8.723-1.28 7.487 7.487 0 0 0 2.232-3.962 1 1 0 0 1 1.959.402 9.487 9.487 0 0 1-2.826 5.022 9.781 9.781 0 0 1-10.971 1.613 9.554 9.554 0 0 1-4.172-3.99A9.362 9.362 0 0 1 1.07 9.36a9.456 9.456 0 0 1 2.42-5.223A9.715 9.715 0 0 1 8.509 1.23a9.794 9.794 0 0 1 5.794.454 1 1 0 0 1-.742 1.858ZM22.5 6a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z"
          clipRule="evenodd"
        />
      </svg>
    </SvgIcon>
  );
}
