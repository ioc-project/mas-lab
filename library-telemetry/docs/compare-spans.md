# Span parity comparison

Span parity checks that two OTel span sets mean the same thing. Use it to confirm that replaying `events.jsonl` produces the same spans as a live plugin export, or as a second replay. It mirrors `compare_kg` in `library-kg`.

`compare_otel_span_sets` compares semantics, not identifiers. It ignores trace ids, span ids and timestamps, which differ between replay and live export, and it ignores span order.

## Checks

| Check | Compares |
|-------|----------|
| `span_count` | total number of spans |
| `span_name_counts` | count of spans per `name` |
| `mas_attr_keys_by_name` | set of `mas.*` attribute keys per span name |
| `topology` | parent→child edges keyed by span **name** (multiset) |
| `max_depth` | maximum tree depth |
| `mas_attr_values` | multiset of `(name, mas.* values)` signatures (excl. `mas.call.id`) |

## Usage

```python
from mas.library.telemetry import OtelSpanSet

ref = OtelSpanSet.from_file("otel_sdk_spans.jsonl")        # live plugin export
cand = OtelSpanSet.from_events("events.jsonl", app_name="x")  # replay

report = cand.compare_to(ref, strict=True)
print(report["passed"], report["summary"])   # {'passed': 6, 'failed': 0, 'total_checks': 6}
```

Step functions and CLI:

```python
from mas.library.telemetry.steps import run_compare_spans, run_compare_spans_multi
run_compare_spans("reference.jsonl", "candidate.jsonl", strict=True, output_path="parity.json")
run_compare_spans_multi("reference.jsonl", [("otel-plugin", "a.jsonl"), ("observe-sdk", "b.jsonl")])
```

```bash
mas-lab telemetry compare reference.jsonl candidate.jsonl   # prints the summary; exit code 1 if a check fails
mas-lab telemetry compare reference.jsonl candidate.jsonl --no-strict
```

## Typical workflow

```
events.jsonl ──replay──▶ otel_sdk_spans_replay.jsonl ─┐
                                                       ├─▶ compare ─▶ parity_report.json
live plugin ───────────▶ otel_sdk_spans.jsonl ────────┘
```

See the [`otel-span-parity`](../pipelines/otel-span-parity.yaml) pipeline, which
replays a reference and compares it against both the OTel and observe plugin
live exports.
