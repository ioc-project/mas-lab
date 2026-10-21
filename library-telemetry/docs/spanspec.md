# SpanSpec — the OTel span contract (L1–L4)

`mas.spanspec.yaml` declares which attributes each OTel span must carry. `mas.library.telemetry.verification.SpanValidator` evaluates it. It plays the role for spans that SHACL (Shapes Constraint Language) plays for an RDF graph: a set of declarative shape constraints. Read this page to interpret a conformance report.

```python
from mas.library.telemetry import SpanValidator
print(SpanValidator().validate_file("otel_sdk_spans.jsonl").conformance("L3"))
```

## Conformance model

- A span is **passing** at level *L* ⇔ all `required` attributes for its
  `span.name` (plus the global attributes) are present at *L*.
- A span is **full** at *L* ⇔ `required` + `recommended` are present.
- A trace/session is passing/full when every span in it is.

`report.conformance(level)` returns one of:

| Value | Meaning |
|-------|---------|
| `failing` | at least one **error** at this level (a required attribute is absent) |
| `passing` | no errors, but at least one **warning** (a recommended attribute is absent) |
| `full` | no errors and no warnings |

## Levels

| Level | Name | What it guarantees |
|-------|------|--------------------|
| L1 | structural | OTel GenAI / envelope structural conventions |
| L2 | gen_ai_semconv | observe-sdk / GenAI attribute contract required for OXP ingestion |
| L3 | mas_native_roundtrip | MAS Framework observability — the full MAS span taxonomy |
| L4 | full | All recommended fields present — enough for complete KG construction |

## Shapes

The spec has five sections:

- `global_attributes` — attributes required/recommended on **every** span
  (e.g. `application_id`, `session.id` at L2; `mas.call.id` recommended at L3).
- `shapes` — per-`span.name` constraints (e.g. `AgentCall`, `LLMCall`, `ToolCall`).
- `suffix_shapes` — matched by span-name **suffix** (the `ioa_observe` convention
  `%.agent`, `%.chat`, `%.tool`); the longest matching suffix wins.
- `attribute_types` — type/enum/min-length constraints for individual attributes.
- `consistency_rules` — cross-span rules (`group_unique`, `has_ancestor_named`).

## Strictness

`validate(spans, strictness=...)` controls how recommended/optional gaps are
scored:

| Strictness | recommended absent | optional absent |
|------------|--------------------|-----------------|
| `required` (default) | warning | ignored |
| `recommended` | **error** | ignored |
| `complete` | **error** | **error** |

## Usage

```python
from mas.library.telemetry import SpanValidator

report = SpanValidator().validate_file("otel_sdk_spans.jsonl", strictness="required")
print(report.conformance("L2"))       # 'full' | 'passing' | 'failing'
print(len(report.errors()), "errors")
for v in report.errors("L3"):
    print(v.span_name, v.attribute, v.message)

# machine-readable
import json; print(json.dumps(report.summary(), indent=2))
```

Custom spec:

```python
SpanValidator("my.spanspec.yaml").validate(spans)
```

The built-in spec ships inside the wheel at
`mas/library/telemetry/schemas/mas.spanspec.yaml`. Source of truth for both the
spec and the JSON-schema envelope is [`../schemas/`](../schemas/README.md).
