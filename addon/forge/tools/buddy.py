"""Buddy mode: a teacher's eyes on the work in progress.

The artist asked for this in one sentence: *"i also want it to view my model
right, so i am working on it, it can be like we need to remesh here or I notice
clipping etc, a full ai teacher/buddy, it can occasionally check so its not
always looking or we can tell it check work"*.

So: two ways in, one path through.

* **Check my work** — a button in the Assistant box (and a sentence they can
  type). Gathers what a teacher would need to look over their shoulder and
  sends it as an ordinary message.
* **The buddy timer** — off by default, minimum five minutes, ten by default.
  It fires the same gather on its own, and it is deliberately lazy about it:
  it skips while the assistant is busy, it skips when the mesh has not changed
  since the last look, and it carries the last check-in's own words forward so
  the second note never repeats the first.

What gets gathered, every time:

1. ``capture_viewport`` — what they are looking at, their angle, their shading.
2. ``render_preview`` (iso) — the same model lit cleanly, so the form reads.
3. ``mesh_diagnose`` — the numbers, with millimetre locations for every defect.
4. The workspace context — mode, selection, brush.

Every check-in costs a Claude turn.  That is said in the toggle's own tooltip
and in the panel, because a background process quietly spending money is the
one thing that would make an artist turn this off and never turn it on again.
"""

import os
import tempfile
import time

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from . import assistant, registry
from .partforge import _tag_redraw

__all__ = [
    "CHECK_IN_LEAD",
    "MIN_INTERVAL_MINUTES",
    "DEFAULT_INTERVAL_MINUTES",
    "TURN_COST_NOTE",
    "mesh_signature",
    "should_check",
    "gather",
    "buddy_tick",
]

#: The first line of every check-in message.  Fixed, because it is what the
#: system prompt's "## Checking their work" section keys off: the model has to
#: recognise a check-in on sight to answer in the right voice and the right
#: length.
CHECK_IN_LEAD = "[check-in] Look at my work and tell me what you notice."

#: What the chat log shows for the outgoing turn.  The real message is two file
#: paths and forty lines of measurements; a sidebar 38 characters wide wants the
#: sentence, not the payload.
CHECK_IN_LOG = "Check my work"

#: Said in the UI wherever the timer can be switched on.  A turn is money.
TURN_COST_NOTE = "Each check-in costs one Claude turn"

MIN_INTERVAL_MINUTES = 5
DEFAULT_INTERVAL_MINUTES = 10
MAX_INTERVAL_MINUTES = 120

#: How often the timer wakes up to ask whether it is time yet.  Much shorter
#: than the interval on purpose: a busy-skip has to retry soon rather than
#: forfeit the whole ten minutes, and a changed interval has to take effect
#: without the artist toggling anything.
POLL_SECONDS = 20.0

#: How many characters of the last check-in ride along in the next one. Enough
#: to stop it repeating itself, short enough that it is a reminder rather than
#: a transcript.
NOTE_CARRY = 300

#: How many vertices are sampled for the change hash. The question is only "did
#: this move since I last looked", and 512 spread evenly answers it for a
#: sculpt stroke without touching a million coordinates.
HASH_SAMPLES = 512

#: Where the check-in pictures land. Scratch by design, never the repo: they are
#: evidence for one turn, and the next check-in wants its own pair to compare
#: against.
CHECKIN_DIR_NAME = "forge-checkins"


