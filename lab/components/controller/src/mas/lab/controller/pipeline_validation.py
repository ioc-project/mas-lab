#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pipeline YAML validation helpers."""

from __future__ import annotations


def validate_pipeline_yaml(content: str) -> dict:
    """Validate pipeline YAML content against the JSON schema and check DAG integrity.

    Returns {"valid": True} or {"valid": False, "errors": [...]}.
    """
    import json

    import jsonschema
    import yaml as _yaml

    errors: list[str] = []

    try:
        doc = _yaml.safe_load(content)
    except _yaml.YAMLError as exc:
        return {"valid": False, "errors": [f"YAML parse error: {exc}"]}

    if not isinstance(doc, dict):
        return {"valid": False, "errors": ["Document must be a YAML mapping"]}

    # JSON Schema validation (bench package: pipeline-manifest.schema.json)
    try:
        from mas.lab.controller.schema_registry import read_schema_text

        _, schema_text = read_schema_text("pipeline")
        schema = json.loads(schema_text)
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as exc:
        errors.append(f"Schema: {exc.message} (at {'/'.join(str(p) for p in exc.absolute_path)})")
    except Exception as exc:
        errors.append(f"Schema validation error: {exc}")

    # DAG integrity checks
    steps = (doc.get("spec") or {}).get("steps") or []
    step_names = {s["name"] for s in steps if "name" in s}
    seen: set[str] = set()
    for s in steps:
        name = s.get("name", "<unnamed>")
        if name in seen:
            errors.append(f"Duplicate step name: '{name}'")
        seen.add(name)
        for dep in s.get("depends_on", []):
            if dep not in step_names:
                errors.append(f"Step '{name}' depends on unknown step '{dep}'")
            if dep == name:
                errors.append(f"Step '{name}' depends on itself")

    # Cycle detection (topological sort)
    if not errors:
        adj: dict[str, list[str]] = {s["name"]: s.get("depends_on", []) for s in steps}
        visited: set[str] = set()
        in_stack: set[str] = set()

        def _has_cycle(node: str) -> bool:
            if node in in_stack:
                return True
            if node in visited:
                return False
            visited.add(node)
            in_stack.add(node)
            for dep in adj.get(node, []):
                if _has_cycle(dep):
                    return True
            in_stack.discard(node)
            return False

        for step_name in adj:
            if _has_cycle(step_name):
                errors.append("Dependency cycle detected in pipeline steps")
                break

    return {"valid": len(errors) == 0, "errors": errors}
