"""Headless add-on tests for Phase 19 — ``build_floorplan``.

ONE Blender launch covers the whole command, on purpose: every extra
``--background`` run is another flash on the artist's machine, so registration,
the first build, four separate incremental edits, the never-clobber rule, the
rebuild escape hatch and every refusal are tested together, in one process, in
one file.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_floorplan.py

The socket port is **9905** (9876 belongs to a live session, 9879-9904 to the
earlier suites). No geometry service, no Claude CLI, no network beyond loopback,
no files on disk: the plan is a Python dict in this file and every right answer
below is arithmetic.

The fixture, and why every expectation is a number
--------------------------------------------------
A two-room flat, 7000 x 3000 mm outside, split by a party wall at x = 4000 with
one 820 mm door in it, plus a washer/dryer placeholder::

    (0,3000)                                        (7000,3000)
        +---------------------+----------------------+
        |                     |                      |
        |      room-left      |      room-right      |
        |        [wd]         D                      |
        +---------------------+----------------------+
    (0,0)                  (4000,0)               (7000,0)

Eight objects: two floor slabs, five walls, one fixture box.  The party wall is
3000 mm long, 100 mm thick, 2400 mm high with a 820 x 2040 mm door centred at
1500 mm along it — so the door runs 1090..1910 mm, the wall builds as exactly
**three boxes** (a pier either side and one header over the door), and that is
**24 vertices and 18 faces**, asserted rather than eyeballed.  Not one vertex
may sit in the void the door cut.

What is actually being proved
-----------------------------
1. the command is registered, is **not** read-only (it builds objects, so Ctrl+Z
   has to reach it), and is a legal flow step — proven by running a flow, not by
   reading a list;
2. **the first build** — object count, names, per-object vertex/face counts,
   millimetre dimensions, the door cutout's exact X and Z coordinates, materials
   by kind, the floor slab below z = 0, the fixture's placement and spin, and a
   revolute mechanism record for the swinging door;
3. **THE INCREMENTAL LAW**, four ways, each asserted on
   ``as_pointer()`` identity rather than on the report: one wall moved touches
   only that wall's mesh (every other object *and mesh datablock* is the same
   allocation); a new fixture only creates; a removed fixture only deletes; and
   a changed default rebuilds every wall while the floors and the fixture are
   not touched at all;
4. **never clobber** — a hand-scaled fixture whose plan entry then changes is
   *kept*, with a warning naming it and a reason; so is an object carrying
   ``forge_fp_keep``, and so is an object Forge did not build;
5. **rebuild** replaces everything (new allocations for every object) and still
   honours ``forge_fp_keep``, because an explicit marker outranks a mode;
6. **refusals are sentences** — duplicate ids, an opening wider than its wall, a
   label with no footprint, an unknown kind (with the ``difflib`` near miss), an
   unknown mode, a plan in the wrong units, overlapping openings, an opening off
   the end of its wall, a zero-length wall, a two-point room, an empty plan and
   no plan at all.
"""

import json
import math
import os
import socket as socketlib
import sys
import threading
import time
import traceback

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9905  # not 9876 (a live session) and not 9879..9904 (every other suite)

COLLECTION = "Floorplan"
PREFIX = "FP:"

#: The fixture's numbers, spelled once so the assertions below can be arithmetic.
FLAT_W = 7000.0
FLAT_D = 3000.0
SPLIT_X = 4000.0
WALL_T = 100.0
CEILING = 2400.0
DOOR_W = 820.0
DOOR_H = 2040.0
DOOR_AT = 1500.0
DOOR_START = DOOR_AT - DOOR_W / 2.0   # 1090
DOOR_END = DOOR_AT + DOOR_W / 2.0     # 1910
FLOOR_T = 50.0
WD_SIZE = (600.0, 600.0)
WD_H = 850.0

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


def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


# --- socket harness ---------------------------------------------------------

def _roundtrip(payload, timeout=120.0):
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


def build(**params):
    return _roundtrip({"type": "build_floorplan", "params": params})


def flow_run(**params):
    return _roundtrip({"type": "flow_run", "params": params})


def ok(reply):
    return reply.get("status") == "success"


def result(reply):
    return reply.get("result") or {}


def message(reply):
    return reply.get("message") or ""


# --- fixtures ---------------------------------------------------------------

def plan(**overrides):
    """The two-room flat. ``overrides`` replace whole top-level sections."""
    doc = {
        "version": 1,
        "units": "mm",
        "defaults": {"ceiling_mm": CEILING, "wall_mm": WALL_T,
                     "door_w_mm": DOOR_W, "door_h_mm": DOOR_H,
                     "floor_mm": FLOOR_T},
        "rooms": [
            {"id": "room-left", "label": "living",
             "polygon_mm": [[0, 0], [SPLIT_X, 0], [SPLIT_X, FLAT_D], [0, FLAT_D]]},
            {"id": "room-right", "label": "kitchen",
             "polygon_mm": [[SPLIT_X, 0], [FLAT_W, 0],
                            [FLAT_W, FLAT_D], [SPLIT_X, FLAT_D]]},
        ],
        "walls": [
            {"id": "wall-s", "from_mm": [0, 0], "to_mm": [FLAT_W, 0]},
            {"id": "wall-e", "from_mm": [FLAT_W, 0], "to_mm": [FLAT_W, FLAT_D]},
            {"id": "wall-n", "from_mm": [FLAT_W, FLAT_D], "to_mm": [0, FLAT_D]},
            {"id": "wall-w", "from_mm": [0, FLAT_D], "to_mm": [0, 0]},
            {"id": "wall-mid", "from_mm": [SPLIT_X, 0], "to_mm": [SPLIT_X, FLAT_D],
             "openings": [{"id": "door-01", "kind": "door", "at_mm": DOOR_AT,
                           "swing": "in", "hinge": "left"}]},
        ],
        "labels": [
            {"id": "wd-01", "label": "washer/dryer",
             "footprint_mm": [1000, 500, WD_SIZE[0], WD_SIZE[1]],
             "height_mm": WD_H, "source": "library"},
        ],
    }
    doc.update(overrides)
    return doc


ALL_IDS = ("room-left", "room-right", "wall-s", "wall-e", "wall-n", "wall-w",
           "wall-mid", "wd-01")


def wipe():
    """A clean file AND a clean collection, so object counts start at zero."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)


def obj_of(ident):
    return bpy.data.objects.get(PREFIX + ident)


def pointers():
    """``{name: (object allocation, mesh allocation)}`` for the whole level.

    ``as_pointer()`` is the only honest answer to "is this the same object?" —
    a name survives a delete-and-recreate and a report can say anything.
    """
    out = {}
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return out
    for obj in collection.all_objects:
        if obj.name.startswith(PREFIX):
            out[obj.name] = (obj.as_pointer(), obj.data.as_pointer())
    return out


def verts_mm(ident):
    """One object's own (local) vertex coordinates, in millimetres."""
    mesh = obj_of(ident).data
    return [(v.co.x * 1000.0, v.co.y * 1000.0, v.co.z * 1000.0)
            for v in mesh.vertices]


def dims_mm(ident):
    """Local bounding-box size in millimetres, scale included."""
    obj = obj_of(ident)
    points = verts_mm(ident)
    return [round((max(p[a] for p in points) - min(p[a] for p in points))
                  * abs(obj.scale[a]), 3) for a in range(3)]


