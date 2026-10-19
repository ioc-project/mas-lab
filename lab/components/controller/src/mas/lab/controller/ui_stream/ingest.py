#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Publish native records onto the UI envelope bus."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from mas.lab.controller.ui_stream.bus import EventBus
from mas.lab.controller.ui_stream.governance import GovernanceStore, compact_context_record
from mas.lab.controller.ui_stream.metrics import MetricsStore, coerce_timestamp
from mas.lab.controller.ui_stream.plot_folds import LivePlotFolds
from mas.lab.controller.ui_stream.refs import DataSourceRef, envelope

_HITL_KINDS = frozenset({"hitl_gate", "hitl_resolve", "hitl_request", "hitl_response", "hitl_timeout"})
_FLUSH_KINDS = frozenset({"mas_call_end", "execution_end", "run.terminal"})
_TICK_KINDS = frozenset({"llm_call_end", "tool_call_start", "execution_start", "mas_call_end", "execution_end"})
_GOV_KINDS = frozenset({"context_assembled", "context_usage"})
_GOV_FLUSH_KINDS = frozenset({"context_usage", "llm_call_end", "mas_call_end"})
_SNAPSHOT_INTERVAL_S = 1.0
_CHART_KEYS = (
    "data",
    "cards",
    "nodes",
    "edges",
    "series",
    "events",
    "diracs",
    "agents",
    "roster",
    "fragments",
    "activity",
)


def live_tick(record: dict[str, Any]) -> dict[str, Any] | None:
    """Keep JSON chart data; drop HTML shells. Same rule for every plot."""
    if not isinstance(record, dict):
        return None
    html = record.get("html")
    out = {key: value for key, value in record.items() if key != "html"}
    if html and not any(out.get(key) not in (None, "", [], {}) for key in _CHART_KEYS):
        return None
    return out


def _envelope_ts(record: dict[str, Any]) -> str | None:
    ts = coerce_timestamp(record.get("timestamp", record.get("ts", record.get("snapshot_at"))))
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, UTC).isoformat()


