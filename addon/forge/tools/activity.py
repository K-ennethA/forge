"""Live activity feed — what the artist did while the assistant was not looking.

The ask, verbatim: *"it should have live context awareness of blender otherwise
its useless as a copilot."*  Until now the assistant saw the scene only at the
moment it called a tool, which means it saw the scene it asked about and nothing
else.  An artist who moved a wall, deleted a closet and dropped into sculpt mode
between two messages was invisible, and a copilot that cannot see the controls
move is a copilot in name only.

So the add-on keeps a small, always-on record of scene activity and hands it over
on request.  Three properties make it safe to leave running forever:

**It is names and timestamps, nothing else.**  The handler never reads a mesh,
never walks vertices, never evaluates a depsgraph of its own.  Blender already
computed the classification we want — every ``DepsgraphUpdate`` carries
``is_updated_geometry`` and ``is_updated_transform`` — so the handler reads two
booleans and a name off work that has already happened.  That is cheaper than the
cached ``(vert_count, matrix hash)`` comparison the plan allowed for, and more
accurate: a vertex moved back to where it started still sets the geometry flag,
and a hash would call that "nothing happened".

**It is bounded, in every direction.**  A ring of :data:`RING_LIMIT` entries, at
most :data:`MAX_UPDATES_PER_TICK` updates inspected per handler call, at most
:data:`MAX_TRACKED_OBJECTS` object names in the added/removed snapshot, and
consecutive events about the same object coalesced inside
:data:`COALESCE_SECONDS`.  That last one is not a nicety: dragging one wall fires
hundreds of depsgraph updates, and without coalescing a single drag would push
every other thing the artist did out of the ring.  A coalesced entry keeps its
``count``, so "moved it" and "moved it 340 times" still read differently.

**It never raises into Blender.**  Handlers run in contexts no operator would
choose — mid-undo, during a file load, on a thread Blender is picky about — so
every handler body is wrapped, every failure is counted, and the count comes back
in the report.  A handler that throws gets *removed by Blender*, which would turn
one bad frame into a permanently dead feed.  Swallow and count is the only
correct policy here.

Who did it: Forge, or the artist?
---------------------------------
The whole point of the feed is the distinction between "Forge built this" and
"the artist edited this" — the second is news, the first is the assistant reading
its own homework back to itself.  Two mechanisms, because one is not enough:

1. **While a socket command runs**, ``_FORGE_DEPTH`` is above zero.
   :func:`register` wraps ``registry.dispatch`` to maintain it (re-entrant, so a
   flow step or ``check_my_work`` nested inside another command counts once), and
   :func:`unregister` puts the original back.
2. **For a short grace window after it returns.**  This is the load-bearing half.
   Blender does not deliver ``depsgraph_update_post`` at the moment a command
   mutates an object; it delivers it when the depsgraph is next evaluated, which
   for a command running in the socket pump's timer is usually *after* the
   command has returned.  Without the window, every mesh Forge ever built would
   be reported as the artist's own work.  :data:`FORGE_GRACE_SECONDS` is how long
   the credit lasts.

The window is a judgement call, and it is the honest one: the failure it can
cause is an artist edit landing in the same fraction of a second as a Forge
command being attributed to Forge — and an artist is not dragging a vertex while
they wait for the assistant's answer.  The failure it prevents is the feed being
wrong about *every* built object, every time.

Why the dispatch wrapper rather than a line in ``registry.dispatch``
-------------------------------------------------------------------
Because the feed is a feature that must be able to fail without taking the
command registry with it, and because a module that installs and removes its own
hook can be enabled, disabled and reloaded without the rest of the add-on
knowing it exists.  The wrapper is idempotent (it refuses to wrap itself), it is
restored on unregister, and if ``registry.dispatch`` ever grows the hook
natively, this module will see its marker attribute and leave it alone.

``get_activity`` is READ-ONLY
-----------------------------
It answers a question; it cannot change the .blend.  So it joins
``registry.READ_ONLY_COMMANDS`` and pushes no undo checkpoint — a history full of
"Forge: get_activity" is a history nobody can use, which is the same argument the
workspace commands are in that set for.
"""

import sys
import time
from collections import deque

import bpy
from bpy.app.handlers import persistent

from .registry import ForgeError, command
from . import registry

