#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generate docs/packages-reference.md and docs/plugins-reference.md.

Run from the repository root:

    python3 scripts/gen_docs.py

Or via Taskfile:

    task docs-gen

The two output files are checked-in.  Commit them together with any
changes to pyproject.toml files or library.yaml manifests.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any

try:
    import yaml  # PyYAML — installed with the runtime package
except ImportError:
    print("PyYAML not available — install the runtime package first.", file=sys.stderr)
    sys.exit(1)


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class Package:
    name: str
    description: str
    version: str
    path: str  # relative install path (e.g. "runtime")
    layer: str
    deps: list[str] = field(default_factory=list)
    extras: dict[str, list[str]] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)
    entry_points: dict[str, dict[str, str]] = field(default_factory=dict)
    readme: str = ""


@dataclass
class Plugin:
    class_name: str
    module: str  # short module name (relative to module_base)
    full_module: str  # absolute Python module path
    category: str
    library: str  # checkout directory (stable across PyPI renames)
    deprecated_types: tuple[str, ...] = ()  # old plugin types that still resolve to this row


@dataclass
class ToolManifest:
    name: str
    description: str
    tags: list[str]
    module_path: str
    class_name: str
    library: str


@dataclass
class FlavourManifest:
    name: str
    description: str
    library: str


# ── TOML parsing ─────────────────────────────────────────────────────────────


def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _checkout_dir(path: Path, root: Path) -> str:
    """Top-level checkout directory that owns *path* (not the PyPI wheel name)."""
    rel = path.resolve().relative_to(root.resolve())
    parts = rel.parts
    if parts[:2] == ("lab", "components") and len(parts) >= 3:
        return "/".join(parts[:3])
    return parts[0]


def _pkg_from_toml(path: Path, install_path: str, layer: str) -> Package:
    data = _load_toml(path)
    proj = data.get("project", {})
    name = proj.get("name", path.parent.name)
    desc = proj.get("description", "")
    version = proj.get("version", "?")
    deps = proj.get("dependencies", [])
    extras = proj.get("optional-dependencies", {})
    scripts = proj.get("scripts", {})
    eps = {k: v for k, v in data.get("project", {}).get("entry-points", {}).items()}
    return Package(
        name=name,
        description=desc,
        version=version,
        path=install_path,
        layer=layer,
        deps=deps,
        extras=extras,
        scripts=scripts,
        entry_points=eps,
    )


# ── YAML parsing ──────────────────────────────────────────────────────────────


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _plugins_from_library_yaml(path: Path, library_name: str) -> list[Plugin]:
    data = _load_yaml_file(path)
    module_base = data.get("module_base", "")
    raw = data.get("plugins", [])
    # library.yaml accepts either a flat list (category carried by each entry's
    # "type") or a category -> entries mapping.
    if isinstance(raw, dict):
        grouped = raw.items()
    else:
        grouped = [(entry.get("type", ""), [entry]) for entry in raw]
    plugins: list[Plugin] = []
    for category, entries in grouped:
        for entry in entries:
            module = entry.get("module", "")
            # Entries may spell out the fully-qualified module or a path
            # relative to module_base.
            if module.startswith("mas.") or (module_base and module.startswith(f"{module_base}.")):
                full_module = module
            elif module_base:
                full_module = f"{module_base}.{module}"
            else:
                full_module = module
            # A single entry may declare one class or multiple via "classes"
            classes: list[str] = []
            if "class" in entry:
                classes.append(entry["class"])
            classes.extend(entry.get("classes", []))
            for cls in classes:
                plugins.append(
                    Plugin(
                        class_name=cls,
                        module=module,
                        full_module=full_module,
                        category=category,
                        library=library_name,
                        deprecated_types=tuple((entry.get("attributes") or {}).get("deprecated_types") or ()),
                    )
                )
    return plugins


