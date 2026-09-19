"""Headless add-on tests for corrective shape keys (``rigforge_correctives``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_correctives.py

Needs no geometry service and downloads nothing.

**Why this suite exists.**  ``rig_check`` on a real project came back with
40-50% volume loss at the knees and elbows at 90 degrees of flex.  The obvious
fix was tried first and it **failed**: a genuine edge-loop density pass around
the joints moved the number from 50.3% to 51.1% — nothing.  That is not a bad
retopology pass, it is the ceiling of linear-blend skinning, which averages
rigid transforms and therefore shrinks between two bones however many vertices
are sampling the average.  The production answer is a **corrective shape key
driven by the joint's bend angle** (a JCM), and this file measures whether ours
works.

**The subject is the classic case, on purpose.**  Not the Phase 3 blob biped: a
two-bone limb with a smoothly blended weight band across the joint, which is the
textbook demonstration of the collapse and has no Rigify machinery in it to
argue about.  It is built here, bone by bone, so the numbers below are
reproducible from this file alone — and it carries a real IK constraint, so the
"does the driver still fire when the limb is solved rather than rotated"
question is asked of an actual solver.

The two ``--background`` facts that shape this file are the usual ones: no event
loop (the harness drains the server queue from the main thread) and no window.
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
from mathutils import Vector

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9908  # not 9876 (a live session), not 9878-9881 (phases 2-5), not 9907 (rigik)

RIG = "SyntheticLimb"
MESH = "SyntheticLimbMesh"
BEND_ACTION = "bend"

HIP = Vector((0.0, 0.0, 1.0))
KNEE = Vector((0.0, 0.015, 0.5))
ANKLE = Vector((0.0, 0.0, 0.0))
RADIUS = 0.09
RINGS = 41
SEGMENTS = 20
#: Half-width of the thigh/shin blend ramp, in metres.  Wide on purpose: this is
#: what "automatic weights on a smooth limb" produces, it is what the real
#: project's character had, and it is the condition linear-blend skinning fails
#: under.  A narrow band would make the joint a crease and the harness's hull
#: would measure the *fold* (which legitimately changes volume) instead of the
#: *pinch* (which must not).
WEIGHT_BAND = 0.25

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


# --- the subject: a two-bone limb with a blended weight band -----------------

def _lerp(a, b, t):
    return a + (b - a) * t


def build_limb():
    """A cylinder limb, Rigify-shaped bone names, one smooth blend band.

    The bone layout copies the shape of a generated rig rather than its size:
    ``thigh.L`` / ``shin.L`` are the FK controls the harness poses, and each is
    **subdivided into two ``DEF-`` bones** the way Rigify's twist segmentation
    does, because that subdivision is what sizes ``rig_check``'s neighbourhood
    and therefore what the volume number is measured over.
    """
    section("the subject: a two-bone limb (the classic collapse case)")

    armature = bpy.data.armatures.new(RIG + "Data")
    rig = bpy.data.objects.new(RIG, armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name, head, tail, parent=None, deform=False, connect=False):
        edit = armature.edit_bones.new(name)
        edit.head = head
        edit.tail = tail
        edit.use_deform = deform
        if parent is not None:
            edit.parent = armature.edit_bones[parent]
            edit.use_connect = connect
        return edit

    mid_thigh = _lerp(HIP, KNEE, 0.5)
    mid_shin = _lerp(KNEE, ANKLE, 0.5)

    bone("thigh.L", HIP, KNEE)
    bone("shin.L", KNEE, ANKLE, parent="thigh.L", connect=True)
    bone("DEF-thigh.L", HIP, mid_thigh, parent="thigh.L", deform=True)
    bone("DEF-thigh.L.001", mid_thigh, KNEE, parent="DEF-thigh.L", deform=True,
         connect=True)
    bone("DEF-shin.L", KNEE, mid_shin, parent="DEF-thigh.L.001", deform=True)
    bone("DEF-shin.L.001", mid_shin, ANKLE, parent="DEF-shin.L", deform=True,
         connect=True)
    bone("foot_ik.L", ANKLE, ANKLE + Vector((0.0, 0.0, -0.1)))
    bpy.ops.object.mode_set(mode="OBJECT")

    # The DEF bones follow whichever chain is driving — which is the whole point
    # of hanging the corrective's driver off them rather than off an FK control.
    for deform, control in (("DEF-thigh.L", "thigh.L"), ("DEF-thigh.L.001", "thigh.L"),
                            ("DEF-shin.L", "shin.L"), ("DEF-shin.L.001", "shin.L")):
        constraint = rig.pose.bones[deform].constraints.new("COPY_ROTATION")
        constraint.name = "Follow"
        constraint.target = rig
        constraint.subtarget = control

    ik = rig.pose.bones["shin.L"].constraints.new("IK")
    ik.name = "IK"
    ik.target = rig
    ik.subtarget = "foot_ik.L"
    ik.chain_count = 2
    ik.influence = 0.0  # FK by default; the IK test switches it on and back

    check("the rig has two FK controls and four deform bones",
          len([b for b in armature.bones if b.use_deform]) == 4
          and "shin.L" in rig.pose.bones, str(sorted(b.name for b in armature.bones)))

    # --- the mesh: a capped cylinder along the limb
    bm = bmesh.new()
    rings = []
    for row in range(RINGS):
        z = 1.0 - row / float(RINGS - 1)
        ring = []
        for step in range(SEGMENTS):
            angle = 2.0 * math.pi * step / SEGMENTS
            ring.append(bm.verts.new((RADIUS * math.cos(angle),
                                      RADIUS * math.sin(angle), z)))
        rings.append(ring)
    for row in range(RINGS - 1):
        for step in range(SEGMENTS):
            nxt = (step + 1) % SEGMENTS
            bm.faces.new((rings[row][step], rings[row][nxt],
                          rings[row + 1][nxt], rings[row + 1][step]))
    bm.faces.new(tuple(reversed(rings[0])))
    bm.faces.new(tuple(rings[-1]))
    mesh_data = bpy.data.meshes.new(MESH + "Data")
    bm.to_mesh(mesh_data)
    bm.free()

    mesh = bpy.data.objects.new(MESH, mesh_data)
    bpy.context.scene.collection.objects.link(mesh)

    upper = mesh.vertex_groups.new(name="DEF-thigh.L")
    upper_twist = mesh.vertex_groups.new(name="DEF-thigh.L.001")
    lower = mesh.vertex_groups.new(name="DEF-shin.L")
    lower_twist = mesh.vertex_groups.new(name="DEF-shin.L.001")
    knee_z = KNEE.z
    for vertex in mesh_data.vertices:
        z = vertex.co.z
        # One smooth ramp across the knee, split again along each bone so the
        # twist halves carry the flesh the way a generated rig's do.
        above = min(1.0, max(0.0, (z - (knee_z - WEIGHT_BAND)) / (2.0 * WEIGHT_BAND)))
        below = 1.0 - above
        # The twist split sits OUTSIDE the joint's neighbourhood on purpose, so
        # the flesh the knee owns is blended between exactly two bones and the
        # measurement is of the thing under test rather than of a four-way mix.
        thigh_split = min(1.0, max(0.0, (z - 0.72) / 0.15))
        shin_split = min(1.0, max(0.0, (0.28 - z) / 0.15))
        upper.add([vertex.index], above * thigh_split, "REPLACE")
        upper_twist.add([vertex.index], above * (1.0 - thigh_split), "REPLACE")
        lower_twist.add([vertex.index], below * shin_split, "REPLACE")
        lower.add([vertex.index], below * (1.0 - shin_split), "REPLACE")

    modifier = mesh.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = rig
    bpy.context.view_layer.objects.active = mesh
    bpy.context.view_layer.update()

    check("the mesh is skinned to the rig",
          any(m.type == "ARMATURE" and m.object is rig for m in mesh.modifiers),
          str([m.type for m in mesh.modifiers]))
    check("every vertex carries weight (nothing rides an unweighted island)",
          all(sum(g.weight for g in v.groups) > 0.99 for v in mesh_data.vertices),
          "%d vertices" % len(mesh_data.vertices))
    note("%d vertices, %d faces" % (len(mesh_data.vertices), len(mesh_data.polygons)))
    return mesh, rig


# --- small readers ----------------------------------------------------------

def evaluated_coords(obj):
    import numpy as np

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    data = evaluated.to_mesh()
    try:
        flat = np.empty(len(data.vertices) * 3, dtype="f8")
        data.vertices.foreach_get("co", flat)
        return flat.reshape(-1, 3)
    finally:
        evaluated.to_mesh_clear()


def key_value(obj, name):
    """The **evaluated** value of a shape key — i.e. what its driver resolved to."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    keys = getattr(evaluated.data, "shape_keys", None)
    if keys is None or name not in keys.key_blocks:
        return None
    return float(keys.key_blocks[name].value)


