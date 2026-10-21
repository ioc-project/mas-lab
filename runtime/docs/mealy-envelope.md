<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Mealy envelope: Python kernel status

This page lists what the envelope wraps and which parts are implemented in the Python kernel.
Kernel developers and reviewers use it as the status checklist. For the formal model, read [automaton-product-model.md](automaton-product-model.md).

Run the envelope tests:

```bash
pytest runtime/tests/test_mealy_envelope.py -q
```

**Implementation:** `runtime/src/mas/runtime/` (`kernel/envelope.py`, `schema/envelope.py`, boundary coordination).

**Formal proofs:** TLA+ specs and TLC gates are maintained outside this OSS release.

---

## 1. What the envelope covers

The envelope is the **authorize → execute → validate** walk around each **scheduled egress operation**:

| Operation kind | Envelope applies |
|----------------|------------------|
| `TOOL_CALL` | Yes |
| `LLM_CALL` | Yes |
| `MEMORY_*` / transport egress | Yes |
| User input, lifecycle pause/abort | No (ingress / ctrl only) |
| Ctl observability transforms | No (terminating adapters) |
| Design-pattern internal evaluate | No until an egress is scheduled |

Profile flags (`mas-ctl chat --without-gov`, `--without-obs`) collapse σ summands as the v2 `EnvelopeProduct` spec describes.

---

## 2. Feature checklist (Python — this repo)

| # | Feature | Status |
|---|---------|--------|
| 1 | `EnvelopeSymbol` alphabet | Implemented |
| 2 | Collapsed σ when obs/gov disabled | Implemented |
| 3 | Egress / ingress chokepoints | Implemented |
| 4 | Execute σ (driver/engine) | Implemented |
| 5 | TOOL / LLM hot paths | Implemented |
| 6 | Memory / transport crossings | Implemented |
| 7 | HITL egress/ingress | Implemented |
| 8 | Context assembly order | Implemented |
| 9 | DP scheduler (`evaluate_next`) | Implemented |
| 10 | `--without-obs` / `--without-gov` | Implemented |

---

## 3. Tests (mas-lab CI)

```bash
pytest runtime/tests/test_mealy_envelope.py -q
pytest tests/test_envelope_prompt_parity.py -q   # via task verify-lab-smoke
```

Kernel envelope unit tests live **in this repo**. Trace replay and TLC gates are **not** part of mas-lab CI.

---

## 4. CLI

```bash
mas-ctl chat agent.yaml --without-gov
mas-ctl chat agent.yaml --without-obs
mas-ctl chat agent.yaml --without-obs --without-gov
```

Maps to `KernelConfig.enable_governance` / `enable_envelope_observability`.

---

## 5. Further reading

- [automaton-product-model.md](automaton-product-model.md)
- [mealy-machines-guide.md](mealy-machines-guide.md)
