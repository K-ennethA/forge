"""Command registry and uniform error wrapping for the Forge socket protocol.

Handlers are plain functions ``handler(params: dict) -> dict | None`` registered
with the :func:`command` decorator.  :func:`dispatch` is the only entry point the
socket server uses; it never raises, it returns a ``(status, result, message)``
triple matching the wire contract in ``docs/architecture.md``.

Undo checkpoints
----------------
Every command that can change the scene pushes a named undo step *before* it
runs, so Ctrl+Z in the viewport reverses what the assistant just did and the
Assistant box's "Revert last AI action" button is a real button rather than an
apology.  The step is named for the command ("Forge: remesh"), which is what
Blender shows in Edit > Undo History — the artist can see, in words, what is
about to be taken back.

Read-only commands are skipped: pushing a checkpoint for ``ping`` would bury the
one the artist actually wants under a pile of nothing.  ``execute_python`` is
deliberately NOT skipped — arbitrary code is exactly the case where a checkpoint
is worth having, even though the code may have done nothing at all.
"""

import traceback

__all__ = [
    "ForgeError",
    "command",
    "dispatch",
    "command_names",
    "has_command",
    "READ_ONLY_COMMANDS",
    "push_undo",
    "undo_message",
]


#: Commands that cannot change the .blend: they answer a question, or (in the
#: case of ``export_stl``) write a file that undo could never take back anyway.
#: Everything else — including ``execute_python`` — gets a checkpoint.
READ_ONLY_COMMANDS = frozenset({
    "ping",
    "get_scene_info",
    "flow_list",
    "rigforge_list_tags",
    "rigforge_status",
    "export_stl",
    # Looking at the scene is not changing it. render_preview borrows the render
    # settings and a camera and puts every one of them back, so an undo step for
    # it would take back whatever the artist actually wanted undone.
    "render_preview",
})

#: Flipped off the first time ``bpy.ops.ed.undo_push`` refuses, so a Blender
#: build (or a headless run) without an undo stack costs one warning rather than
#: one exception per command.  Trust features must never break the flows they
#: are meant to protect.
_UNDO_AVAILABLE = True


def undo_message(name):
    """What the artist reads in Edit > Undo History."""
    return "Forge: %s" % name


def push_undo(name):
    """Push a named undo checkpoint for ``name``. Returns True if one landed.

    Verified working in ``--background`` (Blender 5.0 pushes and pops happily
    with no window), but guarded regardless: this runs before every command, and
    a checkpoint that fails must cost the command nothing.
    """
    global _UNDO_AVAILABLE
    if not _UNDO_AVAILABLE or name in READ_ONLY_COMMANDS:
        return False
    try:
        import bpy

        bpy.ops.ed.undo_push(message=undo_message(name))
    except Exception as exc:  # noqa: BLE001 - never let a checkpoint fail a command
        _UNDO_AVAILABLE = False
        print("[Forge] undo checkpoints unavailable (%s: %s); commands still run."
              % (type(exc).__name__, exc))
        return False
    return True


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

    # Before the handler, not after: the checkpoint has to hold the scene as it
    # was, and a command that fails half-way through is exactly the one worth
    # being able to take back.
    push_undo(name)

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
