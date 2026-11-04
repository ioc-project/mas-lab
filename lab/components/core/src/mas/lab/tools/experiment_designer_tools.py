#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Experiment Designer Tools — give an agent full access to MAS Lab resources.

These tools communicate with the mas-lab HTTP API so the agent can:
- List, read and write scenarios, overlays, experiments
- Trigger experiment runs and read results
- Suggest and apply configuration changes

Usage in agent manifest:
  tools:
    - ref: mas-lab/agents/tools/experiment_designer_tools.py
      class_name: ListScenariosTool
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

# ── Base helper ───────────────────────────────────────────────────────────────

_DEFAULT_BASE = "http://localhost:8888/api"


def _api(method: str, path: str, body: dict | None = None, base: str = _DEFAULT_BASE) -> dict:
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}
    except Exception as e:
        return {"error": str(e)}


# ── Shared param: lab_slug + api_base ─────────────────────────────────────────

_SHARED_PARAMS = {
    "lab_slug": {
        "type": "string",
        "description": "Lab slug (e.g. 'cognitive-challenges'). If omitted, uses current context.",
    },
    "api_base": {
        "type": "string",
        "description": "API base URL (default: http://localhost:8888/api).",
    },
}


# ── Tools ─────────────────────────────────────────────────────────────────────

try:
    from mas.runtime.contracts import ToolContract
except ImportError:
    class ToolContract:  # type: ignore[no-redef]
        def get_name(self) -> str: ...
        def get_description(self) -> str: ...
        def get_parameters_schema(self) -> dict: ...
        def execute(self, **kwargs) -> dict: ...


class ListScenariosTool(ToolContract):
    """List all scenarios defined in a lab."""

    def get_name(self) -> str:
        return "list_scenarios"

    def get_description(self) -> str:
        return "List all scenarios defined in the lab, including their id, description, challenge_type, difficulty, tags, and overlay."  # noqa: E501

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": _SHARED_PARAMS, "required": []}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug = kwargs.get("lab_slug", "")
        base = kwargs.get("api_base", _DEFAULT_BASE)
        if not slug:
            return {"error": "lab_slug is required"}
        r = _api("GET", f"/labs/{slug}/scenarios", base=base)
        scenarios = r.get("scenarios", [])
        summary = [
            {
                "id": s["id"],
                "description": (s.get("description") or "")[:120],
                "challenge_type": s.get("challenge_type", ""),
                "difficulty": s.get("difficulty", ""),
                "tags": s.get("tags", []),
            }
            for s in scenarios
        ]
        return {"scenarios": summary, "count": len(summary)}


class GetScenarioTool(ToolContract):
    """Get full details of a single scenario."""

    def get_name(self) -> str:
        return "get_scenario"

    def get_description(self) -> str:
        return "Get the full definition of a specific scenario including question, expected_answer, and expected metrics."  # noqa: E501

    def get_parameters_schema(self) -> Dict[str, Any]:
        props = {**_SHARED_PARAMS, "scenario_id": {"type": "string", "description": "Scenario id."}}
        return {"type": "object", "properties": props, "required": ["scenario_id"]}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug = kwargs.get("lab_slug", "")
        base = kwargs.get("api_base", _DEFAULT_BASE)
        scen_id = kwargs.get("scenario_id", "")
        if not slug or not scen_id:
            return {"error": "lab_slug and scenario_id are required"}
        r = _api("GET", f"/labs/{slug}/scenarios/{scen_id}", base=base)
        return r


