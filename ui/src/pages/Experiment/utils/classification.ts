//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
//
// Pure, unit-testable predicates over the file tree. They reason only about
// file extension and directory shape, never about specific filenames/schemas.
import type { FileTreeEntry } from "@/api/apiCalls";
import {
  GENERIC_WRAPPER_NAMES,
  HIDDEN_DIR_SEGMENTS,
  HIDDEN_FILE_NAMES,
  ITEMCTX_RE,
  PLOT_EXTENSIONS,
  REP_ID_RE,
  RUN_ARTIFACT_NAMES,
  RUN_ID_RE,
} from "./constants";

/** Lower-cased extension including the leading dot, or "" when none. */
export function getExtension(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i).toLowerCase() : "";
}

/** Files that should surface ONLY under the "All files" section. */
export function isHidden(path: string, name: string): boolean {
  if (name.startsWith(".")) return true;
  const segments = path.split("/").filter(Boolean);
  if (segments.some((s) => HIDDEN_DIR_SEGMENTS.has(s))) return true;
  if (getExtension(name) === ".fingerprint") return true;
  if (HIDDEN_FILE_NAMES.has(name)) return true;
  return false;
}

/** True for image extensions rendered in the Plots grid. */
export function isPlot(name: string): boolean {
  return PLOT_EXTENSIONS.has(getExtension(name));
}

/** True when a directory name looks like a single run (r0, rep-3, foo-rep-3). */
export function isRunIdName(name: string): boolean {
  return RUN_ID_RE.test(name) || REP_ID_RE.test(name);
}

/** True when a directory directly holds a run artifact file. */
export function directlyContainsArtifact(entry: FileTreeEntry): boolean {
  return (entry.children ?? []).some(
    (c) => c.type === "file" && RUN_ARTIFACT_NAMES.has(c.name),
  );
}

/** True when a directory is itself a single run (by name shape or artifact). */
export function isRunDir(entry: FileTreeEntry): boolean {
  return (
    entry.type === "directory" &&
    (isRunIdName(entry.name) || directlyContainsArtifact(entry))
  );
}

/** True when a path segment is a structural wrapper, not a meaningful label. */
export function isGenericWrapperName(name: string): boolean {
  return GENERIC_WRAPPER_NAMES.has(name) || ITEMCTX_RE.test(name);
}

/**
 * Rank an output file by role so reports/summaries surface first:
 *   0 markdown, 1 summary/report json, 2 other json, 3 csv, 4 the rest.
 */
export function outputRole(name: string): number {
  const ext = getExtension(name);
  if (ext === ".md") return 0;
  if (ext === ".json") return /summary|report/i.test(name) ? 1 : 2;
  if (ext === ".csv") return 3;
  return 4;
}
