//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiJson } from "@/api/client";

export type KindInfo = {
  id: string;
  plural: string;
  schema_id: string;
  creatable: boolean;
  versioned: boolean;
  children: string[];
  name_pattern: string;
  template_ids: string[];
  display_fields: string[];
};

export type ItemRef = {
  id: string;
  version: string | null;
  ref: string;
  writable: boolean;
  ui_canvas?: boolean;
};

export type ItemGroup = {
  name: string;
  kind: string;
  title: string;
  summary: string;
  versions: string[];
  latest: string | null;
  items: ItemRef[];
  ui_canvas?: boolean;
};

export type ItemDetail = {
  id: string;
  kind: string;
  name: string;
  version: string | null;
  ref: string;
  revision: string;
  writable: boolean;
  manifest: string;
  children: Record<string, string>;
  title: string;
  summary: string;
  versions: string[];
  latest: string | null;
  resolved_id: string;
};

export async function fetchKinds(): Promise<KindInfo[]> {
  const data = await apiJson<{ kinds: KindInfo[] }>("/api/kinds");
  return data.kinds;
}

export function useKinds() {
  return useQuery({
    queryKey: ["kinds"],
    queryFn: fetchKinds,
  });
}

export async function fetchItems(scopeId: string, kind: string, q?: string): Promise<ItemGroup[]> {
  const params = new URLSearchParams({ kind });
  if (q) params.set("q", q);
  const data = await apiJson<{ items: ItemGroup[] }>(
    `/api/scopes/${encodeURIComponent(scopeId)}/items?${params}`,
  );
  return data.items;
}

export function useItems(scopeId: string, kind: string, q?: string) {
  return useQuery({
    queryKey: [scopeId, kind, q ?? ""],
    queryFn: () => fetchItems(scopeId, kind, q),
    enabled: !!scopeId && !!kind,
  });
}

export async function fetchItem(scopeId: string, kind: string, itemId: string): Promise<ItemDetail> {
  return apiJson<ItemDetail>(
    `/api/scopes/${encodeURIComponent(scopeId)}/items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}`,
  );
}

export function useItem(scopeId: string, kind: string, itemId: string) {
  return useQuery({
    queryKey: [scopeId, kind, itemId],
    queryFn: () => fetchItem(scopeId, kind, itemId),
    enabled: !!scopeId && !!kind && !!itemId,
  });
}

export async function createItem(
  scopeId: string,
  body: {
    kind: string;
    name: string;
    version?: string;
    template?: string;
    parent?: string;
    upload_id?: string;
  },
): Promise<ItemDetail> {
  return apiJson<ItemDetail>(`/api/scopes/${encodeURIComponent(scopeId)}/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateItem(
  scopeId: string,
  kind: string,
  itemId: string,
  body: { manifest: string; children?: Record<string, string>; revision: string },
): Promise<ItemDetail> {
  return apiJson<ItemDetail>(
    `/api/scopes/${encodeURIComponent(scopeId)}/items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function forkItem(
  scopeId: string,
  kind: string,
  itemId: string,
  body: { version?: string; name?: string; target_scope?: string },
): Promise<ItemDetail> {
  return apiJson<ItemDetail>(
    `/api/scopes/${encodeURIComponent(scopeId)}/items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}/fork`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function renameItem(
  scopeId: string,
  kind: string,
  itemId: string,
  name: string,
): Promise<{ id: string; ref: string; changed_items: string[] }> {
  return apiJson(
    `/api/scopes/${encodeURIComponent(scopeId)}/items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}/rename`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
  );
}

export async function deleteItem(scopeId: string, kind: string, itemId: string): Promise<void> {
  await apiJson(
    `/api/scopes/${encodeURIComponent(scopeId)}/items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );
}

export async function checkItem(
  scopeId: string,
  body: { kind: string; name: string; version?: string },
): Promise<{ ok: boolean; reason?: string; copy_into?: string[]; id?: string; ref?: string }> {
  return apiJson(`/api/scopes/${encodeURIComponent(scopeId)}/items/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function resolveRef(ref: string): Promise<ItemDetail> {
  const params = new URLSearchParams({ ref });
  return apiJson<ItemDetail>(`/api/resolve?${params}`);
}

export async function validateInScope(
  scopeId: string,
  body: { kind?: string; manifest: string },
): Promise<{ valid: boolean; stderr: string; exit_code: number }> {
  return apiJson(`/api/scopes/${encodeURIComponent(scopeId)}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function useInvalidateItems() {
  const client = useQueryClient();
  return (scopeId: string, kind?: string) => {
    if (kind) {
      void client.invalidateQueries({ queryKey: [scopeId, kind] });
    } else {
      void client.invalidateQueries({ queryKey: [scopeId] });
    }
  };
}

export function useCreateItem(scopeId: string) {
  const invalidate = useInvalidateItems();
  return useMutation({
    mutationFn: (body: Parameters<typeof createItem>[1]) => createItem(scopeId, body),
    onSuccess: (_data, body) => invalidate(scopeId, body.kind),
  });
}
