"""Tests for native events.jsonl validation."""

from __future__ import annotations

import json

import pytest

from mas.library.kg.observability.native.validate import (
    EventValidator,
    validate_event_json_schema,
)
from mas.library.kg.observability.native.verify import verify_events_file


def _base_event(**extra):
    ev = {
        "kind": "execution_start",
        "timestamp": 1.0,
        "run_id": "r1",
        "agent_id": "a1",
        "call_id": "c1",
        "input": "hello",
    }
    ev.update(extra)
    return ev


def test_json_schema_rejects_missing_run_id():
    ev = _base_event()
    del ev["run_id"]
    errs = validate_event_json_schema(ev)
    assert any("run_id" in e for e in errs)


def test_json_schema_requires_call_id_on_interval():
    ev = _base_event(kind="llm_call_start", model="gpt")
    del ev["call_id"]
    errs = validate_event_json_schema(ev)
    assert errs


def test_event_validator_requires_llm_model():
    ev = _base_event(kind="llm_call_start", call_id="c2")
    del ev["input"]
    report = EventValidator().validate([ev])
    assert report.errors("L3")


def test_verify_events_file_minimal(tmp_path):
    path = tmp_path / "events.jsonl"
    lines = [
        _base_event(),
        _base_event(kind="execution_end", call_id="c1", status="success", output="ok"),
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    report = verify_events_file(path, spec_fail_on_error=False)
    assert report["stats"]["total_events"] == 2
    assert report["json_schema"]["ok"] is True
    assert report["json_schema"]["skipped"] is False


def test_json_schema_check_skipped_is_distinct_from_pass(monkeypatch):
    """jsonschema missing must report skipped=True, ok=None -- not the
    ok=True a plain `except ImportError: return []` used to produce,
    which is indistinguishable from a real clean validation run."""
    import builtins

    from mas.library.kg.observability.native import validate as validate_mod

    real_import = builtins.__import__

    def _no_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("simulated: jsonschema not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_jsonschema)

    with pytest.raises(validate_mod.JsonSchemaUnavailable):
        validate_mod.validate_event_json_schema(_base_event())

    report = validate_mod.validate_events_json_schema([_base_event()])
    assert report["ok"] is None
    assert report["skipped"] is True


def test_verify_events_file_surfaces_schema_skip_as_warning(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("simulated: jsonschema not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_jsonschema)

    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(_base_event()) + "\n", encoding="utf-8")
    report = verify_events_file(path, spec_fail_on_error=False)

    assert report["json_schema"]["skipped"] is True
    assert any("json_schema_check_skipped" in w for w in report["warnings"])
    # A skip must not silently flip overall "ok" to True either way on its
    # own -- it's neither a pass nor a failure of this specific check.
    assert not any("json_schema_check_skipped" in e for e in report["errors"])
