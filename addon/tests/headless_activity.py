"""Headless add-on tests for the live activity feed.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_activity.py

``--factory-startup`` matters here more than in most of these suites: the feed
hangs off ``bpy.app.handlers``, and a user's own start-up scripts are exactly the
thing that would leave a handler in those lists and make "registration is
idempotent" a claim about somebody's preferences file.

The socket port is **9906** (9876 belongs to a live session, 9878-9905 to the
earlier suites).  Nothing else is needed: no geometry service, no bridge, no
Claude CLI, no network beyond loopback.

What is actually being proved:

1. the handlers register, register *idempotently* (twice leaves one of each),
   and unregister cleanly — including putting ``registry.dispatch`` back;
2. ``get_activity`` is a protocol command, is classified READ-ONLY, and changes
   nothing in the scene when it is called;
3. an artist-style edit — an object mutated directly in this file, the way a
   viewport gesture would — lands in the feed as ``source: "artist"``, with
   ``transformed`` and ``geometry`` told apart;
4. a Forge socket command's own mutation lands as ``source: "forge"``, including
   the part the depsgraph delivers *after* the command returned (which is the
   whole reason the grace window exists);
5. a READ command never claims credit: ``get_scene_info`` flushes whatever the
   artist had pending, and if that came back tagged ``forge`` the assistant would
   go blinder the more attentive it was;
6. ``since`` filtering, on Blender's own clock;
7. the ring is capped, and a gesture that fires hundreds of updates coalesces
   into one event with a count rather than flooding it;
8. added and removed objects are noticed, and a mode switch is an event;
9. the handlers swallow their own failures instead of raising into Blender
   (a handler that raises is a handler Blender removes);
10. ``get_scene_info`` now reports ``mode``.
"""

import json
import os
import socket as socketlib
import sys
import threading
import time
import traceback

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9906  # not 9876 (a live session) and not 9879..9905 (every other suite)

_RESULTS = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def note(text):
    print("     %s" % text)


def section(title):
    print("\n== %s ==" % title)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def _roundtrip(payload, timeout=60.0):
    """One socket command, pumping the main-thread queue until it replies."""
    from forge import server as forge_server

    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                box["reply"] = json.loads(buffer.split(b"\n")[0].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=talk, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        if forge_server._server is not None:
            forge_server._server.drain()
        time.sleep(0.005)
    thread.join(timeout=2.0)

    if "error" in box:
        return {"status": "error", "message": "harness: %s" % box["error"]}
    return box.get("reply") or {"status": "error",
                                "message": "no reply within %.0fs" % timeout}


def call(command, **params):
    return _roundtrip({"type": command, "params": params})


def activity(**params):
    """``get_activity`` over the real socket, returning its result or ``{}``."""
    reply = call("get_activity", **params)
    if reply.get("status") != "success":
        return {"_error": reply.get("message", "")}
    return reply.get("result") or {}


def flush():
    """Make Blender evaluate the depsgraph, which is what fires the handler.

    A viewport redraw does this every frame; ``--background`` has no redraw
    loop, so the test asks for it by name.  This is the only concession the
    suite makes to being headless, and it is the same call the add-on's own
    ``refresh_view_layer`` makes.
    """
    try:
        bpy.context.view_layer.update()
    except Exception:  # noqa: BLE001
        pass


def settle():
    """Wait out any open Forge grace window, so what follows is the artist's."""
    from forge.tools import activity as feed

    remaining = feed._FORGE_GRACE_UNTIL - time.time()
    if remaining > 0:
        time.sleep(remaining + 0.05)


def mark():
    """A ``since`` for the next assertion — the add-on's own clock."""
    result = activity(limit=1)
    return result.get("now")


def kinds_for(events, name):
    return [event["kind"] for event in events if event.get("object") == name]


def find(events, name, kind=None):
    for event in events:
        if event.get("object") == name and (kind is None or event.get("kind") == kind):
            return event
    return None


def make_cube(name, size=1.0):
    """An object built the way a script builds one — not through Forge."""
    mesh = bpy.data.meshes.new(name + "Mesh")
    half = size / 2.0
    verts = [(-half, -half, -half), (half, -half, -half), (half, half, -half),
             (-half, half, -half), (-half, -half, half), (half, -half, half),
             (half, half, half), (-half, half, half)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def drop(name):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


# ---------------------------------------------------------------------------
# 1 — registration
# ---------------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import activity as feed
    from forge.tools import registry

    check("get_activity is a protocol command", registry.has_command("get_activity"))
    check("and every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "get_scene_info", "load_mesh", "export_stl", "flow_list",
               "rigforge_tag", "build_floorplan")),
          "%d commands registered" % len(registry.command_names()))

    check("it is classified READ-ONLY (no undo checkpoint for a question)",
          "get_activity" in registry.READ_ONLY_COMMANDS)

    check("all four handlers are installed", feed.handlers_installed() == 4,
          str(feed.handlers_installed()))
    for list_name, _fn in feed._HANDLER_SPECS:
        handlers = getattr(bpy.app.handlers, list_name)
        ours = [fn for fn in handlers
                if getattr(fn, "_forge_activity_handler", False)]
        check("exactly one handler in %s" % list_name, len(ours) == 1,
              "%d found" % len(ours))

    check("registry.dispatch is wrapped so the feed knows who is acting",
          getattr(registry.dispatch, "_forge_activity_wrapped", False))


