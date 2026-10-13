//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { FileTreeEntry } from "@/api/apiCalls";

/** A file-tree entry that may optionally carry a byte size from the API. */
export type SizedEntry = FileTreeEntry & { size?: number };

/** A leaf file flattened out of the tree, keyed by its full "/"-joined path. */
export interface FlatFile {
  path: string;
  name: string;
  size?: number;
}

/** A single run within a run group (a directory the user can drill into). */
export interface RunEntry {
  name: string;
  path: string;
  entry: FileTreeEntry;
}

/** A collapsed group of runs (typically one scenario). */
export interface RunGroup {
  name: string;
  path: string;
  entry: FileTreeEntry;
  runs: RunEntry[];
  count: number;
}

/** A run directory discovered during the tree walk, with its path segments. */
export interface RawRun {
  segments: string[];
  entry: FileTreeEntry;
}

/** Result of collecting run groups from a tree. */
export interface CollectedRuns {
  runGroups: RunGroup[];
  /** Full paths of every run leaf (for excluding run files from Output files). */
  runPaths: string[];
  /** Container prefixes of dropped duplicate groups (hidden from Output files). */
  copyPrefixes: string[];
}

/** The grouped view model derived from a raw file tree. */
export interface GroupedOutputs {
  plots: FlatFile[];
  outputFiles: FlatFile[];
  runGroups: RunGroup[];
  /** False when no heuristic matched: caller should fall back to the raw tree. */
  useGrouped: boolean;
}
