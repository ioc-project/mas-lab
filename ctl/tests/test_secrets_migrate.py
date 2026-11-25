#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-ctl secrets migrate`` rewrites the removed env forms and never reads a variable."""

from __future__ import annotations

import yaml
from click.testing import CliRunner
from mas.ctl.cli.commands.secrets import secrets_group
from mas.ctl.secrets.migrate import has_old_forms, migrate_text

OLD = """\
spec:
  proxy:
    # the endpoint
    api_base: env:LLM_PROXY_API_BASE|https://proxy.example/v1
    api_key_env: OPENAI_API_KEY   # the key
  other:
    password_env: MY_DB_PASSWORD
    token_env: SOME_TOKEN
    host: env:NO_DEFAULT
"""


def test_old_forms_become_refs_and_hosts_and_the_rest_is_left_alone():
    result = migrate_text(OLD)
    doc = yaml.safe_load(result.text)
    assert doc["spec"]["proxy"] == {"api_base": "https://proxy.example/v1", "api_key": {"secret": "openai"}}
    assert doc["spec"]["other"]["password"] == {"secret": "my-db-password"}
    assert "    # the endpoint\n" in result.text and "  # the key" in result.text
    assert result.secrets == {"openai": "OPENAI_API_KEY", "my-db-password": "MY_DB_PASSWORD"}
    # a variable that is not a secret, and a value with no default, need a person
    assert len(result.left) == 2
    assert "token_env: SOME_TOKEN" in result.text and "env:NO_DEFAULT" in result.text


def test_a_migrated_file_is_clean_and_a_clean_file_is_unchanged():
    clean = migrate_text("spec:\n  proxy:\n    api_base: https://x/v1\n    api_key: {secret: openai}\n")
    assert clean.changed == 0 and not clean.left
    assert has_old_forms(OLD) and not has_old_forms(migrate_text(OLD.split("  other:")[0]).text)


def test_the_command_reports_by_default_and_rewrites_with_write(tmp_path):
    path = tmp_path / "proxy.yaml"
    path.write_text(OLD.split("  other:")[0], encoding="utf-8")
    runner = CliRunner()
    report = runner.invoke(secrets_group, ["migrate", str(path)], obj={"secrets_dir": str(tmp_path / "s")})
    assert report.exit_code == 0 and "would rewrite 2" in report.output
    assert path.read_text(encoding="utf-8") == OLD.split("  other:")[0]
    done = runner.invoke(secrets_group, ["migrate", "--write", str(path)], obj={"secrets_dir": str(tmp_path / "s")})
    assert done.exit_code == 0 and "rewrote 2" in done.output
    assert 'printf %s "$OPENAI_API_KEY" | mas-ctl secrets set openai' in done.output
    assert (tmp_path / "proxy.yaml.bak").read_text(encoding="utf-8") == OLD.split("  other:")[0]
    assert not has_old_forms(path.read_text(encoding="utf-8"))


def test_the_command_fails_when_a_line_needs_a_person(tmp_path):
    path = tmp_path / "proxy.yaml"
    path.write_text("host: env:NO_DEFAULT\n", encoding="utf-8")
    result = CliRunner().invoke(secrets_group, ["migrate", str(path)], obj={})
    assert result.exit_code != 0 and "need a person" in result.output


def test_the_load_error_names_the_command():
    from mas.runtime.secrets.ref import removed_key_problem

    assert "mas-ctl secrets migrate" in removed_key_problem("api_key_env", "OPENAI_API_KEY")
    assert "mas-ctl secrets migrate" in removed_key_problem("api_base", "env:X|https://h")
