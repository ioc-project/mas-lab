//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";

import { apiJson } from "./client";

describe("api client", () => {
  it("fetches /api/info against MSW", async () => {
    const info = await apiJson<{ api_version: string; auth: string }>("/api/info");
    expect(info.api_version).toBe("0.3.0");
    expect(info.auth).toBe("none");
  });

  it("does not call deprecated /api/libraries", async () => {
    await expect(apiJson("/api/libraries")).rejects.toThrow();
  });
});
