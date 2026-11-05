#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-slim``: join a SLIM node by hand, with no model and no MAS.

    printf %s "$SLIM_SECRET" | mas-slim listen --identity acme/lab/desk --agent billing
    printf %s "$SLIM_SECRET" | mas-slim send --identity acme/lab/desk --as triage billing "I was charged twice"

``listen`` links one agent and answers every request. ``send`` links as an
agent, sends one request, and prints the reply. Each command runs a
one-layer stack (``slim``), the same stack a Flavour binds to a ``slim``
infra row. Both read the shared secret from standard input (``--secret-file -``,
the default) or from a file. Neither reads the environment.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mas.library.standard.lib.stack import StackError, StackHandle
from mas.runtime.boundary.agentcomm.protocol import AgentCommError


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--identity", required=True, help="org/ns/service, as on the slim infra row")
    common.add_argument("--url", default="http://127.0.0.1:46357", help="the SLIM node")
    common.add_argument(
        "--secret-file", default="-", help="file holding the shared secret; '-' (the default) reads standard input"
    )
    common.add_argument("--timeout", type=float, default=3.0, help="seconds to wait for the node and for replies")

    parser = argparse.ArgumentParser(prog="mas-slim", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    listen = sub.add_parser("listen", parents=[common], help="link an agent and answer its requests")
    listen.add_argument("--agent", required=True, help="agent id to join as")
    listen.add_argument("--reply", default="", help="fixed reply (default: 'ack: <request>')")
    send = sub.add_parser("send", parents=[common], help="send one request and print the reply")
    send.add_argument("--as", dest="sender", required=True, help="agent id to join as")
    send.add_argument("target", help="an agent id, or the identity for anycast")
    send.add_argument("task", help="the request text")
    return parser


def _secret(path: str, stdin: TextIO) -> str:
    try:
        text = stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentCommError(f"cannot read the secret: {exc.strerror or exc}") from None
    secret = text.strip()
    if not secret:
        raise AgentCommError("no secret on --secret-file (the shared secret, at least 32 bytes)")
    return secret


def _stack(args: argparse.Namespace, answer: Callable[..., str], stdin: TextIO) -> StackHandle:
    params = {
        "url": args.url,
        "identity": args.identity,
        "secret": _secret(args.secret_file, stdin),
        "timeout_s": args.timeout,
        "reply_timeout_s": args.timeout,  # --timeout also bounds the wait for a reply
    }
    return StackHandle("mas-slim", [{"type": "slim", "params": params}], answer)


def main(argv: Sequence[str] | None = None, *, stop: threading.Event | None = None, stdin: TextIO | None = None) -> int:
    args = _parser().parse_args(argv)
    source = stdin if stdin is not None else sys.stdin
    try:
        if args.command == "listen":
            return _listen(args, stop or threading.Event(), source)
        return _send(args, source)
    except (AgentCommError, StackError) as exc:
        print(f"mas-slim: {exc}", file=sys.stderr)
        return 1


def _listen(args: argparse.Namespace, stop: threading.Event, stdin: TextIO) -> int:
    def answer(agent_id: str, task: str, *_rest: object) -> str:
        print(f"[{agent_id}] {task}", flush=True)
        return args.reply or f"ack: {task}"

    stack = _stack(args, answer, stdin)
    try:
        stack.link(args.agent)
        print(f"{args.agent} is listening as {stack.agent_name(args.agent)}", flush=True)
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
    finally:
        stack.close()
    return 0


def _send(args: argparse.Namespace, stdin: TextIO) -> int:
    stack = _stack(args, lambda *_: "", stdin)
    try:
        sender = stack.link(args.sender)
        print(sender.send(args.target, args.task))
    finally:
        stack.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
