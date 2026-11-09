#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import click
from mas.lab.cli import app
from mas.lab.cli.extensions import register_extension_components


class _FakeEP:
    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class _FakeEPs:
    def __init__(self, entries):
        self._entries = entries

    def select(self, group):
        if group == "mas.lab.cli.components":
            return self._entries
        return []


@click.command("ext-demo")
def _ext_demo_cmd():
    pass


class _DemoComponent:
    def register(self, target: click.Group) -> str | None:
        if _ext_demo_cmd.name in target.commands:
            return None
        target.add_command(_ext_demo_cmd)
        return _ext_demo_cmd.name


class _ServicesComponent:
    def register(self, target: click.Group) -> str | None:
        @click.command("ext-services")
        def _cmd():
            pass

        if _cmd.name in target.commands:
            return None
        target.add_command(_cmd)
        return _cmd.name


def test_core_cli_has_no_enterprise_commands_by_default():
    assert "demo" not in app.commands
    assert "services" not in app.commands
    # Optional CLI extensions register via entry points; do not assert presence here.


def test_register_extension_components_registers_commands(monkeypatch):
    target = click.Group()

    fake_eps = _FakeEPs(
        [
            _FakeEP("demo", _DemoComponent),
            _FakeEP("services", _ServicesComponent()),
        ]
    )

    monkeypatch.setattr("mas.lab.cli.extensions.entry_points", lambda: fake_eps)

    registered = register_extension_components(target)

    assert "ext-demo" in target.commands
    assert "ext-services" in target.commands
    assert sorted(registered) == ["ext-demo", "ext-services"]


def test_register_extension_components_skips_invalid_and_duplicate(monkeypatch):
    @click.command("benchmark")
    def _dup_name():
        pass

    class _DuplicateComponent:
        def register(self, app_group: click.Group) -> str | None:
            if _dup_name.name in app_group.commands:
                return None
            app_group.add_command(_dup_name)
            return _dup_name.name

    target = click.Group()
    target.add_command(_dup_name)

    fake_eps = _FakeEPs(
        [
            _FakeEP("invalid-target", object()),
            _FakeEP("duplicate", _DuplicateComponent),
            _FakeEP("load-error", RuntimeError("boom")),
        ]
    )

    monkeypatch.setattr("mas.lab.cli.extensions.entry_points", lambda: fake_eps)

    registered = register_extension_components(target)

    assert registered == []
    assert "benchmark" in target.commands
