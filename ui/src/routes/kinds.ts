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

/**
 * Maps an item kind to its URL path segment.
 * Lookup order: API catalog `plural` -> local `KIND_TO_PATH` -> `${kind}s`.
 *
 * @example
 * pathForKind("agent")                                   // "agents"
 * pathForKind("skill", [{ id: "skill", plural: "abilities" }]) // "abilities" (catalog wins)
 * pathForKind("widget")                                  // "widgets" (fallback)
 */
export function pathForKind(kind: string, catalog?: KindInfo[]): string {
  const fromApi = catalog?.find((entry) => entry.id === kind)?.plural;
  return fromApi || KIND_TO_PATH[kind] || `${kind}s`;
}

/**
 * Picks the default page (path segment) to open for a scope.
 * Prefers "application" if the scope has it, otherwise its first kind.
 * Falls back to "applications" when the scope has no kinds.
 *
 * @example
 * firstKindPath({ kinds: ["experiment", "application"] }) // "applications"
 * firstKindPath({ kinds: ["experiment", "pipeline"] })    // "experiments"
 * firstKindPath({ kinds: [] })                            // "applications"
 * firstKindPath(undefined)                                // "applications"
 */
export function firstKindPath(
  scope?: { kinds: string[] },
  catalog?: KindInfo[],
): string {
  if (!scope?.kinds?.length) return "applications";
  const preferred = scope.kinds.includes("application") ? "application" : scope.kinds[0];
  return pathForKind(preferred, catalog);
}

/**
 * Used when switching scopes: returns the part of the current URL that comes
 * after `/:scopeType/:scope`, so the user stays on the same page in the new
 * scope. If the target scope doesn't support that page, returns its default
 * page instead (see `firstKindPath`).
 *
 * Pages in `STABLE_PATHS` (playground, control-panel) exist in every scope.
 *
 * @example
 * // Target scope supports overlays -> keep the same sub-path
 * restPathForScope("/labs/x/overlays/tune", { kinds: ["overlay"] }) // "/overlays/tune"
 *
 * // Stable page is always kept
 * restPathForScope("/labs/x/playground", { kinds: ["agent"] })      // "/playground"
 *
 * // Target scope has no experiments -> fall back to its default page
 * restPathForScope("/libraries/demo/experiments", { kinds: ["agent"] }) // "/agents"
 *
 * // No sub-path (just the scope root) -> default page
 * restPathForScope("/labs/x", { kinds: ["experiment"] })            // "/experiments"
 */
export function restPathForScope(
  pathname: string,
  target?: { kinds: string[] },
  catalog?: KindInfo[],
): string {
  // "/labs/x/overlays/tune" -> ["", "labs", "x", "overlays", "tune"] -> "overlays/tune"
  const rest = pathname.split("/").slice(3).join("/");
  // First segment of the sub-path, e.g. "overlays"
  const slug = rest.split("/")[0];
  // Pages the target scope can show: its kinds (as path segments) + stable pages
  const allowed = new Set((target?.kinds ?? []).map((kind) => pathForKind(kind, catalog)));
  STABLE_PATHS.forEach((path) => allowed.add(path));
  if (slug && allowed.has(slug)) return rest ? `/${rest}` : "";
  return `/${firstKindPath(target, catalog)}`;
}
