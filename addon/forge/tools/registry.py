"""Command registry and uniform error wrapping for the Forge socket protocol.

Handlers are plain functions ``handler(params: dict) -> dict | None`` registered
with the :func:`command` decorator.  :func:`dispatch` is the only entry point the
socket server uses; it never raises, it returns a ``(status, result, message)``
triple matching the wire contract in ``docs/architecture.md``.
"""

import traceback

__all__ = [
    "ForgeError",
    "command",
    "dispatch",
    "command_names",
    "has_command",
]


class ForgeError(Exception):
    """An expected, user-facing failure.

    Raised by handlers for bad parameters, missing objects, unsupported modes and
    similar.  Reported to the client as ``status: "error"`` with just the message
    (no traceback) because the traceback would add nothing.
    """


# name -> handler
_HANDLERS = {}


def command(name):
    """Decorator registering ``name`` as a protocol command."""

    def decorator(func):
        _HANDLERS[name] = func
        func.forge_command = name
        return func

    return decorator


def command_names():
    """Sorted list of registered command names."""
    return sorted(_HANDLERS)


def has_command(name):
    return name in _HANDLERS


def get_handler(name):
    return _HANDLERS.get(name)


def dispatch(name, params):
    """Run a command. Returns ``(status, result, message)`` and never raises.

    ``status`` is ``"success"`` or ``"error"``; ``result`` is a JSON-safe dict
    (``None`` on error); ``message`` is the human-readable error (``""`` on
    success).  Unexpected exceptions carry their full traceback in the message so
    the caller (Claude, via the MCP server) can act on it without a Blender
    console.
    """
    if not isinstance(name, str) or not name.strip():
        return (
            "error",
            None,
            "Request is missing a 'type' field. Known commands: %s"
            % ", ".join(command_names()),
        )
    name = name.strip()
    handler = _HANDLERS.get(name)
    if handler is None:
        return (
            "error",
            None,
            "Unknown command %r. Known commands: %s" % (name, ", ".join(command_names())),
        )
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return (
            "error",
            None,
            "'params' must be a JSON object, got %s." % type(params).__name__,
        )

    try:
        result = handler(params)
    except ForgeError as exc:
        return "error", None, str(exc) or exc.__class__.__name__
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all, server must survive
        return (
            "error",
            None,
            "%s in command %r: %s\n%s"
            % (type(exc).__name__, name, exc, traceback.format_exc()),
        )

    if result is None:
        result = {}
    elif not isinstance(result, dict):
        result = {"value": result}
    return "success", result, ""
