"""Headless add-on tests for grab-and-track (``forge.tools.trackforge``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_trackforge.py

The socket port is **9919** — 9876 belongs to the artist's live session and
9878 through 9918 to the other suites, so this is the next free one.  Every
command is driven through the real socket.

The fixture is constructed so every answer is known before it is measured:

* ``Block`` — a 20 mm cube, unparented, at (10, -5, 3) mm turned 30 deg about Z:
  the exact-path mover;
* ``Rig`` — an armature at (60, 0, 0) mm with ``root`` (0..40 mm up Z), ``tip``
  (40..80 mm, child of root, NOT connected) and ``nub`` (80..90 mm, connected
  to tip — the refusal case);
* ``Skin`` — a 10 x 10 x 80 mm column of 9 rings on the rig's Armature
  modifier, rings at z <= 40 mm weighted wholly to ``root`` and the rest wholly
  to ``tip``, so a deformed vertex's ground truth is one bone's pose matrix
  applied to its rest position — computed here independently of the module.

What is proved: registration on location / vertex / bone; probe after an object
transform and after a pose change (the vertex tracker follows the deformation);
grab_to exact landing for all eight axis-lock combinations with locked channels
bit-identical; refusals (locked axis, connected bone) leave the scene
bit-identical; pivot = tracker (location, vertex, deformed vertex via a bone
grab); the ledger survives save + reload.
"""

import json
import math
import os
import shutil
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9919  # not 9876 (a live session) and not 9878..9918 (every other suite)

MM = 0.001
M_TO_MM = 1000.0

BLOCK = "Block"
BLOCK_SIZE_MM = 20.0
BLOCK_LOC_MM = (10.0, -5.0, 3.0)
BLOCK_ROT_DEG = 30.0

RIG = "Rig"
RIG_LOC_MM = (60.0, 0.0, 0.0)
SKIN = "Skin"
RING_COUNT = 9
RING_STEP_MM = 10.0
HALF_WIDTH_MM = 5.0
SPLIT_Z_MM = 40.0
TOP_INDEX = (RING_COUNT - 1) * 4      # first vertex of the top ring (z = 80 mm)
BOTTOM_INDEX = 0

#: The landing gate from the brief: 1e-5 mm.  Blender stores a location as
#: float32, so a pivot at ~50 mm can land no closer than half an ulp of 0.05 m
#: (1.9e-6 mm per axis); 1e-5 mm is ~3 ulps of room above that floor.
LANDING_MM = 1.0e-5
#: Comparisons against a ground truth this file computes with mathutils, which
#: is single precision: a few float32 ulps at 100 mm scale.
TRUTH_MM = 1.0e-4

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


# --- wire -------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def _roundtrip(payload, timeout=120.0):
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
        time.sleep(0.002)
    thread.join(timeout=2.0)
    if "error" in box:
        return {"status": "error", "message": "harness: %s" % box["error"]}
    return box.get("reply") or {"status": "error",
                                "message": "no reply within %.0fs" % timeout}


def call(command, **params):
    return _roundtrip({"type": command, "params": params})


def result_of(reply):
    return reply.get("result") or {}


def ok(reply):
    return reply.get("status") == "success"


# --- fixture ----------------------------------------------------------------

def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for arm in list(bpy.data.armatures):
        if arm.users == 0:
            bpy.data.armatures.remove(arm)


def _link(obj):
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def build_block():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=BLOCK_SIZE_MM * MM)
    mesh = bpy.data.meshes.new(BLOCK)
    bm.to_mesh(mesh)
    bm.free()
    obj = _link(bpy.data.objects.new(BLOCK, mesh))
    obj.location = Vector(BLOCK_LOC_MM) * MM
    obj.rotation_euler = Euler((0.0, 0.0, math.radians(BLOCK_ROT_DEG)))
    bpy.context.view_layer.update()
    return obj


