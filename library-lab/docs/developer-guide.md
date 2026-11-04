<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# library-lab Developer Guide

This guide covers extending public lab-oriented providers and utilities.

## Package layout

- `src/mas/library/lab/eval/`: eval providers
- `src/mas/library/lab/plots.py`: plotting helpers
- `src/mas/library/lab/__init__.py`: public exports

## Extend eval providers

1. Add provider module under `eval/`.
2. Expose stable registration identifier.
3. Keep metric output schema explicit and documented.
4. Add tests validating provider registration and score shape.

## Extend plotting utilities

- Keep plotting helpers pure and reusable.
- Avoid side effects that break notebook or headless usage.
- Document required dataframe columns and expected types.

## Release checklist

- Public exports updated
- Provider IDs documented
- Backward compatibility impact reviewed
- Examples updated to include new provider usage
