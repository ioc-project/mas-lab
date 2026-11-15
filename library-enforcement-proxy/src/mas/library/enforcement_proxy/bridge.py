# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""One reference-based mediation path for request and delivery policies."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import secrets
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


class AuthorizationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DecisionRecord:
    evaluation_id: str
    activity_trace_id: str
    activity_span_id: str
    candidate_sha256: str
    recipient: str
    allowed: bool
    authorization_record_id: str


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def span_payload(trace: str, span: str, name: str, attributes: dict[str, str], *, parent: str = "") -> dict:
    now = str(time.time_ns())
    data = {
        "traceId": trace,
        "spanId": span,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": now,
        "endTimeUnixNano": now,
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attributes.items()],
    }
    if parent:
        data["parentSpanId"] = parent
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "enforcement-proxy-maslab"}}]
                },
                "scopeSpans": [{"scope": {"name": "enforcement-proxy-maslab", "version": "0.1.0"}, "spans": [data]}],
            }
        ]
    }


class Bridge:
    def __init__(self, config_file: str, agent: str) -> None:
        self.config = json.loads(Path(config_file).read_text())
        self.agent = agent
        self.identity = self.config["identities"][agent]
        self.timeout = float(self.config.get("timeout", 20))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 60:
            raise ValueError("Mediation timeout must be finite, positive and at most 60 seconds")
        self.endpoint = self.config["adapter_url"].rstrip("/")
        self.collector = self.config["otlp_url"]
        for name, value in (("adapter", self.endpoint), ("collector", self.collector)):
            u = urlsplit(value)
            allowed = u.scheme == "https" or (
                name == "collector" and u.scheme == "http" and self.config.get("allow_insecure_collector") is True
            )
            if not allowed or not u.hostname or u.username or u.password or u.fragment or u.query:
                raise ValueError(f"Invalid {name} endpoint")
        base = Path(config_file).resolve().parent
        self.tls = ssl.create_default_context(cafile=str(base / self.config["ca_file"]))
        self.tls.load_cert_chain(str(base / self.identity["certificate_file"]), str(base / self.identity["key_file"]))
        self._closed = threading.Event()
        for tool in self.identity.get("tools", {}):
            self.request_route(tool)

    def client(self) -> httpx.AsyncClient:
        # Per-operation clients are safe across native agent threads and event loops.
        return httpx.AsyncClient(
            mounts={self.endpoint: httpx.AsyncHTTPTransport(verify=self.tls)}, trust_env=False, follow_redirects=False
        )

    def close(self) -> None:
        self._closed.set()

    def request_route(self, tool: str) -> dict:
        route = self.identity.get("tools", {}).get(tool)
        if (
            not isinstance(route, dict)
            or route.get("kind") not in {"delegation", "tool"}
            or route.get("destination") not in self.config["identities"]
            or route.get("transport") != "http"
            or (route.get("kind") == "tool" and not route.get("tool"))
        ):
            raise AuthorizationUnavailable("No configured HTTP authorization route for tool")
        return route

    async def authorize(
        self, *, destination: str, kind: str, transport: str, payload: bytes, context: dict, tool: str = ""
    ) -> DecisionRecord:
        if self._closed.is_set():
            raise AuthorizationUnavailable("Mediation integration is closed")
        if destination not in self.config["identities"] or kind not in {"delegation", "tool", "release"}:
            raise AuthorizationUnavailable("Unregistered authorization destination or kind")
        if transport != "http":
            raise AuthorizationUnavailable("Only HTTP operation transport is supported")
        # Freeze all content before publication. No policy may rewrite it.
        envelope = canonical(
            {"candidate": json.loads(payload), "context": context, "kind": kind, "tool": tool, "transport": transport}
        )
        if len(envelope) > int(self.config.get("max_evidence_bytes", 1048576)):
            raise AuthorizationUnavailable("Candidate evidence exceeds the configured bound")
        trace, span, send_span = secrets.token_hex(16), secrets.token_hex(8), secrets.token_hex(8)
        evaluation = str(uuid.uuid4())
        digest = hashlib.sha256(payload).hexdigest()
        session = str(context.get("session_id") or context.get("context_id") or trace)
        attrs = {
            "application_id": self.config["mas_id"],
            "session.id": session,
            "agent_id": self.identity["id"],
            "ioc.activity.schema": "ioc.proposed_activity.v1",
            "ioc.activity.producer": "maslab",
            "ioc.activity.phase": "proposed",
            "ioc.activity.body": envelope.decode(),
            "ioc.activity.candidate_sha256": digest,
            "ioc.activity.kind": kind,
            "ioc.activity.source": self.identity["id"],
            "ioc.activity.destination": self.config["identities"][destination]["id"],
            "ioc.evaluation.id": evaluation,
        }
        activity = span_payload(trace, span, "ioc.maslab.proposed", attrs)
        deadline = int((time.time() + self.timeout) * 1000)
        request = {
            "evaluation_id": evaluation,
            "destination": destination,
            "kind": kind,
            "transport": transport,
            "trace_id": trace,
            "span_id": span,
            "send_trace_id": trace,
            "send_span_id": send_span,
            "session_id": session,
            "decision_deadline_ms": deadline,
        }
        if tool:
            request["tool"] = tool
        try:
            async with asyncio.timeout(self.timeout), self.client() as client:
                exported = await client.post(self.collector, json=activity, timeout=self.timeout)
                exported.raise_for_status()
                receipt = exported.json() if exported.content else {}
                if receipt.get("partialSuccess", {}).get("rejectedSpans", "0") not in (0, "0"):
                    raise AuthorizationUnavailable("Collector rejected proposed evidence")
                if self._closed.is_set():
                    raise AuthorizationUnavailable("Mediation integration closed during publication")
                response = await client.post(self.endpoint + "/evaluate", json=request, timeout=self.timeout)
                response.raise_for_status()
                result = response.json()
                if (
                    result.get("evaluation_id") != evaluation
                    or result.get("decision") not in {"allow", "deny"}
                    or not result.get("authorization_record_id")
                ):
                    raise AuthorizationUnavailable("Invalid authorization receipt")
                uuid.UUID(result["authorization_record_id"])
                if time.time() * 1000 >= deadline or self._closed.is_set():
                    raise AuthorizationUnavailable("Authorization expired or integration closed")
                decision = DecisionRecord(
                    evaluation,
                    trace,
                    span,
                    digest,
                    destination,
                    result["decision"] == "allow",
                    result["authorization_record_id"],
                )
                # Publish the actual authorization exchange as correlation evidence. This
                # does not claim that the protected operation or delivery has happened.
                evidence = span_payload(
                    trace,
                    send_span,
                    "ioc.maslab.authorization",
                    {
                        "session.id": session,
                        "ioc.evaluation.id": evaluation,
                        "ioc.authorization.record_id": decision.authorization_record_id,
                        "ioc.authorization.decision": result["decision"],
                    },
                    parent=span,
                )
                recorded = await client.post(self.collector, json=evidence, timeout=self.timeout)
                recorded.raise_for_status()
                if time.time() * 1000 >= deadline or self._closed.is_set():
                    raise AuthorizationUnavailable("Authorization expired before release")
                return decision
        except asyncio.CancelledError:
            raise
        except Exception:
            # Candidate text and credential details must not escape through model errors.
            raise AuthorizationUnavailable("Authorization unavailable; operation blocked") from None