__all__ = [
    "RING_LIMIT",
    "COALESCE_SECONDS",
    "FORGE_GRACE_SECONDS",
    "EVENT_KINDS",
    "register",
    "unregister",
    "handlers_installed",
    "snapshot",
    "reset",
]


# ---------------------------------------------------------------------------
# the dials
# ---------------------------------------------------------------------------

#: How many events the ring holds.  200 is roughly an hour of ordinary work once
#: coalescing has done its job, and about 25 KB of small dicts — small enough
#: that nobody has to think about it, large enough that a long silence between
#: messages does not lose the beginning of itself.
RING_LIMIT = 200

#: Consecutive events about the same object, of the same kind, from the same
#: source, inside this many seconds become ONE event with a ``count``.  A drag is
#: one gesture and the artist would describe it as one gesture.
COALESCE_SECONDS = 2.0

#: How long after a Forge socket command returns its scene changes are still
#: credited to Forge.  See the module docstring: Blender delivers the depsgraph
#: update after the command, not during it.
FORGE_GRACE_SECONDS = 0.75

#: Never inspect more than this many depsgraph updates in one handler call.  A
#: bulk import can report thousands; the first few hundred say the same thing.
MAX_UPDATES_PER_TICK = 256

#: Above this many objects the added/removed snapshot is abandoned — building a
#: set of names on every depsgraph tick is cheap at a thousand objects and not
#: cheap at a hundred thousand.  Transform and geometry events keep working; the
#: report says the census was skipped rather than pretending nothing was added.
MAX_TRACKED_OBJECTS = 20000

#: How many events one ``get_activity`` call returns by default.
DEFAULT_LIMIT = 40

#: How many distinct collection names the report names.
MAX_COLLECTIONS = 12

#: The kinds an event can carry.
#:
#: ``transformed`` / ``geometry`` / ``added`` / ``removed`` / ``mode`` are the
#: contract's five.  ``undo`` and ``redo`` are additive and earn their place: an
#: artist pressing Ctrl+Z is the single most important thing the assistant can
#: know about, because it usually means the last thing somebody did was wrong,
#: and a feed that reported the *effects* of an undo as ordinary edits would be
#: actively misleading about it.
EVENT_KINDS = ("transformed", "geometry", "added", "removed", "mode",
               "undo", "redo")

#: Sources.
EVENT_SOURCES = ("artist", "forge")

#: Commands that are in ``READ_ONLY_COMMANDS`` but still change something the
#: depsgraph reports.  Everything else in that set answers a question, and a
#: command that answers a question must NOT claim credit for what the depsgraph
#: says next — ``get_scene_info`` calls ``refresh_view_layer()``, which flushes
#: whatever the ARTIST had pending, and the assistant polls it constantly.  If a
#: read command could take the credit, then the more attentive the assistant was,
#: the blinder it would become: exactly backwards.
#:
#: ``set_mode`` and ``sculpt_brush`` are the two exceptions, and they are in that
#: set for a reason about undo history rather than a claim that they change
#: nothing — a mode switch really is Forge's doing when Forge asked for it.
CREDIT_READ_ONLY = frozenset({"set_mode", "sculpt_brush"})


def _claims_credit(name):
    """Could a scene change during/just after ``name`` be Forge's doing?"""
    name = str(name or "")
    if not name:
        return False
    if name in CREDIT_READ_ONLY:
        return True
    return name not in registry.READ_ONLY_COMMANDS


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

_RING = deque(maxlen=RING_LIMIT)

#: Object names as of the last census, for added/removed.
_NAMES = set()
#: ``len(bpy.data.objects)`` as of the last census — the cheap "did anything
#: appear or vanish" test, so the full set is only rebuilt when it might differ.
_COUNT = -1
#: Last seen ``bpy.context.mode``.
_MODE = ""

#: Re-entrant depth of Forge socket commands, and what the outermost one is.
_FORGE_DEPTH = 0
_FORGE_COMMAND = ""
#: Until when the last Forge command still gets the credit, and for what.
_FORGE_GRACE_UNTIL = 0.0
_FORGE_GRACE_COMMAND = ""

_STATS = {
    "recorded": 0,      # events appended
    "coalesced": 0,     # events folded into an existing entry
    "dropped": 0,       # events pushed out of the ring, plus updates not read
    "errors": 0,        # handler bodies that raised
    "last_error": "",
    "census_skipped": 0,
}

