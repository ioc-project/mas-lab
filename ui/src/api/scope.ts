//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useParams } from "react-router";

export type ScopeKind = "library" | "lab";

export function parseScopeId(scopeId: string): { kind: ScopeKind; name: string } {
  const idx = scopeId.indexOf(":");
  if (idx === -1) {
    return { kind: "library", name: scopeId };
  }
  const kind = scopeId.slice(0, idx) === "lab" ? "lab" : "library";
  return { kind, name: scopeId.slice(idx + 1) };
}

export function toScopeId(value: string): string {
  if (!value) return "";
  if (value.includes(":")) return value;
  return `library:${value}`;
}

export function scopeHref(scopeId: string, rest = ""): string {
  const { kind, name } = parseScopeId(scopeId);
  const prefix = kind === "lab" ? "/labs" : "/libraries";
  const suffix = !rest ? "" : rest.startsWith("/") ? rest : `/${rest}`;
  return `${prefix}/${encodeURIComponent(name)}${suffix}`;
}

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

export function useScopeId(): string {
  const params = useParams<{ scopeType?: string; scope?: string; library?: string }>();
  return scopeIdFromRoute(params);
}

export function isLabsCatalogPath(pathname: string): boolean {
  return pathname === "/" || pathname === "/labs";
}

export function defaultScopeSetting(): string {
  if (typeof window === "undefined") return "";
  return window.__MAS_LAB_DEFAULT_SCOPE__ || window.__MAS_LAB_DEFAULT_LIBRARY__ || "";
}
