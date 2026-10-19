/** Fetch JSON with a readable error when the proxy or body is empty. */

export function authToken() {
  try {
    const query = new URLSearchParams(location.search).get("token");
    if (query) sessionStorage.setItem("mas-demo-ui-token", query);
    return query || sessionStorage.getItem("mas-demo-ui-token") || "";
  } catch {
    return "";
  }
}

export function authHeaders(extra) {
  const headers = { ...(extra || {}) };
  const token = authToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export function apiFetch(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  return fetch(url, { ...init, headers });
}

export async function readJson(res, label) {
  const text = await res.text();
  const name = label || res.url || "request";
  if (!res.ok) {
    const hint = text.trim().slice(0, 240);
    throw new Error(`${name} failed (${res.status})${hint ? `: ${hint}` : ""}`);
  }
  if (!text.trim()) {
    throw new Error(`${name} returned an empty body — is mas-lab serve listening on :8090?`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${name} returned non-JSON: ${text.trim().slice(0, 120)}`);
  }
}
