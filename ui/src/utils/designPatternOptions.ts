//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
export interface DesignPatternEntry {
  type: string;
  label: string;
  library: string;
  description?: string;
  config_keys?: string[];
}

export interface PatternOption {
  value: string;
  label: string;
}

export interface PatternGroup {
  label: string;
  options: PatternOption[];
}

/** Empty catalog while `/api/choices/design-patterns` is loading. */

/** Human label for a library grouping key returned by the backend. */
function groupLabel(library: string): string {
  if (library === "standard") return "Standard";
  if (library === "lab") return "Experimental";
  return library.charAt(0).toUpperCase() + library.slice(1);
}

/**
 * Reshape the API pattern list into `<optgroup>`-friendly groups, grouping by
 * library ("Standard" vs "Experimental").
 */
export function buildPatternGroups(
  patterns: DesignPatternEntry[] | undefined,
): PatternGroup[] {
  if (!patterns || patterns.length === 0) {
    return [];
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
