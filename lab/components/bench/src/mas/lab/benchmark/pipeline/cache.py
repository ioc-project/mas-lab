#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
Caching and fingerprinting for pipeline steps.

Determines when steps need to be rerun based on:
- Configuration changes (step config, pipeline config)
- Dependency output changes (upstream step outputs)
- Missing outputs (expected files don't exist)

This is the canonical implementation.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages fingerprints and caching for pipeline steps."""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory cache of fingerprints (sidecars of ArtifactStore node keys)
        self._fingerprints: Dict[str, str] = {}
        self._loaded = False
        from mas.lab.benchmark.pipeline.store import ArtifactStore

        self.store = ArtifactStore(self.cache_dir / "artefacts")
    
    def _load_fingerprints(self):
        """Load all fingerprints from cache directory."""
        if self._loaded:
            return
        
        for fp_file in self.cache_dir.glob("*.fingerprint"):
            step_name = fp_file.stem
            with open(fp_file, "r") as f:
                self._fingerprints[step_name] = f.read().strip()
        
        self._loaded = True
    
    def compute_fingerprint(
        self,
        step: "PipelineStep",
        dependency_outputs: Dict[str, "StepOutput"],
    ) -> str:
        """Compute fingerprint for a step.
        
        Fingerprint = hash(step config + dependency output hashes)
        
        Args:
            step: Pipeline step
            dependency_outputs: Outputs from dependency steps
            
        Returns:
            SHA256 hex digest
        """
        from mas.lab.benchmark.pipeline.incremental import step_version
        from mas.lab.benchmark.pipeline.store import compute_node_key

        config = {
            k: v
            for k, v in dict(getattr(step, "config", None) or {}).items()
            if k not in {"_batch_instances", "dry_run"}
        }
        digests = [
            self._hash_step_output(output)
            for _, output in sorted(dependency_outputs.items())
        ]
        return compute_node_key(step.type, step_version(step), config, *digests)
    
    def _hash_step_output(self, output: "StepOutput") -> str:
        """Hash a step output.
        
        Uses:
        - Metadata (counts, timestamps excluded)
        - File hashes (for files that exist)
        - Data keys (not values, as they may be large DataFrames)
        """
        output_repr = {
            "metadata": {
                k: v for k, v in output.metadata.items()
                if k not in ["timestamp", "duration_ms"]  # Exclude timing
            },
            "data_keys": sorted(output.data.keys()),
            "files": [
                {
                    "path": str(f),
                    "hash": self._hash_file(f) if f.exists() else None
                }
                for f in output.files
            ],
        }
        
        json_str = json.dumps(output_repr, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()
    
    def _hash_file(self, path: Path) -> str:
        """Fingerprint a file without reading its bytes.

        Size + mtime is enough to detect reruns; hashing hundreds of CSV
        artifacts on every step dominated large level-scoped pipelines.
        """
        try:
            st = path.stat()
        except OSError:
            return "missing"
        return f"{st.st_size}:{st.st_mtime_ns}"
    
    def get_cached_fingerprint(self, step_name: str) -> Optional[str]:
        """Get cached fingerprint for step."""
        self._load_fingerprints()
        return self._fingerprints.get(step_name)
    
    def save_fingerprint(self, step_name: str, fingerprint: str):
        """Save fingerprint for step."""
        fp_path = self.cache_dir / f"{step_name}.fingerprint"
        with open(fp_path, "w") as f:
            f.write(fingerprint)
        
        self._fingerprints[step_name] = fingerprint
    
    def should_rerun(
        self,
        step: "PipelineStep",
        dependency_outputs: Dict[str, "StepOutput"],
        output_dir: Path,
    ) -> bool:
        """Determine if step needs to be rerun.
        
        Returns True if:
        - No cached fingerprint (never run)
        - Fingerprint changed (config or dependency changed)
        - Expected outputs don't exist
        
        Args:
            step: Pipeline step
            dependency_outputs: Outputs from dependencies
            output_dir: Base output directory
            
        Returns:
            True if step should be rerun
        """
        # Check if outputs exist on disk or were persisted in the cache store
        if not step.outputs_exist(output_dir) and self.load_output(step.name) is None:
            return True
        
        # Compute current fingerprint
        current_fp = self.compute_fingerprint(step, dependency_outputs)
        
        # Get cached fingerprint
        cached_fp = self.get_cached_fingerprint(step.name)
        
        if cached_fp is None:
            return True  # Never run before
        
        if current_fp != cached_fp:
            return True  # Config or dependencies changed

        if self.store.exists(current_fp):
            self.store.get(current_fp)
        return False  # Cache valid
    
    def invalidate(self, step_name: str):
        """Invalidate cache for a step."""
        fp_path = self.cache_dir / f"{step_name}.fingerprint"
        if fp_path.exists():
            fp_path.unlink()
        
        if step_name in self._fingerprints:
            del self._fingerprints[step_name]
    
    def clear(self):
        """Clear all cached fingerprints."""
        for fp_file in self.cache_dir.glob("*.fingerprint"):
            fp_file.unlink()
        
        self._fingerprints.clear()

    # ------------------------------------------------------------------
    # Intermediate artifact persistence
    # ------------------------------------------------------------------

    def save_output(self, step_name: str, output: Any) -> None:
        """Persist a step's in-memory output to disk.

        Handles DataFrames (saved as Parquet) and JSON-serialisable values.
        Non-serialisable values (e.g. complex objects) are silently skipped —
        the step will be re-run if loading fails later.
        """
        step_dir = self.cache_dir / step_name
        step_dir.mkdir(parents=True, exist_ok=True)

        data_serialized: Dict[str, Any] = {}
        for key, value in output.data.items():
            try:
                import pandas as pd  # optional; skip DataFrame handling if absent
                if isinstance(value, pd.DataFrame):
                    pq_path = step_dir / f"{key}.parquet"
                    value.to_parquet(pq_path, index=False)
                    data_serialized[key] = {"__type__": "dataframe", "path": str(pq_path)}
                    continue
            except ImportError:
                logger.debug('suppressed', exc_info=True)

            if isinstance(value, Path):
                data_serialized[key] = {"__type__": "path", "path": str(value)}
                continue

            try:
                json.dumps(value, default=str)
                data_serialized[key] = value
            except (TypeError, ValueError):
                pass  # skip — step will be re-run if this key is needed

        meta: Dict[str, Any] = {
            "metadata": {
                k: v for k, v in output.metadata.items()
                if k not in ("duration_ms",)
            },
            "files":    [str(f) for f in output.files],
            "warnings": output.warnings,
            "data":     data_serialized,
        }
        (step_dir / "output.json").write_text(
            json.dumps(meta, default=str), encoding="utf-8"
        )

    def persist_output(self, step: Any, fingerprint: str, output: Any) -> None:
        """Fingerprint sidecar + in-memory snapshot + content-addressed store."""
        self.save_fingerprint(getattr(step, "name", ""), fingerprint)
        try:
            self.save_output(getattr(step, "name", ""), output)
        except Exception:
            logger.debug("save_output skipped for %s", getattr(step, "name", ""), exc_info=True)
        try:
            from mas.lab.benchmark.pipeline.incremental import step_version
            from mas.lab.benchmark.pipeline.store import StoreManifest

            files: Dict[str, Path] = {}
            for path in getattr(output, "files", None) or []:
                dest = Path(path)
                if dest.is_file():
                    files[dest.name] = dest
            self.store.put(
                StoreManifest(
                    key=fingerprint,
                    step_type=str(getattr(step, "type", "")),
                    version=step_version(step),
                    config=dict(getattr(step, "config", None) or {}),
                    input_digests=[],
                    output_digest=fingerprint,
                    extra={"step_name": getattr(step, "name", "")},
                ),
                files=files or None,
            )
        except Exception:
            logger.debug(
                "artifact store put skipped for %s",
                getattr(step, "name", ""),
                exc_info=True,
            )

    def load_output(self, step_name: str) -> Optional[Any]:
        """Load a previously persisted step output, or ``None`` if unavailable."""
        output_file = self.cache_dir / step_name / "output.json"
        if not output_file.exists():
            return None

        try:
            meta = json.loads(output_file.read_text(encoding="utf-8"))
        except Exception:
            return None

        data: Dict[str, Any] = {}
        for key, value in meta.get("data", {}).items():
            if isinstance(value, dict):
                if value.get("__type__") == "dataframe":
                    try:
                        import pandas as pd
                        data[key] = pd.read_parquet(value["path"])
                    except Exception:
                        return None  # parquet missing/corrupt — force re-run
                    continue
                if value.get("__type__") == "path":
                    data[key] = Path(value["path"])
                    continue
            data[key] = value

        # Import here to avoid circular import at module level
        from mas.lab.benchmark.pipeline import StepOutput
        return StepOutput(
            data=data,
            files=[Path(f) for f in meta.get("files", [])],
            metadata=meta.get("metadata", {}),
            warnings=meta.get("warnings", []),
        )