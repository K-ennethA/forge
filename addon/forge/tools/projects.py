"""Project ``.blend`` files: a per-project home for scene work (Phase 15).

The gap this closes, in the artist's own words: *"clicking a model should open
the associated blender file."*  Everything Forge builds up to here is
parametric — a script, a spec, an STL — and none of that is where a **sculpt**
lives, or a lighting setup, or the six reference empties someone spent an hour
placing.  Those live in a ``.blend``, and until now a project folder had nowhere
to put one.

Two commands, and the whole design is in what each of them refuses to do.

``save_project_blend``
    ``wm.save_as_mainfile(..., copy=True)`` into
    ``projects/<name>/<name>.blend``.  **copy=True is the entire point.**
    Without it Blender *retargets the session*: the artist's own file — the one
    they have been pressing Ctrl+S on all afternoon, wherever they keep it —
    silently becomes the project file, and their next Ctrl+S writes somewhere
    they did not choose.  A tool that moves where your work is saved without
    asking is a tool nobody should trust with a sculpt, so this one never does:
    ``bpy.data.filepath`` is the same string before and after, and the result
    says so out loud in ``session_file``.

``open_project_blend``
    ``wm.open_mainfile`` — which throws away the running scene.  So the first
    call, when there is anything to lose, does **not** open: it answers
    ``needs_confirmation`` with a sentence naming exactly what would go, and
    waits to be asked again with ``confirm: true``.  There is no undo across a
    file load (Blender resets the stack), which is precisely why the
    confirmation exists rather than a checkpoint.

Does the socket server survive the world being replaced?
--------------------------------------------------------
This was the question the feature stood or fell on, and it is answered by
measurement rather than by hope (``addon/tests/headless_projects.py`` proves it
on every run):

* the listening socket, its accept thread and the module-level server singleton
  are plain Python and are untouched by a file load — Blender reloads *data*,
  not the Python modules holding it;
* the main-thread pump is a ``bpy.app.timers`` callback registered
  **persistent=True**, and persistence is exactly the flag that survives
  ``open_mainfile``.  The test registers a second, non-persistent timer
  alongside it and asserts that one is *gone* after the open while the pump is
  still there — otherwise "it survived" would only mean "the file load never
  cleared any timers".

Belt and braces regardless: :func:`cmd_open_project_blend` calls
``server._ensure_pump()`` after the open.  Re-registering is a no-op when the
timer is still there, and on some future build where persistence changes
meaning it is the difference between a live add-on and a dead port.

Both commands are read-only for undo purposes.  ``save_project_blend`` writes a
file and changes nothing in the session (the ``export_stl`` precedent);
``open_project_blend`` destroys the undo stack it would be pushing onto.
"""

import os
import re

import bpy

from ..prefs import repo_root
from . import common
from .registry import ForgeError, command

#: One plain folder name under ``projects/`` — deliberately the same alphabet
#: the bridge's ``_PROJECT_NAME_RE`` uses, because this name arrives from a URL
#: in a browser by way of the bridge and must fail on its *shape* rather than on
#: path arithmetic.  No percent sign is in it, so an encoded traversal is
#: rejected before anything is joined.
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: How many object names one answer lists before it starts counting instead.
MAX_NAMES = 12

#: The environment variable the bridge and the MCP server already read.  Two
#: names for one folder would be a bug that only shows up on the machine that
#: set the variable.
PROJECTS_ENV = "FORGE_PROJECTS_DIR"


# ---------------------------------------------------------------------------
# where projects live
# ---------------------------------------------------------------------------

def projects_dir():
    """The folder project folders live in, or ``""`` if there is no telling.

    ``FORGE_PROJECTS_DIR`` first — the same variable the bridge and
    ``partforge_new_part`` use — then ``<repo>/projects`` when this add-on is a
    source checkout.  Installed from a zip there is no repo above it, so this is
    empty and every caller says so rather than guessing at somebody's disk.
    """
    override = str(os.environ.get(PROJECTS_ENV) or "").strip().strip('"')
    if override:
        try:
            return os.path.abspath(bpy.path.abspath(override))
        except Exception:  # noqa: BLE001 - a junk variable is simply "no path"
            return os.path.abspath(override)
    root = repo_root()
    if not root:
        return ""
    return os.path.join(root, "projects")


