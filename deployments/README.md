<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Deployments

**`local-inproc.yaml`** — default in-process run (see `spec.runtime_id` in component-registry).

Referenced from `config.yaml` (`mas_ctl.deployment: local-inproc`) and copied into tutorial bundles under `deployments/`.

## Local Docker Compose

See [`docker/README.md`](../docker/README.md) for volume mounts, env vars, and one-off commands.

## CI images

Container images are built and published via
`.github/workflows/build-push-ghcr.yaml` and triggered by the release workflow
in `.github/workflows/docker.yml`.

Published images for the release line include:

- `ghcr.io/ioc-project/mas-lab/ui`
- `ghcr.io/ioc-project/mas-lab/backend`

The tag is derived from the release version and git SHA so the image stays tied
to the exact source revision that produced it.