def test_registration_is_idempotent():
    section("registering twice leaves one of each")
    from forge.tools import activity as feed
    from forge.tools import registry

    before = feed.handlers_installed()
    feed.register()
    feed.register()
    check("still four handlers after two more register() calls",
          feed.handlers_installed() == 4,
          "%d before, %d after" % (before, feed.handlers_installed()))
    check("and dispatch was not wrapped twice",
          getattr(getattr(registry.dispatch, "_forge_activity_inner", None),
                  "_forge_activity_wrapped", False) is False)
    check("the wrapper still dispatches",
          call("ping").get("status") == "success")


# ---------------------------------------------------------------------------
# 2 — the artist's own edits
# ---------------------------------------------------------------------------

def test_artist_transform():
    section("an artist-style transform lands as the artist's")
    drop("ArtistBox")
    obj = make_cube("ArtistBox")
    flush()
    settle()

    since = mark()
    obj.location.x += 0.25
    flush()

    result = activity(since=since)
    events = result.get("events") or []
    event = find(events, "ArtistBox", "transformed")
    if not check("a 'transformed' event for ArtistBox", event is not None,
                 json.dumps(events)[:400]):
        return
    check("tagged as the artist's, not Forge's", event.get("source") == "artist",
          str(event.get("source")))
    check("with a timestamp after the mark", event.get("t", 0) > (since or 0))
    check("and an 'ago' the caller can read without doing arithmetic",
          isinstance(event.get("ago"), (int, float)) and event["ago"] >= 0,
          str(event.get("ago")))
    check("newest first", events[0].get("t") >= events[-1].get("t"))


def test_artist_geometry():
    section("geometry is told apart from a transform")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    flush()
    settle()

    since = mark()
    obj.data.vertices[0].co.z += 0.4
    obj.data.update()
    flush()

    events = activity(since=since).get("events") or []
    event = find(events, "ArtistBox", "geometry")
    check("a 'geometry' event, not a 'transformed' one", event is not None,
          json.dumps(kinds_for(events, "ArtistBox")))
    if event is not None:
        check("still the artist's", event.get("source") == "artist",
              str(event.get("source")))
    check("no mesh was read to work that out (no vertex counts in the event)",
          event is None or not any(key in event for key in
                                   ("vertices", "vertex_count", "verts")),
          json.dumps(event or {}))


def test_added_and_removed():
    section("objects appearing and vanishing")
    settle()
    since = mark()
    make_cube("ArtistNewBox")
    flush()

    events = activity(since=since).get("events") or []
    added = find(events, "ArtistNewBox", "added")
    check("a new object is 'added'", added is not None,
          json.dumps(kinds_for(events, "ArtistNewBox")))
    check("and not also reported as a geometry edit in the same breath",
          "geometry" not in kinds_for(events, "ArtistNewBox"),
          json.dumps(kinds_for(events, "ArtistNewBox")))
    if added is not None:
        check("by the artist", added.get("source") == "artist",
              str(added.get("source")))

    settle()
    since = mark()
    drop("ArtistNewBox")
    flush()
    events = activity(since=since).get("events") or []
    removed = find(events, "ArtistNewBox", "removed")
    check("a deleted object is 'removed'", removed is not None,
          json.dumps(events)[:400])


