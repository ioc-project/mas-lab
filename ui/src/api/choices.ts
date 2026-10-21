//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useQuery } from "@tanstack/react-query";

import { apiJson } from "@/api/client";
import {
  buildPatternGroups,
  type DesignPatternEntry,
} from "@/utils/designPatternOptions";

export type Choice = {
  id: string;
  label?: string;
  ref?: string;
  namespace?: string;
  source?: string;
  default?: boolean;
  [key: string]: unknown;
};

export async function fetchChoices(
  kind: string,
  scopeId?: string,
): Promise<Record<string, unknown>> {
  const params = new URLSearchParams();
  if (scopeId) params.set("scope", scopeId);
  const qs = params.toString();
  return apiJson(`/api/choices/${encodeURIComponent(kind)}${qs ? `?${qs}` : ""}`);
}

export function useChoices(kind: string, scopeId?: string) {
  return useQuery({
    queryKey: ["choices", kind, scopeId ?? ""],
    queryFn: () => fetchChoices(kind, scopeId),
    enabled: !!kind,
  });
}

function namedFromChoices(rows: Choice[] | undefined) {
  return (rows ?? []).map((row) => ({
    name: String(row.ref || row.id),
    description: String(row.label || row.id),
    ref: String(row.ref || row.id),
    namespace: row.namespace,
  }));
}

export function useModelChoices() {
  const query = useChoices("models");
  const payload = query.data as { models?: Choice[]; default?: string } | undefined;
  const models = payload?.models ?? [];
  const fallback = payload?.default || models[0]?.id || "";
  return { ...query, models, defaultModel: fallback };
}

export function useToolChoices(scopeId: string) {
  const query = useChoices("tools", scopeId);
  const tools = namedFromChoices((query.data as { tools?: Choice[] } | undefined)?.tools);
  return { ...query, tools };
}

export function useSkillChoices(scopeId: string) {
  const query = useChoices("skills", scopeId);
  const skills = namedFromChoices((query.data as { skills?: Choice[] } | undefined)?.skills);
  return { ...query, skills };
}

export function useDesignPatternChoices() {
  const query = useChoices("design-patterns");
  const payload = query.data as
    | { patterns?: DesignPatternEntry[]; default?: string }
    | undefined;
  return {
    ...query,
    groups: buildPatternGroups(payload?.patterns),
    defaultType: payload?.default || "",
  };
}

export function useOverlayChoices(scopeId: string) {
  const query = useChoices("overlays", scopeId);
  // Overlay manifests name the overlay by id, not by library:ref.
  const overlays = ((query.data as { overlays?: Choice[] } | undefined)?.overlays ?? []).map(
    (row) => ({
      name: row.id,
      ref: String(row.ref || row.id),
      description: String(row.label || row.id),
    }),
  );
  return { ...query, overlays };
}