#: The handler functions currently installed, so unregister removes exactly what
#: register added even across an add-on reload.
_INSTALLED = []

#: The unwrapped ``registry.dispatch``, while ours is in its place.
_ORIGINAL_DISPATCH = None


# ---------------------------------------------------------------------------
# who is doing this
# ---------------------------------------------------------------------------

def begin_forge_command(name):
    """A Forge socket command is starting. Re-entrant."""
    global _FORGE_DEPTH, _FORGE_COMMAND
    if _FORGE_DEPTH == 0:
        _FORGE_COMMAND = str(name or "")
    _FORGE_DEPTH += 1


def end_forge_command(name):
    """...and it has returned. Opens the grace window on the way out.

    Only for a command that could have changed something: a read command that
    armed the window would spend it stealing the artist's next edit.
    """
    global _FORGE_DEPTH, _FORGE_GRACE_UNTIL, _FORGE_GRACE_COMMAND
    _FORGE_DEPTH = max(0, _FORGE_DEPTH - 1)
    if _FORGE_DEPTH == 0 and _claims_credit(name):
        _FORGE_GRACE_COMMAND = str(name or "")
        _FORGE_GRACE_UNTIL = time.time() + FORGE_GRACE_SECONDS


def _source(now):
    """``("artist"|"forge", command_name)`` for something happening at ``now``."""
    if _FORGE_DEPTH > 0:
        if _claims_credit(_FORGE_COMMAND):
            return "forge", _FORGE_COMMAND
        return "artist", ""
    if now <= _FORGE_GRACE_UNTIL:
        return "forge", _FORGE_GRACE_COMMAND
    return "artist", ""


# ---------------------------------------------------------------------------
# the ring
# ---------------------------------------------------------------------------

#: How far back coalescing looks.  Not just the last entry: two objects dragged
#: together alternate in the stream, and a window of one would coalesce neither.
_COALESCE_LOOKBACK = 8


def _append(when, name, kind, source, detail="", command=""):
    """Record one event, folding it into a recent twin where that is honest."""
    global _STATS
    name = str(name or "")
    for existing in list(_RING)[-_COALESCE_LOOKBACK:]:
        if (existing["kind"] == kind
                and existing["object"] == name
                and existing["source"] == source
                and existing.get("detail", "") == detail
                and when - existing["t"] <= COALESCE_SECONDS):
            existing["t"] = when
            existing["count"] = int(existing.get("count", 1)) + 1
            _STATS["coalesced"] += 1
            return existing
    if len(_RING) == RING_LIMIT:
        _STATS["dropped"] += 1
    event = {
        # Stored unrounded, rounded only on the way out.  A rounded timestamp
        # compared against an unrounded clock can come out NEGATIVE by half a
        # millisecond, and a drag firing every 0.13 ms would then coalesce two
        # ticks in three: measured, 120 flushes became 40 events instead of one.
        "t": float(when),
        "object": name,
        "kind": kind,
        "source": source,
        "count": 1,
    }
    if detail:
        event["detail"] = str(detail)
    if command:
        event["command"] = str(command)
    _RING.append(event)
    _STATS["recorded"] += 1
    return event


def reset(clear_events=True):
    """Forget the census (and, by default, the feed).

    Called on file load, after an undo/redo and by :func:`register`: in all three
    cases the set of objects we believe exists may have nothing to do with the
    one that does, and a stale census would report a whole file as "added".
    """
    global _NAMES, _COUNT, _MODE
    if clear_events:
        _RING.clear()
    try:
        objects = bpy.data.objects
        if len(objects) <= MAX_TRACKED_OBJECTS:
            _NAMES = set(objects.keys())
            _COUNT = len(objects)
        else:
            _NAMES = set()
            _COUNT = -1
    except Exception:  # noqa: BLE001 - bpy.data may be mid-load
        _NAMES = set()
        _COUNT = -1
    _MODE = _current_mode()


def _current_mode():
    """``bpy.context.mode`` as a string, or ``""`` when it cannot be read.

    Handlers run in contexts where ``bpy.context`` is thin, so this is allowed to
    answer "I do not know" — and a mode we could not read is simply not reported
    as a change.
    """
    try:
        return str(bpy.context.mode)
    except (AttributeError, RuntimeError, TypeError):
        return ""


