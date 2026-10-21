#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Locate and plan writes for each catalog kind. Edits always write where they were read."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mas.lab.controller.path_safety import (
    UnsafeNameError,
    ensure_inside,
    is_hidden_under,
    validate_name,
)
from mas.lab.controller.scope_catalog import Scope
from mas.ctl.overlay.canvas import overlay_is_layout_only
from mas.runtime.spec.source import load_yaml_file

_VERSION_DIR = re.compile(r"^v\d+")


@dataclass
class FileWrite:
    path: Path
    content: str = ""
    delete: bool = False
    source: Path | None = None


@dataclass
class ItemBundle:
    item_id: str
    kind: str
    name: str
    version: str | None
    ref: str
    revision: str
    writable: bool
    manifest: str
    children: dict[str, str] = field(default_factory=dict)
    title: str = ""
    summary: str = ""
    versions: list[str] = field(default_factory=list)
    latest: str | None = None
    resolved_id: str = ""
    # Internal only — never serialised to the API.
    root: Path | None = None
    manifest_path: Path | None = None
    child_paths: dict[str, Path] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.item_id,
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "ref": self.ref,
            "revision": self.revision,
            "writable": self.writable,
            "manifest": self.manifest,
            "children": dict(self.children),
            "title": self.title,
            "summary": self.summary,
            "versions": list(self.versions),
            "latest": self.latest,
            "resolved_id": self.resolved_id or self.item_id,
        }
        return payload


@dataclass
class Located:
    item_id: str
    name: str
    version: str | None
    root: Path
    manifest_path: Path
    child_paths: dict[str, Path] = field(default_factory=dict)
    title: str = ""
    summary: str = ""


def item_ref(scope: Scope, item_id: str) -> str:
    return f"{scope.name}:{item_id}"