def clear_pose(rig):
    for bone in rig.pose.bones:
        bone.matrix_basis.identity()
    bpy.context.view_layer.update()


def bone_angle_deg(rig, a, b):
    qa = rig.pose.bones[a].matrix.to_quaternion()
    qb = rig.pose.bones[b].matrix.to_quaternion()
    return math.degrees(qa.rotation_difference(qb).angle)


def volume_loss_at(rig, mesh, joint, flex_deg):
    result = call("rig_check", {
        "rig": rig.name, "mesh": mesh.name, "joints": [joint],
        "poses": [{"label": "flex", "flex_deg": flex_deg, "twist_deg": 0.0}],
        "intersections": False,
    })
    entries = [entry for entry in result["joints"] if entry["joint"] == joint]
    if not entries:
        return None, result
    return entries[0]["poses"][0]["volume_loss_pct"], result


# --- the measurement the whole thing exists for -----------------------------

def test_the_collapse_is_real(rig, mesh):
    section("the collapse, before any corrective")
    loss, result = volume_loss_at(rig, mesh, "knee.L", 90.0)
    check("rig_check measures the knee at all", loss is not None,
          str(result.get("joints_skipped")))
    if loss is None:
        raise AssertionError("no baseline; the rest of the suite needs one")
    note("knee.L at 90 deg: %.1f%% volume loss (linear-blend skinning, no corrective)"
         % loss)
    check("a smoothly blended two-bone limb really does lose volume at 90 degrees - "
          "this is the artefact, not a bug in the subject", loss > 8.0,
          "%.2f%%" % loss)
    check("rig_check reports what morph targets were live while it measured",
          isinstance(result.get("shape_keys"), dict)
          and result["shape_keys"]["count"] == 0,
          str(result.get("shape_keys")))
    return loss


