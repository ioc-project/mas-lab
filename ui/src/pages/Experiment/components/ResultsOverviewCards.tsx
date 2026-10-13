//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { type ReactNode } from "react";
import { Box, Paper, Stack, Typography } from "@mui/material";
import { Tooltip } from "@open-ui-kit/core";
import type { FlatFile, RunGroup } from "../utils";

export interface ResultsOverviewCardsProps {
  metadata: Record<string, unknown>;
  plots: FlatFile[];
  outputFiles: FlatFile[];
  runGroups: RunGroup[];
  /** True while a benchmark run for this experiment is in flight. */
  running?: boolean;
}

/** Read the first present string value across candidate metadata keys. */
function readMetaString(
  meta: Record<string, unknown>,
  keys: string[],
): string | undefined {
  for (const key of keys) {
    const raw = meta[key];
    if (typeof raw === "string" && raw.trim() !== "") return raw;
  }
  return undefined;
}

/** Read the first present finite number across candidate metadata keys. */
function readMetaNumber(
  meta: Record<string, unknown>,
  keys: string[],
): number | undefined {
  for (const key of keys) {
    const raw = meta[key];
    if (typeof raw === "number" && Number.isFinite(raw)) return raw;
    if (typeof raw === "string" && raw.trim() !== "") {
      const n = Number(raw);
      if (!Number.isNaN(n)) return n;
    }
  }
  return undefined;
}

/** Read a non-empty nested `metadata.error` string, if present. */
function readNestedError(meta: Record<string, unknown>): string | undefined {
  const nested = meta["metadata"];
  if (nested && typeof nested === "object") {
    const err = (nested as Record<string, unknown>)["error"];
    if (typeof err === "string" && err.trim() !== "") return err;
  }
  return undefined;
}

/** Turn a raw status token like "in_progress" into "In Progress". */
function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

interface StatusInfo {
  word: string;
  color: string;
  sub?: string;
  tooltip?: string;
}

/**
 * Derive experiment Status from metadata.yaml ONLY (never plots/artifacts).
 * Returns undefined when there's no authoritative signal, so the card self-omits.
 */
function deriveStatus(meta: Record<string, unknown>): StatusInfo | undefined {
  const raw = readMetaString(meta, ["status"]);
  const s = raw?.trim().toLowerCase();

  // 1. Scenario counts are the most authoritative signal: a run that didn't
  //    finish every scenario is Partial regardless of the top-level status.
  const failed = readMetaNumber(meta, ["failed_scenarios"]);
  const completed = readMetaNumber(meta, ["completed_scenarios"]);
  const total = readMetaNumber(meta, ["total_scenarios"]);
  if (failed !== undefined && failed > 0) {
    return { word: "Partial", color: "warning.main", sub: `${failed} failed` };
  }
  if (completed !== undefined && total !== undefined && completed < total) {
    return {
      word: "Partial",
      color: "warning.main",
      sub: `${completed}/${total} completed`,
    };
  }

  // 2-4. Top-level status is authoritative when counts agree (or are absent).
  if (s === "completed") return { word: "Complete", color: "success.main" };
  if (s === "failed" || s === "error") {
    return { word: "Failed", color: "error.main" };
  }
  if (s === "running" || s === "in_progress") {
    return { word: "Running", color: "info.main" };
  }

  // 5. Nested error is DEMOTED: it can only fail an experiment when the
  //    top-level status doesn't already assert completed/running. This keeps a
  //    stale `metadata.error` from overriding a genuine `status: completed`.
  const nestedError = readNestedError(meta);
  if (nestedError && s !== "completed" && s !== "running") {
    return { word: "Failed", color: "error.main", tooltip: nestedError };
  }

  // 6. Recognized-but-unhandled status -> title-cased passthrough.
  if (raw) return { word: titleCase(raw), color: "text.secondary" };

  // 7. No status signal at all -> omit the card (never infer from artifacts).
  return undefined;
}

/** Parse an ISO-ish timestamp into a valid Date, or undefined. */
function parseDate(value: string | undefined): Date | undefined {
  if (!value) return undefined;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? undefined : d;
}