def revision_of(files: dict[Path, str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: str(p)):
        digest.update(str(path.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _title_summary(doc: dict[str, Any], fallback: str) -> tuple[str, str]:
    meta = doc.get("metadata") or {}
    block = doc.get("experiment") if isinstance(doc.get("experiment"), dict) else None
    name = (
        meta.get("name")
        or (block or {}).get("name")
        or doc.get("name")
        or fallback
    )
    summary = str(
        meta.get("description")
        or (block or {}).get("description")
        or doc.get("description")
        or ""
    ).strip()
    return str(name), summary.splitlines()[0][:200] if summary else ""


class KindLayout:
    kind: str = ""
    child_subdir = ""

    def locate(self, scope: Scope, item_id: str) -> Located | None:
        raise NotImplementedError

    def list_located(self, scope: Scope) -> list[Located]:
        raise NotImplementedError

    def read(self, scope: Scope, item_id: str) -> ItemBundle | None:
        located = self.locate(scope, item_id)
        if located is None:
            return None
        return self._bundle_from_located(scope, located)

    def _bundle_from_located(self, scope: Scope, located: Located) -> ItemBundle:
        files: dict[Path, str] = {}
        manifest = _read_text(located.manifest_path) if located.manifest_path.is_file() else ""
        files[located.manifest_path] = manifest
        children: dict[str, str] = {}
        for name, path in located.child_paths.items():
            if path.is_file():
                text = _read_text(path)
                children[name] = text
                files[path] = text
        versions, latest = self.versions_for(scope, located.name)
        resolved = located.item_id
        if located.version is None and latest:
            resolved = f"{located.name}@{latest}"
        return ItemBundle(
            item_id=located.item_id,
            kind=self.kind,
            name=located.name,
            version=located.version,
            ref=item_ref(scope, located.item_id),
            revision=revision_of(files),
            writable=scope.writable,
            manifest=manifest,
            children=children,
            title=located.title or located.name,
            summary=located.summary,
            versions=versions,
            latest=latest,
            resolved_id=resolved,
            root=located.root,
            manifest_path=located.manifest_path,
            child_paths=located.child_paths,
        )

    def versions_for(self, scope: Scope, name: str) -> tuple[list[str], str | None]:
        return versions_of(item for item in self.list_located(scope) if item.name == name)

    def child_path(self, located: Located, child_name: str) -> Path:
        existing = located.child_paths.get(child_name)
        if existing is not None:
            return existing
        if self.child_subdir:
            return located.root / self.child_subdir / f"{child_name}.yaml"
        return located.root / f"{child_name}.yaml"

    def plan_create(
        self,
        scope: Scope,
        *,
        name: str,
        version: str | None,
        template: str | None,
        parent: str | None,
        default_model: str | None,
        copy_from: ItemBundle | None = None,
    ) -> list[FileWrite]:
        raise NotImplementedError

    def plan_update(self, scope: Scope, bundle: ItemBundle, located: Located) -> list[FileWrite]:
        writes: list[FileWrite] = []
        if located.manifest_path is not None:
            writes.append(FileWrite(located.manifest_path, bundle.manifest))
        existing = dict(located.child_paths)
        for child_name, content in bundle.children.items():
            safe = validate_name(child_name)
            path = existing.get(safe) or self.child_path(located, safe)
            path = ensure_inside(scope.root, path)
            writes.append(FileWrite(path, content))
        for old_name, old_path in existing.items():
            if old_name not in bundle.children:
                writes.append(FileWrite(old_path, "", delete=True))
        return writes

    def plan_delete(self, scope: Scope, located: Located) -> list[FileWrite]:
        writes = [FileWrite(located.manifest_path, "", delete=True)]
        for path in located.child_paths.values():
            writes.append(FileWrite(path, "", delete=True))
        return writes

    def default_create_root(self, scope: Scope, name: str, version: str | None) -> Path:
        raise NotImplementedError


def _version_key(version: str) -> tuple:
    from mas.version_spec import version_sort_key

    return version_sort_key(version)


def versions_of(items: Iterable[Located]) -> tuple[list[str], str | None]:
    """Sorted unique versions from already-located items (no extra directory walk)."""
    versions = sorted({item.version for item in items if item.version}, key=_version_key)
    return versions, versions[-1] if versions else None


def apply_writes(scope: Scope, writes: list[FileWrite]) -> None:
    """Validate targets, write to a staging tree, then rename into place.

    On failure nothing from this batch remains. Every path is computed by a
    layout and checked against the scope root.
    """
    if not writes:
        return
    checked: list[FileWrite] = []
    for write in writes:
        target = ensure_inside(scope.root, write.path)
        checked.append(
            FileWrite(target, write.content, delete=write.delete, source=write.source)
        )

    created: list[Path] = []
    replaced: list[tuple[Path, Path | None]] = []
    staging: Path | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".mas-write-", dir=str(scope.root)))
        ensure_inside(scope.root, staging)
        staged: list[tuple[Path, Path]] = []
        for write in checked:
            if write.delete:
                continue
            rel = write.path.relative_to(scope.root.resolve())
            staged_path = staging / rel
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            if write.source is not None:
                shutil.copyfile(write.source, staged_path)
            else:
                staged_path.write_text(write.content.rstrip() + "\n", encoding="utf-8")
            staged.append((staged_path, write.path))
        for staged_path, final_path in staged:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if final_path.exists():
                backup = final_path.with_name(final_path.name + ".bak")
                os.replace(final_path, backup)
            os.replace(staged_path, final_path)
            created.append(final_path)
            replaced.append((final_path, backup))
        for write in checked:
            if write.delete and write.path.exists():
                backup = write.path.with_name(write.path.name + ".bak")
                os.replace(write.path, backup)
                replaced.append((write.path, backup))
        shutil.rmtree(staging, ignore_errors=True)
        for _final, backup in replaced:
            if backup is not None and backup.exists():
                backup.unlink()
    except Exception:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        for final, backup in reversed(replaced):
            if backup is not None and backup.exists():
                os.replace(backup, final)
            elif final.exists() and final in created:
                final.unlink()
        raise


class ApplicationLayout(KindLayout):
    kind = "application"
    child_subdir = "agents"

    def list_located(self, scope: Scope) -> list[Located]:
        from mas.lab.controller.lab_registry import LabRegistry

        resources = LabRegistry.apps_in_directory(scope.root)
        return self._from_resources(scope, resources)

    def _from_resources(self, scope: Scope, resources: dict[str, dict[str, Any]]) -> list[Located]:
        located: list[Located] = []
        seen: set[str] = set()
        for catalog_id, entry in resources.items():
            name, version = _split_optional_version(catalog_id)
            rel = str(entry.get("path") or "")
            # Internal path is used only to find the folder; never returned.
            manifest_path = (scope.root / rel).resolve() if rel else None
            if manifest_path is None or not manifest_path.is_file():
                # Bare app directory or agent-only app.
                continue
            try:
                ensure_inside(scope.root, manifest_path)
            except UnsafeNameError:
                continue
            root = manifest_path.parent
            child_paths: dict[str, Path] = {}
            agents_dir = root / "agents"
            if agents_dir.is_dir():
                for agent_file in sorted(agents_dir.glob("*.yaml")):
                    child_paths[agent_file.stem] = agent_file
            title, summary = name, ""
            try:
                doc = load_yaml_file(manifest_path)
                title, summary = _title_summary(doc if isinstance(doc, dict) else {}, name)
            except Exception:
                pass
            item_id = catalog_id
            if item_id in seen:
                continue
            seen.add(item_id)
            located.append(
                Located(
                    item_id=item_id,
                    name=name,
                    version=version,
                    root=root,
                    manifest_path=manifest_path,
                    child_paths=child_paths,
                    title=title,
                    summary=summary,
                )
            )
        return located

    def locate(self, scope: Scope, item_id: str) -> Located | None:
        name, version = _split_optional_version(item_id)
        matches = [loc for loc in self.list_located(scope) if loc.name == name]
        if not matches:
            return None
        if version is None:
            versioned = [m for m in matches if m.version]
            if versioned:
                latest = max(versioned, key=lambda m: _version_key(m.version or "v0"))
                return latest
            return matches[0]
        for match in matches:
            if match.version == version or match.item_id == item_id:
                return match
        return None

    def default_create_root(self, scope: Scope, name: str, version: str | None) -> Path:
        if version:
            return ensure_inside(scope.root, scope.root / "apps" / name / version)
        return ensure_inside(scope.root, scope.root / "apps" / name)

    def plan_create(
        self,
        scope: Scope,
        *,
        name: str,
        version: str | None,
        template: str | None,
        parent: str | None,
        default_model: str | None,
        copy_from: ItemBundle | None = None,
    ) -> list[FileWrite]:
        root = self.default_create_root(scope, name, version)
        mas_path = root / "mas.yaml"
        agent_path = root / "agents" / f"{name}.yaml"
        if copy_from is not None:
            writes = [FileWrite(mas_path, _rewrite_name(copy_from.manifest, name))]
            for child_name, content in copy_from.children.items():
                writes.append(
                    FileWrite(root / "agents" / f"{child_name}.yaml", content)
                )
            return writes
        mas_yaml = _new_mas_yaml(name)
        agent_yaml = _new_agent_yaml(name, default_model=default_model)
        return [FileWrite(mas_path, mas_yaml), FileWrite(agent_path, agent_yaml)]


class AgentLayout(KindLayout):
    kind = "agent"

    def list_located(self, scope: Scope) -> list[Located]:
        located: list[Located] = []
        for folder in (scope.root / "agents",):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.yaml")):
                located.append(self._located_from_file(scope, path))
        app_layout = ApplicationLayout()
        for app in app_layout.list_located(scope):
            for child_name, path in app.child_paths.items():
                located.append(
                    Located(
                        item_id=child_name,
                        name=child_name,
                        version=None,
                        root=path.parent,
                        manifest_path=path,
                        title=child_name,
                    )
                )
        # Deduplicate by item_id, first wins.
        uniq: dict[str, Located] = {}
        for item in located:
            uniq.setdefault(item.item_id, item)
        return list(uniq.values())

    def _located_from_file(self, scope: Scope, path: Path) -> Located:
        title, summary = path.stem, ""
        try:
            doc = load_yaml_file(path)
            title, summary = _title_summary(doc if isinstance(doc, dict) else {}, path.stem)
        except Exception:
            pass
        return Located(
            item_id=path.stem,
            name=path.stem,
            version=None,
            root=path.parent,
            manifest_path=path,
            title=title,
            summary=summary,
        )

    def locate(self, scope: Scope, item_id: str) -> Located | None:
        name, _version = _split_optional_version(item_id)
        for item in self.list_located(scope):
            if item.name == name or item.item_id == item_id:
                return item
        return None

    def default_create_root(self, scope: Scope, name: str, version: str | None) -> Path:
        return ensure_inside(scope.root, scope.root / "agents")

    def plan_create(
        self,
        scope: Scope,
        *,
        name: str,
        version: str | None,
        template: str | None,
        parent: str | None,
        default_model: str | None,
        copy_from: ItemBundle | None = None,
    ) -> list[FileWrite]:
        if parent:
            app = ApplicationLayout().locate(scope, parent)
            if app is None:
                raise KeyError(parent)
            path = ensure_inside(scope.root, app.root / "agents" / f"{name}.yaml")
            content = copy_from.manifest if copy_from else _new_agent_yaml(name, default_model=default_model)
            writes = [FileWrite(path, content)]
            # Add the agent to the MAS spec.agency.agents and workflow.
            mas_text = _read_text(app.manifest_path)
            mas_text = _add_agent_to_mas(mas_text, name)
            writes.append(FileWrite(app.manifest_path, mas_text))
            return writes
        path = ensure_inside(scope.root, scope.root / "agents" / f"{name}.yaml")
        content = copy_from.manifest if copy_from else _new_agent_yaml(name, default_model=default_model)
        return [FileWrite(path, content)]


class YamlDirLayout(KindLayout):
    """YAML files in a typed directory (overlays, pipelines, experiments, datasets)."""

    directory: str = ""
    suffix: str = ".yaml"
    declared_kind: str = ""

    def list_located(self, scope: Scope) -> list[Located]:
        folder = scope.root / self.directory
        if not folder.is_dir():
            # Fall back to kind-classified files already discovered by the registry.
            return self._from_registry(scope)
        located: list[Located] = []
        for path in sorted(folder.rglob(f"*{self.suffix}")):
            if not path.is_file() or is_hidden_under(scope.root, path):
                continue
            located.append(self._located_from_file(path))
        if located:
            return located
        return self._from_registry(scope)

    def _from_registry(self, scope: Scope) -> list[Located]:
        from mas.lab.controller.lab_registry import _iter_manifest_files

        expected = self.declared_kind or self.kind
        parent = self.directory if self.directory else None
        located: list[Located] = []
        for path in _iter_manifest_files(scope.root, expected, parent_dir=parent):
            located.append(self._located_from_file(path))
        return located

    def _located_from_file(self, path: Path) -> Located:
        item_id = path.stem
        title, summary = item_id, ""
        try:
            doc = load_yaml_file(path)
            if isinstance(doc, dict):
                title, summary = _title_summary(doc, item_id)
                meta_name = (doc.get("metadata") or {}).get("name")
                if meta_name:
                    item_id = str(meta_name)
                exp = doc.get("experiment")
                if isinstance(exp, dict) and exp.get("name"):
                    item_id = str(exp["name"])
        except Exception:
            pass
        return Located(
            item_id=item_id,
            name=item_id,
            version=None,
            root=path.parent,
            manifest_path=path,
            title=title,
            summary=summary,
        )

    def locate(self, scope: Scope, item_id: str) -> Located | None:
        name, _version = _split_optional_version(item_id)
        for item in self.list_located(scope):
            if item.item_id == item_id or item.name == name or item.manifest_path.stem == name:
                return item
        return None

    def default_create_root(self, scope: Scope, name: str, version: str | None) -> Path:
        return ensure_inside(scope.root, scope.root / self.directory)

    def plan_create(
        self,
        scope: Scope,
        *,
        name: str,
        version: str | None,
        template: str | None,
        parent: str | None,
        default_model: str | None,
        copy_from: ItemBundle | None = None,
    ) -> list[FileWrite]:
        path = ensure_inside(scope.root, self.default_create_root(scope, name, version) / f"{name}{self.suffix}")
        if copy_from is not None:
            return [FileWrite(path, _rewrite_name(copy_from.manifest, name))]
        return [FileWrite(path, self._blank(name))]

    def _blank(self, name: str) -> str:
        return f"metadata:\n  name: {name}\n"


class OverlayLayout(YamlDirLayout):
    kind = "overlay"
    directory = "overlays"
    declared_kind = "overlay"

    def list_located(self, scope: Scope) -> list[Located]:
        located = []
        for item in super().list_located(scope):
            try:
                doc = load_yaml_file(item.manifest_path)
            except Exception:
                located.append(item)
                continue
            if overlay_is_layout_only(doc):
                continue
            located.append(item)
        return located

    def _blank(self, name: str) -> str:
        return (
            "apiVersion: mas/v1\n"
            "kind: Overlay\n"
            f"metadata:\n  name: {name}\n  description: ''\n"
            "spec:\n"
            "  target:\n"
            "    kind: MAS\n"
            "  patch: {}\n"
        )


class PipelineLayout(YamlDirLayout):
    kind = "pipeline"
    directory = "pipelines"
    declared_kind = "pipeline"

    def _blank(self, name: str) -> str:
        return (
            "apiVersion: lab/v1\n"
            "kind: Pipeline\n"
            f"metadata:\n  name: {name}\n  description: ''\n"
            "spec:\n  steps: []\n"
        )


class ExperimentLayout(YamlDirLayout):
    kind = "experiment"
    directory = "experiments"
    declared_kind = "experiment"

    def _blank(self, name: str) -> str:
        return (
            "experiment:\n"
            f"  name: {name}\n"
            "  description: ''\n"
            "  application:\n"
            "    app: unset\n"
            "  dataset:\n"
            "    path: queries\n"
            "  run:\n"
            "    n_runs: 1\n"
        )


class DatasetLayout(YamlDirLayout):
    kind = "dataset"
    directory = "datasets"
    declared_kind = "dataset"

    def _blank(self, name: str) -> str:
        return (
            "apiVersion: lab/v1\n"
            "kind: Dataset\n"
            f"metadata:\n  name: {name}\n  description: ''\n"
            "spec:\n"
            "  items:\n"
            "    - id: item-1\n"
            "      inputs:\n"
            "        user: placeholder\n"
        )


class ToolLayout(YamlDirLayout):
    kind = "tool"
    directory = "tools"
    suffix = ".tool.yaml"
    declared_kind = "tool"

    def list_located(self, scope: Scope) -> list[Located]:
        folder = scope.root / self.directory
        if not folder.is_dir():
            return []
        located: list[Located] = []
        for path in sorted(folder.rglob("*.tool.yaml")):
            if is_hidden_under(scope.root, path):
                continue
            located.append(self._located_from_file(path))
        return located

    def _blank(self, name: str) -> str:
        return (
            "apiVersion: mas/v1\n"
            "kind: Tool\n"
            f"metadata:\n  name: {name}\n  description: ''\n"
            "spec:\n  source:\n    type: python\n"
        )


class SkillLayout(KindLayout):
    kind = "skill"

    def list_located(self, scope: Scope) -> list[Located]:
        folder = scope.root / "skills"
        if not folder.is_dir():
            return []
        located: list[Located] = []
        for skill_dir in sorted(folder.iterdir()):
            if skill_dir.name.startswith("."):
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_md.is_file():
                located.append(
                    Located(
                        item_id=skill_dir.name,
                        name=skill_dir.name,
                        version=None,
                        root=skill_dir,
                        manifest_path=skill_md,
                        title=skill_dir.name,
                    )
                )
        return located

    def locate(self, scope: Scope, item_id: str) -> Located | None:
        name, _version = _split_optional_version(item_id)
        for item in self.list_located(scope):
            if item.name == name:
                return item
        return None

    def plan_create(self, scope: Scope, **kwargs: Any) -> list[FileWrite]:
        raise UnsafeNameError("skills cannot be created through this API")

    def default_create_root(self, scope: Scope, name: str, version: str | None) -> Path:
        return ensure_inside(scope.root, scope.root / "skills" / name)


class ScenarioLayout(YamlDirLayout):
    kind = "scenario"
    directory = "scenarios"
    declared_kind = "scenario"

    def _blank(self, name: str) -> str:
        return f"id: {name}\nlogic: []\n"


_LAYOUTS: dict[str, KindLayout] = {
    "application": ApplicationLayout(),
    "agent": AgentLayout(),
    "overlay": OverlayLayout(),
    "pipeline": PipelineLayout(),
    "experiment": ExperimentLayout(),
    "dataset": DatasetLayout(),
    "tool": ToolLayout(),
    "skill": SkillLayout(),
    "scenario": ScenarioLayout(),
}


def layout_for(kind: str) -> KindLayout:
    layout = _LAYOUTS.get(kind)
    if layout is None:
        raise KeyError(kind)
    return layout


def _split_optional_version(item_id: str) -> tuple[str, str | None]:
    text = str(item_id or "").strip()
    if "@" not in text:
        return text, None
    name, _, version = text.rpartition("@")
    version = version.strip()
    if not version or version.lower() == "latest":
        return name, None
    if version.startswith("^") or version[0] in "<>=":
        return name, version
    return name, version


def _new_mas_yaml(name: str) -> str:
    agent_ref = f"{name}.agent"
    return (
        "apiVersion: mas/v1\n"
        "kind: MAS\n"
        "metadata:\n"
        f"  name: {name}\n"
        "  description: ''\n"
        "spec:\n"
        "  agency:\n"
        "    agents:\n"
        f"      - id: {name}\n"
        f"        ref: {agent_ref}\n"
        "  workflow:\n"
        f"    entry: {name}\n"
        "    nodes:\n"
        f"      - id: {name}\n"
        "        role: specialist\n"
    )


def _new_agent_yaml(name: str, *, default_model: str | None) -> str:
    model_block = ""
    if default_model:
        model_block = (
            "  models:\n"
            f"    - model: {default_model}\n"
            "      temperature: 0.3\n"
        )
    return (
        "apiVersion: mas/v1\n"
        "kind: Agent\n"
        "metadata:\n"
        f"  name: {name}\n"
        "spec:\n"
        f"  description: {name}\n"
        "  context:\n"
        f"    role: You are {name}.\n"
        f"{model_block}"
        "  design_pattern:\n"
        "    type: react\n"
    )


def _rewrite_name(manifest: str, name: str) -> str:
    try:
        doc = yaml.safe_load(manifest) or {}
    except yaml.YAMLError:
        return manifest
    if not isinstance(doc, dict):
        return manifest
    if isinstance(doc.get("metadata"), dict):
        doc["metadata"]["name"] = name
    elif "experiment" not in doc:
        doc["metadata"] = {"name": name}
    if isinstance(doc.get("experiment"), dict):
        doc["experiment"]["name"] = name
    return yaml.safe_dump(doc, sort_keys=False)


def _add_agent_to_mas(mas_text: str, agent_id: str) -> str:
    doc = yaml.safe_load(mas_text) or {}
    spec = doc.setdefault("spec", {})
    agency = spec.setdefault("agency", {})
    agents = agency.setdefault("agents", [])
    if not any(isinstance(a, dict) and a.get("id") == agent_id for a in agents):
        agents.append({"id": agent_id, "ref": f"{agent_id}.agent"})
    workflow = spec.setdefault("workflow", {})
    nodes = workflow.setdefault("nodes", [])
    if not any(isinstance(n, dict) and n.get("id") == agent_id for n in nodes):
        nodes.append({"id": agent_id, "role": "specialist"})
    return yaml.safe_dump(doc, sort_keys=False)
