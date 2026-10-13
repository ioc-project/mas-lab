//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Box, List, Typography } from "@mui/material";
import {
  ImageOutlined as PlotsIcon,
  DescriptionOutlined as OutputsIcon,
  PlayArrowRounded as RunsIcon,
} from "@mui/icons-material";
import { CollapsibleSection } from "./CollapsibleSection";
import { PlotThumbnail } from "./PlotThumbnail";
import { OutputFileRow } from "./OutputFileRow";
import { RunGroupItem } from "./RunGroupItem";
import { AllFilesSection } from "./AllFilesSection";
import { TreeNode } from "./TreeNode";
import type { FileTreeEntry } from "@/api/apiCalls";
import type { FlatFile, RunGroup } from "../utils";

export interface GroupedResultsProps {
  experimentId: string;
  tree: FileTreeEntry[];
  plots: FlatFile[];
  outputFiles: FlatFile[];
  runGroups: RunGroup[];
  allFilesHint: string;
  useGrouped: boolean;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}

/**
 * The left results panel: grouped Plots / Output files / Runs sections plus an
 * "All files (advanced)" escape hatch. Falls back to the raw tree when no
 * heuristic matched.
 */
export function GroupedResults({
  experimentId,
  tree,
  plots,
  outputFiles,
  runGroups,
  allFilesHint,
  useGrouped,
  selectedPath,
  onSelect,
}: GroupedResultsProps) {
  if (!useGrouped) {
    return (
      <>
        <Typography
          variant="subtitle2"
          sx={{ px: 1, py: 1, color: "text.secondary" }}
        >
          Files
        </Typography>
        {tree.map((entry) => (
          <TreeNode
            key={entry.name}
            entry={entry}
            path=""
            selectedPath={selectedPath}
            onSelect={onSelect}
          />
        ))}
      </>
    );
  }

  return (
    <>
      {plots.length > 0 && (
        <CollapsibleSection
          icon={<PlotsIcon sx={{ fontSize: 16, color: "success.main" }} />}
          label="Plots"
        >
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
              gap: 1,
              px: 1,
              pb: 1,
            }}
          >
            {plots.map((file) => (
              <PlotThumbnail
                key={file.path}
                experimentId={experimentId}
                file={file}
                selected={selectedPath === file.path}
                onSelect={onSelect}
              />
            ))}
          </Box>
        </CollapsibleSection>
      )}

      {outputFiles.length > 0 && (
        <CollapsibleSection
          icon={<OutputsIcon sx={{ fontSize: 16, color: "text.secondary" }} />}
          label="Output files"
        >
          <List disablePadding dense sx={{ px: 0.5 }}>
            {outputFiles.map((file) => (
              <OutputFileRow
                key={file.path}
                file={file}
                selected={selectedPath === file.path}
                onSelect={onSelect}
              />
            ))}
          </List>
        </CollapsibleSection>
      )}

      {runGroups.length > 0 && (
        <CollapsibleSection
          icon={<RunsIcon sx={{ fontSize: 18, color: "text.secondary" }} />}
          label="Runs"
          hint="grouped by scenario"
        >
          <Box sx={{ px: 0.5 }}>
            {runGroups.map((group) => (
              <RunGroupItem
                key={group.path}
                group={group}
                selectedPath={selectedPath}
                onSelect={onSelect}
              />
            ))}
          </Box>
        </CollapsibleSection>
      )}

      <Box sx={{ px: 0.5, pt: 1.5, pb: 1 }}>
        <AllFilesSection
          tree={tree}
          hint={allFilesHint}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      </Box>
    </>
  );
}
