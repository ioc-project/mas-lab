//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { CreateItemDialog } from "./index";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("CreateItemDialog", () => {
  it("renders kind, scope, and name fields", async () => {
    const { findByLabelText, getByLabelText } = render(
      <CreateItemDialog
        open
        kind="application"
        scopeId="library:versioned_lib"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
      { wrapper },
    );
    expect(await findByLabelText(/Name/i)).toBeTruthy();
    expect(getByLabelText(/Scope/i)).toBeTruthy();
  });
});
