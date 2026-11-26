#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bind a RunEnvironment around one MAS run and publish its event stream."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from mas.lab.benchmark.environment.engine import (
    RunEnvironment,
    compile_environment,
    environment_digest,
    merge_environment,
    replay_environment_jsonl,
    write_environment_control,
)
from mas.lab.benchmark.environment.fixtures import FixtureRegistry
from mas.lab.benchmark.environment.hub import get_environment_hub
from mas.lab.benchmark.environment.model import EnvironmentSpec, RunCoordinates
from mas.lab.benchmark.environment.variables import Variables
from mas.lab.benchmark.pipeline.channel import get_channel, get_stream_id
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.library.standard.lib.environment_port import reset_environment_port, set_environment_port
from mas.runtime.boundary.obs.event_stream import get_event_stream

logger = logging.getLogger(__name__)


def _environment_block(obj: object | None) -> Mapping[str, Any]:
    if obj is None:
        return {}
    try:
        value = object.__getattribute__(obj, "environment")
    except AttributeError:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def blocks_for_run(*, exp: Any, scenario: Any, item: Mapping[str, Any] | None) -> dict[str, Any]:
    item_block = dict((item or {}).get("environment") or {})
    if "fixtures" in item_block:
        raise ValueError("dataset item environment cannot declare fixtures")
    return merge_environment(_environment_block(exp), _environment_block(scenario), item_block)


def digest_for_run(
    *,
    exp: Any,
    scenario: Any,
    item: Mapping[str, Any] | None,
    work_dir: Path | None = None,
) -> str:
    """Digest of the environment a run sees, including the script files its fixtures run.

    The command is in the spec digest; the file it names is not, so editing a fixture script
    would otherwise serve a stale cached trace.
    """
    block = blocks_for_run(exp=exp, scenario=scenario, item=item)
    digest = environment_digest(block)
    if not digest or work_dir is None:
        return digest
    spec = compile_environment(block)
    contents = fixture_file_digests(spec, Path(work_dir))
    if not contents:
        return digest
    blob = json.dumps({"spec": digest, "files": contents}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:20]


def validate_environments(*, exp: Any, plan: Sequence[tuple[str, Mapping[str, Any], int]]) -> None:
    """Compile every environment the batch will use, before the first run starts.

    A misspelled trigger, an item that declares fixtures, or a bad duration is reported once,
    with the scenario and item, instead of failing the run that happens to reach it.
    """
    seen: set[str] = set()
    for scenario_id, item, _run_idx in plan:
        key = f"{scenario_id}/{item.get('id', 0)}"
        if key in seen:
            continue
        seen.add(key)
        try:
            compile_environment(blocks_for_run(exp=exp, scenario=exp.get_scenario(scenario_id), item=item))
        except ValueError as exc:
            raise ValueError(f"environment of scenario {scenario_id!r}, item {item.get('id', 0)!r}: {exc}") from exc


def environment_violations(data: Mapping[str, Any], base_dir: Path | None = None) -> list[str]:
    """Semantic problems of the ``environment:`` blocks of an experiment manifest.

    The JSON schema checks shapes. These need the compiler: a timer that names a trigger that does
    not exist, a duplicate fixture, a bad duration, an item that declares fixtures. Found here, they
    fail ``validate`` and ``--dry-run`` instead of the first run that reaches them.
    """
    experiment = data.get("experiment") if isinstance(data.get("experiment"), Mapping) else None
    if experiment is None:
        return []
    problems: list[str] = []
    exp_block = experiment.get("environment") or {}
    items: list[tuple[str, Mapping[str, Any]]] = []
    dataset = experiment.get("dataset")
    path_text = dataset.get("path") if isinstance(dataset, Mapping) else None
    if path_text and base_dir is not None:
        try:
            import yaml

            target = (Path(base_dir) / str(path_text)).resolve()
            loaded = yaml.safe_load(target.read_text(encoding="utf-8")) if target.is_file() else None
            spec = loaded.get("spec") if isinstance(loaded, Mapping) else None
            for item in (spec or {}).get("items") or []:
                if isinstance(item, Mapping):
                    items.append((str(item.get("id", "?")), item))
        except (OSError, ValueError, AttributeError):
            items = []  # the dataset has its own checks; do not report its problems twice
    scenarios = [s for s in (experiment.get("scenarios") or []) if isinstance(s, Mapping)] or [{}]
    for scenario in scenarios:
        sid = str(scenario.get("id", "?"))
        try:
            base = merge_environment(exp_block, scenario.get("environment"))
            compile_environment(base)
        except (ValueError, TypeError) as exc:
            problems.append(f"environment of scenario {sid!r}: {exc}")
            continue  # every item inherits it: say it once
        for item_id, item in items:
            item_env = item.get("environment") or {}
            try:
                if "fixtures" in item_env:
                    raise ValueError("dataset item environment cannot declare fixtures")
                compile_environment(merge_environment(base, item_env))
            except (ValueError, TypeError) as exc:
                problems.append(f"environment of scenario {sid!r}, item {item_id!r}: {exc}")
    return problems


