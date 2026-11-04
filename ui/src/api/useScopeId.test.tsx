//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { useScopeId } from "@/api/scope";

function Probe() {
  return <span data-testid="scope">{useScopeId() || "(none)"}</span>;
}

describe("useScopeId", () => {
  afterEach(cleanup);

  it("is empty outside <Routes> (why TopBar must live in the route tree)", () => {
    render(
      <MemoryRouter initialEntries={["/labs/extensions/experiments"]}>
        <Probe />
        <Routes>
          <Route path="/:scopeType/:scope/*" element={null} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("scope").textContent).toBe("(none)");
  });

  it("sees the scope params from a layout route that wraps the page routes", () => {
    render(
      <MemoryRouter initialEntries={["/labs/extensions/experiments"]}>
        <Routes>
          <Route
            path="/"
            element={
              <>
                <Probe />
                <Outlet />
              </>
            }
          >
            <Route path=":scopeType/:scope/experiments" element={null} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("scope").textContent).toBe("lab:extensions");
  });
});
