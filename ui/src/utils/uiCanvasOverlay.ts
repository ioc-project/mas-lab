//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0

/** Canonical overlay name/filename the UI auto-binds as canvas layout. */
export const UI_CANVAS_OVERLAY_NAME = "ui-canvas";

export function isUiCanvasOverlay(entry: {
  name?: string;
  path?: string;
  ui_canvas?: boolean | string;
}): boolean {
  if (entry.ui_canvas === true || entry.ui_canvas === "true") return true;
  if (entry.name === UI_CANVAS_OVERLAY_NAME) return true;
  const file = entry.path?.split("/").pop() ?? "";
  return file === `${UI_CANVAS_OVERLAY_NAME}.yaml` || file === `${UI_CANVAS_OVERLAY_NAME}.yml`;
}
