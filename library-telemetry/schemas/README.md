# OTel span contracts

Two layers — **do not merge** into one file:

| Layer | Artifact | Validates |
|-------|----------|-----------|
| **Wire envelope** | [`otel_sdk_spans.schema.json`](otel_sdk_spans.schema.json) | JSON shape: `name`, `context`, timestamps, `attributes` bag |
| **MAS semantics** | [`mas.spanspec.yaml`](mas.spanspec.yaml) | Per `span.name`: required/recommended `mas.*` attrs (L1–L4) |

Both are enforced by `mas.library.telemetry.verification`:

- `validate_spans_json_schema` — wire envelope
- `SpanValidator` — MAS SpanSpec (L1–L4 conformance)
- `verify_otel_file` — runs structural checks + both of the above

Conformance levels (ascending strictness):

| Level | Name | Meaning |
|-------|------|---------|
| L1 | genai-semconv | OTel GenAI semantic conventions |
| L2 | observe | observe-sdk (required for UI) |
| L3 | openclaw | MAS Framework observability (full span taxonomy) |
| L4 | full | All recommended fields present (complete KG) |

**Native events (source of truth):** `library-kg/schemas/events.schema.json` + `events.spec.yaml`
**KG (downstream):** `library-kg/.../core/verifier.py` + SHACL

These files are shipped inside the wheel at
`mas/library/telemetry/schemas/` (copied under `src/`), so the
validators resolve them without any repo checkout.
