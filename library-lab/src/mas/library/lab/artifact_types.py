#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Artifact kinds declared by mas-library-lab.

Each class is a library.yaml ``type: artifact`` plugin
(``type`` / ``name`` / ``module`` / ``class``).
"""

from __future__ import annotations

from mas.lab.lab.config.artifact_types import ArtifactType


class Trace(ArtifactType):
    path = "{run_dir}/traces/events.jsonl"
    format = "jsonl"
    stream = "native"
    realtime = True
    description = "Raw observability event stream from a single run."


class RunInfo(ArtifactType):
    path = "{run_dir}/run_info.json"
    format = "json"
    description = "Run metadata (hash, model, timing, status)."


class Plot(ArtifactType):
    path = "{level_dir}/plot.png"
    format = "png"
    description = "Visualization output (PNG, SVG, HTML, or PDF)."


class Metrics(ArtifactType):
    path = "{level_dir}/metrics.json"
    format = "json"
    schema = "artefacts/metrics.schema.json"
    stream = "metric"
    realtime = True
    description = "Quality metrics computed by evaluation providers."


class ChatSnapshot(ArtifactType):
    path = "{run_dir}/custom.chat.jsonl"
    format = "jsonl"
    stream = "custom.chat"
    plot = "chat"
    realtime = True
    description = "Folded chat cards for the chat HTML plot."


class TopologySnapshot(ArtifactType):
    path = "{run_dir}/custom.topology.jsonl"
    format = "jsonl"
    stream = "custom.topology"
    plot = "topology"
    realtime = True
    description = "Folded graph for the topology HTML plot."


class LoopSeries(ArtifactType):
    path = "{run_dir}/plots/series.svg"
    format = "svg"
    stream = "series"
    plot = "loop"
    realtime = True
    description = "Generic timeseries (html live; svg/png end-state)."


class DataFrame(ArtifactType):
    path = "{level_dir}/data.csv"
    format = "csv"
    description = "Tidy CSV dataframe for analysis and plotting."
