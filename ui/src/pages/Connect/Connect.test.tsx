//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import Connect from "./index";

describe("Connect", () => {
  it("renders the connection form", () => {
    const { getByText, getByLabelText } = render(
      <MemoryRouter>
        <Connect />
      </MemoryRouter>,
    );
    expect(getByText(/Connect to MAS Lab/i)).toBeTruthy();
    expect(getByLabelText(/Controller address/i)).toBeTruthy();
  });
});
