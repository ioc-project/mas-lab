//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiJson, streamSse } from "@/api/client";

export type RunRecord = {
  id: string;
  scope: string;
  kind: string;
  item: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  job_id?: string | null;
  log?: string | null;
  request_summary?: {
    source?: string;
    action?: string;
    query?: string;
  };
};

export async function startRun(body: {
  scope: string;
  kind: "application" | "experiment" | "pipeline";
  item: string;
  overlays?: string[];
  bind?: string;
  query?: string;
  manifest?: string;
  action?: "run" | "analyze" | "import" | "export";
  timeout?: number;
}): Promise<{ id: string; status: string; job_id?: string }> {
  return apiJson("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchRuns(): Promise<RunRecord[]> {
  const data = await apiJson<{ runs: RunRecord[] }>("/api/runs");
  return data.runs;
}

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: fetchRuns,
    refetchInterval: (query) => {
      const rows = query.state.data;
      if (rows?.some((run) => run.status === "pending" || run.status === "running")) {
        return 1500;
      }
      return 2500;
    },
  });
}

export async function fetchRun(id: string): Promise<RunRecord> {
  return apiJson<RunRecord>(`/api/runs/${encodeURIComponent(id)}`);
}

export function useRun(id: string) {
  return useQuery({
    queryKey: ["runs", id],
    queryFn: () => fetchRun(id),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "running" ? 1500 : false;
    },
  });
}

export async function deleteRun(id: string): Promise<void> {
  await apiJson(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function useDeleteRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: deleteRun,
    onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }),
  });
}

export async function* streamRunEvents(id: string) {
  yield* streamSse(`/api/runs/${encodeURIComponent(id)}/events`);
}
