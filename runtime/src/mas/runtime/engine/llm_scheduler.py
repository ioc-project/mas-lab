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
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]

_LOCK = threading.Lock()
_POLICY: "SchedulerPolicy | None" = None

_VALID_KEYS = frozenset({"provider", "model", "user"})

# Explicit overrides a host (``mas-ctl``, ``mas-lab``) installs for this process.
# The runtime never reads the environment: the CLI reads its variables once and
# hands the values over here. Keys: ``requests_per_minute``, ``max_in_flight``,
# ``user``, ``provider``, ``model``, ``state_path``. A key set here wins over
# the step's ``params``.
_OVERRIDES: dict[str, Any] = {}
_OVERRIDE_KEYS = frozenset({"requests_per_minute", "max_in_flight", "user", "provider", "model", "state_path"})


def install_overrides(overrides: Mapping[str, Any] | None) -> None:
    """Replace the process-wide scheduler overrides (``None`` clears them)."""
    unknown = set(overrides or {}) - _OVERRIDE_KEYS
    if unknown:
        raise ValueError(f"unknown llm_scheduler override(s): {sorted(unknown)}")
    _OVERRIDES.clear()
    _OVERRIDES.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})


_DEFAULT_USER: ContextVar[str] = ContextVar("mas_llm_scheduler_default_user", default="")


def _effective_overrides() -> dict[str, Any]:
    out = dict(_OVERRIDES)
    if not out.get("user") and _DEFAULT_USER.get():
        out["user"] = _DEFAULT_USER.get()
    return out


def current_overrides() -> dict[str, Any]:
    """What a spawned worker needs to reproduce: the installed overrides plus this run's default user."""
    return _effective_overrides()


def explicit_overrides() -> dict[str, Any]:
    """Only what a host installed (no run default), for save and restore."""
    return dict(_OVERRIDES)


@contextmanager
def default_user(user: str) -> Iterator[None]:
    """Fairness key for one run (this context) unless the host already overrode ``user``.

    Scoped to the block and to the calling context, not the process: the next experiment in a
    long-lived controller does not inherit this one's name.
    """
    token = _DEFAULT_USER.set(user or "")
    try:
        yield
    finally:
        _DEFAULT_USER.reset(token)


def resolve_state_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    override = _OVERRIDES.get("state_path")
    if override:
        return Path(str(override)).expanduser().resolve()
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
        over = _effective_overrides()
        rpm_raw = params.get("requests_per_minute", params.get("rpm"))
        if over.get("requests_per_minute") is not None:
            rpm = float(over["requests_per_minute"])
        else:
            rpm = float(0.0 if rpm_raw is None else rpm_raw)
        if over.get("max_in_flight") is not None:
            flight = int(over["max_in_flight"])
        elif params.get("max_in_flight") is not None:
            flight = int(params.get("max_in_flight"))
        else:
            flight = 8
        user = str(over.get("user") or params.get("user") or "default")
        provider = str(over.get("provider") or params.get("provider") or "")
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
    from mas.runtime.engine.infra_pipeline import is_scheduler_middleware

    for step in pipeline or []:
        if isinstance(step, dict) and is_scheduler_middleware(step):
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
    from mas.runtime.engine.infra_pipeline import call_model

    model = call_model(engine)
    if model:
        return model
    return str(_OVERRIDES.get("model") or "")


def infer_provider(engine: Any, fallback: str = "") -> str:
    if fallback:
        return fallback
    override = str(_OVERRIDES.get("provider") or "")
    if override:
        return override
    from urllib.parse import urlparse

    from mas.runtime.engine.infra_pipeline import call_api_base

    text = call_api_base(engine)
    if not text:
        return "default"
    if "://" in text:
        host = urlparse(text).hostname
        if host:
            return host
    return text
