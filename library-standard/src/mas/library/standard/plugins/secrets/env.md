<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# env — secret store from process variables (dev)

| Field | Value |
|-------|--------|
| **ID** | `env` |
| **Type** | `secret_store` |
| **URN** | `mas.secret_store.env` |
| **Implementation** | `EnvSecretStore` in `mas.library.standard.plugins.secrets.env_store` |
| **Default** | no |

Maps `openai` to `MAS_SECRET_OPENAI`; keyed refs append `__KEY`. Development only. See [Secrets](../../../../../../../docs/references/secrets.md).