def _tools_from_yaml_files(root: Path, library_name: str) -> list[ToolManifest]:
    tools: list[ToolManifest] = []
    for path in sorted(root.rglob("*.tool.yaml")):
        data = _load_yaml_file(path)
        if data.get("kind") != "Tool":
            continue
        meta = data.get("metadata", {})
        spec = data.get("spec", {})
        impl = spec.get("impl", {})
        desc = meta.get("description") or spec.get("description", "")
        if isinstance(desc, str):
            desc = desc.strip()
        tools.append(
            ToolManifest(
                name=meta.get("name", path.stem),
                description=desc,
                tags=meta.get("tags", []),
                module_path=impl.get("module_path", ""),
                class_name=impl.get("class_name", ""),
                library=library_name,
            )
        )
    return tools


def _flavours_from_yaml_files(root: Path, library_name: str) -> list[FlavourManifest]:
    flavours: list[FlavourManifest] = []
    for path in sorted(root.rglob("flavours/*.yaml")):
        data = _load_yaml_file(path)
        if data.get("kind") not in ("Flavour", "Overlay"):
            continue
        meta = data.get("metadata", {})
        desc = meta.get("description", "")
        if isinstance(desc, str):
            desc = desc.strip()
        flavours.append(
            FlavourManifest(
                name=meta.get("name", path.stem),
                description=desc,
                library=library_name,
            )
        )
    return flavours


# ── Markdown generation ───────────────────────────────────────────────────────

_HEADER = """\
<!-- AUTO-GENERATED by scripts/gen_docs.py — run `task docs-gen` to refresh -->
<!-- Do not edit this file manually. -->
"""

_AUTOREF_NOTE = """\
> **Auto-generated** from `pyproject.toml` files and `library.yaml` manifests.  \
Run `task docs-gen` to refresh after adding or modifying packages.

"""


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    col_w = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    sep = "| " + " | ".join("-" * w for w in col_w) + " |"
    header_row = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, col_w, strict=True)) + " |"
    data_rows = ["| " + " | ".join(str(c).ljust(w) for c, w in zip(row, col_w, strict=True)) + " |" for row in rows]
    return "\n".join([header_row, sep] + data_rows)


def _deps_clean(deps: list[str]) -> list[str]:
    """Keep only MAS package deps (mas-*), strip version specifiers."""
    out = []
    for d in deps:
        name = d.split("[")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
        if name.startswith("mas-"):
            out.append(f"`{name}`")
    return out


