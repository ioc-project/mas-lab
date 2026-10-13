//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
//
// Tree traversal + grouping logic that turns a raw file tree into the grouped
// results view model (Plots / Output files / Runs). Pure and unit-testable.
import type { FileTreeEntry } from "@/api/apiCalls";
import {
  getExtension,
  isGenericWrapperName,
  isHidden,
  isPlot,
  isRunDir,
  outputRole,
} from "./classification";
import type {
  CollectedRuns,
  FlatFile,
  GroupedOutputs,
  RawRun,
  RunEntry,
  RunGroup,
  SizedEntry,
} from "./types";

/**
 * Collect every leaf run directory in the tree (a run = dir whose name matches
 * r<N>/rep-<N> or that directly holds a run artifact). Hidden subtrees — build
 * scratch and the internal `traces/` / `results/runs/` copies — are skipped, so
 * only the experiment's own canonical trace tree (plus any non-hidden copy of
 * the runs made by a pipeline step) is walked. Recursion stops at each run so nested files aren't
 * mistaken for further runs.
 */
export function collectRuns(tree: FileTreeEntry[]): RawRun[] {
  const runs: RawRun[] = [];
  const walk = (entry: FileTreeEntry, parentSegs: string[]) => {
    if (entry.type !== "directory") return;
    const segments = [...parentSegs, entry.name];
    if (isHidden(segments.join("/"), entry.name)) return;
    if (isRunDir(entry)) {
      runs.push({ segments, entry });
      return;
    }
    for (const child of entry.children ?? []) walk(child, segments);
  };
  for (const entry of tree) walk(entry, []);
  return runs;
}

/**
 * Resolve the group a run belongs to: walk up from the run's parent, skipping
 * generic wrapper segments (itemctx-1, traces, runs, results), to the nearest
 * distinctive ancestor — the scenario. `prefix` is whatever sits above that
 * label (e.g. the output folder of a pipeline step that copied the runs),
 * used to hide duplicate copies.
 */
export function resolveRunGroup(segments: string[]): {
  groupPath: string;
  // scenario label
  label: string;
  // path string used for hiding duplicate copies
  prefix: string;
} {
  const parent = segments.slice(0, -1);
  let end = parent.length;
  while (end > 0 && isGenericWrapperName(parent[end - 1])) end -= 1;
  if (end <= 0) {
    // No distinctive ancestor: the run stands alone as its own group.
    const label = segments[segments.length - 1];
    return { groupPath: segments.join("/"), label, prefix: "" };
  }
  const groupSegs = parent.slice(0, end);
  return {
    groupPath: groupSegs.join("/"),
    label: groupSegs[groupSegs.length - 1],
    prefix: groupSegs.slice(0, -1).join("/"),
  };
}

/**
 * Build the deduplicated Runs view: group runs by resolved scenario label, then
 * for each label keep a single canonical group (the shallowest path — i.e. the
 * experiment's own trace tree — over any copy made by a pipeline step). Every
 * run is counted exactly once.
 */
export function collectRunGroups(tree: FileTreeEntry[]): CollectedRuns {
  const runs = collectRuns(tree);
  const runPaths = runs.map((r) => r.segments.join("/"));

  // Bucket runs by the directory that will label their group.
  const byGroupPath = new Map<
    string,
    { label: string; prefix: string; runs: RunEntry[] }
  >();
  for (const run of runs) {
    const { groupPath, label, prefix } = resolveRunGroup(run.segments);
    let bucket = byGroupPath.get(groupPath);
    if (!bucket) {
      bucket = { label, prefix, runs: [] };
      byGroupPath.set(groupPath, bucket);
    }
    bucket.runs.push({
      name: run.entry.name,
      path: run.segments.join("/"),
      entry: run.entry,
    });
  }

  // Among groups that resolve to the same label, keep the canonical one
  // (fewest path segments; the trace tree beats any nested pipeline-step copy).
  const canonicalByLabel = new Map<string, string>();
  const depth = (p: string) => p.split("/").length;
  for (const groupPath of byGroupPath.keys()) {
    const { label } = byGroupPath.get(groupPath)!;
    const current = canonicalByLabel.get(label);
    if (
      current === undefined ||
      depth(groupPath) < depth(current) ||
      (depth(groupPath) === depth(current) && groupPath < current)
    ) {
      canonicalByLabel.set(label, groupPath);
    }
  }

  // Paths of the groups that won the dedup above (one per scenario label).
  const kept = new Set(canonicalByLabel.values());
  const runGroups: RunGroup[] = [];
  // Containers of the groups that lost, so their files can be excluded later.
  const copyPrefixes: string[] = [];
  for (const [groupPath, bucket] of byGroupPath) {
    if (!kept.has(groupPath)) {
      // Duplicate copy: show no run group for it. Remember the folder that
      // holds it so groupOutputs() also hides its files, not just its runs.
      if (bucket.prefix) copyPrefixes.push(bucket.prefix);
      continue;
    }
    // Canonical group: becomes one entry in the Runs view. `entry` is the
    // first run's tree node, used as the group's representative directory.
    runGroups.push({
      name: bucket.label,
      path: groupPath,
      entry: bucket.runs[0].entry,
      runs: bucket.runs,
      count: bucket.runs.length,
    });
  }
  runGroups.sort((a, b) => a.name.localeCompare(b.name));

  return { runGroups, runPaths, copyPrefixes };
}

