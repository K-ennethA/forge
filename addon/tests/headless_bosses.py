"""Headless add-on tests for reversible boss attachment (``forge.tools.bosses``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_bosses.py

The socket port is **9916** — 9876 belongs to the artist's live session and
9878 through 9915 to the other suites (9915 is ``headless_spriteforge``), so
this is the next free one.  Every command is driven through the real socket, so
what is proved is the wire path and not just the functions.

The fixture is synthetic and deterministic: a 48×24 UV sphere of radius 25 mm
whose vertices are pushed along their own normals by ``random.Random(20260921)``
in ±0.6 mm — a stand-in for a sculpt, with enough facet noise that a boolean
seam has something real to cut across.  The boss is the ``peg`` spec (6 × 11 mm,
anti-rotation rib, 3 mm fillet skirt), seated 5 mm into the sphere.

What is actually being proved:

1. **attach_boss** — the union lands (there is surface at the peg tip where
   there was none), both halves are retained in a hidden ``forge_bosses``
   collection, and the ledger on the target reads back with the right id,
   transform, volumes and retained names;
2. **detach_boss** — the part comes back to its pre-attach *surface*: volume to
   within a measured fraction of a percent and a measured Hausdorff distance
   quoted in the output, the record is gone, and the retained objects are gone
   with it.  The zero-epsilon control is run too, which is where the epsilon's
   whole justification lives;
3. **move_boss** — the stump moves: there is surface at the new tip, none at
   the old one, and **exactly one** boss (one record, one cutter, one added
   solid) is left behind;
4. **save/reload** — the ledger and both retained objects survive a .blend round
   trip, and a detach after the reload still works;
5. **refusals** — unknown boss id, a target with no records at all, both/neither
   of ``boss``/``spec``, attaching an object to itself, and a boss whose
   retained solid was deleted behind the tool's back;
6. **determinism** — the whole attach/detach cycle run twice from scratch
   produces byte-identical geometry digests.
"""

import hashlib
import json
import math
import os
import random
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9916  # not 9876 (a live session) and not 9878..9915 (every other suite)

MM = 0.001
M3_TO_MM3 = 1.0e9

SCULPT = "sculpt"
SEED = 20260921
RADIUS_MM = 25.0
NOISE_MM = 0.6

PEG_SPEC = {
    "kind": "peg",
    "diameter_mm": 6.0,
    "length_mm": 11.0,
    "rib": {"width_mm": 1.8, "height_mm": 0.6},
    "skirt": {"flare_mm": 3.0, "height_mm": 3.0},
}
#: Base 5 mm inside the surface, pointing out along +X (local +Z is the growth
#: direction, so a 90 degree turn about Y aims it at +X).
SEAT_MM = [RADIUS_MM - 5.0, 0.0, 0.0]
SEAT_ROT = [0.0, 90.0, 0.0]
#: A 60 degree correction — the eevee-bowl-v2 ears were out by more than that.
#: Wide enough that the old tip ends up further from any surface than the peg's
#: own radius, which is what makes "nothing is left there" unambiguous rather
#: than a threshold argument.
MOVED_ROT = [0.0, 30.0, 0.0]
PEG_RADIUS_MM = PEG_SPEC["diameter_mm"] * 0.5

_RESULTS = []
_TEMP = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def note(text):
    print("     %s" % text)


def section(title):
    print("\n== %s ==" % title)


# --- setup ------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def _roundtrip(payload, timeout=120.0):
    """Send one socket command and pump the main-thread queue until it replies."""
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


def result_of(reply):
    return reply.get("result") or {}


# --- fixture ----------------------------------------------------------------

