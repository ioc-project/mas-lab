//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useQuery } from "@tanstack/react-query";

import { apiFetch, apiJson, getApiBaseUrl, getAuthToken } from "@/api/client";

export type ArtifactNode = {
  artifact_id: string;
  name: string;
  kind: string;
  size?: number;
  media_type?: string;
  children?: ArtifactNode[];
};

export async function fetchRunArtifacts(runId: string): Promise<ArtifactNode[]> {
  const data = await apiJson<{ artifacts: ArtifactNode[] }>(
    `/api/runs/${encodeURIComponent(runId)}/artifacts`,
  );
  return data.artifacts;
}

export function useRunArtifacts(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "artifacts"],
    queryFn: () => fetchRunArtifacts(runId),
    enabled: !!runId,
  });
}

export async function fetchArtifactText(artifactId: string): Promise<string> {
  const response = await apiFetch(`/api/artifacts/${encodeURIComponent(artifactId)}`);
  if (!response.ok) {
    throw new Error(`Failed to read artifact (${response.status})`);
  }
  return response.text();
}

export async function fetchArtifactBlob(artifactId: string): Promise<Blob> {
  const response = await apiFetch(
    `/api/artifacts/${encodeURIComponent(artifactId)}?download=1`,
  );
  if (!response.ok) {
    throw new Error(`Failed to download artifact (${response.status})`);
  }
  return response.blob();
}

export function artifactUrl(artifactId: string): string {
  const token = getAuthToken();
  const base = `${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}`;
  if (!token) return base;
  const params = new URLSearchParams({ token });
  return `${base}?${params}`;
}

export function useArtifactText(artifactId: string) {
  return useQuery({
    queryKey: ["artifacts", artifactId],
    queryFn: () => fetchArtifactText(artifactId),
    enabled: !!artifactId,
  });
}