def build_rig():
    data = bpy.data.armatures.new(RIG)
    arm = _link(bpy.data.objects.new(RIG, data))
    arm.location = Vector(RIG_LOC_MM) * MM
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = data.edit_bones.new("root")
    root.head, root.tail = (0.0, 0.0, 0.0), (0.0, 0.0, SPLIT_Z_MM * MM)
    tip = data.edit_bones.new("tip")
    tip.head, tip.tail = (0.0, 0.0, SPLIT_Z_MM * MM), (0.0, 0.0, 80.0 * MM)
    tip.parent = root
    tip.use_connect = False
    nub = data.edit_bones.new("nub")
    nub.head, nub.tail = (0.0, 0.0, 80.0 * MM), (0.0, 0.0, 90.0 * MM)
    nub.parent = tip
    nub.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    return arm


def build_skin(arm):
    verts = []
    corners = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    for ring in range(RING_COUNT):
        z = ring * RING_STEP_MM * MM
        for sx, sy in corners:
            verts.append((sx * HALF_WIDTH_MM * MM, sy * HALF_WIDTH_MM * MM, z))
    faces = [(0, 3, 2, 1), tuple(TOP_INDEX + k for k in range(4))]
    for ring in range(RING_COUNT - 1):
        a, b = ring * 4, (ring + 1) * 4
        for k in range(4):
            n = (k + 1) % 4
            faces.append((a + k, a + n, b + n, b + k))
    mesh = bpy.data.meshes.new(SKIN)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = _link(bpy.data.objects.new(SKIN, mesh))
    obj.location = Vector(RIG_LOC_MM) * MM
    root_group = obj.vertex_groups.new(name="root")
    tip_group = obj.vertex_groups.new(name="tip")
    for index, co in enumerate(verts):
        group = root_group if co[2] * M_TO_MM <= SPLIT_Z_MM + 1e-9 else tip_group
        group.add([index], 1.0, "REPLACE")
    modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = arm
    bpy.context.view_layer.update()
    return obj


def fresh():
    clear_scene()
    block = build_block()
    arm = build_rig()
    skin = build_skin(arm)
    return block, arm, skin


# --- ground truth (independent of the module) --------------------------------

def world_mm(matrix, local_m):
    return Vector(matrix @ Vector(local_m)) * M_TO_MM


def deformed_truth_mm(skin, arm, index):
    """World position of a single-bone-weighted vertex, from the pose matrices."""
    co = skin.data.vertices[index].co
    bone = "root" if co.z * M_TO_MM <= SPLIT_Z_MM + 1e-9 else "tip"
    pose_bone = arm.pose.bones[bone]
    deform = pose_bone.matrix @ pose_bone.bone.matrix_local.inverted()
    world = arm.matrix_world @ deform @ arm.matrix_world.inverted() @ skin.matrix_world @ co
    return Vector(world) * M_TO_MM


def dist(a, b):
    return (Vector(a) - Vector(b)).length


def location_mm(obj):
    return [float(v) * M_TO_MM for v in obj.matrix_world.translation]


def matrix_snapshot(obj):
    return [float(obj.matrix_world[i][j]) for i in range(4) for j in range(4)]


# --- tests ------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import registry

    for name in ("track_points", "probe", "grab_to", "list_trackers"):
        check("%s is a protocol command" % name, registry.has_command(name))
    for name in ("probe", "list_trackers", "list_bosses"):
        check("%s is read-only (no undo checkpoint)" % name,
              name in registry.READ_ONLY_COMMANDS)
    for name in ("track_points", "grab_to"):
        check("%s is NOT read-only (Ctrl+Z reaches it)" % name,
              name not in registry.READ_ONLY_COMMANDS)
    check("the older commands are untouched",
          all(registry.has_command(name) for name in
              ("ping", "attach_boss", "list_bosses", "seat_part", "measure_socket")))