def _active_name():
    try:
        obj = bpy.context.view_layer.objects.active
    except (AttributeError, RuntimeError, TypeError):
        return ""
    return obj.name if obj is not None else ""


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

def _note_error(exc):
    _STATS["errors"] += 1
    _STATS["last_error"] = "%s: %s" % (type(exc).__name__, exc)


def _census(now, source, command, touched_names):
    """Added/removed objects, by name, against the last snapshot.

    Runs only when it might find something: the object count changed, or one of
    the objects the depsgraph just told us about is a name we have never seen
    (which is how a rename is caught for the price of a set lookup).
    """
    global _NAMES, _COUNT
    try:
        objects = bpy.data.objects
        count = len(objects)
    except Exception as exc:  # noqa: BLE001
        _note_error(exc)
        return set()

    if count > MAX_TRACKED_OBJECTS:
        _STATS["census_skipped"] += 1
        _NAMES = set()
        _COUNT = -1
        return set()

    unknown = any(name not in _NAMES for name in touched_names)
    if count == _COUNT and not unknown:
        return set()

    current = set(objects.keys())
    added = current - _NAMES
    removed = _NAMES - current
    _NAMES = current
    _COUNT = count

    for name in sorted(removed):
        _append(now, name, "removed", source, command=command)
    for name in sorted(added):
        _append(now, name, "added", source, command=command)
    return added


def _record_depsgraph(depsgraph):
    global _MODE
    now = time.time()
    source, command = _source(now)

    touched = []
    seen = set()
    updates = getattr(depsgraph, "updates", None) or ()
    index = 0
    for update in updates:
        index += 1
        if index > MAX_UPDATES_PER_TICK:
            _STATS["dropped"] += 1
            break
        ident = getattr(update, "id", None)
        if not isinstance(ident, bpy.types.Object):
            # Mesh/material datablocks arrive as their own updates; the object
            # that uses them arrives too, with the same flags, and the object is
            # the thing the artist has a name for.
            continue
        name = getattr(ident, "name", "") or ""
        if not name:
            continue
        if update.is_updated_geometry:
            kind = "geometry"
        elif update.is_updated_transform:
            kind = "transformed"
        else:
            # Shading-only updates fire constantly (a material preview, a
            # viewport shading change) and say nothing about the artist's work.
            continue
        key = (name, kind)
        if key in seen:
            continue
        seen.add(key)
        touched.append((name, kind))

    # The census first: an object that has just appeared should read "added",
    # not "its geometry changed", and it will have both flags set.
    added = _census(now, source, command, [name for name, _kind in touched])
    for name, kind in touched:
        if name in added:
            continue
        _append(now, name, kind, source, command=command)

    mode = _current_mode()
    if mode and mode != _MODE:
        _MODE = mode
        _append(now, _active_name(), "mode", source, detail=mode, command=command)


@persistent
def _forge_activity_depsgraph_post(*args):
    try:
        depsgraph = args[1] if len(args) > 1 else None
        if depsgraph is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        _record_depsgraph(depsgraph)
    except Exception as exc:  # noqa: BLE001 - a handler that raises gets removed
        _note_error(exc)


@persistent
def _forge_activity_undo_post(*args):
    try:
        now = time.time()
        source, command = _source(now)
        _append(now, "", "undo", source, command=command)
        # Everything we believed about the file may have just been taken back.
        reset(clear_events=False)
    except Exception as exc:  # noqa: BLE001
        _note_error(exc)


@persistent
def _forge_activity_redo_post(*args):
    try:
        now = time.time()
        source, command = _source(now)
        _append(now, "", "redo", source, command=command)
        reset(clear_events=False)
    except Exception as exc:  # noqa: BLE001
        _note_error(exc)


@persistent
def _forge_activity_load_post(*args):
    try:
        # A different file is a different scene; its predecessor's edits are not
        # news about it.  Clear, then re-census.
        reset(clear_events=True)
    except Exception as exc:  # noqa: BLE001
        _note_error(exc)


#: ``(handler list name, function)``, in the order they are installed.
_HANDLER_SPECS = (
    ("depsgraph_update_post", _forge_activity_depsgraph_post),
    ("undo_post", _forge_activity_undo_post),
    ("redo_post", _forge_activity_redo_post),
    ("load_post", _forge_activity_load_post),
)

