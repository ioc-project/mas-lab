#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Reproducibility metadata and provenance tracking.

Captures version information, configuration, and provenance chain for
experiment runs, enabling re-evaluation and model-sensitivity analysis.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExperimentMetadata:
    """Immutable reproducibility metadata for an experiment run."""

    # Core versioning
    mas_lab_version: str
    """MAS-Lab framework version or commit hash."""

    framework_commit: str
    """MAS Framework commit hash used for agent execution."""

    model_name: str
    """LLM model used (e.g., 'gpt-4o', 'claude-3.5-sonnet')."""

    model_endpoint: str
    """API endpoint (e.g., 'https://api.openai.com/v1')."""

    recorded_at: str
    """ISO 8601 timestamp when experiment was recorded."""

    # Experiment scope
    experiment_name: str
    """Experiment identifier (e.g., 'design-patterns-qa')."""

    dataset_version: str
    """Dataset identifier or version."""

    dataset_item_count: int
    """Number of items evaluated."""

    n_runs: int
    """Number of runs per item."""

    # Optional: governance/plugin details (Labs 2–3)
    governance_overlays: list[str] = field(default_factory=list)
    """List of governance overlays applied (Lab 2 specific)."""

    plugin_versions: Dict[str, str] = field(default_factory=dict)
    """Plugin name → version map (Lab 3 specific)."""

    # Optional: reproducibility helpers
    notes: str = ""
    """Human-readable notes on environment or variations."""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-serializable dict."""
        return asdict(self)

    def to_json_str(self) -> str:
        """Serialize to pretty JSON."""
        return json.dumps(self.to_dict(), indent=2, default=str)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> ExperimentMetadata:
        """Deserialize from dict."""
        return ExperimentMetadata(**data)

    @staticmethod
    def from_json_str(s: str) -> ExperimentMetadata:
        """Deserialize from JSON string."""
        return ExperimentMetadata.from_dict(json.loads(s))


def load_mas_lab_version() -> str:
    """Load MAS-Lab version from __version__ or git HEAD."""
    try:
        from mas.lab import __version__

        return __version__
    except (ImportError, AttributeError):
        logger.debug("suppressed", exc_info=True)

    # Fallback: read git HEAD
    try:
        git_dir = Path(__file__).parent.parent.parent.parent.parent / ".git" / "HEAD"
        if git_dir.exists():
            ref = git_dir.read_text().strip()
            if ref.startswith("ref:"):
                ref_path = Path(__file__).parent.parent.parent.parent.parent / ".git" / ref.split()[-1]
                if ref_path.exists():
                    return ref_path.read_text().strip()[:8]
    except Exception:
        logger.debug("suppressed", exc_info=True)

    return "unknown"


def get_framework_commit() -> str:
    """Get MAS Framework commit hash."""
    try:
        import subprocess

        from mas.runtime.secrets import child_env

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            env=child_env(),
            cwd=Path(__file__).parent.parent.parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:8]
    except Exception:
        logger.debug("suppressed", exc_info=True)
    return "unknown"


class MetadataWriter:
    """Helper to write reproducibility metadata alongside outputs."""

    @staticmethod
    def write_experiment_metadata(
        output_dir: Path,
        metadata: ExperimentMetadata,
        filename: str = "metadata.json",
    ) -> Path:
        """Write metadata to a file in output_dir.

        Args:
            output_dir: Directory where results are stored.
            metadata: Metadata object to serialize.
            filename: Name of metadata file (default: metadata.json).

        Returns:
            Path to written file.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        output_path.write_text(metadata.to_json_str(), encoding="utf-8")
        logger.info("Wrote reproducibility metadata to %s", output_path)
        return output_path

    @staticmethod
    def read_experiment_metadata(
        output_dir: Path,
        filename: str = "metadata.json",
    ) -> Optional[ExperimentMetadata]:
        """Read metadata from a file.

        Args:
            output_dir: Directory where results are stored.
            filename: Name of metadata file.

        Returns:
            ExperimentMetadata if file exists, else None.
        """
        metadata_path = output_dir / filename
        if not metadata_path.exists():
            return None
        try:
            return ExperimentMetadata.from_json_str(metadata_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read metadata from %s: %s", metadata_path, e)
            return None


@dataclass
class OutputSchema:
    """Schema validation for experiment output files.

    Enables fail-fast detection of missing or malformed outputs.
    """

    required_files: list[str] = field(default_factory=list)
    """Relative paths that must exist (e.g., 'results/metrics.csv')."""

    required_columns: Dict[str, list[str]] = field(default_factory=dict)
    """Per-file column requirements (e.g., 'results/ci_summary.csv': ['scenario', 'mean', 'ci_low'])."""

    @staticmethod
    def validate(
        output_dir: Path,
        schema: OutputSchema,
        warn_only: bool = True,
    ) -> bool:
        """Validate output directory against schema.

        Args:
            output_dir: Root directory of outputs.
            schema: Schema to validate against.
            warn_only: If True, log warnings; if False, raise ValueError.

        Returns:
            True if all checks pass, False otherwise.

        Raises:
            ValueError if warn_only=False and validation fails.
        """
        errors = []

        # Check required files
        for file_path in schema.required_files:
            full_path = output_dir / file_path
            if not full_path.exists():
                errors.append(f"Missing required file: {file_path}")

        # Check required columns
        for file_path, required_cols in schema.required_columns.items():
            full_path = output_dir / file_path
            if not full_path.exists():
                errors.append(f"Cannot validate columns: {file_path} does not exist")
                continue

            try:
                import pandas as pd

                df = pd.read_csv(full_path)
                missing_cols = [c for c in required_cols if c not in df.columns]
                if missing_cols:
                    errors.append(f"Missing columns in {file_path}: {missing_cols}. Found: {list(df.columns)}")
            except Exception as e:
                errors.append(f"Error reading {file_path}: {e}")

        if errors:
            msg = "\n  ".join(errors)
            if warn_only:
                logger.warning("Output schema validation failed:\n  %s", msg)
                return False
            else:
                raise ValueError(f"Output schema validation failed:\n  {msg}")

        logger.info("Output schema validation passed")
        return True