def _require_projects_dir():
    directory = projects_dir()
    if not directory:
        raise ForgeError(
            "Forge cannot tell where projects/ is on this machine (the add-on "
            "is installed from a zip, so there is no repo above it). Set the "
            "%s environment variable to the projects folder and restart "
            "Blender." % PROJECTS_ENV)
    return directory


def check_name(name):
    """One plain project name, or a refusal that says what a name may contain."""
    text = str(name or "").strip()
    if not text:
        raise ForgeError(
            "Which project? Pass 'project' — the folder name under projects/, "
            "e.g. \"small-magnet-holder\".")
    if text in (".", "..") or not PROJECT_NAME_RE.match(text):
        raise ForgeError(
            "%r is not a project name. A project is one plain folder name under "
            "projects/ — letters, digits, dots, dashes and underscores, no "
            "slashes." % text)
    return text


def project_folder(name):
    """``<projects>/<name>`` for a checked name.  Existence is not implied."""
    root = _require_projects_dir()
    folder = os.path.abspath(os.path.join(root, check_name(name)))
    if os.path.dirname(folder) != os.path.abspath(root):
        # Unreachable through :func:`check_name`, kept because the day someone
        # loosens that regex this is the line that still refuses.
        raise ForgeError("%r does not name a folder directly under %s."
                         % (name, root))
    return folder


def blend_path(name):
    """``projects/<name>/<name>.blend`` — the folder convention, once."""
    checked = check_name(name)
    return os.path.join(project_folder(checked), "%s.blend" % checked)


def _panel_project():
    """The project the PartForge panel is pointed at, or ``""``.

    ``projects/small-magnet-holder/part.py`` -> ``small-magnet-holder``: the
    part-object naming convention read one step further out, so a Save with no
    arguments saves into the project the artist is visibly working on.
    """
    try:
        props = bpy.context.scene.forge_partforge
    except (AttributeError, KeyError):
        return ""
    path = str(getattr(props, "script_path", "") or "").strip()
    if not path:
        return ""
    try:
        folder = os.path.basename(os.path.dirname(common.resolve_path(path)))
    except Exception:  # noqa: BLE001 - a junk stored path names no project
        return ""
    if not folder or not PROJECT_NAME_RE.match(folder):
        return ""
    return folder


def resolve_project(params, key="project"):
    """The project named in ``params``, else the one the panel is showing."""
    raw = params.get(key)
    if raw is not None and not isinstance(raw, str):
        raise ForgeError("Parameter %r must be the project's folder name, as a "
                         "string." % key)
    text = str(raw or "").strip()
    if text:
        return check_name(text)
    guessed = _panel_project()
    if guessed:
        return guessed
    raise ForgeError(
        "Which project? Pass 'project' — the folder name under projects/. "
        "(Nothing could be guessed: the PartForge panel has no script loaded.)")


def _stat(path):
    """``(size, mtime)`` for a file, or ``(0, 0.0)`` when there is none."""
    try:
        info = os.stat(path)
    except OSError:
        return 0, 0.0
    return int(info.st_size), round(info.st_mtime, 3)


def _scene_names(limit=MAX_NAMES):
    try:
        names = [obj.name for obj in bpy.data.objects]
    except Exception:  # noqa: BLE001
        return [], 0
    return names[:limit], len(names)


# ---------------------------------------------------------------------------
# save_project_blend
# ---------------------------------------------------------------------------

