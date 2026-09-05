"""Error types shared by the Forge MCP backends.

Every error raised out of a tool function is one of these; FastMCP turns the
message into the tool's error text, so the messages are written to be read by
a model and acted on without further digging.
"""

from __future__ import annotations


class ForgeError(RuntimeError):
    """Base class for every error the Forge MCP server surfaces to the caller."""


class BackendUnavailable(ForgeError):
    """A backend process is not listening (Blender add-on or geometry service)."""


class BackendError(ForgeError):
    """The backend was reached but reported a failure, or spoke garbage."""


BLENDER_DOWN = (
    "Blender is not running or the Forge add-on server is stopped "
    "— start it from the Forge panel (View3D sidebar > Forge > Start Server), "
    "then retry. Expected a listener on {address}."
)

SERVICE_DOWN = (
    "The Forge geometry service is not running — start it from the repo's "
    "service/ directory (see service/README.md), then retry. "
    "Expected an HTTP server on {address}."
)
