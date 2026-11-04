//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useParams } from "react-router";

export type ScopeKind = "library" | "lab";

/**
 * Splits a scope id ("<kind>:<name>") into its kind and name.
 * No ":" means a bare name, treated as a library. Any prefix other than
 * "lab" is also treated as a library (the prefix is dropped from the name).
 *
 * @example
 * parseScopeId("lab:demo")     // { kind: "lab", name: "demo" }
 * parseScopeId("library:core") // { kind: "library", name: "core" }
 * parseScopeId("core")         // { kind: "library", name: "core" }
 * parseScopeId("foo:bar")      // { kind: "library", name: "bar" }
 */
export function parseScopeId(scopeId: string): { kind: ScopeKind; name: string } {
  const idx = scopeId.indexOf(":");
  if (idx === -1) {
    return { kind: "library", name: scopeId };
  }
  const kind = scopeId.slice(0, idx) === "lab" ? "lab" : "library";
  return { kind, name: scopeId.slice(idx + 1) };
}

/**
 * Normalizes a value into a full scope id. Values that already contain ":"
 * are returned unchanged; bare names become library ids; empty stays empty.
 *
 * @example
 * toScopeId("core")      // "library:core"
 * toScopeId("lab:demo")  // "lab:demo"
 * toScopeId("")          // ""
 */
export function toScopeId(value: string): string {
  if (!value) return "";
  if (value.includes(":")) return value;
  return `library:${value}`;
}

/**
 * Builds the app URL for a scope, with an optional sub-path.
 * Labs live under "/labs", everything else under "/libraries".
 * The name is URL-encoded; `rest` may start with "/" or not.
 *
 * @example
 * scopeHref("lab:demo")                     // "/labs/demo"
 * scopeHref("library:core", "agents")       // "/libraries/core/agents"
 * scopeHref("library:core", "/agents/a1")   // "/libraries/core/agents/a1"
 * scopeHref("lab:my lab")                   // "/labs/my%20lab"
 */
export function scopeHref(scopeId: string, rest = ""): string {
  const { kind, name } = parseScopeId(scopeId);
  const prefix = kind === "lab" ? "/labs" : "/libraries";
  const suffix = !rest ? "" : rest.startsWith("/") ? rest : `/${rest}`;
  return `${prefix}/${encodeURIComponent(name)}${suffix}`;
}

/**
 * Reverse of `scopeHref`: builds a scope id from React Router params.
 * Prefers the new `/:scopeType/:scope` params ("labs" -> lab, anything else
 * -> library), then falls back to the legacy `/:library` param.
 * Returns "" when no scope is in the URL.
 *
 * @example
 * scopeIdFromRoute({ scopeType: "labs", scope: "demo" })      // "lab:demo"
 * scopeIdFromRoute({ scopeType: "libraries", scope: "core" }) // "library:core"
 * scopeIdFromRoute({ library: "core" })                       // "library:core" (legacy URL)
 * scopeIdFromRoute({})                                        // ""
 */
export function scopeIdFromRoute(params: {
  scopeType?: string;
  scope?: string;
  library?: string;
}): string {
  if (params.scopeType && params.scope) {
    const kind = params.scopeType === "labs" ? "lab" : "library";
    return `${kind}:${params.scope}`;
  }
  if (params.library) {
    return toScopeId(params.library);
  }
  return "";
}

/**
 * Hook: the scope id of the current route, or "" outside a scope.
 *
 * @example
 * // at /labs/demo/experiments
 * const scopeId = useScopeId(); // "lab:demo"
 */
export function useScopeId(): string {
  const params = useParams<{ scopeType?: string; scope?: string; library?: string }>();
  return scopeIdFromRoute(params);
}

/**
 * True for the labs catalog landing pages ("/" and "/labs"), i.e. where no
 * single scope is selected.
 *
 * @example
 * isLabsCatalogPath("/")              // true
 * isLabsCatalogPath("/labs")          // true
 * isLabsCatalogPath("/labs/demo")     // false
 * isLabsCatalogPath("/libraries")     // false
 */
export function isLabsCatalogPath(pathname: string): boolean {
  return pathname === "/" || pathname === "/labs";
}

/**
 * The default scope configured on the page via a global, or "" if none
 * (or when not running in a browser). `__MAS_LAB_DEFAULT_SCOPE__` wins over
 * the older `__MAS_LAB_DEFAULT_LIBRARY__`.
 *
 * @example
 * // window.__MAS_LAB_DEFAULT_SCOPE__ = "lab:demo"
 * defaultScopeSetting() // "lab:demo"
 *
 * // only window.__MAS_LAB_DEFAULT_LIBRARY__ = "core"
 * defaultScopeSetting() // "core" (note: not normalized; wrap with toScopeId)
 */
export function defaultScopeSetting(): string {
  if (typeof window === "undefined") return "";
  return window.__MAS_LAB_DEFAULT_SCOPE__ || window.__MAS_LAB_DEFAULT_LIBRARY__ || "";
}