class NativeIngest:
    def __init__(self, bus: EventBus, metrics: MetricsStore, governance: GovernanceStore | None = None) -> None:
        self.bus = bus
        self.metrics = metrics
        self.governance = governance or GovernanceStore()
        self._last_snapshot: dict[str, float] = {}
        self._last_gov: dict[str, float] = {}
        self._plots = LivePlotFolds()
        self._skip_plots: set[str] = set()

    def clear(self) -> None:
        self._last_snapshot.clear()
        self._last_gov.clear()
        self._plots = LivePlotFolds()
        self._skip_plots.clear()

    def seed_spec(self, ref: DataSourceRef, snap: dict[str, Any], *, replay: bool = False) -> None:
        """Publish topology from a spec snapshot so GET/retry and the plot are not empty."""
        if "custom.topology" in self._skip_plots:
            return
        payload = self._plots.seed_spec(ref, snap)
        if not payload.get("nodes"):
            return
        self.bus.publish(
            envelope("custom.topology.snapshot", ref, "custom", payload, replay=replay, complete=False)
        )

    def ingest(self, ref: DataSourceRef, record: dict[str, Any], *, replay: bool = False) -> None:
        scoped = ref.merge(
            {
                "scenario": record.get("scenario"),
                "overlay": record.get("overlay"),
                "test": record.get("test") or record.get("item"),
            }
        )
        kind = str(record.get("kind") or "unknown")
        if kind in _GOV_KINDS:
            if not self._wants_context(scoped):
                return
            record = compact_context_record(record)
        event_id = str(record.get("call_id") or record.get("id") or "") or None
        stamped = _envelope_ts(record)
        self.bus.publish(
            envelope(
                f"native.{kind}",
                scoped,
                "native",
                record,
                replay=replay,
                event_id=event_id,
                ts=stamped,
            )
        )
        if kind in _HITL_KINDS:
            self.bus.publish(
                envelope(
                    f"control.{kind}",
                    scoped,
                    "control",
                    record,
                    replay=replay,
                    event_id=event_id,
                    ts=stamped,
                )
            )
        if kind == "metric":
            stats = self.metrics.apply_step(scoped, record)
            self.bus.publish(
                envelope("metric.snapshot", scoped, "metric", stats, replay=replay, ts=stamped, complete=False)
            )
            self.bus.publish(
                envelope("artifact.metric", scoped, "artifact", record, replay=replay, ts=stamped)
            )
            return
        if kind.startswith("control."):
            self.bus.publish(
                envelope(kind if kind.startswith("control.") else f"control.{kind}", scoped, "control", record, replay=replay, ts=stamped)
            )
            return
        if kind == "scope.progress":
            self.bus.publish(
                envelope("metric.scope", scoped, "metric", record, replay=replay, ts=stamped)
            )
            self.bus.publish(
                envelope("job.progress", scoped, "job", record, replay=replay, ts=stamped)
            )
            return
        if kind.startswith("artifact."):
            flag = record.get("complete") if isinstance(record.get("complete"), bool) else None
            self.bus.publish(
                envelope(kind, scoped, "artifact", record, replay=replay, ts=stamped, complete=flag)
            )
            if kind.endswith(".sealed") or flag is True:
                self.seal_artefacts(scoped)
            return
        if kind.startswith("custom."):
            record = live_tick(record)
            if record is None:
                return
            flag = record.get("complete") if isinstance(record.get("complete"), bool) else None
            self.bus.publish(
                envelope(kind, scoped, "custom", record, replay=replay, ts=stamped, complete=flag)
            )
            return
        if kind.startswith("series."):
            record = live_tick(record)
            if record is None:
                return
            flag = record.get("complete") if isinstance(record.get("complete"), bool) else None
            self.bus.publish(
                envelope(kind, scoped, "artifact", record, replay=replay, ts=stamped, complete=flag)
            )
            return
        usage = self.metrics.apply_native(scoped, record, wall=not replay)
        updated = bool(usage.pop("_updated", None))
        if updated or kind in _FLUSH_KINDS or kind in _TICK_KINDS:
            payload = usage if updated else self.metrics.snapshot(scoped)
            self._maybe_snapshot(
                scoped,
                payload,
                force=kind in _FLUSH_KINDS or kind in _TICK_KINDS,
                replay=replay,
                ts=stamped,
            )
        if self._wants_context(scoped):
            gov = self.governance.apply(scoped, record)
            gov_updated = bool(gov.pop("_updated", None))
            if gov_updated or kind in _GOV_FLUSH_KINDS:
                self._maybe_gov(
                    scoped,
                    gov if gov_updated else self.governance.snapshot(scoped),
                    force=kind in _GOV_FLUSH_KINDS,
                    replay=replay,
                    ts=stamped,
                )
                if kind in _GOV_FLUSH_KINDS and scoped.session:
                    self._request_inspect("context", scoped)
        def _emit_plot(target: DataSourceRef, rec: dict[str, Any]) -> None:
            plot_kind = str(rec.get("kind") or "")
            source = "custom" if plot_kind.startswith("custom.") else "artifact"
            self.bus.publish(
                envelope(plot_kind, target, source, rec, replay=replay, ts=stamped, complete=False)
            )

        self._plots.apply(_emit_plot, scoped, record, skip=self._skip_plots)
        if kind in _FLUSH_KINDS:
            self.seal_artefacts(scoped)

    def seal_artefacts(self, ref: DataSourceRef) -> None:
        """Publish ``{stream}.sealed`` with ``complete=True`` for each produced fold."""
        from mas.lab.controller.ui_stream.artefact_bind import LIVE_GRAPH, is_sealed_topic

        for name, node in LIVE_GRAPH.items():
            if name == "events":
                continue
            stream = str(node.get("stream") or name)
            ticks = self.bus.snapshot([stream, f"{stream}.*"], ref)
            if not ticks:
                continue
            if any(is_sealed_topic(str(getattr(item, "topic", "") or "")) for item in ticks):
                continue
            last = ticks[-1]
            payload = last.payload if isinstance(last.payload, dict) else {"kind": name}
            self.bus.publish(
                envelope(
                    f"{stream}.sealed",
                    ref,
                    "artifact",
                    payload,
                    complete=True,
                    artefact=name,
                )
            )

    def flush_snapshot(self, ref: DataSourceRef, *, replay: bool = False, ts: str | None = None) -> None:
        self._publish_snapshot(ref, self.metrics.snapshot(ref), replay=replay, ts=ts)
        if self._wants_context(ref):
            self._publish_gov(ref, self.governance.snapshot(ref), replay=replay, ts=ts)

    def _wants_context(self, ref: DataSourceRef) -> bool:
        demand = getattr(self, "_demand", None)
        if callable(demand):
            return bool(demand("context", ref))
        demands = getattr(self.bus, "demands_topic", None)
        if not callable(demands):
            return False
        return bool(demands("governance.context", ref))

    def _request_inspect(self, pull_id: str, ref: DataSourceRef) -> None:
        hook = getattr(self, "_on_inspect", None)
        if callable(hook):
            hook(pull_id, ref)

    def _maybe_snapshot(
        self,
        ref: DataSourceRef,
        payload: dict[str, Any],
        *,
        force: bool,
        replay: bool,
        ts: str | None = None,
    ) -> None:
        key = ref.coordinate() + "|" + (ref.overlay or "")
        now = time.monotonic()
        last = self._last_snapshot.get(key, 0.0)
        if not force and (now - last) < _SNAPSHOT_INTERVAL_S:
            return
        self._last_snapshot[key] = now
        self._publish_snapshot(ref, payload, replay=replay, ts=ts)

    def _maybe_gov(
        self,
        ref: DataSourceRef,
        payload: dict[str, Any],
        *,
        force: bool,
        replay: bool,
        ts: str | None = None,
    ) -> None:
        if not payload:
            return
        key = ref.coordinate() + "|" + (ref.overlay or "")
        now = time.monotonic()
        last = self._last_gov.get(key, 0.0)
        if not force and (now - last) < _SNAPSHOT_INTERVAL_S:
            return
        self._last_gov[key] = now
        self._publish_gov(ref, payload, replay=replay, ts=ts)

    def _publish_snapshot(
        self, ref: DataSourceRef, payload: dict[str, Any], *, replay: bool, ts: str | None = None
    ) -> None:
        self.bus.publish(
            envelope("metric.snapshot", ref, "metric", payload, replay=replay, ts=ts, complete=False)
        )
        self.bus.publish(
            envelope("metric.usage", ref, "metric", payload, replay=replay, ts=ts, complete=False)
        )

    def _publish_gov(
        self, ref: DataSourceRef, payload: dict[str, Any], *, replay: bool, ts: str | None = None
    ) -> None:
        if not payload:
            return
        self.bus.publish(envelope("governance.context", ref, "governance", payload, replay=replay, ts=ts))