/** Flatten every leaf file into { path, name, size }. */
export function flattenFiles(tree: FileTreeEntry[]): FlatFile[] {
  const out: FlatFile[] = [];
  const walk = (entry: FileTreeEntry, parentPath: string) => {
    const fullPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
    if (entry.type === "file") {
      out.push({
        path: fullPath,
        name: entry.name,
        size: (entry as SizedEntry).size,
      });
    } else {
      for (const child of entry.children ?? []) walk(child, fullPath);
    }
  };
  for (const entry of tree) walk(entry, "");
  return out;
}

/** A short, comma-separated hint of the notable files directly inside a run. */
export function runFileHint(entry: FileTreeEntry): string {
  const files = (entry.children ?? [])
    .filter((c) => c.type === "file" && !isHidden(c.name, c.name))
    .map((c) => c.name);
  return files.slice(0, 2).join(", ");
}

/**
 * A friendly summary of what lives under the hidden/advanced entries, derived
 * only from directory/file names (e.g. "cache · staging · spans").
 */
export function hiddenSummary(tree: FileTreeEntry[]): string {
  const tokens: string[] = [];
  const add = (t: string) => {
    if (!tokens.includes(t)) tokens.push(t);
  };
  const walk = (entry: FileTreeEntry, parentPath: string) => {
    const fullPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
    if (isHidden(fullPath, entry.name)) {
      const n = entry.name.toLowerCase();
      if (n.includes("cache")) add("cache");
      else if (n.includes("stage")) add("staging");
      else if (n.includes("span")) add("spans");
      else if (n.includes("log")) add("logs");
      else if (getExtension(entry.name) === ".fingerprint") add("fingerprints");
      return; // don't descend into hidden subtrees
    }
    if (entry.type === "directory") {
      for (const child of entry.children ?? []) walk(child, fullPath);
    }
  };
  for (const entry of tree) walk(entry, "");
  return tokens.slice(0, 3).join(" · ");
}

/** Derive the grouped view model from a raw file tree. */
export function groupOutputs(tree: FileTreeEntry[]): GroupedOutputs {
  const flat = flattenFiles(tree);
  const { runGroups, runPaths, copyPrefixes } = collectRunGroups(tree);
  // Files already shown under Runs (or deliberately dropped) must not be listed
  // again under Output files / Plots. That covers any file located:
  //   - inside a run directory, e.g. `baseline/itemctx-1/r0/run_info.json`;
  //   - inside a copy of the runs made by a pipeline step, which was dropped in
  //     favour of the canonical run group, e.g. `<step-output>/baseline/itemctx-1/r0/...`.
  // `consumed` lists those directories; a file matches if it is one of them or
  // lives anywhere beneath one (the trailing "/" keeps `r1` from matching `r10/`).
  const consumed = [...runPaths, ...copyPrefixes];
  const isConsumed = (path: string) =>
    consumed.some((p) => path === p || path.startsWith(`${p}/`));

  const visible = flat.filter(
    (f) => !isHidden(f.path, f.name) && !isConsumed(f.path),
  );
  const plots = visible.filter((f) => isPlot(f.name));
  const outputFiles = visible
    .filter((f) => !isPlot(f.name))
    .sort(
      (a, b) =>
        outputRole(a.name) - outputRole(b.name) || a.path.localeCompare(b.path),
    );

  return {
    plots,
    outputFiles,
    runGroups,
    useGrouped:
      plots.length > 0 || outputFiles.length > 0 || runGroups.length > 0,
  };
}
