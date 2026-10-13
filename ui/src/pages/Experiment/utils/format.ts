//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
//
// Presentation helpers: language detection, sizes, paths, image data URIs, and
// per-file-type icons.
import {
  InsertDriveFileOutlined as FileIcon,
  DataObjectRounded as JsonIcon,
  TableChartOutlined as CsvIcon,
  ArticleOutlined as DocIcon,
  type SvgIconComponent,
} from "@mui/icons-material";
import { getExtension } from "./classification";
import { IMAGE_MIME } from "./constants";

/** Map a file path to a syntax-highlighting language id. */
export function getLanguageFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    json: "json",
    jsonl: "json",
    yaml: "yaml",
    yml: "yaml",
    csv: "csv",
    md: "markdown",
    html: "html",
    txt: "plaintext",
    py: "python",
  };
  return map[ext] ?? "plaintext";
}

/** Human-readable byte size, or "" when unknown. */
export function formatSize(bytes?: number): string {
  if (bytes == null || Number.isNaN(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/** Pick a type icon + theme color for a leaf file, from its extension only. */
export function fileIconFor(name: string): {
  Icon: SvgIconComponent;
  color: string;
} {
  const ext = getExtension(name);
  if (ext === ".json" || ext === ".jsonl")
    return { Icon: JsonIcon, color: "info.main" };
  if (ext === ".yaml" || ext === ".yml")
    return { Icon: JsonIcon, color: "text.secondary" };
  if (ext === ".csv") return { Icon: CsvIcon, color: "text.secondary" };
  if (ext === ".md" || ext === ".txt")
    return { Icon: DocIcon, color: "text.secondary" };
  return { Icon: FileIcon, color: "text.secondary" };
}

/** Display a full path with spaced separators, e.g. "results / metrics.csv". */
export function prettyPath(path: string): string {
  return path.split("/").join(" / ");
}

/** MIME type for an image file, from its extension. */
export function imageMime(name: string): string {
  return IMAGE_MIME[getExtension(name)] ?? "application/octet-stream";
}

/**
 * Build a `data:` URI for an image from the API's file `content` (which is
 * text/JSON, never raw bytes). SVG is inlined as URL-encoded markup; raster
 * formats are assumed base64-encoded by the API.
 */
export function imageDataUri(name: string, content: string): string {
  if (getExtension(name) === ".svg") {
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}`;
  }
  return `data:${imageMime(name)};base64,${content}`;
}
