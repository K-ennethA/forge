"""Headless add-on tests for Phase 5 (RigForge cloth and animation).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_phase5.py

Needs no geometry service and **downloads nothing**.  The mocap clip the
retarget test consumes is written by :func:`write_bvh` below — a hand-authored
nineteen-joint hierarchy with twelve frames of rotation, put on disk in a temp
folder and deleted with it.  That is authoring, not fetching; the whole point of
the retarget command is that it reads files the sculptor already has.

The sculpt is Phase 3's, tagged the way Phase 4 expects, carried through the
whole chain in one run: tag -> retopo -> metarig -> generate -> **garment ->
actions -> keyframes -> retarget** -> glTF, with the exported file parsed back
out of the .glb.  Importing the builder rather than copying it is deliberate:
three suites disagreeing about what the test character looks like would be
worse than no suite at all.

The two ``--background`` facts that shape this file are the usual ones: no event
loop (the harness drains the server queue from the main thread) and no window.
The cloth sim is the interesting case — it runs by stepping ``scene.frame_set``,
which needs no viewport at all.
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

import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9881  # not 9876 (a live session), 9878/9879/9880 (phases 2, 3, 4)
SCULPT = "Sculpt"
RETOPO = SCULPT + "_retopo"

SHIRT = "Shirt"
CLOAK = "Cloak"
IDLE = "idle-loop"
HOP = "hop"
MOCAP = "mocap-loop"

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


def call(command, params=None, timeout=1800.0, expect_error=False):
    """One socket round trip, pumping the main-thread queue while it is in flight."""
    from forge import server as forge_server

    payload = {"type": command, "params": params or {}}
    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(1 << 20)
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
        reply = {"status": "error", "message": "harness: %s" % box["error"]}
    else:
        reply = box.get("reply") or {"status": "error",
                                     "message": "no reply within %.0fs" % timeout}
    if expect_error:
        return reply
    if reply.get("status") != "success":
        raise AssertionError("%s failed: %s" % (command, reply.get("message")))
    return reply.get("result") or {}


# --- the hand-authored mocap clip -------------------------------------------

#: ``(name, parent, offset)`` for the synthetic skeleton, root first.  The names
#: are deliberately Mixamo-flavoured because that is the naming the auto mapping
#: has to survive; ``LeftHandIndex1`` is in there on purpose, as a joint the
#: mapping must *refuse* rather than fold into the hand.
JOINTS = [
    ("Hips", None, (0.0, 0.0, 0.0)),
    ("Spine", "Hips", (0.0, 10.0, 0.0)),
    ("Chest", "Spine", (0.0, 15.0, 0.0)),
    ("Neck", "Chest", (0.0, 20.0, 0.0)),
    ("Head", "Neck", (0.0, 8.0, 0.0)),
    ("LeftShoulder", "Chest", (5.0, 15.0, 0.0)),
    ("LeftArm", "LeftShoulder", (12.0, 0.0, 0.0)),
    ("LeftForeArm", "LeftArm", (25.0, 0.0, 0.0)),
    ("LeftHand", "LeftForeArm", (22.0, 0.0, 0.0)),
    ("LeftHandIndex1", "LeftHand", (8.0, 0.0, 0.0)),
    ("RightShoulder", "Chest", (-5.0, 15.0, 0.0)),
    ("RightArm", "RightShoulder", (-12.0, 0.0, 0.0)),
    ("RightForeArm", "RightArm", (-25.0, 0.0, 0.0)),
    ("RightHand", "RightForeArm", (-22.0, 0.0, 0.0)),
    ("LeftUpLeg", "Hips", (8.0, 0.0, 0.0)),
    ("LeftLeg", "LeftUpLeg", (0.0, -40.0, 0.0)),
    ("LeftFoot", "LeftLeg", (0.0, -40.0, 0.0)),
    ("RightUpLeg", "Hips", (-8.0, 0.0, 0.0)),
    ("RightLeg", "RightUpLeg", (0.0, -40.0, 0.0)),
    ("RightFoot", "RightLeg", (0.0, -40.0, 0.0)),
]

BVH_FRAMES = 12


def write_bvh(path):
    """Write a small, valid BVH by hand. Nothing is downloaded, ever."""
    children = {}
    for name, parent, _offset in JOINTS:
        children.setdefault(parent, []).append(name)
    offsets = {name: offset for name, _parent, offset in JOINTS}

    lines = ["HIERARCHY"]
    order = []

    def emit(name, depth, root=False):
        pad = "\t" * depth
        lines.append("%s%s %s" % (pad, "ROOT" if root else "JOINT", name))
        lines.append("%s{" % pad)
        lines.append("%s\tOFFSET %.4f %.4f %.4f" % ((pad,) + offsets[name]))
        if root:
            lines.append("%s\tCHANNELS 6 Xposition Yposition Zposition "
                         "Zrotation Xrotation Yrotation" % pad)
            order.append((name, 6))
        else:
            lines.append("%s\tCHANNELS 3 Zrotation Xrotation Yrotation" % pad)
            order.append((name, 3))
        kids = children.get(name, [])
        if kids:
            for kid in kids:
                emit(kid, depth + 1)
        else:
            lines.append("%s\tEnd Site" % pad)
            lines.append("%s\t{" % pad)
            lines.append("%s\t\tOFFSET 0.0000 6.0000 0.0000" % pad)
            lines.append("%s\t}" % pad)
        lines.append("%s}" % pad)

    emit("Hips", 0, root=True)

    channels = sum(count for _name, count in order)
    lines.append("MOTION")
    lines.append("Frames: %d" % BVH_FRAMES)
    lines.append("Frame Time: 0.0416667")
    for frame in range(BVH_FRAMES):
        phase = 2.0 * math.pi * frame / float(BVH_FRAMES)
        swing = math.sin(phase)
        row = []
        for name, count in order:
            if count == 6:
                # a little travel forward plus a bob, so hip location has to
                # survive the retarget as well as the rotations
                row.extend([0.0, 2.0 * abs(swing), 6.0 * frame])
                row.extend([0.0, 4.0 * swing, 0.0])
                continue
            if name in ("LeftArm", "RightLeg"):
                row.extend([0.0, 35.0 * swing, 0.0])
            elif name in ("RightArm", "LeftLeg"):
                row.extend([0.0, -35.0 * swing, 0.0])
            elif name in ("LeftForeArm", "RightForeArm"):
                row.extend([0.0, -20.0 * abs(swing), 0.0])
            elif name in ("Spine", "Chest", "Head"):
                row.extend([6.0 * swing, 0.0, 0.0])
            else:
                row.extend([0.0, 0.0, 0.0])
        assert len(row) == channels, "%d channels, %d values" % (channels, len(row))
        lines.append(" ".join("%.4f" % value for value in row))

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path, channels


# --- geometry helpers -------------------------------------------------------

def world_points(obj):
    matrix = obj.matrix_world
    return [matrix @ vertex.co for vertex in obj.data.vertices]


def bbox_of(points):
    return (Vector((min(p.x for p in points), min(p.y for p in points),
                    min(p.z for p in points))),
            Vector((max(p.x for p in points), max(p.y for p in points),
                    max(p.z for p in points))))


def nearest_distances(garment, body):
    """Distance from each garment vertex to the closest body vertex."""
    points = world_points(body)
    tree = KDTree(len(points))
    for index, point in enumerate(points):
        tree.insert(point, index)
    tree.balance()
    out = []
    for point in world_points(garment):
        _co, _index, distance = tree.find(point)
        out.append(distance)
    out.sort()
    return out


# --- the pipeline up to a rigged character ----------------------------------

def build_character():
    section("the tagged sculpt (Phase 3/4 builder, reused)")
    import headless_phase4 as phase4

    obj, regions = phase4.build_tagged_biped()
    for name, faces in sorted(regions.items()):
        if faces:
            call("rigforge_tag", {"object": obj.name, "tag": name, "faces": faces,
                                  "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get", "archetype": "biped",
                               "motion_notes": "ears are floppy and lag behind the head"})
    listing = call("rigforge_list_tags", {"object": obj.name})
    check("the shared builder produced the eight tags",
          len(listing["tags"]) == 8, str(sorted(e["name"] for e in listing["tags"])))

    section("retopo -> metarig -> rig")
    started = time.monotonic()
    call("rigforge_retopo", {"object": obj.name, "target_faces": 4000,
                             "platform": "mobile", "lods": 1})
    retopo = bpy.data.objects.get(RETOPO)
    if not check("the retopo mesh exists", retopo is not None):
        raise AssertionError("no retopo mesh")

    meta = call("rigforge_metarig", {"object": RETOPO, "archetype": "auto"})
    generated = call("rigforge_generate_rig", {"metarig": meta["metarig"],
                                               "mesh": RETOPO})
    rig = bpy.data.objects.get(generated["rig"])
    check("the rig exists", rig is not None, generated["rig"])
    note("retopo+rig took %.1fs; %d deform bones"
         % (time.monotonic() - started, generated["deform_bones"]))
    return retopo, rig


# --- cloth ------------------------------------------------------------------

def test_cloth_skin_tight(body, rig):
    section("rigforge_cloth - skin_tight on the Torso tag")
    result = call("rigforge_cloth", {
        "object": body.name, "tags": ["Torso"], "name": SHIRT,
        "output": "skin_tight", "offset_mm": 20.0, "thickness_mm": 6.0})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    garment = bpy.data.objects.get(SHIRT)
    if not check("the garment object exists", garment is not None, result["garment"]):
        return None
    check("it is a mesh with faces",
          garment.type == "MESH" and len(garment.data.polygons) > 0,
          "%s, %d faces" % (garment.type, len(garment.data.polygons)))
    check("the command reports skin_tight", result["output"] == "skin_tight",
          result["output"])
    check("no shape key was baked (skin_tight does not simulate)",
          not result["shape_keys"] and garment.data.shape_keys is None,
          str(result["shape_keys"]))
    check("no cloth or collision modifier survived",
          not [m for m in garment.modifiers if m.type in ("CLOTH", "COLLISION")],
          str([m.type for m in garment.modifiers]))
    check("the body was left without a collision modifier",
          not [m for m in body.modifiers if m.type == "COLLISION"],
          str([m.type for m in body.modifiers]))

    # --- the offset is real geometry, not a modifier
    distances = nearest_distances(garment, body)
    median = distances[len(distances) // 2]
    check("the garment sits off the body by about the offset it was given",
          0.005 <= median <= 0.06, "median nearest-vertex distance %.4f m" % median)
    torso = [p for p in world_points(body)]
    body_box = bbox_of(torso)
    garment_box = bbox_of(world_points(garment))
    check("the garment is inside the body's world (it did not fly off)",
          all(garment_box[0][i] > body_box[0][i] - 0.5
              and garment_box[1][i] < body_box[1][i] + 0.5 for i in range(3)),
          "%s vs %s" % (list(garment_box[0]), list(body_box[0])))

    section("the garment is skinned to the same rig")
    check("the weights report names the rig", result["weights"]["rig"] == rig.name,
          str(result["weights"]))
    check("it has an armature modifier bound to the rig",
          any(m.type == "ARMATURE" and m.object is rig for m in garment.modifiers),
          str([(m.type, getattr(m.object, "name", None)) for m in garment.modifiers]))
    check("and it is parented to the rig", garment.parent is rig,
          str(garment.parent.name if garment.parent else None))

    from forge.tools import rigforge_rig as rr

    weights = rr.weight_report(garment, rig)
    check("every garment vertex is weighted to a deform bone",
          weights["unweighted_vertices"] == 0,
          "%d unweighted" % weights["unweighted_vertices"])
    check("no vertex has more than 4 influences",
          weights["max_influences_found"] <= 4, "max %d" % weights["max_influences_found"])
    check("the weights are normalised", weights["unnormalized_vertices"] == 0,
          "%d un-normalised" % weights["unnormalized_vertices"])
    check("the deform groups came from the body, not from nowhere",
          weights["deform_groups"] >= 3, str(weights["deform_groups"]))
    note("weights: %s" % json.dumps(result["weights"]))

    check("the garment carries the Garment tag",
          garment.vertex_groups.get("tag_Garment") is not None,
          str([g.name for g in garment.vertex_groups][:6]))
    check("it remembers the body it grew from",
          garment.get("forge_garment_of") == body.name,
          str(garment.get("forge_garment_of")))
    check("it inherited the body's material slot layout",
          len(garment.material_slots) == len(body.material_slots),
          "%d vs %d" % (len(garment.material_slots), len(body.material_slots)))
    return garment


def test_cloth_shapekeys(body, rig):
    section("rigforge_cloth - shapekeys with the cotton preset")
    before = {name: len(bpy.data.objects[name].data.vertices)
              for name in bpy.data.objects.keys()
              if bpy.data.objects[name].type == "MESH"}
    started = time.monotonic()
    result = call("rigforge_cloth", {
        "object": body.name, "tags": ["Torso"], "name": CLOAK,
        "output": "shapekeys", "preset": "cotton", "frames": 24,
        "offset_mm": 30.0, "thickness_mm": 4.0, "collision": True})
    note("cloth sim took %.1fs" % (time.monotonic() - started))
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    garment = bpy.data.objects.get(CLOAK)
    if not check("the garment exists", garment is not None, result["garment"]):
        return None

    sim = result.get("sim") or {}
    note("sim: %s" % json.dumps({k: v for k, v in sim.items() if k != "physics"}))
    check("the sim reports that it ran and settled", sim.get("ok") is True,
          sim.get("reason"))
    check("it ran the frames it was asked for", sim.get("frames") == 24,
          str(sim.get("frames")))

    check("a Settled shape key was baked", result["shape_keys"] == ["Settled"],
          str(result["shape_keys"]))
    keys = garment.data.shape_keys
    if check("the mesh carries shape keys", keys is not None):
        names = [block.name for block in keys.key_blocks]
        check("Basis and Settled, in that order", names == ["Basis", "Settled"], str(names))
        settled = keys.key_blocks["Settled"]
        check("Settled is fully applied (value 1.0)", abs(settled.value - 1.0) < 1e-6,
              str(settled.value))
        basis = keys.key_blocks["Basis"]
        moved = max((settled.data[i].co - basis.data[i].co).length
                    for i in range(len(basis.data)))
        check("the sim actually moved the cloth (Settled differs from Basis)",
              moved > 1e-5, "largest displacement %.6f m" % moved)
        note("largest settled displacement: %.5f m over %d vertices"
             % (moved, len(basis.data)))

    section("the sim does not ship")
    check("the cloth modifier was removed from the garment",
          not [m for m in garment.modifiers if m.type == "CLOTH"],
          str([m.type for m in garment.modifiers]))
    check("the collision modifier was removed from the body",
          not [m for m in body.modifiers if m.type == "COLLISION"],
          str([m.type for m in body.modifiers]))
    check("what is left is an ordinary skinned mesh",
          [m.type for m in garment.modifiers] == ["ARMATURE"],
          str([m.type for m in garment.modifiers]))

    section("the settled result is sane, not exploded")
    size = sim.get("bbox")
    body_size = [body.dimensions[i] for i in range(3)]
    if check("the sim reports the settled bounding box", bool(size), str(size)):
        check("it is inside three times the body on every axis",
              all(size[i] <= body_size[i] * 3.0 + 1e-6 for i in range(3)),
              "%s vs body %s" % (size, [round(v, 4) for v in body_size]))
    check("the body mesh itself was not touched",
          len(body.data.vertices) == before.get(body.name),
          "%d vs %d" % (len(body.data.vertices), before.get(body.name, -1)))

    check("the cotton physics are reported so they can be checked",
          result["physics"].get("mass") == 0.30
          and result["physics"].get("tension_stiffness") == 15.0,
          json.dumps(result["physics"]))
    check("the boundary of the patch was pinned",
          result["pinned_vertices"] > 0, str(result["pinned_vertices"]))
    return garment


def test_cloth_bones_and_errors(body):
    section("rigforge_cloth - the v1 limits and the refusals")
    result = call("rigforge_cloth", {"object": body.name, "tags": ["Head"],
                                     "name": "Hat", "output": "bones",
                                     "offset_mm": 10.0})
    check("output='bones' still produces a garment", "Hat" in bpy.data.objects,
          result["garment"])
    check("it says what it actually did", result["output"] == "skin_tight"
          and result["requested_output"] == "bones",
          "%s / %s" % (result["output"], result["requested_output"]))
    check("and it warns loudly rather than silently substituting",
          any("bones" in w and "not implemented" in w
              for w in result.get("warnings") or []),
          str(result.get("warnings")))
    hat = bpy.data.objects.get("Hat")
    if hat is not None:
        bpy.data.objects.remove(hat, do_unlink=True)

    reply = call("rigforge_cloth", {"object": body.name, "tags": ["Trousers"]},
                 expect_error=True)
    check("an unknown tag is an error that lists the real tags",
          reply.get("status") == "error" and "Torso" in (reply.get("message") or ""),
          reply.get("message"))
    reply = call("rigforge_cloth", {"object": body.name}, expect_error=True)
    check("no tags and no selection is an error that says what to pass",
          reply.get("status") == "error"
          and "use_selection" in (reply.get("message") or ""),
          reply.get("message"))
    reply = call("rigforge_cloth", {"object": body.name, "tags": ["Torso"],
                                    "use_selection": True}, expect_error=True)
    check("passing both tags and use_selection is refused",
          reply.get("status") == "error", reply.get("message"))


# --- the action library -----------------------------------------------------

def test_actions(rig):
    section("rigforge_action - the Godot library and the -loop convention")
    result = call("rigforge_action", {"action": "new", "name": "idle",
                                      "loop": True, "rig": rig.name})
    check("loop=true appended Godot's -loop suffix", result["name"] == IDLE,
          result["name"])
    check("the new action was assigned to the rig", result["assigned"] is True,
          str(result))
    check("the rig really is holding it",
          rig.animation_data is not None and rig.animation_data.action is not None
          and rig.animation_data.action.name == IDLE,
          str(rig.animation_data.action if rig.animation_data else None))

    plain = call("rigforge_action", {"action": "new", "name": "attack-loop",
                                     "loop": False, "rig": rig.name})
    check("loop=false stripped a suffix that was already there",
          plain["name"] == "attack", plain["name"])

    listing = call("rigforge_action", {"action": "list", "rig": rig.name})
    entries = {entry["name"]: entry for entry in listing["actions"]}
    check("list returns every action in the file", IDLE in entries and "attack" in entries,
          str(sorted(entries)))
    check("the loop flag is read back off the suffix",
          entries[IDLE]["loop"] is True and entries["attack"]["loop"] is False,
          str([(n, e["loop"]) for n, e in sorted(entries.items())]))
    check("each entry carries a frame range",
          all(isinstance(entry["frame_range"], list) and len(entry["frame_range"]) == 2
              for entry in listing["actions"]),
          str([entry["frame_range"] for entry in listing["actions"]]))
    check("and says whether it is in the NLA",
          all("nla" in entry for entry in listing["actions"]),
          str(sorted(entries)))
    check("nothing is in the NLA yet",
          not any(entry["nla"] for entry in listing["actions"]),
          str([n for n, e in entries.items() if e["nla"]]))

    duplicated = call("rigforge_action", {"action": "duplicate", "source": IDLE,
                                          "name": "idle_alt", "loop": True,
                                          "rig": rig.name})
    check("duplicate branches under a new looping name",
          duplicated["name"] == "idle_alt-loop", duplicated["name"])

    renamed = call("rigforge_action", {"action": "rename", "source": "idle_alt-loop",
                                       "name": "walk", "loop": True})
    check("rename moves an action and keeps the convention",
          renamed["name"] == "walk-loop"
          and renamed["previous_name"] == "idle_alt-loop", str(renamed))
    flipped = call("rigforge_action", {"action": "rename", "name": "walk-loop",
                                       "loop": False})
    check("rename with just loop=false strips the suffix in place",
          flipped["name"] == "walk" and flipped["previous_name"] == "walk-loop",
          str(flipped))
    back = call("rigforge_action", {"action": "rename", "name": "walk", "loop": True})
    check("and loop=true puts it back", back["name"] == "walk-loop", back["name"])

    deleted = call("rigforge_action", {"action": "delete", "name": "attack"})
    check("delete removes the action", deleted["deleted"] is True
          and bpy.data.actions.get("attack") is None, str(deleted["name"]))
    check("and the library shrank",
          "attack" not in {entry["name"] for entry in deleted["actions"]},
          str(sorted(entry["name"] for entry in deleted["actions"])))

    reply = call("rigforge_action", {"action": "delete", "name": "attack"},
                 expect_error=True)
    check("deleting a missing action is a clean error that lists the real ones",
          reply.get("status") == "error" and IDLE in (reply.get("message") or ""),
          reply.get("message"))
    reply = call("rigforge_action", {"action": "new", "name": "idle", "loop": True,
                                     "rig": rig.name}, expect_error=True)
    check("creating a name that is taken is refused, not silently suffixed",
          reply.get("status") == "error" and "already exists" in (reply.get("message") or ""),
          reply.get("message"))
    return IDLE


def test_push_nla(rig):
    section("rigforge_action - push_nla")
    result = call("rigforge_action", {"action": "push_nla", "name": "walk-loop",
                                      "rig": rig.name})
    check("a track named after the action was made",
          result["track"] == "walk-loop", str(result))
    tracks = [track.name for track in rig.animation_data.nla_tracks]
    check("the rig carries the track", "walk-loop" in tracks, str(tracks))
    strips = [strip.action.name for track in rig.animation_data.nla_tracks
              for strip in track.strips if strip.action is not None]
    check("with the action in a strip", "walk-loop" in strips, str(strips))
    entry = {e["name"]: e for e in result["actions"]}["walk-loop"]
    check("and list now reports it as being in the NLA", entry["nla"] is True,
          str(entry["nla_tracks"]))


# --- keyframing -------------------------------------------------------------

def pick_control(rig, *candidates):
    for name in candidates:
        if name in rig.pose.bones:
            return name
    return None


def test_keyframe(rig):
    section("rigforge_keyframe - the described-motion command")
    from forge.tools import rigforge_anim as ra

    controls, machinery = ra.control_bones(rig)
    note("%d control bone(s): %s ..." % (len(controls), ", ".join(controls[:10])))
    arm = pick_control(rig, "upper_arm_fk.L", "upper_arm_ik.L", "shoulder.L")
    torso = pick_control(rig, "torso", "hips", "chest")
    if not check("the rig has the control bones this test needs",
                 arm is not None and torso is not None,
                 "%s / %s" % (arm, torso)):
        return None

    keys = [
        {"bone": torso, "frame": 1, "rotation_euler_deg": [0.0, 0.0, 0.0],
         "location": [0.0, 0.0, 0.0]},
        {"bone": torso, "frame": 12, "rotation_euler_deg": [-8.0, 0.0, 0.0],
         "location": [0.0, 0.0, -0.05]},
        {"bone": torso, "frame": 24, "rotation_euler_deg": [0.0, 0.0, 0.0],
         "location": [0.0, 0.0, 0.0]},
        {"bone": arm, "frame": 1, "rotation_euler_deg": [0.0, 0.0, 0.0]},
        {"bone": arm, "frame": 12, "rotation_euler_deg": [0.0, 0.0, 30.0]},
        {"bone": arm, "frame": 24, "rotation_euler_deg": [0.0, 0.0, 0.0]},
    ]
    result = call("rigforge_keyframe", {"rig": rig.name, "action": HOP,
                                        "keys": keys, "interpolation": "LINEAR",
                                        "clear": True})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    check("the action was created", result["created"] is True and result["action"] == HOP,
          result["action"])
    check("it reports how many channels it keyed", result["keys_set"] == 9,
          "%d (3 rotations + 2 rotations+locations ... )" % result["keys_set"])
    check("and the frame range it covered", result["frame_range"] == [1, 24],
          str(result["frame_range"]))
    check("it names the bones it touched",
          sorted(result["bones"]) == sorted({torso, arm}), str(result["bones"]))

    action = bpy.data.actions.get(HOP)
    if not check("the action exists in the file", action is not None):
        return None

    path = 'pose.bones["%s"].rotation_euler' % arm
    curve = ra.find_fcurve(action, path, 2)
    if check("there is a Z rotation curve for %s" % arm, curve is not None, path):
        at = {int(round(point.co.x)): point.co.y for point in curve.keyframe_points}
        check("frame 12 holds the 30 degrees we asked for",
              12 in at and abs(at[12] - math.radians(30.0)) < 1e-4,
              str({k: round(v, 5) for k, v in sorted(at.items())}))
        check("and frames 1 and 24 are back at zero",
              abs(at.get(1, 9) ) < 1e-6 and abs(at.get(24, 9)) < 1e-6,
              str({k: round(v, 5) for k, v in sorted(at.items())}))
        modes = {point.interpolation for point in curve.keyframe_points}
        check("the interpolation we asked for was applied to the points we made",
              modes == {"LINEAR"}, str(modes))

    location = ra.find_fcurve(action, 'pose.bones["%s"].location' % torso, 2)
    check("the location channel was keyed too", location is not None,
          'pose.bones["%s"].location' % torso)

    check("a quaternion control was converted to XYZ euler and said so",
          bool(result["rotation_modes"]) and rig.pose.bones[arm].rotation_mode == "XYZ",
          str(result["rotation_modes"]))
    if "_fk" in arm:
        check("Rigify's IK/FK blend was switched to FK so the pose reaches the deform bones",
              bool(result["fk_switched"]), str(result["fk_switched"])[:120])

    section("rigforge_keyframe - clear, and errors that help")
    again = call("rigforge_keyframe", {
        "rig": rig.name, "action": HOP, "clear": True,
        "keys": [{"bone": arm, "frame": 5, "rotation_euler_deg": [0.0, 0.0, 10.0]}]})
    check("clear:true wipes the action first",
          again["cleared_fcurves"] > 0 and again["created"] is False,
          str(again["cleared_fcurves"]))
    check("and only the new keys are left",
          ra.find_fcurve(bpy.data.actions[HOP],
                         'pose.bones["%s"].location' % torso, 2) is None,
          "the torso location curve survived a clear")

    reply = call("rigforge_keyframe", {
        "rig": rig.name, "action": HOP,
        "keys": [{"bone": arm.lower(), "frame": 1, "rotation_euler_deg": [0, 0, 5]}]},
        expect_error=True)
    message = reply.get("message") or ""
    check("a near-miss bone name is refused with the name it meant",
          reply.get("status") == "error" and arm in message and "Did you mean" in message,
          message[:220])
    check("and the message lists control bones, capped so it stays readable",
          "Control bones" in message and len(message) < 2000, "%d chars" % len(message))

    reply = call("rigforge_keyframe", {
        "rig": rig.name, "action": HOP, "keys": [{"bone": arm, "frame": 3}]},
        expect_error=True)
    check("a key that sets nothing is refused by name and frame",
          reply.get("status") == "error"
          and "rotation_euler_deg" in (reply.get("message") or ""),
          reply.get("message"))

    # put the real motion back for the export test
    call("rigforge_keyframe", {"rig": rig.name, "action": HOP, "keys": keys,
                               "interpolation": "BEZIER", "clear": True})
    return HOP


# --- retargeting ------------------------------------------------------------

def test_retarget(rig, workspace):
    section("rigforge_retarget - a clip this test wrote by hand")
    path = os.path.join(workspace, "hand_authored.bvh")
    _written, channels = write_bvh(path)
    check("the BVH was authored on disk (nothing was downloaded)",
          os.path.isfile(path) and os.path.getsize(path) > 500,
          "%s (%d bytes, %d channels/frame)"
          % (path, os.path.getsize(path) if os.path.exists(path) else 0, channels))

    before = set(bpy.data.objects.keys())
    before_actions = set(bpy.data.actions.keys())
    result = call("rigforge_retarget", {
        "target_rig": rig.name, "source_path": path, "action_name": "mocap",
        "loop": True, "mapping": "auto", "scale": "auto"})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    check("the action was created under the looping name",
          result["action"] == MOCAP and result["loop"] is True, result["action"])
    action = bpy.data.actions.get(MOCAP)
    check("and it exists in the file", action is not None, str(sorted(bpy.data.actions.keys())))

    section("the mapping report")
    mapped = {entry["source"]: entry["target"] for entry in result["mapped"]}
    note("mapped %d: %s" % (len(mapped), json.dumps(mapped, indent=None)))
    check("the mapped list is not empty", bool(mapped), str(mapped))
    check("it found the hips, the head and both hands",
          {"Hips", "Head", "LeftHand", "RightHand"} <= set(mapped), str(sorted(mapped)))
    check("both arms landed on FK controls, not IK ones",
          all("_fk" in mapped.get(name, "") or "_ik" not in mapped.get(name, "")
              for name in ("LeftArm", "RightArm", "LeftForeArm", "RightForeArm")),
          str({k: mapped.get(k) for k in ("LeftArm", "LeftForeArm")}))
    check("and both legs did too",
          all(name in mapped for name in ("LeftUpLeg", "LeftLeg", "LeftFoot",
                                          "RightUpLeg", "RightLeg", "RightFoot")),
          str({k: mapped.get(k) for k in ("LeftUpLeg", "LeftLeg", "LeftFoot")}))
    check("left and right did not cross over",
          all(mapped.get(src, "").endswith(".L") for src in ("LeftArm", "LeftUpLeg"))
          and all(mapped.get(src, "").endswith(".R")
                  for src in ("RightArm", "RightUpLeg")),
          str({k: mapped.get(k) for k in ("LeftArm", "RightArm")}))
    check("no two source bones fought over one control",
          len(set(mapped.values())) == len(mapped),
          str(sorted(mapped.values())))

    unmapped = result["unmapped"]
    check("the unmapped bones are reported, not swallowed", bool(unmapped), str(unmapped))
    check("the finger joint is one of them (it must not fold into the hand)",
          "LeftHandIndex1" in unmapped, str(unmapped))
    reasons = {entry["source"]: entry["reason"] for entry in result["unmapped_detail"]}
    check("with a reason each", all(bool(text) for text in reasons.values()),
          json.dumps(reasons))

    section("the transfer itself")
    check("the scale was worked out from the two skeletons' heights",
          result["scale_mode"] == "auto" and 0.001 < result["scale"] < 1.0,
          str(result["scale"]))
    check("the clip's frames came across", result["frames"] >= BVH_FRAMES - 2,
          "%s %s" % (result["frames"], result["frame_range"]))

    from forge.tools import rigforge_rig as rr
    from forge.tools import rigforge_anim as ra

    animated = rr.action_bones(action)
    check("the baked action animates the mapped controls",
          set(mapped.values()) <= animated,
          str(sorted(set(mapped.values()) - animated)))
    check("and nothing that was not mapped",
          not (animated - set(mapped.values())
               - {entry.split("[")[0] for entry in result["fk_switched"]}),
          str(sorted(animated - set(mapped.values()))[:8]))

    root_target = mapped.get("Hips")
    curve = ra.find_fcurve(action, 'pose.bones["%s"].location' % root_target, 1)
    if check("the hips carry a location curve (the travel came across)",
             curve is not None, 'pose.bones["%s"].location' % root_target):
        values = [point.co.y for point in curve.keyframe_points]
        check("and the travel is not a flat line",
              max(values) - min(values) > 1e-4,
              "range %.6f over %d keys" % (max(values) - min(values), len(values)))

    rotation = None
    for index in range(4):
        rotation = ra.find_fcurve(
            action, 'pose.bones["%s"].rotation_quaternion' % mapped["LeftArm"], index)
        if rotation is None:
            rotation = ra.find_fcurve(
                action, 'pose.bones["%s"].rotation_euler' % mapped["LeftArm"],
                min(index, 2))
        if rotation is not None and len({round(p.co.y, 6)
                                         for p in rotation.keyframe_points}) > 1:
            break
    check("the arm really rotates over the clip", rotation is not None
          and len({round(p.co.y, 6) for p in rotation.keyframe_points}) > 1,
          str(rotation.data_path if rotation is not None else None))

    section("the import left nothing behind")
    after = set(bpy.data.objects.keys())
    check("no imported object survived", after == before, str(sorted(after ^ before)))
    check("the temp collection is gone",
          "FORGE_RETARGET_TEMP" not in bpy.data.collections,
          str(list(bpy.data.collections.keys())))
    check("the only new action is the one that was asked for",
          set(bpy.data.actions.keys()) - before_actions == {MOCAP},
          str(sorted(set(bpy.data.actions.keys()) - before_actions)))
    check("no retarget constraint was left on the rig",
          not [c.name for bone in rig.pose.bones for c in bone.constraints
               if c.name.startswith("Forge Retarget")],
          str([c.name for bone in rig.pose.bones for c in bone.constraints
               if "Forge" in c.name]))

    section("retarget refusals")
    reply = call("rigforge_retarget", {"target_rig": rig.name,
                                       "source_path": os.path.join(workspace, "nope.bvh"),
                                       "action_name": "nope"}, expect_error=True)
    check("a missing file is a clean error that says nothing is downloaded",
          reply.get("status") == "error"
          and "downloaded" in (reply.get("message") or ""), reply.get("message"))
    other = os.path.join(workspace, "clip.txt")
    with open(other, "w", encoding="utf-8") as handle:
        handle.write("not a clip\n")
    reply = call("rigforge_retarget", {"target_rig": rig.name, "source_path": other,
                                       "action_name": "nope"}, expect_error=True)
    check("an unsupported extension is refused with the two that work",
          reply.get("status") == "error" and ".bvh" in (reply.get("message") or "")
          and ".fbx" in (reply.get("message") or ""), reply.get("message"))
    check("a refused retarget still left the scene clean",
          set(bpy.data.objects.keys()) == before
          and "FORGE_RETARGET_TEMP" not in bpy.data.collections,
          str(sorted(set(bpy.data.objects.keys()) ^ before)))
    return MOCAP


# --- export -----------------------------------------------------------------

def test_export(rig, body, garments, actions, workspace):
    section("rigforge_export_godot with the garment and the new actions")
    import headless_phase4 as phase4

    before = set(bpy.data.objects.keys())
    path = os.path.join(workspace, "dressed.glb")
    started = time.monotonic()
    result = call("rigforge_export_godot", {"rig": rig.name, "path": path,
                                            "actions": actions, "lods": False})
    note("export took %.1fs" % (time.monotonic() - started))
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    check("the glTF exists on disk",
          os.path.isfile(path) and os.path.getsize(path) > 1024,
          "%s (%s bytes)" % (path,
                             os.path.getsize(path) if os.path.exists(path) else "missing"))
    doc = phase4.parse_gltf(path)
    check("it parses as JSON", isinstance(doc, dict) and "asset" in doc,
          str(list(doc)[:6]))

    meshes = [entry.get("name") for entry in doc.get("meshes", [])]
    note("meshes in the file: %s" % meshes)
    for name in garments:
        check("the garment %s is in the exported file" % name,
              any(name in str(entry) for entry in meshes), str(meshes))
    check("the body mesh came too", any(RETOPO in str(entry) for entry in meshes),
          str(meshes))

    animations = [entry.get("name") for entry in doc.get("animations", [])]
    note("animations in the file: %s" % animations)
    for name in actions:
        check("the action %s is in the file under its own name" % name,
              name in animations, str(animations))
    check("the -loop names survived the export (Godot reads the suffix)",
          [name for name in animations if str(name).endswith("-loop")],
          str(animations))

    nodes = doc.get("nodes", [])
    check("it has a skin", bool(doc.get("skins")), str(len(doc.get("skins", []))))
    joints = [nodes[index].get("name", "") for index in doc["skins"][0]["joints"]]
    leaked = sorted(name for name in joints
                    if not str(name).startswith("DEF-") and name != "root")
    check("no control bone leaked into the exported skeleton", not leaked, str(leaked))

    channels = doc["animations"][animations.index(MOCAP)].get("channels", [])
    targets = {nodes[channel["target"]["node"]].get("name") for channel in channels}
    check("the retargeted clip reaches the deform bones",
          len(targets) > 4 and all(str(name).startswith("DEF-") or name == "root"
                                   for name in targets),
          str(sorted(targets)[:8]))

    morphs = [entry for entry in doc.get("meshes", [])
              if any("targets" in primitive for primitive in entry.get("primitives", []))]
    check("the simulated garment shipped its Settled shape as a morph target",
          any(CLOAK in str(entry.get("name")) for entry in morphs),
          str([entry.get("name") for entry in morphs]))

    section("the export left no litter")
    after = set(bpy.data.objects.keys())
    check("no temporary object survived", after == before, str(sorted(after ^ before)))
    check("the garments kept their names",
          all(name in bpy.data.objects for name in garments), str(garments))
    check("the actions kept their names",
          all(bpy.data.actions.get(name) is not None for name in actions), str(actions))
    return result


# --- panel ------------------------------------------------------------------

def test_panels():
    section("panel wiring")
    import re

    from forge.ui import panels

    for name in ("VIEW3D_PT_forge_cloth", "VIEW3D_PT_forge_actions"):
        cls = getattr(bpy.types, name, None)
        if check("%s is registered" % name, cls is not None):
            check("%s hangs off the RigForge panel" % name,
                  cls.bl_parent_id == "VIEW3D_PT_forge_rigforge", cls.bl_parent_id)

    source = open(panels.__file__, "r", encoding="utf-8").read()
    state = bpy.context.scene.forge_rigforge_anim
    unknown = sorted({name for name in re.findall(r'\.prop\(ra,\s*"([a-z_]+)"', source)
                      if not hasattr(state, name)})
    check("every Phase 5 panel property exists on the scene props", not unknown,
          str(unknown))
    operators = sorted(set(re.findall(r'\.operator\(\s*\n?\s*"(forge\.rf_[a-z_]+)"',
                                      source)))
    missing = [name for name in operators
               if not hasattr(bpy.ops.forge, name.split(".")[1])]
    check("every button maps to a registered operator", not missing, str(missing))
    for name in ("forge.rf_cloth", "forge.rf_cloth_tag", "forge.rf_action",
                 "forge.rf_action_select", "forge.rf_retarget"):
        check("the panel offers %s" % name, name in operators, str(operators))

    check("the Phase 4 panels are still wired to their own props",
          bool(re.findall(r'\.prop\(rf,\s*"export_path"', source)))


def test_operators(body, rig, workspace):
    section("panel operators drive the same code")
    from forge.tools import rigforge as rf

    state = bpy.context.scene.forge_rigforge_anim
    bpy.context.view_layer.objects.active = body
    rf.pull_meta(body, bpy.context.scene.forge_rigforge)

    result = bpy.ops.forge.rf_cloth_tag(tag="Head")
    check("the tag toggle finished", "FINISHED" in result, str(result))
    check("and put the tag in the coverage list", state.cloth_tags == "Head",
          state.cloth_tags)
    bpy.ops.forge.rf_cloth_tag(tag="Head")
    check("toggling again takes it out", state.cloth_tags == "", state.cloth_tags)

    state.cloth_tags = "Head"
    state.cloth_name = "PanelHat"
    state.cloth_output = "skin_tight"
    state.cloth_offset_mm = 15.0
    state.cloth_thickness_mm = 4.0
    result = bpy.ops.forge.rf_cloth()
    check("the Make Garment button finished", "FINISHED" in result, str(result))
    check("and made the garment", "PanelHat" in bpy.data.objects, state.status)
    check("with a status line that says what happened",
          "Garment" in state.status, state.status)

    state.cloth_tags = ""
    try:
        result = bpy.ops.forge.rf_cloth()
    except RuntimeError:
        result = {"CANCELLED"}
    check("no coverage is refused into the status line, not a traceback",
          "CANCELLED" in result and state.status_is_error, state.status)

    state.action_name = "panel_test"
    state.action_loop = True
    result = bpy.ops.forge.rf_action(action="new")
    check("the New Action button finished", "FINISHED" in result, str(result))
    check("and made a looping action",
          bpy.data.actions.get("panel_test-loop") is not None, state.status)
    result = bpy.ops.forge.rf_action(action="delete", name="panel_test-loop")
    check("the Delete button removed it",
          "FINISHED" in result and bpy.data.actions.get("panel_test-loop") is None,
          state.status)

    result = bpy.ops.forge.rf_action_select(name=IDLE)
    check("the action row's radio button assigns the action",
          "FINISHED" in result and rig.animation_data.action.name == IDLE,
          str(rig.animation_data.action))

    state.retarget_path = ""
    try:
        failed = bpy.ops.forge.rf_retarget()
    except RuntimeError:
        failed = {"CANCELLED"}
    check("an empty clip path is refused into the status line",
          "CANCELLED" in failed and state.status_is_error, state.status)

    hat = bpy.data.objects.get("PanelHat")
    if hat is not None:
        bpy.data.objects.remove(hat, do_unlink=True)


def test_server_frees_its_port():
    section("server shutdown")
    from forge import server as forge_server

    forge_server.stop_server()
    check("the server reports itself stopped", not forge_server.is_running())
    probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


# --- entry point ------------------------------------------------------------

def main():
    print("Forge add-on Phase 5 (RigForge cloth + animation) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_phase5_test_")
    try:
        body, rig = build_character()
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")

        shirt = test_cloth_skin_tight(body, rig)
        cloak = test_cloth_shapekeys(body, rig)
        test_cloth_bones_and_errors(body)

        test_actions(rig)
        test_push_nla(rig)
        hop = test_keyframe(rig)
        mocap = test_retarget(rig, workspace)

        garments = [name for name in (getattr(shirt, "name", None),
                                      getattr(cloak, "name", None)) if name]
        actions = [name for name in (hop, mocap) if name]
        test_export(rig, body, garments, actions, workspace)
        test_panels()
        test_operators(body, rig, workspace)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_server_frees_its_port()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