class CreateOrUpdateScenarioTool(ToolContract):
    """Create or update a scenario definition."""

    def get_name(self) -> str:
        return "create_or_update_scenario"

    def get_description(self) -> str:
        return (
            "Create a new scenario or update an existing one. "
            "Provide id, description, challenge_type, difficulty, question, "
            "expected_answer, expected_metrics (dict), overlay, tags (list)."
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        props = {
            **_SHARED_PARAMS,
            "id":               {"type": "string",  "description": "Scenario id (slug)."},
            "description":      {"type": "string",  "description": "Short description."},
            "challenge_type":   {"type": "string",  "description": "Challenge category (e.g. shared-intent, ontology)."},  # noqa: E501
            "difficulty":       {"type": "string",  "description": "low | medium | high | critical"},
            "question":         {"type": "string",  "description": "Prompt / input message to the MAS."},
            "expected_answer":  {"type": "string",  "description": "Expected output or behaviour."},
            "expected_metrics": {"type": "object",  "description": "Dict of metric_name: expected_value."},
            "overlay":          {"type": "string",  "description": "Overlay id to activate for this scenario."},
            "tags":             {"type": "array",   "items": {"type": "string"}, "description": "Tag list."},
            "notes":            {"type": "string",  "description": "Research rationale."},
        }
        return {"type": "object", "properties": props, "required": ["id"]}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug = kwargs.get("lab_slug", "")
        base = kwargs.get("api_base", _DEFAULT_BASE)
        if not slug:
            return {"error": "lab_slug is required"}
        payload = {k: v for k, v in kwargs.items() if k not in ("lab_slug", "api_base") and v is not None}
        return _api("POST", f"/labs/{slug}/scenarios", body=payload, base=base)


class ListOverlaysTool(ToolContract):
    """List all overlays (agent config variants) in a lab."""

    def get_name(self) -> str:
        return "list_overlays"

    def get_description(self) -> str:
        return "List all overlays available in the lab. Each overlay patches agent configs to inject or prevent faults."

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": _SHARED_PARAMS, "required": []}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug = kwargs.get("lab_slug", "")
        base = kwargs.get("api_base", _DEFAULT_BASE)
        if not slug:
            return {"error": "lab_slug is required"}
        r = _api("GET", f"/labs/{slug}/overlays", base=base)
        overlays = r.get("overlays", [])
        summary = [{"id": o["id"], "description": (o.get("description") or "")[:100]} for o in overlays]
        return {"overlays": summary, "count": len(summary)}


class ListExperimentsTool(ToolContract):
    """List all experiments in a lab."""

    def get_name(self) -> str:
        return "list_experiments"

    def get_description(self) -> str:
        return "List all experiments defined in the lab."

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": _SHARED_PARAMS, "required": []}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug = kwargs.get("lab_slug", "")
        base = kwargs.get("api_base", _DEFAULT_BASE)
        if not slug:
            return {"error": "lab_slug is required"}
        r = _api("GET", f"/labs/{slug}/experiments", base=base)
        exps = r.get("experiments", [])
        summary = [
            {
                "id": e.get("exp_id") or e.get("slug", "?"),
                "description": (e.get("description") or "")[:100],
                "scenarios": e.get("scenario_ids", []),
            }
            for e in exps
        ]
        return {"experiments": summary, "count": len(summary)}


class GetExperimentResultsTool(ToolContract):
    """Get results for an experiment run."""

    def get_name(self) -> str:
        return "get_experiment_results"

    def get_description(self) -> str:
        return "Get the list of result runs for an experiment, including summary metrics."

    def get_parameters_schema(self) -> Dict[str, Any]:
        props = {
            **_SHARED_PARAMS,
            "exp_id": {"type": "string", "description": "Experiment id."},
        }
        return {"type": "object", "properties": props, "required": ["exp_id"]}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug   = kwargs.get("lab_slug", "")
        base   = kwargs.get("api_base", _DEFAULT_BASE)
        exp_id = kwargs.get("exp_id", "")
        if not slug or not exp_id:
            return {"error": "lab_slug and exp_id are required"}
        r = _api("GET", f"/labs/{slug}/experiments/{exp_id}/results", base=base)
        runs = r.get("runs", [])
        # Return compact summaries
        compact = []
        for run in runs[:10]:
            s = run.get("summary") or {}
            compact.append({
                "name": run.get("name", ""),
                "type": run.get("type", ""),
                "scenarios": s.get("scenarios", {}),
                "metrics": {k: v for k, v in list(s.items())[:12] if not isinstance(v, dict)},
            })
        return {"runs": compact, "count": len(runs), "path": r.get("path")}


class RunExperimentTool(ToolContract):
    """Trigger an experiment run."""

    def get_name(self) -> str:
        return "run_experiment"

    def get_description(self) -> str:
        return "Launch an experiment and return a job_id. Use smoke=True for a quick single-run validation before a full benchmark."  # noqa: E501

    def get_parameters_schema(self) -> Dict[str, Any]:
        props = {
            **_SHARED_PARAMS,
            "exp_id": {"type": "string",  "description": "Experiment id."},
            "smoke":  {"type": "boolean", "description": "If true, run a quick smoke test (1 run)."},
            "force":  {"type": "boolean", "description": "Force re-run even if results exist."},
        }
        return {"type": "object", "properties": props, "required": ["exp_id"]}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug   = kwargs.get("lab_slug", "")
        base   = kwargs.get("api_base", _DEFAULT_BASE)
        exp_id = kwargs.get("exp_id", "")
        if not slug or not exp_id:
            return {"error": "lab_slug and exp_id are required"}
        payload = {
            "smoke": bool(kwargs.get("smoke", False)),
            "force": bool(kwargs.get("force", False)),
        }
        r = _api("POST", f"/labs/{slug}/experiments/{exp_id}/run", body=payload, base=base)
        return r


class GetExperimentTool(ToolContract):
    """Get the full config of an experiment."""

    def get_name(self) -> str:
        return "get_experiment"

    def get_description(self) -> str:
        return "Get the full configuration of a specific experiment (scenarios, overlays, settings)."

    def get_parameters_schema(self) -> Dict[str, Any]:
        props = {**_SHARED_PARAMS, "exp_id": {"type": "string", "description": "Experiment id."}}
        return {"type": "object", "properties": props, "required": ["exp_id"]}

    def execute(self, **kwargs) -> Dict[str, Any]:
        slug   = kwargs.get("lab_slug", "")
        base   = kwargs.get("api_base", _DEFAULT_BASE)
        exp_id = kwargs.get("exp_id", "")
        if not slug or not exp_id:
            return {"error": "lab_slug and exp_id are required"}
        return _api("GET", f"/labs/{slug}/experiments/{exp_id}", base=base)
