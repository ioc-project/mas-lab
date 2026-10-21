//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";

import { fetchScopes, groupScopes, pickEditScope, type Scope } from "./scopes";

describe("scopes client", () => {
  it("lists libraries and labs without calling /api/libraries", async () => {
    const scopes = await fetchScopes();
    expect(scopes.some((s) => s.id === "library:versioned_lib")).toBe(true);
    expect(scopes.some((s) => s.id === "lab:lonely")).toBe(true);
    const { labs, workspace, local, packaged } = groupScopes(scopes);
    expect(labs).toHaveLength(1);
    expect(workspace.some((s) => s.id === "library:workspace")).toBe(true);
    expect(local.some((s) => s.id === "library:versioned_lib")).toBe(true);
    expect(packaged.some((s) => s.id === "library:packaged")).toBe(true);
  });

  it("defaults create/copy to a writable location when the current scope is packaged", () => {
    const scopes: Scope[] = [
      {
        id: "library:packaged",
        kind: "library",
        name: "packaged",
        title: "Packaged",
        description: "",
        origin: "package",
        writable: false,
        kinds: ["application"],
      },
      {
        id: "library:workspace",
        kind: "library",
        name: "workspace",
        title: "Workspace",
        description: "",
        origin: "workspace",
        writable: true,
        kinds: ["application"],
      },
      {
        id: "lab:lonely",
        kind: "lab",
        name: "lonely",
        title: "Lonely",
        description: "",
        origin: "lab",
        writable: true,
        kinds: ["experiment"],
      },
    ];
    expect(pickEditScope(scopes, "library:packaged", "application")).toBe("library:workspace");
    expect(pickEditScope(scopes, "library:workspace", "application")).toBe("library:workspace");
  });

  it("keeps create/copy inside the current lab and its local library", () => {
    const scopes: Scope[] = [
      {
        id: "lab:x",
        kind: "lab",
        name: "x",
        title: "X",
        description: "",
        origin: "lab",
        writable: true,
        kinds: ["experiment", "pipeline", "overlay", "dataset"],
        libraries: ["library:x_lib"],
      },
      {
        id: "library:x_lib",
        kind: "library",
        name: "x_lib",
        title: "X Lib",
        description: "",
        origin: "lab",
        writable: true,
        kinds: ["application", "agent", "overlay", "dataset"],
        owner: "lab:x",
      },
      {
        id: "library:workspace",
        kind: "library",
        name: "workspace",
        title: "Workspace",
        description: "",
        origin: "workspace",
        writable: true,
        kinds: ["application", "agent", "overlay", "dataset"],
      },
    ];
    expect(pickEditScope(scopes, "lab:x", "experiment")).toBe("lab:x");
    expect(pickEditScope(scopes, "lab:x", "application")).toBe("library:x_lib");
    expect(pickEditScope(scopes, "library:x_lib", "experiment")).toBe("lab:x");
    expect(pickEditScope(scopes, "library:x_lib", "application")).toBe("library:x_lib");
  });
});
