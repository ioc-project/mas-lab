#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Paired fixture lifecycle: process group, pidfile, reap, reference count."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from mas.lab.benchmark.environment.model import FixtureSpec
from mas.lab.benchmark.environment.variables import Variables

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Any]


# A name is a secret when one of its `_`-separated words ends in a secret word (API_KEY, APIKEY,
# GH_TOKEN, DB_PASSWORD), or is a weaker word on its own (AUTH, COOKIE, SESSION). Whole words, so
# KEYBOARD and KEYCHAIN stay, and a short list of well-known harmless names is kept.
_STRONG_SUFFIX = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDENTIALS")
_WEAK_WORDS = frozenset({"AUTH", "COOKIE", "COOKIES", "SESSION", "PAT", "PASS", "BEARER", "JWT"})
_SECRET_NAMES = frozenset({"MYSQL_PWD", "PGPASSWORD", "AUTHORIZATION", "HTTP_AUTHORIZATION", "PROXY_AUTHORIZATION"})
# A URL that carries credentials (postgres://user:pw@host, redis://:pw@host) is a secret whatever its name.
_URL_CREDENTIALS = re.compile(r"://[^/\s:@]*:[^/\s@]+@")
# Fixtures talk to 127.0.0.1. A proxy from the environment (http_proxy, macOS system settings) would
# send that traffic away from the machine, or fail it, so these probes never use one.
_LOOPBACK_OPENER = build_opener(ProxyHandler({}))


def loopback_urlopen(target: Any, timeout: float) -> Any:
    return _LOOPBACK_OPENER.open(target, timeout=timeout)


def open_url(url: str, timeout: float) -> Any:
    """``urlopen`` that skips the environment's proxy for a loopback host and keeps it for any other."""
    from urllib.parse import urlparse
    from urllib.request import urlopen

    from mas.lab.benchmark.environment.limits import LOOPBACK_HOSTS

    if (urlparse(url).hostname or "").lower() in LOOPBACK_HOSTS:
        return loopback_urlopen(url, timeout)
    return urlopen(url, timeout=timeout)


_HARMLESS = frozenset(
    {"SSH_AUTH_SOCK", "GPG_AGENT_INFO", "TERM_SESSION_ID", "SECURITYSESSIONID", "DBUS_SESSION_BUS_ADDRESS"}
)


def looks_secret(name: str) -> bool:
    upper = name.upper()
    if upper in _HARMLESS or upper.startswith("XDG_SESSION_"):
        return False
    if upper in _SECRET_NAMES:
        return True
    words = upper.split("_")
    return any(word.endswith(_STRONG_SUFFIX) for word in words) or any(word in _WEAK_WORDS for word in words)


def scrubbed_environ(base_env: Mapping[str, str], *, passthrough: tuple[str, ...] = ()) -> dict[str, str]:
    """*base_env* minus names and URL values that look like secrets."""
    keep = set(passthrough)
    return {
        name: value
        for name, value in base_env.items()
        if name in keep or not (looks_secret(name) or _URL_CREDENTIALS.search(value))
    }


# What a process needs to run at all; nothing else comes along in ``env_mode: minimal``.
_MINIMAL_ENV = frozenset({"PATH", "HOME", "LANG", "TMPDIR", "TERM", "USER", "SYSTEMROOT", "TZ"})


def _fixture_env(spec: FixtureSpec, base_env: Mapping[str, str]) -> dict[str, str]:
    """The environment a fixture process gets: ``base_env`` (given by the CLI), minus secret-looking names.

    A fixture is a command from a lab file; it must not inherit the API keys of the process that
    runs the experiment. ``passthrough`` names variables to keep, ``env`` sets explicit values.
    ``env_mode: minimal`` is an allow list instead: only what a process needs to start,
    plus ``passthrough`` and ``env``.
    """
    keep = set(spec.passthrough)
    if spec.env_mode == "minimal":
        env = {
            name: value
            for name, value in base_env.items()
            if name in keep or name in _MINIMAL_ENV or name.startswith("LC_")
        }
        env.update(spec.env)
        env["MAS_FIXTURE_NAME"] = spec.name
        return env
    env = scrubbed_environ(base_env, passthrough=tuple(keep))
    env.update(spec.env)
    env["MAS_FIXTURE_NAME"] = spec.name
    return env