def register_all(block, arm, skin):
    corner = world_mm(block.matrix_world, Vector((1, 1, 1)) * (BLOCK_SIZE_MM * 0.5 * MM))
    r1 = call("track_points", object=BLOCK, points=[
        {"name": "corner", "location_mm": list(corner)},
        {"name": "v0", "vertex_index": 0},
    ])
    r2 = call("track_points", object=RIG, points=[
        {"name": "tip_head", "bone": "tip"},
        {"name": "root_tail", "bone": "root", "end": "tail"},
    ])
    r3 = call("track_points", object=SKIN, points=[
        {"name": "top", "vertex_index": TOP_INDEX},
        {"name": "bottom", "vertex_index": BOTTOM_INDEX},
        {"name": "skin_tip", "bone": "tip"},
    ])
    return corner, r1, r2, r3


def test_track_points():
    section("track_points — location, vertex, bone")
    block, arm, skin = fresh()
    corner, r1, r2, r3 = register_all(block, arm, skin)
    for label, reply in (("Block", r1), ("Rig", r2), ("Skin", r3)):
        check("track_points on %s succeeded" % label, ok(reply), reply.get("message"))
    if not (ok(r1) and ok(r2) and ok(r3)):
        return
    pos = result_of(r1)["positions_mm"]
    check("location tracker reads back the world point it was given",
          dist(pos["corner"], corner) < TRUTH_MM, (pos["corner"], list(corner)))
    v0 = world_mm(block.matrix_world, block.data.vertices[0].co)
    check("vertex tracker reads matrix_world @ co", dist(pos["v0"], v0) < TRUTH_MM,
          (pos["v0"], list(v0)))
    pos = result_of(r2)["positions_mm"]
    head = world_mm(arm.matrix_world, arm.pose.bones["tip"].head)
    check("bone tracker reads the posed head", dist(pos["tip_head"], head) < TRUTH_MM,
          (pos["tip_head"], list(head)))
    tail = world_mm(arm.matrix_world, arm.pose.bones["root"].tail)
    check("end=tail reads the tail", dist(pos["root_tail"], tail) < TRUTH_MM)
    pos = result_of(r3)["positions_mm"]
    check("a bone tracker on a mesh resolves its Armature modifier's rig",
          dist(pos["skin_tip"], head) < TRUTH_MM
          and result_of(r3)["trackers"][2]["kind"] == "bone")
    check("the ledger is a JSON string on the object",
          isinstance(skin.get("forge_trackers"), str)
          and len(json.loads(skin["forge_trackers"])) == 3)

    before = block.get("forge_trackers")
    bad = [
        ("an out-of-range vertex", [{"name": "x", "vertex_index": 999}], "out of range"),
        ("two sources at once", [{"name": "x", "vertex_index": 1, "location_mm": [0, 0, 0]}],
         "exactly one"),
        ("a missing bone", [{"name": "x", "bone": "nope"}], "no Armature modifier"),
        ("a batch with one bad point",
         [{"name": "ok1", "vertex_index": 1}, {"name": "bad", "vertex_index": -3}], ">="),
    ]
    for label, points, needle in bad:
        reply = call("track_points", object=BLOCK, points=points)
        check("%s is refused" % label,
              reply.get("status") == "error" and needle in (reply.get("message") or ""),
              reply.get("message"))
    check("and the ledger is unchanged by every refusal",
          block.get("forge_trackers") == before)
    reply = call("track_points", object=RIG, points=[{"name": "x", "bone": "nope"}])
    check("a bone the armature lacks is refused with the bone list",
          reply.get("status") == "error" and "root" in (reply.get("message") or ""),
          reply.get("message"))


