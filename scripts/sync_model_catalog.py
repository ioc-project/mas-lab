#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Refresh curated catalog facts from a models.dev api.json snapshot.

Maintainer tool: not imported by the runtime, not run in CI. Default writes
the catalog; ``--check`` prints a diff and exits non-zero when it would change.
Hand-written fields (aliases, apis, defaults, settings, thinking_mode,
max_thinking_tokens) are left untouched.

models.dev is MIT (Copyright (c) 2025 models.dev). The required notice is in
NOTICE at the repository root. The runtime does not fetch this URL.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import dataclass, field
from difflib import unified_diff
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO_ROOT / "runtime" / "src" / "mas" / "runtime" / "spec" / "llm-model-catalog.yaml"
DEFAULT_URL = "https://models.dev/api.json"
ALLOWED_MODALITIES = ("text", "image", "audio", "video", "pdf")
FIRST_PARTY = ("openai", "anthropic", "google", "google-vertex")
GEMMA_DEPLOYMENT = "gemma-4-26b-a4b-it-node1-a100-0"
GEMMA_COMMENT = "Image support depends on the served build and is enabled only after it is tested there."
MODEL_KEY_RE = re.compile(r"^    ([A-Za-z0-9._:-]+):\s*$")
FIELD_KEY_RE = re.compile(r"^      ([A-Za-z0-9_.]+):(?:\s|$)")
PRICING_KEY_RE = re.compile(r"^        ([A-Za-z0-9_]+):(?:\s|$)")
COST_TO_PRICING = (
    ("input", "input_per_million_tokens"),
    ("output", "output_per_million_tokens"),
    ("cache_read", "cached_input_per_million_tokens"),
)


@dataclass
class SyncResult:
    exit_code: int = 0
    updated: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    field_diffs: list[str] = field(default_factory=list)
    diff_text: str = ""


