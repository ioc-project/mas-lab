#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A stand-in for an external agent harness: standard library only, speaks the stdio task protocol.

``stub_harness.py line`` answers each input line with ``echo:<line>``. ``stub_harness.py jsonrpc``
answers each JSON-RPC ``task`` request with an ``event`` notification and a result whose text is
``echo:<text>``. Whatever the framing, a few task texts make it misbehave on purpose:

* ``ENV``: reply with the sorted names of the environment variables it was started with
* ``PID``: reply with its own process id
* ``SLEEP``: never reply
* ``BIG``: reply with one very long line
* ``MANY`` (jsonrpc): send two hundred short ``event`` notifications before the reply
* ``DIE``: exit with status 3 without a reply
* ``GARBAGE`` (jsonrpc): write a line that is not JSON
"""

from __future__ import annotations

import json
import os
import sys
import time


def _answer(text: str) -> str:
    if text == "ENV":
        return ",".join(sorted(os.environ))
    if text == "PID":
        return str(os.getpid())
    if text == "SLEEP":
        time.sleep(3600)
    if text == "BIG":
        return "x" * 300_000
    if text == "DIE":
        sys.exit(3)
    return f"echo:{text}"


def main() -> None:
    framing = sys.argv[1]
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if framing == "line":
            sys.stdout.write(_answer(line) + "\n")
            sys.stdout.flush()
            continue
        request = json.loads(line)
        text = request["params"]["text"]
        if text == "GARBAGE":
            sys.stdout.write("not json\n")
            sys.stdout.flush()
            continue
        event = {"jsonrpc": "2.0", "method": "event", "params": {"kind": "tool_call", "name": "grep", "task": text}}
        for _ in range(200 if text == "MANY" else 1):
            sys.stdout.write(json.dumps(event) + "\n")
        reply = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"text": _answer(text), "session_id": request["params"]["session_id"]},
        }
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
