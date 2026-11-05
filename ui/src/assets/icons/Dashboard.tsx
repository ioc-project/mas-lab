//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { SvgIcon } from '@mui/material';
import type { SvgIconProps } from '@open-ui-kit/core';

export function Dashboard(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">
        <rect x="2" y="3.11108" width="8.88889" height="11.1111" rx="1" fill={props.fill} />
        <path
          d="M2 17.5555C2 16.9419 2.49746 16.4444 3.11111 16.4444H9.77778C10.3914 16.4444 10.8889 16.9419 10.8889 17.5555V19.7778C10.8889 20.3914 10.3914 20.8889 9.77778 20.8889H3.11111C2.49746 20.8889 2 20.3914 2 19.7778V17.5555Z"
          fill={props.fill}
        />
        <path
          d="M13.1111 4.2222C13.1111 3.60855 13.6086 3.11108 14.2222 3.11108H20.8889C21.5025 3.11108 22 3.60855 22 4.2222V8.66664C22 9.28029 21.5025 9.77775 20.8889 9.77775H14.2222C13.6086 9.77775 13.1111 9.28029 13.1111 8.66664V4.2222Z"
          fill={props.fill}
        />
        <rect x="13.1111" y="12" width="8.88889" height="8.88889" rx="1" fill={props.fill} />
      </svg>
    </SvgIcon>
  );
}