for _name, _function in _HANDLER_SPECS:
    _function._forge_activity_handler = True
del _name, _function


def _purge(list_name):
    """Remove every Forge activity handler from one of Blender's handler lists.

    By marker attribute rather than by identity: an add-on reload builds new
    function objects, and the old ones are still sitting in Blender's lists.
    This is what makes registration idempotent in the one case that matters.
    """
    handlers = getattr(bpy.app.handlers, list_name, None)
    if handlers is None:
        return 0
    doomed = [fn for fn in handlers
              if getattr(fn, "_forge_activity_handler", False)]
    for fn in doomed:
        try:
            handlers.remove(fn)
        except ValueError:
            pass
    return len(doomed)


def handlers_installed():
    """How many of our handlers Blender currently holds."""
    total = 0
    for list_name, _fn in _HANDLER_SPECS:
        handlers = getattr(bpy.app.handlers, list_name, None) or ()
        total += sum(1 for fn in handlers
                     if getattr(fn, "_forge_activity_handler", False))
    return total


# ---------------------------------------------------------------------------
# the dispatch hook
# ---------------------------------------------------------------------------

def _wrap_dispatch():
    """Wrap ``registry.dispatch`` so the feed knows when Forge is the one acting.

    Idempotent by marker attribute, so a double register, an add-on reload or a
    future native hook in ``registry`` all leave exactly one wrapper in place.
    """
    global _ORIGINAL_DISPATCH
    inner = registry.dispatch
    if getattr(inner, "_forge_activity_wrapped", False):
        return False

    def dispatch(name, params):
        begin_forge_command(name)
        try:
            return inner(name, params)
        finally:
            end_forge_command(name)

    dispatch.__name__ = getattr(inner, "__name__", "dispatch")
    dispatch.__doc__ = getattr(inner, "__doc__", None)
    dispatch._forge_activity_wrapped = True
    dispatch._forge_activity_inner = inner
    _ORIGINAL_DISPATCH = inner
    registry.dispatch = dispatch
    _rebind_package_dispatch(inner, dispatch)
    return True


def _unwrap_dispatch():
    global _ORIGINAL_DISPATCH
    current = registry.dispatch
    if not getattr(current, "_forge_activity_wrapped", False):
        _ORIGINAL_DISPATCH = None
        return False
    inner = getattr(current, "_forge_activity_inner", None) or _ORIGINAL_DISPATCH
    if inner is None:
        return False
    registry.dispatch = inner
    _rebind_package_dispatch(current, inner)
    _ORIGINAL_DISPATCH = None
    return True


def _rebind_package_dispatch(old, new):
    """``forge.tools.dispatch`` is a re-export, bound at import time.

    Nothing in the add-on calls it (everything reaches for ``registry.dispatch``)
    but a re-export that points at a different function than the module it
    re-exports from is a trap laid for whoever writes the next caller.
    """
    package = sys.modules.get(__package__)
    if package is not None and getattr(package, "dispatch", None) is old:
        package.dispatch = new


def _mark_read_only():
    """``get_activity`` answers a question; it must not cost an undo step."""
    if "get_activity" in registry.READ_ONLY_COMMANDS:
        return False
    registry.READ_ONLY_COMMANDS = frozenset(
        registry.READ_ONLY_COMMANDS | {"get_activity"})
    return True


# ---------------------------------------------------------------------------
# register / unregister
# ---------------------------------------------------------------------------

def register():
    """Install the handlers and the dispatch hook. Safe to call twice."""
    for list_name, _fn in _HANDLER_SPECS:
        _purge(list_name)
    installed = []
    for list_name, function in _HANDLER_SPECS:
        handlers = getattr(bpy.app.handlers, list_name, None)
        if handlers is None:
            # A Blender build without one of these lists is not a reason to have
            # none of the feed.
            continue
        handlers.append(function)
        installed.append((list_name, function))
    global _INSTALLED
    _INSTALLED = installed
    _wrap_dispatch()
    _mark_read_only()
    reset(clear_events=True)


def unregister():
    """Take the handlers back out and restore ``registry.dispatch``."""
    global _INSTALLED, _FORGE_DEPTH, _FORGE_GRACE_UNTIL
    for list_name, _fn in _HANDLER_SPECS:
        _purge(list_name)
    _INSTALLED = []
    _unwrap_dispatch()
    _RING.clear()
    _FORGE_DEPTH = 0
    _FORGE_GRACE_UNTIL = 0.0


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------

