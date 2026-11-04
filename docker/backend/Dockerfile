#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# MAS Lab tooling image — controller API, benchmarks, and agent runtime CLIs.
FROM python:3.12-bookworm

ENV MAS_INSTALL_ROOT=/opt/mas-lab
WORKDIR ${MAS_INSTALL_ROOT}

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Workspace metadata first (layer cache)
COPY pyproject.toml ./
COPY runtime/pyproject.toml runtime/
COPY ctl/pyproject.toml ctl/
COPY library-standard/pyproject.toml library-standard/
COPY library-skills/pyproject.toml library-skills/
COPY library-samples/pyproject.toml library-samples/
COPY library-lab/pyproject.toml library-lab/
COPY library-eval/pyproject.toml library-eval/
COPY library-ioa/pyproject.toml library-ioa/
COPY library-telemetry/pyproject.toml library-telemetry/
COPY library-kg/pyproject.toml library-kg/
COPY lab/components/core/pyproject.toml lab/components/core/
COPY lab/components/bench/pyproject.toml lab/components/bench/
COPY lab/components/controller/pyproject.toml lab/components/controller/

# Source trees
COPY runtime/ runtime/
COPY ctl/ ctl/
COPY library-standard/ library-standard/
COPY library-skills/ library-skills/
COPY library-samples/ library-samples/
COPY library-lab/ library-lab/
COPY library-eval/ library-eval/
COPY library-ioa/ library-ioa/
COPY library-telemetry/ library-telemetry/
COPY library-kg/ library-kg/
COPY lab/ lab/

# Build-time force-include sources: ctl + lab/components/core force-include
# ../docs/schemas(/lab). `uv sync` builds those packages editable, so these
# must be present or the build fails.
COPY docs/schemas/ docs/schemas/

# Default workspace content (overridden when /workspace is mounted)
COPY library-samples/sample-workspace/config.yaml ./config.yaml
COPY labs/ labs/
COPY docs/tutorials/ docs/tutorials/

ENV MAS_WORKSPACE_ROOT=/workspace
ENV MAS_DATA_ROOT=/data
ENV MAS_CONTROLLER_PORT=8090
ENV PYTHONUNBUFFERED=1

RUN uv sync --no-dev

COPY docker/backend/entrypoint.sh /usr/local/bin/mas-lab-entrypoint
RUN chmod +x /usr/local/bin/mas-lab-entrypoint

WORKDIR /workspace

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health')"

ENTRYPOINT ["/usr/local/bin/mas-lab-entrypoint"]
CMD ["controller"]
