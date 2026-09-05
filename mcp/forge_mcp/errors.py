"""Error types shared by the Forge MCP backends.

Every error raised out of a tool function is one of these, and the messages are
written to be read by a model and acted on without further digging.

Getting that message to the model is why ForgeError derives from the SDK's
``ToolError``. mcp 2.x splits tool failures in two (see
``mcp.server.mcpserver.exceptions``): a ``ToolError`` is *anticipated*, so its
text is returned to the caller in the ``is_error`` result and the server logs it
at INFO; anything else is a *crash*, wrapped in ``UnexpectedToolError`` — the
caller is told only "Error executing tool <name>" and the real message plus a
traceback go to the server's stderr, where the model never sees them. A plain
``RuntimeError`` here would silently turn every "Blender is not running" into
that useless generic string.

Genuine bugs (AttributeError, KeyError, ...) are deliberately left to the crash
path: those *should* stay on the server rather than leak a traceback.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class ForgeError(ToolError):
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
