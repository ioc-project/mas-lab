#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.lab.lab.config.lab_context -- discover_lab_context() must find
a lab's own lab-config.yaml regardless of how many directories the experiment
YAML is nested below the lab root. It used to check only the experiment's own
directory, so libraries:/plugins: declarations in a root-level lab-config.yaml
were silently never applied for any experiment nested more than one level
deep (the common case: <lab>.lab/experiments/<name>/experiment.yaml).
"""

from __future__ import annotations

import sys

import pytest
from mas.lab.lab.config.lab_context import (
    _discover_lab_name,
    discover_lab_context,
    inject_lab_libraries,
)


def _write_lab_config(lab_dir, *, name="my-lab", libraries=None, plugins=None) -> None:
    lines = ["lab:", f'  name: "{name}"']
    if libraries:
        lines.append("  libraries:")
        lines += [f"    - {lib}" for lib in libraries]
    if plugins:
        lines.append("  plugins:")
        for p in plugins:
            lines.append(f"    - path: {p['path']}")
            lines.append(f"      module: {p['module']}")
    (lab_dir / "lab-config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _private_plugin_registry(monkeypatch):
    """Tests here build the process-wide plugin registry under a lab-only view of the
    libraries; give them their own and put the real one back afterwards."""
    import mas.runtime.registry as registry

    monkeypatch.setattr(registry, "_instance", None)


def test_discover_lab_context_finds_lab_config_one_level_up(tmp_path) -> None:
    lab_dir = tmp_path / "some.lab"
    exp_dir = lab_dir / "experiments" / "01-smoke"
    exp_dir.mkdir(parents=True)
    _write_lab_config(lab_dir, name="some-lab")

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_dir == lab_dir.resolve()
    assert ctx.lab_yaml == lab_dir.resolve() / "lab-config.yaml"
    assert ctx.lab_name == "some-lab"


def test_discover_lab_context_finds_lab_config_several_levels_up(tmp_path) -> None:
    """The actual regression case: experiments/<hackathon>/<pattern>/pipelines/
    is 4 levels below the lab root, not 1."""
    lab_dir = tmp_path / "trip-planner.lab"
    exp_dir = lab_dir / "experiments" / "hackathon-experiments" / "centralized-moderator"
    pipelines_dir = exp_dir / "pipelines"
    pipelines_dir.mkdir(parents=True)
    _write_lab_config(lab_dir, name="trip-planner", libraries=["lib/"])

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_dir == lab_dir.resolve()
    assert ctx.lab_name == "trip-planner"
    assert ctx.libraries == ["lib/"]


def test_discover_lab_context_does_not_cross_a_lab_boundary(tmp_path) -> None:
    """An experiment inside inner.lab must never pick up outer.lab's config."""
    outer_lab = tmp_path / "outer.lab"
    inner_lab = outer_lab / "nested" / "inner.lab"
    exp_dir = inner_lab / "experiments" / "01-smoke"
    exp_dir.mkdir(parents=True)
    _write_lab_config(outer_lab, name="outer-lab")
    # inner.lab deliberately has no lab-config.yaml of its own.

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_yaml is None
    assert ctx.lab_name == "inner"  # falls back to the .lab dir name, not "outer-lab"


def test_discover_lab_context_falls_back_to_experiment_dir_when_no_lab_config(
    tmp_path,
) -> None:
    exp_dir = tmp_path / "experiments" / "01-smoke"
    exp_dir.mkdir(parents=True)

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_dir == exp_dir.resolve()
    assert ctx.lab_yaml is None


def test_discover_lab_name_matches_discover_lab_context(tmp_path) -> None:
    lab_dir = tmp_path / "cognitive-mas" / "trip-planner.lab"
    exp_dir = lab_dir / "experiments" / "top1-smoke"
    exp_dir.mkdir(parents=True)
    _write_lab_config(lab_dir, name="trip-planner")

    assert _discover_lab_name(exp_dir / "experiment.yaml") == "trip-planner"


