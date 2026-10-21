//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { apiJson } from "@/api/client";

export type UploadResult = {
  upload_id: string;
  name: string;
  size: number;
};

export async function uploadFile(file: File): Promise<UploadResult> {
  const body = new FormData();
  body.append("file", file);
  return apiJson<UploadResult>("/api/uploads", { method: "POST", body });
}
