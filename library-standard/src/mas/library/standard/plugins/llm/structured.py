#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Opt-in schema-conforming LLM return: map a schema onto a protocol request, validate the answer."""

from __future__ import annotations

import json
from typing import Any

from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass


def schema_from_format(response_format: Any) -> dict[str, Any] | None:
    """JSON Schema from Chat Completions ``response_format``, or a bare schema object."""
    if not isinstance(response_format, dict) or not response_format:
        return None
    nested = response_format.get("json_schema")
    if isinstance(nested, dict):
        schema = nested.get("schema")
        return dict(schema) if isinstance(schema, dict) else dict(nested) if _looks_like_schema(nested) else None
    schema = response_format.get("schema")
    if isinstance(schema, dict):
        return dict(schema)
    if response_format.get("type") == "json_object":
        return {"type": "object"}
    if _looks_like_schema(response_format):
        return dict(response_format)
    return None


def _looks_like_schema(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("type", "properties", "$ref", "items", "anyOf", "oneOf", "allOf"))


def json_schema_format(schema: dict[str, Any], *, name: str = "result") -> dict[str, Any]:
    """OpenAI-shaped ``response_format`` the protocol layers map onward."""
    return {"type": "json_schema", "json_schema": {"name": name, "schema": dict(schema), "strict": True}}


def _json_text(content: Any) -> str:
    from mas.runtime.contracts.content_types import text_of

    text = (content if isinstance(content, str) else text_of(content)).strip()
    if not text.startswith("```"):
        return text
    rest = text[3:]
    rest = rest.split("\n", 1)[-1].strip() if "\n" in rest else ""
    if rest.endswith("```"):
        rest = rest[:-3].strip()
    return rest


def _looks_like_content_parts(content: list[Any]) -> bool:
    """True when *content* is a message part list wrapping JSON, not a JSON array value."""
    if not content or not all(isinstance(part, dict) for part in content):
        return False
    kinds = [str(part.get("type") or "text") for part in content]
    if not all(kind in {"text", "image", "file", "audio", "video"} for kind in kinds):
        return False
    if any(kind != "text" for kind in kinds):
        return True
    joined = "".join(str(part.get("text") or "") for part in content).strip()
    return joined.startswith("{") or joined.startswith("[") or joined.startswith("```")


def parse_json_content(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if isinstance(content, list) and not _looks_like_content_parts(content):
        return content
    text = _json_text(content)
    if not text:
        raise ClassifiedFailure(
            "structured return: model returned no JSON",
            failure_class=FailureClass.APPLICATION,
            code="SCHEMA",
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClassifiedFailure(
            f"structured return: model output is not JSON ({exc})",
            failure_class=FailureClass.APPLICATION,
            code="SCHEMA",
        ) from exc


def validate_schema(instance: Any, schema: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError, ValidationError

    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(instance)
    except SchemaError as exc:
        raise ClassifiedFailure(
            f"structured return: invalid schema ({exc.message})",
            failure_class=FailureClass.APPLICATION,
            code="SCHEMA",
        ) from exc
    except ValidationError as exc:
        raise ClassifiedFailure(
            f"structured return: output does not match schema ({exc.message})",
            failure_class=FailureClass.APPLICATION,
            code="SCHEMA",
        ) from exc


def apply_structured_result(message: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """Parse ``content`` as JSON, validate, and set ``parsed``. No-op without a schema."""
    if not schema:
        return message
    if message.get("tool_calls") or str(message.get("finish_reason") or "") == "tool_calls":
        return message
    parsed = parse_json_content(message.get("content"))
    validate_schema(parsed, schema)
    out = dict(message)
    out["parsed"] = parsed
    if not isinstance(out.get("content"), str):
        out["content"] = json.dumps(parsed)
    return out