def test_author(rig, mesh, baseline):
    section("rigforge_correctives - authoring, and the before/after it reports")
    before_rest = evaluated_coords(mesh)

    result = call("rigforge_correctives", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "angle_samples": [{"flex_deg": 90.0}],
    })
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    for text in result.get("notes") or []:
        note("note: %s" % text)
    note("says: %s" % result.get("says"))

    check("it wrote exactly one corrective shape key",
          result["shape_keys"] == ["corr_knee_L_090"], str(result["shape_keys"]))
    check("the key is on the mesh",
          mesh.data.shape_keys is not None
          and "corr_knee_L_090" in mesh.data.shape_keys.key_blocks,
          str(list(mesh.data.shape_keys.key_blocks.keys())
              if mesh.data.shape_keys else None))
    check("and a Basis key was created for it to be relative to",
          mesh.data.shape_keys.key_blocks[0].name == "Basis",
          mesh.data.shape_keys.key_blocks[0].name)

    joint = result["joints"][0]
    note("driver bones: %s (rest angle %.1f deg), skinning residual %.4f mm"
         % (joint["driver_bones"], joint["driver_rest_angle_deg"],
            joint["skin_residual_mm"]))
    check("the driver is hung off bones that move under FK *and* IK (the deform or "
          "ORG chain), not off the FK control",
          all(name.startswith(("DEF-", "ORG-")) for name in joint["driver_bones"]),
          str(joint["driver_bones"]))
    check("the skinning model it pulled the delta back through agrees with Blender's "
          "own evaluation", joint["skin_residual_mm"] < 0.5,
          "%.4f mm" % joint["skin_residual_mm"])

    key = joint["keys"][0]
    check("the corrective actually displaces geometry", key["vertices_moved"] > 20,
          "%d vertices, max push %.2f mm"
          % (key["vertices_moved"], key["max_push_mm"]))
    note("pinch measured: worst %.2f mm, mean %.2f mm over %d of %d region vertices"
         % (key["worst_pinch_mm"], key["mean_pinch_mm"],
            key["pinched_vertices"], key["region_vertices"]))

    # --- the table the command is required to carry
    check("the result carries its own before/after table", bool(result.get("table")),
          str(result.get("table"))[:200])
    row = result["table"][0]
    check("and the table has both numbers",
          row["volume_loss_before_pct"] is not None
          and row["volume_loss_after_pct"] is not None, str(row))
    before, after = row["volume_loss_before_pct"], row["volume_loss_after_pct"]
    note("BEFORE/AFTER  knee.L at 90 deg:  %.1f%%  ->  %.1f%%   (%.0f%% of the loss "
         "recovered)" % (before, after, row["improvement_pct"]))
    check("the before number matches the harness's own baseline",
          abs(before - baseline) < 1.0, "%.2f vs %.2f" % (before, baseline))
    check("volume loss went DOWN", after < before, "%.2f -> %.2f" % (before, after))
    check("and it was at least halved - the direction and a floor, not a vibe",
          after <= 0.5 * before, "%.2f -> %.2f" % (before, after))
    check("the command says the rest-space math it used",
          "L^-1" in (result.get("rest_space") or ""), result.get("rest_space"))
    check("the pose was restored", result.get("pose_restored") is True)
    return result, before_rest


def test_rest_is_bit_identical(mesh, before_rest):
    section("value 0 at rest: the base mesh is untouched, bit for bit")
    import numpy as np

    clear_pose(bpy.data.objects[RIG])
    after_rest = evaluated_coords(mesh)
    check("the corrective's value at rest is exactly 0",
          key_value(mesh, "corr_knee_L_090") == 0.0,
          str(key_value(mesh, "corr_knee_L_090")))
    check("and the evaluated rest mesh is bit-identical to before the key existed",
          np.array_equal(before_rest, after_rest),
          "max delta %g" % float(np.max(np.abs(before_rest - after_rest))))


