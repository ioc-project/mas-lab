# Verifying OTel spans

`library-telemetry` verifies OTel spans in three layers, the counterpart of the combined KG validation in `library-kg`. Use `verify_otel_file` to check a spans file before you push it.

| Layer | Function | Checks |
|-------|----------|--------|
| structural | `verify_otel_spans` | required fields, a root span, `mas.boundary` presence, single trace, parent references |
| wire envelope | `validate_spans_json_schema` | JSON shape against `otel_sdk_spans.schema.json` |
| MAS semantics | `SpanValidator` | per-`span.name` `mas.*` attributes, L1–L4 ([spanspec.md](spanspec.md)) |

`verify_otel_file` runs all three against a JSONL file and returns one report:

```python
from mas.library.telemetry import verify_otel_file

report = verify_otel_file(
    "otel_sdk_spans.jsonl",
    spanspec_level="L3",
    spanspec_strictness="required",
    spanspec_fail_on_error=False,   # spanspec gaps don't fail the run by default
)
print(report["ok"])                              # structural + schema pass?
print(report["spanspec"]["conformance_at_level"]) # 'full' | 'passing' | 'failing'
print(report["stats"])                           # spans, traces, agents, root_spans
```

Structural errors and JSON-schema errors always set `report["ok"] = False`. SpanSpec errors fail the run only when `spanspec_fail_on_error=True`, because spans are often partial (recommended attributes absent) without being wrong.

## Step + CLI

```python
from mas.library.telemetry.steps import run_verify_spans
report = run_verify_spans("otel_sdk_spans.jsonl", spanspec_level="L3", fail_on_error=True)
```

```bash
mas-lab telemetry verify otel_sdk_spans.jsonl --level L3 --fail   # prints the conformance summary
mas-lab telemetry verify otel_sdk_spans.jsonl --level L3 --spanspec my.spanspec.yaml
mas-lab telemetry inspect otel_sdk_spans.jsonl        # quick stats, no validation
```

## Structural checks in detail (`verify_otel_spans`)

| Check | Severity | Rule |
|-------|----------|------|
| every span has `name`, `start_time`, `end_time`, `context.span_id`, `context.trace_id` | error | `required_fields` |
| at least one root span (no `parent_id`) | error | `root_span_present` |
| at least one span carries `mas.boundary` | warning | `mas_boundary_present` |
| exactly one `trace_id` | warning | `single_trace` |

## SpanSpec reference validation (`SpanValidator`)

Beyond per-span attributes, `SpanValidator` also checks:

- **duplicate `mas.call.id`** within a trace → error;
- **missing / cross-trace parent** (`parent_id` resolves within the same trace)
  → error;
- **`mas.call.parent` vs parent `mas.call.id`** mismatch → warning;
- **consistency rules** from the spec (`group_unique`, `has_ancestor_named`).