/** Compact relative time, e.g. "just now", "5m ago", "2h ago", "3d ago". */
function formatRelative(date: Date): string {
  const diffMs = Date.now() - date.getTime();
  const sec = Math.round(Math.abs(diffMs) / 1000);
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  const mo = Math.round(day / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.round(mo / 12)}y ago`;
}

/** Absolute timestamp that always includes the year, e.g. "30 Aug 2026, 16:12". */
function formatAbsolute(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

interface OverviewCardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  dotColor?: string;
  valueColor?: string;
  tooltip?: string;
}

/** A single stat card composed from theme-aware primitives. */
function OverviewCard({
  label,
  value,
  sub,
  dotColor,
  valueColor,
  tooltip,
}: OverviewCardProps) {
  const card = (
    <Paper
      variant="outlined"
      sx={{
        px: 1,
        pt: 0,
        pb: 1,
        flex: "1 1 150px",
        minWidth: 150,
        borderRadius: 1.5,
      }}
    >
      <Typography
        variant="overline"
        sx={{
          color: "text.secondary",
          fontWeight: 600,
          letterSpacing: "0.06em",
          lineHeight: 1.4,
        }}
      >
        {label}
      </Typography>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mt: 0.25 }}
      >
        {dotColor && (
          <Box
            sx={{
              width: 9,
              height: 9,
              borderRadius: "50%",
              bgcolor: dotColor,
              flexShrink: 0,
            }}
          />
        )}
        <Typography
          variant="h6"
          sx={{ fontWeight: 600, lineHeight: 1.2, color: valueColor }}
        >
          {value}
        </Typography>
      </Stack>
      {sub && (
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", display: "block", mt: 0.25 }}
        >
          {sub}
        </Typography>
      )}
    </Paper>
  );

  return tooltip ? (
    <Tooltip title={tooltip} placement="top">
      {card}
    </Tooltip>
  ) : (
    card
  );
}

/**
 * A row of at-a-glance stat cards for the experiment results header. Each card
 * self-omits when its value can't be derived, so the row degrades gracefully.
 * All counts are lifted from the same bucket classification the sections below
 * use, so they always match.
 */
export function ResultsOverviewCards({
  metadata,
  plots,
  outputFiles,
  runGroups,
  running = false,
}: ResultsOverviewCardsProps) {
  const cards: ReactNode[] = [];

  // 1. Status — a live run wins over the (still-stale) on-disk status, so a
  //    rerun shows "Running" immediately; otherwise derive from metadata.
  const status: StatusInfo | undefined = running
    ? { word: "Running", color: "info.main" }
    : deriveStatus(metadata);
  if (status) {
    cards.push(
      <OverviewCard
        key="status"
        label="Status"
        value={status.word}
        sub={status.sub}
        dotColor={status.color}
        valueColor={status.color}
        tooltip={status.tooltip}
      />,
    );
  }

  // 2. Runs — total run count across groups, with a scenario sub-line (the
  //    scenario count still surfaces here).
  const totalRuns = runGroups.reduce((acc, g) => acc + g.count, 0);
  if (totalRuns > 0) {
    cards.push(
      <OverviewCard
        key="runs"
        label="Runs"
        value={totalRuns}
        sub={plural(runGroups.length, "scenario")}
      />,
    );
  }

  // 3. Artifacts — output files + plots (summed; not double counted).
  const artifacts = outputFiles.length + plots.length;
  if (artifacts > 0) {
    cards.push(
      <OverviewCard
        key="artifacts"
        label="Artifacts"
        value={artifacts}
        sub={`${plural(outputFiles.length, "file")} · ${plural(plots.length, "plot")}`}
      />,
    );
  }

  // 4. Last updated — newest metadata timestamp (the API doesn't expose mtimes).
  const updatedAt = parseDate(
    readMetaString(metadata, ["completed_at", "started_at", "timestamp"]),
  );
  if (updatedAt) {
    cards.push(
      <OverviewCard
        key="updated"
        label="Last updated"
        value={formatRelative(updatedAt)}
        sub={formatAbsolute(updatedAt)}
        tooltip={formatAbsolute(updatedAt)}
      />,
    );
  }

  if (cards.length === 0) return null;

  return (
    <Box
      sx={{
        display: "flex",
        flexWrap: "wrap",
        gap: 1.5,
        mb: 2,
      }}
    >
      {cards}
    </Box>
  );
}
