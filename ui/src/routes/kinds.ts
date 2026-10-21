//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { KindInfo } from "@/api/items";

export const KIND_TO_PATH: Record<string, string> = {
  application: "applications",
  experiment: "experiments",
  pipeline: "pipelines",
  overlay: "overlays",
  dataset: "datasets",
  scenario: "scenarios",
  agent: "agents",
  tool: "tools",
  skill: "skills",
};

const STABLE_PATHS = new Set(["playground", "control-panel"]);

export function pathForKind(kind: string, catalog?: KindInfo[]): string {
  const fromApi = catalog?.find((entry) => entry.id === kind)?.plural;
  return fromApi || KIND_TO_PATH[kind] || `${kind}s`;
}

export function firstKindPath(
  scope?: { kinds: string[] },
  catalog?: KindInfo[],
): string {
  if (!scope?.kinds?.length) return "applications";
  const preferred = scope.kinds.includes("application") ? "application" : scope.kinds[0];
  return pathForKind(preferred, catalog);
}

export function restPathForScope(
  pathname: string,
  target?: { kinds: string[] },
  catalog?: KindInfo[],
): string {
  const rest = pathname.split("/").slice(3).join("/");
  const slug = rest.split("/")[0];
  const allowed = new Set((target?.kinds ?? []).map((kind) => pathForKind(kind, catalog)));
  STABLE_PATHS.forEach((path) => allowed.add(path));
  if (slug && allowed.has(slug)) return rest ? `/${rest}` : "";
  return `/${firstKindPath(target, catalog)}`;
}
