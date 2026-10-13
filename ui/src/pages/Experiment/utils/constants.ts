//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
//
// Generic output classification constants.
//
// These reason ONLY about file extension and directory shape — never about any
// specific pipeline's filenames or JSON schema — so grouping works for any
// experiment layout.

// Directory segments that never hold *canonical* results: build scratch space,
// shard logs, and the two well-known internal COPY containers (a "traces/" mirror
// and a "results/runs/" mirror). Anything under these appears only under
// "All files (advanced)".
export const HIDDEN_DIR_SEGMENTS = new Set([
  "_stage",
  "shard-logs",
  "traces",
  "runs",
]);

// Directory names that are structural wrappers, not meaningful group labels.
// When resolving the label for a run's group we skip past these to reach the
// nearest distinctive ancestor (e.g. the scenario). Matches "itemctx", "itemctx-1",
// "traces", "runs", "results" — never a scenario name.
export const GENERIC_WRAPPER_NAMES = new Set(["traces", "runs", "results"]);
export const ITEMCTX_RE = /^itemctx(-?\d+)?$/i;

export const HIDDEN_FILE_NAMES = new Set([
  "observe_sdk_spans.jsonl",
  "session_mappings.jsonl",
  "manifest.json",
  "metadata.yaml",
]);

export const PLOT_EXTENSIONS = new Set([
  ".svg",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
]);

// Raster + svg image extensions the preview pane can render inline.
export const IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".bmp",
]);

export const IMAGE_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".bmp": "image/bmp",
  ".svg": "image/svg+xml",
};

export const RUN_ARTIFACT_NAMES = new Set([
  "events.jsonl",
  "run_info.json",
  "run.json",
]);
export const RUN_ID_RE = /^r\d+$/;
export const REP_ID_RE = /(^|-)rep-?\d+$/;

/** How many runs to show in a group before the "… N more" affordance. */
export const RUNS_PREVIEW_CAP = 3;
