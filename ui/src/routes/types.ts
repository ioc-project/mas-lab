//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { SvgIconProps } from "@open-ui-kit/core";

export interface SideBarProps {
  title: string;
  icon?: React.ElementType<SvgIconProps>;
  preview?: boolean;
  disabled?: boolean;
  hidden?: boolean;
}

export type AppRoute = {
  name: string;
  path: string;
  element: React.ReactElement;
  sideBarProps?: SideBarProps;
  children?: AppRoute[];
};
