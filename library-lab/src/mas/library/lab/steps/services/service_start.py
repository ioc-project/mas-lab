#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""service_start — pipeline step that provisions an infrastructure service.

Starts a named service from an ``infra/v1`` ``ServiceBundle`` (or a legacy
top-level ``services:`` map) in the experiment's ``infra/`` directory and
exports the service's env vars into the current process so subsequent steps
can consume them.

Typical use-case: start a fake OTel collector before per-run ``export_otel``
steps, then stop it with :class:`service_stop.ServiceStopStep` in the post
phase.

Config keys
-----------
service : str (required)
    Name of the service as declared in the infra bundle YAML.
infra : str, default "services"
    Infra bundle name.  Resolved as ``<experiment_dir>/infra/<name>.yaml``.
    Falls back to the workspace root's ``infra/<name>.yaml`` when not found
    locally.  Use ``"services"`` (default) for the backward-compatible
    ``infra/services.yaml``.
health_timeout : int, default 30
    Seconds to wait for the service's health URL to become reachable.  Zero
    disables the wait entirely (fire-and-forget).

Example (experiment ``pipeline:`` section, phase=pre):

.. code-block:: yaml

    pipeline:
      - type: service_start
        name: start-otel
        phase: pre
        config:
          service: otel-collector
          infra: local-test
          health_timeout: 30
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import urlopen
from urllib.error import URLError

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.runtime.spec.infra_paths import experiment_infra_bundle_path, resolve_infra_bundle

logger = logging.getLogger(__name__)


def _resolve_infra_yaml(
    infra_name: str, ctx: Any, base_dir: Optional[Path] = None
) -> Path:
    """Resolve ``infra/<name>.yaml`` relative to the experiment directory.

    Resolution order:
    1. ``<experiment_dir>/infra/<name>.yaml``   (experiment-local)
    2. ``<workspace_root>/infra/<name>.yaml``   (shared across labs)

    ``base_dir`` overrides the experiment-dir derived from *ctx* when given.
    """
    exp_dir: Path
    if base_dir is not None:
        exp_dir = base_dir
    else:
        config_path: Optional[Path] = getattr(
            getattr(ctx, "pipeline", None), "config_path", None
        )
        exp_dir = config_path.parent if config_path else Path(".")

    found = resolve_infra_bundle(exp_dir, infra_name, search_workspace=True)
    if found is not None:
        return found
    # Return the local path even if missing — ServiceManager will log a warning.
    local = experiment_infra_bundle_path(exp_dir, infra_name)
    if local is not None:
        return local
    name = infra_name if infra_name.endswith(".yaml") else f"{infra_name}.yaml"
    return exp_dir / "infra" / name


def _wait_for_url(url: str, timeout: int) -> bool:
    """Poll *url* until it returns 2xx or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urlopen(url, timeout=2)
            if 200 <= resp.status < 300:
                return True
        except (URLError, OSError):
            logger.debug('suppressed', exc_info=True)
        time.sleep(1)
    return False


class ServiceStartStep(PipelineStep):
    """Start a named service from an infra bundle and export its env vars.

    Intended to run in the ``pre`` phase (before the benchmark loop) so that
    infrastructure services are ready when per-run pipeline steps execute.

    Delegates lifecycle management to
    :class:`~mas.lab.benchmark.service_manager.ServiceManager`.
    """

    type = "service_start"

    def outputs_exist(self, output_dir: Path) -> bool:
        # Service lifecycle steps are always executed — they are idempotent
        # (ServiceManager skips start if already running) but must never be
        # skipped by the pipeline cache check.
        return False

    async def execute(self, ctx: Any) -> StepOutput:  # type: ignore[override]
        from mas.lab.benchmark.service_manager import ServiceManager

        service_name: str = self.config["service"]
        infra_name: str = self.config.get("infra", "services")
        health_timeout: int = int(self.config.get("health_timeout", 30))

        services_yaml = _resolve_infra_yaml(infra_name, ctx)
        logger.info(
            "service_start: starting %r from %s", service_name, services_yaml
        )

        mgr = ServiceManager(services_yaml=services_yaml)
        ok = mgr.start(service_name)

        if not ok:
            logger.warning(
                "service_start: failed to start service %r — continuing",
                service_name,
            )
            return StepOutput(
                metadata={"service": service_name, "started": False, "healthy": False}
            )

        # Optional health-check wait
        healthy: Optional[bool] = None
        if health_timeout > 0:
            svc = mgr._services.get(service_name)
            health_url = (svc.health_check.url if svc else "") or ""
            if health_url:
                logger.info(
                    "service_start: waiting up to %ds for %s …", health_timeout, health_url
                )
                healthy = _wait_for_url(health_url, health_timeout)
                if healthy:
                    logger.info("service_start: %r is healthy", service_name)
                else:
                    logger.warning(
                        "service_start: health check timed out for %r at %s",
                        service_name, health_url,
                    )

        return StepOutput(
            metadata={
                "service": service_name,
                "started": True,
                "healthy": healthy,
                "infra": infra_name,
                "services_yaml": str(services_yaml),
            }
        )