def test_inject_lab_libraries_puts_the_lab_root_on_sys_path_not_the_experiment_dir(tmp_path, monkeypatch) -> None:
    lab_dir = tmp_path / "some.lab"
    exp_dir = lab_dir / "experiments" / "deeply" / "nested" / "example"
    exp_dir.mkdir(parents=True)
    _write_lab_config(lab_dir)

    monkeypatch.setattr(sys, "path", list(sys.path))
    ctx = discover_lab_context(exp_dir / "experiment.yaml")
    inject_lab_libraries(ctx)

    assert str(lab_dir.resolve()) in sys.path
    assert str(exp_dir.resolve()) not in sys.path


def test_inject_lab_libraries_resolves_libraries_relative_to_the_lab_root(tmp_path, monkeypatch) -> None:
    lab_dir = tmp_path / "some.lab"
    exp_dir = lab_dir / "experiments" / "a" / "b" / "c"
    exp_dir.mkdir(parents=True)
    lib_dir = lab_dir / "lib"
    lib_dir.mkdir()
    _write_lab_config(lab_dir, libraries=["lib/"])

    monkeypatch.setattr(sys, "path", list(sys.path))
    ctx = discover_lab_context(exp_dir / "experiment.yaml")
    inject_lab_libraries(ctx)

    assert str(lib_dir.resolve()) in sys.path