def test_driver_fires_under_fk_and_ik(rig, mesh, authored):
    section("the driver fires whether the limb is ROTATED or SOLVED")
    from forge.tools import rigcheck

    joint = authored["joints"][0]
    sign = 1.0 if joint["bend_sign"] == "positive" else -1.0

    # --- FK: rotate the control the harness rotates.
    clear_pose(rig)
    control = rig.pose.bones["shin.L"]
    control.rotation_mode = "XYZ"
    control.rotation_euler = (math.radians(90.0) * sign, 0.0, 0.0)
    bpy.context.view_layer.update()
    fk_angle = bone_angle_deg(rig, joint["driver_bones"][0], joint["driver_bones"][1])
    fk_value = key_value(mesh, "corr_knee_L_090")
    note("FK: driver bones %.1f deg apart, key value %.3f" % (fk_angle, fk_value))
    check("an FK rotation drives the corrective to full strength",
          fk_value > 0.95, "%.3f at %.1f deg" % (fk_value, fk_angle))

    # --- IK: switch the solver on and place the foot so the knee folds.
    clear_pose(rig)
    ik = rig.pose.bones["shin.L"].constraints["IK"]
    ik.influence = 1.0
    target = rig.pose.bones["foot_ik.L"]
    # Ankle at sqrt(2)/2 of the straight leg from the hip: the classic 90-degree
    # fold, solved rather than keyed.
    wanted = Vector((0.0, -0.5, 0.5))
    local = rig.data.bones["foot_ik.L"].matrix_local.inverted() @ wanted
    target.location = local
    bpy.context.view_layer.update()
    ik_angle = bone_angle_deg(rig, joint["driver_bones"][0], joint["driver_bones"][1])
    ik_value = key_value(mesh, "corr_knee_L_090")
    note("IK: driver bones %.1f deg apart, key value %.3f" % (ik_angle, ik_value))
    check("the IK solver really bent the knee", ik_angle > 45.0, "%.1f deg" % ik_angle)
    check("and the same driver fires on the solve - no FK control was touched",
          ik_value > 0.5, "%.3f at %.1f deg" % (ik_value, ik_angle))

    ik.influence = 0.0
    clear_pose(rig)
    check("rig_check's own IK-switch handling is untouched by any of this",
          rigcheck._ik_switches(rig) == {}, str(rigcheck._ik_switches(rig)))


def test_report_and_clear(rig, mesh):
    section("report and clear")
    report = call("rigforge_correctives", {"rig": rig.name, "mesh": mesh.name,
                                           "action": "report"})
    check("report finds the corrective", report["count"] == 1, str(report))
    entry = report["correctives"][0]
    check("and says it is driven, with the bones and the ramp",
          entry["driven"] and len(entry["driver_bones"]) == 2
          and len(entry["driver_ramp"]) >= 2, str(entry))
    check("nothing is left undriven", report["undriven"] == [], str(report["undriven"]))
    note("ramp: %s" % entry["driver_ramp"])

    cleared = call("rigforge_correctives", {"rig": rig.name, "mesh": mesh.name,
                                            "action": "clear"})
    check("clear removes the key", cleared["removed"] == ["corr_knee_L_090"],
          str(cleared))
    check("and its driver goes with it",
          not any('corr_knee_L_090' in (f.data_path or "")
                  for f in (mesh.data.shape_keys.animation_data.drivers
                            if mesh.data.shape_keys
                            and mesh.data.shape_keys.animation_data else [])),
          "a driver survived its key")
    check("the Basis is not collateral damage",
          mesh.data.shape_keys is not None
          and mesh.data.shape_keys.key_blocks[0].name == "Basis")


def test_refusals(rig, mesh):
    section("refusals are sentences")
    reply = call("rigforge_correctives", {"rig": "NoSuchRig"}, expect_error=True)
    message = reply.get("message") or ""
    check("a rig that does not exist is refused by name",
          reply.get("status") == "error" and "NoSuchRig" in message, message[:200])

    reply = call("rigforge_correctives", {"rig": mesh.name}, expect_error=True)
    message = reply.get("message") or ""
    check("a mesh passed as the rig says what it is instead",
          reply.get("status") == "error" and "not an armature" in message,
          message[:200])

    reply = call("rigforge_correctives",
                 {"rig": rig.name, "joints": ["wrist.L"]}, expect_error=True)
    message = reply.get("message") or ""
    check("an unknown joint is refused AND told which joints this rig has",
          reply.get("status") == "error" and "wrist.L" in message
          and "knee.L" in message, message[:240])
    check("and it is a sentence, not a traceback",
          message.strip().endswith(".") and "Traceback" not in message, message[:120])

    reply = call("rigforge_correctives",
                 {"rig": rig.name, "joints": ["knee.L"], "angle_samples": [7.0]},
                 expect_error=True)
    message = reply.get("message") or ""
    check("a bare number above 1 is caught as the fraction/degrees confusion it is",
          reply.get("status") == "error" and "FRACTION" in message
          and "flex_deg" in message, message[:240])



