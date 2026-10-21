//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useQuery } from "@tanstack/react-query";

import { apiJson } from "@/api/client";

export type EditTarget = {
  id: string;
  kind: string;
  name: string;
  origin: string;
  kinds: string[];
  owner?: string | null;
};

export type ApiInfo = {
  api_version: string;
  features: string[];
  auth: "none" | "bearer";
  cors_ok: boolean;
  writable_default: boolean;
  edit_targets?: EditTarget[];
};

export async function fetchApiInfo(): Promise<ApiInfo> {
  return apiJson<ApiInfo>("/api/info");
}

export function useApiInfo(enabled = true) {
  return useQuery({
    queryKey: ["api-info"],
    queryFn: fetchApiInfo,
    enabled,
    retry: false,
  });
}
