#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PackageMetadata:
    name: str
    version: str | None = None
    description: str | None = None
    entry_point: str | None = None
    type: str = "unknown"  # agent, mas, tool, etc.
    manifest: Dict[str, Any] | None = None  # Raw manifest content


class AgentPackage(ABC):
    """Abstract representation of an agent package on disk."""

    def __init__(self, path: Path):
        self.path = path.resolve()

    @property
    @abstractmethod
    def metadata(self) -> PackageMetadata:
        """Extract metadata from the package."""
        pass

    @abstractmethod
    def get_command(self) -> List[str]:
        """Get the command to execute this package."""
        pass

    def get_library_entry(self) -> Optional[tuple[str, str]]:
        """
        Returns (module_name, function_name) if this package can be run as a library.
        Returns None if it supports CLI only.
        """
        return None

    @classmethod
    @abstractmethod
    def detect(cls, path: Path) -> Optional[AgentPackage]:
        """Attempt to detect this package type at the given path."""
        pass


class MASPackage(AgentPackage):
    """A MAS package (directory or manifest) for osi-agent."""

    def __init__(self, path: Path, manifest_path: Optional[Path] = None):
        super().__init__(path)
        self.manifest_path = manifest_path or (path / "mas.json")
        self._manifest = None

    @property
    def metadata(self) -> PackageMetadata:
        if not self._manifest:
            try:
                if self.manifest_path.exists():
                    self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                else:
                    self._manifest = {}
            except Exception:
                self._manifest = {}

        mas_info = self._manifest.get("mas", {})
        return PackageMetadata(
            name=mas_info.get("id", self.path.name),
            version=mas_info.get("version"),
            description=mas_info.get("description"),
            type="mas",
            manifest=self._manifest,
        )

    def get_command(self) -> List[str]:
        # CLI Fallback
        if not self.manifest_path.exists() and (self.path / "run.py").exists():
            return [sys.executable, str(self.path / "run.py")]

        if self.manifest_path.exists():
            return ["uv", "run", "python", "-m", "mas.runtime.app", "--config", str(self.manifest_path)]
        return ["uv", "run", "python", "-m", "mas.runtime.app"]

    def get_library_entry(self) -> Optional[tuple[str, str]]:
        # If no manifest but run.py exists, assumes it's a script package
        if not self.manifest_path.exists() and (self.path / "run.py").exists():
            return ("run", "main")

        return ("mas.runtime.app", "run_mas")

    @classmethod
    def detect(cls, path: Path) -> Optional[MASPackage]:
        # Broad detection: any directory is potentially a MAS if the user says so.
        # But we prioritize checking for artifacts.
        if (path / "mas.json").exists():
            return cls(path, path / "mas.json")
        if (path / "config" / "mas.json").exists():
            return cls(path, path / "config" / "mas.json")

        # If no manifest, we still return a MASPackage if it looks like a project
        if path.is_dir():
            return cls(path, None)
        return None


def discover_package(path: Path) -> AgentPackage:
    """Factory to discover the best package wrapper for a path."""
    # Priority: MASPackage (default for osi-agent runtime)
    return MASPackage.detect(path)