def test_two_samples_ramp(rig, mesh):
    section("two samples: triangular ramps, so the keys do not stack")
    result = call("rigforge_correctives", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "angle_samples": [{"flex_deg": 60.0}, {"flex_deg": 120.0}],
    })
    names = result["shape_keys"]
    check("both samples were authored",
          names == ["corr_knee_L_060", "corr_knee_L_120"], str(names))
    for row in result["table"]:
        if row["volume_loss_before_pct"] is None:
            continue
        note("knee.L at %3.0f deg:  %.1f%% -> %.1f%%"
             % (row["flex_deg"], row["volume_loss_before_pct"],
                row["volume_loss_after_pct"]))
        check("volume loss dropped at %.0f deg" % row["flex_deg"],
              row["volume_loss_after_pct"] < row["volume_loss_before_pct"],
              "%.2f -> %.2f" % (row["volume_loss_before_pct"],
                                row["volume_loss_after_pct"]))

    joint = result["joints"][0]
    sign = 1.0 if joint["bend_sign"] == "positive" else -1.0
    control = rig.pose.bones["shin.L"]
    control.rotation_mode = "XYZ"
    for angle, expect_low, expect_high in ((60.0, "corr_knee_L_120", "corr_knee_L_060"),
                                           (120.0, "corr_knee_L_060", "corr_knee_L_120")):
        control.rotation_euler = (math.radians(angle) * sign, 0.0, 0.0)
        bpy.context.view_layer.update()
        low, high = key_value(mesh, expect_low), key_value(mesh, expect_high)
        note("at %.0f deg: %s=%.3f  %s=%.3f"
             % (angle, expect_high, high, expect_low, low))
        check("at %.0f deg only its own key is up" % angle,
              high > 0.9 and low < 0.15, "%s=%.3f %s=%.3f"
              % (expect_high, high, expect_low, low))
    clear_pose(rig)
    return result


def test_the_rejected_alternative(rig, mesh):
    """The push along posed normals was built, measured, and thrown away.

    It is worth a test of its own because the refusal carries the number: an
    option removed silently comes back as somebody's good idea in six months.
    """
    section("the push along normals: tried, measured WORSE, removed")
    call("rigforge_correctives", {"rig": rig.name, "mesh": mesh.name,
                                  "action": "clear"})
    reply = call("rigforge_correctives", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "direction": "normal"}, expect_error=True)
    message = reply.get("message") or ""
    check("'direction' is refused rather than silently ignored",
          reply.get("status") == "error", message[:160])
    check("and the refusal carries the measurement that settled it (39.4% along "
          "normals against 13.6% radial, from 29.7%)",
          "39.4" in message and "13.6" in message, message[:400])
    note("refusal: %s" % message)

    check("strength 0 measures and writes nothing",
          call("rigforge_correctives", {
              "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
              "angle_samples": [{"flex_deg": 90.0}], "strength": 0.0,
              "verify": False})["shape_keys"] == [])


def test_default_joint_selection(rig, mesh):
    section("no 'joints': the harness picks its own worst")
    call("rigforge_correctives", {"rig": rig.name, "mesh": mesh.name,
                                  "action": "clear"})
    result = call("rigforge_correctives", {
        "rig": rig.name, "mesh": mesh.name,
        "angle_samples": [{"flex_deg": 90.0}],
    })
    picked = sorted({entry["joint"] for entry in result["joints"]})
    check("it picked the joint rig_check measures as failing on volume",
          "knee.L" in picked, str(picked))
    check("and said so in a warning rather than silently choosing",
          any("harness picked" in w for w in result.get("warnings") or []),
          str(result.get("warnings")))
    note("picked: %s" % ", ".join(picked))
    return result


# --- the driver's own domain ------------------------------------------------
#
# The bug this section exists for, measured on ``werewolf-wip-14``: four of the
# ten shipped JCM drivers were keyed to reach 1.0 at 3.9530 rad (knee) and
# 3.9886 rad (elbow).  ``ROTATION_DIFF`` is an angle between two orientations
# and Blender folds it into ``[0, pi]`` before the F-curve sees it, so those
# keys were **unreachable**: the deep-flex correctives could not exceed 0.715
# anywhere, and at the deepest knee in any authored action (94.3 degrees) the
# knee key delivered 0.19 where its own curve intended 0.40.
#
# The cause was a measurement that did not fold where the driver folds -
# ``mathutils``' ``Quaternion.angle`` happily reports the long way round - so
# the fix is in the measurement and these checks are on both ends of it.

def driver_curves(mesh):
    """``{key name: fcurve}`` for every corrective driver on the mesh."""
    keys = mesh.data.shape_keys
    out = {}
    if keys is None or keys.animation_data is None:
        return out
    for fcurve in keys.animation_data.drivers:
        path = fcurve.data_path
        if path.startswith('key_blocks["') and path.endswith("].value"):
            out[path.split('"')[1]] = fcurve
    return out


def test_fold_is_the_drivers_own(rig, mesh):
    section("the fold: the measurement speaks the driver's units")
    from forge.tools import correctives as cx

    cases = ((0.0, 0.0), (1.1085, 1.1085), (math.pi, math.pi),
             (3.9530, 2.0 * math.pi - 3.9530), (3.9886, 2.0 * math.pi - 3.9886),
             (2.0 * math.pi, 0.0))
    worst = max(abs(cx.fold_rotation_diff(raw) - want) for raw, want in cases)
    check("every raw quaternion angle folds to the short way round, which is what "
          "ROTATION_DIFF reports", worst < 1e-9, "worst %.3g" % worst)
    note("the two werewolf keys: 3.9530 -> %.4f rad (%.1f deg), 3.9886 -> %.4f rad "
         "(%.1f deg)"
         % (cx.fold_rotation_diff(3.9530), math.degrees(cx.fold_rotation_diff(3.9530)),
            cx.fold_rotation_diff(3.9886), math.degrees(cx.fold_rotation_diff(3.9886))))
    check("nothing survives the fold above pi",
          all(cx.fold_rotation_diff(raw) <= math.pi + 1e-12
              for raw in (0.0, 1.0, 3.0, 3.9886, 5.5, 6.28, 12.0)))
    clamped = cx.clamp_driver_domain([0.1133, 1.1085, 3.9530])
    check("and the belt to that braces pulls an out-of-domain table back, still "
          "strictly ascending",
          max(clamped) <= math.pi + 1e-12
          and all(b > a for a, b in zip(clamped, clamped[1:])),
          str([round(v, 5) for v in clamped]))


