//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { ThemeMode, ThemeProvider } from "@open-ui-kit/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
// Same entry the kit's Link uses, so both share one router context.
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { FilePreview } from "./FilePreview";

afterEach(cleanup);

describe("FilePreview download link", () => {
  it("renders a plain anchor with a data: URI and a download name", () => {
    render(
      <ThemeProvider defaultMode={ThemeMode.Midnight}>
        <MemoryRouter>
          <FilePreview
            selectedFile="runs/a/output.txt"
            fileContent="hello world"
            fileLoading={false}
          />
        </MemoryRouter>
      </ThemeProvider>,
    );

    const link = screen.getByRole("link", { name: /download/i });
    expect(link.getAttribute("href")).toBe(
      `data:text/plain;charset=utf-8,${encodeURIComponent("hello world")}`,
    );
    expect(link).toHaveProperty("download", "output.txt");

    // Not routed client-side: the browser handles the click.
    expect(fireEvent.click(link)).toBe(true);
  });
});
