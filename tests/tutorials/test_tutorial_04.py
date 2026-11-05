#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 04 — MCP tools: expose one tool over MCP, discover and call it,
then mix a local and an MCP provider in the same agent — all offline,
using the tutorial's own documented ``mas-mcp`` commands.

Tutorial 4 has no files of its own: it reuses Tutorial 01's agent.yaml plus
library-samples/tools/*.tool.yaml and library-samples/infra/mixed-tools.infra.yaml.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from tutorial_support import REPO_ROOT, T01, free_port, load_yaml, run_cli

WEB_SEARCH_TOOL = REPO_ROOT / "library-samples" / "tools" / "web-search.tool.yaml"
MIXED_TOOLS_INFRA = REPO_ROOT / "library-samples" / "infra" / "mixed-tools.infra.yaml"


def _mas_mcp_exe() -> str:
    venv_bin = Path(sys.executable).parent
    exe = venv_bin / "mas-mcp"
    return str(exe) if exe.exists() else "mas-mcp"


class TestManifestValidation:
    def test_validate_reused_agent_with_tools_overlay(self):
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(T01 / "agent.yaml"),
                "--overlay",
                str(T01 / "overlays" / "tools.yaml"),
            ]
        )
        assert r.returncode == 0, r.stderr


class TestMixedToolsInfra:
    def test_mixed_tools_infra_declares_local_and_mcp_providers(self):
        infra = load_yaml(MIXED_TOOLS_INFRA)
        servers = {s["id"]: s for s in infra["spec"]["tool_servers"]}
        assert servers["local-tools"]["protocol"] == "local"
        assert servers["mcp-web-search"]["protocol"] == "mcp"
        assert servers["mcp-web-search"]["url"].startswith("http://127.0.0.1:")


class TestMCPServeDiscoverDispatch:
    """docs/tutorials/04-mcp-tools/README.md § Start the MCP provider."""

    def test_serve_list_and_call_web_search(self):
        port = free_port()
        proc = subprocess.Popen(
            [
                _mas_mcp_exe(),
                "serve",
                "--tool-manifest",
                str(WEB_SEARCH_TOOL),
                "--tool",
                "web-search",
                "--transport",
                "streamable-http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            deadline = time.monotonic() + 20
            listed = None
            while time.monotonic() < deadline:
                listed = run_cli(["mas-mcp", "tools", "list", "--url", url], timeout=5)
                if listed.returncode == 0:
                    break
                time.sleep(0.5)
            assert listed is not None and listed.returncode == 0, (
                listed.stderr if listed else "mas-mcp never became reachable"
            )
            assert "web-search" in listed.stdout

            called = run_cli(
                [
                    "mas-mcp",
                    "tools",
                    "call",
                    "--url",
                    url,
                    "--tool",
                    "web-search",
                    "--arguments",
                    '{"query":"MAS-Lab"}',
                ],
                timeout=10,
            )
            assert called.returncode == 0, called.stderr
            assert called.stdout.strip()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


class TestMixedLocalAndMCPDispatch:
    """docs/tutorials/04-mcp-tools/README.md § Mix local and MCP tools.

    Discovery builds a name-to-provider registry; dispatch routes each tool
    name to exactly one provider. Proven directly against the registry/
    provider machinery, without a live LLM.
    """

    def test_mcp_provider_claims_web_search_local_keeps_calc(self):
        import asyncio

        from library_ioa.plugins.mcp.client import MCPClient
        from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider

        port = free_port()
        proc = subprocess.Popen(
            [
                _mas_mcp_exe(),
                "serve",
                "--tool-manifest",
                str(WEB_SEARCH_TOOL),
                "--tool",
                "web-search",
                "--transport",
                "streamable-http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            client = MCPClient(url=f"http://127.0.0.1:{port}/mcp")
            deadline = time.monotonic() + 20
            mcp_names: list[str] = []
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    mcp_names = [t["name"] for t in asyncio.run(client.list_tools())]
                    if mcp_names:
                        break
                except Exception as exc:  # noqa: BLE001 - server still starting
                    last_error = exc
                time.sleep(0.5)
            assert "web-search" in mcp_names, last_error

            # The local provider still resolves calc — never advertised by MCP.
            local_provider = build_manifest_tool_provider([{"ref": "samples:tools/calc.tool.yaml"}], T01)
            local_names = [t["name"] for t in local_provider.list_tools()]
            assert "calc" in local_names
            assert "web-search" not in local_names
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