def _collections_for(names):
    """The collections holding the objects named, newest mention first."""
    found = []
    for name in names:
        if not name or len(found) >= MAX_COLLECTIONS:
            break
        try:
            obj = bpy.data.objects.get(name)
        except Exception:  # noqa: BLE001
            break
        if obj is None:
            continue
        try:
            users = list(obj.users_collection)
        except (AttributeError, RuntimeError, TypeError):
            continue
        for collection in users:
            label = getattr(collection, "name", "")
            if label and label not in found:
                found.append(label)
                if len(found) >= MAX_COLLECTIONS:
                    break
    return found


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForgeError("%s must be a number, got %s."
                         % (label, type(value).__name__))
    return float(value)


def _floor_ms(value):
    """Milliseconds, rounded DOWN.

    The ``now`` in this report becomes the caller's next ``since``, so it must
    never be later than the moment it describes: half a millisecond of rounding
    up is half a millisecond of the artist's work that the next call would never
    be told about.  Down is the only safe direction.
    """
    return int(float(value) * 1000.0) / 1000.0


def snapshot(since=None, limit=DEFAULT_LIMIT):
    """The feed plus the live pointing state. The body of ``get_activity``."""
    now = time.time()

    matched = [event for event in _RING
               if since is None or event["t"] > since]
    matched.sort(key=lambda event: event["t"], reverse=True)
    total = len(matched)
    events = [dict(event) for event in matched[:limit]]
    for event in events:
        event["ago"] = round(max(0.0, now - event["t"]), 2)
        event["t"] = round(event["t"], 3)

    try:
        scene = bpy.context.scene
    except (AttributeError, RuntimeError):
        scene = None
    cursor_mm = None
    if scene is not None:
        try:
            cursor_mm = [round(float(v) * 1000.0, 3)
                         for v in scene.cursor.location]
        except (AttributeError, RuntimeError, TypeError):
            cursor_mm = None
    try:
        selected = [obj.name for obj in bpy.context.view_layer.objects
                    if obj.select_get()]
    except (AttributeError, RuntimeError, TypeError):
        selected = []

    active = _active_name()
    return {
        "events": events,
        "count": len(events),
        "matched": total,
        "truncated": total > len(events),
        "since": since,
        "now": _floor_ms(now),
        "mode": _current_mode(),
        "active": active or None,
        "selected": selected,
        "selected_count": len(selected),
        "cursor_mm": cursor_mm,
        "collections_touched": _collections_for(
            [event["object"] for event in events] + [active]),
        "objects": len(getattr(bpy.data, "objects", ()) or ()),
        "ring": {
            "limit": RING_LIMIT,
            "size": len(_RING),
            "recorded": _STATS["recorded"],
            "coalesced": _STATS["coalesced"],
            "dropped": _STATS["dropped"],
            "errors": _STATS["errors"],
            "last_error": _STATS["last_error"],
            "census_skipped": _STATS["census_skipped"],
            "coalesce_seconds": COALESCE_SECONDS,
            "forge_grace_seconds": FORGE_GRACE_SECONDS,
        },
        "handlers": handlers_installed(),
        "watching": handlers_installed() > 0,
        "kinds": list(EVENT_KINDS),
        "sources": list(EVENT_SOURCES),
    }


@command("get_activity")
def cmd_get_activity(params):
    """What has happened in the scene, newest first, plus where the artist is.

    One call answers "what has the artist done since I last looked": pass the
    ``now`` from the previous call as ``since`` and everything that comes back is
    new.  The clock is Blender's own, so a caller on another process never has to
    reason about clock skew between the two.
    """
    since = params.get("since")
    if since is not None:
        since = _number(since, "'since'")

    limit = params.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        raise ForgeError("'limit' must be a whole number of events, got %s."
                         % type(limit).__name__)
    limit = int(limit)
    if limit < 1:
        raise ForgeError("'limit' must be at least 1, got %d." % limit)
    limit = min(limit, RING_LIMIT)

    return snapshot(since=since, limit=limit)


# The command is registered at import; so is its read-only classification, so a
# caller who reaches it before ``register()`` has run still costs no undo step.
_mark_read_only()
