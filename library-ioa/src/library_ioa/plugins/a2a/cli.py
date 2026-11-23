from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Sequence

from a2a.utils.constants import TransportProtocol
from mas.runtime.entrypoint import install_host_environment

from library_ioa.plugins.a2a.client import A2AClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mas-a2a",
        description="Send one text message to an A2A agent.",
    )
    parser.add_argument("--a2a", required=True, help="Base URL of the A2A agent.")
    parser.add_argument(
        "--a2a-transport",
        choices=[protocol.value for protocol in TransportProtocol],
        default=TransportProtocol.JSONRPC.value,
        help="Binding to prefer when the AgentCard advertises it.",
    )
    parser.add_argument("--message", required=True, help="Text message to send.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    install_host_environment()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        client = A2AClient(url=args.a2a, transport=args.a2a_transport)
        response = asyncio.run(client.asend_message(args.message))
    except Exception as error:
        print(f"mas-a2a: {error}", file=sys.stderr)
        return 1

    print(json.dumps(response, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