def generate_packages_reference(packages: list[Package]) -> str:
    lines: list[str] = [_HEADER, "# MAS Lab — Packages Reference\n", _AUTOREF_NOTE]

    # ── Summary table ────────────────────────────────────────────────────────
    lines.append("## Summary\n")
    rows = []
    for p in packages:
        cli = ", ".join(f"`{c}`" for c in p.scripts) or "—"
        rows.append([f"`{p.name}`", p.layer, p.description, cli])
    lines.append(
        _md_table(
            ["Package", "Layer", "Description", "CLI"],
            rows,
        )
    )
    lines.append("\n")

    # ── Installation ─────────────────────────────────────────────────────────
    lines.append(
        dedent("""\
        ---

        ## Installation

        ### Minimal install (core toolchain)

        Installs the four core packages needed to run and orchestrate agents,
        execute benchmarks, and call `mas-lab`.

        ```bash
        # From the repository root (uses UV_PROJECT_ENVIRONMENT if set):
        task install
        # Equivalent to:
        uv pip install -e runtime -e ctl -e library-standard -e lab/components/core
        ```

        ### Full install (all packages + optional extras)

        This is **not** a minimal install — it covers every package in this
        repository plus all optional feature extras.  Use this when you need
        evaluation metrics, or community sample
        apps.

        ```bash
        uv pip install \\
          -e runtime \\
          -e ctl \\
          -e library-standard \\
          -e lab/components/core \\
          -e lab/components/bench \\
          -e lab/components/controller \\
          -e . \\
          -e library-eval \\
          -e library-lab \\
          -e library-samples
        ```

        See [Tutorial 0 — Environment Setup](tutorials/00-environment-setup/README.md)
        for the complete walkthrough including LLM endpoint wiring and verification.

        ### Lab-specific backends

        Third-party backends used by a single lab (e.g. `letta` in
        `labs/extensions.lab`) are **not** extras of any package here — some of
        them conflict with core workspace dependencies.  They are declared in
        `<lab>/requirements.txt` and installed into a dedicated venv:

        ```bash
        task install-lab LAB=labs/extensions.lab
        ```

        OpenTelemetry export and knowledge-graph normalisation ship as
        separate libraries (see the tables below), not as extras of this
        package.

        ---

        ## Package Details

    """)
    )

    # ── Per-package sections ─────────────────────────────────────────────────
    for p in packages:
        lines.append(f"### `{p.name}`\n")
        lines.append(f"**Install path:** `{p.path}`  \n")
        lines.append(f"**Layer:** {p.layer}  \n")
        lines.append(f"{p.description}\n\n")

        # Install snippet
        if p.extras:
            extras_str = "all" if "all" in p.extras else ",".join(p.extras)
            lines.append(f"```bash\nuv pip install -e {p.path}  # source\n")
            lines.append(f'uv pip install "{p.name}[{extras_str}]"  # PyPI, all extras\n```\n\n')
        else:
            lines.append(f"```bash\nuv pip install -e {p.path}  # source\n")
            lines.append(f"uv pip install {p.name}  # PyPI\n```\n\n")

        # MAS deps
        mas_deps = _deps_clean(p.deps)
        if mas_deps:
            lines.append(f"**Depends on:** {', '.join(mas_deps)}  \n\n")

        # Optional extras
        if p.extras:
            lines.append("**Optional extras:**\n\n")
            rows_ext = [[f"`{k}`", ", ".join(f"`{v}`" for v in vs)] for k, vs in p.extras.items()]
            lines.append(_md_table(["Extra", "Packages / features"], rows_ext))
            lines.append("\n\n")

        # CLI commands
        if p.scripts:
            lines.append("**CLI commands:**\n\n")
            rows_cli = [[f"`{cmd}`", f"`{ep}`"] for cmd, ep in p.scripts.items()]
            lines.append(_md_table(["Command", "Entry point"], rows_cli))
            lines.append("\n\n")

        # Entry-point groups (abbreviated)
        for group, eps in p.entry_points.items():
            if group.startswith("mas."):
                short = group.replace("mas.", "")
                lines.append(
                    f"**Python entry point `{group}`:** this package advertises "
                    f"{len(eps)} discoverable item(s) so the runtime can load "
                    f"`{short}` without a hard-coded path.\n\n"
                )

        lines.append("---\n\n")

    return "".join(lines)


