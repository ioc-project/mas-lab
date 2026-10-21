//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";

/**
 * Remote controller: bind a non-loopback address with --token-file and
 * --cors-origin, no shared filesystem with the UI host.
 * Skipped unless MAS_LAB_REMOTE_API is set.
 */
const remote = process.env.MAS_LAB_REMOTE_API;

test.skip(!remote, "Set MAS_LAB_REMOTE_API to run the remote e2e");

test("connects through the connection screen", async ({ page }) => {
  await page.goto("/connect");
  await page.getByLabel(/Controller address/i).fill(remote!);
  if (process.env.MAS_LAB_REMOTE_TOKEN) {
    await page.getByLabel(/Bearer token/i).fill(process.env.MAS_LAB_REMOTE_TOKEN);
  }
  await page.getByRole("button", { name: /Connect/i }).click();
  await expect(page.getByText("MAS-LAB")).toBeVisible({ timeout: 15_000 });
});