def test_deep_key_is_reachable(rig, mesh):
    """The whole defect, end to end: 70/140 on the knee, like the shipped rig."""
    section("a deep corrective reaches its own authored value")
    call("rigforge_correctives", {"rig": rig.name, "mesh": mesh.name,
                                  "action": "clear"})
    result = call("rigforge_correctives", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "angle_samples": [{"flex_deg": 70.0}, {"flex_deg": 140.0}],
    })
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    joint = result["joints"][0]
    names = result["shape_keys"]
    check("both samples were authored, shallow and deep",
          names == ["corr_knee_L_070", "corr_knee_L_140"], str(names))
    note("driver bones %s, rest angle %.2f deg, curve tops out at %.4f rad "
         "(domain cap %.4f)"
         % (joint["driver_bones"], joint["driver_rest_angle_deg"],
            joint["driver_curve_max_rad"], joint["driver_domain_max_rad"]))

    curves = driver_curves(mesh)
    check("every authored key has a driver", set(names) <= set(curves),
          str(sorted(curves)))
    worst_x, worst_key = 0.0, None
    for name, fcurve in sorted(curves.items()):
        for point in fcurve.keyframe_points:
            if float(point.co.x) > worst_x:
                worst_x, worst_key = float(point.co.x), name
        note("  %s ramp %s" % (name, [(round(float(p.co.x), 4),
                                       round(float(p.co.y), 3))
                                      for p in fcurve.keyframe_points]))
    check("NO driver f-curve keyframe sits past pi - a key the variable cannot "
          "reach is a corrective that never arrives",
          worst_x <= math.pi + 1e-9,
          "worst %.4f rad on %s against pi = %.4f" % (worst_x, worst_key, math.pi))

    sign = 1.0 if joint["bend_sign"] == "positive" else -1.0
    control = rig.pose.bones["shin.L"]
    control.rotation_mode = "XYZ"

    def value_at(flex_deg, name):
        control.rotation_euler = (math.radians(flex_deg) * sign, 0.0, 0.0)
        bpy.context.view_layer.update()
        return key_value(mesh, name)

    deep = value_at(140.0, "corr_knee_L_140")
    note("corr_knee_L_140 at its own 140 deg sample: %.4f "
         "(the shipped rig's deep keys could not pass 0.715)" % (deep or 0.0))
    check("the deep key reaches 1.0 at the angle it was authored for",
          deep is not None and deep > 0.99, "%.4f" % (deep or 0.0))

    # The number the review quoted: a knee at 94.3 degrees got 0.19 where the
    # curve intended 0.40. Here the intent is read off the authored ramp rather
    # than hard-coded, and the driver has to deliver it.
    ramp = [(float(p.co.x), float(p.co.y))
            for p in curves["corr_knee_L_140"].keyframe_points]
    ramp.sort()
    measured = value_at(94.3, "corr_knee_L_140")
    lower, upper = ramp[0], ramp[-1]
    # Where the driver actually reads at this pose, in its own folded units.
    from forge.tools import correctives as cx
    angle = cx._rotation_difference(rig, *joint["driver_bones"])
    span = max(1e-9, upper[0] - lower[0])
    intended = min(1.0, max(0.0, (angle - lower[0]) / span))
    note("at 94.3 deg the driver reads %.4f rad; the ramp intends %.3f and the key "
         "evaluates to %.3f" % (angle, intended, measured or 0.0))
    check("the driver's reading is inside its own domain at a real pose",
          angle <= math.pi + 1e-9, "%.4f rad" % angle)
    check("and a 94-degree knee receives the value its curve intends, not a "
          "plateau part-way up an unreachable slope",
          measured is not None and abs(measured - intended) < 0.01,
          "%.4f measured vs %.4f intended" % (measured or 0.0, intended))
    check("which on this ramp is a real fraction of the correction rather than "
          "nothing", intended > 0.2, "%.3f" % intended)

    # And the fold where it bites: past half a turn ``mathutils`` reports the
    # long way round while the driver reports the short one. That divergence is
    # the werewolf bug in one line, so it is pinned against Blender's own
    # evaluation rather than against arithmetic.
    control.rotation_euler = (math.radians(220.0) * sign, 0.0, 0.0)
    bpy.context.view_layer.update()
    a, b = joint["driver_bones"]
    raw = (rig.pose.bones[a].matrix.to_quaternion()
           .rotation_difference(rig.pose.bones[b].matrix.to_quaternion()).angle)
    folded = cx._rotation_difference(rig, a, b)
    note("at 220 deg of flex mathutils reports %.4f rad (%.1f deg); the module "
         "reports %.4f rad (%.1f deg)"
         % (raw, math.degrees(raw), folded, math.degrees(folded)))
    check("the module's reading never leaves ROTATION_DIFF's domain, whatever "
          "mathutils says", folded <= math.pi + 1e-9, "%.4f rad" % folded)
    check("and where the two disagree it is exactly the fold, not a different "
          "measurement",
          abs(folded - (2.0 * math.pi - raw if raw > math.pi else raw)) < 1e-9,
          "raw %.6f vs folded %.6f" % (raw, folded))
    clear_pose(rig)
    return result


