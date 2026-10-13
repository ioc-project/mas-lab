//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { Box, CircularProgress, Typography } from "@mui/material";
import { ImageOutlined as PlotsIcon } from "@mui/icons-material";
import { fetchExperimentFile } from "@/api/apiCalls";
import { imageDataUri } from "../utils";
import type { FlatFile } from "../utils";

export interface PlotThumbnailProps {
  experimentId: string;
  file: FlatFile;
  selected: boolean;
  onSelect: (path: string) => void;
}

/**
 * A plot thumbnail. Fetches the file content (the /file endpoint returns JSON,
 * not bytes) and renders it inline as a `data:` URI so SVG/raster plots actually
 * appear.
 */
export function PlotThumbnail({
  experimentId,
  file,
  selected,
  onSelect,
}: PlotThumbnailProps) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchExperimentFile(experimentId, file.path)
      .then((r) => {
        if (!cancelled) setContent(r.content);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [experimentId, file.path]);

  const src = content != null ? imageDataUri(file.name, content) : null;

  return (
    <Box
      onClick={() => onSelect(file.path)}
      title={file.path}
      sx={{
        cursor: "pointer",
        borderRadius: 1.5,
        overflow: "hidden",
        border: 2,
        borderColor: selected ? "primary.main" : "divider",
        "&:hover": { borderColor: "primary.main" },
      }}
    >
      <Box
        sx={{
          height: 84,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "common.white",
        }}
      >
        {src ? (
          <img
            src={src}
            alt={file.name}
            loading="lazy"
            style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
          />
        ) : error ? (
          <PlotsIcon sx={{ fontSize: 28, color: "text.disabled" }} />
        ) : (
          <CircularProgress size={18} />
        )}
      </Box>
      <Typography
        variant="caption"
        sx={{
          display: "block",
          px: 0.75,
          py: 0.5,
          textAlign: "center",
          color: "text.secondary",
          bgcolor: "background.paper",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {file.name}
      </Typography>
    </Box>
  );
}
