#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mas.lab import paths as _paths


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _count_spans(trace_path: Path) -> int:
    if not trace_path.exists():
        return 0
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    total = 0
    for resource in payload.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            total += len(scope.get("spans", []))
    return total


def compute_metrics(
    feed_path: Path | None = None,
    trace_path: Path | None = None,
) -> dict:
    """Compute MAS metrics by reading the UI feed JSONL directly (no HTTP)."""
    if feed_path is None:
        feed_path = Path(os.getenv("UI_FEED_PATH", str(_paths.lab_output() / "ui_feed.jsonl")))
    if trace_path is None:
        trace_path = Path(os.getenv("OTEL_TRACES_PATH", "observability/otel-data/traces.json"))

    metrics: dict[str, int] = {}
    if feed_path.exists():
        with feed_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                kind = event.get("kind")
                if kind == "challenge_common_ground":
                    metrics["common_ground"] = metrics.get("common_ground", 0) + 1
                elif kind == "challenge_ontology":
                    metrics["semantic_misalignment"] = metrics.get("semantic_misalignment", 0) + 1
                elif kind == "challenge_knowledge_gap":
                    metrics["knowledge_asymmetry"] = metrics.get("knowledge_asymmetry", 0) + 1
                elif kind == "challenge_orchestration":
                    metrics["orchestration"] = metrics.get("orchestration", 0) + 1
                elif kind == "challenge_communication":
                    metrics["communication"] = metrics.get("communication", 0) + 1
                elif kind == "challenge_verification":
                    metrics["verification"] = metrics.get("verification", 0) + 1

    if os.getenv("METRICS_INCLUDE_BASIC") == "1":
        metrics.setdefault("llm_calls", 0)
        metrics.setdefault("tool_calls", 0)
        metrics.setdefault("memory_reads", 0)
        metrics["otel_spans"] = _count_spans(trace_path)

    return metrics


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/metrics":
            self.send_error(404)
            return
        data = json.dumps(compute_metrics()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    host = os.getenv("METRICS_HOST", "0.0.0.0")
    port = int(os.getenv("METRICS_PORT", "8090"))
    server = ReusableThreadingHTTPServer((host, port), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