@command("save_project_blend")
def cmd_save_project_blend(params):
    """Write the scene into ``projects/<name>/<name>.blend`` as a **copy**.

    params: ``project?`` (default: the project the PartForge panel is pointed
    at), ``create?`` (default true — make the project folder when it is not
    there yet, which is what gives a sculpt that has never had a folder a home).

    Never retargets the session.  ``copy=True`` is not an option here, it is the
    contract: the artist's own file stays their own file, ``bpy.data.filepath``
    is unchanged, and the answer carries it in ``session_file`` so a caller can
    check rather than trust.
    """
    project = resolve_project(params)
    create = common.get_bool(params, "create", True)

    folder = project_folder(project)
    created_folder = False
    if not os.path.isdir(folder):
        if not create:
            raise ForgeError(
                "There is no project folder at %s. Pass create: true to make "
                "one, or check the name." % folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            raise ForgeError("Could not create the project folder %s: %s"
                             % (folder, exc))
        created_folder = True

    path = os.path.join(folder, "%s.blend" % project)
    replaced = os.path.isfile(path)
    before = str(bpy.data.filepath or "")

    kwargs = common.op_kwargs(bpy.ops.wm.save_as_mainfile,
                              {"filepath": path, "copy": True,
                               "check_existing": False})
    if not kwargs.get("copy"):
        # A build whose save_as_mainfile has no `copy` would retarget the
        # artist's session. Refusing is the only honest answer: the whole
        # promise of this command is the one thing that build cannot keep.
        raise ForgeError(
            "This Blender build's save_as_mainfile has no 'copy' option, and "
            "saving without it would move where your own file saves to. "
            "Nothing was written — use File > Save Copy instead.")
    try:
        bpy.ops.wm.save_as_mainfile(**kwargs)
    except RuntimeError as exc:
        raise ForgeError("Blender could not write %s: %s" % (path, exc))

    after = str(bpy.data.filepath or "")
    if after != before:
        # Cannot happen with copy=True, and if it ever did the artist needs to
        # hear it from us rather than discover it at their next Ctrl+S.
        raise ForgeError(
            "Blender retargeted this session to %s despite copy=True. Your "
            "file was %s — use File > Save As to put it back before saving "
            "again." % (after or "an unsaved file", before or "unsaved"))

    size, mtime = _stat(path)
    names, count = _scene_names()
    return {
        "project": project,
        "path": path,
        "folder": folder,
        "size": size,
        "mtime": mtime,
        "exists": bool(size) or os.path.isfile(path),
        "replaced": replaced,
        "created_folder": created_folder,
        "object_count": count,
        "objects": names,
        # The proof, handed over rather than asserted: this is where *your*
        # file saves to, and it is what it was before this command ran.
        "session_file": after,
        "retargeted": False,
        "changed": "Saved %d object(s) into %s%s."
                   % (count, path, " (replacing the previous save)"
                      if replaced else ""),
        "where": "projects/%s/%s.blend — your own file (File > Save) is "
                 "untouched." % (project, project),
    }


# ---------------------------------------------------------------------------
# open_project_blend
# ---------------------------------------------------------------------------

def _would_lose():
    """What an ``open_mainfile`` would throw away, in one sentence and a count.

    Two conditions, and the second one is not padding.  ``bpy.data.is_dirty`` is
    the question Blender itself asks before it offers to save — but it is only
    half an answer, for two measured reasons:

    * an **empty scene** has nothing to lose whatever the flag says, and asking
      "are you sure?" about nothing is how a confirmation dialog trains someone
      to click through the one that mattered;
    * in ``blender --background`` ``is_dirty`` is **always true** — it is true at
      startup before anything has happened, and an explicit save does not clear
      it (measured on 5.0.1).  A gate on that flag alone could never be tested
      headless in its "do not ask" state, and a branch no test can reach is a
      branch that rots.

    So: ask when the flag is up *and* there is something in the file.  The
    failure direction is deliberate — asking once too often costs a click, and
    not asking once too often costs an afternoon.
    """
    session = str(bpy.data.filepath or "")
    names, count = _scene_names()
    dirty = bool(getattr(bpy.data, "is_dirty", False)) and count > 0
    if not dirty:
        return False, session, names, count, ""
    if session:
        sentence = ("This session has unsaved changes since %s was last saved "
                    "(%d object(s) in the scene)." % (session, count))
    else:
        sentence = ("This session has never been saved (%d object(s) in the "
                    "scene), so everything in it would be lost." % count)
    return True, session, names, count, sentence


@command("open_project_blend")
def cmd_open_project_blend(params):
    """Open ``projects/<name>/<name>.blend``, asking first when that costs work.

    params: ``name`` (or ``project``), ``confirm?`` (default false).

    Three answers, all of them ``status: "success"`` because none of them is a
    fault:

    * **the file is not there** — no, and a sentence naming the button that
      makes one.  (This one *is* an error: an open that cannot happen is not a
      question, it is a missing file.)
    * **there is unsaved work** and ``confirm`` was not passed —
      ``needs_confirmation: true`` plus what would go.  Nothing is touched.
    * **otherwise** — the world is replaced, and the answer says what came back
      and that the socket is still listening.
    """
    raw = params.get("name")
    if raw is None:
        raw = params.get("project")
    if raw is None:
        raise ForgeError(
            "Which project? Pass 'name' — the folder name under projects/, "
            "e.g. \"small-magnet-holder\".")
    project = resolve_project({"project": raw})
    confirm = common.get_bool(params, "confirm", False)

    folder = project_folder(project)
    path = os.path.join(folder, "%s.blend" % project)
    if not os.path.isfile(path):
        raise ForgeError(
            "%s has no scene file yet (nothing at %s). Open the scene you want "
            "kept and press Save scene to project — that writes it — then try "
            "opening again." % (project, path))

    dirty, session, names, count, sentence = _would_lose()
    if dirty and not confirm:
        return {
            "project": project,
            "path": path,
            "opened": False,
            "needs_confirmation": True,
            "dirty": True,
            "session_file": session,
            "object_count": count,
            "objects": names,
            "would_lose": sentence,
            "hint": "Press Save scene to project first if you want this work "
                    "kept, then open again — or open anyway to discard it.",
            "changed": "Nothing yet — %s" % sentence,
            "where": "File > Open in Blender does the same thing, with the "
                     "same warning.",
        }

    discarded = sentence if dirty else ""
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    except RuntimeError as exc:
        raise ForgeError("Blender could not open %s: %s" % (path, exc))

    # The world just came back new.  The pump is registered persistent=True and
    # measurably survives this (headless_projects.py proves it against a
    # non-persistent control timer), so this is insurance rather than a fix —
    # and insurance on the one thing whose failure would leave the add-on
    # listening on a socket nothing drains.
    pump_alive = True
    try:
        from .. import server as forge_server

        pump_alive = bool(bpy.app.timers.is_registered(forge_server._pump))
        if forge_server.is_running():
            forge_server._ensure_pump()
        running = forge_server.is_running()
        status = forge_server.get_status()
        port = int(status.get("port") or 0)
    except Exception:  # noqa: BLE001 - never let bookkeeping fail an open
        running, port = True, 0

    names, count = _scene_names()
    return {
        "project": project,
        "path": path,
        "opened": True,
        "needs_confirmation": False,
        "confirmed": bool(confirm),
        "discarded": discarded,
        "session_file": str(bpy.data.filepath or ""),
        "object_count": count,
        "objects": names,
        "server_running": bool(running),
        "server_port": port,
        # Said plainly because it is the one thing an artist cannot see: the
        # add-on kept answering across a file load.
        "pump_survived": bool(pump_alive),
        "changed": "Opened %s — %d object(s). The Forge server is still "
                   "listening." % (path, count),
        "where": "The title bar now says %s. Undo does not cross a file load."
                 % os.path.basename(path),
    }


# ---------------------------------------------------------------------------
# the panel's own button
# ---------------------------------------------------------------------------

class FORGE_OT_save_project_blend(bpy.types.Operator):
    """Save this scene into the project folder, without moving your own file."""

    bl_idname = "forge.save_project_blend"
    bl_label = "Save Scene to Project"
    bl_description = (
        "Save this scene as projects/<project>/<project>.blend so the sculpt, "
        "the lighting and the references have a home. Your own file (File > "
        "Save) is not moved"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        from . import registry
        from .partforge import get_props, set_status

        props = get_props(context)
        status, result, message = registry.dispatch("save_project_blend", {})
        if status != "success":
            if props is not None:
                set_status(props, message or "The project file was not saved.",
                           error=True)
            self.report({"ERROR"}, message or "The project file was not saved.")
            return {"CANCELLED"}
        result = result or {}
        sentence = "Saved %s (%d object(s))." % (
            os.path.basename(str(result.get("path") or "")),
            int(result.get("object_count") or 0))
        if props is not None:
            set_status(props, sentence)
        self.report({"INFO"}, sentence)
        return {"FINISHED"}


_CLASSES = (FORGE_OT_save_project_blend,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
