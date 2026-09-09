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
    # The workspace copilot (Phase 8).  None of these seven touch geometry, and
    # Blender does not put viewport state — the angle, the shading, the overlays
    # — in the undo stack at all, so a checkpoint here could only ever bury the
    # sculpt stroke the artist actually wants back.  `set_mode` and
    # `sculpt_brush` are in the same boat by intent rather than by API: a mode
    # switch and a brush size are the artist's tools, not their work, and an
    # undo history full of "Forge: set_mode" is an undo history nobody can use.
    # What replaces undo is transparency: every one of these returns a `changed`
    # line saying what is different and a `where` line naming the switch, so the
    # artist can put it back by hand and knows how next time.
    "set_view",
    "frame_object",
    "local_view",
    "set_shading",
    "set_overlays",
    "set_mode",
    "sculpt_brush",
    # Buddy mode's two eyes: one takes a picture, one counts defects.
    "capture_viewport",
    "mesh_diagnose",
    # The geometric gate. `verify_design` measures and changes nothing;
    # `turntable` borrows the render settings and a camera and puts every one of
    # them back, exactly as `render_preview` does — so both are here for exactly
    # the reasons those two are, and an undo step for either would take back
    # whatever the artist actually wanted undone.
    "verify_design",
    "turntable",
    # The deformation harness poses the rig into its extremes and puts every
    # bone back in a `finally`, so it is a measurement, not an edit — and an
    # undo step for it would bury whatever the animator actually wants back.
    "rig_check",
    # Phase 11's two samplers. They read a curve the artist drew and hand back
    # control points — nothing is built and nothing in the scene moves, so a
    # checkpoint for "I measured your drawing" would only bury the stroke
    # itself. (`merge_for_print` is deliberately NOT here: it builds an object
    # and hides the pieces it came from, which is exactly what Ctrl+Z is for.)
    "profile_from_curve",
    "outline_from_curve",
    # Phase 15's two. `save_project_blend` writes a file and changes nothing in
    # the session (it saves a *copy*) — the `export_stl` precedent exactly.
    # `open_project_blend` is here for the opposite reason: a file load resets
    # Blender's undo stack, so a checkpoint pushed just before one is a
    # checkpoint that cannot exist a moment later. What replaces undo there is
    # the confirmation round trip — the command asks before it destroys
    # anything, which is a better guarantee than an undo step that would be
    # gone by the time it was needed.
    "save_project_blend",
    "open_project_blend",
    # Phase 17's camera. `render_animation` borrows the render settings, the
    # frame range and a camera and puts every one of them back, exactly as
    # `render_preview` and `turntable` do — the only thing it leaves behind is
    # the .mp4, which undo could never take back anyway. Its two siblings,
    # `animate_object` and `set_material_emission`, are deliberately NOT here:
    # keys and materials are the artist's work, and Ctrl+Z is what a demo that
    # went the wrong way needs.
    "render_animation",
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