def refuse_environment_in_workers(*, exp: Any, plan: Sequence[tuple[str, Mapping[str, Any], int]]) -> None:
    """``executor.worker_processes > 1`` runs each MAS in a spawn child, which has no bound environment.

    The port, the fixtures, named actions, recorded replay and the event stream live in this
    process. A trigger would silently never fire in the child, so a batch that declares any
    environment is refused until it runs in-process.
    """
    for scenario_id, item, _run_idx in plan:
        block = blocks_for_run(exp=exp, scenario=exp.get_scenario(scenario_id), item=item)
        spec = compile_environment(block)
        if (
            spec.triggers
            or spec.fixtures
            or spec.script
            or spec.actions
            or spec.replay == "recorded"
            or spec.clock.kind != "wall"
        ):
            raise ValueError(
                f"environment of scenario {scenario_id!r} needs executor.worker_processes: 1; "
                "a worker process has no bound environment, so its triggers, fixtures, named "
                "actions and recorded replay would never run"
            )


def fixture_file_digests(spec: EnvironmentSpec, work_dir: Path) -> dict[str, str]:
    """``{relative path: sha256}`` of files a fixture command names, when they sit under *work_dir*."""
    root = work_dir.resolve()
    found: dict[str, str] = {}
    for fixture in spec.fixtures:
        for pattern in fixture.watch:
            for candidate in sorted(root.glob(pattern)):
                for path in [candidate, *sorted(candidate.rglob("*"))] if candidate.is_dir() else [candidate]:
                    resolved = path.resolve()
                    if path.is_file() and root in resolved.parents:
                        found[str(resolved.relative_to(root))] = _file_sha256(resolved)
        for part in fixture.command:
            # ``--script=fixtures/x.py`` names a file too: look at the value after the first ``=``.
            for text in dict.fromkeys((part, part.split("=", 1)[-1])):
                candidate = (root / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
                if candidate != root and root not in candidate.parents:
                    continue
                if candidate.is_file():
                    found[str(candidate.relative_to(root))] = _file_sha256(candidate)
                    # The script's own folder joins the key: a helper it imports sits beside it.
                    _add_folder(found, root, candidate.parent)
    return found


_FOLDER_FILE_CAP = 500
_FOLDER_SIZE_CAP = 5 << 20


def _add_folder(found: dict[str, str], root: Path, folder: Path) -> None:
    """Hash the small files of *folder* (not the experiment root itself), bounded."""
    if folder == root or root not in folder.parents:
        return
    count = 0
    for path in sorted(folder.rglob("*")):
        if "__pycache__" in path.parts or not path.is_file() or path.stat().st_size > _FOLDER_SIZE_CAP:
            continue
        count += 1
        if count > _FOLDER_FILE_CAP:
            logger.warning("fixture folder %s has more than %d files; the rest are not in the cache key", folder, count)
            return
        found.setdefault(str(path.relative_to(root)), _file_sha256(path))


def _file_sha256(path: Path) -> str:
    """Hash in chunks: a command can name a large binary, and this runs for every run of a batch."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def environment_jsonl_path(run_dir: Path) -> Path:
    return run_dir / "traces" / "environment.jsonl"


def publish_environment_record(record: dict[str, Any]) -> None:
    ch = get_channel()
    sid = get_stream_id()
    if ch is None or not sid:
        return
    payload = dict(record)
    payload.setdefault("kind", "custom.environment")
    try:
        ch.publish_sync(sid, payload)
    except RuntimeError:
        # The stream was sealed by an earlier batch in this process (same run key). The record is
        # already in environment.jsonl; a live view must not fail the run it draws.
        logger.debug("environment record not published: stream %s is sealed", sid)


def replay_cached_environment(run_dir: Path) -> None:
    replay_environment_jsonl(environment_jsonl_path(run_dir), publish_environment_record)


class BoundRun:
    def __init__(
        self,
        env: RunEnvironment,
        token: Any,
        key: RunKey | None,
        control_path: Path | None,
    ) -> None:
        self.env = env
        self._token = token
        self._key = key
        self.control_path = control_path

    def close(self) -> None:
        try:
            self.env.close()
        finally:
            reset_environment_port(self._token)
            if self._key is not None:
                get_environment_hub().unbind(self._key, self.env)


def run_variables(run: RunCoordinates, key: RunKey | None) -> dict[str, str]:
    """The run variables of a trigger's actions: experiment, scenario, item and run ids, and ``run.key``.

    ``run.key`` is the id of the run's event stream (``experiment/scenario/item/run#attempt``), the same string a
    shared service sees on the run's own requests, so one action can name the run it fires for.
    """
    found = {
        "experiment.id": run.experiment,
        "scenario.id": run.scenario,
        "item.id": run.item,
        "run.id": run.run,
        "run.attempt": str(run.attempt),
    }
    if key is not None:
        found["run.key"] = key.stream_id()
    return found


def bind_run_environment(
    *,
    spec: EnvironmentSpec,
    run: RunCoordinates,
    run_dir: Path,
    work_dir: Path,
    output_dir: Path,
    fixtures: FixtureRegistry,
    key: RunKey | None,
    session_id: str = "",
    control: Any = None,
) -> BoundRun:
    path = environment_jsonl_path(run_dir)
    env = RunEnvironment(
        spec,
        run=run,
        path=path,
        work_dir=work_dir,
        output_dir=output_dir,
        fixtures=fixtures,
        on_record=publish_environment_record,
        control=control,
        session_id=session_id,
        vars=Variables({**fixtures.vars, **run_variables(run, key)}),
    )
    token = set_environment_port(env)
    bound = BoundRun(env, token, key, None)
    try:
        if key is not None:
            get_environment_hub().bind(key, env)
        stream = get_event_stream()
        if stream is not None:
            env.attach_event_stream(stream)
        if spec.triggers or spec.script:
            bound.control_path = write_environment_control(
                run_dir / ".environment" / "control.yaml",
                script=spec.script,
            )
    except BaseException:
        bound.close()  # a half-bound run must not leave its port or hub entry behind
        raise
    return bound


def fixture_scope_key(*, scope: str, run_key: str, item_key: str, scenario_key: str) -> str:
    if scope == "run":
        return run_key
    if scope == "item":
        return item_key
    return scenario_key


def control_refs_for_run(*, loaded: Sequence[str], control_path: Path | None) -> list[str]:
    refs = [str(item) for item in loaded]
    if control_path is not None:
        path = str(control_path)
        if path not in refs:
            refs.append(path)
    return refs


# The secret the loopback model's proxy row names, as optional: a scripted model ignores the key.
FIXTURE_LLM_SECRET = "lab"


def write_fixture_llm_infra(path: Path, url: str, *, name: str = "openai-direct") -> Path:
    """Point the session LLM at a loopback OpenAI-compatible fixture.

    The proxy row is keyless: its ``api_key`` names :data:`FIXTURE_LLM_SECRET` as optional, so a
    run needs no secret in the store; one that is there is sent to the loopback fixture only.
    """
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    base = str(url).rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    doc = {
        "apiVersion": "infra/v1",
        "kind": "LLMProxy",
        "metadata": {"name": name},
        "spec": {
            "protocol": "openai",
            "proxy": {"api_base": base, "api_key": {"secret": {"name": FIXTURE_LLM_SECRET, "optional": True}}},
            "models": {
                "allowed": ["scripted", "any"],
                "mappings": {"default": "scripted", "any": "scripted"},
            },
        },
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def start_run_fixtures(
    spec: EnvironmentSpec,
    fixtures: FixtureRegistry,
    *,
    run_key: str,
    item_key: str,
    scenario_key: str,
    env: Any = None,
) -> None:
    keys: list[str] = []
    for item in spec.fixtures:
        key = fixture_scope_key(
            scope=item.scope,
            run_key=run_key,
            item_key=item_key,
            scenario_key=scenario_key,
        )
        keys.append(key)
        emit = getattr(getattr(env, "events", None), "emit", None)
        if callable(emit):
            fixtures.bind_emit(key, emit)
        if item.scope == "run":
            fixtures.start(item, scope_key=key)
        else:
            fixtures.start_once(item, scope_key=key)
        if env is None:
            continue
        try:
            url = fixtures.url(item.name, scope_key=key)
        except KeyError:
            continue
        env.vars[f"fixture.{item.name}.url"] = url
        env.vars[f"fixtures.{item.name}.url"] = url
        try:
            port = str(fixtures.port(item.name, scope_key=key))
            host = fixtures.host(item.name, scope_key=key)
        except KeyError:
            continue
        env.vars[f"fixture.{item.name}.port"] = port
        env.vars[f"fixture.{item.name}.host"] = host
        env.vars[f"fixtures.{item.name}.port"] = port
        env.vars[f"fixtures.{item.name}.host"] = host
    if env is not None:
        env.fixture_keys = tuple(dict.fromkeys(keys))


def stop_run_fixtures(
    spec: EnvironmentSpec,
    fixtures: FixtureRegistry,
    *,
    run_key: str = "",
    item_key: str = "",
    scenario_key: str = "",
) -> None:
    for item in spec.fixtures:
        if item.scope != "run":
            continue
        key = fixture_scope_key(
            scope=item.scope,
            run_key=run_key,
            item_key=item_key,
            scenario_key=scenario_key,
        )
        fixtures.release(item.name, force=False, scope_key=key)


def run_with_cleanups(body: Any) -> Any:
    """Call ``body(pending)``; if it raises, undo what it registered in ``pending``, newest first.

    The body appends a callable for each thing it acquires (event stream, bound environment,
    started fixtures) and clears the list once a try/finally of its own owns the cleanup.
    """
    pending: list[Any] = []
    try:
        return body(pending)
    except BaseException:
        while pending:
            try:
                pending.pop()()
            except Exception:
                logger.debug("run setup cleanup failed", exc_info=True)
        raise