def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def build_sculpt(name=SCULPT):
    """A UV sphere with deterministic per-vertex noise along its own normals."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=RADIUS_MM * MM)
    bm.verts.ensure_lookup_table()
    bm.normal_update()
    rng = random.Random(SEED)
    for vert in bm.verts:
        vert.co += vert.normal * (rng.uniform(-NOISE_MM, NOISE_MM) * MM)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    return obj


def fresh_fixture():
    clear_scene()
    return build_sculpt()


# --- measurement ------------------------------------------------------------

def world_bmesh(obj, triangulate=False):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    if triangulate and bm.faces:
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
    bm.normal_update()
    return bm


def volume_mm3(obj):
    bm = world_bmesh(obj, triangulate=True)
    try:
        return abs(bm.calc_volume(signed=True)) * M3_TO_MM3
    finally:
        bm.free()


def area_mm2(obj):
    bm = world_bmesh(obj)
    try:
        return sum(face.calc_area() for face in bm.faces) * 1.0e6
    finally:
        bm.free()


def bounds_mm(obj):
    bm = world_bmesh(obj)
    try:
        xs = [vert.co for vert in bm.verts]
        low = [min(co[axis] for co in xs) * 1000.0 for axis in range(3)]
        high = [max(co[axis] for co in xs) * 1000.0 for axis in range(3)]
        return low, high
    finally:
        bm.free()


def digest(obj):
    """A geometric fingerprint: volume, area, bounds and vertex count, hashed.

    Geometric rather than topological on purpose — a detach re-triangulates the
    seam, so no hash of the mesh buffers could ever match again, and a digest
    that cannot match is a digest nobody can gate on.  This one is for
    determinism (same inputs, same numbers), not for equality across a cut.
    """
    low, high = bounds_mm(obj)
    parts = ["%.6f" % volume_mm3(obj), "%.6f" % area_mm2(obj),
             "%d" % len(obj.data.vertices), "%d" % len(obj.data.polygons)]
    parts += ["%.6f" % value for value in low + high]
    blob = "|".join(parts)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16], blob


def snapshot(obj):
    """Vertices and a BVH of the object's world-space surface."""
    bm = world_bmesh(obj, triangulate=True)
    try:
        bm.verts.ensure_lookup_table()
        verts = [vert.co.copy() for vert in bm.verts]
        tris = [tuple(vert.index for vert in face.verts) for face in bm.faces]
    finally:
        bm.free()
    tree = BVHTree.FromPolygons([tuple(co) for co in verts], tris, all_triangles=True)
    return verts, tree


def hausdorff_mm(first, second):
    """Two-sided nearest-surface distance between two snapshots, in mm."""
    worst = 0.0
    for verts, tree in ((first[0], second[1]), (second[0], first[1])):
        for co in verts:
            hit = tree.find_nearest(co)
            if hit[0] is not None:
                worst = max(worst, (hit[0] - co).length)
    return worst * 1000.0


def surface_distance_mm(tree, point_mm):
    """How far a millimetre point is from the nearest surface of a snapshot."""
    hit = tree.find_nearest(Vector([value * MM for value in point_mm]))
    if hit[0] is None:
        return float("inf")
    return (hit[0] - Vector([value * MM for value in point_mm])).length * 1000.0


def peg_tip_mm(rotation_deg, length_mm=None):
    """Where the peg's tip lands for a seating rotation about Y, in mm."""
    length = PEG_SPEC["length_mm"] if length_mm is None else length_mm
    angle = math.radians(rotation_deg)
    # Local +Z rotated about Y by `angle` is (sin, 0, cos).
    return [SEAT_MM[0] + math.sin(angle) * length,
            0.0,
            SEAT_MM[2] + math.cos(angle) * length]


# --- tests ------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import registry

    for name in ("attach_boss", "detach_boss", "move_boss", "list_bosses"):
        check("%s is a protocol command" % name, registry.has_command(name))
    check("and every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "boolean", "load_mesh", "remesh", "export_stl",
               "partforge_open", "rigforge_tag", "build_floorplan")),
          "%d commands registered" % len(registry.command_names()))

    from forge.tools import bosses

    check("the seam epsilon fraction is the derived 0.02",
          abs(bosses.SEAM_EPSILON_FRACTION - 0.02) < 1e-12,
          bosses.SEAM_EPSILON_FRACTION)
    check("the retained collection is forge_bosses",
          bosses.BOSS_COLLECTION == "forge_bosses", bosses.BOSS_COLLECTION)


