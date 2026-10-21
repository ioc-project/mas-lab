//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0

const STORAGE_KEY = "mas-lab.connection";

export type StoredConnection = {
  address: string;
  token: string;
};

export function getStoredConnection(): StoredConnection | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredConnection;
    if (!parsed || typeof parsed.address !== "string") return null;
    return { address: parsed.address, token: parsed.token || "" };
  } catch {
    return null;
  }
}

export function setStoredConnection(connection: StoredConnection): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(connection));
  } catch {
    // ignore quota / private mode
  }
}

export function clearStoredConnection(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
