#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
"""ServiceManager — declarative management of Docker Compose services for mas-lab.

Reads ``infra/services.yaml`` (co-located with the experiment) and manages the
lifecycle of declared services:

  * ``start(name)``   — ``docker compose up -d --wait``, sets env vars
  * ``stop(name)``    — ``docker compose down``, clears env vars
  * ``status(name)``  — running/stopped + health check

Auto-start is integrated into ``mas-lab benchmark run``: when the active flavour
declares ``observability.backend: otel_extended`` and the ``otel-collector`` service
has that backend in its ``triggers.observability_backends``, the service is started
before the run loop and stopped after.

Usage as context manager::

    from mas.lab.benchmark.service_manager import ServiceManager

    mgr = ServiceManager.for_benchmark(
        flavour=_flavour,
        experiment_dir=experiment_yaml.parent,
        output_dir=output_dir,
    )
    with mgr:
        # services started, env vars set
        ...
    # services stopped, env vars restored

Manual CLI usage::

    mas-lab services start  --experiment path/to/mas-experiment.yaml
    mas-lab services stop   --experiment path/to/mas-experiment.yaml
    mas-lab services status --experiment path/to/mas-experiment.yaml
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.error import URLError

from mas.lab.benchmark.environment.declared_env import compile_env, plain_env
from mas.runtime import hostenv
from mas.runtime.secrets import SecretRef, child_env, load_checked_document
from mas.runtime.secrets.resolver import reveal_mapping

logger = logging.getLogger(__name__)

_COMPOSE_PROJECT_PREFIX = "mas-lab-svc"

# name -> (value, holders, value before the first export); one process may run
# several benchmarks (controller daemon), so exports are shared and refcounted.
_EXPORTED: dict[str, tuple[str, int]] = {}
_EXPORTED_LOCK = threading.Lock()
_MANAGERS: dict[str, "ServiceManager"] = {}
_MANAGERS_LOCK = threading.Lock()
_KEEP_RUNNING: set[tuple[str, str]] = set()


def shared_manager(
    services_yaml: Path,
    *,
    output_dir: Path | None = None,
    base_env: Mapping[str, str] | None = None,
) -> "ServiceManager":
    """One ServiceManager per bundle path so start and stop share PIDs."""
    key = str(Path(services_yaml).resolve())
    with _MANAGERS_LOCK:
        mgr = _MANAGERS.get(key)
        if mgr is None:
            template = {"output_dir": str(output_dir)} if output_dir is not None else None
            mgr = ServiceManager(services_yaml, template_vars=template)
            if output_dir is not None:
                mgr._template_vars.setdefault("output_dir", str(output_dir))
            _MANAGERS[key] = mgr
        if base_env is not None:
            mgr.base_env = base_env  # what a process service may inherit; the CLI supplies it
        if output_dir is not None:
            # The newest batch owns new pidfiles; a running service keeps the one it wrote.
            mgr._template_vars["output_dir"] = str(output_dir)
        return mgr


def _bundle_key(services_yaml: Path | str) -> str:
    return str(Path(services_yaml).resolve())


def mark_keep_running(name: str, services_yaml: Path | str = "") -> None:
    """Keep *name* of this services bundle running past the end of its level."""
    _KEEP_RUNNING.add((_bundle_key(services_yaml) if services_yaml else "", name))


def is_keep_running(name: str, services_yaml: Path | str = "") -> bool:
    return (_bundle_key(services_yaml) if services_yaml else "", name) in _KEEP_RUNNING


def stop_all_shared(*, include_keep_running: bool = False) -> None:
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
    for mgr in managers:
        for name in list(mgr._started):
            if not include_keep_running and is_keep_running(name, mgr._services_yaml):
                continue
            mgr.stop(name)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HealthCheckSpec:
    url: str = ""
    timeout: int = 15


@dataclass
class TriggersSpec:
    observability_backends: list[str] = field(default_factory=list)


@dataclass
class ServiceDef:
    name: str
    description: str = ""
    backend: str = "docker-compose"
    compose_file: str = ""  # relative to services.yaml
    command: list[str] = field(default_factory=list)  # backend=process only
    auto_start: bool = True
    env: dict[str, str | SecretRef] = field(default_factory=dict)  # a SecretRef reaches this service's child only
    health_check: HealthCheckSpec = field(default_factory=HealthCheckSpec)
    triggers: TriggersSpec = field(default_factory=TriggersSpec)

    # runtime-resolved
    compose_file_abs: Path = field(default_factory=Path, init=False, repr=False)
    _prev_env: dict[str, Optional[str]] = field(default_factory=dict, init=False, repr=False)


# ---------------------------------------------------------------------------
# ServiceManager
# ---------------------------------------------------------------------------


class ServiceManager:
    """Manages a set of services declared in ``infra/services.yaml``.

    When a service has ``backend: catalogue`` (or omits ``compose_file`` and its
    name matches an entry in the built-in ``mas-lab-services`` catalogue), the
    manager delegates lifecycle calls to :class:`mas.lab.services.CatalogueRegistry`.
    ``mas-lab-services`` is an optional dependency; catalogue services are skipped
    gracefully when it is not installed.

    Args:
        services_yaml:  Path to the services YAML file.
        template_vars:  Dict used to resolve ``{key}`` placeholders in env values.
                        Typically ``{"output_dir": str(output_dir)}``.
    """

    def __init__(
        self,
        services_yaml: Path,
        template_vars: dict[str, str] | None = None,
    ) -> None:
        self._services_yaml = services_yaml
        self._template_vars = template_vars or {}
        self._services: dict[str, ServiceDef] = {}
        self._started: set[str] = set()
        self._auto_start_names: set[str] = set()  # populated by for_benchmark
        self._catalogue: "Any | None" = None  # lazy CatalogueRegistry
        self._pids: dict[str, int] = {}  # backend=process: service_name → PID
        self.base_env: Mapping[str, str] | None = None  # None: the process environment, as before
        self._pid_roots: dict[str, Path] = {}  # where each service's pidfile was written

        if services_yaml.exists():
            self._load(services_yaml)
        else:
            logger.debug("services.yaml not found: %s — ServiceManager is a no-op", services_yaml)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ServiceManager":
        for name in self._auto_start_names:
            self.start(name)
        return self

    def __exit__(self, *_exc) -> None:
        self.stop_all()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_benchmark(
        cls,
        flavour,
        experiment_dir: Path,
        output_dir: Path,
    ) -> "ServiceManager":
        """Create a ServiceManager pre-filtered to auto-start services that
        match the flavour's observability backend.

        When the flavour is None or the services YAML is absent the returned
        instance is a no-op context manager.
        """
        services_yaml = experiment_dir / "infra" / "services.yaml"
        mgr = cls(
            services_yaml=services_yaml,
            template_vars={"output_dir": str(output_dir)},
        )
        # Mark which services should auto-start given the flavour
        if flavour is not None:
            try:
                # FlavourManifest exposes observability directly, not via .spec
                obs_backend = flavour.observability.backend
            except AttributeError:
                obs_backend = ""
            for svc in mgr._services.values():
                if svc.auto_start and obs_backend in svc.triggers.observability_backends:
                    mgr._auto_start_names.add(svc.name)
                    logger.info("Service %r queued for auto-start (flavour backend=%r)", svc.name, obs_backend)
        return mgr

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, name: str) -> bool:
        """Start a named service.  Returns True on success.

        When no service is declared locally but the name matches a built-in
        catalogue entry, delegates to :class:`CatalogueRegistry` automatically.
        """
        svc = self._services.get(name)
        if svc is None:
            # Catalogue fallback: try the built-in catalogue for unknown names.
            return self._start_catalogue_fallback(name)
        if svc.backend == "catalogue":
            return self._catalogue_start(svc)
        if svc.backend == "process":
            return self._process_start(svc)
        if self._is_running(svc):
            logger.info("Service %r already running — skipping start", name)
            self._apply_env(svc)  # still set env vars
            self._started.add(name)
            return True
        return self._compose_up(svc)

    def stop(self, name: str) -> bool:
        """Stop a named service.  Returns True on success."""
        svc = self._services.get(name)
        if svc is None:
            logger.warning("Unknown service: %r", name)
            return False
        if svc.backend == "catalogue":
            return self._catalogue_stop(svc)
        if svc.backend == "process":
            return self._process_stop(svc)
        return self._compose_down(svc)

    def start_all(self) -> list[str]:
        """Start all declared services.  Returns list of started names."""
        started = []
        for name in self._services:
            if self.start(name):
                started.append(name)
        return started

    def stop_all(self) -> None:
        """Stop all services that were started by this manager."""
        for name in list(self._started):
            svc = self._services.get(name)
            if svc is not None:
                self.stop(name)
            else:
                # Started via catalogue fallback — not in _services dict.
                cat = self._get_catalogue()
                if cat is not None:
                    cat.stop(name)
                self._started.discard(name)

    def status(self, name: str | None = None) -> dict[str, dict[str, Any]]:
        """Return status dict for one named service or all services.

        Keys: ``running`` (bool), ``healthy`` (bool | None), ``container``.
        Includes services started via catalogue fallback even if not in services.yaml.
        """
        # Merge declared services + implicitly started catalogue services
        known = dict.fromkeys(self._services.keys())
        for n in self._started:
            known.setdefault(n, None)

        targets = [name] if name else list(known.keys())
        result: dict[str, dict[str, Any]] = {}
        for n in targets:
            svc = self._services.get(n)
            if svc is None:
                # Catalogue fallback service — delegate entirely to catalogue
                cat = self._get_catalogue()
                if cat is not None and n in cat.available():
                    result[n] = cat.status(n)
                else:
                    result[n] = {"running": False, "healthy": None, "container": None, "error": "unknown"}
                continue
            running = self._is_running(svc)
            healthy: bool | None = None
            if running and (svc.health_check.url or svc.backend == "catalogue"):
                healthy = self._health_check(svc)
            result[n] = {
                "running": running,
                "healthy": healthy,
                "container": self._container_name(svc),
            }
        return result

    @property
    def service_names(self) -> list[str]:
        return list(self._services.keys())

    # ------------------------------------------------------------------
    # Internal: load
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        data = load_checked_document(path)  # SecretRefs stay mappings: compile_env hydrates the env
        spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}
        services_raw = spec.get("services") or data.get("services") or {}
        base_dir = path.parent

        for name, spec in services_raw.items():
            spec = spec or {}
            hc_raw = spec.get("health_check", {}) or {}
            tr_raw = spec.get("triggers", {}) or {}
            env_raw = compile_env(spec.get("env"), where=f"services.{name}")

            backend = spec.get("backend", "docker-compose")
            compose_file = spec.get("compose_file", "")
            command_raw = spec.get("command", [])
            if isinstance(command_raw, str):
                import shlex

                command_raw = shlex.split(command_raw)

            # Auto-promote to catalogue backend: when no compose_file is given
            # and the service name exists in the built-in catalogue.
            if backend == "docker-compose" and not compose_file:
                cat = self._get_catalogue()
                if cat is not None and name in cat.available():
                    backend = "catalogue"
                    logger.debug("Service %r: no compose_file — promoting to backend=catalogue", name)

            svc = ServiceDef(
                name=name,
                description=spec.get("description", ""),
                backend=backend,
                compose_file=compose_file,
                command=command_raw,
                auto_start=spec.get("auto_start", True),
                env=env_raw,
                health_check=HealthCheckSpec(
                    url=hc_raw.get("url", ""),
                    timeout=int(hc_raw.get("timeout", 15)),
                ),
                triggers=TriggersSpec(
                    observability_backends=tr_raw.get("observability_backends", []),
                ),
            )
            if svc.compose_file:
                svc.compose_file_abs = base_dir / svc.compose_file
            else:
                svc.compose_file_abs = Path()
            self._services[name] = svc

        logger.debug("ServiceManager loaded %d service(s) from %s", len(self._services), path)

    # ------------------------------------------------------------------
    # Internal: docker compose lifecycle
    # ------------------------------------------------------------------

    def _compose_project(self, svc: ServiceDef) -> str:
        return f"{_COMPOSE_PROJECT_PREFIX}-{svc.name}"

    def _container_name(self, svc: ServiceDef) -> str:
        return f"{_COMPOSE_PROJECT_PREFIX}-{svc.name}"

    def _preflight(self, svc: ServiceDef) -> bool:
        if not svc.compose_file_abs.exists():
            logger.warning("Compose file not found for service %r: %s", svc.name, svc.compose_file_abs)
            return False
        result = subprocess.run(["docker", "info"], capture_output=True, env=child_env())
        if result.returncode != 0:
            logger.warning(
                "Docker not available (exit %d) — service %r will not start",
                result.returncode,
                svc.name,
            )
            return False
        return True

    def _resolve_env(self, svc: ServiceDef) -> dict[str, str | SecretRef]:
        """Substitute {template_vars} in plain env values. SecretRefs stay refs."""
        resolved: dict[str, str | SecretRef] = {}
        for k, v in svc.env.items():
            if isinstance(v, SecretRef):
                resolved[k] = v
                continue
            try:
                resolved[k] = v.format(**self._template_vars)
            except KeyError as exc:
                raise ValueError(
                    f"service {svc.name!r}: env {k}={v!r} uses unknown placeholder {exc}; "
                    f"available: {sorted(self._template_vars)}"
                ) from exc
        return resolved

    def _compose_env(self, svc: ServiceDef) -> dict[str, str]:
        """The env of one ``docker compose`` child of this service: the allowlist, its plain env, its secrets.

        ``up``, ``down`` and ``ps`` all get it: a compose file that interpolates ``${VAR:?}`` fails to
        parse without the value, and a ``down`` that fails leaves the containers running. The revealed
        values live in that child's environment only, never in a log line or the command's arguments.
        """
        return child_env(reveal_mapping(self._resolve_env(svc)))

    def _apply_env(self, svc: ServiceDef) -> None:
        """Export the plain env values to this process (shared by concurrent benchmarks).

        A SecretRef value is never exported: only the service's own child receives it. The whole
        map is checked before anything is exported, so a refused map changes nothing.
        """
        if svc._prev_env:
            return
        resolved = plain_env(self._resolve_env(svc))  # compile_env refused credential names and URLs
        with _EXPORTED_LOCK:
            for k, v in resolved.items():
                current = _EXPORTED.get(k)
                if current is not None and current[0] != v:
                    raise RuntimeError(
                        f"service {svc.name!r}: env {k} conflicts with the value "
                        "exported by a service of another running benchmark"
                    )
            hostenv.set_overlay({k: v for k, v in resolved.items() if k not in _EXPORTED})
            for k, v in resolved.items():
                if k in _EXPORTED:
                    value, count = _EXPORTED[k]
                    _EXPORTED[k] = (value, count + 1)
                else:
                    _EXPORTED[k] = (v, 1)
                svc._prev_env[k] = None
                logger.debug("service %r exports %s", svc.name, k)

    def _restore_env(self, svc: ServiceDef) -> None:
        """Release this service's exports; the last holder restores the previous value."""
        with _EXPORTED_LOCK:
            for k in svc._prev_env:
                value, count = _EXPORTED[k]
                if count > 1:
                    _EXPORTED[k] = (value, count - 1)
                    continue
                del _EXPORTED[k]
                hostenv.clear_overlay([k])
        svc._prev_env.clear()

    def _compose_up(self, svc: ServiceDef) -> bool:
        if not self._preflight(svc):
            return False

        resolved_env = plain_env(self._resolve_env(svc))
        # Create any host directories referenced by ``_DIR``-suffixed env vars
        # (used as bind-mount sources in docker compose volume declarations).
        for _k, _v in resolved_env.items():
            if _k.endswith("_DIR") and _v:
                try:
                    Path(_v).mkdir(parents=True, exist_ok=True)
                except Exception:
                    logger.debug("suppressed", exc_info=True)
        compose_env = self._compose_env(svc)
        logger.info("Starting service %r …", svc.name)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self._compose_project(svc),
                "-f",
                str(svc.compose_file_abs),
                "up",
                "-d",
            ],
            env=compose_env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "docker compose up for %r failed (exit %d):\n%s",
                svc.name,
                result.returncode,
                result.stderr[:600],
            )
            return False

        self._apply_env(svc)
        self._started.add(svc.name)
        logger.info("Service %r started → env vars set", svc.name)
        return True

    def _compose_down(self, svc: ServiceDef) -> bool:
        if not svc.compose_file_abs.exists():
            return True
        logger.info("Stopping service %r …", svc.name)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self._compose_project(svc),
                "-f",
                str(svc.compose_file_abs),
                "down",
            ],
            env=self._compose_env(svc),
            capture_output=True,
        )
        self._restore_env(svc)
        self._started.discard(svc.name)
        if result.returncode != 0:
            logger.warning("docker compose down for %r failed (exit %d)", svc.name, result.returncode)
            return False
        logger.info("Service %r stopped", svc.name)
        return True

    def _is_running(self, svc: ServiceDef) -> bool:
        """Return True if the service is running."""
        if svc.backend == "catalogue":
            cat = self._get_catalogue()
            if cat is None:
                return False
            return cat.status(svc.name).get("running", False)
        if svc.backend == "process":
            return self._process_is_running(svc)
        if not svc.compose_file_abs.exists():
            return False
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self._compose_project(svc),
                "-f",
                str(svc.compose_file_abs),
                "ps",
                "--services",
                "--filter",
                "status=running",
            ],
            env=self._compose_env(svc),
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())

    def _health_check(self, svc: ServiceDef) -> bool:
        """True if the HTTP health endpoint returns 2xx."""
        if svc.backend == "catalogue":
            cat = self._get_catalogue()
            return cat.status(svc.name).get("healthy", False) if cat else False
        url = svc.health_check.url
        if not url:
            return True
        try:
            from mas.runtime.secrets.transport import opener_for

            resp = opener_for(url).open(url, timeout=2)
            return 200 <= resp.status < 300
        except (URLError, OSError):
            return False

    # ------------------------------------------------------------------
    # Internal: process backend (kubectl port-forward, arbitrary commands)
    # ------------------------------------------------------------------

    def _process_start(self, svc: ServiceDef) -> bool:
        """Start *svc* as a background subprocess.

        The command is taken from ``svc.command``.  The child PID is stored in
        ``self._pids[svc.name]`` so it can be terminated on stop.
        If the service is already running (PID still alive), env vars are
        re-applied and the call returns True immediately.
        """
        if not svc.command:
            logger.warning("Service %r: backend=process but no 'command' defined — skipping", svc.name)
            return False

        if self._process_is_running(svc):
            logger.info("Service %r already running (pid=%d) — skipping start", svc.name, self._pids.get(svc.name, -1))
            self._apply_env(svc)
            self._started.add(svc.name)
            return True

        with _EXPORTED_LOCK:
            exported = {k: v[0] for k, v in _EXPORTED.items()}
        proc_env = child_env({**exported, **reveal_mapping(self._resolve_env(svc))})

        logger.info("Starting process service %r: %s", svc.name, " ".join(svc.command))
        try:
            proc = subprocess.Popen(
                svc.command,
                env=proc_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            logger.warning(
                "Service %r: command not found (%s) — is %r installed?",
                svc.name,
                exc,
                svc.command[0],
            )
            return False

        self._pids[svc.name] = proc.pid
        self._write_pidfile(svc, proc.pid)
        self._apply_env(svc)
        self._started.add(svc.name)
        logger.info("Service %r started as PID %d", svc.name, proc.pid)
        return True

    def _pidfile(self, svc: ServiceDef) -> Path:
        known = self._pid_roots.get(svc.name)
        if known is None:
            known = Path(self._template_vars.get("output_dir") or self._services_yaml.parent) / ".services"
        known.mkdir(parents=True, exist_ok=True)
        return known / f"{svc.name}.pid"

    def _write_pidfile(self, svc: ServiceDef, pid: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            pgid = pid
        self._pid_roots[svc.name] = self._pidfile(svc).parent
        # The command is recorded so a later process can tell this group from a recycled pid.
        self._pidfile(svc).write_text(f"{pid} {pgid} {json.dumps(list(svc.command))}\n", encoding="utf-8")

    def _read_pidfile(self, svc: ServiceDef) -> tuple[int, int, list[str]] | None:
        path = self._pidfile(svc)
        if not path.is_file():
            return None
        parts = path.read_text(encoding="utf-8").split(maxsplit=2)
        try:
            pid = int(parts[0])
            pgid = int(parts[1]) if len(parts) > 1 else pid
            command = json.loads(parts[2]) if len(parts) > 2 else []
        except (IndexError, ValueError):
            path.unlink(missing_ok=True)  # a corrupt record is not a reason to signal anything
            return None
        return pid, pgid, [str(part) for part in command]

    @staticmethod
    def _is_our_leftover(pid: int, command: list[str]) -> bool:
        """A pid from an earlier process is ours only while it still runs the recorded command."""
        from mas.lab.benchmark.environment.fixtures import _command_of, _pid_alive, _same_command

        return bool(command) and _pid_alive(pid) and _same_command(_command_of(pid), tuple(command))

    def _process_stop(self, svc: ServiceDef) -> bool:
        """Terminate the background process group for *svc*."""
        import signal
        import time

        pid = self._pids.pop(svc.name, None)
        pgid = pid
        recorded = self._read_pidfile(svc)
        if pid is None and recorded is not None:
            pid, pgid, command = recorded
            if not self._is_our_leftover(pid, command):
                logger.warning("Service %r: pid %d is not this service any more — not signalled", svc.name, pid)
                self._pidfile(svc).unlink(missing_ok=True)
                pid = None
        elif recorded is not None:
            pgid = recorded[1]
        self._restore_env(svc)
        self._started.discard(svc.name)
        if pid is None:
            logger.debug("Service %r: no PID recorded — nothing to stop", svc.name)
            return True
        if (pgid or pid) <= 1 or (pgid or pid) == os.getpgrp():
            logger.warning("Service %r: refusing to signal process group %s", svc.name, pgid or pid)
            return False
        try:
            os.killpg(pgid or pid, signal.SIGTERM)
            logger.info("Service %r (PID %d) sent SIGTERM to process group", svc.name, pid)
        except ProcessLookupError:
            logger.debug("Service %r PID %d already gone", svc.name, pid)
        except PermissionError as exc:
            logger.warning("Service %r: cannot terminate PID %d: %s", svc.name, pid, exc)
            return False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
            try:
                os.killpg(pgid or pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(pgid or pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        pidfile = self._pidfile(svc)
        if pidfile.exists():
            pidfile.unlink()
        self._pid_roots.pop(svc.name, None)
        return True

    def _process_is_running(self, svc: ServiceDef) -> bool:
        """Return True if the recorded PID for *svc* is still alive."""
        pid = self._pids.get(svc.name)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)  # signal 0 = existence check, no actual signal
            return True
        except ProcessLookupError:
            self._pids.pop(svc.name, None)
            return False
        except PermissionError:
            return True  # process exists but we can't signal it

    # ------------------------------------------------------------------
    # Internal: catalogue delegation
    # ------------------------------------------------------------------

    def _get_catalogue(self) -> "Any | None":
        """Lazily return a CatalogueRegistry, or None if mas-lab-services is absent."""
        if self._catalogue is not None:
            return self._catalogue
        try:
            from mas.lab.services import CatalogueRegistry  # optional dep

            self._catalogue = CatalogueRegistry()
        except ImportError:
            logger.debug("mas-lab-services not installed — catalogue backend unavailable")
            self._catalogue = False  # sentinel: don't retry
        return self._catalogue if self._catalogue is not False else None

    def _catalogue_start(self, svc: ServiceDef) -> bool:
        """Start a catalogue service via CatalogueRegistry."""
        cat = self._get_catalogue()
        if cat is None:
            logger.warning(
                "Cannot start catalogue service %r: mas-lab-services not installed. "
                "Run: uv pip install -e mas-lab-services",
                svc.name,
            )
            return False
        ok = cat.start(svc.name)
        if ok:
            # Apply any local env overrides declared on top of catalogue defaults.
            if svc.env:
                self._apply_env(svc)
            self._started.add(svc.name)
        return ok

    def _catalogue_stop(self, svc: ServiceDef) -> bool:
        """Stop a catalogue service via CatalogueRegistry."""
        cat = self._get_catalogue()
        if cat is None:
            return True  # nothing to stop
        ok = cat.stop(svc.name)
        if svc.env:
            self._restore_env(svc)
        self._started.discard(svc.name)
        return ok

    def _start_catalogue_fallback(self, name: str) -> bool:
        """Fallback: start a catalogue service that is not declared in services.yaml.

        Allows ``mas-lab services start neo4j`` without any local services.yaml
        entry, as long as ``neo4j`` exists in the built-in catalogue.
        """
        cat = self._get_catalogue()
        if cat is None or name not in cat.available():
            logger.warning(
                "Unknown service %r — not in local services.yaml and not in catalogue (available in catalogue: %s)",
                name,
                cat.available() if cat else [],
            )
            return False
        logger.info("Service %r not in services.yaml — using built-in catalogue", name)
        ok = cat.start(name)
        if ok:
            self._started.add(name)
        return ok
