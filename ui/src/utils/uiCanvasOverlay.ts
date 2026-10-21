//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0

/** Layout-only canvas overlay, as flagged by the controller (`spec.target` + empty patch). */
export function isUiCanvasOverlay(entry: {
  ui_canvas?: boolean | string;
}): boolean {
  return entry.ui_canvas === true || entry.ui_canvas === "true";
}
