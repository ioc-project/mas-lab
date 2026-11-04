#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra manager for the MAS demo controller — local flavour.

All communication between mas-lab and mas-controller is via direct library
calls (no HTTP). Daemon threads are started only for external protocol needs.

Local flavour service policy
----------------------------
Started as daemon threads only when required: none in local flavour.

NOT started (handled in-process):
- metrics     : computed directly from the JSONL feed via compute_metrics()
- otlp_local  : agents emit telemetry via in-process callbacks

Infrastructure backends (process/docker/k8s) are provided by
``mas.runtime.plugins.infra_backend_plugin``.  This module uses
``ProcessBackend`` for the demo controller's local services and keeps a
thin ``InfraManager`` facade used by the controller daemon.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Callable, Dict, List, Optional

# Re-export for external consumers (flavour selection, etc.)
from mas.library.standard.plugins.infra_backend_plugin import (  # noqa: F401
    DockerBackend,
    InfraBackendPlugin,
    K8sBackend,
    ProcessBackend,
    ServiceSpec,
    create_backend,
    register_backend,
)

# ---------------------------------------------------------------------------
# Service registry — maps service name → Python entry point
# ---------------------------------------------------------------------------

_SERVICE: Dict[str, str] = {
    "metrics":    "mas.lab.components.metrics.server:main",
}

_SERVICE_PORT: Dict[str, int] = {
    "metrics":    8090,
}


def _import_fn(dotted: str) -> Optional[Callable[[], None]]:
    """Import ``module:fn`` notation and return the callable, or None."""
    module_path, fn_name = dotted.rsplit(":", 1)
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, fn_name)
    except (ImportError, AttributeError):
        return None


def _clear_ui_feed(feed_path_str: str) -> None:
    path = Path(feed_path_str).expanduser()
    if path.suffix == ".jsonl" and path.exists():
        path.write_text("", encoding="utf-8")


def services_for_config(
    config: Dict[str, Any],
    config_dir: Optional[Path] = None,
) -> list[str]:
    """Return the list of service names required by a MAS config dict.

    Local flavour does not start extra HTTP services: metrics and OTLP
    are in-process. Offline LLM uses llm_cache replay, not a local fake server.
    """
    return []


# ---------------------------------------------------------------------------
# InfraManager — thin facade over ProcessBackend for the demo controller
# ---------------------------------------------------------------------------

class InfraManager:
    """Start and manage infra services via ProcessBackend.

    Usage::

        mgr = InfraManager()
        mgr.start(config)         # start services derived from MAS config
        mgr.run(config, config_dir, scenario)  # start MAS runtime in a thread
        ...
        mgr.stop()                # called on Ctrl-C

    Services are started as OS subprocesses via ProcessBackend. They are
    torn down when stop() is called or when the main process exits.
    """

    def __init__(self, backend: Optional[InfraBackendPlugin] = None) -> None:
        self._backend: InfraBackendPlugin = backend or ProcessBackend()
        self._run_lock = Lock()
        self._run_thread: Optional[Thread] = None
        self._service_names: List[str] = []

    # -- service lifecycle --------------------------------------------------

    def _launch(self, name: str) -> bool:
        dotted = _SERVICE.get(name)
        if not dotted:
            return False
        fn = _import_fn(dotted)
        if fn is None:
            print(f"[infra] warning: service '{name}' module not importable — skipped")
            return False

        # Build a ServiceSpec from the registry entry.  For thread-based
        # services we wrap the entry point in a "python -c" command so
        # ProcessBackend can manage it as a subprocess.
        module_path, fn_name = dotted.rsplit(":", 1)
        spec = ServiceSpec(
            name=name,
            command=["python", "-c", f"import {module_path}; {module_path}.{fn_name}()"],
            port=_SERVICE_PORT.get(name, 0),
            health_endpoint="/healthz" if _SERVICE_PORT.get(name) else "",
        )
        try:
            self._backend.start(spec)
            if name not in self._service_names:
                self._service_names.append(name)
            return True
        except Exception as exc:
            print(f"[infra] {name}: failed to start: {exc}")
            return False

    def start(self, config: Dict[str, Any]) -> None:
        """Start services required by *config*."""
        for name in services_for_config(config):
            if not self._backend.health(name):
                self._launch(name)

    def ensure(self, config: Dict[str, Any]) -> None:
        """Re-start any services that have died."""
        for name in list(self._service_names):
            if not self._backend.health(name):
                self._launch(name)

    def stop(self) -> None:
        """Stop all managed services."""
        self._backend.stop_all()
        self._service_names.clear()

    def service_names(self) -> list[str]:
        return list(self._service_names)

    def is_service_alive(self, name: str) -> bool:
        return self._backend.health(name)

    # -- MAS run lifecycle --------------------------------------------------

    def run(self, config: Dict[str, Any], config_dir: Path, scenario: str) -> None:
        """Run the MAS in autonomous mode inside a daemon thread."""
        from mas.ctl.executor.run_mas import execute_run_mas
        from mas.lab.lab.config import load_scenario_config

        try:
            _, base_path = load_scenario_config(config_dir, scenario)
        except Exception:
            base_path = config_dir / f"{scenario}.json"

        mas_yaml = base_path.parent / "mas.yaml"
        if not mas_yaml.is_file():
            mas_yaml = base_path if base_path.suffix in (".yaml", ".yml") else mas_yaml

        overlay_paths = []
        if base_path.is_file() and base_path != mas_yaml:
            overlay_paths = [base_path]

        initial_prompt = os.getenv("MAS_PROMPT", "").strip() or None

        def _target() -> None:
            try:
                execute_run_mas(
                    mas_yaml,
                    prompt=initial_prompt,
                    overlay_paths=overlay_paths,
                    single_turn=bool(initial_prompt),
                    validate=False,
                )
            except Exception as exc:
                print(f"[MAS run error — {scenario}] {exc}")

        self.stop_run()
        thread = Thread(target=_target, name=f"mas-run-{scenario}", daemon=True)
        thread.start()
        with self._run_lock:
            self._run_thread = thread

    def stop_run(self) -> None:
        with self._run_lock:
            self._run_thread = None

    def is_running(self) -> bool:
        with self._run_lock:
            return self._run_thread is not None and self._run_thread.is_alive()


def get_backend(name: str, **kwargs: Any) -> InfraBackendPlugin:
    """Create an infrastructure backend by name.

    This is a convenience alias for ``create_backend()`` from
    ``mas.runtime.plugins.infra_backend_plugin``.
    """
    return create_backend(name, **kwargs)