def checkin_dir():
    path = os.path.join(tempfile.gettempdir(), CHECKIN_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:  # pragma: no cover - a temp dir that will not be made
        return tempfile.gettempdir()
    return path


# ---------------------------------------------------------------------------
# has anything changed?
# ---------------------------------------------------------------------------

def mesh_signature(obj, samples=HASH_SAMPLES):
    """A cheap fingerprint of a mesh: name, counts and its coordinates.

    Counts catch a remesh, a decimate or a boolean; the coordinates catch a
    sculpt stroke.  Both together are what makes "nothing has changed, don't
    spend a turn" a decision rather than a guess.

    With numpy (which Blender ships) the whole coordinate array is hashed —
    about a millisecond on a 200 000-vertex sculpt, and it cannot miss a
    stroke.  Without it, a spread sample of ``samples`` vertices stands in;
    that can in principle miss a single vertex nudged in one corner, which is
    the right trade when the alternative is a Python loop over a million
    coordinates every twenty seconds.
    """
    if obj is None or getattr(obj, "type", None) != "MESH":
        return ""
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return ""
    try:
        vertex_count = len(mesh.vertices)
        face_count = len(mesh.polygons)
    except (AttributeError, ReferenceError):
        return ""
    if not vertex_count:
        return "%s:0:0:0" % obj.name

    step = max(1, vertex_count // max(int(samples), 1))
    accumulator = 0
    try:
        import numpy

        flat = numpy.empty(vertex_count * 3, dtype="f4")
        mesh.vertices.foreach_get("co", flat)
        accumulator = int(hash(flat.tobytes()) & 0xFFFFFFFFFFFF)
    except Exception:  # noqa: BLE001 - numpy absent, or a mesh mid-edit
        try:
            for index in range(0, vertex_count, step):
                co = mesh.vertices[index].co
                accumulator = ((accumulator * 1000003)
                               ^ hash((round(co[0], 5), round(co[1], 5),
                                       round(co[2], 5)))) & 0xFFFFFFFFFFFF
        except (AttributeError, IndexError, ReferenceError):
            return ""
    return "%s:%d:%d:%012x" % (obj.name, vertex_count, face_count, accumulator)


def should_check(now, due, busy, signature, last_hash):
    """Should the timer fire right now? Returns ``(fire, reason)``.

    Pulled out as a plain function with no ``bpy`` in it because it holds the
    two rules that decide whether this feature costs money for nothing, and
    both of them deserve to be tested directly rather than inferred from a
    timer that did or did not go off.
    """
    if busy:
        return False, "waiting — the assistant is still working"
    if now < due:
        remaining = max(int(round(due - now)), 1)
        return False, "next look in about %d min" % max(1, int(round(remaining / 60.0)))
    if signature and last_hash and signature == last_hash:
        return False, "nothing has changed since the last look"
    return True, "looking now"


# ---------------------------------------------------------------------------
# gathering — the same four things every time
# ---------------------------------------------------------------------------

def _run(name, params):
    """One socket command, in process. Returns ``(result_or_None, message)``."""
    status, result, message = registry.dispatch(name, params)
    if status != "success":
        return None, message
    return result, ""


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _active_mesh(context):
    view_layer = getattr(context, "view_layer", None)
    obj = getattr(getattr(view_layer, "objects", None), "active", None)
    if obj is not None and getattr(obj, "type", None) == "MESH":
        return obj
    scene = getattr(context, "scene", None)
    for candidate in getattr(scene, "objects", []) or []:
        if getattr(candidate, "type", None) == "MESH":
            try:
                if candidate.visible_get():
                    return candidate
            except (RuntimeError, ReferenceError):
                return candidate
    return None


def diagnose_lines(result, limit=8):
    """``mesh_diagnose``'s verdict as the few lines a message should carry."""
    if not isinstance(result, dict):
        return []
    lines = ["%s: %s faces, %s vertices"
             % (result.get("object"), result.get("face_count"),
                result.get("vertex_count"))]
    for sentence in (result.get("verdict") or [])[:limit]:
        lines.append("- %s" % sentence)
    density = (result.get("density") or {}).get("faces") or {}
    if density.get("median_mm2"):
        lines.append("- face size: median %.4f mm2, 5th-95th percentile "
                     "%.4f-%.4f mm2"
                     % (density.get("median_mm2", 0.0),
                        density.get("p05_mm2", 0.0), density.get("p95_mm2", 0.0)))
    for note in result.get("notes") or []:
        lines.append("- note: %s" % note)
    return lines


def _workspace_lines(context, scene_context):
    lines = []
    if scene_context.get("mode"):
        lines.append("Mode: %s" % scene_context["mode"])
    if scene_context.get("active_object"):
        lines.append("Active object: %s" % scene_context["active_object"])
    if scene_context.get("selected_objects"):
        lines.append("Selected: %s" % ", ".join(scene_context["selected_objects"]))
    brush = scene_context.get("brush")
    if isinstance(brush, dict):
        bits = [str(brush.get("name"))]
        if brush.get("size") is not None:
            bits.append("size %s" % brush["size"])
        if brush.get("strength") is not None:
            bits.append("strength %s" % brush["strength"])
        if brush.get("symmetry"):
            bits.append("symmetry %s" % "/".join(brush["symmetry"]))
        lines.append("Brush: %s" % ", ".join(bits))
    return lines


def gather(context=None, previous_note="", examples=4):
    """Build one check-in message. Returns ``(message, notes, evidence)``.

    ``notes`` are the things that could not be gathered, said out loud rather
    than silently dropped — a check-in with no viewport picture is still worth
    sending, but the model has to know it is looking at a render only.
    """
    context = context or bpy.context
    notes = []
    evidence = {"images": [], "diagnose": None}
    stamp = _stamp()
    folder = checkin_dir()

    scene_context = assistant.collect_context(context)
    obj = _active_mesh(context)

    viewport_path = os.path.join(folder, "viewport-%s.png" % stamp)
    result, problem = _run("capture_viewport", {"path": viewport_path})
    if result:
        evidence["images"].append(("what I am looking at right now",
                                   result.get("path") or viewport_path))
    else:
        notes.append("No viewport screenshot this time (%s)"
                     % (problem.splitlines()[0] if problem else "unavailable"))

    preview_path = os.path.join(folder, "render-%s.png" % stamp)
    params = {"path": preview_path, "view": "iso", "resolution": 768}
    if obj is not None:
        params["objects"] = [obj.name]
    result, problem = _run("render_preview", params)
    if result:
        evidence["images"].append(("a clean 3/4 render of the same model",
                                   result.get("path") or preview_path))
    else:
        notes.append("No clean render this time (%s)"
                     % (problem.splitlines()[0] if problem else "unavailable"))

    diagnosis = None
    if obj is not None:
        diagnosis, problem = _run("mesh_diagnose",
                                  {"object": obj.name, "examples": examples})
        if diagnosis is None:
            notes.append("The mesh check did not run (%s)"
                         % (problem.splitlines()[0] if problem else "unavailable"))
        evidence["diagnose"] = diagnosis
    else:
        notes.append("There is no mesh in the scene to measure yet")

    parts = [CHECK_IN_LEAD, ""]
    if evidence["images"]:
        for label, path in evidence["images"]:
            parts.append("--- %s ---" % label)
            parts.append(path)
            parts.append("")
        parts.append("View %s with the Read tool BEFORE answering."
                     % ("both images" if len(evidence["images"]) > 1
                        else "this image"))
        parts.append("")

    workspace = _workspace_lines(context, scene_context)
    if workspace:
        parts.append("--- Where I am working ---")
        parts.extend(workspace)
        parts.append("")

    lines = diagnose_lines(diagnosis)
    if lines:
        parts.append("--- Mesh check (numbers, with locations in mm) ---")
        parts.extend(lines)
        parts.append("")

    if notes:
        parts.append("--- Could not gather ---")
        parts.extend("- %s" % note for note in notes)
        parts.append("")

    previous_note = str(previous_note or "").strip()
    if previous_note:
        parts.append("You previously noted: %s" % previous_note[:NOTE_CARRY])
        parts.append("Do not repeat it — say what is new, or that it is fixed.")
        parts.append("")

    return "\n".join(parts).strip(), notes, evidence


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

def _enabled_update(self, context):
    if self.enabled:
        self.next_due = time.time() + self.interval_minutes * 60.0
        self.status = "Watching — first look in about %d min" % self.interval_minutes
        start_timer()
    else:
        self.status = ""
        stop_timer()


class ForgeBuddyProps(PropertyGroup):
    enabled: BoolProperty(
        name="Buddy",
        description=("Let the assistant look over your shoulder now and then "
                     "and tell you what it notices. " + TURN_COST_NOTE
                     + ", so it only looks when the model has actually changed"),
        default=False,
        update=_enabled_update,
    )
    interval_minutes: IntProperty(
        name="Every",
        description=("How many minutes between looks. It skips a look when "
                     "nothing has changed, and waits when the assistant is busy"),
        default=DEFAULT_INTERVAL_MINUTES,
        min=MIN_INTERVAL_MINUTES,
        max=MAX_INTERVAL_MINUTES,
    )
    #: Unix time of the next look. Absolute rather than a countdown so the
    #: interval can change under it without the artist waiting twice.
    next_due: FloatProperty(default=0.0)
    #: The last check-in's own words, so the next one never repeats them.
    last_note: StringProperty(default="")
    #: The mesh fingerprint at the last look.
    last_hash: StringProperty(default="")
    checks: IntProperty(default=0)
    status: StringProperty(default="")


def get_props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    return getattr(scene, "forge_buddy", None)


# ---------------------------------------------------------------------------
# sending a check-in
# ---------------------------------------------------------------------------

def send_check_in(context, props=None, reason="asked"):
    """Gather and send one check-in through the normal ``/ask`` path."""
    props = props or get_props(context)
    chat = assistant.get_props(context)
    if chat is None:
        return {"CANCELLED"}
    if chat.busy:
        assistant.set_status(chat, "The assistant is still working — try again "
                                   "in a moment.", error=True)
        return {"CANCELLED"}

    previous = str(getattr(props, "last_note", "") or "") if props else ""
    message, notes, _evidence = gather(context, previous_note=previous)

    def remember(reply):
        if props is None:
            return
        props.last_note = str(reply or "").strip()[:NOTE_CARRY]
        props.checks += 1
        props.status = "Last look: %s" % time.strftime("%H:%M")

    if props is not None:
        props.last_hash = mesh_signature(_active_mesh(context))
        props.next_due = time.time() + max(
            getattr(props, "interval_minutes", DEFAULT_INTERVAL_MINUTES),
            MIN_INTERVAL_MINUTES) * 60.0

    log_text = CHECK_IN_LOG if reason == "asked" else "Check-in (buddy)"
    if notes:
        # The chat log is 38 characters wide. One short clause about what could
        # not be gathered belongs there; the full sentence is already in the
        # message the assistant reads.
        log_text += " (%s)" % notes[0].split("(")[0].strip().rstrip(".")[:48]
    return assistant.send_message(
        context, chat, message,
        check_in=True,
        log_text=log_text,
        extra_context={"check_in": reason},
        on_reply=remember,
    )


class FORGE_OT_buddy_check(Operator):
    """Ask the assistant to look at the work in progress, right now."""

    bl_idname = "forge.buddy_check"
    bl_label = "Check my work"
    bl_description = ("Show the assistant what you are working on — your view, "
                      "a clean render and a mesh check — and get a teacher's "
                      "notes back. " + TURN_COST_NOTE)
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        chat = assistant.get_props(context)
        return chat is not None and not chat.busy

    def execute(self, context):
        return send_check_in(context, get_props(context), reason="asked")


class FORGE_OT_buddy_toggle(Operator):
    """Turn the periodic look on or off without hunting for the checkbox."""

    bl_idname = "forge.buddy_toggle"
    bl_label = "Buddy"
    bl_description = ("Look over your shoulder every few minutes. " + TURN_COST_NOTE
                      + ", and it only looks when the model has changed")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return get_props(context) is not None

    def execute(self, context):
        props = get_props(context)
        props.enabled = not props.enabled
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# the timer
# ---------------------------------------------------------------------------

_TIMER_RUNNING = False


def buddy_tick():
    """One wake-up. Returns the seconds until the next, or ``None`` to stop.

    Deliberately callable by hand: the harness drives this directly rather than
    waiting minutes for a timer, and every skip path is a return value it can
    assert on.
    """
    global _TIMER_RUNNING
    context = bpy.context
    props = get_props(context)
    if props is None or not props.enabled:
        _TIMER_RUNNING = False
        return None

    chat = assistant.get_props(context)
    if chat is None:
        return POLL_SECONDS

    busy = bool(getattr(chat, "busy", False) or getattr(chat, "queued", False))
    signature = mesh_signature(_active_mesh(context))
    fire, reason = should_check(time.time(), props.next_due, busy,
                                signature, props.last_hash)
    props.status = reason
    if not fire:
        if not busy and signature and signature == props.last_hash:
            # Nothing changed: push the clock forward so this does not spin on
            # the same answer every twenty seconds.
            props.next_due = time.time() + props.interval_minutes * 60.0
        _tag_redraw()
        return POLL_SECONDS

    send_check_in(context, props, reason="buddy")
    _tag_redraw()
    return POLL_SECONDS


def start_timer():
    global _TIMER_RUNNING
    if _TIMER_RUNNING or bpy.app.background:
        # Headless there is nobody to look over the shoulder of, and a timer in
        # a --background run would fire against a scene nobody is sculpting.
        return False
    try:
        bpy.app.timers.register(buddy_tick, first_interval=POLL_SECONDS)
    except Exception:  # noqa: BLE001 - a missing timer is never a failed add-on
        return False
    _TIMER_RUNNING = True
    return True


def stop_timer():
    global _TIMER_RUNNING
    _TIMER_RUNNING = False
    try:
        if bpy.app.timers.is_registered(buddy_tick):
            bpy.app.timers.unregister(buddy_tick)
    except Exception:  # noqa: BLE001
        pass
    return True


def timer_running():
    return _TIMER_RUNNING


_CLASSES = (
    ForgeBuddyProps,
    FORGE_OT_buddy_check,
    FORGE_OT_buddy_toggle,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forge_buddy = bpy.props.PointerProperty(type=ForgeBuddyProps)


def unregister():
    stop_timer()
    try:
        del bpy.types.Scene.forge_buddy
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
