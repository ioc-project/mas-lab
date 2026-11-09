#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ontology resolution via the SCOPE scope_ontology package."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("scope_ontology")


class TestOntologyPackage:
    def test_get_ontology_path_returns_existing_file(self):
        from scope_ontology import get_ontology_path

        path = get_ontology_path("mas-ontology")
        assert Path(path).exists(), f"mas-ontology.ttl not found at {path}"

    def test_get_ontology_path_shapes_returns_existing_file(self):
        from scope_ontology import get_ontology_path

        path = get_ontology_path("mas-shapes")
        assert Path(path).exists(), f"mas-shapes.ttl not found at {path}"

    def test_get_ontology_path_by_explicit_name(self):
        from scope_ontology import get_ontology_path

        p1 = get_ontology_path("mas-ontology")
        p2 = get_ontology_path("mas-ontology")
        assert Path(p1).resolve() == Path(p2).resolve()

    def test_unknown_name_raises_file_not_found(self):
        from scope_ontology import get_ontology_path

        with pytest.raises(FileNotFoundError):
            get_ontology_path("no-such-ontology-xyz")

    def test_mas_ontology_ttl_contains_expected_content(self):
        from scope_ontology import get_ontology_path

        path = Path(get_ontology_path("mas-ontology"))
        content = path.read_text()
        # Must contain OWL/RDFS declarations
        assert "@prefix" in content or "@base" in content or "owl:Ontology" in content


class TestOntologyResolution:
    def test_resolve_mas_ontology_path_returns_path(self):
        from mas.library.kg.ontology import resolve_mas_ontology_path

        path = resolve_mas_ontology_path()
        assert path.exists()
        assert path.suffix == ".ttl"

    def test_explicit_config_path_overrides_oxp(self, tmp_path):
        from mas.library.kg.ontology import resolve_mas_ontology_path

        fake_ttl = tmp_path / "custom.ttl"
        fake_ttl.write_text("# custom ontology\n")
        result = resolve_mas_ontology_path(config_path=str(fake_ttl))
        assert result == fake_ttl.resolve()

    def test_nonexistent_config_path_raises(self, tmp_path):
        from mas.library.kg.ontology import resolve_mas_ontology_path

        with pytest.raises(FileNotFoundError):
            resolve_mas_ontology_path(config_path=str(tmp_path / "ghost.ttl"))

    def test_resolve_kg_ontology_paths_returns_nonempty_list(self):
        from mas.library.kg.ontology import resolve_kg_ontology_paths

        paths = resolve_kg_ontology_paths()
        assert len(paths) >= 1
        for p in paths:
            assert p.exists(), f"TTL not found: {p}"

    def test_resolve_mas_shapes_path_returns_path(self):
        from mas.library.kg.ontology import resolve_mas_shapes_path

        path = resolve_mas_shapes_path()
        if path is not None:
            assert path.exists()
