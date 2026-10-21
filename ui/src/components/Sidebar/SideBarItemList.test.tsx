//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";

import { firstKindPath, pathForKind, restPathForScope } from "@/routes/kinds";

describe("sidebar kinds", () => {
  it("maps lab kinds without applications", () => {
    const kinds = ["experiment", "pipeline", "overlay", "dataset"];
    const slugs = kinds.map((k) => pathForKind(k)).filter(Boolean);
    expect(slugs).toContain("experiments");
    expect(slugs).not.toContain("applications");
  });

  it("keeps a shared kind when switching lab and library", () => {
    const library = { kinds: ["application", "overlay", "dataset"] };
    const rest = restPathForScope("/labs/x/overlays/tune", library);
    expect(rest).toBe("/overlays/tune");
  });

  it("falls back to the first kind the target actually has", () => {
    const lab = { kinds: ["experiment", "pipeline"] };
    expect(firstKindPath(lab)).toBe("experiments");
    expect(restPathForScope("/libraries/demo/applications", lab)).toBe("/experiments");
  });
});