def test_probe_after_transform():
    section("probe — after an object transform")
    block, arm, skin = fresh()
    corner, *_ = register_all(block, arm, skin)
    local_corner = Vector((1, 1, 1)) * (BLOCK_SIZE_MM * 0.5 * MM)
    block.matrix_world = (Matrix.Translation(Vector((-22.0, 14.5, 8.25)) * MM)
                          @ Euler((math.radians(11), math.radians(-37),
                                   math.radians(64))).to_matrix().to_4x4())
    bpy.context.view_layer.update()
    ledgers = {name: bpy.data.objects[name].get("forge_trackers")
               for name in (BLOCK, RIG, SKIN)}
    matrix = matrix_snapshot(block)
    reply = call("probe", names=["corner", "v0"], pairs=[["corner", "v0"]])
    if not check("probe succeeded", ok(reply), reply.get("message")):
        return
    report = result_of(reply)
    truth_corner = world_mm(block.matrix_world, local_corner)
    truth_v0 = world_mm(block.matrix_world, block.data.vertices[0].co)
    check("location tracker rode the new transform",
          dist(report["positions_mm"]["corner"], truth_corner) < TRUTH_MM,
          (report["positions_mm"]["corner"], list(truth_corner)))
    check("vertex tracker rode the new transform",
          dist(report["positions_mm"]["v0"], truth_v0) < TRUTH_MM)
    d = report["distances"][0]["distance_mm"]
    check("pair distance is the true corner-to-v0 distance",
          abs(d - (truth_corner - truth_v0).length) < TRUTH_MM, d)
    check("probe wrote nothing (ledgers and matrix identical)",
          all(bpy.data.objects[n].get("forge_trackers") == ledgers[n] for n in ledgers)
          and matrix_snapshot(block) == matrix)

    reply = call("probe")
    check("probe with no names returns every tracker",
          ok(reply) and result_of(reply)["count"] == 7, result_of(reply).get("count"))
    reply = call("probe", names=["%s::top" % SKIN])
    check("qualified names resolve", ok(reply), reply.get("message"))
    reply = call("probe", names=["nope"])
    check("an unknown name is an error listing the known ones",
          reply.get("status") == "error" and "corner" in (reply.get("message") or ""))

    reply = call("list_trackers", object=SKIN)
    check("list_trackers reports definitions and resolution",
          ok(reply) and result_of(reply)["count"] == 3
          and all(row["resolves"] for row in result_of(reply)["objects"][0]["trackers"]),
          reply.get("message"))

    started = time.perf_counter()
    for _ in range(20):
        call("probe", names=["top", "bottom", "corner", "tip_head"])
    per = (time.perf_counter() - started) / 20.0
    note("probe round trip (4 trackers, incl. 2 deformed vertices): %.2f ms" % (per * 1000))
    check("probe is cheap enough to call every step (< 50 ms round trip)", per < 0.05,
          "%.2f ms" % (per * 1000))


def test_probe_after_pose():
    section("probe — after an armature pose change (the vertex follows the deformation)")
    block, arm, skin = fresh()
    register_all(block, arm, skin)
    rest = result_of(call("probe", names=["top", "bottom", "tip_head"]))["positions_mm"]
    root = arm.pose.bones["root"]
    tip = arm.pose.bones["tip"]
    root.rotation_mode = "XYZ"
    tip.rotation_mode = "XYZ"
    root.rotation_euler = (math.radians(20.0), 0.0, 0.0)
    root.location = (0.004, 0.0, 0.002)
    tip.rotation_euler = (math.radians(90.0), 0.0, math.radians(15.0))
    bpy.context.view_layer.update()
    reply = call("probe", names=["top", "bottom", "tip_head", "skin_tip"])
    if not check("probe after the pose succeeded", ok(reply), reply.get("message")):
        return
    pos = result_of(reply)["positions_mm"]
    top_truth = deformed_truth_mm(skin, arm, TOP_INDEX)
    bottom_truth = deformed_truth_mm(skin, arm, BOTTOM_INDEX)
    moved = dist(pos["top"], rest["top"])
    check("the top vertex moved with the pose", moved > 10.0, "%.4f mm" % moved)
    check("the top vertex is where tip's pose matrix puts it",
          dist(pos["top"], top_truth) < TRUTH_MM,
          (pos["top"], list(top_truth)))
    check("the bottom vertex is where root's pose matrix puts it",
          dist(pos["bottom"], bottom_truth) < TRUTH_MM)
    head = world_mm(arm.matrix_world, tip.head)
    check("the bone tracker follows the posed head",
          dist(pos["tip_head"], head) < TRUTH_MM and dist(pos["skin_tip"], head) < TRUTH_MM)
    check("and it did move off rest", dist(pos["tip_head"], rest["tip_head"]) > 1.0)
    note("top vertex moved %.3f mm with the pose; %.2e mm from the independent truth"
         % (moved, dist(pos["top"], top_truth)))