def generate_plugins_reference(
    plugins: list[Plugin],
    tools: list[ToolManifest],
    flavours: list[FlavourManifest],
) -> str:
    lines: list[str] = [_HEADER, "# MAS Lab — Plugin Reference\n", _AUTOREF_NOTE]

    lines.append(
        dedent("""\
        This document is the canonical index of all plugins, tools, and flavours
        distributed in `ioc-project/mas-lab`.  Each entry names the checkout
        directory that provides it. Wheel names live in the
        [packages reference](packages-reference.md).

        ---

        ## How to use a plugin

        Reference a plugin in your agent manifest (`agent.yaml`) or overlay:

        ```yaml
        plugins:
          - module: mas.library.standard.plugins.design_patterns.dp_react
            class: ReactDP
        ```

        Or by short name when using a flavour that already activates it.

        See [flavours documentation](https://github.com/ioc-project/mas-lab/blob/main/library-standard/docs/user-guide.md)
        and
        [Tutorial 2 — Creating a MAS](tutorials/02-creating-a-mas/README.md) for
        practical examples.

        ---

        ## Plugins by Category

    """)
    )

    # Group plugins by category, then sort alphabetically within each
    from collections import defaultdict

    by_cat: dict[str, list[Plugin]] = defaultdict(list)
    for p in plugins:
        by_cat[p.category].append(p)

    for category in sorted(by_cat):
        title = category.replace("-", " ").title()
        lines.append(f"### {title}\n\n")
        rows = []
        for p in sorted(by_cat[category], key=lambda x: x.class_name):
            rows.append(
                [
                    f"`{p.class_name}`",
                    f"`{p.full_module}`",
                    f"`{p.library}`",
                ]
            )
        lines.append(_md_table(["Class", "Full module path", "Library"], rows))
        lines.append("\n\n")

    deprecated = [p for p in plugins if p.deprecated_types]
    if deprecated:
        lines.append("### Deprecated plugin types\n\n")
        lines.append(
            "A plugin type listed here is a retired spelling. Lookups under it still resolve to the "
            "current row and warn once; a library that registers under it keeps loading. "
            "It will be dropped in a later release.\n\n"
        )
        rows_dep = [
            [
                f"`{t}`",
                f"`{p.category}`",
                f"`{p.class_name}`",
                f"`{p.library}`",
            ]
            for p in sorted(deprecated, key=lambda x: x.class_name)
            for t in p.deprecated_types
        ]
        lines.append(_md_table(["Deprecated type", "Use type", "Class", "Library"], rows_dep))
        lines.append("\n\n")

    lines.append("---\n\n")

    # ── All plugins flat alphabetical ────────────────────────────────────────
    lines.append("## All Plugins — Alphabetical Index\n\n")
    rows_all = []
    for p in sorted(plugins, key=lambda x: x.class_name.lower()):
        rows_all.append(
            [
                f"`{p.class_name}`",
                p.category,
                f"`{p.library}`",
            ]
        )
    lines.append(_md_table(["Class", "Category", "Library"], rows_all))
    lines.append("\n\n---\n\n")

    # ── Tools ────────────────────────────────────────────────────────────────
    lines.append("## Tool Manifests\n\n")
    lines.append("Tools declared via `*.tool.yaml` manifests across all libraries.\n\n")
    if tools:
        rows_t = []
        for t in sorted(tools, key=lambda x: x.name):
            tags = ", ".join(f"`{tg}`" for tg in t.tags) if t.tags else "—"
            rows_t.append(
                [
                    f"`{t.name}`",
                    t.description[:80] + ("…" if len(t.description) > 80 else ""),
                    f"`{t.class_name}`" if t.class_name else "—",
                    tags,
                    f"`{t.library}`",
                ]
            )
        lines.append(
            _md_table(
                ["Name", "Description", "Class", "Tags", "Library"],
                rows_t,
            )
        )
        lines.append("\n\n")
    else:
        lines.append("_No tool manifests found._\n\n")

    lines.append("---\n\n")

    # ── Flavours ─────────────────────────────────────────────────────────────
    lines.append("## Bundled Flavours\n\n")
    lines.append("Flavour YAML files shipped inside library packages.\n\n")
    if flavours:
        rows_f = []
        for f in sorted(flavours, key=lambda x: x.name):
            rows_f.append(
                [
                    f"`{f.name}`",
                    f.description[:80] + ("…" if len(f.description) > 80 else ""),
                    f"`{f.library}`",
                ]
            )
        lines.append(_md_table(["Name", "Description", "Library"], rows_f))
        lines.append("\n\n")
    else:
        lines.append("_No flavour manifests found._\n\n")

    return "".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

