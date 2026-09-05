"""Error types for the Forge geometry service.

The HTTP error contract (``docs/architecture.md``) is:

* HTTP 400 with ``{"error": ..., "traceback": ...}`` for script / parameter
  failures -- i.e. anything the caller can fix by editing the script or the
  overrides.
* HTTP 500 for service bugs -- anything that is our fault.

Every error raised inside the service should therefore be one of the classes
below so the FastAPI exception handlers can map it without guessing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ForgeError(Exception):
    """Base class carrying an HTTP status and the wire payload shape."""

    http_status: int = 500

    def __init__(self, message: str, traceback_text: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.traceback_text = traceback_text

    def to_payload(self) -> Dict[str, Any]:
        """Exact body shape from the architecture contract."""
        return {"error": self.message, "traceback": self.traceback_text}

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ScriptError(ForgeError):
    """The user's script (or its parameters) is at fault -> HTTP 400."""

    http_status = 400


class ParamError(ScriptError):
    """A PARAMS schema problem or a bad override -> HTTP 400."""


class TimeoutError_(ScriptError):
    """A script exceeded the wall-clock budget -> HTTP 400.

    Named with a trailing underscore so it never shadows the builtin
    ``TimeoutError`` in modules that do ``from .errors import *``.
    """


class ServiceError(ForgeError):
    """The service itself broke (worker crash, protocol desync) -> HTTP 500."""

    http_status = 500


__all__ = [
    "ForgeError",
    "ScriptError",
    "ParamError",
    "TimeoutError_",
    "ServiceError",
]
