//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useQuery } from "@tanstack/react-query";

import { apiJson } from "@/api/client";
import { defaultScopeSetting } from "@/api/scope";

/** Top-bar / picker value for the labs catalog (no selected lab). */
export const LABS_CATALOG_ID = "all";

export type Scope = {
  id: string;
  kind: "library" | "lab";
  name: string;
  title: string;
  description: string;
  origin: "package" | "workspace" | "lab" | "path" | string;
  writable: boolean;
  kinds: string[];
  libraries?: string[];
  owner?: string;
  counts?: Record<string, number>;
};

export async function fetchScopes(): Promise<Scope[]> {
  const data = await apiJson<{ scopes: Scope[] }>("/api/scopes");
  return data.scopes;
}

export async function createScope(body: {
  kind: "lab";
  name: string;
  description?: string;
  local_library?: boolean;
}): Promise<Scope> {
  return apiJson<Scope>("/api/scopes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function useScopes() {
  return useQuery({
    queryKey: ["scopes"],
    queryFn: fetchScopes,
  });
}

export async function fetchScope(scopeId: string): Promise<Scope> {
  return apiJson<Scope>(`/api/scopes/${encodeURIComponent(scopeId)}`);
}

export function useScope(scopeId: string) {
  return useQuery({
    queryKey: ["scopes", scopeId],
    queryFn: () => fetchScope(scopeId),
    enabled: !!scopeId,
  });
}

export async function fetchVisibleLibraries(scopeId: string): Promise<Scope[]> {
  const data = await apiJson<{ libraries: Scope[] }>(
    `/api/scopes/${encodeURIComponent(scopeId)}/visible-libraries`,
  );
  return data.libraries;
}

export function useVisibleLibraries(scopeId: string) {
  return useQuery({
    queryKey: ["scopes", scopeId, "visible-libraries"],
    queryFn: () => fetchVisibleLibraries(scopeId),
    enabled: !!scopeId,
  });
}

export function pickDefaultScope(scopes: Scope[] | undefined): string {
  if (!scopes?.length) return "";
  const setting = defaultScopeSetting();
  if (setting) {
    const match =
      scopes.find((s) => s.id === setting) ||
      scopes.find((s) => s.name === setting) ||
      scopes.find((s) => s.id === `library:${setting}`) ||
      scopes.find((s) => s.id === `lab:${setting}`);
    if (match) return match.id;
  }
  const withItems = scopes.find((s) => Object.values(s.counts ?? {}).some((n) => n > 0));
  return (withItems ?? scopes[0]).id;
}

export type ScopeGroups = {
  labs: Scope[];
  libraries: Scope[];
  workspace: Scope[];
  local: Scope[];
  packaged: Scope[];
};

export function groupScopes(scopes: Scope[]): ScopeGroups {
  const labs = scopes.filter((s) => s.kind === "lab");
  const libraries = scopes.filter((s) => s.kind === "library");
  const nested = new Set(labs.flatMap((lab) => lab.libraries ?? []));
  const workspace = libraries.filter((s) => s.origin === "workspace" && !nested.has(s.id));
  const packaged = libraries.filter((s) => s.origin === "package");
  const local = libraries.filter(
    (s) => s.origin !== "package" && s.origin !== "workspace" && !nested.has(s.id),
  );
  return { labs, libraries, workspace, local, packaged };
}

export function pickEditScope(scopes: Scope[], currentId: string, kind?: string): string {
  const supports = (scope: Scope) =>
    scope.writable && (!kind || scope.kinds.includes(kind));
  const current = scopes.find((scope) => scope.id === currentId);
  if (current && supports(current)) return current.id;

  if (current?.kind === "lab") {
    const owned = (current.libraries ?? [])
      .map((id) => scopes.find((scope) => scope.id === id))
      .filter((scope): scope is Scope => !!scope && supports(scope));
    if (owned[0]) return owned[0].id;
  }

  if (current?.kind === "library" && current.owner) {
    const owner = scopes.find((scope) => scope.id === current.owner);
    if (owner && supports(owner)) return owner.id;
    const siblings = scopes.filter((scope) => scope.owner === current.owner && supports(scope));
    if (siblings[0]) return siblings[0].id;
  }

  const { workspace, labs, local, libraries } = groupScopes(scopes);
  const preferred = [...labs, ...workspace, ...local, ...libraries.filter((s) => s.writable)];
  return preferred.find(supports)?.id || currentId;
}

export type ScopeConfig = Record<string, Record<string, string>>;

export async function fetchScopeConfig(scopeId: string): Promise<ScopeConfig> {
  return apiJson<ScopeConfig>(`/api/scopes/${encodeURIComponent(scopeId)}/config`);
}

export function useScopeConfig(scopeId: string) {
  return useQuery({
    queryKey: ["scopes", scopeId, "config"],
    queryFn: () => fetchScopeConfig(scopeId),
    enabled: !!scopeId,
  });
}