LOCK_SETS = [[], ["x"], ["y"], ["z"], ["x", "y"], ["x", "z"], ["y", "z"], ["x", "y", "z"]]
OFFSET_MM = (7.25, -3.5, 12.125)


def test_grab_locks():
    section("grab_to — exact landing under every axis-lock combination")
    block, arm, skin = fresh()
    register_all(block, arm, skin)
    worst = 0.0
    for locks in LOCK_SETS:
        block.location = Vector(BLOCK_LOC_MM) * MM
        bpy.context.view_layer.update()
        before = [float(v) for v in block.location]
        start = location_mm(block)
        idx = [("xyz".index(a)) for a in locks]
        target = [start[i] + (0.0 if i in idx else OFFSET_MM[i]) for i in range(3)]
        reply = call("grab_to", object=BLOCK, target_mm=target, axis_locks=locks)
        tag = "locks=%s" % ("".join(locks) or "none")
        if not check("%s: grab succeeded" % tag, ok(reply), reply.get("message")):
            continue
        report = result_of(reply)
        landed = dist(location_mm(block), target)
        worst = max(worst, landed, report["residual_norm_mm"])
        check("%s: reported residual < %.0e mm" % (tag, LANDING_MM),
              report["residual_norm_mm"] < LANDING_MM, report["residual_norm_mm"])
        check("%s: the object's own translation is on the target" % tag,
              landed < LANDING_MM, "%.3e mm" % landed)
        check("%s: locked channels are bit-identical" % tag,
              all(float(block.location[i]) == before[i] for i in idx),
              [(float(block.location[i]), before[i]) for i in idx])
        check("%s: exact path, zero locked drift reported" % tag,
              report["solve"]["path"] == "exact"
              and all(v == 0.0 for v in report["locked_axis_drift_mm"].values()),
              (report["solve"]["path"], report["locked_axis_drift_mm"]))
    note("worst landing error over the eight lock sets: %.3e mm" % worst)

    block.location = Vector(BLOCK_LOC_MM) * MM
    bpy.context.view_layer.update()
    start = location_mm(block)
    reply = call("grab_to", object=BLOCK, delta_mm=[1.0, -2.0, 3.5])
    check("delta mode moves by exactly the delta",
          ok(reply) and dist(location_mm(block),
                             [start[0] + 1.0, start[1] - 2.0, start[2] + 3.5]) < LANDING_MM,
          reply.get("message"))
    check("the grab report carries a probe readout of the object's trackers",
          ok(reply) and set(result_of(reply)["positions_mm"])
          == {"%s::corner" % BLOCK, "%s::v0" % BLOCK})