def test_attach():
    section("attach_boss — the union lands and both halves are kept")
    target = fresh_fixture()
    before_volume = volume_mm3(target)
    before = snapshot(target)
    tip = peg_tip_mm(SEAT_ROT[1])
    check("nothing is at the peg tip before the attach",
          surface_distance_mm(before[1], tip) > 3.0,
          "%.3f mm" % surface_distance_mm(before[1], tip))

    reply = call("attach_boss", object=SCULPT, spec=PEG_SPEC,
                 location_mm=SEAT_MM, rotation_deg=SEAT_ROT, label="ear peg")
    if not check("attach_boss succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return None
    report = result_of(reply)

    check("the report names the boss id", report.get("boss_id") == "boss-01",
          report.get("boss_id"))
    check("the report carries the label", report.get("label") == "ear peg")
    check("the report names the spec kind", report.get("kind") == "spec:peg",
          report.get("kind"))
    check("the report quotes both volumes",
          report.get("volume_before_mm3") and report.get("volume_after_mm3")
          and report["volume_after_mm3"] > report["volume_before_mm3"],
          (report.get("volume_before_mm3"), report.get("volume_after_mm3")))
    check("the report quotes a seam epsilon with its derivation",
          report.get("seam_epsilon_mm", 0) > 0 and "median edge" in
          (report.get("seam_epsilon_basis") or ""),
          (report.get("seam_epsilon_mm"), report.get("seam_epsilon_basis")))
    note("seam epsilon %.5f mm (%s)" % (report["seam_epsilon_mm"],
                                        report["seam_epsilon_basis"]))

    grew = report["volume_after_mm3"] - report["volume_before_mm3"]
    kept = report["retained_added_volume_mm3"]
    check("the union grew the part by the retained solid's volume",
          abs(grew - kept) <= 0.03 * kept,
          "grew %.3f mm3, retained solid %.3f mm3 (%.3f%%)"
          % (grew, kept, 100.0 * abs(grew - kept) / kept))
    note("union added %.3f mm3; the retained added solid is %.3f mm3 (%.2f%% of "
         "the boss is buried)" % (grew, kept, report["buried_pct"]))

    # the geometry itself
    attached = snapshot(target)
    check("there is surface at the peg tip after the attach",
          surface_distance_mm(attached[1], tip) < 0.5,
          "%.4f mm" % surface_distance_mm(attached[1], tip))
    check("the measured volume matches the reported one",
          abs(volume_mm3(target) - report["volume_after_mm3"]) < 0.01,
          (volume_mm3(target), report["volume_after_mm3"]))

    # the drawer
    collection = bpy.data.collections.get("forge_bosses")
    if check("the forge_bosses collection exists", collection is not None):
        check("it is hidden from the viewport", collection.hide_viewport)
        check("it is hidden from renders", collection.hide_render)
        check("it holds exactly the two retained objects",
              len(collection.objects) == 2,
              [obj.name for obj in collection.objects])
    cutter = bpy.data.objects.get(report["retained"]["cutter"])
    added = bpy.data.objects.get(report["retained"]["added"])
    check("the cutter was retained", cutter is not None, report["retained"]["cutter"])
    check("the added solid was retained", added is not None, report["retained"]["added"])
    if cutter is not None and added is not None:
        check("both retained objects are hidden",
              cutter.hide_viewport and added.hide_viewport
              and cutter.hide_render and added.hide_render)
        check("both are stamped with the boss id",
              cutter.get("forge_boss_id") == "boss-01"
              and added.get("forge_boss_id") == "boss-01")
        check("they are stamped with their roles",
              cutter.get("forge_boss_role") == "cutter"
              and added.get("forge_boss_role") == "added")
        check("the cutter still has the whole peg, skirt and all",
              volume_mm3(cutter) > kept,
              "cutter %.3f mm3 vs added %.3f mm3" % (volume_mm3(cutter), kept))

    # the ledger
    raw = target.get("forge_bosses")
    check("the ledger is a JSON string on the target", isinstance(raw, str))
    ledger = json.loads(raw) if isinstance(raw, str) else []
    check("it holds exactly one record", len(ledger) == 1, len(ledger))
    if ledger:
        record = ledger[0]
        check("the record's id matches", record.get("id") == "boss-01")
        check("the record names both retained objects",
              record.get("cutter") == report["retained"]["cutter"]
              and record.get("added") == report["retained"]["added"])
        check("the record's matrix is in millimetres",
              record.get("matrix_mm")
              and abs(record["matrix_mm"][3] - SEAT_MM[0]) < 1e-6,
              record.get("matrix_mm", [])[:4])
        check("the record carries the pre-attach volume",
              abs(record.get("volume_before_mm3", 0) - before_volume) < 0.01,
              (record.get("volume_before_mm3"), before_volume))

    listed = result_of(call("list_bosses", object=SCULPT))
    check("list_bosses sees one reversible boss",
          len(listed.get("bosses", [])) == 1
          and listed["bosses"][0].get("reversible") is True,
          listed.get("bosses"))
    return before


def test_detach(before):
    section("detach_boss — the surface comes back")
    target = bpy.data.objects[SCULPT]
    attached_volume = volume_mm3(target)

    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    if not check("detach_boss succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    report = result_of(reply)

    check("the report quotes the seam epsilon it used",
          report.get("seam_epsilon_mm", 0) > 0, report.get("seam_epsilon_mm"))
    check("the report says the record was removed", report.get("record_removed") is True)
    check("the cut removed what the attach added",
          abs(report["volume_removed_mm3"] - report["expected_removed_mm3"])
          <= 0.05 * report["expected_removed_mm3"],
          (report["volume_removed_mm3"], report["expected_removed_mm3"]))

    detached = snapshot(target)
    volume_now = volume_mm3(target)
    drift = volume_now - report["attach_volume_before_mm3"]
    pct = 100.0 * abs(drift) / report["attach_volume_before_mm3"]
    check("the volume returns to the pre-attach volume within 0.05 percent",
          pct <= 0.05,
          "%.4f mm3 of %.1f mm3 (%.5f%%)"
          % (drift, report["attach_volume_before_mm3"], pct))
    note("volume: %.3f mm3 before attach -> %.3f attached -> %.3f detached "
         "(drift %.4f mm3, %.5f%%)"
         % (report["attach_volume_before_mm3"], attached_volume, volume_now, drift, pct))

    distance = hausdorff_mm(before, detached)
    TOLERANCE_MM = 0.5
    check("the surface returns to within the measured tolerance (%.2f mm)" % TOLERANCE_MM,
          distance <= TOLERANCE_MM, "%.5f mm" % distance)
    note("MEASURED detach tolerance: %.5f mm Hausdorff against the pre-attach "
         "surface, with a seam epsilon of %.5f mm. The gate is %.2f mm."
         % (distance, report["seam_epsilon_mm"], TOLERANCE_MM))
    note("(vertex counts do NOT return: the seam ring is re-cut. That is why the "
         "gate is a surface distance and a volume, not a mesh hash.)")

    check("the record is gone", not target.get("forge_bosses"),
          target.get("forge_bosses"))
    check("both retained objects are gone",
          len(report.get("retained_removed", [])) == 2
          and all(bpy.data.objects.get(name) is None
                  for name in report["retained_removed"]),
          report.get("retained_removed"))
    collection = bpy.data.collections.get("forge_bosses")
    check("the drawer is empty again",
          collection is not None and len(collection.objects) == 0,
          collection and [obj.name for obj in collection.objects])
    return distance


def test_zero_epsilon_control():
    section("the epsilon's justification — the same cut with none")
    fresh_fixture()
    target = bpy.data.objects[SCULPT]
    before = snapshot(target)

    reply = call("attach_boss", object=SCULPT, spec=PEG_SPEC,
                 location_mm=SEAT_MM, rotation_deg=SEAT_ROT)
    if not check("attach for the control succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    reply = call("detach_boss", object=SCULPT, boss_id="boss-01", seam_epsilon_mm=0.0)
    if not check("detach with seam_epsilon_mm=0 succeeded",
                 reply.get("status") == "success", reply.get("message")):
        return
    report = result_of(reply)
    check("the report says the epsilon was given explicitly",
          report.get("seam_epsilon_basis") == "given explicitly",
          report.get("seam_epsilon_basis"))
    distance = hausdorff_mm(before, snapshot(target))
    check("a zero epsilon leaves a seam artifact the derived one does not",
          distance > 1.0, "%.5f mm" % distance)
    note("zero-epsilon detach departs from the pre-attach surface by %.4f mm — "
         "this is the sliver the 0.02 x median-edge inflation exists to avoid."
         % distance)


def test_move():
    section("move_boss — the iteration this tool exists for")
    fresh_fixture()
    target = bpy.data.objects[SCULPT]

    reply = call("attach_boss", object=SCULPT, spec=PEG_SPEC,
                 location_mm=SEAT_MM, rotation_deg=SEAT_ROT, label="ear peg")
    if not check("attach before the move succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    old_tip = peg_tip_mm(SEAT_ROT[1])
    new_tip = peg_tip_mm(MOVED_ROT[1])
    attached = snapshot(target)
    check("the stump is at the old angle to begin with",
          surface_distance_mm(attached[1], old_tip) < 0.5,
          "%.4f mm" % surface_distance_mm(attached[1], old_tip))

    reply = call("move_boss", object=SCULPT, boss_id="boss-01", rotation_deg=MOVED_ROT)
    if not check("move_boss succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    report = result_of(reply)
    check("the report says it moved", report.get("moved") is True)
    check("it quotes the transform before and after",
          report.get("transform_before", {}).get("rotation_deg", [0, 0, 0])[1] == 90.0
          and abs(report.get("transform_after", {}).get("rotation_deg",
                                                        [0, 0, 0])[1] - MOVED_ROT[1]) < 1e-6,
          (report.get("transform_before"), report.get("transform_after")))
    check("it carries the detach it ran on the way",
          report.get("detach", {}).get("volume_removed_mm3", 0) > 0,
          report.get("detach"))
    check("it quotes the seam epsilon", report.get("seam_epsilon_mm", 0) > 0)

    moved = snapshot(target)
    check("there is surface at the NEW tip",
          surface_distance_mm(moved[1], new_tip) < 0.5,
          "%.4f mm" % surface_distance_mm(moved[1], new_tip))
    check("there is nothing left at the OLD tip (further off than the peg's radius)",
          surface_distance_mm(moved[1], old_tip) > PEG_RADIUS_MM,
          "%.4f mm against a %.1f mm radius"
          % (surface_distance_mm(moved[1], old_tip), PEG_RADIUS_MM))
    note("old tip is now %.3f mm from any surface; new tip is %.3f mm from one"
         % (surface_distance_mm(moved[1], old_tip),
            surface_distance_mm(moved[1], new_tip)))

    ledger = json.loads(target.get("forge_bosses") or "[]")
    check("exactly one record survives the move", len(ledger) == 1, ledger)
    check("it is still boss-01 with its label",
          ledger and ledger[0].get("id") == "boss-01"
          and ledger[0].get("label") == "ear peg")
    check("the record's matrix carries the new angle",
          abs(report["transform"]["rotation_deg"][1] - MOVED_ROT[1]) < 1e-6,
          report["transform"])
    collection = bpy.data.collections.get("forge_bosses")
    check("exactly one cutter and one added solid are retained",
          collection is not None and len(collection.objects) == 2,
          collection and [obj.name for obj in collection.objects])
    check("the retained pair is stamped for boss-01",
          collection is not None
          and {obj.get("forge_boss_role") for obj in collection.objects}
          == {"cutter", "added"},
          collection and [(obj.name, obj.get("forge_boss_role"))
                          for obj in collection.objects])

    # and it is still reversible after the move
    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    check("a moved boss can still be detached", reply.get("status") == "success",
          reply.get("message"))
    check("and the drawer is empty afterwards",
          len(bpy.data.collections["forge_bosses"].objects) == 0)


def test_two_bosses_and_ids():
    section("two bosses on one part")
    fresh_fixture()
    first = call("attach_boss", object=SCULPT, spec=PEG_SPEC,
                 location_mm=SEAT_MM, rotation_deg=SEAT_ROT)
    second = call("attach_boss", object=SCULPT, spec=dict(PEG_SPEC, skirt=None),
                  location_mm=[0.0, RADIUS_MM - 5.0, 0.0], rotation_deg=[-90.0, 0.0, 0.0],
                  boss_id="tail")
    check("a second boss attaches", second.get("status") == "success",
          second.get("message"))
    check("ids auto-number and honour an explicit one",
          result_of(first).get("boss_id") == "boss-01"
          and result_of(second).get("boss_id") == "tail",
          (result_of(first).get("boss_id"), result_of(second).get("boss_id")))
    check("the ledger holds both", len(result_of(second)["bosses"]["ids"]) == 2,
          result_of(second)["bosses"]["ids"])
    check("the drawer holds four retained objects",
          len(bpy.data.collections["forge_bosses"].objects) == 4,
          [obj.name for obj in bpy.data.collections["forge_bosses"].objects])

    detached = call("detach_boss", object=SCULPT, boss_id="boss-01")
    check("detaching one leaves the other", detached.get("status") == "success",
          detached.get("message"))
    check("one record remains, and it is the other one",
          result_of(detached)["bosses"]["ids"] == ["tail"],
          result_of(detached)["bosses"]["ids"])
    check("two retained objects remain",
          len(bpy.data.collections["forge_bosses"].objects) == 2,
          [obj.name for obj in bpy.data.collections["forge_bosses"].objects])


def test_existing_object_boss():
    section("a boss that is an existing scene object")
    fresh_fixture()
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=24,
                          radius1=4.0 * MM, radius2=4.0 * MM, depth=12.0 * MM)
    mesh = bpy.data.meshes.new("Stud")
    bm.to_mesh(mesh)
    bm.free()
    stud = bpy.data.objects.new("Stud", mesh)
    bpy.context.scene.collection.objects.link(stud)
    stud.location = (0.0, 0.0, (RADIUS_MM - 2.0) * MM)
    bpy.context.view_layer.update()

    reply = call("attach_boss", object=SCULPT, boss="Stud")
    if not check("attaching an existing object succeeded",
                 reply.get("status") == "success", reply.get("message")):
        return
    report = result_of(reply)
    check("the report records the source object", report.get("source") == "Stud",
          report.get("source"))
    check("the source was consumed", report.get("source_consumed") == "Stud"
          and bpy.data.objects.get("Stud") is None, report.get("source_consumed"))
    check("the boss kept its own transform",
          abs(report["transform"]["location_mm"][2] - (RADIUS_MM - 2.0)) < 1e-4,
          report["transform"])
    check("and it is retained as a cutter",
          bpy.data.objects.get(report["retained"]["cutter"]) is not None)

    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    check("it detaches like any other", reply.get("status") == "success",
          reply.get("message"))


def test_save_reload():
    section("save / reload — the records have to outlive the session")
    fresh_fixture()
    reply = call("attach_boss", object=SCULPT, spec=PEG_SPEC,
                 location_mm=SEAT_MM, rotation_deg=SEAT_ROT, label="ear peg")
    if not check("attach before the save succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    report = result_of(reply)
    before_ledger = bpy.data.objects[SCULPT].get("forge_bosses")

    workdir = tempfile.mkdtemp(prefix="forge_bosses_")
    _TEMP.append(workdir)
    path = os.path.join(workdir, "bosses.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    note("saved and reopened %s" % path)

    target = bpy.data.objects.get(SCULPT)
    if not check("the target survived the round trip", target is not None):
        return
    check("the ledger survived byte for byte",
          target.get("forge_bosses") == before_ledger,
          (target.get("forge_bosses") or "")[:80])
    collection = bpy.data.collections.get("forge_bosses")
    check("the drawer survived with both objects",
          collection is not None and len(collection.objects) == 2,
          collection and [obj.name for obj in collection.objects])
    check("and it is still hidden",
          collection is not None and collection.hide_viewport and collection.hide_render)
    check("the retained objects kept their stamps",
          collection is not None
          and {obj.get("forge_boss_id") for obj in collection.objects} == {"boss-01"},
          collection and [obj.get("forge_boss_id") for obj in collection.objects])

    reply = call("list_bosses", object=SCULPT)
    check("list_bosses reads the reloaded ledger",
          reply.get("status") == "success"
          and result_of(reply)["bosses"][0]["reversible"] is True,
          reply.get("message") or result_of(reply).get("bosses"))
    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    check("a boss attached in another session still detaches",
          reply.get("status") == "success", reply.get("message"))
    if reply.get("status") == "success":
        check("and the cut removed what that session recorded",
              abs(result_of(reply)["volume_removed_mm3"]
                  - result_of(reply)["expected_removed_mm3"])
              <= 0.05 * result_of(reply)["expected_removed_mm3"],
              (result_of(reply)["volume_removed_mm3"],
               result_of(reply)["expected_removed_mm3"]))


def test_refusals():
    section("refusals")
    fresh_fixture()

    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    check("detaching from a part with no records at all refuses",
          reply.get("status") == "error"
          and "no boss records" in (reply.get("message") or "").lower(),
          reply.get("message"))

    call("attach_boss", object=SCULPT, spec=PEG_SPEC,
         location_mm=SEAT_MM, rotation_deg=SEAT_ROT)

    reply = call("detach_boss", object=SCULPT, boss_id="nope")
    check("an unknown boss id refuses and lists the known ones",
          reply.get("status") == "error"
          and "'boss-01'" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("attach_boss", object=SCULPT, spec=PEG_SPEC, boss_id="boss-01")
    check("a duplicate boss id refuses",
          reply.get("status") == "error"
          and "already carries" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("attach_boss", object=SCULPT)
    check("neither boss nor spec refuses",
          reply.get("status") == "error"
          and "exactly one" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("attach_boss", object=SCULPT, boss=SCULPT, spec=PEG_SPEC)
    check("both boss and spec refuses",
          reply.get("status") == "error"
          and "exactly one" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("attach_boss", object=SCULPT, boss=SCULPT)
    check("attaching a part to itself refuses",
          reply.get("status") == "error"
          and "different object" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("attach_boss", object=SCULPT, spec={"kind": "sphere", "diameter_mm": 3})
    check("an unknown spec kind refuses and names the known ones",
          reply.get("status") == "error"
          and "cylinder, cone, peg" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("attach_boss", object=SCULPT, spec={"kind": "peg", "length_mm": 8})
    check("a spec missing a dimension refuses",
          reply.get("status") == "error"
          and "diameter_mm" in (reply.get("message") or ""),
          reply.get("message"))

    reply = call("move_boss", object=SCULPT, boss_id="boss-01")
    check("move_boss with no new transform refuses",
          reply.get("status") == "error"
          and "needs a new transform" in (reply.get("message") or ""),
          reply.get("message"))

    # the one that matters: the retained solid deleted behind the tool's back
    ledger = json.loads(bpy.data.objects[SCULPT]["forge_bosses"])
    added = bpy.data.objects.get(ledger[0]["added"])
    if added is not None:
        bpy.data.objects.remove(added, do_unlink=True)
    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    check("a boss whose retained solid was deleted refuses, and says why",
          reply.get("status") == "error"
          and "retained added object is gone" in (reply.get("message") or ""),
          reply.get("message"))
    reply = call("list_bosses", object=SCULPT)
    check("list_bosses reports it as no longer reversible",
          result_of(reply)["bosses"][0]["reversible"] is False,
          result_of(reply)["bosses"])
    reply = call("detach_boss", object=SCULPT, boss_id="boss-01", force=True)
    check("force=true drops the record and says the union is still in the mesh",
          reply.get("status") == "success"
          and result_of(reply).get("forced") is True
          and "still in the mesh" in " ".join(result_of(reply).get("notes", [])),
          reply.get("message") or result_of(reply).get("notes"))

    reply = call("attach_boss", object="no-such-object", spec=PEG_SPEC)
    check("an unknown target refuses",
          reply.get("status") == "error" and "no object named" in
          (reply.get("message") or "").lower(), reply.get("message"))


def cycle():
    """One whole attach/detach from a fresh fixture; returns the digests."""
    fresh_fixture()
    target = bpy.data.objects[SCULPT]
    start = digest(target)
    reply = call("attach_boss", object=SCULPT, spec=PEG_SPEC,
                 location_mm=SEAT_MM, rotation_deg=SEAT_ROT)
    if reply.get("status") != "success":
        return None
    attached = digest(target)
    reply = call("detach_boss", object=SCULPT, boss_id="boss-01")
    if reply.get("status") != "success":
        return None
    return start, attached, digest(target)


def test_determinism():
    section("determinism — two runs, same numbers")
    first = cycle()
    second = cycle()
    if not check("both cycles ran", first is not None and second is not None):
        return
    check("the fixture is byte-deterministic", first[0][0] == second[0][0],
          (first[0][0], second[0][0]))
    check("the attached geometry is byte-deterministic",
          first[1][0] == second[1][0], (first[1][1], second[1][1]))
    check("the detached geometry is byte-deterministic",
          first[2][0] == second[2][0], (first[2][1], second[2][1]))
    note("fixture %s -> attached %s -> detached %s (both runs)"
         % (first[0][0], first[1][0], first[2][0]))


def test_port_is_free_after():
    section("teardown")
    from forge import server as forge_server

    forge_server.stop_server()
    probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
    probe.setsockopt(socketlib.SOL_SOCKET, socketlib.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


# ---------------------------------------------------------------------------

def main():
    print("Forge add-on reversible boss attachment headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    try:
        test_registration()
        before = test_attach()
        if before is not None:
            test_detach(before)
        test_zero_epsilon_control()
        test_move()
        test_two_bosses_and_ids()
        test_existing_object_boss()
        test_refusals()
        test_determinism()
        test_save_reload()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        import shutil

        for directory in _TEMP:
            shutil.rmtree(directory, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
