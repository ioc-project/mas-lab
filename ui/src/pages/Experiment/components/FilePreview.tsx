//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useMemo } from "react";
import { Box, CircularProgress, Link, Stack, Typography } from "@mui/material";
import { Download as DownloadIcon } from "@mui/icons-material";
import { CodeBlock } from "@/components";
import {
  IMAGE_EXTENSIONS,
  getExtension,
  getLanguageFromPath,
  imageDataUri,
  prettyPath,
} from "../utils";

export interface FilePreviewProps {
  selectedFile: string | null;
  fileContent: string | null;
  fileLoading: boolean;
}

/**
 * The right-hand preview pane. Renders HTML/SVG in a sandboxed iframe, raster
 * images from an inlined data URI, and everything else as syntax-highlighted
 * text — plus a client-side download link built from the fetched content.
 */
export function FilePreview({
  selectedFile,
  fileContent,
  fileLoading,
}: FilePreviewProps) {
  const isHtmlFile = selectedFile?.toLowerCase().endsWith(".html") ?? false;
  const isSvgFile = selectedFile?.toLowerCase().endsWith(".svg") ?? false;
  const isImageFile = selectedFile
    ? IMAGE_EXTENSIONS.has(getExtension(selectedFile))
    : false;
  const isRasterImage = isImageFile && !isSvgFile;

  const downloadHref = useMemo(() => {
    if (!selectedFile || fileContent == null) return "";
    if (isImageFile || isSvgFile) return imageDataUri(selectedFile, fileContent);
    const mime = isHtmlFile ? "text/html" : "text/plain";
    return `data:${mime};charset=utf-8,${encodeURIComponent(fileContent)}`;
  }, [selectedFile, fileContent, isImageFile, isSvgFile, isHtmlFile]);

  if (!selectedFile) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography variant="body1" color="text.secondary">
          Select a file to view its contents
        </Typography>
      </Box>
    );
  }

  if (fileLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", pt: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (fileContent === null) return null;

  return (
    <Box sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ px: 1, py: 0.5, gap: 1 }}
      >
        <Typography
          variant="caption"
          title={selectedFile}
          sx={{
            color: "text.secondary",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {prettyPath(selectedFile)}
        </Typography>
        {downloadHref && (
          <Link
            href={downloadHref}
            download={selectedFile.split("/").pop() ?? "file"}
            underline="hover"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              fontSize: "12px",
              flexShrink: 0,
            }}
          >
            <DownloadIcon sx={{ fontSize: 15 }} />
            download
          </Link>
        )}
      </Stack>
      {isHtmlFile ? (
        <iframe
          srcDoc={fileContent}
          title={selectedFile}
          sandbox="allow-scripts allow-same-origin"
          style={{
            width: "100%",
            height: "calc(100% - 30px)",
            border: "none",
            borderRadius: 4,
            backgroundColor: "#fff",
          }}
        />
      ) : isSvgFile ? (
        <iframe
          srcDoc={`<!DOCTYPE html><html><head><style>body{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#fff}svg{max-width:100%;height:auto}</style></head><body>${fileContent}</body></html>`}
          title={selectedFile}
          sandbox="allow-same-origin"
          style={{
            width: "100%",
            height: "calc(100% - 30px)",
            border: "none",
            borderRadius: 4,
            backgroundColor: "#fff",
          }}
        />
      ) : isRasterImage ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "calc(100% - 30px)",
            backgroundColor: "#fff",
            borderRadius: 1,
            p: 2,
          }}
        >
          <img
            src={imageDataUri(selectedFile, fileContent)}
            alt={selectedFile}
            style={{
              maxWidth: "100%",
              maxHeight: "100%",
              objectFit: "contain",
            }}
          />
        </Box>
      ) : (
        <CodeBlock
          code={fileContent}
          language={getLanguageFromPath(selectedFile)}
        />
      )}
    </Box>
  );
}
