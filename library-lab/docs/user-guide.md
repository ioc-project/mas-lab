<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# library-lab User Guide

`library-lab` ships public extensions for `mas-lab`, including eval providers and plotting helpers.

## Install

```bash
uv pip install -e library-lab
```

## What is included

- Public eval provider registration (`mcev1`)
- Plot helper exports such as `execution_chain_graph`

## Typical usage

- Use this package when your experiments depend on public eval plugins.
- Keep experiment config aligned with provider names exported by this package.

## Quick verification

1. Install package in editable mode.
2. Run a benchmark referencing the provider.
3. Confirm provider-specific metrics are present in artifacts.

## Troubleshooting

- Provider not detected: verify package is installed in active env.
- Missing metric output: check provider identifier and experiment pipeline step config.