def test_regenerating_lands_the_fix(rig, mesh, previous):
    """A character that already carries keys has to come out corrected too."""
    section("re-keying an existing character lands the corrected curves")
    before = {name: [(round(float(p.co.x), 6), round(float(p.co.y), 6))
                     for p in fcurve.keyframe_points]
              for name, fcurve in driver_curves(mesh).items()}
    check("the mesh already carries the keys to be regenerated",
          set(before) >= set(previous["shape_keys"]), str(sorted(before)))

    again = call("rigforge_correctives", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "angle_samples": [{"flex_deg": 70.0}, {"flex_deg": 140.0}],
    })
    after = {name: [(round(float(p.co.x), 6), round(float(p.co.y), 6))
                    for p in fcurve.keyframe_points]
             for name, fcurve in driver_curves(mesh).items()}
    check("the keys were overwritten in place rather than duplicated",
          sorted(after) == sorted(before), str(sorted(after)))
    check("re-keying is deterministic - the same curves come back", after == before,
          str([name for name in after if after[name] != before.get(name)]))
    worst = max((x for rows in after.values() for x, _ in rows), default=0.0)
    check("and the regenerated curves are inside the domain, so a character keyed "
          "before the fix comes out corrected", worst <= math.pi + 1e-9,
          "worst %.4f rad" % worst)
    joint = again["joints"][0]
    check("the report quotes the domain it kept to",
          joint["driver_curve_max_rad"] <= joint["driver_domain_max_rad"] + 1e-9,
          "%.4f vs %.4f" % (joint["driver_curve_max_rad"],
                            joint["driver_domain_max_rad"]))


# --- the export -------------------------------------------------------------

def author_bend_action(rig, authored):
    """A ten-frame clip that bends the knee — the thing the driver must survive."""
    joint = authored["joints"][0]
    sign = 1.0 if joint["bend_sign"] == "positive" else -1.0
    clear_pose(rig)
    action = bpy.data.actions.get(BEND_ACTION)
    if action is not None:
        bpy.data.actions.remove(action)
    action = bpy.data.actions.new(BEND_ACTION)
    from forge.tools import rigforge_rig as rr

    rr.assign_action(rig, action)
    control = rig.pose.bones["shin.L"]
    control.rotation_mode = "XYZ"
    for frame, angle in ((1, 0.0), (10, 90.0)):
        control.rotation_euler = (math.radians(angle) * sign, 0.0, 0.0)
        control.keyframe_insert(data_path="rotation_euler", frame=frame)
    bpy.context.scene.frame_set(1)
    clear_pose(rig)
    return action


def test_export_carries_morphs_and_weights(rig, mesh, authored, workspace):
    section("the glTF round trip: morph targets AND an animated weights channel")
    from forge.tools import rigforge_rig as rr

    rna = [prop.identifier
           for prop in bpy.ops.export_scene.gltf.get_rna_type().properties]
    for name in ("export_morph", "export_morph_animation", "export_morph_reset_sk_data"):
        check("the installed exporter really has %s (not a kwarg op_kwargs drops)"
              % name, name in rna,
              str([n for n in rna if "morph" in n]))

    action = author_bend_action(rig, authored)
    driven = rr.driven_shape_keys(mesh)
    check("the mesh's corrective is driven, so the export has to bake its weight",
          driven == ["corr_knee_L_090"], str(driven))

    path = os.path.join(workspace, "limb.glb")
    result = call("rigforge_export_godot", {
        "rig": rig.name, "path": path, "actions": [action.name], "lods": "auto",
        "godot_import_script": False,
    })
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    check("the glTF exists", os.path.isfile(path) and os.path.getsize(path) > 1024,
          "%s bytes" % (os.path.getsize(path) if os.path.exists(path) else "missing"))
    check("the export reports the morph targets it shipped",
          any(name.endswith("corr_knee_L_090") for name in result["morph_targets"]),
          str(result["morph_targets"]))
    check("and that it baked the driven weight for this clip",
          any(name.endswith("corr_knee_L_090")
              for name in result["morph_animation"].get(action.name, [])),
          str(result["morph_animation"]))

    import headless_phase4 as phase4

    doc = phase4.parse_gltf(path)
    meshes = doc.get("meshes", [])
    targets = [len(prim.get("targets") or [])
               for entry in meshes for prim in entry.get("primitives", [])]
    check("the .glb really carries a morph target", any(targets), str(targets))
    names = []
    for entry in meshes:
        extras = entry.get("extras") or {}
        names.extend(extras.get("targetNames") or [])
    check("named corr_knee_L_090, so Godot can find it",
          "corr_knee_L_090" in names, str(names))

    animations = doc.get("animations", [])
    clips = [entry.get("name") for entry in animations]
    check("the clip shipped under its own name", action.name in clips, str(clips))
    weight_channels = []
    for entry in animations:
        for channel in entry.get("channels", []):
            if (channel.get("target") or {}).get("path") == "weights":
                weight_channels.append((entry.get("name"), channel))
    check("and it carries an animated WEIGHTS channel - a driver does not export, so "
          "without the bake the corrective would arrive inert",
          bool(weight_channels), str(clips))
    if weight_channels:
        clip_name, channel = weight_channels[0]
        check("the weights channel is in the same animation as the bones, not a "
              "second clip", clip_name == action.name, str(clip_name))
        sampler = animations[clips.index(clip_name)]["samplers"][channel["sampler"]]
        values = _accessor_floats(path, doc, sampler["output"])
        note("baked weights across the clip: %s"
             % ", ".join("%.2f" % v for v in values))
        check("the baked weight really follows the bend (0 at the start, ~1 at the "
              "end)", values and values[0] < 0.1 and max(values) > 0.9,
              "%s" % (values[:12],))
        check("and never overshoots past 1 (a morph above 1 inflates the limb it "
              "was meant to rescue)", max(values) <= 1.001, "max %.4f" % max(values))
    return path