def test_mode_is_an_event():
    section("a mode switch is news")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    flush()
    settle()

    since = mark()
    switched = False
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        switched = True
    except Exception as exc:  # noqa: BLE001
        note("mode_set refused headless (%s); the mode branch is checked directly"
             % exc)
    flush()
    events = activity(since=since).get("events") or []
    if switched:
        event = find(events, None, "mode") or next(
            (e for e in events if e.get("kind") == "mode"), None)
        check("switching to Edit is a 'mode' event", event is not None,
              json.dumps(events)[:400])
        if event is not None:
            check("carrying the mode it switched to",
                  "EDIT" in str(event.get("detail", "")),
                  str(event.get("detail")))
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:  # noqa: BLE001
            pass
        flush()
    else:
        from forge.tools import activity as feed
        feed._MODE = "SCULPT"
        feed._record_depsgraph(type("D", (), {"updates": ()})())
        event = next((e for e in (activity(since=since).get("events") or [])
                      if e.get("kind") == "mode"), None)
        check("a changed mode is recorded as a 'mode' event", event is not None)


# ---------------------------------------------------------------------------
# 3 — Forge's own work
# ---------------------------------------------------------------------------

def test_forge_command_is_tagged_forge():
    section("a Forge socket command's own mutation is Forge's")
    drop("ForgeBox")
    settle()
    since = mark()

    reply = call("load_mesh", name="ForgeBox",
                 vertices=[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
                           [0, 0, 10], [10, 0, 10], [10, 10, 10], [0, 10, 10]],
                 faces=[[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
                        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]])
    if not check("load_mesh succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    # The depsgraph delivers the command's changes AFTER the timer that ran it,
    # which is exactly the case the grace window exists for.
    flush()

    events = activity(since=since).get("events") or []
    event = find(events, "ForgeBox")
    if not check("the built object is in the feed", event is not None,
                 json.dumps(events)[:400]):
        return
    check("tagged as Forge's work, not the artist's",
          event.get("source") == "forge", str(event.get("source")))
    check("and it names the command that did it",
          event.get("command") == "load_mesh", str(event.get("command")))

    # ...and again for a mutation of something that already existed.
    settle()
    since = mark()
    moved = call("execute_python",
                 code="import bpy\nbpy.data.objects['ForgeBox'].location.x += 0.5\n")
    check("execute_python succeeded", moved.get("status") == "success",
          str(moved.get("message"))[:300])
    flush()
    events = activity(since=since).get("events") or []
    event = find(events, "ForgeBox")
    check("a Forge edit of an existing object is Forge's too",
          event is not None and event.get("source") == "forge",
          json.dumps(events)[:400])


def test_a_read_command_never_claims_credit():
    section("reading the scene does not steal the artist's next edit")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    settle()

    # get_scene_info calls refresh_view_layer(), which flushes whatever the
    # artist had pending — so this is the exact sequence that would misattribute
    # an artist edit if a read command armed the grace window.
    since = mark()
    obj.location.y += 0.3
    call("get_scene_info")
    flush()

    events = activity(since=since).get("events") or []
    event = find(events, "ArtistBox")
    if not check("the pending edit is in the feed", event is not None,
                 json.dumps(events)[:400]):
        return
    check("and it is still the ARTIST's, despite the read in between",
          event.get("source") == "artist", str(event.get("source")))

    from forge.tools import activity as feed
    check("get_activity itself claims no credit",
          not feed._claims_credit("get_activity"))
    check("nor get_scene_info", not feed._claims_credit("get_scene_info"))
    check("but load_mesh does", feed._claims_credit("load_mesh"))
    check("and so does set_mode, which is read-only only for undo's sake",
          feed._claims_credit("set_mode"))


# ---------------------------------------------------------------------------
# 4 — the shape of the report
# ---------------------------------------------------------------------------

def test_report_shape():
    section("the report answers the whole question in one call")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.scene.cursor.location = (0.012, 0.0, 0.0)
    flush()

    result = activity()
    for key in ("events", "now", "mode", "active", "selected", "cursor_mm",
                "collections_touched", "count", "matched", "truncated", "ring",
                "handlers", "watching", "kinds", "sources"):
        check("the report carries %r" % key, key in result,
              json.dumps(sorted(result))[:400])
    check("active is the object we just activated",
          result.get("active") == "ArtistBox", str(result.get("active")))
    check("selected names it too", "ArtistBox" in (result.get("selected") or []),
          json.dumps(result.get("selected")))
    check("the cursor is reported in millimetres",
          result.get("cursor_mm") and abs(result["cursor_mm"][0] - 12.0) < 0.01,
          json.dumps(result.get("cursor_mm")))
    check("mode is a string", isinstance(result.get("mode"), str),
          str(result.get("mode")))
    check("collections_touched names the collection the work is in",
          isinstance(result.get("collections_touched"), list)
          and result["collections_touched"],
          json.dumps(result.get("collections_touched")))
    check("and the clock is Blender's own, in epoch seconds",
          isinstance(result.get("now"), (int, float))
          and abs(result["now"] - time.time()) < 10.0,
          str(result.get("now")))


def test_since_filters():
    section("since filtering, on Blender's clock")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    settle()

    obj.location.z += 0.11
    flush()
    marker = mark()
    time.sleep(0.02)

    settle()
    drop("SinceBox")
    make_cube("SinceBox")
    flush()

    later = activity(since=marker).get("events") or []
    check("only what happened after the mark comes back",
          all(event["t"] > marker for event in later),
          json.dumps([e["t"] - marker for e in later])[:200])
    check("and the new object is in it",
          find(later, "SinceBox") is not None, json.dumps(later)[:400])
    check("the earlier edit is not",
          find(later, "ArtistBox", "transformed") is None
          or later[-1]["t"] > marker)

    everything = activity()
    check("without a since, the whole ring comes back",
          len(everything.get("events") or []) >= len(later))
    drop("SinceBox")
    flush()


def test_limit_and_refusals():
    section("limits, and refusals that are sentences")
    result = activity(limit=1)
    check("limit caps what comes back", len(result.get("events") or []) <= 1,
          str(len(result.get("events") or [])))
    check("and says there was more", result.get("truncated") is True
          or result.get("matched", 0) <= 1, json.dumps(result.get("matched")))

    bad = call("get_activity", since="this morning")
    check("a non-numeric 'since' is refused with a sentence",
          bad.get("status") == "error" and "number" in str(bad.get("message")),
          str(bad.get("message"))[:200])
    bad = call("get_activity", limit=0)
    check("limit 0 is refused",
          bad.get("status") == "error" and "at least 1" in str(bad.get("message")),
          str(bad.get("message"))[:200])
    bad = call("get_activity", limit="lots")
    check("a non-numeric limit is refused",
          bad.get("status") == "error", str(bad.get("message"))[:200])
    big = activity(limit=100000)
    check("an enormous limit is clamped to the ring, not refused",
          "_error" not in big, json.dumps(big.get("_error", ""))[:200])


def test_read_only_changes_nothing():
    section("get_activity is read-only in fact, not just by classification")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    flush()
    settle()

    before = {
        "objects": sorted(bpy.data.objects.keys()),
        "meshes": sorted(bpy.data.meshes.keys()),
        "location": tuple(round(v, 6) for v in obj.location),
        "vertices": len(obj.data.vertices),
        "active": getattr(bpy.context.view_layer.objects.active, "name", None),
        "selected": sorted(o.name for o in bpy.context.view_layer.objects
                           if o.select_get()),
        "mode": bpy.context.mode,
        "pointer": obj.as_pointer(),
        "mesh_pointer": obj.data.as_pointer(),
        "cursor": tuple(round(v, 6) for v in bpy.context.scene.cursor.location),
    }
    for _ in range(3):
        activity()
    after = {
        "objects": sorted(bpy.data.objects.keys()),
        "meshes": sorted(bpy.data.meshes.keys()),
        "location": tuple(round(v, 6) for v in obj.location),
        "vertices": len(obj.data.vertices),
        "active": getattr(bpy.context.view_layer.objects.active, "name", None),
        "selected": sorted(o.name for o in bpy.context.view_layer.objects
                           if o.select_get()),
        "mode": bpy.context.mode,
        "pointer": obj.as_pointer(),
        "mesh_pointer": obj.data.as_pointer(),
        "cursor": tuple(round(v, 6) for v in bpy.context.scene.cursor.location),
    }
    for key in before:
        check("get_activity left %s exactly as it was" % key,
              before[key] == after[key],
              "%r -> %r" % (before[key], after[key]))

    settle()
    since = mark()
    activity()
    activity()
    events = activity(since=since).get("events") or []
    check("and asking three times records nothing about itself",
          not events, json.dumps(events)[:300])


# ---------------------------------------------------------------------------
# 5 — the bounds
# ---------------------------------------------------------------------------

def test_ring_is_capped():
    section("the ring is capped")
    from forge.tools import activity as feed

    before = len(feed._RING)
    now = time.time()
    # Distinct names, so coalescing cannot be what keeps the number down.
    for index in range(feed.RING_LIMIT * 2):
        feed._append(now + index, "Cap%04d" % index, "transformed", "artist")
    check("the ring never grows past its limit",
          len(feed._RING) == feed.RING_LIMIT,
          "%d before, %d after, limit %d"
          % (before, len(feed._RING), feed.RING_LIMIT))
    check("and it kept the NEWEST entries",
          feed._RING[-1]["object"] == "Cap%04d" % (feed.RING_LIMIT * 2 - 1),
          feed._RING[-1]["object"])
    result = activity(limit=feed.RING_LIMIT)
    check("a full read never returns more than the ring holds",
          len(result.get("events") or []) <= feed.RING_LIMIT,
          str(len(result.get("events") or [])))
    check("and the drop is counted rather than hidden",
          (result.get("ring") or {}).get("dropped", 0) > 0,
          json.dumps(result.get("ring")))
    feed._RING.clear()


def test_a_drag_coalesces():
    section("a gesture is one event, with a count")
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    flush()
    settle()

    since = mark()
    started = time.monotonic()
    for step in range(120):
        obj.location.x += 0.001
        flush()
    elapsed = time.monotonic() - started

    events = activity(since=since).get("events") or []
    mine = [event for event in events if event.get("object") == "ArtistBox"
            and event.get("kind") == "transformed"]
    check("120 depsgraph flushes do not become 120 events",
          len(mine) <= 2, "%d events" % len(mine))
    if mine:
        check("the one event counts the whole gesture",
              mine[0].get("count", 1) > 1, str(mine[0].get("count")))
    check("and the rest of the feed still fits in the ring",
          len(events) < 120, str(len(events)))
    note("120 flushes through the handler in %.3f s (%.2f ms each)"
         % (elapsed, elapsed * 1000.0 / 120.0))
    check("the handler is cheap enough to leave on forever (<5 ms a flush)",
          elapsed / 120.0 < 0.005, "%.3f ms" % (elapsed * 1000.0 / 120.0))


# ---------------------------------------------------------------------------
# 6 — safety
# ---------------------------------------------------------------------------

def test_handlers_swallow_their_own_failures():
    section("a handler never raises into Blender")
    from forge.tools import activity as feed

    class Exploding(object):
        @property
        def updates(self):
            raise RuntimeError("the depsgraph is having a day")

    before = feed._STATS["errors"]
    raised = ""
    try:
        feed._forge_activity_depsgraph_post(None, Exploding())
    except Exception as exc:  # noqa: BLE001
        raised = "%s: %s" % (type(exc).__name__, exc)
    check("a depsgraph that raises costs an error count, not an exception",
          not raised, raised)
    check("and the failure is counted", feed._STATS["errors"] > before,
          "%d -> %d" % (before, feed._STATS["errors"]))
    check("the count is reported rather than hidden",
          (activity().get("ring") or {}).get("errors", 0) > before)

    raised = ""
    try:
        feed._forge_activity_undo_post(None)
        feed._forge_activity_redo_post(None)
        feed._forge_activity_load_post(None)
        feed._forge_activity_depsgraph_post()
    except Exception as exc:  # noqa: BLE001
        raised = "%s: %s" % (type(exc).__name__, exc)
    check("undo/redo/load handlers survive being called bare", not raised, raised)

    check("the feed still works afterwards",
          isinstance(activity().get("now"), (int, float)))


def test_undo_is_an_event():
    section("Ctrl+Z is the most important thing the artist can do")
    from forge.tools import activity as feed

    settle()
    since = mark()
    pushed = False
    try:
        bpy.ops.ed.undo_push(message="activity test")
        obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
        obj.location.x += 0.7
        flush()
        bpy.ops.ed.undo_push(message="activity test 2")
        bpy.ops.ed.undo()
        pushed = True
    except Exception as exc:  # noqa: BLE001
        note("the undo stack is not usable here (%s)" % exc)
    flush()

    if pushed:
        events = activity(since=since).get("events") or []
        check("an undo lands in the feed",
              any(event.get("kind") == "undo" for event in events),
              json.dumps([e.get("kind") for e in events])[:300])
    else:
        before = len(feed._RING)
        feed._forge_activity_undo_post(None)
        check("the undo handler records an 'undo' event",
              len(feed._RING) > before and feed._RING[-1]["kind"] == "undo")

    check("'undo' and 'redo' are declared kinds",
          "undo" in feed.EVENT_KINDS and "redo" in feed.EVENT_KINDS)


# ---------------------------------------------------------------------------
# 7 — get_scene_info's one addition
# ---------------------------------------------------------------------------

def test_scene_info_reports_mode():
    section("get_scene_info now says which mode they are in")
    reply = call("get_scene_info")
    if not check("get_scene_info succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    result = reply["result"]
    check("it carries 'mode'", "mode" in result, json.dumps(sorted(result))[:400])
    check("as Blender's own mode string",
          result.get("mode") == bpy.context.mode,
          "%r vs %r" % (result.get("mode"), bpy.context.mode))
    for key in ("objects", "active", "selected", "cursor", "cursor_mm", "scene",
                "unit_scale"):
        check("and every older field is still there: %s" % key, key in result)


# ---------------------------------------------------------------------------
# 8 — teardown
# ---------------------------------------------------------------------------

def test_unregister_cleans_up():
    section("unregister puts everything back")
    from forge.tools import activity as feed
    from forge.tools import registry

    feed.unregister()
    check("no handlers left in any of Blender's lists",
          feed.handlers_installed() == 0, str(feed.handlers_installed()))
    for list_name, _fn in feed._HANDLER_SPECS:
        handlers = getattr(bpy.app.handlers, list_name)
        check("%s is clean" % list_name,
              not [fn for fn in handlers
                   if getattr(fn, "_forge_activity_handler", False)])
    check("registry.dispatch is the original again",
          not getattr(registry.dispatch, "_forge_activity_wrapped", False))
    check("the ring is empty", len(feed._RING) == 0, str(len(feed._RING)))
    check("commands still dispatch with the wrapper gone",
          call("ping").get("status") == "success")

    # ...and a depsgraph tick after unregistering records nothing.
    obj = bpy.data.objects.get("ArtistBox") or make_cube("ArtistBox")
    obj.location.x += 0.9
    flush()
    check("and nothing is recorded once the handlers are gone",
          len(feed._RING) == 0, str(len(feed._RING)))

    feed.register()
    check("re-registering brings it back", feed.handlers_installed() == 4,
          str(feed.handlers_installed()))


def test_ports_are_free_after():
    section("ports")
    from forge import server as forge_server

    forge_server.stop_server()
    time.sleep(0.2)
    try:
        probe = socketlib.create_connection(("127.0.0.1", PORT), timeout=0.5)
        probe.close()
        free = False
    except OSError:
        free = True
    check("port %d is free after the suite" % PORT, free)


# ---------------------------------------------------------------------------

def main():
    print("Forge headless tests - live activity feed")
    print("Blender %s" % bpy.app.version_string)

    enable_addon()

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)

    try:
        test_registration()
        test_registration_is_idempotent()
        test_artist_transform()
        test_artist_geometry()
        test_added_and_removed()
        test_mode_is_an_event()
        test_forge_command_is_tagged_forge()
        test_a_read_command_never_claims_credit()
        test_report_shape()
        test_since_filters()
        test_limit_and_refusals()
        test_read_only_changes_nothing()
        test_ring_is_capped()
        test_a_drag_coalesces()
        test_handlers_swallow_their_own_failures()
        test_undo_is_an_event()
        test_scene_info_reports_mode()
        test_unregister_cleans_up()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_ports_are_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