def test_grab_refusals():
    section("grab_to — refusals leave everything bit-identical")
    block, arm, skin = fresh()
    register_all(block, arm, skin)
    snapshot = matrix_snapshot(block)
    ledger = block.get("forge_trackers")
    start = location_mm(block)
    reply = call("grab_to", object=BLOCK, axis_locks=["z"],
                 target_mm=[start[0] + 1.0, start[1], start[2] + 5.0])
    message = reply.get("message") or ""
    check("a target off the locked Z is refused",
          reply.get("status") == "error" and "Refusing" in message, message[:200])
    check("the refusal quotes the shortfall on Z",
          "shortfall      (0.000000, 0.000000, 5.000000)" in message, message)
    check("and says nothing moved", "NOTHING was moved" in message)
    check("the matrix is bit-identical", matrix_snapshot(block) == snapshot)
    check("the ledger is untouched", block.get("forge_trackers") == ledger)
    note(" / ".join(line.strip() for line in message.splitlines()[1:6]))

    reply = call("grab_to", object=BLOCK, axis_locks=["x"], delta_mm=[0.5, 0.0, 0.0])
    check("a delta along a locked axis is refused",
          reply.get("status") == "error" and "Refusing" in (reply.get("message") or ""))
    reply = call("grab_to", object=BLOCK, axis_locks="xyz", delta_mm=[0.0, 0.0, 1e-3])
    check("a 1 micron move with every axis locked is refused (not rounded away)",
          reply.get("status") == "error")
    check("still bit-identical after both", matrix_snapshot(block) == snapshot)

    reply = call("grab_to", object=RIG, bone="nub", delta_mm=[1.0, 0.0, 0.0])
    check("a connected bone is refused with its pinned head",
          reply.get("status") == "error" and "CONNECTED" in (reply.get("message") or ""),
          reply.get("message"))

    # A refused probed-path grab: the pose must come back bit-identical, probing
    # included.
    tip = arm.pose.bones["tip"]
    tip.rotation_mode = "XYZ"
    tip.rotation_euler = (math.radians(35.0), 0.0, 0.0)
    bpy.context.view_layer.update()
    saved = [float(v) for v in tip.location]
    head = world_mm(arm.matrix_world, tip.head)
    top_before = result_of(call("probe", names=["top"]))["positions_mm"]["top"]
    reply = call("grab_to", object=RIG, bone="tip", axis_locks=["z"],
                 target_mm=[head.x, head.y + 2.0, head.z + 3.0])
    check("a bone grab off a locked axis is refused",
          reply.get("status") == "error" and "Refusing" in (reply.get("message") or ""),
          reply.get("message"))
    check("the pose bone's channels are bit-identical after probing + refusal",
          [float(v) for v in tip.location] == saved)
    top_after = result_of(call("probe", names=["top"]))["positions_mm"]["top"]
    check("and the deformed vertex did not move", top_after == top_before,
          (top_before, top_after))


def test_grab_pivots():
    section("grab_to — pivot = tracker, and bone grabs")
    block, arm, skin = fresh()
    register_all(block, arm, skin)

    target = [3.5, 21.0, -4.75]
    reply = call("grab_to", object=BLOCK, pivot="corner", target_mm=target)
    if check("pivot=location tracker grab succeeded", ok(reply), reply.get("message")):
        report = result_of(reply)
        probed = result_of(call("probe", names=["corner"]))["positions_mm"]["corner"]
        check("probe puts the corner on the target",
              dist(probed, target) < LANDING_MM, "%.3e mm" % dist(probed, target))
        check("a location pivot on an unparented object takes the exact path",
              report["solve"]["path"] == "exact")

    before_y = float(block.location[1])
    corner = result_of(call("probe", names=["corner"]))["positions_mm"]["corner"]
    target = [corner[0] - 6.0, corner[1], corner[2] + 2.0]
    reply = call("grab_to", object=BLOCK, pivot="v0", axis_locks=["y"], target_mm=[
        v + d for v, d in zip(result_of(call("probe", names=["v0"]))["positions_mm"]["v0"],
                              (-6.0, 0.0, 2.0))])
    if check("pivot=vertex tracker with Y locked succeeded", ok(reply), reply.get("message")):
        report = result_of(reply)
        check("the vertex pivot lands (residual < %.0e mm)" % LANDING_MM,
              report["residual_norm_mm"] < LANDING_MM, report["residual_norm_mm"])
        check("the locked Y channel is bit-identical", float(block.location[1]) == before_y)
        note("vertex pivot: path %s, %d iteration(s), residual %.3e mm"
             % (report["solve"]["path"], report["solve"]["iterations"],
                report["residual_norm_mm"]))

    # bone grab, pivot = the bone head, one axis locked
    tip = arm.pose.bones["tip"]
    root = arm.pose.bones["root"]
    root.rotation_mode = "XYZ"
    root.rotation_euler = (math.radians(25.0), 0.0, math.radians(-10.0))
    tip.rotation_mode = "XYZ"
    tip.rotation_euler = (math.radians(60.0), 0.0, 0.0)
    bpy.context.view_layer.update()
    head = world_mm(arm.matrix_world, tip.head)
    target = [head.x, head.y - 4.0, head.z + 5.0]
    reply = call("grab_to", object=RIG, bone="tip", axis_locks=["x"], target_mm=target)
    if check("bone grab (pivot = head, X locked) succeeded", ok(reply), reply.get("message")):
        report = result_of(reply)
        new_head = world_mm(arm.matrix_world, tip.head)
        check("the head lands on the target (residual < %.0e mm)" % LANDING_MM,
              report["residual_norm_mm"] < LANDING_MM and dist(new_head, target) < TRUTH_MM,
              (report["residual_norm_mm"], dist(new_head, target)))
        drift = abs(report["locked_axis_drift_mm"]["X"])
        check("the locked X drift is reported and below 1e-5 mm", drift < 1e-5, drift)
        check("a bone grab takes the probed path", report["solve"]["path"] == "probed")
        check("the readout includes the deformed mesh's trackers",
              "%s::top" % SKIN in report["positions_mm"])
        note("bone grab: X drift %.3e mm, residual %.3e mm, %d iteration(s)"
             % (drift, report["residual_norm_mm"], report["solve"]["iterations"]))

    # bone grab steered by a deformed vertex on the skin
    top = result_of(call("probe", names=["top"]))["positions_mm"]["top"]
    target = [top[0] + 2.0, top[1] + 3.0, top[2] - 1.0]
    reply = call("grab_to", object=RIG, bone="tip", pivot="top", target_mm=target)
    if check("bone grab with pivot = deformed skin vertex succeeded", ok(reply),
             reply.get("message")):
        report = result_of(reply)
        probed = result_of(call("probe", names=["top"]))["positions_mm"]["top"]
        truth = deformed_truth_mm(skin, arm, TOP_INDEX)
        check("probe puts the deformed vertex on the target (< %.0e mm)" % LANDING_MM,
              dist(probed, target) < LANDING_MM, "%.3e mm" % dist(probed, target))
        check("and the pose-matrix truth agrees", dist(truth, target) < TRUTH_MM,
              "%.3e mm" % dist(truth, target))
        note("deformed-vertex pivot: residual %.3e mm, %d iteration(s)"
             % (report["residual_norm_mm"], report["solve"]["iterations"]))


