#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from mas._dist import expand_extra, extra_hint, extra_spec, installed_version, name_for

_ROOT = Path(__file__).resolve().parents[2]


def _toml_name(rel: str) -> str:
    text = (_ROOT / rel / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("name"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise AssertionError(f"no name in {rel}/pyproject.toml")


def test_name_for_reads_nearest_pyproject() -> None:
    runtime_init = _ROOT / "runtime" / "src" / "mas" / "runtime" / "__init__.py"
    assert name_for(file=runtime_init) == _toml_name("runtime")


def test_name_for_module() -> None:
    assert name_for(module="mas.runtime") == _toml_name("runtime")


def test_extra_spec_and_hint_use_pyproject_name() -> None:
    yaml_path = _ROOT / "library-telemetry" / "library.yaml"
    expected = _toml_name("library-telemetry")
    assert extra_spec("convert", file=yaml_path) == f"{expected}[convert]"
    assert extra_hint("convert", file=yaml_path) == f'uv pip install "{expected}[convert]"'
    assert extra_hint("convert", file=yaml_path, editable=True) == (f'uv pip install -e "{expected}[convert]"')


def test_expand_extra_leaves_full_spec() -> None:
    assert expand_extra("already[foo]", file=_ROOT / "runtime" / "pyproject.toml") == "already[foo]"
    assert expand_extra("") == ""


def test_expand_extra_fills_bare_name() -> None:
    yaml_path = _ROOT / "library-skills" / "library.yaml"
    assert expand_extra("adk", file=yaml_path) == extra_spec("adk", file=yaml_path)


def test_installed_version_of_runtime() -> None:
    ver = installed_version(module="mas.runtime")
    assert ver
    assert ver != "0.1.0"


def test_name_for_requires_anchor() -> None:
    with pytest.raises(LookupError):
        name_for()


def test_extra_spec_rejects_empty_extra() -> None:
    with pytest.raises(ValueError):
        extra_spec("  ", file=_ROOT / "runtime" / "pyproject.toml")


def test_expand_extra_passthrough_when_unresolved(tmp_path: Path) -> None:
    orphan = tmp_path / "nowhere.py"
    orphan.write_text("# no pyproject above here\n", encoding="utf-8")
    assert expand_extra("convert", file=orphan) == "convert"


def test_installed_version_missing_fallback() -> None:
    from mas import _dist as dist

    original = dist.name_for

    def boom(**_kwargs: object) -> str:
        return "mas-lab-does-not-exist-for-tests"

    dist.name_for = boom  # type: ignore[assignment]
    try:
        assert installed_version(file=_ROOT / "runtime" / "pyproject.toml", missing="missing") == "missing"
    finally:
        dist.name_for = original


def test_name_from_installed_matches_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas import _dist as dist

    runtime_init = _ROOT / "runtime" / "src" / "mas" / "runtime" / "__init__.py"

    class _Entry:
        pass

    class _Dist:
        metadata = {"Name": "from-record"}
        files = [_Entry()]

        def locate_file(self, _entry: object) -> Path:
            return runtime_init

    monkeypatch.setattr(dist, "_name_from_pyproject", lambda _path: None)
    monkeypatch.setattr(dist.importlib.metadata, "distributions", lambda: [_Dist()])
    dist._name_from_installed.cache_clear()
    assert name_for(file=runtime_init) == "from-record"
    dist._name_from_installed.cache_clear()


def test_origin_of_missing_and_broken_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas import _dist as dist

    assert dist._origin_of("this_module_does_not_exist_zzz") is None

    def _boom(_name: str) -> None:
        raise ValueError("nope")

    monkeypatch.setattr(dist.importlib.util, "find_spec", _boom)
    assert dist._origin_of("anything") is None


def test_origin_of_namespace_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mas import _dist as dist

    class _Spec:
        origin = "namespace"
        submodule_search_locations = [str(tmp_path)]

    monkeypatch.setattr(dist.importlib.util, "find_spec", lambda _name: _Spec())
    assert dist._origin_of("pkg") == tmp_path.resolve()

    class _Empty:
        origin = None
        submodule_search_locations = []

    monkeypatch.setattr(dist.importlib.util, "find_spec", lambda _name: _Empty())
    assert dist._origin_of("pkg") is None


def test_project_name_without_project_table() -> None:
    from mas import _dist as dist

    assert dist._project_name("name = 'x'\n") is None


def test_name_from_installed_skips_empty_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas import _dist as dist

    class _Empty:
        files = None

    class _Bad:
        files = ["x"]

        def locate_file(self, _entry: object) -> Path:
            raise OSError("nope")

    monkeypatch.setattr(dist.importlib.metadata, "distributions", lambda: [_Empty(), _Bad()])
    dist._name_from_installed.cache_clear()
    assert dist._name_from_installed(Path("/tmp/nope.py")) is None
    dist._name_from_installed.cache_clear()
