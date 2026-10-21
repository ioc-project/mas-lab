//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";

test("app shell renders", async ({ page }) => {
  await page.goto("/connect");
  await expect(page.getByText("Connect to MAS Lab")).toBeVisible();
});
