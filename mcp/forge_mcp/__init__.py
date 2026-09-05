"""Forge MCP server package.

Exposes the Blender add-on (TCP 9876) and the Build123d geometry service
(HTTP 8765) to Claude Code as one stdio MCP tool surface.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__", "main"]


def main() -> None:
    """Run the stdio MCP server (see forge_mcp.server)."""
    from .server import main as _main

    _main()
