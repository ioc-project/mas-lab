//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { DesignPatternEntry } from "@/api/apiCalls";

export interface PatternOption {
  value: string;
  label: string;
}

export interface PatternGroup {
  label: string;
  options: PatternOption[];
}

/**
 * Static fallback used while the API is loading or on error, so the design
 * pattern dropdown never renders empty and the canvas keeps working offline.
 */
export const FALLBACK_PATTERN_GROUPS: PatternGroup[] = [
  {
    label: "Standard",
    options: [
      { value: "react", label: "ReAct" },
      { value: "cot", label: "Chain of Thought" },
      { value: "reflection", label: "Reflection" },
    ],
  },
];

/** Human label for a library grouping key returned by the backend. */
function groupLabel(library: string): string {
  if (library === "standard") return "Standard";
  if (library === "lab") return "Experimental";
  return library.charAt(0).toUpperCase() + library.slice(1);
}

/**
 * Reshape the API pattern list into `<optgroup>`-friendly groups, grouping by
 * library ("Standard" vs "Experimental"). Falls back to the static list when
 * no patterns are available.
 */
export function buildPatternGroups(
  patterns: DesignPatternEntry[] | undefined,
): PatternGroup[] {
  if (!patterns || patterns.length === 0) {
    return FALLBACK_PATTERN_GROUPS;
  }

  const byLibrary = new Map<string, PatternOption[]>();
  for (const p of patterns) {
    const options = byLibrary.get(p.library) ?? [];
    options.push({ value: p.type, label: p.label });
    byLibrary.set(p.library, options);
  }

  // Standard first, then experimental, then any other libraries.
  const order = (library: string): number => {
    if (library === "standard") return 0;
    if (library === "lab") return 1;
    return 2;
  };

  return Array.from(byLibrary.entries())
    .sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b))
    .map(([library, options]) => ({ label: groupLabel(library), options }));
}
