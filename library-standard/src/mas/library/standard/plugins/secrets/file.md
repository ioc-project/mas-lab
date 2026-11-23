<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# file — secret store on disk

| Field | Value |
|-------|--------|
| **ID** | `file` |
| **Type** | `secret_store` |
| **URN** | `mas.secret_store.file` |
| **Implementation** | `FileSecretStore` in `mas.library.standard.plugins.secrets.file_store` |
| **Default** | yes |

One file per secret under the directory `mas-ctl` passes. See [Secrets](../../../../../../../docs/references/secrets.md).