def test_inject_lab_libraries_registers_the_lab_for_library_discovery(tmp_path, monkeypatch) -> None:
    """A daemon runs experiments from a cwd outside the lab; the lab-local
    ``library.yaml`` (pipeline steps) must still be found there."""
    import mas.library_roots as library_roots

    lab_dir = tmp_path / "some.lab"
    lib_dir = lab_dir / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "library.yaml").write_text("apiVersion: mas/v1\nkind: Library\nname: lib\n", encoding="utf-8")
    _write_lab_config(lab_dir, libraries=["lib/"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(library_roots, "_REGISTERED_LAB_DIRS", [])

    assert lib_dir.resolve() not in library_roots.discover_library_roots()
    inject_lab_libraries(discover_lab_context(lab_dir / "experiment.yaml"))

    assert lib_dir.resolve() in library_roots.discover_library_roots()


def test_a_lab_step_resolves_in_a_process_whose_registry_is_already_built(tmp_path, monkeypatch) -> None:
    """The daemon failure: ``Unknown step type`` for a lab's own step.

    The registry exists before the lab is loaded and the cwd is outside the lab.
    """
    import mas.library_roots as library_roots
    from mas.lab.benchmark.pipeline.core import get_step

    lab_dir = tmp_path / "some.lab"
    lib_dir = lab_dir / "probe_lab"
    lib_dir.mkdir(parents=True)
    (lib_dir / "__init__.py").write_text("", encoding="utf-8")
    (lib_dir / "steps.py").write_text(
        "from mas.lab.benchmark.pipeline.core import PipelineStep, register_step\n\n"
        "class ProbeStep(PipelineStep):\n"
        "    type = 'probe_lab_step'\n\n"
        "    async def execute(self, ctx):\n"
        "        raise NotImplementedError\n",
        encoding="utf-8",
    )
    (lib_dir / "library.yaml").write_text(
        "apiVersion: mas/v1\nkind: Library\nname: probe-lab\ntypes:\n  - step\nplugins:\n"
        "  - type: step\n    name: probe_lab_step\n    module: probe_lab.steps\n    class: ProbeStep\n",
        encoding="utf-8",
    )
    _write_lab_config(lab_dir, libraries=["probe_lab/"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(library_roots, "_REGISTERED_LAB_DIRS", [])

    assert get_step("probe_lab_step") is None  # builds the registry before the lab is loaded
    inject_lab_libraries(discover_lab_context(lab_dir / "experiment.yaml"))

    assert get_step("probe_lab_step").__name__ == "ProbeStep"


def _write_lab_with_step(lab_dir, *, pkg: str, step: str, class_name: str) -> None:
    lib_dir = lab_dir / pkg
    lib_dir.mkdir(parents=True)
    (lib_dir / "__init__.py").write_text("", encoding="utf-8")
    (lib_dir / "steps.py").write_text(
        "from mas.lab.benchmark.pipeline.core import PipelineStep\n\n"
        f"class {class_name}(PipelineStep):\n"
        f"    type = {step!r}\n\n"
        "    async def execute(self, ctx):\n"
        "        raise NotImplementedError\n",
        encoding="utf-8",
    )
    (lib_dir / "library.yaml").write_text(
        "apiVersion: mas/v1\nkind: Library\n"
        f"name: {pkg}\ntypes:\n  - step\nplugins:\n"
        f"  - type: step\n    name: {step}\n    module: {pkg}.steps\n    class: {class_name}\n",
        encoding="utf-8",
    )
    _write_lab_config(lab_dir, libraries=[f"{pkg}/"])


def test_two_labs_that_ship_the_same_plugin_package_are_refused(tmp_path, monkeypatch) -> None:
    """A later lab must not silently import the first lab's ``lib`` package."""
    from mas.lab.benchmark.pipeline.core import get_step

    first = tmp_path / "first.lab"
    second = tmp_path / "second.lab"
    _write_lab_with_step(first, pkg="twinpkg", step="twin_a_step", class_name="TwinA")
    _write_lab_with_step(second, pkg="twinpkg", step="twin_b_step", class_name="TwinB")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("twin_a_step") is None
    inject_lab_libraries(discover_lab_context(first / "experiment.yaml"))
    assert "twinpkg" not in sys.modules

    with pytest.raises(ValueError, match="unique package name"):
        inject_lab_libraries(discover_lab_context(second / "experiment.yaml"))


def test_pipeline_yaml_resolves_a_lab_step_from_a_foreign_cwd(tmp_path, monkeypatch) -> None:
    """The same inject `/api/pipeline/run` uses before Pipeline.from_yaml."""
    from mas.lab.benchmark.pipeline.core import Pipeline, get_step

    lab_dir = tmp_path / "pipe.lab"
    _write_lab_with_step(lab_dir, pkg="pipe_lab", step="pipe_lab_step", class_name="PipeStep")
    pipelines = lab_dir / "pipelines"
    pipelines.mkdir()
    pipe = pipelines / "post.yaml"
    pipe.write_text(
        "pipeline:\n  name: post\n  steps:\n  - name: probe\n    type: pipe_lab_step\n    depends_on: []\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("pipe_lab_step") is None
    inject_lab_libraries(discover_lab_context(pipe))
    loaded = Pipeline.from_yaml(pipe)
    assert loaded.steps[0].type == "pipe_lab_step"
    assert type(loaded.steps[0]).__name__ == "PipeStep"


def test_a_lab_that_fails_to_register_does_not_break_the_next_lab(tmp_path, monkeypatch):
    from mas.lab.lab.config import lab_context as lc
    from mas.library_roots import _REGISTERED_LAB_DIRS

    bad = tmp_path / "bad.lab"
    bad.mkdir()
    (bad / "lab.yaml").write_text("name: bad\n")
    ctx = lc.LabContext(lab_dir=bad, lab_yaml=bad / "lab.yaml")

    def boom(**_kwargs) -> None:
        raise ValueError("clash")

    monkeypatch.setattr(lc, "register_new_library_plugins", boom)
    with pytest.raises(ValueError):
        lc.inject_lab_libraries(ctx)
    assert bad.resolve() not in _REGISTERED_LAB_DIRS


def test_a_refused_lab_leaves_earlier_labs_and_later_labs_working(tmp_path, monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.core import get_step
    from mas.library_roots import _REGISTERED_LAB_DIRS

    first = tmp_path / "first.lab"
    second = tmp_path / "second.lab"
    third = tmp_path / "third.lab"
    _write_lab_with_step(first, pkg="rbpkg", step="rb_a_step", class_name="RbA")
    _write_lab_with_step(second, pkg="rbpkg", step="rb_b_step", class_name="RbB")
    _write_lab_with_step(third, pkg="rbother", step="rb_c_step", class_name="RbC")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("rb_a_step") is None  # builds the registry
    inject_lab_libraries(discover_lab_context(first / "experiment.yaml"))
    with pytest.raises(ValueError, match="unique package name"):
        inject_lab_libraries(discover_lab_context(second / "experiment.yaml"))
    assert first.resolve() in _REGISTERED_LAB_DIRS
    assert second.resolve() not in _REGISTERED_LAB_DIRS

    inject_lab_libraries(discover_lab_context(third / "experiment.yaml"))
    assert get_step("rb_c_step") is not None
    # Re-injecting the first lab makes it current again; a refused lab afterwards must not
    # push it back behind the third.
    inject_lab_libraries(discover_lab_context(first / "experiment.yaml"))
    assert _REGISTERED_LAB_DIRS[-1] == first.resolve()
    with pytest.raises(ValueError, match="unique package name"):
        inject_lab_libraries(discover_lab_context(second / "experiment.yaml"))
    assert _REGISTERED_LAB_DIRS[-2:] == [third.resolve(), first.resolve()]


def test_pipeline_cli_loads_the_lab_library_steps(tmp_path, monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
    from mas.lab.benchmark.pipeline.core import get_step

    lab_dir = tmp_path / "cli.lab"
    _write_lab_with_step(lab_dir, pkg="cli_lab", step="cli_lab_step", class_name="CliStep")
    pipelines = lab_dir / "pipelines"
    pipelines.mkdir()
    pipe = pipelines / "post.yaml"
    pipe.write_text("pipeline:\n  name: post\n  steps: []\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("cli_lab_step") is None
    _load_lab_custom_steps(pipe.resolve())
    assert get_step("cli_lab_step") is not None


def test_pipeline_cli_stops_when_the_labs_library_cannot_be_loaded(tmp_path, monkeypatch) -> None:
    """A lab whose library fails to register ends the run with that error, not with an unknown step later."""
    from mas.lab.benchmark.pipeline.cli import _load_lab_custom_steps
    from mas.lab.benchmark.pipeline.core import get_step

    first = tmp_path / "first.lab"
    second = tmp_path / "second.lab"
    _write_lab_with_step(first, pkg="clashpkg", step="clash_a_step", class_name="ClashA")
    _write_lab_with_step(second, pkg="clashpkg", step="clash_b_step", class_name="ClashB")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("clash_a_step") is None
    inject_lab_libraries(discover_lab_context(first / "experiment.yaml"))
    with pytest.raises(SystemExit, match="Could not load the lab's libraries"):
        _load_lab_custom_steps(second / "experiment.yaml")


def test_a_broken_lab_found_through_the_cwd_does_not_break_the_next_lab(tmp_path, monkeypatch) -> None:
    """The daemon's cwd can sit inside a lab that fails: discovery finds it on every call."""
    from mas.lab.benchmark.pipeline.core import get_step

    broken = tmp_path / "broken.lab"
    good = tmp_path / "good.lab"
    _write_lab_with_step(broken, pkg="brokenpkg", step="broken_step", class_name="Broken")
    manifest = broken / "brokenpkg" / "library.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "    shortcuts: [bad.name]\n", encoding="utf-8")
    _write_lab_with_step(good, pkg="goodpkg", step="good_step", class_name="Good")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("good_step") is None  # builds the registry
    monkeypatch.chdir(broken / "brokenpkg")
    with pytest.raises(ValueError, match="without"):
        inject_lab_libraries(discover_lab_context(broken / "experiment.yaml"))

    inject_lab_libraries(discover_lab_context(good / "experiment.yaml"))
    assert get_step("good_step").__name__ == "Good"


def test_a_lab_whose_second_library_fails_registers_none_of_its_libraries(tmp_path, monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.core import get_step

    lab = tmp_path / "pair.lab"
    _write_lab_with_step(lab, pkg="apair", step="apair_step", class_name="APair")
    _write_lab_with_step(tmp_path / "scratch.lab", pkg="bpair", step="bpair_step", class_name="BPair")
    (tmp_path / "scratch.lab" / "bpair").rename(lab / "bpair")
    broken = lab / "bpair" / "library.yaml"
    broken.write_text(broken.read_text(encoding="utf-8") + "    shortcuts: [bad.name]\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert get_step("apair_step") is None  # builds the registry
    with pytest.raises(ValueError, match="without"):
        inject_lab_libraries(discover_lab_context(lab / "experiment.yaml"))

    assert get_step("apair_step") is None