# Package registry: (pyproject path, install path, layer)
PACKAGE_REGISTRY: list[tuple[str, str, str]] = [
    # (pyproject.toml relative path, install path, layer label)
    ("pyproject.toml", ".", "Meta"),
    ("runtime/pyproject.toml", "runtime", "Runtime core"),
    ("ctl/pyproject.toml", "ctl", "Orchestration"),
    ("lab/components/core/pyproject.toml", "lab/components/core", "Lab framework"),
    ("lab/components/bench/pyproject.toml", "lab/components/bench", "Lab framework"),
    ("lab/components/controller/pyproject.toml", "lab/components/controller", "Lab framework"),
    ("library-standard/pyproject.toml", "library-standard", "Libraries"),
    ("library-skills/pyproject.toml", "library-skills", "Libraries"),
    ("library-eval/pyproject.toml", "library-eval", "Libraries"),
    ("library-lab/pyproject.toml", "library-lab", "Libraries"),
    ("library-samples/pyproject.toml", "library-samples", "Libraries"),
    ("library-ioa/pyproject.toml", "library-ioa", "Libraries"),
    ("library-enforcement-proxy/pyproject.toml", "library-enforcement-proxy", "Libraries"),
    ("library-telemetry/pyproject.toml", "library-telemetry", "Libraries"),
    ("library-kg/pyproject.toml", "library-kg", "Libraries"),
]

# Library manifests: library.yaml relative path (PyPI name comes from pyproject).
LIBRARY_MANIFESTS: list[str] = [
    "library-standard/library.yaml",
    "library-skills/library.yaml",
    "library-samples/library.yaml",
    "library-telemetry/library.yaml",
    "library-enforcement-proxy/library.yaml",
    "library-kg/library.yaml",
    "library-eval/library.yaml",
    "library-ioa/library.yaml",
]

# Tool/flavour search roots: relative path (PyPI name comes from pyproject).
ASSET_ROOTS: list[str] = [
    "library-standard/src",
    "library-skills/tools",
    "library-samples/apps",
]