def near(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


def near_all(values, expected, tol=0.01):
    return len(values) == len(expected) and all(
        near(a, b, tol) for a, b in zip(values, expected))


# ---------------------------------------------------------------------------
# 1. registration
# ---------------------------------------------------------------------------

def test_registration():
    section("registration and undo classification")
    from forge.tools import registry

    check("build_floorplan is a registered command",
          registry.has_command("build_floorplan"))
    check("it is NOT read-only — it builds objects, Ctrl+Z must reach it",
          "build_floorplan" not in registry.READ_ONLY_COMMANDS)
    check("the undo step is named for the command",
          registry.undo_message("build_floorplan") == "Forge: build_floorplan")

    from forge.tools import floorplan

    check("objects are named FP:<id>", floorplan.PREFIX == "FP:")
    check("the default collection is Floorplan",
          floorplan.DEFAULT_COLLECTION == COLLECTION)

    # The fingerprint is the diff, so it had better be a function of the
    # resolved values and nothing else.
    entry = {"kind": "label", "id": "a", "size_mm": [1.0, 2.0]}
    same = {"id": "a", "size_mm": [1.0, 2.0], "kind": "label"}
    other = {"kind": "label", "id": "a", "size_mm": [1.0, 2.5]}
    check("the fingerprint ignores key order",
          floorplan.fingerprint(entry) == floorplan.fingerprint(same))
    check("the fingerprint notices a changed millimetre",
          floorplan.fingerprint(entry) != floorplan.fingerprint(other))

    spec = floorplan.resolve_plan(plan())
    check("resolving the plan fills in every default",
          spec["walls"][0]["height_mm"] == CEILING
          and spec["walls"][0]["thickness_mm"] == WALL_T,
          str(spec["walls"][0])[:160])
    mid = [w for w in spec["walls"] if w["id"] == "wall-mid"][0]
    door = mid["openings"][0]
    check("at_mm is the opening's CENTRE along the wall",
          near(door["start_mm"], DOOR_START) and near(door["end_mm"], DOOR_END),
          "%s..%s" % (door["start_mm"], door["end_mm"]))
    check("resolving the plan touched nothing in the scene",
          not [o for o in bpy.data.objects if o.name.startswith(PREFIX)])


# ---------------------------------------------------------------------------
# 2. the first build
# ---------------------------------------------------------------------------

def test_first_build():
    section("the first build — two rooms, five walls, one door, one fixture")
    wipe()
    reply = build(plan=plan())
    if not check("the level built", ok(reply), message(reply)[:400]):
        return
    data = result(reply)
    note("%d objects in %.3f s" % (data["objects"], data["seconds"]))

    check("eight objects", data["objects"] == 8, str(data["objects"]))
    check("all eight are reported as built",
          sorted(data["built"]) == sorted(ALL_IDS), str(data["built"]))
    check("nothing was updated, deleted or kept on a first build",
          not data["updated"] and not data["deleted"] and not data["kept"],
          str(data)[:200])
    check("the counts add up", (data["walls"], data["openings"],
                                data["fixtures"], data["floors"])
          == (5, 1, 1, 2),
          str((data["walls"], data["openings"], data["fixtures"], data["floors"])))
    check("every object is FP:-prefixed and in the Floorplan collection",
          data["object_names"] == sorted(PREFIX + i for i in ALL_IDS),
          str(data["object_names"]))
    check("the collection is the default one", data["collection"] == COLLECTION)
    check("the mode defaulted to update", data["mode"] == "update")

    for ident in ALL_IDS:
        if obj_of(ident) is None:
            check("FP:%s exists" % ident, False)
            return
    check("every plan id became an object", True)

    collection = bpy.data.collections.get(COLLECTION)
    check("the Floorplan collection is linked into the scene",
          collection is not None
          and collection.name in {c.name for c in bpy.context.scene.collection.children})

    # --- the honest sizes ---------------------------------------------------
    check("the south wall is 7000 x 100 x 2400 mm",
          near_all(dims_mm("wall-s"), [FLAT_W, WALL_T, CEILING]),
          str(dims_mm("wall-s")))
    check("the party wall is 3000 x 100 x 2400 mm",
          near_all(dims_mm("wall-mid"), [FLAT_D, WALL_T, CEILING]),
          str(dims_mm("wall-mid")))
    check("a floor slab is 4000 x 3000 x 50 mm",
          near_all(dims_mm("room-left"), [SPLIT_X, FLAT_D, FLOOR_T]),
          str(dims_mm("room-left")))
    check("the washer/dryer is 600 x 600 x 850 mm",
          near_all(dims_mm("wd-01"), [WD_SIZE[0], WD_SIZE[1], WD_H]),
          str(dims_mm("wd-01")))

    # --- the door is a real cutout, not a decal -----------------------------
    mid = obj_of("wall-mid")
    check("the party wall is three boxes: two piers and one header",
          (len(mid.data.vertices), len(mid.data.polygons)) == (24, 18),
          "%d verts, %d faces" % (len(mid.data.vertices), len(mid.data.polygons)))
    xs = sorted({round(p[0], 3) for p in verts_mm("wall-mid")})
    zs = sorted({round(p[2], 3) for p in verts_mm("wall-mid")})
    check("its X breaks are 0, 1090, 1910, 3000 — the door's own edges",
          near_all(xs, [0.0, DOOR_START, DOOR_END, FLAT_D]), str(xs))
    check("its Z breaks are 0, 2040, 2400 — floor, door head, ceiling",
          near_all(zs, [0.0, DOOR_H, CEILING]), str(zs))
    inside = [p for p in verts_mm("wall-mid")
              if DOOR_START + 0.01 < p[0] < DOOR_END - 0.01]
    check("no vertex sits inside the doorway's width", not inside, str(inside[:3]))
    void = [p for p in verts_mm("wall-mid")
            if DOOR_START - 0.01 <= p[0] <= DOOR_END + 0.01
            and 0.01 < p[2] < DOOR_H - 0.01]
    check("and none in the doorway's opening", not void, str(void[:3]))
    solid = obj_of("wall-s")
    check("a wall with no openings is one box",
          (len(solid.data.vertices), len(solid.data.polygons)) == (8, 6),
          "%d verts, %d faces" % (len(solid.data.vertices), len(solid.data.polygons)))

    # --- placement ----------------------------------------------------------
    check("a wall's origin is its from_mm end",
          near_all([c * 1000.0 for c in mid.location], [SPLIT_X, 0.0, 0.0]),
          str(list(mid.location)))
    check("and it is rotated along its own centreline (90 degrees here)",
          near(math.degrees(mid.rotation_euler.z), 90.0, 1e-4),
          str(math.degrees(mid.rotation_euler.z)))
    wd = obj_of("wd-01")
    check("a label stands at the centre of its footprint",
          near_all([c * 1000.0 for c in wd.location], [1000.0, 500.0, 0.0]),
          str(list(wd.location)))
    check("every object is left at scale 1",
          all(near(c, 1.0, 1e-6) for c in wd.scale), str(list(wd.scale)))

    floor_z = [p[2] for p in verts_mm("room-left")]
    check("the floor slab hangs below z=0 so the walls stand on it",
          near(max(floor_z), 0.0) and near(min(floor_z), -FLOOR_T),
          "%g..%g" % (min(floor_z), max(floor_z)))

    # --- materials ----------------------------------------------------------
    check("walls wear one grey material",
          obj_of("wall-s").data.materials[0].name == "Forge FP Wall",
          str([m.name for m in obj_of("wall-s").data.materials]))
    check("floors wear the darker one",
          obj_of("room-left").data.materials[0].name == "Forge FP Floor")
    check("fixtures wear the accent one",
          obj_of("wd-01").data.materials[0].name == "Forge FP Fixture")
    check("the three materials were made once and shared",
          len([m for m in bpy.data.materials if m.name.startswith("Forge FP")]) == 3,
          str([m.name for m in bpy.data.materials]))

    # --- the fingerprint is on the objects ----------------------------------
    check("every object carries a fingerprint",
          all(obj_of(i).get("forge_fp_hash") for i in ALL_IDS))
    check("and the id, kind, vertex count and dimensions it was built with",
          obj_of("wall-mid").get("forge_fp_id") == "wall-mid"
          and obj_of("wall-mid").get("forge_fp_kind") == "wall"
          and int(obj_of("wall-mid").get("forge_fp_verts")) == 24,
          str(dict(obj_of("wall-mid").items()))[:220])

    # --- bounds and the report's voice --------------------------------------
    check("the level measures 7000 x 3000 mm on the floor",
          near(data["dimensions_mm"][0], FLAT_W + WALL_T, 1.0)
          and near(data["dimensions_mm"][1], FLAT_D + WALL_T, 1.0),
          str(data["dimensions_mm"]))
    check("bounds carry min, max and size",
          set(data["bounds_mm"]) == {"min", "max", "size"}, str(data["bounds_mm"]))
    check("the report says out loud that this is a greybox",
          "greybox" in data["honesty"] and "construction drawing" in data["honesty"])
    check("and that it is at real sizes, not a drawing",
          "real sizes" in data["honesty"])
    check("it carries notes, warnings and seconds",
          isinstance(data["notes"], list) and isinstance(data["warnings"], list)
          and isinstance(data["seconds"], float))

    # --- the mechanism record ----------------------------------------------
    mechanisms = data["mechanisms"]
    if not check("the swinging door got one mechanism record",
                 len(mechanisms) == 1, str(mechanisms)):
        return
    record = mechanisms[0]
    check("it is revolute about +Z, the hinge edge of a door",
          record["joint_type"] == "revolute" and record["axis"] == [0.0, 0.0, 1.0],
          str(record))
    check("its range is 90 degrees", record["range_deg"] == 90)
    check("its origin is the hinge end of the opening, on the centreline",
          near_all(record["origin_mm"], [SPLIT_X, DOOR_START, 0.0]),
          str(record["origin_mm"]))
    check("a left-hung door swinging in turns the positive way about +Z",
          record["direction"] == 1 and record["swing"] == "in"
          and record["hinge"] == "left", str(record))
    check("it names the wall object it belongs to",
          record["object"] == "FP:wall-mid" and record["wall"] == "wall-mid")
    check("and says nothing was rigged", "rigged" in record["note"])
    check("no leaf object was built for it", obj_of("door-01") is None)


# ---------------------------------------------------------------------------
# 3. THE INCREMENTAL LAW
# ---------------------------------------------------------------------------

def test_moving_one_wall_touches_one_wall():
    section("the incremental law — one wall moved, one wall rebuilt")
    before = pointers()
    if not check("eight objects to start from", len(before) == 8, str(len(before))):
        return
    old_mid_verts = len(obj_of("wall-mid").data.vertices)

    doc = plan()
    for wall in doc["walls"]:
        if wall["id"] == "wall-s":
            wall["to_mm"] = [FLAT_W + 1500.0, 0]   # the one edit
    reply = build(plan=doc)
    if not check("the edited plan built", ok(reply), message(reply)[:400]):
        return
    data = result(reply)

    check("exactly one entry was updated",
          data["updated"] == ["wall-s"], str(data["updated"]))
    check("nothing was built or deleted",
          not data["built"] and not data["deleted"], str(data)[:200])
    check("the other seven are reported unchanged",
          sorted(data["unchanged"]) == sorted(i for i in ALL_IDS if i != "wall-s"),
          str(data["unchanged"]))
    after = pointers()
    check("still eight objects", len(after) == 8, str(len(after)))
    check("EVERY object is the same allocation — even the rebuilt one",
          all(after[name][0] == before[name][0] for name in before),
          str([n for n in before if after.get(n, (0,))[0] != before[n][0]]))
    moved_mesh = [n for n in before if after[n][1] != before[n][1]]
    check("and exactly one mesh datablock was replaced: the wall that moved",
          moved_mesh == ["FP:wall-s"], str(moved_mesh))
    check("the wall really did get longer",
          near(dims_mm("wall-s")[0], FLAT_W + 1500.0), str(dims_mm("wall-s")))
    check("the untouched party wall still has its door cut in it",
          len(obj_of("wall-mid").data.vertices) == old_mid_verts == 24)

    # Calling again with the SAME plan must be a complete no-op.
    again = result(build(plan=doc))
    check("a second call with the same plan touches nothing at all",
          len(again["unchanged"]) == 8 and not again["built"]
          and not again["updated"] and not again["deleted"],
          str(again)[:200])
    check("and every allocation survives it, objects and meshes both",
          pointers() == after)
    check("a no-op call says so in its notes",
          any("Nothing changed" in n for n in again["notes"]), str(again["notes"]))


def test_adding_a_fixture_only_creates():
    section("the incremental law — an addition is an addition")
    before = pointers()
    doc = plan()
    for wall in doc["walls"]:
        if wall["id"] == "wall-s":
            wall["to_mm"] = [FLAT_W + 1500.0, 0]   # keep the previous edit
    doc["labels"].append({"id": "fridge-01", "label": "fridge",
                          "footprint_mm": [5500, 2500, 700, 700],
                          "height_mm": 1800, "rotation_deg": 90})
    data = result(build(plan=doc))

    check("only the new fixture was built",
          data["built"] == ["fridge-01"], str(data["built"]))
    check("nothing was updated or deleted",
          not data["updated"] and not data["deleted"], str(data)[:200])
    check("nine objects now", data["objects"] == 9, str(data["objects"]))
    after = pointers()
    check("every pre-existing object and mesh is the same allocation",
          all(after[name] == before[name] for name in before),
          str([n for n in before if after.get(n) != before[n]]))
    check("the fridge is 700 x 700 x 1800 mm",
          near_all(dims_mm("fridge-01"), [700.0, 700.0, 1800.0]),
          str(dims_mm("fridge-01")))
    check("and it was spun 90 degrees as asked",
          near(math.degrees(obj_of("fridge-01").rotation_euler.z), 90.0, 1e-4),
          str(math.degrees(obj_of("fridge-01").rotation_euler.z)))
    return doc


def test_removing_a_fixture_only_deletes(doc):
    section("the incremental law — a removal removes one thing")
    before = pointers()
    doc = json.loads(json.dumps(doc))
    doc["labels"] = [entry for entry in doc["labels"] if entry["id"] != "fridge-01"]
    data = result(build(plan=doc))

    check("only the removed fixture was deleted",
          data["deleted"] == ["fridge-01"], str(data["deleted"]))
    check("nothing was built or updated",
          not data["built"] and not data["updated"], str(data)[:200])
    check("eight objects again", data["objects"] == 8, str(data["objects"]))
    check("the object really is gone", obj_of("fridge-01") is None)
    after = pointers()
    check("and every survivor is the same allocation, object and mesh",
          all(after[name] == before[name] for name in after),
          str([n for n in after if after[n] != before.get(n)]))
    return doc


def test_a_changed_default_rebuilds_only_what_it_reaches(doc):
    section("the incremental law — a default is resolved into every entry")
    before = pointers()
    doc = json.loads(json.dumps(doc))
    doc["defaults"]["ceiling_mm"] = 2700.0
    data = result(build(plan=doc))

    check("every wall was updated by the taller ceiling",
          sorted(data["updated"]) == sorted(["wall-s", "wall-e", "wall-n",
                                             "wall-w", "wall-mid"]),
          str(data["updated"]))
    check("the floors and the fixture were not",
          sorted(data["unchanged"]) == sorted(["room-left", "room-right", "wd-01"]),
          str(data["unchanged"]))
    after = pointers()
    untouched = ["FP:room-left", "FP:room-right", "FP:wd-01"]
    check("their allocations are identical, meshes included",
          all(after[name] == before[name] for name in untouched),
          str([n for n in untouched if after[n] != before[n]]))
    check("the walls are 2700 mm high now",
          near(dims_mm("wall-s")[2], 2700.0), str(dims_mm("wall-s")))
    check("and the door head is still at 2040 mm",
          near(sorted({round(p[2], 3) for p in verts_mm("wall-mid")})[1], DOOR_H),
          str(sorted({round(p[2], 3) for p in verts_mm("wall-mid")})))
    doc["defaults"]["ceiling_mm"] = CEILING
    build(plan=doc)
    return doc


# ---------------------------------------------------------------------------
# 4. never clobber the artist's hands
# ---------------------------------------------------------------------------

def test_a_hand_scaled_fixture_is_kept(doc):
    section("never clobber — a promoted placeholder is skipped, never overwritten")
    doc = json.loads(json.dumps(doc))
    wd = obj_of("wd-01")
    wd.scale = (2.0, 2.0, 1.0)           # the artist made it bigger by hand
    before_mesh = wd.data.as_pointer()
    before_pointer = wd.as_pointer()

    for label in doc["labels"]:
        if label["id"] == "wd-01":
            label["height_mm"] = 1000.0  # ... and the plan then disagrees

    data = result(build(plan=doc))
    kept = {item["id"]: item for item in data["kept"]}
    if not check("the hand-scaled fixture was kept, not rebuilt",
                 "wd-01" in kept, str(data["kept"])):
        return doc
    check("the report says which object and why",
          kept["wd-01"]["object"] == "FP:wd-01"
          and "scaled" in kept["wd-01"]["why"],
          str(kept["wd-01"]))
    check("a warning names it too",
          any("FP:wd-01" in w for w in data["warnings"]), str(data["warnings"]))
    check("it is not in built, updated or deleted",
          "wd-01" not in data["built"] and "wd-01" not in data["updated"]
          and "wd-01" not in data["deleted"], str(data)[:240])
    check("the object survived untouched — same object, same mesh",
          obj_of("wd-01").as_pointer() == before_pointer
          and obj_of("wd-01").data.as_pointer() == before_mesh)
    check("and the artist's scale is still on it",
          near(obj_of("wd-01").scale[0], 2.0), str(list(obj_of("wd-01").scale)))
    check("the walls around it still rebuilt normally",
          data["objects"] == 8, str(data["objects"]))

    obj_of("wd-01").scale = (1.0, 1.0, 1.0)
    return doc


def test_forge_fp_keep_is_an_unconditional_hands_off(doc):
    section("never clobber — forge_fp_keep")
    doc = json.loads(json.dumps(doc))
    wall = obj_of("wall-e")
    wall["forge_fp_keep"] = True
    before = wall.data.as_pointer()

    for entry in doc["walls"]:
        if entry["id"] == "wall-e":
            entry["thickness_mm"] = 250.0

    data = result(build(plan=doc))
    kept = {item["id"]: item for item in data["kept"]}
    check("a wall marked forge_fp_keep is kept",
          "wall-e" in kept, str(data["kept"]))
    check("and the reason names the marker",
          "forge_fp_keep" in kept.get("wall-e", {}).get("why", ""),
          str(kept.get("wall-e")))
    check("its mesh was never replaced",
          obj_of("wall-e").data.as_pointer() == before)
    check("it is still 100 mm thick, the way the artist left it",
          near(dims_mm("wall-e")[1], WALL_T), str(dims_mm("wall-e")))
    return doc


def test_an_object_forge_did_not_build_is_kept(doc):
    section("never clobber — an FP: object with no fingerprint")
    doc = json.loads(json.dumps(doc))
    doc["labels"].append({"id": "sofa-01", "label": "sofa",
                          "footprint_mm": [2000, 1500, 2000, 900]})
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    intruder = bpy.context.view_layer.objects.active
    intruder.name = "FP:sofa-01"
    collection = bpy.data.collections.get(COLLECTION)
    for other in list(intruder.users_collection):
        other.objects.unlink(intruder)
    collection.objects.link(intruder)
    before = intruder.as_pointer(), intruder.data.as_pointer()

    data = result(build(plan=doc))
    kept = {item["id"]: item for item in data["kept"]}
    check("an FP: object Forge did not build is kept, not overwritten",
          "sofa-01" in kept, str(data["kept"]))
    check("and the reason says exactly that",
          "fingerprint" in kept.get("sofa-01", {}).get("why", ""),
          str(kept.get("sofa-01")))
    check("its allocation and mesh both survive",
          (obj_of("sofa-01").as_pointer(), obj_of("sofa-01").data.as_pointer())
          == before)

    # And it is not deleted when its id leaves the plan again, either.
    doc["labels"] = [e for e in doc["labels"] if e["id"] != "sofa-01"]
    data = result(build(plan=doc))
    check("nor is it deleted when the plan stops mentioning it",
          obj_of("sofa-01") is not None and "sofa-01" not in data["deleted"],
          str(data["deleted"]))
    check("it is reported as kept, with a reason, instead",
          any(item["id"] == "sofa-01" for item in data["kept"]), str(data["kept"]))

    bpy.data.objects.remove(obj_of("sofa-01"), do_unlink=True)
    return doc


# ---------------------------------------------------------------------------
# 5. rebuild
# ---------------------------------------------------------------------------

def test_rebuild_replaces_everything(doc):
    section("mode:rebuild — the explicit full regen")
    doc = json.loads(json.dumps(doc))
    for label in doc["labels"]:
        if label["id"] == "wd-01":
            label["height_mm"] = WD_H       # back to the plan's own number
    # Identity across a delete-and-recreate is proved with a marker rather than
    # with as_pointer(): Blender's allocator is entitled to hand the freed
    # address straight back, and a test that passes because of the allocator is
    # a test that fails on a Tuesday.
    for ident in ALL_IDS:
        obj_of(ident)["forge_probe"] = "before the rebuild"
    kept_wall = obj_of("wall-e").as_pointer()

    data = result(build(plan=doc, mode="rebuild"))
    check("the mode is echoed back", data["mode"] == "rebuild")
    check("every entry Forge owns was built afresh",
          sorted(data["built"]) == sorted(i for i in ALL_IDS if i != "wall-e"),
          str(data["built"]))
    check("nothing is reported as unchanged",
          not data["unchanged"], str(data["unchanged"]))
    check("and nothing as deleted — a rebuilt id is not a removed one",
          not data["deleted"], str(data["deleted"]))
    survivors = [i for i in ALL_IDS if obj_of(i).get("forge_probe")]
    check("every rebuilt object is a NEW object — the marker did not survive",
          survivors == ["wall-e"], str(survivors))
    check("forge_fp_keep held even in rebuild",
          obj_of("wall-e").as_pointer() == kept_wall
          and any(item["id"] == "wall-e" for item in data["kept"]),
          str(data["kept"]))
    check("a warning says the marker outranked the mode",
          any("rebuild" in w and "FP:wall-e" in w for w in data["warnings"]),
          str(data["warnings"]))
    check("the notes admit hand edits are gone",
          any("thrown away" in n for n in data["notes"]), str(data["notes"]))
    check("the washer/dryer is back to the plan's 850 mm",
          near(dims_mm("wd-01")[2], WD_H), str(dims_mm("wd-01")))
    del obj_of("wall-e")["forge_fp_keep"]


# ---------------------------------------------------------------------------
# 6. floors, collections and the other knobs
# ---------------------------------------------------------------------------

def test_floor_false_and_a_named_collection():
    section("floor:false, and a collection of the artist's choosing")
    wipe()
    data = result(build(plan=plan(), floor=False, collection="Level A"))
    check("the collection Forge was pointed at is the one it used",
          data["collection"] == "Level A", str(data["collection"]))
    check("six objects — five walls and one fixture, no slabs",
          data["objects"] == 6, str(data["objects"]))
    check("no floors were counted", data["floors"] == 0, str(data["floors"]))
    check("and the report says why",
          any("floor:false" in n for n in data["notes"]), str(data["notes"]))
    check("the rooms' ids built nothing",
          obj_of("room-left") is None and obj_of("room-right") is None)

    # Turning floors back on is an addition, not a regen.
    before = len(bpy.data.objects)
    data = result(build(plan=plan(), collection="Level A"))
    check("turning floors on adds the two slabs and nothing else",
          sorted(data["built"]) == ["room-left", "room-right"], str(data["built"]))
    check("and leaves the six that were there alone",
          len(data["unchanged"]) == 6, str(data["unchanged"]))
    check("eight objects now", len(bpy.data.objects) == before + 2)

    # And off again deletes only the slabs.
    data = result(build(plan=plan(), floor=False, collection="Level A"))
    check("turning them off again deletes only the slabs",
          sorted(data["deleted"]) == ["room-left", "room-right"],
          str(data["deleted"]))

    # A single room can opt out on its own: per-room "floor": false is the
    # service half's convention, and the two halves must agree on it.
    doc = plan()
    doc["rooms"][0]["floor"] = False
    data = result(build(plan=doc, collection="Level A"))
    check("a per-room floor:false builds only the other room's slab",
          data["built"] == ["room-right"], str(data["built"]))
    check("  ... the opted-out room's id built nothing",
          obj_of("room-left") is None)
    doc["rooms"][0]["floor"] = "yes"
    reply = build(plan=doc, collection="Level A")
    check("a non-bool per-room floor flag is refused with a sentence",
          not ok(reply) and "true or false" in message(reply).lower(),
          message(reply)[:200])
    wipe()


def test_windows_gaps_and_an_L_shaped_room():
    section("a window keeps its sill, a gap runs floor to ceiling, an L has a floor")
    wipe()
    doc = {
        "units": "mm",
        "defaults": {"ceiling_mm": 2400, "wall_mm": 100},
        "rooms": [{"id": "room-L", "polygon_mm": [[0, 0], [4000, 0], [4000, 2000],
                                                  [2000, 2000], [2000, 4000],
                                                  [0, 4000]]}],
        "walls": [
            {"id": "w-window", "from_mm": [0, 0], "to_mm": [4000, 0],
             "openings": [{"id": "win-01", "kind": "window", "at_mm": 2000,
                           "width_mm": 1200, "height_mm": 1200, "sill_mm": 900}]},
            {"id": "w-gap", "from_mm": [0, 4000], "to_mm": [4000, 4000],
             "openings": [{"id": "gap-01", "kind": "gap", "at_mm": 2000,
                           "width_mm": 1000}]},
        ],
    }
    data = result(build(plan=doc))
    check("the L-shaped plan built", data["objects"] == 3, str(data["objects"]))

    window_zs = sorted({round(p[2], 3) for p in verts_mm("w-window")})
    check("a window leaves a sill below and a header above",
          near_all(window_zs, [0.0, 900.0, 2100.0, 2400.0]), str(window_zs))
    check("so its wall is four boxes: two piers, a sill and a header",
          len(obj_of("w-window").data.vertices) == 32,
          str(len(obj_of("w-window").data.vertices)))

    gap_zs = sorted({round(p[2], 3) for p in verts_mm("w-gap")})
    check("a gap runs floor to ceiling — no header at all",
          near_all(gap_zs, [0.0, 2400.0]), str(gap_zs))
    check("so its wall is two separate piers",
          len(obj_of("w-gap").data.vertices) == 16,
          str(len(obj_of("w-gap").data.vertices)))

    slab = obj_of("room-L")
    check("the L-shaped slab is 4000 x 4000 mm overall",
          near_all(dims_mm("room-L")[:2], [4000.0, 4000.0]), str(dims_mm("room-L")))
    check("it has one vertex ring top and bottom (6 corners each)",
          len(slab.data.vertices) == 12, str(len(slab.data.vertices)))
    check("and it is tessellated, not fanned — an L needs 4 triangles a cap",
          len(slab.data.polygons) == 6 + 4 + 4, str(len(slab.data.polygons)))
    check("no mechanism records — nothing here swings",
          not data["mechanisms"], str(data["mechanisms"]))
    wipe()


# ---------------------------------------------------------------------------
# 7. refusals, each a sentence
# ---------------------------------------------------------------------------

def test_refusals():
    section("refusals — sentences, not stack traces")
    wipe()

    def refuse(label, expect, **params):
        reply = build(**params)
        text = message(reply)
        if not check("%s is refused" % label, not ok(reply), str(result(reply))[:200]):
            return
        check("  ... and the sentence says %r" % expect,
              expect.lower() in text.lower(), text[:260])
        check("  ... and nothing was built",
              not [o for o in bpy.data.objects if o.name.startswith(PREFIX)])

    refuse("no plan at all", "pass the floorplan.json object")

    doc = plan()
    doc["labels"].append({"id": "wall-s", "label": "clash",
                          "footprint_mm": [0, 0, 100, 100]})
    refuse("a duplicate id", "unique", plan=doc)

    doc = plan()
    doc["walls"] = [{"id": "tiny", "from_mm": [0, 0], "to_mm": [700, 0],
                     "openings": [{"id": "door-x", "kind": "door",
                                   "width_mm": 900, "at_mm": 350}]}]
    refuse("an opening wider than its wall", "only 700 mm long", plan=doc)

    doc = plan()
    doc["labels"] = [{"id": "no-box", "label": "mystery"}]
    refuse("a label with no footprint", "footprint_mm", plan=doc)

    doc = plan()
    doc["walls"][4]["openings"][0]["kind"] = "windo"
    reply = build(plan=doc)
    check("an unknown opening kind is refused", not ok(reply))
    check("  ... and difflib offers the near miss",
          "did you mean 'window'" in message(reply).lower(), message(reply)[:240])

    doc = plan()
    doc["walls"][4]["openings"][0]["at_mm"] = 2900.0
    refuse("an opening hanging off the end of its wall", "hangs off the end", plan=doc)

    doc = plan()
    doc["walls"][4]["openings"].append(
        {"id": "door-02", "kind": "door", "at_mm": DOOR_AT + 200})
    refuse("two openings overlapping", "overlap", plan=doc)

    doc = plan()
    doc["walls"].append({"id": "nowhere", "from_mm": [10, 10], "to_mm": [10, 10]})
    refuse("a zero-length wall", "same place", plan=doc)

    doc = plan()
    doc["rooms"][0]["polygon_mm"] = [[0, 0], [1000, 0]]
    refuse("a room with two corners", "three", plan=doc)

    doc = plan()
    doc["rooms"][0]["polygon_mm"] = [[0, 0], [1000, 0], [2000, 0]]
    refuse("a room with no area", "no area", plan=doc)

    doc = plan()
    doc["units"] = "inches"
    refuse("a plan in the wrong units", "millimetres", plan=doc)

    refuse("a plan with nothing in it", "nothing to build",
           plan={"units": "mm", "rooms": [], "walls": [], "labels": []})

    refuse("a plan that is not an object", "floorplan.json object", plan=[1, 2, 3])

    doc = plan()
    doc["walls"][0].pop("to_mm")
    refuse("a wall with only one end", "from_mm", plan=doc)

    doc = plan()
    doc["walls"][0]["from_mm"] = [0, "north"]
    refuse("a coordinate that is not a number", "must be a number", plan=doc)

    doc = plan()
    del doc["walls"][0]["id"]
    refuse("an entry with no id", "stable id", plan=doc)

    doc = plan()
    doc["walls"][0]["id"] = "w" * 70
    refuse("an id too long for a Blender name", "truncates", plan=doc)

    reply = build(plan=plan(), mode="reubild")
    check("an unknown mode is refused", not ok(reply))
    check("  ... and difflib offers the near miss",
          "did you mean 'rebuild'" in message(reply).lower(), message(reply)[:240])

    doc = plan()
    doc["walls"][4]["openings"][0]["swing"] = "sideways"
    refuse("a swing nobody has heard of", "swings", plan=doc)

    doc = plan()
    doc["walls"][4]["openings"][0]["at_mm"] = DOOR_AT
    doc["walls"][4]["openings"][0]["start_mm"] = DOOR_START
    refuse("both at_mm and start_mm", "give one", plan=doc)

    doc = plan()
    doc["labels"][0]["footprint_mm"] = [0, 0, 0, 600]
    refuse("a footprint with no area", "no floor area", plan=doc)

    check("after every refusal the scene is still empty",
          not [o for o in bpy.data.objects if o.name.startswith(PREFIX)])


def test_a_non_mesh_in_the_way():
    section("an FP: name already taken by something that is not a mesh")
    wipe()
    empty = bpy.data.objects.new("FP:wall-s", None)
    bpy.context.scene.collection.objects.link(empty)
    before = empty.as_pointer()

    reply = build(plan=plan())
    if not check("the rest of the level still builds", ok(reply),
                 message(reply)[:300]):
        return
    data = result(reply)
    kept = {item["id"]: item for item in data["kept"]}
    check("the empty is KEPT, never replaced by a wall",
          "wall-s" in kept, str(data["kept"]))
    check("and the reason says what it is now",
          "EMPTY" in kept.get("wall-s", {}).get("why", ""), str(kept.get("wall-s")))
    check("the empty is still an empty",
          bpy.data.objects["FP:wall-s"].type == "EMPTY"
          and bpy.data.objects["FP:wall-s"].as_pointer() == before)
    check("wall-s is in neither built nor updated",
          "wall-s" not in data["built"] and "wall-s" not in data["updated"],
          str(data)[:240])
    check("and the other seven objects built anyway",
          sorted(data["built"]) == sorted(i for i in ALL_IDS if i != "wall-s"),
          str(data["built"]))
    check("a warning names it",
          any("FP:wall-s" in w for w in data["warnings"]), str(data["warnings"]))
    wipe()


# ---------------------------------------------------------------------------
# 8. a flow can run it
# ---------------------------------------------------------------------------

def test_it_is_a_legal_flow_step():
    section("a flow can run it — proven by running one")
    wipe()
    reply = flow_run(flow={
        "name": "floorplan-test",
        "description": "ping, then build a floor plan",
        "params": {"where": {"value": "Level B", "description": "the collection"}},
        "steps": [
            {"kind": "blender", "op": "ping", "args": {}},
            {"kind": "blender", "op": "build_floorplan",
             "args": {"plan": plan(), "collection": "{{where}}"}},
        ],
    })
    if not check("the flow ran", ok(reply), message(reply)[:400]):
        return
    steps = result(reply).get("steps") or []
    check("both steps ran", len(steps) == 2, str(len(steps)))
    last = steps[-1] if steps else {}
    check("the second step really is the build",
          last.get("op") == "build_floorplan", str(last)[:200])
    check("and it succeeded inside the flow", last.get("ok") is True, str(last)[:300])
    check("the collection parameter reached it",
          bpy.data.collections.get("Level B") is not None)
    check("and the level is in it",
          len([o for o in bpy.data.collections["Level B"].all_objects
               if o.name.startswith(PREFIX)]) == 8)
    wipe()


# ---------------------------------------------------------------------------
# 9. budget
# ---------------------------------------------------------------------------

def test_budget():
    section("the budget — a 40-wall plan, built then re-diffed")
    wipe()
    walls = []
    for index in range(40):
        x = index * 500.0
        walls.append({"id": "bw-%02d" % index, "from_mm": [x, 0], "to_mm": [x, 4000],
                      "openings": [{"id": "bd-%02d" % index, "kind": "door",
                                    "at_mm": 2000, "swing": "in"}]})
    doc = {"units": "mm", "walls": walls}

    started = time.monotonic()
    data = result(build(plan=doc))
    first = time.monotonic() - started
    check("forty walls with forty doors built", data["objects"] == 40,
          str(data["objects"]))
    check("and forty mechanism records came back", len(data["mechanisms"]) == 40,
          str(len(data["mechanisms"])))
    check("the first build is under 20 s", first < 20.0, "%.2f s" % first)

    started = time.monotonic()
    again = result(build(plan=doc))
    second = time.monotonic() - started
    note("40 walls: %.2f s to build, %.2f s to re-diff round trip (the command "
         "itself reported %.3f s and %.3f s)"
         % (first, second, data["seconds"], again["seconds"]))
    check("the command times itself", data["seconds"] > 0.0, str(data["seconds"]))
    check("re-diffing the same plan changes nothing",
          len(again["unchanged"]) == 40, str(len(again["unchanged"])))
    check("and is faster than building it", second < first + 0.5,
          "%.2f s vs %.2f s" % (second, first))
    wipe()



# ---------------------------------------------------------------------------
# 10. reconcile — the return channel, measured
# ---------------------------------------------------------------------------
#
# The ask: *"I should be able to manually edit and forge should be aware of my
# changes."*  And the incident: a wall the owner deleted on purpose came back
# twice, because `build_floorplan` never looks at an object's TRANSFORM and the
# plan never hears about a deletion at all.
#
# Every expectation below is arithmetic on the fixture flat, and the one that
# matters most is the last: the command must not touch the scene, proven on
# `as_pointer()` identity and on the transforms themselves rather than on the
# report saying so.

def reconcile(**params):
    return _roundtrip({"type": "reconcile_floorplan", "params": params})


def transforms():
    """Every FP: object's world matrix, flattened — the read-only proof.

    The view layer is evaluated first, exactly as the command does: a matrix
    read before the depsgraph has caught up is stale rather than wrong, and
    comparing a stale reading with a fresh one would fail a command that never
    touched anything.
    """
    bpy.context.view_layer.update()
    out = {}
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return out
    for obj in collection.all_objects:
        if obj.name.startswith(PREFIX):
            out[obj.name] = [round(value, 9)
                             for row in obj.matrix_world for value in row]
    return out


def box_mesh(name, half_x, half_y, z0, z1):
    """One axis-aligned 8-vertex box mesh, in METRES, for a hand 'resize'."""
    verts = [(-half_x, -half_y, z0), (half_x, -half_y, z0),
             (half_x, half_y, z0), (-half_x, half_y, z0),
             (-half_x, -half_y, z1), (half_x, -half_y, z1),
             (half_x, half_y, z1), (-half_x, half_y, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def hex_mesh(name):
    """A twelve-vertex prism: what a sculpted placeholder looks like from here."""
    verts = []
    for z in (0.0, 0.85):
        for index in range(6):
            angle = math.radians(60 * index)
            verts.append((0.3 * math.cos(angle), 0.3 * math.sin(angle), z))
    faces = [(0, 1, 2, 3, 4, 5), (11, 10, 9, 8, 7, 6)]
    for index in range(6):
        following = (index + 1) % 6
        faces.append((index, following, following + 6, index + 6))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def test_reconcile_registration():
    section("reconcile — registration and the read-only classification")
    from forge.tools import registry

    check("reconcile_floorplan is a registered command",
          registry.has_command("reconcile_floorplan"))
    check("it IS read-only — it measures the scene and changes nothing",
          "reconcile_floorplan" in registry.READ_ONLY_COMMANDS)
    check("its sibling build_floorplan is still not read-only",
          "build_floorplan" not in registry.READ_ONLY_COMMANDS)


def test_reconcile_on_an_untouched_level():
    section("reconcile — a level nobody has touched is all clean")
    wipe()
    build(plan=plan())
    check("every object carries where Forge PUT it",
          all(obj_of(i).get("forge_fp_loc_mm") is not None
              and obj_of(i).get("forge_fp_rot_deg") is not None for i in ALL_IDS))
    check("and the stamp is in millimetres, not metres",
          near(obj_of("wall-mid").get("forge_fp_loc_mm")[0], SPLIT_X),
          str(list(obj_of("wall-mid").get("forge_fp_loc_mm"))))
    check("a wall's stamped rotation is its own angle",
          near(obj_of("wall-mid").get("forge_fp_rot_deg"), 90.0),
          str(obj_of("wall-mid").get("forge_fp_rot_deg")))

    before_pointers = pointers()
    before_transforms = transforms()
    reply = reconcile(plan=plan())
    if not check("the untouched level reconciles", ok(reply), message(reply)[:400]):
        return
    data = result(reply)
    check("all eight entries are clean",
          sorted(data["clean"]) == sorted(ALL_IDS), str(data["clean"]))
    check("nothing moved, resized, vanished or turned up",
          not data["moved"] and not data["resized"]
          and not data["deleted_in_scene"] and not data["candidates"]
          and not data["unabsorbable"] and not data["stale"], str(data)[:300])
    check("the report says there is nothing to absorb",
          any("Nothing to absorb" in note for note in data["notes"]),
          str(data["notes"]))
    check("it says out loud that it measured rather than decided",
          "measurements of the scene" in data["honesty"])
    check("and names the tolerance every number was compared at",
          data["tolerance_mm"]["move"] == 0.5, str(data["tolerance_mm"]))

    # THE read-only claim, proven on the scene rather than on the report.
    check("READ-ONLY: not one allocation changed", pointers() == before_pointers)
    check("READ-ONLY: not one transform changed", transforms() == before_transforms)
    check("READ-ONLY: no object was added or removed",
          len(bpy.data.objects) == len(before_pointers), str(len(bpy.data.objects)))


def test_reconcile_measures_a_move():
    section("reconcile — a wall dragged 500 mm north, measured to the millimetre")
    wall = obj_of("wall-s")
    wall.location = (0.0, 0.5, 0.0)          # the artist drags it in the viewport

    data = result(reconcile(plan=plan()))
    moved = {record["id"]: record for record in data["moved"]}
    if not check("the wall that moved is the one reported",
                 list(moved) == ["wall-s"], str(data["moved"])[:200]):
        return
    record = moved["wall-s"]
    check("its measured centreline is the new one, to the mm",
          near_all(record["measured"]["from_mm"], [0.0, 500.0])
          and near_all(record["measured"]["to_mm"], [FLAT_W, 500.0]),
          str(record["measured"]))
    check("and it says where it was",
          near_all(record["was"]["from_mm"], [0.0, 0.0]), str(record["was"]))
    check("the fields that changed are named",
          sorted(record["changed"]) == ["from_mm", "to_mm"], str(record["changed"]))
    check("how far it went is a number", near(record["moved_mm"], 500.0),
          str(record["moved_mm"]))
    check("its thickness and height are unchanged, so they are not in `changed`",
          near(record["measured"]["thickness_mm"], WALL_T)
          and near(record["measured"]["height_mm"], CEILING),
          str(record["measured"]))
    check("the mesh is reported intact — this was a transform, not an edit",
          record["mesh"] == "intact" and record["confidence"] == "measured",
          str(record["mesh"]))
    check("the other seven are still clean",
          sorted(data["clean"]) == sorted(i for i in ALL_IDS if i != "wall-s"),
          str(data["clean"]))
    wall.location = (0.0, 0.0, 0.0)


def test_reconcile_measures_a_rotation_and_a_scale():
    section("reconcile — a fixture spun 90 degrees, a wall scaled thicker")
    fixture = obj_of("wd-01")
    fixture.rotation_euler = (0.0, 0.0, math.radians(90))
    wall = obj_of("wall-e")
    wall.scale = (1.0, 2.0, 1.0)             # twice as thick, by hand

    data = result(reconcile(plan=plan()))
    moved = {record["id"]: record for record in data["moved"]}
    resized = {record["id"]: record for record in data["resized"]}

    check("a spin is a MOVE — the plan has a field for it",
          "wd-01" in moved, str(data["moved"])[:200])
    if "wd-01" in moved:
        record = moved["wd-01"]
        check("the measured angle is exactly 90, not 89.9999",
              record["measured"]["rotation_deg"] == 90.0,
              str(record["measured"]["rotation_deg"]))
        check("its footprint centre did not move",
              near_all(record["measured"]["centre_mm"], [1000.0, 500.0]),
              str(record["measured"]["centre_mm"]))
        check("and its size did not change",
              near_all(record["measured"]["size_mm"], list(WD_SIZE)),
              str(record["measured"]["size_mm"]))

    check("a scale is a RESIZE", "wall-e" in resized, str(data["resized"])[:200])
    if "wall-e" in resized:
        record = resized["wall-e"]
        check("the measured thickness is the scaled one",
              near(record["measured"]["thickness_mm"], WALL_T * 2),
              str(record["measured"]))
        check("the centreline did not move with it",
              near_all(record["measured"]["from_mm"], [FLAT_W, 0.0]),
              str(record["measured"]["from_mm"]))
        check("thickness_mm is the field named as changed",
              record["changed"] == ["thickness_mm"], str(record["changed"]))
    fixture.rotation_euler = (0.0, 0.0, 0.0)
    wall.scale = (1.0, 1.0, 1.0)


def test_reconcile_measures_a_mesh_edited_box():
    section("reconcile — a box resized in EDIT mode is still a box")
    fixture = obj_of("wd-01")
    old_mesh = fixture.data
    fixture.data = box_mesh("wd-hand", 0.5, 0.3, 0.0, 0.85)   # 1000 x 600 x 850

    data = result(reconcile(plan=plan()))
    resized = {record["id"]: record for record in data["resized"]}
    if check("the hand-resized fixture is measured, not refused",
             "wd-01" in resized, str(data)[:300]):
        record = resized["wd-01"]
        check("its measured footprint is the mesh's own",
              near_all(record["measured"]["size_mm"], [1000.0, 600.0]),
              str(record["measured"]["size_mm"]))
        check("and the report says the mesh itself was edited",
              record["mesh"] == "edited-but-still-a-box", str(record["mesh"]))
    fixture.data = old_mesh


def test_reconcile_refuses_to_describe_a_sculpt():
    section("reconcile — a sculpted placeholder is a promotion, not a measurement")
    fixture = obj_of("wd-01")
    old_mesh = fixture.data
    fixture.data = hex_mesh("wd-sculpted")

    data = result(reconcile(plan=plan()))
    blocked = {record["id"]: record for record in data["unabsorbable"]}
    if check("it comes back as unabsorbable", "wd-01" in blocked,
             str(data)[:300]):
        why = blocked["wd-01"]["why"]
        check("the reason says it was sculpted", "SCULPTED" in why, why[:200])
        check("and recommends the promotion marker", "forge_fp_keep" in why,
              why[:200])
    check("it is not quietly measured as something else",
          not any(r["id"] == "wd-01" for r in data["moved"] + data["resized"]))
    fixture.data = old_mesh


def test_reconcile_refuses_a_tilt_and_a_lift():
    section("reconcile — a plan is top-down, and says so twice")
    wall = obj_of("wall-mid")
    wall.rotation_euler = (math.radians(12), 0.0, math.radians(90))
    slab = obj_of("room-right")
    slab.location = (slab.location.x, slab.location.y, 1.0)

    data = result(reconcile(plan=plan()))
    blocked = {record["id"]: record["why"] for record in data["unabsorbable"]}
    check("a tilted wall is unabsorbable", "wall-mid" in blocked, str(blocked)[:300])
    check("and the reason names the tilt in degrees",
          "tilted" in blocked.get("wall-mid", "")
          and "12" in blocked.get("wall-mid", ""),
          blocked.get("wall-mid", "")[:200])
    check("a slab lifted off the floor is unabsorbable too",
          "room-right" in blocked, str(blocked)[:300])
    check("and the reason says the plan has no field for a height",
          "off the floor" in blocked.get("room-right", ""),
          blocked.get("room-right", "")[:200])
    wall.rotation_euler = (0.0, 0.0, math.radians(90))
    slab.location = (slab.location.x, slab.location.y, 0.0)


def test_reconcile_reads_a_deletion_and_a_stranger():
    section("reconcile — a wall they deleted, and a box they added")
    victim = obj_of("wall-w")
    bpy.data.objects.remove(victim, do_unlink=True)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(2.0, 2.0, 0.425))
    stranger = bpy.context.view_layer.objects.active
    stranger.name = "FP:sofa-99"
    stranger.scale = (1.8, 0.9, 0.85)
    collection = bpy.data.collections.get(COLLECTION)
    for other in list(stranger.users_collection):
        other.objects.unlink(stranger)
    collection.objects.link(stranger)

    data = result(reconcile(plan=plan()))
    gone = {record["id"]: record for record in data["deleted_in_scene"]}
    check("the deleted wall is reported as deleted in the scene",
          list(gone) == ["wall-w"], str(data["deleted_in_scene"])[:200])
    check("with the kind and what it was, so the plan knows what it is dropping",
          gone.get("wall-w", {}).get("kind") == "wall"
          and near_all(gone["wall-w"]["was"]["from_mm"], [0.0, FLAT_D]),
          str(gone.get("wall-w"))[:240])
    check("the report says a deletion is a layout decision",
          any("layout decision" in note for note in data["notes"]),
          str(data["notes"]))

    candidates = {record["id"]: record for record in data["candidates"]}
    if check("the stranger is a candidate, not an error",
             "sofa-99" in candidates, str(data["candidates"])[:240]):
        record = candidates["sofa-99"]
        check("its bounding box is measured in millimetres",
              near_all(record["bbox_mm"]["size"], [1800.0, 900.0, 850.0], 1.0),
              str(record["bbox_mm"]))
        check("and it is offered as a footprint, with the rotation unknown",
              near_all(record["suggested"]["footprint_mm"][2:],
                       [1800.0, 900.0], 1.0)
              and record["suggested"]["rotation_deg"] == 0.0,
              str(record["suggested"]))
        check("the reason says Forge did not build it",
              "did not build it" in record["why"], record["why"][:160])

    bpy.data.objects.remove(stranger, do_unlink=True)
    build(plan=plan())      # put wall-w back for the tests after this one


def test_reconcile_does_not_read_a_tidied_object_as_a_deletion():
    section("reconcile — an object moved to another collection is not a deletion")
    obj = obj_of("wall-n")
    collection = bpy.data.collections.get(COLLECTION)
    collection.objects.unlink(obj)
    bpy.context.scene.collection.objects.link(obj)

    data = result(reconcile(plan=plan()))
    blocked = {record["id"]: record["why"] for record in data["unabsorbable"]}
    check("it is NOT reported as deleted",
          not any(record["id"] == "wall-n"
                  for record in data["deleted_in_scene"]),
          str(data["deleted_in_scene"]))
    check("it is reported as outside the collection instead",
          "outside the" in blocked.get("wall-n", ""),
          str(blocked.get("wall-n"))[:200])
    bpy.context.scene.collection.objects.unlink(obj)
    collection.objects.link(obj)


def test_reconcile_will_not_read_a_pending_plan_edit_as_a_hand_edit():
    section("reconcile — a plan that moved on since the build is STALE, not moved")
    doc = plan()
    for wall in doc["walls"]:
        if wall["id"] == "wall-s":
            wall["to_mm"] = [FLAT_W + 2000.0, 0]   # edited in the PLAN, not built

    data = result(reconcile(plan=doc))
    check("the entry waiting for a rebuild is stale",
          data["stale"] == ["wall-s"], str(data["stale"]))
    check("and it is NOT measured as something the artist moved",
          not any(record["id"] == "wall-s"
                  for record in data["moved"] + data["resized"]),
          str(data["moved"] + data["resized"])[:200])
    check("the note says a rebuild is what it is waiting for",
          any("waiting for a rebuild" in note for note in data["notes"]),
          str(data["notes"]))

    # ... and a plan edit PLUS a hand edit is a conflict Forge refuses to guess at.
    obj_of("wall-s").location = (0.0, 0.9, 0.0)
    data = result(reconcile(plan=doc))
    blocked = {record["id"]: record["why"] for record in data["unabsorbable"]}
    check("a plan edit and a hand edit at once is unabsorbable",
          "wall-s" in blocked, str(data)[:300])
    check("and the sentence says to build first, then reconcile",
          "build first" in blocked.get("wall-s", ""),
          blocked.get("wall-s", "")[:200])
    obj_of("wall-s").location = (0.0, 0.0, 0.0)


def test_reconcile_refusals():
    section("reconcile — refusals, sentences again")
    reply = reconcile()
    check("no plan at all is refused", not ok(reply))
    check("  ... and the sentence says to pass one",
          "needs" in message(reply) and "plan" in message(reply),
          message(reply)[:200])

    reply = reconcile(plan=plan(), collection="Nowhere")
    check("a collection that does not exist is refused", not ok(reply))
    check("  ... and the sentence says Forge will not create one",
          "never creates anything" in message(reply), message(reply)[:240])
    check("  ... and it really did not create one",
          bpy.data.collections.get("Nowhere") is None)

    empty = bpy.data.collections.new("Empty Level")
    bpy.context.scene.collection.children.link(empty)
    reply = reconcile(plan=plan(), collection="Empty Level")
    check("a collection with no FP: objects is refused, not answered",
          not ok(reply))
    check("  ... because the answer would be 'everything was deleted'",
          "every entry in your plan has been deleted" in message(reply),
          message(reply)[:300])
    bpy.data.collections.remove(empty)


def test_reconcile_is_still_read_only_after_all_that():
    section("reconcile — the read-only guarantee, one more time")
    obj_of("wd-01").location = (2.0, 1.0, 0.0)   # the edit happens FIRST
    before_pointers = pointers()
    before_transforms = transforms()
    before_objects = len(bpy.data.objects)
    before_collections = len(bpy.data.collections)
    before_meshes = len(bpy.data.meshes)

    data = result(reconcile(plan=plan()))
    check("it measured something", bool(data["moved"]), str(data["moved"])[:120])
    check("allocations are untouched, objects and meshes both",
          pointers() == before_pointers)
    check("transforms are exactly as the artist left them",
          transforms() == before_transforms,
          str([n for n in before_transforms
               if transforms().get(n) != before_transforms[n]]))
    check("no object, collection or mesh was created or removed",
          (len(bpy.data.objects), len(bpy.data.collections),
           len(bpy.data.meshes))
          == (before_objects, before_collections, before_meshes),
          "%d/%d/%d" % (len(bpy.data.objects), len(bpy.data.collections),
                        len(bpy.data.meshes)))
    obj_of("wd-01").location = (1.0, 0.5, 0.0)
    wipe()

def test_port_is_free_after():
    section("shutdown")
    from forge import server as forge_server

    forge_server.stop_server()
    time.sleep(0.2)
    try:
        conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=0.5)
        conn.close()
        check("the socket closed with the server", False, "still accepting")
    except OSError:
        check("the socket closed with the server", True)


def main():
    print("Forge headless tests — Phase 19 build_floorplan")
    enable_addon()

    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)

    try:
        test_registration()
        test_first_build()
        test_moving_one_wall_touches_one_wall()
        doc = test_adding_a_fixture_only_creates()
        doc = test_removing_a_fixture_only_deletes(doc)
        doc = test_a_changed_default_rebuilds_only_what_it_reaches(doc)
        doc = test_a_hand_scaled_fixture_is_kept(doc)
        doc = test_forge_fp_keep_is_an_unconditional_hands_off(doc)
        doc = test_an_object_forge_did_not_build_is_kept(doc)
        test_rebuild_replaces_everything(doc)
        test_floor_false_and_a_named_collection()
        test_windows_gaps_and_an_L_shaped_room()
        test_refusals()
        test_a_non_mesh_in_the_way()
        test_it_is_a_legal_flow_step()
        test_budget()
        test_reconcile_registration()
        test_reconcile_on_an_untouched_level()
        test_reconcile_measures_a_move()
        test_reconcile_measures_a_rotation_and_a_scale()
        test_reconcile_measures_a_mesh_edited_box()
        test_reconcile_refuses_to_describe_a_sculpt()
        test_reconcile_refuses_a_tilt_and_a_lift()
        test_reconcile_reads_a_deletion_and_a_stranger()
        test_reconcile_does_not_read_a_tidied_object_as_a_deletion()
        test_reconcile_will_not_read_a_pending_plan_edit_as_a_hand_edit()
        test_reconcile_refusals()
        test_reconcile_is_still_read_only_after_all_that()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    failed = [label for label, ok_, _ in _RESULTS if not ok_]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
