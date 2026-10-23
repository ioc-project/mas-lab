#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load-time warnings for still-accepted legacy experiment/dataset shapes.

Callers must keep loading the old shape. Warn once per (code, where) so a
250-item dataset does not flood the log. Docs URLs are the published site;
the source path is in the message so a local checkout still works.
"""
from __future__ import annotations

import logging
import warnings
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

DOCS_BASE = "https://ioc-project.github.io/mas-lab"

# code -> (docs path under /docs, short replacement)
NOTICES: Dict[str, Tuple[str, str]] = {
    "experiment.mas": (
        "manifests/experiment.md#applications",
        "use application: {app|manifest, configs_dir}",
    ),
    "lab.mas": (
        "manifests/experiment.md#applications",
        "use lab.applications: [{app|manifest, configs_dir}]",
    ),
    "experiment.applications": (
        "manifests/experiment.md#applications",
        "use application: {app|manifest, configs_dir}",
    ),
    "experiment.application_post": (
        "manifests/experiment.md",
        "application.post is deprecated; use experiment-level post: (matches CLI --depth exp)",
    ),
    "experiment.test_key": (
        "manifests/experiment.md",
        "use item: (matches CLI --item)",
    ),
    "dataset.legacy_item": (
        "manifests/dataset-migration.md",
        "use inputs.user + expectations (Run Input Envelope)",
    ),
    "dataset.role_list_user": (
        "manifests/dataset-migration.md",
        "inputs.user must be a string or list of strings, not [{role, content}]",
    ),
    "dataset.bare_list": (
        "manifests/dataset.md#1-manifest-format",
        "wrap items in apiVersion/kind: Dataset and spec.items",
    ),
    "dataset.legacy_expectations": (
        "manifests/dataset-migration.md",
        "put app- or tool-specific ground truth under expectations.details",
    ),
    "pipeline.output_dir_template": (
        "manifests/pipeline.md#output-locations",
        "drop the {output_dir}/ prefix: use a relative path (results/trace_stats.csv) "
        "or omit the key to get the step's default artifact",
    ),
    "experiment.execution": (
        "manifests/experiment.md#design-vs-schedule",
        "use experiment.design, experiment.schedule, and experiment.bench_emulation",
    ),
}

_emitted: set[tuple[str, str]] = set()


def docs_url(docs_path: str) -> str:
    """MkDocs publishes ``docs/foo.md#bar`` as ``/foo/#bar``."""
    path, _, frag = docs_path.partition("#")
    html = path.removesuffix(".md").strip("/")
    url = f"{DOCS_BASE}/{html}/"
    if frag:
        url = f"{url}#{frag}"
    return url


def warn_deprecated(code: str, *, where: str) -> None:
    """Emit a DeprecationWarning and a log warning. No-op if already seen."""
    notice = NOTICES.get(code)
    if notice is None:
        raise KeyError(f"unknown deprecation code {code!r}")
    docs_path, replacement = notice
    key = (code, str(where))
    if key in _emitted:
        return
    _emitted.add(key)
    url = docs_url(docs_path)
    message = (
        f"Deprecated: {code} in {where}; {replacement}. "
        f"See {url} (source: docs/{docs_path}). "
        "This shape still loads; it will be removed in a future release."
    )
    warnings.warn(message, DeprecationWarning, stacklevel=2)
    logger.warning(message)


def clear_deprecation_warnings() -> None:
    """Test helper: allow the same (code, where) to warn again."""
    _emitted.clear()