def test_persistence():
    section("persistence — the ledger survives save + reload")
    block, arm, skin = fresh()
    register_all(block, arm, skin)
    ledgers = {name: bpy.data.objects[name].get("forge_trackers")
               for name in (BLOCK, RIG, SKIN)}
    before = result_of(call("probe"))["positions_mm"]
    workdir = tempfile.mkdtemp(prefix="forge_trackforge_")
    _TEMP.append(workdir)
    path = os.path.join(workdir, "trackers.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    note("saved and reopened %s" % path)
    check("every ledger survived byte for byte",
          all(bpy.data.objects[n].get("forge_trackers") == ledgers[n] for n in ledgers))
    reply = call("probe")
    if check("probe works on the reloaded file", ok(reply), reply.get("message")):
        after = result_of(reply)["positions_mm"]
        check("same trackers after reload", set(after) == set(before))
        worst = max(dist(after[k], before[k]) for k in before)
        check("same positions after reload", worst < 1e-9, worst)
    reply = call("grab_to", object=BLOCK, pivot="corner", delta_mm=[0.0, 0.0, 1.0])
    check("grab_to works off a reloaded ledger", ok(reply), reply.get("message"))


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


def main():
    print("Forge add-on grab-and-track headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))
    started = time.perf_counter()
    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    try:
        test_registration()
        test_track_points()
        test_probe_after_transform()
        test_probe_after_pose()
        test_grab_locks()
        test_grab_refusals()
        test_grab_pivots()
        test_persistence()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for path in _TEMP:
            shutil.rmtree(path, ignore_errors=True)

    failed = [label for label, passed, _ in _RESULTS if not passed]
    print("\n%d checks, %d failed  (%.1f s)" % (len(_RESULTS), len(failed),
                                                time.perf_counter() - started))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
