"""The real transport: `python -m forge_mcp` as a subprocess over stdio.

This is what Claude Code does with the `.mcp.json` entry, so it is the test that
proves the registration works: spawn the module, complete an MCP initialize
handshake, list tools, shut down.

The SDK's `stdio_client` spawns with CREATE_NO_WINDOW on Windows and puts the
child in a Job Object, so no console window appears and the process tree is
reaped on exit.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment

from .conftest import free_port
from .test_server_tools import EXPECTED_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_JSON = REPO_ROOT / ".mcp.json"


def server_parameters() -> StdioServerParameters:
    env = get_default_environment()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            # Keep the child away from the real backend ports even though a
            # handshake never dials them.
            "FORGE_BLENDER_PORT": str(free_port()),
            "FORGE_SERVICE_PORT": str(free_port()),
        }
    )
    return StdioServerParameters(
        command=sys.executable, args=["-m", "forge_mcp"], env=env
    )


def test_stdio_handshake_and_tool_listing() -> None:
    """Initialize + tools/list over a real subprocess, then a clean shutdown."""

    async def run():
        async with Client(server_parameters(), read_timeout_seconds=30) as client:
            info = client.server_info
            tools = (await client.list_tools()).tools
            return info, client.protocol_version, [t.name for t in tools]

    info, protocol_version, names = asyncio.run(run())

    assert info.name == "forge"
    assert protocol_version
    assert set(names) == EXPECTED_TOOLS


def test_module_entry_point_is_importable_without_running() -> None:
    """`python -m forge_mcp` must resolve; a typo here only shows up at spawn."""
    import forge_mcp
    import forge_mcp.__main__  # noqa: F401

    assert callable(forge_mcp.main)


# --- .mcp.json registration -------------------------------------------------


@pytest.mark.skipif(not MCP_JSON.is_file(), reason=".mcp.json not present")
def test_mcp_json_is_strict_json_and_launches_this_venv() -> None:
    config = json.loads(MCP_JSON.read_text(encoding="utf-8"))

    entry = config["mcpServers"]["forge"]
    assert entry["args"] == ["-m", "forge_mcp"]

    interpreter = Path(entry["command"])
    assert interpreter.is_absolute(), "an absolute path works from any launch directory"
    assert interpreter.is_file(), f"{interpreter} does not exist"

    # It must be this project's venv interpreter, or Claude Code spawns a Python
    # that cannot import forge_mcp and the server just shows up as failed.
    venv = REPO_ROOT / "mcp" / ".venv"
    assert venv in interpreter.resolve().parents, f"{interpreter} is outside {venv}"
