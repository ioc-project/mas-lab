//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { getStoredConnection } from "@/api/connection";

declare global {
  interface Window {
    __MAS_LAB_API_BASE_URL__?: string;
    __MAS_LAB_DEFAULT_LIBRARY__?: string;
    __MAS_LAB_DEFAULT_SCOPE__?: string;
    __MAS_LAB_TOKEN__?: string;
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function getApiBaseUrl(): string {
  try {
    const stored = getStoredConnection();
    if (stored?.address) return stored.address.replace(/\/$/, "");
  } catch {
    // localStorage may be unavailable
  }
  if (typeof window !== "undefined" && window.__MAS_LAB_API_BASE_URL__) {
    return window.__MAS_LAB_API_BASE_URL__;
  }
  return (
    import.meta.env.VITE_API_BASE_URL ||
    import.meta.env.VITE_API_URL ||
    "http://localhost:8090"
  );
}

export function getAuthToken(): string {
  try {
    const stored = getStoredConnection();
    if (stored?.token) return stored.token;
  } catch {
    // ignore
  }
  if (typeof window !== "undefined" && window.__MAS_LAB_TOKEN__) {
    return window.__MAS_LAB_TOKEN__;
  }
  return "";
}

/** @deprecated use getApiBaseUrl */
export const API_BASE_URL = getApiBaseUrl();

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const token = getAuthToken();
  if (token && !h.has("Authorization")) {
    h.set("Authorization", `Bearer ${token}`);
  }
  return h;
}

async function parseError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null);
  const detail = body?.detail ?? body;
  const message =
    typeof detail === "string"
      ? detail
      : detail?.reason
        ? String(detail.reason)
        : `Request failed with status ${response.status}`;
  return new ApiError(message, response.status, detail);
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = path.startsWith("http") ? path : `${getApiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: headers(init.headers),
    });
  } catch (err) {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("mas-lab:unreachable"));
    }
    throw err;
  }
  if (response.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("mas-lab:unauthorized"));
  }
  return response;
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function* streamSse(
  path: string,
): AsyncGenerator<{ event: string; data: string }> {
  const response = await apiFetch(path, { headers: { Accept: "text/event-stream" } });
  if (!response.ok) {
    throw await parseError(response);
  }
  if (!response.body) {
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "message";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const block of parts) {
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        yield { event, data };
        event = "message";
      }
    }
  }
}