def generate_pypi_names_snippet(packages: list[Package]) -> str:
    """MkDocs snippet file: install command and per-wheel names from pyproject."""
    lines = [
        "<!--",
        "  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates",
        "  SPDX-License-Identifier: Apache-2.0",
        "-->",
        "<!-- Auto-generated by scripts/gen_docs.py — do not edit; run task docs-gen -->",
        "",
    ]
    meta = next((p for p in packages if p.path == "."), None)
    if meta is not None:
        lines.extend(
            [
                "--8<-- [start:install-meta]",
                f"uv pip install {meta.name}",
                "--8<-- [end:install-meta]",
                "",
            ]
        )
    for p in packages:
        slug = "meta" if p.path == "." else p.path.replace("/", "-")
        lines.extend(
            [
                f"--8<-- [start:{slug}-name]",
                p.name,
                f"--8<-- [end:{slug}-name]",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MAS Lab reference docs.")
    parser.add_argument("--root", default=".", help="Repository root (default: .)")
    parser.add_argument("--output-dir", default="docs", help="Output directory (default: docs)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and fail if checked-in files differ",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Collect packages ─────────────────────────────────────────────────────
    packages: list[Package] = []
    for rel_toml, install_path, layer in PACKAGE_REGISTRY:
        toml_path = root / rel_toml
        if not toml_path.exists():
            print(f"  [skip] {rel_toml} not found", file=sys.stderr)
            continue
        pkg = _pkg_from_toml(toml_path, install_path, layer)
        packages.append(pkg)
        print(f"  [pkg]  {pkg.name} ({layer})")

    # ── Collect plugins ──────────────────────────────────────────────────────
    all_plugins: list[Plugin] = []
    for rel_manifest in LIBRARY_MANIFESTS:
        manifest_path = root / rel_manifest
        if not manifest_path.exists():
            print(f"  [skip] {rel_manifest} not found", file=sys.stderr)
            continue
        library_dir = _checkout_dir(manifest_path, root)
        plugins = _plugins_from_library_yaml(manifest_path, library_dir)
        all_plugins.extend(plugins)
        print(f"  [lib]  {library_dir}: {len(plugins)} plugin(s)")

    # ── Collect tools & flavours ─────────────────────────────────────────────
    all_tools: list[ToolManifest] = []
    all_flavours: list[FlavourManifest] = []
    for rel_root in ASSET_ROOTS:
        asset_root = root / rel_root
        if not asset_root.exists():
            continue
        library_dir = _checkout_dir(asset_root, root)
        tools = _tools_from_yaml_files(asset_root, library_dir)
        flavours = _flavours_from_yaml_files(asset_root, library_dir)
        all_tools.extend(tools)
        all_flavours.extend(flavours)
        if tools:
            print(f"  [tool] {library_dir}: {len(tools)} tool manifest(s)")
        if flavours:
            print(f"  [flv]  {library_dir}: {len(flavours)} flavour(s)")

    # ── Write packages-reference.md ──────────────────────────────────────────
    pkg_ref = out_dir / "packages-reference.md"
    pkg_content = generate_packages_reference(packages)
    plugin_ref = out_dir / "plugins-reference.md"
    plugin_content = generate_plugins_reference(all_plugins, all_tools, all_flavours)

    if args.check:
        errors: list[str] = []
        for path, expected, label in (
            (pkg_ref, pkg_content, "packages-reference.md"),
            (plugin_ref, plugin_content, "plugins-reference.md"),
        ):
            if not path.is_file():
                errors.append(f"missing {path.relative_to(root)} — run: task docs-gen")
                continue
            actual = path.read_text(encoding="utf-8")
            if actual != expected:
                errors.append(f"stale {label} — run: task docs-gen")
        if errors:
            for err in errors:
                print(f"  [check FAIL] {err}", file=sys.stderr)
            sys.exit(1)
        print("  [check OK] docs/packages-reference.md and docs/plugins-reference.md")

        from gen_doc_paths import generate_mas_paths_snippet

        paths_snippet = root / "docs" / "includes" / "mas-paths.md"
        paths_content = generate_mas_paths_snippet()
        if not paths_snippet.is_file():
            print("  [check FAIL] missing docs/includes/mas-paths.md — run: task docs-gen", file=sys.stderr)
            sys.exit(1)
        if paths_snippet.read_text(encoding="utf-8") != paths_content:
            print("  [check FAIL] stale docs/includes/mas-paths.md — run: task docs-gen", file=sys.stderr)
            sys.exit(1)
        print("  [check OK] docs/includes/mas-paths.md")
        names_snippet = root / "docs" / "includes" / "pypi-names.md"
        names_content = generate_pypi_names_snippet(packages)
        if not names_snippet.is_file():
            print("  [check FAIL] missing docs/includes/pypi-names.md — run: task docs-gen", file=sys.stderr)
            sys.exit(1)
        if names_snippet.read_text(encoding="utf-8") != names_content:
            print("  [check FAIL] stale docs/includes/pypi-names.md — run: task docs-gen", file=sys.stderr)
            sys.exit(1)
        print("  [check OK] docs/includes/pypi-names.md")
        return

    pkg_ref.write_text(pkg_content, encoding="utf-8")
    print(f"\n  → wrote {pkg_ref.relative_to(root)}")

    # ── Write plugins-reference.md ───────────────────────────────────────────
    plugin_ref.write_text(plugin_content, encoding="utf-8")
    print(f"  → wrote {plugin_ref.relative_to(root)}")

    # ── Path snippet include (MkDocs pymdownx.snippets) ─────────────────────
    from gen_doc_paths import generate_mas_paths_snippet

    paths_snippet = root / "docs" / "includes" / "mas-paths.md"
    paths_content = generate_mas_paths_snippet()
    paths_snippet.parent.mkdir(parents=True, exist_ok=True)
    paths_snippet.write_text(paths_content, encoding="utf-8")
    print(f"  → wrote {paths_snippet.relative_to(root)}")

    names_snippet = root / "docs" / "includes" / "pypi-names.md"
    names_snippet.write_text(generate_pypi_names_snippet(packages), encoding="utf-8")
    print(f"  → wrote {names_snippet.relative_to(root)}")


if __name__ == "__main__":
    main()
