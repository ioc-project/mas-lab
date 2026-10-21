# KG comparison API

`mas.library.kg.core.compare` compares two knowledge graphs (KGs, JSON-LD documents of nodes and edges built from a multi-agent system run) and reports structural differences. Use it for regression tests, CI parity gates, and checking that a new MAS implementation behaves like an old one. For running it as a pipeline step, see [compare-kg.md](compare-kg.md) and [steps.md](steps.md#run_compare_kg).

```python
import json
from mas.library.kg import compare_kg

reference = json.load(open("golden.kg.jsonld"))
candidate = json.load(open("run_output.kg.jsonld"))

result = compare_kg(candidate, reference)
print(result.passed, result.summary)
# True {'total_checks': 7, 'passed': 7, 'failed': 0}
```

Top-level import: `from mas.library.kg import compare_kg, KGCompareResult`.

**Contents:** [`compare_kg`](#compare_kg) · [`KGCompareResult`](#kgcompareresult) · [Check functions](#individual-check-functions) · [Strict mode](#strict-mode) · [Volatile fields](#volatile-fields) · [Quick reference](#quick-reference)

## What "structural" means

`compare_kg` takes a *candidate* (the system under test) and a *reference* (the expected ground truth) and runs seven checks. They cover the agent set, tool set, node and edge type counts, containment depth, delegation pairs, and element-level identity (type, agent, tool, and a hash of the non-volatile fields).

The comparison ignores run-specific *volatile* fields: span, trace, session and call identifiers, timestamps, and durations. See [Volatile fields](#volatile-fields).

<a id="compare_kg"></a>

## `compare_kg`

```python
def compare_kg(
    candidate: dict,
    reference: dict,
    *,
    strict: bool = False,
) -> KGCompareResult:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `candidate` | `dict` | KG document loaded from a `.jsonld` file. Reads the `"nodes"` and `"edges"` keys (lists of dicts); missing or empty keys count as empty lists. |
| `reference` | `dict` | Ground-truth KG, same format. |
| `strict` | `bool` | When `True`, check 7 (`element_level_diff`) counts toward `passed`. Default `False`: only checks 1-6 decide `passed`. |

| # | Name | Tests |
|---|------|-------|
| 1 | `agent_coverage` | Candidate contains every agent ID present in reference |
| 2 | `node_distribution` | Candidate has >= reference count for each node type |
| 3 | `edge_distribution` | Candidate has >= reference count for each edge type |
| 4 | `call_depth` | Candidate's maximum containment depth >= reference |
| 5 | `tool_coverage` | Candidate contains every tool name present in reference |
| 6 | `delegation_topology` | Every agent-to-agent delegation pair in reference appears in candidate |
| 7 | `element_level_diff` | Node-by-node and edge-by-edge multiset diff (volatile fields excluded) |

### Example: CI assertion

```python
def test_kg_parity():
    reference = load_kg("tests/fixtures/golden.kg.jsonld")
    candidate = load_kg("artifacts/latest_run.kg.jsonld")

    result = compare_kg(candidate, reference)

    failed_checks = [c for c in result.checks if not c["passed"]]
    assert result.passed, (
        f"KG comparison failed {result.summary['failed']}/7 checks:\n"
        + "\n".join(f"  - {c['name']}: {c}" for c in failed_checks)
    )
```

Here `load_kg` is a helper that returns `json.load(open(path))`. Pass `strict=True` to also gate on element-level equality.

### Example: save a report

```python
result = compare_kg(candidate, reference, strict=True)
with open("compare_report.json", "w") as f:
    json.dump(result.to_dict(), f, indent=2)
```

## `KGCompareResult`

```python
@dataclass
class KGCompareResult:
    passed: bool
    checks: list[dict]
    summary: dict
    reference_stats: dict
    candidate_stats: dict

    def to_dict(self) -> dict: ...
```

| Field | Content |
|-------|---------|
| `passed` | `True` when all checks that count pass: checks 1-6 by default, all 7 when `strict=True`. |
| `checks` | One dict per check, in order 1-7. Every dict has `name` (str) and `passed` (bool); the other keys are check-specific. There is no generic `message` or `detail` key. |
| `summary` | `{"total_checks": 7, "passed": int, "failed": int}`. Counts all 7 checks regardless of `strict`, so `passed` can be `True` while `summary["failed"]` is 1. |
| `reference_stats` | `{"nodes": int, "edges": int, "agents": list[str]}`; `agents` is the sorted list of unique agent IDs. |
| `candidate_stats` | Same shape, for the candidate. |

`to_dict()` returns all five fields as a JSON-serializable dict.

Print only the failing checks:

```python
for check in result.checks:
    if not check["passed"]:
        print(f"FAILED: {check['name']}")
        for key, value in check.items():
            if key not in ("name", "passed"):
                print(f"  {key}: {value}")
```

Access one check by name:

```python
checks_by_name = {c["name"]: c for c in result.checks}
edge_check = checks_by_name["edge_distribution"]
if not edge_check["passed"]:
    print("Edge type deficits:", edge_check["deficits"])
```

## Individual check functions

All seven are importable from the module and take plain lists of dicts:

```python
from mas.library.kg.core.compare import (
    check_agent_coverage,
    check_node_distribution,
    check_edge_distribution,
    check_call_depth,
    check_tool_coverage,
    check_delegation_topology,
    check_element_diff,
)
```

Edge endpoints are read from `from_id`/`to_id` (the names KGs written by the library use), then `source`/`target`, then `from`/`to`. Node types are read from `node_type`, `nodeType` or `type`; edge types from `edge_type`, `edgeType` or `type`.

Signatures take `candidate_*` arguments before `reference_*` arguments, with nodes before edges:

```python
check_agent_coverage(candidate_nodes, reference_nodes)
check_node_distribution(candidate_nodes, reference_nodes)
check_edge_distribution(candidate_edges, reference_edges)
check_call_depth(candidate_nodes, candidate_edges, reference_nodes, reference_edges)
check_tool_coverage(candidate_nodes, reference_nodes)
check_delegation_topology(candidate_nodes, candidate_edges, reference_nodes, reference_edges)
check_element_diff(candidate_nodes, candidate_edges, reference_nodes, reference_edges)
```

Call one directly when you care about a single dimension:

```python
topo = check_delegation_topology(
    candidate["nodes"], candidate["edges"],
    reference["nodes"], reference["edges"],
)
print("Missing from candidate:", topo["missing"])
```

### Check 1: `check_agent_coverage`

Collects agent IDs (the `agentId` or `agent_id` field on any node) from both graphs. Passes when `missing` is empty. Extra candidate agents do not fail the check.

```python
{
    "name": "agent_coverage",
    "passed": False,
    "reference_agents": ["orchestrator", "planner", "executor"],
    "candidate_agents": ["orchestrator", "executor"],
    "missing": ["planner"],   # in reference, not in candidate
    "extra": []               # in candidate, not in reference (informational)
}
```

### Check 2: `check_node_distribution`

Counts nodes by type in both graphs. Passes when `deficits` is empty, that is, the candidate has at least as many nodes of each reference type. `deficits[type]` is `reference_count - candidate_count`.

```python
{
    "name": "node_distribution",
    "passed": False,
    "reference": {"AgentCall": 3, "ToolCall": 8, "LLMCall": 3},
    "candidate": {"AgentCall": 4, "ToolCall": 8, "LLMCall": 2},
    "deficits":  {"LLMCall": 1}
}
```

### Check 3: `check_edge_distribution`

Same as check 2 for edges.

```python
{
    "name": "edge_distribution",
    "passed": False,
    "reference": {"hasAgentCall": 2, "hasToolCall": 8, "hasLLMCall": 3},
    "candidate": {"hasAgentCall": 2, "hasToolCall": 7, "hasLLMCall": 3},
    "deficits":  {"hasToolCall": 1}
}
```

### Check 4: `check_call_depth`

Computes the maximum containment nesting depth by walking containment edges from the roots (nodes that no containment edge points to). Containment edges are `contains`, `hasCall`, and the ontology properties the builders emit: `hasAgentCall`, `hasLLMCall`, `hasToolCall`, `hasMASCall`, `hasProcessingCall`, `hasMemoryCall`, `hasSkillCall`, `hasRAGQuery`, `contributesTo`, `annotates` (`CONTAINMENT_EDGE_TYPES` in `mas.library.kg.core.ontology_align`). A shallow candidate suggests truncated or missing sub-calls. Passes when `candidate_depth >= reference_depth`.

```python
{"name": "call_depth", "passed": False, "reference_depth": 4, "candidate_depth": 2}
```

### Check 5: `check_tool_coverage`

Collects tool names (`toolName` or `tool_name`) from `ToolCall` nodes. Passes when `missing` is empty.

```python
{
    "name": "tool_coverage",
    "passed": False,
    "reference_tools": ["file_search", "python_interpreter", "web_search"],
    "candidate_tools": ["file_search", "web_search"],
    "missing": ["python_interpreter"]
}
```

### Check 6: `check_delegation_topology`

Extracts delegation pairs from edges of type `callsAgent` or `delegates_to`, then resolves the source and target node `id`s to agent IDs through the `agentId`/`agent_id` field. The native builder emits `callsAgent` from a delegating `ToolCall` to the target agent. Edges whose endpoints carry no agent ID are skipped, so a graph without `callsAgent` edges has an empty delegation set and the check passes trivially. Self-loops are excluded. Passes when `missing` is empty.

Pairs are formatted `"source_agent→target_agent"`.

```python
{
    "name": "delegation_topology",
    "passed": False,
    "reference": ["orchestrator→executor", "orchestrator→planner"],
    "candidate": ["orchestrator→executor"],
    "missing":   ["orchestrator→planner"]
}
```

### Check 7: `check_element_diff`

Identifies each node by the key `node_type|agentId|toolName|hash`, where `hash` is the first 16 hex characters of a SHA-256 over the node's non-volatile fields. Identifies each edge by `edge_type|source_id|target_id` using the raw endpoint IDs. Edge IDs are not stripped, so edges match only when node IDs match. Keys are compared as multisets, so two identical calls count twice.

Reference keys with no candidate match go to `missing_nodes`/`missing_edges`. Candidate keys with no reference match go to `spurious_nodes`/`spurious_edges`. The check passes when all four lists are empty.

```python
{
    "name": "element_level_diff",
    "passed": False,
    "missing_nodes":  ["LLMCall|orchestrator||3f9a1c0d5b7e2a64"],
    "spurious_nodes": [],
    "missing_edges":  [],
    "spurious_edges": ["hasToolCall|node-42|node-99"],
}
```

This check always runs and always appears in `result.checks`, but it decides `passed` only when `strict=True`.

## Strict mode

By default `passed` depends on checks 1-6 only.

- **Default mode:** use it for suites that tolerate extra context nodes, diagnostic annotations, or framework-specific auxiliary edges. The agent set, tool set, delegation, and depth are enforced; exact element identity is not.
- **Strict mode:** use it for golden-file tests of a deterministic pipeline, where the candidate must match the reference node for node and edge for edge, minus volatile fields.

```python
result = compare_kg(candidate, reference)                 # checks 1-6 decide
result = compare_kg(candidate, reference, strict=True)    # all 7 decide

# Inspect the element diff without it affecting result.passed
diff = next(c for c in result.checks if c["name"] == "element_level_diff")
if not diff["passed"]:
    print("Missing nodes:", diff["missing_nodes"])
    print("Spurious nodes:", diff["spurious_nodes"])
```

## Volatile fields

These node fields are removed before the content hash of check 7 is computed. Checks 1-6 compare no fields and are unaffected. Edge keys use only type and endpoint IDs, so this list does not affect them.

| Group | Fields (snake_case alias in parentheses where accepted) |
|-------|--------|
| Run-specific identifiers | `id`, `callId` (`call_id`), `parentCallId` (`parent_call_id`), `spanId` (`span_id`), `traceId` (`trace_id`), `sourceRecordIds`, `runId` (`run_id`), `sessionId` (`session_id`), `executionId`, `sourceCallId`, `stateNodeId`, `fromState`, `toState`, `annotationId`, `transitionId`, `realizesCallId`, `contentHash` |
| Timestamps and durations | `timestamp`, `startTime` (`start_time`), `endTime` (`end_time`), `durationMs` (`duration_ms`), `transitionTimestamp`, `transitionDuration` |
| Token counts and segments | `segments` (raw LLM message segments, variable per run), `totalTokens` (may vary with model version) |

Fields such as `canonicalId`, `name` and `description` of generated nodes often embed run or call IDs but are not in the list. Because they are hashed, they can make `element_level_diff` fail between equivalent runs. This is why the check is informational by default.

Any field not listed is treated as stable. If your graph has other run-specific fields, strip them before calling `compare_kg`, or keep `strict=False`.

## Quick reference

```python
from mas.library.kg import compare_kg, KGCompareResult
result: KGCompareResult = compare_kg(candidate_kg, reference_kg)           # default
result = compare_kg(candidate_kg, reference_kg, strict=True)               # strict

result.passed            # bool
result.checks            # list of 7 dicts
result.summary           # {"total_checks": 7, "passed": N, "failed": N}
result.reference_stats   # {"nodes": N, "edges": N, "agents": [...]}
result.candidate_stats   # same shape
result.to_dict()         # serializable dict of all of the above
```