def _accessor_floats(path, doc, index):
    """Read a scalar float accessor out of a .glb's binary chunk."""
    import struct

    with open(path, "rb") as handle:
        data = handle.read()
    offset = 12
    binary = None
    while offset < len(data):
        chunk_length, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        if chunk_type == 0x004E4942:  # 'BIN'
            binary = data[offset + 8:offset + 8 + chunk_length]
            break
        offset += 8 + chunk_length
    if binary is None:
        return []
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"]
    return list(struct.unpack_from("<%df" % count, binary, start))


def test_harness_sees_the_correctives(rig, mesh):
    section("rig_check evaluates the mesh WITH shape keys and drivers applied")
    result = call("rig_check", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "poses": [{"label": "flex", "flex_deg": 90.0, "twist_deg": 0.0}],
        "intersections": False,
    })
    keys = result.get("shape_keys") or {}
    check("the report names the morph targets that were live",
          keys.get("count", 0) >= 1 and "corr_knee_L_090" in (keys.get("driven") or []),
          str(keys))
    check("and states that the measurement is on the evaluated mesh",
          keys.get("evaluated") is True and "evaluated" in (keys.get("says") or ""),
          str(keys.get("says")))

    # Muting the key must change the number, which is the only proof that the
    # harness was reading through the shape key rather than past it.
    block = mesh.data.shape_keys.key_blocks["corr_knee_L_090"]
    with_key = [e for e in result["joints"] if e["joint"] == "knee.L"][0]
    block.mute = True
    bpy.context.view_layer.update()
    muted = call("rig_check", {
        "rig": rig.name, "mesh": mesh.name, "joints": ["knee.L"],
        "poses": [{"label": "flex", "flex_deg": 90.0, "twist_deg": 0.0}],
        "intersections": False,
    })
    block.mute = False
    bpy.context.view_layer.update()
    without = [e for e in muted["joints"] if e["joint"] == "knee.L"][0]
    note("volume loss with the corrective live: %.1f%%; with it muted: %.1f%%"
         % (with_key["worst_volume_loss_pct"], without["worst_volume_loss_pct"]))
    check("muting the corrective makes the harness's number worse again - so the "
          "harness really was measuring through it",
          without["worst_volume_loss_pct"] > with_key["worst_volume_loss_pct"] + 1.0,
          "%.2f vs %.2f" % (without["worst_volume_loss_pct"],
                            with_key["worst_volume_loss_pct"]))
    check("the report says the region was built from the evaluated rest shape",
          muted.get("pose_restored") is True)


# --- shutdown ---------------------------------------------------------------

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
    print("Forge add-on corrective shape key headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_correctives_test_")
    try:
        mesh, rig = build_limb()
        baseline = test_the_collapse_is_real(rig, mesh)
        authored, before_rest = test_author(rig, mesh, baseline)
        test_rest_is_bit_identical(mesh, before_rest)
        test_driver_fires_under_fk_and_ik(rig, mesh, authored)
        test_harness_sees_the_correctives(rig, mesh)
        test_export_carries_morphs_and_weights(rig, mesh, authored, workspace)
        test_report_and_clear(rig, mesh)
        test_refusals(rig, mesh)
        test_two_samples_ramp(rig, mesh)
        test_fold_is_the_drivers_own(rig, mesh)
        deep = test_deep_key_is_reachable(rig, mesh)
        test_regenerating_lands_the_fix(rig, mesh, deep)
        test_the_rejected_alternative(rig, mesh)
        test_default_joint_selection(rig, mesh)
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