def _safe_scope(scope: str) -> str:
    """A scope key becomes a directory: no ``..``, no absolute path, no odd characters."""
    parts = []
    for part in str(scope).split("/"):
        clean = re.sub(r"[^A-Za-z0-9_.-]", "_", part)
        if clean in {"", ".", ".."}:
            clean = "_" + clean.replace(".", "_")
        parts.append(clean)
    safe = "/".join(parts)
    if safe != scope:
        safe += "-" + hashlib.sha1(scope.encode("utf-8")).hexdigest()[:8]
    return safe


def command_digest(command: tuple[str, ...]) -> str:
    blob = "\0".join(command).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


class _EarlyExit(RuntimeError):
    """The fixture process ended before it became ready."""


@dataclass
class FixtureHandle:
    spec: FixtureSpec
    port: int | None = None
    host: str = "127.0.0.1"
    pid: int | None = None
    pgid: int | None = None
    pidfile: Path | None = None
    command: tuple[str, ...] = ()
    data_dir: Path | None = None
    refs: int = 0
    started: bool = False
    launched: bool = False
    proc: subprocess.Popen[bytes] | None = None
    log_path: Path | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass
class FixtureRegistry:
    """One registry per benchmark batch."""

    output_dir: Path
    work_dir: Path
    emit: EmitFn | None = None
    # The environment fixtures may inherit. The CLI reads it and hands it in; this module never does.
    base_env: Mapping[str, str] = field(default_factory=dict)
    _emit_by_scope: dict[str, EmitFn] = field(default_factory=dict)
    vars: Variables = field(default_factory=Variables)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _vars_lock: threading.RLock = field(default_factory=threading.RLock)
    _handles: dict[tuple[str, str], FixtureHandle] = field(default_factory=dict)

    def bind_emit(self, scope_key: str, emit: EmitFn) -> None:
        """Send the ``fixture.*`` events of this scope to one run's log.

        One registry serves a whole batch. A single ``emit`` slot would be overwritten by whichever
        run bound last, and parallel runs would write each other's fixture events.
        """
        with self._lock:
            self._emit_by_scope[scope_key] = emit

    def unbind_emit(self, scope_key: str, emit: EmitFn) -> None:
        with self._lock:
            if self._emit_by_scope.get(scope_key) is emit:
                del self._emit_by_scope[scope_key]

    def pid_root(self) -> Path:
        path = self.output_dir / ".environment" / "fixtures"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def register(self, spec: FixtureSpec, *, scope_key: str = "") -> FixtureHandle:
        key = (spec.name, scope_key or spec.scope)
        with self._lock:
            handle = self._handles.get(key)
            if handle is None:
                handle = FixtureHandle(spec=spec)
                self._handles[key] = handle
            return handle

    def url(self, name: str, *, scope_key: str = "") -> str:
        handle = self._require(name, scope_key=scope_key)
        port = handle.port
        if port is None:
            raise KeyError(f"fixture {name!r} has no port")
        return f"http://{handle.host}:{port}"

    def port(self, name: str, *, scope_key: str = "") -> int:
        handle = self._require(name, scope_key=scope_key)
        if handle.port is None:
            raise KeyError(f"fixture {name!r} has no port")
        return int(handle.port)

    def host(self, name: str, *, scope_key: str = "") -> str:
        return self._require(name, scope_key=scope_key).host

    def _require(self, name: str, *, scope_key: str = "") -> FixtureHandle:
        if scope_key:
            with self._lock:
                handle = self._handles.get((name, scope_key))
            if handle is not None and handle.started:
                return handle
            raise KeyError(f"unknown fixture {name!r}")
        with self._lock:
            named = [handle for (item, _), handle in self._handles.items() if item == name and handle.started]
        if len(named) == 1:
            return named[0]
        if not named:
            raise KeyError(f"unknown fixture {name!r}")
        raise KeyError(f"fixture {name!r} has {len(named)} instances; pass scope_key")

    def start(self, spec: FixtureSpec, *, scope_key: str = "") -> FixtureHandle:
        """Start (or join) a fixture. Slow starts hold only this fixture's lock."""
        handle = self.register(spec, scope_key=scope_key)
        with handle.lock:
            if handle.started:
                handle.refs += 1
                return handle
            try:
                self._start_locked(handle)
            except BaseException:
                self._teardown(handle)
                raise
            handle.refs += 1
            return handle

    def start_once(self, spec: FixtureSpec, *, scope_key: str = "") -> FixtureHandle:
        handle = self.register(spec, scope_key=scope_key)
        with handle.lock:
            if handle.started:
                handle.refs += 1
                return handle
            return self.start(spec, scope_key=scope_key)

    def _teardown(self, handle: FixtureHandle) -> None:
        """Undo a start that did not finish: no orphan process, no stale pidfile."""
        spec = handle.spec
        if spec.kind == "process":
            _stop_group(handle.pgid or handle.pid, 0.5, proc=handle.proc)
        elif handle.launched:
            # A step or service that started but never became ready is still running.
            try:
                if spec.kind == "step" and spec.stop is not None:
                    _run_fixture_step(spec.stop, self, scope_key=self._handle_scope(handle))
                elif spec.kind == "service":
                    from mas.lab.benchmark.service_manager import shared_manager

                    yaml_path = self.work_dir / "infra" / f"{spec.infra or 'services'}.yaml"
                    shared_manager(yaml_path, output_dir=self.output_dir, base_env=self.base_env).stop(
                        spec.service or spec.name
                    )
            except Exception:
                logger.warning("fixture %s: cleanup after a failed start did not complete", spec.name, exc_info=True)
        if handle.pidfile is not None:
            handle.pidfile.unlink(missing_ok=True)
        handle.pid = handle.pgid = None
        handle.proc = None
        handle.started = False
        handle.launched = False

    def _start_locked(self, handle: FixtureHandle) -> None:
        spec = handle.spec
        self._record("fixture.starting", spec.name, handle=handle)
        try:
            data_dir = self.pid_root() / _safe_scope(self._handle_scope(handle)) / spec.name
            data_dir.mkdir(parents=True, exist_ok=True)
            handle.data_dir = data_dir
            # A port taken between "free" and "bound" is rare but real with parallel runs:
            # an allocated port gets one retry on a new number when the process dies at once.
            allocated = False
            for attempt in (1, 2):
                if handle.port is None:
                    if spec.port == "auto" or (spec.port is None and spec.kind == "process"):
                        handle.port = allocate_loopback_port()
                        allocated = True
                    elif isinstance(spec.port, int):
                        handle.port = spec.port
                self._publish_vars(handle)
                try:
                    self._launch(handle)
                    break
                except _EarlyExit:
                    if not (allocated and attempt == 1):
                        raise
                    logger.warning(
                        "fixture %s exited at once on port %s; retrying on a new port", spec.name, handle.port
                    )
                    self._teardown(handle)
                    handle.port = None
            handle.started = True
            self._record("fixture.ready", spec.name, handle=handle, payload={"port": handle.port or 0})
        except Exception as exc:
            self._record("fixture.failed", spec.name, handle=handle, outcome="failed", payload={"status": 0})
            self._teardown(handle)
            raise RuntimeError(f"fixture {spec.name!r} failed to start: {exc}") from exc

    def _publish_vars(self, handle: FixtureHandle) -> None:
        spec = handle.spec
        with self._vars_lock:
            self.vars[f"fixture.{spec.name}.host"] = handle.host
            self.vars[f"fixtures.{spec.name}.host"] = handle.host
            if handle.port is not None:
                url = f"http://{handle.host}:{handle.port}"
                for prefix in ("fixture", "fixtures"):
                    self.vars[f"{prefix}.{spec.name}.port"] = str(handle.port)
                    self.vars[f"{prefix}.{spec.name}.url"] = url
            self.vars[f"fixture.{spec.name}.data_dir"] = str(handle.data_dir)

    def _launch(self, handle: FixtureHandle) -> None:
        spec = handle.spec
        if spec.kind == "process":
            self._start_process(handle)
        elif spec.kind == "service":
            self._start_service(handle)
        elif spec.kind == "step":
            self._start_step(handle)
        else:
            raise ValueError(f"fixture {spec.name!r}: kind {spec.kind} start is not implemented here")
        handle.launched = True
        self._wait_health(handle)

    def _scope_vars(self, handle: FixtureHandle) -> Variables:
        """Interpolate against this scope key, not the last writer on the registry."""
        with self._vars_lock:
            local = Variables(dict(self.vars))
        scope = self._handle_scope(handle)
        with self._lock:
            entries = list(self._handles.items())
        for (name, key), other in entries:
            if key != scope or other.port is None:
                continue
            url = f"http://{other.host}:{other.port}"
            local[f"fixture.{name}.host"] = other.host
            local[f"fixture.{name}.port"] = str(other.port)
            local[f"fixture.{name}.url"] = url
            local[f"fixtures.{name}.host"] = other.host
            local[f"fixtures.{name}.port"] = str(other.port)
            local[f"fixtures.{name}.url"] = url
        if handle.port is not None:
            local["port"] = str(handle.port)
            local["host"] = handle.host
        if handle.data_dir is not None:
            local["data_dir"] = str(handle.data_dir)
        return local

    def _start_process(self, handle: FixtureHandle) -> None:
        spec = handle.spec
        rendered = tuple(self._scope_vars(handle).interpolate(part) for part in spec.command)
        if not rendered:
            raise ValueError(f"fixture {spec.name!r}: process kind needs a command")
        handle.command = rendered
        digest = command_digest(rendered)
        env = _fixture_env(spec, self.base_env)
        if handle.port is not None:
            env["PORT"] = str(handle.port)
        handle.log_path = (handle.data_dir or self.pid_root()) / "fixture.log"
        with handle.log_path.open("ab") as log:
            proc = subprocess.Popen(
                list(rendered),
                cwd=str(self.work_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        handle.proc = proc
        handle.pid = proc.pid
        handle.pgid = proc.pid  # start_new_session makes the child its own group leader
        pidfile = self.pid_root() / _safe_scope(self._handle_scope(handle)) / f"{spec.name}.json"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": handle.pid,
            "pgid": handle.pgid,
            "started_at": time.time(),
            "command_sha256": digest,
            "command": list(rendered),
            "name": spec.name,
        }
        pidfile.write_text(json.dumps(payload), encoding="utf-8")
        handle.pidfile = pidfile

    def _start_service(self, handle: FixtureHandle) -> None:
        from mas.lab.benchmark.service_manager import shared_manager

        spec = handle.spec
        infra = spec.infra or "services"
        yaml_path = self.work_dir / "infra" / f"{infra}.yaml"
        mgr = shared_manager(yaml_path, output_dir=self.output_dir, base_env=self.base_env)
        ok = mgr.start(spec.service or spec.name)
        if not ok:
            raise RuntimeError(f"service fixture {spec.name!r} failed to start")

    def _start_step(self, handle: FixtureHandle) -> None:
        spec = handle.spec
        if spec.start is None or spec.stop is None:
            raise ValueError(f"fixture {spec.name!r}: kind step requires start and stop")
        _run_fixture_step(spec.start, self, scope_key=self._handle_scope(handle))

    def _emit_for(self, handle: FixtureHandle | None) -> EmitFn | None:
        if handle is None:
            return None
        scope = self._handle_scope(handle)
        with self._lock:
            return self._emit_by_scope.get(scope)

    def _handle_scope(self, handle: FixtureHandle) -> str:
        with self._lock:
            entries = list(self._handles.items())
        for (_name, scope_key), item in entries:
            if item is handle:
                return scope_key or handle.spec.scope
        return handle.spec.scope

    def _wait_health(self, handle: FixtureHandle) -> None:
        spec = handle.spec
        if handle.port is None:
            return
        health = spec.health
        if health is None:
            return  # no readiness probe declared: ready as soon as it was spawned
        url = f"http://{handle.host}:{handle.port}{health.path}"
        deadline = time.monotonic() + health.timeout_s
        last: Exception | None = None
        while time.monotonic() < deadline:
            self._raise_if_exited(handle)
            try:
                with loopback_urlopen(url, 2) as resp:
                    status = int(resp.status)
            except HTTPError as exc:
                status = int(exc.code)
            except (URLError, OSError) as exc:
                last = exc
                status = 0
            if status == health.expect_status:
                return
            time.sleep(0.05)
        raise TimeoutError(f"fixture {spec.name!r} health check timed out at {url}: {last}")

    def _raise_if_exited(self, handle: FixtureHandle) -> None:
        proc = handle.proc
        if proc is not None and proc.poll() is not None:
            tail = _log_tail(handle.log_path)
            raise _EarlyExit(f"process exited with code {proc.returncode} before it was ready{tail}")

    def push_clock(self, now_s: float, *, scope_keys: tuple[str, ...] | None = None) -> None:
        """Tell clock-bound fixtures the time; only this run's when ``scope_keys`` is given."""
        for (_name, scope_key), handle in list(self._handles.items()):
            if scope_keys is not None and scope_key not in scope_keys:
                continue
            if not handle.started or handle.spec.clock is None or handle.port is None:
                continue
            url = f"http://{handle.host}:{handle.port}{handle.spec.clock.path}"
            body = json.dumps({"now": now_s}).encode("utf-8")
            try:
                request = Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
                loopback_urlopen(request, 2).close()
            except (URLError, OSError) as exc:
                logger.debug("fixture clock push failed for %s: %s", handle.spec.name, exc)

    def release(self, name: str, *, force: bool = False, scope_key: str = "") -> None:
        handle = self._find(name, scope_key=scope_key)
        if handle is None:
            return
        with handle.lock:
            handle.refs = max(0, handle.refs - 1)
            if handle.refs > 0 and not force:
                return
            if handle.spec.keep_running and not force:
                return
            self._stop_handle(handle)

    def stop(self, name: str, *, scope_key: str = "") -> None:
        handle = self._find(name, scope_key=scope_key)
        if handle is None:
            return
        with handle.lock:
            self._stop_handle(handle)

    def has(self, name: str, *, scope_key: str) -> bool:
        """True when this exact fixture/scope pair is registered (started or not)."""
        with self._lock:
            return (name, scope_key) in self._handles

    def _find(self, name: str, *, scope_key: str = "") -> FixtureHandle | None:
        """The handle for ``name``, started or not (a stopped fixture can be restarted).

        With a ``scope_key`` the answer is exactly that pair: another run's fixture of the
        same name is never returned. Without one the name must be unambiguous.
        """
        with self._lock:
            if scope_key:
                return self._handles.get((name, scope_key))
            named = [handle for (item, _), handle in self._handles.items() if item == name]
        started = [handle for handle in named if handle.started]
        if len(started) == 1:
            return started[0]
        if len(named) == 1:
            return named[0]
        return None

    def _stop_handle(self, handle: FixtureHandle) -> None:
        if not handle.started:
            return
        name = handle.spec.name
        forced = False
        if handle.spec.kind == "process":
            forced = _stop_group(handle.pgid or handle.pid, handle.spec.stop_grace_s, proc=handle.proc)
        elif handle.spec.kind == "service":
            from mas.lab.benchmark.service_manager import shared_manager

            infra = handle.spec.infra or "services"
            yaml_path = self.work_dir / "infra" / f"{infra}.yaml"
            shared_manager(yaml_path, output_dir=self.output_dir, base_env=self.base_env).stop(
                handle.spec.service or name
            )
        elif handle.spec.kind == "step" and handle.spec.stop is not None:
            _run_fixture_step(handle.spec.stop, self, scope_key=self._handle_scope(handle))
        if handle.pidfile is not None:
            handle.pidfile.unlink(missing_ok=True)
        handle.started = False
        handle.pid = None  # pgid stays for inspection; a stopped handle is never signalled
        handle.proc = None
        self._record("fixture.stopped", name, handle=handle, payload={"forced": 1 if forced else 0})

    def restart(self, name: str, *, scope_key: str = "") -> FixtureHandle:
        """Stop and start again on the same address; holders keep their references."""
        handle = self._find(name, scope_key=scope_key)
        if handle is None:
            raise KeyError(f"unknown fixture {name!r}")
        with handle.lock:
            refs = handle.refs
            self._stop_handle(handle)
            try:
                self._start_locked(handle)
            except BaseException:
                self._teardown(handle)
                raise
            handle.refs = max(refs, 1)
            return handle

    def close_all(self, *, force: bool = False) -> None:
        for name, scope_key in list(self._handles):
            handle = self._handles.get((name, scope_key))
            if handle is not None and handle.spec.keep_running and not force:
                continue
            try:
                self.stop(name, scope_key=scope_key)
            except Exception:
                logger.debug("fixture close failed for %s", name, exc_info=True)

    def _record(
        self,
        name: str,
        fixture: str,
        *,
        handle: FixtureHandle | None = None,
        outcome: str = "ok",
        payload: dict[str, Any] | None = None,
    ) -> None:
        emit = self._emit_for(handle) or self.emit
        if emit is None:
            return
        body = {"fixture": fixture}
        if payload:
            for key, value in payload.items():
                if isinstance(value, bool):
                    body[key] = int(value)
                elif isinstance(value, (int, float)):
                    body[key] = value
                elif value is None:
                    continue
                else:
                    body[key] = str(value)
        try:
            emit(
                name,
                source={"type": "fixture", "id": fixture, "fixture": fixture},
                payload=body,
                outcome=outcome,
            )
        except RuntimeError as exc:
            if "closed" not in str(exc):
                raise
            return


class _ScopeBound:
    """url/stop/restart on one scope key; never the unique-instance fallback."""

    def __init__(self, registry: FixtureRegistry, scope_key: str) -> None:
        self._registry = registry
        self._scope_key = scope_key

    def url(self, name: str) -> str:
        return self._registry.url(name, scope_key=self._scope_key)

    def port(self, name: str) -> int:
        return self._registry.port(name, scope_key=self._scope_key)

    def host(self, name: str) -> str:
        return self._registry.host(name, scope_key=self._scope_key)

    def restart(self, name: str) -> Any:
        return self._registry.restart(name, scope_key=self._scope_key)

    def stop(self, name: str) -> None:
        self._registry.release(name, force=False, scope_key=self._scope_key)


def _run_fixture_step(spec: Any, registry: FixtureRegistry, *, scope_key: str = "") -> None:
    """Run a kind-step fixture start/stop action on this registry."""
    import asyncio

    from mas.lab.benchmark.environment.clock import RunClock
    from mas.lab.benchmark.environment.events import EventLog
    from mas.lab.benchmark.environment.model import Limits, RunCoordinates
    from mas.lab.benchmark.environment.step import EnvironmentContext, instantiate_step

    step = instantiate_step(spec)
    ctx = EnvironmentContext(
        run=RunCoordinates(),
        clock=RunClock(),
        fixtures=_ScopeBound(registry, scope_key) if scope_key else registry,
        vars=registry.vars,
        events=EventLog(clock=RunClock(), run=RunCoordinates(), limits=Limits()),
        limits=Limits(),
        work_dir=registry.work_dir,
    )

    error: list[BaseException] = []
    handle: dict[str, Any] = {}

    def worker() -> None:
        async def main() -> None:
            handle["loop"] = asyncio.get_running_loop()
            handle["task"] = asyncio.current_task()
            await step.act(ctx)

        try:
            asyncio.run(main())
        except BaseException as exc:
            error.append(exc)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        worker()
        if error:
            raise error[0] from None
        return

    from mas.runtime.aio import new_thread

    thread = new_thread(worker, name="fixture-step")
    thread.start()
    thread.join(timeout=30.0)
    if thread.is_alive():
        # Cancel the step in its own loop, so the thread ends instead of running on unseen.
        loop, task = handle.get("loop"), handle.get("task")
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
            thread.join(timeout=2.0)
        raise TimeoutError(f"fixture step {getattr(spec, 'type', spec)!r} timed out")
    if error:
        raise error[0] from None


def _stop_group(pgid: int | None, grace_s: float, *, proc: subprocess.Popen[bytes] | None = None) -> bool:
    """SIGTERM the group, wait ``grace_s``, then SIGKILL. True when it had to be forced."""
    if pgid is None or pgid <= 1 or pgid == os.getpgrp():
        return False  # never signal our own group, init, or "every process"
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    deadline = time.monotonic() + max(0.05, grace_s)
    while time.monotonic() < deadline:
        if proc is not None:
            proc.poll()  # reap our child so the group can disappear
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            break
        time.sleep(0.02)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    if proc is not None:
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    return True


def _log_tail(path: Path | None, limit: int = 400) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        text = path.read_bytes()[-limit:].decode("utf-8", "replace").strip()
    except OSError:
        return ""
    return f": {text}" if text else ""


def reap_stale_fixtures(output_dir: Path) -> list[str]:
    """Kill leftover fixture groups whose command digest still matches.

    Pidfiles live at ``<output>/.environment/fixtures/<scope>/<name>.json``. JSON under a
    fixture data directory is not a pidfile.
    """
    root = Path(output_dir) / ".environment" / "fixtures"
    if not root.is_dir():
        return []
    reaped: list[str] = []
    for pidfile in root.glob("*/*.json"):
        if not pidfile.is_file():
            continue
        try:
            data = json.loads(pidfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pidfile.unlink(missing_ok=True)
            continue
        pid = int(data.get("pid") or 0)
        pgid = int(data.get("pgid") or pid)
        digest = str(data.get("command_sha256") or "")
        command = tuple(str(part) for part in (data.get("command") or ()))
        name = str(data.get("name") or pidfile.stem)
        if pid <= 0 or digest != command_digest(command):
            pidfile.unlink(missing_ok=True)
            continue
        alive = _pid_alive(pid)
        if not alive:
            pidfile.unlink(missing_ok=True)
            continue
        current = _command_of(pid)
        if not current:
            continue
        if not _same_command(current, command):
            pidfile.unlink(missing_ok=True)
            continue
        _stop_group(pgid, 2.0)
        pidfile.unlink(missing_ok=True)
        reaped.append(name)
        logger.warning("reaped leftover fixture %s (pid %s)", name, pid)
    return reaped


def _same_command(current: list[str], recorded: tuple[str, ...]) -> bool:
    """Is the live process the one we recorded?

    ``ps`` shows the resolved program (``python3`` becomes the interpreter path) and may split
    an argument that contains spaces, so argv[0] is ignored when there are arguments and the
    remaining tokens are compared as a joined string (equality, not a suffix). A
    single-argument command matches by basename, so ``sha256sum`` is not ``sh``.
    """
    if not recorded or not current:
        return False
    if len(recorded) == 1:
        return Path(current[0]).name == Path(recorded[0]).name
    return " ".join(current[1:]) == " ".join(recorded[1:])


def _decode_cmdline(raw: bytes) -> list[str]:
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _command_of(pid: int) -> list[str]:
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.is_file():
        return _decode_cmdline(cmdline.read_bytes())
    try:
        import subprocess as sp

        result = sp.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split()
    except OSError:
        return []
    return []