def load_api(*, from_file: Path | str | None = None, url: str = DEFAULT_URL) -> dict[str, Any]:
    if from_file is not None:
        return json.loads(Path(from_file).read_text(encoding="utf-8"))
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def flatten_records(api: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for provider, payload in api.items():
        if not isinstance(payload, dict):
            continue
        models = payload.get("models") or {}
        if not isinstance(models, dict):
            continue
        for model_id, raw in models.items():
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            record["_provider"] = str(provider)
            record["_key"] = str(model_id)
            records.append(record)
    return records


def _idents(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("id", "_key", "canonical_model_id"):
        value = record.get(key)
        if value:
            text = str(value)
            values.append(text)
            values.append(text.split("/")[-1])
    values.append(f"{record['_provider']}/{record['_key']}")
    return values


def _canon_id(record: dict[str, Any]) -> str:
    return str(record.get("canonical_model_id") or f"{record['_provider']}/{record['_key']}").lower()


def _limit_pair(record: dict[str, Any]) -> tuple[int, int]:
    limit = record.get("limit") if isinstance(record.get("limit"), dict) else {}
    context = limit.get("context") if isinstance(limit.get("context"), int) else 0
    output = limit.get("output") if isinstance(limit.get("output"), int) else 0
    return context, output


def _cost_quality(record: dict[str, Any]) -> int:
    """Prefer list prices (fewer decimals) over reseller quotes."""
    cost = record.get("cost") if isinstance(record.get("cost"), dict) else {}
    value = cost.get("input")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return -100
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return 3
    text = f"{float(value):.8f}".rstrip("0")
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return max(0, 3 - decimals)


def _rank(record: dict[str, Any], terms_l: set[str]) -> tuple[int, int, int, int, int, int]:
    canon = _canon_id(record)
    key = str(record.get("_key") or "").lower()
    first_party = 1 if record["_provider"] in FIRST_PARTY else 0
    org_match = 1 if canon.split("/")[0] == record["_provider"] else 0
    term_hit = 1 if canon in terms_l or canon.split("/")[-1] in terms_l or key in terms_l else 0
    context, output = _limit_pair(record)
    return (first_party, org_match, term_hit, context, output, _cost_quality(record))


def _exact_hits(records: list[dict[str, Any]], term: str) -> list[dict[str, Any]]:
    term_l = term.lower()
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        names = {item.lower() for item in _idents(record)}
        if term_l not in names:
            continue
        marker = (record["_provider"], record["_key"])
        if marker in seen:
            continue
        seen.add(marker)
        hits.append(record)
    return hits


def _prefix_hits(records: list[dict[str, Any]], term: str) -> list[dict[str, Any]]:
    """First-party ids that continue the search term with a hyphen."""
    if not term:
        return []
    prefix = term.lower() + "-"
    hits: list[dict[str, Any]] = []
    for record in records:
        if record["_provider"] not in FIRST_PARTY:
            continue
        key = str(record.get("_key") or "").lower()
        if key.startswith(prefix):
            hits.append(record)
    return hits


def _deployment_hits(records: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """First-party ids that are a hyphen-prefix of a deployment catalog name."""
    if not name:
        return []
    name_l = name.lower()
    hits: list[dict[str, Any]] = []
    for record in records:
        if record["_provider"] not in FIRST_PARTY:
            continue
        key = str(record.get("_key") or "").lower()
        if key and name_l.startswith(key + "-"):
            hits.append(record)
    return hits


def match_record(
    name: str,
    aliases: list[str],
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    terms: list[str] = []
    for candidate in (name, *aliases):
        if candidate and candidate not in terms:
            terms.append(candidate)
        last = candidate.split("/")[-1]
        if last and last not in terms:
            terms.append(last)
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _absorb(candidates: list[dict[str, Any]]) -> None:
        for record in candidates:
            marker = (record["_provider"], record["_key"])
            if marker in seen:
                continue
            seen.add(marker)
            hits.append(record)

    for term in terms:
        _absorb(_exact_hits(records, term))
    _absorb(_deployment_hits(records, name))
    if not hits:
        for term in terms:
            _absorb(_prefix_hits(records, term))
    if not hits:
        return None
    terms_l = {term.lower() for term in terms}
    return max(hits, key=lambda record: _rank(record, terms_l))


def _norm_modalities(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    present = {str(item) for item in raw}
    return [name for name in ALLOWED_MODALITIES if name in present]


def _union_modalities(existing: Any, incoming: Any) -> list[str]:
    present = set(_norm_modalities(existing)) | set(_norm_modalities(incoming))
    return [name for name in ALLOWED_MODALITIES if name in present]


def facts_from_record(record: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    limit = record.get("limit") if isinstance(record.get("limit"), dict) else {}
    context = limit.get("context")
    if isinstance(context, int) and context >= 1:
        facts["context_window"] = context
    output = limit.get("output")
    if isinstance(output, int) and output >= 1:
        facts["max_output_tokens"] = output
    modalities = record.get("modalities") if isinstance(record.get("modalities"), dict) else {}
    facts["modalities"] = {
        "input": _norm_modalities(modalities.get("input")),
        "output": _norm_modalities(modalities.get("output")),
    }
    cost = record.get("cost") if isinstance(record.get("cost"), dict) else {}
    pricing: dict[str, Any] = {}
    for src, dest in COST_TO_PRICING:
        value = cost.get(src)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            pricing[dest] = value
    if pricing:
        facts["pricing"] = pricing
    return facts


def _format_number(value: int | float) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def _format_flow_list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _field_span(body: list[str], key: str, indent: str = "      ") -> tuple[int, int] | None:
    prefix = f"{indent}{key}:"
    nested = indent + " "
    for index, line in enumerate(body):
        if not (line.startswith(prefix) and (len(line) == len(prefix) or line[len(prefix)] in " \r\n")):
            continue
        end = index + 1
        while end < len(body):
            nxt = body[end]
            if nxt.strip() == "" or nxt.lstrip().startswith("#"):
                if nxt.startswith(nested) or nxt.strip() == "":
                    end += 1
                    continue
                break
            if nxt.startswith(nested):
                end += 1
                continue
            break
        return index, end
    return None


def _insert_after(body: list[str], block: list[str], anchors: tuple[str, ...]) -> None:
    insert_at = 0
    for anchor in anchors:
        span = _field_span(body, anchor)
        if span is not None:
            insert_at = span[1]
    body[insert_at:insert_at] = block


def _set_scalar(
    body: list[str], key: str, value: int | float, anchors: tuple[str, ...], diffs: list[str], name: str
) -> None:
    rendered = f"      {key}: {_format_number(value)}\n"
    span = _field_span(body, key)
    if span is None:
        _insert_after(body, [rendered], anchors)
        diffs.append(f"{name}.{key}: <missing> -> {value}")
        return
    start, end = span
    current = yaml.safe_load("".join(body[start:end])) or {}
    old = current.get(key) if isinstance(current, dict) else current
    if _values_equal(old, value):
        return
    body[start:end] = [rendered]
    diffs.append(f"{name}.{key}: {old} -> {value}")


def _modalities_block(modalities: dict[str, list[str]], comment: str | None = None) -> list[str]:
    lines: list[str] = []
    if comment:
        lines.append(f"      # {comment}\n")
    lines.append("      modalities:\n")
    lines.append(f"        input: {_format_flow_list(modalities.get('input') or [])}\n")
    lines.append(f"        output: {_format_flow_list(modalities.get('output') or [])}\n")
    return lines


def _set_modalities(
    body: list[str],
    modalities: dict[str, list[str]],
    diffs: list[str],
    name: str,
    comment: str | None = None,
) -> None:
    span = _field_span(body, "modalities")
    desired_in = modalities.get("input") or []
    desired_out = modalities.get("output") or []
    if span is not None:
        start, end = span
        current = yaml.safe_load("".join(body[start:end])) or {}
        mapping = current.get("modalities") if isinstance(current, dict) else current
        old_in = list(mapping.get("input") or []) if isinstance(mapping, dict) else []
        old_out = list(mapping.get("output") or []) if isinstance(mapping, dict) else []
        if old_in == desired_in and old_out == desired_out:
            if comment and comment not in "".join(body):
                body[start:start] = [f"      # {comment}\n"]
            return
        comment_line: list[str] = []
        if start > 0 and body[start - 1].lstrip().startswith("#") and comment and comment in body[start - 1]:
            start -= 1
        elif comment:
            comment_line = [f"      # {comment}\n"]
        body[start:end] = comment_line + _modalities_block(modalities)
        diffs.append(f"{name}.modalities.input: {old_in} -> {desired_in}")
        diffs.append(f"{name}.modalities.output: {old_out} -> {desired_out}")
        return
    _insert_after(
        body,
        _modalities_block(modalities, comment=comment),
        ("aliases", "context_window", "max_output_tokens"),
    )
    diffs.append(f"{name}.modalities.input: <missing> -> {desired_in}")
    diffs.append(f"{name}.modalities.output: <missing> -> {desired_out}")


def _existing_pricing_keys(block: list[str]) -> list[str]:
    keys: list[str] = []
    for line in block:
        match = PRICING_KEY_RE.match(line)
        if match:
            keys.append(match.group(1))
    return keys


def _update_pricing(body: list[str], pricing: dict[str, Any], diffs: list[str], name: str) -> None:
    span = _field_span(body, "pricing")
    if span is None:
        lines = ["      pricing:\n"]
        for key, value in pricing.items():
            lines.append(f"        {key}: {_format_number(value)}\n")
            diffs.append(f"{name}.pricing.{key}: <missing> -> {value}")
        _insert_after(body, lines, ("aliases", "context_window", "max_output_tokens", "modalities"))
        return
    start, end = span
    block = body[start:end]
    present = set(_existing_pricing_keys(block))
    for key, value in pricing.items():
        if key not in present:
            continue
        key_span = _field_span(block, key, indent="        ")
        if key_span is None:
            continue
        k_start, k_end = key_span
        current = yaml.safe_load("".join(block[k_start:k_end])) or {}
        old = current.get(key) if isinstance(current, dict) else current
        if _values_equal(old, value):
            continue
        block[k_start:k_end] = [f"        {key}: {_format_number(value)}\n"]
        diffs.append(f"{name}.pricing.{key}: {old} -> {value}")
    body[start:end] = block


def _apply_facts(
    body: list[str],
    facts: dict[str, Any],
    diffs: list[str],
    name: str,
    gemma_comment: bool = False,
) -> None:
    if "context_window" in facts:
        _set_scalar(body, "context_window", facts["context_window"], ("aliases",), diffs, name)
    if "max_output_tokens" in facts:
        _set_scalar(
            body,
            "max_output_tokens",
            facts["max_output_tokens"],
            ("aliases", "context_window"),
            diffs,
            name,
        )
    if "modalities" in facts:
        _set_modalities(
            body,
            facts["modalities"],
            diffs,
            name,
            comment=GEMMA_COMMENT if gemma_comment else None,
        )
    if "pricing" in facts:
        _update_pricing(body, facts["pricing"], diffs, name)


def _model_span(lines: list[str], name: str) -> tuple[int, int] | None:
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"^    {re.escape(name)}:\s*$", line):
            start = index
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        if MODEL_KEY_RE.match(lines[end]):
            break
        if lines[end].startswith("  ") and not lines[end].startswith("    "):
            if FIELD_KEY_RE.match("  " + lines[end].lstrip()) or lines[end].startswith("  -"):
                break
        end += 1
    return start, end


def _render_new_entry(name: str, facts: dict[str, Any]) -> str:
    lines = [f"    {name}:\n"]
    if "context_window" in facts:
        lines.append(f"      context_window: {_format_number(facts['context_window'])}\n")
    if "max_output_tokens" in facts:
        lines.append(f"      max_output_tokens: {_format_number(facts['max_output_tokens'])}\n")
    if "modalities" in facts:
        lines.extend(_modalities_block(facts["modalities"]))
    if "pricing" in facts:
        lines.append("      pricing:\n")
        for key, value in facts["pricing"].items():
            lines.append(f"        {key}: {_format_number(value)}\n")
    lines.append("\n")
    return "".join(lines)


def sync_catalog(
    catalog_path: Path | str,
    *,
    from_file: Path | str | None = None,
    url: str = DEFAULT_URL,
    check: bool = False,
    add: list[str] | None = None,
    api: dict[str, Any] | None = None,
) -> SyncResult:
    path = Path(catalog_path)
    original = path.read_text(encoding="utf-8")
    if not original.endswith("\n"):
        original += "\n"
    payload = api if api is not None else load_api(from_file=from_file, url=url)
    records = flatten_records(payload)
    data = yaml.safe_load(original) or {}
    spec = data.get("spec") or {}
    models = spec.get("models") or {}
    if not isinstance(models, dict):
        models = {}
    result = SyncResult()
    text = original
    for name, raw in models.items():
        aliases = [str(item) for item in (raw.get("aliases") or [])] if isinstance(raw, dict) else []
        record = match_record(str(name), aliases, records)
        if record is None:
            result.unmatched.append(str(name))
            continue
        facts = facts_from_record(record)
        existing_modalities = raw.get("modalities") if isinstance(raw, dict) else None
        incoming_modalities = facts.get("modalities") if isinstance(facts.get("modalities"), dict) else {}
        old_in = existing_modalities.get("input") if isinstance(existing_modalities, dict) else []
        old_out = existing_modalities.get("output") if isinstance(existing_modalities, dict) else []
        new_in = incoming_modalities.get("input") or []
        new_out = incoming_modalities.get("output") or []
        merged_in = _union_modalities(old_in, new_in)
        merged_out = _union_modalities(old_out, new_out)
        if merged_in or merged_out:
            facts["modalities"] = {"input": merged_in, "output": merged_out}
        else:
            facts.pop("modalities", None)
        if str(name) == GEMMA_DEPLOYMENT:
            modalities = dict(facts.get("modalities") or {})
            modalities["input"] = ["text"]
            facts["modalities"] = modalities
        lines = text.splitlines(keepends=True)
        span = _model_span(lines, str(name))
        if span is None:
            result.unmatched.append(str(name))
            continue
        start, end = span
        body = lines[start + 1 : end]
        before_len = len(result.field_diffs)
        _apply_facts(body, facts, result.field_diffs, str(name), gemma_comment=str(name) == GEMMA_DEPLOYMENT)
        if len(result.field_diffs) > before_len:
            result.updated.append(str(name))
        text = "".join(lines[: start + 1] + body + lines[end:])
    for model_id in add or []:
        if model_id in models:
            continue
        record = match_record(model_id, [model_id], records)
        if record is None:
            result.unmatched.append(model_id)
            continue
        facts = facts_from_record(record)
        entry = _render_new_entry(model_id, facts)
        if not text.endswith("\n"):
            text += "\n"
        text += entry
        result.added.append(model_id)
        result.field_diffs.append(f"{model_id}: added")
    if text != original:
        result.diff_text = "".join(
            unified_diff(
                original.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )
        if check:
            result.exit_code = 1
        else:
            path.write_text(text, encoding="utf-8")
    _print_report(result)
    return result


def _print_report(result: SyncResult) -> None:
    if result.updated:
        print("updated:")
        for name in result.updated:
            print(f"  {name}")
    if result.added:
        print("added:")
        for name in result.added:
            print(f"  {name}")
    if result.unmatched:
        print("unmatched:")
        for name in result.unmatched:
            print(f"  {name}")
    if result.field_diffs:
        print("fields:")
        for item in result.field_diffs:
            print(f"  {item}")
    if result.diff_text and result.exit_code != 0:
        print(result.diff_text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--from-file", type=Path, default=None)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--add", nargs="*", default=[])
    args = parser.parse_args(argv)
    result = sync_catalog(
        args.catalog,
        from_file=args.from_file,
        url=args.url,
        check=args.check,
        add=args.add,
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
