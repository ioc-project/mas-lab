#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared live-LLM slot scheduler (token bucket + in-flight cap).

Sits *after* routing and *after* ``llm_cache``: cache hits never acquire a
slot. Runtime engines and pipeline judge calls share one file-backed store so
multiprocess / multithreaded evaluations cannot DDoS the provider.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]

_LOCK = threading.Lock()
_POLICY: "SchedulerPolicy | None" = None

_VALID_KEYS = frozenset({"provider", "model", "user"})
_MIDDLEWARE_IDS = frozenset(
    {"llm_scheduler", "llm-scheduler", "rate_limit", "rate_limiter", "llm_pace"}
)


def resolve_state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env = os.environ.get("MAS_LLM_SCHEDULER_STATE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from mas.runtime.xdg import mas_cache_root

    return (mas_cache_root() / "llm_scheduler.json").resolve()


@dataclass(frozen=True)
class SchedulerPolicy:
    max_in_flight: int = 8
    requests_per_minute: float = 0.0
    keys: tuple[str, ...] = ("provider", "model")
    user: str = "default"
    provider: str = ""
    state_path: Path | None = None
    timeout_s: float = 300.0

    @classmethod
    def from_params(cls, params: dict[str, Any] | None = None) -> "SchedulerPolicy":
        params = dict(params or {})
        raw_keys = params.get("keys") or ("provider", "model")
        if isinstance(raw_keys, str):
            raw_keys = [part.strip() for part in raw_keys.split(",") if part.strip()]
        keys = tuple(k for k in raw_keys if k in _VALID_KEYS) or ("provider", "model")
        env_rpm = os.environ.get("MAS_LLM_RPM", "").strip()
        env_flight = os.environ.get("MAS_LLM_MAX_IN_FLIGHT", "").strip()
        rpm_raw = params.get("requests_per_minute", params.get("rpm"))
        rpm = float(env_rpm) if env_rpm else float(0.0 if rpm_raw is None else rpm_raw)
        if env_flight:
            flight = int(env_flight)
        elif params.get("max_in_flight") is not None:
            flight = int(params.get("max_in_flight"))
        else:
            flight = 8
        user = (
            os.environ.get("MAS_LLM_SCHEDULER_USER", "").strip()
            or str(params.get("user") or "default")
        )
        provider = (
            os.environ.get("MAS_LLM_SCHEDULER_PROVIDER", "").strip()
            or str(params.get("provider") or "")
        )
        path_raw = params.get("state_path") or params.get("path")
        raw_timeout = params.get("timeout_s")
        timeout_s = 300.0 if raw_timeout is None else float(raw_timeout)
        return cls(
            max_in_flight=max(0, flight),
            requests_per_minute=max(0.0, rpm),
            keys=keys,
            user=user,
            provider=provider,
            state_path=resolve_state_path(path_raw) if path_raw else resolve_state_path(),
            timeout_s=max(0.05, timeout_s),
        )

    def enabled(self) -> bool:
        return self.max_in_flight > 0 or self.requests_per_minute > 0

    def bucket_key(self, *, model: str = "", provider: str = "", user: str = "") -> str:
        parts: list[str] = []
        for dim in self.keys:
            if dim == "provider":
                parts.append(f"provider={provider or self.provider or 'default'}")
            elif dim == "model":
                parts.append(f"model={model or 'default'}")
            elif dim == "user":
                parts.append(f"user={user or self.user or 'default'}")
        return "|".join(parts) or "default"


def install_policy(policy: SchedulerPolicy | dict[str, Any] | None) -> SchedulerPolicy | None:
    """Process-global policy so judge completions share the runtime limiter."""
    global _POLICY
    if policy is None:
        _POLICY = None
        return None
    resolved = policy if isinstance(policy, SchedulerPolicy) else SchedulerPolicy.from_params(policy)
    _POLICY = resolved
    return resolved


def install_policy_from_pipeline(pipeline: list[dict[str, Any]] | None) -> SchedulerPolicy | None:
    for step in pipeline or []:
        mid = str(step.get("middleware") or step.get("id") or "").strip().lower()
        if mid in _MIDDLEWARE_IDS:
            return install_policy(dict(step.get("params") or {}))
    return current_policy()


def current_policy() -> SchedulerPolicy | None:
    return _POLICY


def reset_policy() -> None:
    install_policy(None)


@contextmanager
def _locked_state(path: Path) -> Iterator[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                raw = path.read_text(encoding="utf-8") if path.is_file() else "{}"
                data = json.loads(raw or "{}")
            except json.JSONDecodeError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            yield data
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _live_leases(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    live: list[dict[str, Any]] = []
    for lease in bucket.get("leases") or []:
        if not isinstance(lease, dict):
            continue
        pid = int(lease.get("pid") or 0)
        if pid <= 0 or not _pid_alive(pid):
            continue
        live.append(lease)
    return live


def _try_take(policy: SchedulerPolicy, key: str) -> float | None:
    """Acquire one slot. Return ``None`` on success, else seconds to wait."""
    path = policy.state_path or resolve_state_path()
    with _locked_state(path) as data:
        buckets = data.setdefault("buckets", {})
        now = time.time()
        bucket = buckets.get(key) or {
            "tokens": float(policy.requests_per_minute or policy.max_in_flight or 1),
            "updated": now,
            "leases": [],
        }
        rpm = policy.requests_per_minute
        if rpm > 0:
            elapsed = max(0.0, now - float(bucket.get("updated") or now))
            capacity = max(rpm, float(policy.max_in_flight or rpm), 1.0)
            bucket["tokens"] = min(
                capacity,
                float(bucket.get("tokens") or 0.0) + elapsed * (rpm / 60.0),
            )
        bucket["updated"] = now
        leases = _live_leases(bucket)
        in_flight = len(leases)
        flight_ok = policy.max_in_flight <= 0 or in_flight < policy.max_in_flight
        rate_ok = rpm <= 0 or float(bucket.get("tokens") or 0.0) >= 1.0
        if flight_ok and rate_ok:
            leases.append({"pid": os.getpid(), "since": now})
            bucket["leases"] = leases
            bucket["in_flight"] = len(leases)
            if rpm > 0:
                bucket["tokens"] = float(bucket.get("tokens") or 0.0) - 1.0
            buckets[key] = bucket
            return None
        bucket["leases"] = leases
        bucket["in_flight"] = in_flight
        buckets[key] = bucket
        if not flight_ok:
            return 0.05
        if rpm > 0:
            return min(1.0, max(0.02, 60.0 / rpm))
        return 0.05


def _release(policy: SchedulerPolicy, key: str) -> None:
    path = policy.state_path or resolve_state_path()
    if not path.is_file() and not path.with_name(path.name + ".lock").is_file():
        return
    with _locked_state(path) as data:
        buckets = data.setdefault("buckets", {})
        bucket = buckets.get(key)
        if not isinstance(bucket, dict):
            return
        leases = _live_leases(bucket)
        pid = os.getpid()
        for index in range(len(leases) - 1, -1, -1):
            if int(leases[index].get("pid") or 0) == pid:
                leases.pop(index)
                break
        else:
            if leases:
                leases.pop()
        bucket["leases"] = leases
        bucket["in_flight"] = len(leases)
        buckets[key] = bucket


def acquire(
    *,
    model: str = "",
    provider: str = "",
    user: str = "",
    policy: SchedulerPolicy | None = None,
) -> str | None:
    """Block until a live LLM slot is available. Returns the bucket key, or None if unlimited."""
    pol = policy or current_policy()
    if pol is None or not pol.enabled():
        return None
    key = pol.bucket_key(model=model, provider=provider, user=user)
    deadline = time.monotonic() + pol.timeout_s
    while True:
        with _LOCK:
            wait = _try_take(pol, key)
        if wait is None:
            return key
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"llm_scheduler timed out waiting for slot key={key!r} "
                f"(max_in_flight={pol.max_in_flight}, rpm={pol.requests_per_minute})"
            )
        time.sleep(wait)


async def acquire_async(
    *,
    model: str = "",
    provider: str = "",
    user: str = "",
    policy: SchedulerPolicy | None = None,
) -> str | None:
    import asyncio

    pol = policy or current_policy()
    if pol is None or not pol.enabled():
        return None
    key = pol.bucket_key(model=model, provider=provider, user=user)
    deadline = time.monotonic() + pol.timeout_s
    while True:
        with _LOCK:
            wait = _try_take(pol, key)
        if wait is None:
            return key
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"llm_scheduler timed out waiting for slot key={key!r} "
                f"(max_in_flight={pol.max_in_flight}, rpm={pol.requests_per_minute})"
            )
        await asyncio.sleep(wait)


def release(key: str | None, *, policy: SchedulerPolicy | None = None) -> None:
    if not key:
        return
    pol = policy or current_policy()
    if pol is None:
        return
    with _LOCK:
        _release(pol, key)


@contextmanager
def llm_slot(
    *,
    model: str = "",
    provider: str = "",
    user: str = "",
    policy: SchedulerPolicy | None = None,
) -> Iterator[str | None]:
    key = acquire(model=model, provider=provider, user=user, policy=policy)
    try:
        yield key
    finally:
        release(key, policy=policy)


def infer_model(engine: Any) -> str:
    current = engine
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        model = getattr(current, "model", None)
        if model:
            return str(model)
        current = getattr(current, "inner", None)
    return os.environ.get("MAS_LLM_SCHEDULER_MODEL", "").strip()


def infer_provider(engine: Any, fallback: str = "") -> str:
    if fallback:
        return fallback
    env = os.environ.get("MAS_LLM_SCHEDULER_PROVIDER", "").strip()
    if env:
        return env
    current = engine
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("api_base", "base_url", "_openai_base_url"):
            value = getattr(current, attr, None)
            if value:
                text = str(value)
                if "://" in text:
                    try:
                        from urllib.parse import urlparse

                        host = urlparse(text).hostname
                        if host:
                            return host
                    except Exception:
                        return text
                return text
        current = getattr(current, "inner", None)
    return "default"
