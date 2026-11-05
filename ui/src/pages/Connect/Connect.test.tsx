//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { ThemeMode, ThemeProvider } from "@open-ui-kit/core";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import Connect from "./index";

describe("Connect", () => {
  it("renders the connection form", () => {
    // Kit components read tokens from the kit theme, so they need its provider.
    const { getByText, getByLabelText } = render(
      <ThemeProvider defaultMode={ThemeMode.Midnight}>
        <MemoryRouter>
          <Connect />
        </MemoryRouter>
      </ThemeProvider>,
    );
    expect(getByText(/Connect to MAS Lab/i)).toBeTruthy();
    expect(getByLabelText(/Controller address/i)).toBeTruthy();
  });
});
