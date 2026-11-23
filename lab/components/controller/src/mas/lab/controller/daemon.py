#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Controller daemon — Unix socket IPC + optional HTTP."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import threading
from pathlib import Path
from typing import Any

from mas.lab.controller import config as cfg
from mas.lab.controller.api import ControllerAPI, set_controller_api
from mas.lab.controller.config import DEFAULT_HTTP_PORT, ensure_mas_dirs
from mas.lab.controller.jobs import job_error_text, yaml_error_in_chain
from mas.lab.controller.sessions import SessionRegistry
from mas.runtime import hostenv
from mas.runtime.aio import new_thread

logger = logging.getLogger(__name__)

_shutdown = threading.Event()
_api: ControllerAPI | None = None
_sessions = SessionRegistry()
_code_fingerprint: str | None = None
_env_fingerprint: dict[str, str] | None = None


def _serve_http(*, host: str, port: int, settings=None) -> None:
    import uvicorn
    from mas.lab.controller.fastapi_app import create_app

    app = create_app(settings) if settings is not None else create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


def _handle_request(payload: dict) -> dict:
    global _api
    assert _api is not None
    method = payload.get("method")
    params = payload.get("params") or {}

    dispatch = {
        "ping": lambda _p: {"status": "ok", "code": _code_fingerprint, "env": _env_fingerprint},
        "status": lambda _p: _api.status(),
        "shutdown": lambda _p: _shutdown.set() or {"status": "stopping"},
        "acquire_session": lambda p: _sessions.acquire(p["session_id"]) or {"ok": True},
        "release_session": lambda p: _sessions.release(p["session_id"]) or {"ok": True},
        "list_workers": lambda p: _api.list_workers(kind=p.get("kind"), status=p.get("status")),
        "get_worker": lambda p: _api.get_worker(
            p["worker_id"],
            stdout_from=int(p.get("stdout_from") or 0),
            stderr_from=int(p.get("stderr_from") or 0),
        ),
        "cancel_worker": lambda p: {"ok": _api.cancel_worker(p["worker_id"])},
        "submit_benchmark": lambda p: _api.submit_benchmark(p),
        "submit_application": lambda p: _api.submit_application(p),
        "submit_pipeline": lambda p: _api.submit_pipeline(p),
        "list_libraries": lambda _p: _api.list_libraries(),
        "list_runtime_runners": lambda _p: _api.list_runtime_runners(),
    }
    if method not in dispatch:
        return {"error": f"unknown method: {method}"}
    try:
        return {"result": dispatch[method](params)}
    except Exception as exc:
        if yaml_error_in_chain(exc) is None:
            logger.exception("RPC %s failed", method)
        else:
            logger.error("RPC %s failed: %s", method, job_error_text(exc))
        return {"error": job_error_text(exc)}


def _serve_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    # Owner only, before listen(): any process that can connect can submit jobs.
    os.chmod(path, 0o600)
    server.listen(5)
    logger.info("Controller IPC listening on %s", path)

    while not _shutdown.is_set():
        server.settimeout(1.0)
        try:
            conn, _addr = server.accept()
        except TimeoutError:
            continue
        except OSError:
            break
        with conn:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            if not data:
                continue
            line = data.split(b"\n", 1)[0]
            try:
                payload = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                try:
                    conn.sendall(json.dumps({"error": "invalid json"}).encode("utf-8") + b"\n")
                except OSError:
                    pass
                continue
            response = _handle_request(payload)
            conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
    server.close()
    path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    from mas.runtime.entrypoint import install_host_environment

    install_host_environment()  # an entry point: snapshot into hostenv, process reduced to the allowlist
    parser = argparse.ArgumentParser(description="MAS Lab controller daemon")
    parser.add_argument("--port", type=int, default=int(hostenv.get("MAS_CONTROLLER_PORT", DEFAULT_HTTP_PORT)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--socket", type=Path, default=None)
    parser.add_argument("--no-http", action="store_true")
    parser.add_argument("--cors-origin", action="append", default=[])
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--secrets-dir", default=None, help="File-store directory (default <workspace>/.mas/secrets)")
    parser.add_argument("--secret-store", default=None, help="file (default) or env")
    args = parser.parse_args(argv)
    if args.workspace is not None:
        hostenv.set_overlay({"MAS_WORKSPACE_ROOT": str(Path(args.workspace).expanduser().resolve())})
    socket = args.socket or cfg.socket_path()

    from mas.lab.controller.settings import ControllerSettings, load_token_file

    token = load_token_file(args.token_file) if args.token_file else None
    http_settings = ControllerSettings(
        host=args.host,
        port=args.port,
        cors_origins=list(args.cors_origin or []),
        token=token,
        state_dir=args.state_dir or (cfg.controller_dir() / "state"),
    )

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ensure_mas_dirs()

    global _api, _code_fingerprint, _env_fingerprint
    _code_fingerprint = cfg.code_fingerprint()
    _env_fingerprint = cfg.env_fingerprint()
    try:
        from mas.lab.workspace import WorkspaceConfig

        workspace = WorkspaceConfig.load()
    except Exception:
        workspace = None
    from mas.lab.secret_host import SecretHostSettings

    # The one store of the daemon; a job that names another is refused.
    SecretHostSettings.from_workspace(workspace, directory=args.secrets_dir, store=args.secret_store).install_process()
    _api = ControllerAPI(workspace)
    set_controller_api(_api)

    cfg.pid_path().write_text(str(os.getpid()), encoding="utf-8")

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Received signal %s — shutting down", signum)
        _shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    threads = [
        new_thread(target=_serve_socket, args=(socket,), daemon=True, name="ipc"),
    ]
    if not args.no_http:
        threads.append(
            new_thread(
                target=_serve_http,
                kwargs={"host": args.host, "port": args.port, "settings": http_settings},
                daemon=True,
                name="http",
            )
        )
    for thread in threads:
        thread.start()

    logger.info("MAS Lab controller running (HTTP :%s)", args.port)
    try:
        while not _shutdown.is_set():
            if _sessions.should_shutdown(_api):
                logger.info("Idle shutdown — no client sessions and no active workers")
                _shutdown.set()
                break
            _shutdown.wait(timeout=5.0)
    finally:
        cfg.pid_path().unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
