"""Headless add-on tests for measured auto-seating (``forge.tools.seating``).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_seating.py

The socket port is **9917** — 9876 belongs to the artist's live session and
9878 through 9916 to the other suites (9916 is ``headless_bosses``), so this is
the next free one.  Every command is driven through the real socket.

Why the fixture is constructed rather than sculpted
---------------------------------------------------
The whole claim of this module is that it *measures* instead of computing from
a formula, so the only honest test is one where the answer is known to the
micron before anything is measured.  The base here is a plain slab with two
bores **cut by boolean with cylinders whose axes, mouths, depths and radii this
file chose**, so:

* the socket axis has a ground truth (a unit vector this file wrote down);
* the floor centre has a ground truth (the cutter's own bottom cap centre);
* the radius has two ground truths — the cutter's radius, which is where the
  bore's vertices are, and ``R·cos(pi/segments)``, which is where its walls are,
  and the module has to report both and use the second for clearance;
* the seated tip has a ground truth (floor minus the insertion clearance);
* the keyway direction has a ground truth (the slot cutter's own +X).

The second bore is tilted 25°, which is the case that breaks naive ring
finding: a tilted bore opens through a flat face in an **ellipse**, and its
mouth vertices are spread over 2·R·tan(25°) = 2.98 mm of depth in no ring at
all.  A fit that grouped the mesh's own vertices by depth would find one usable
ring there and fail; this one slices the wall and finds a dozen.

The part is a 8 mm sphere with a real ``attach_boss`` peg on it — the ledger
route is an integration, not a mock — and it is parked at an arbitrary pose
(45, -28, 20) mm / (17, -43, 88)° before the boss is attached, so the seat has
a full rigid transform to find rather than a translation.

What is proved
--------------
1. **measure_socket** — axis, floor, radius, inscribed radius, depth and
   keyway, each against the construction, on both the straight and the tilted
   bore;
2. **seat_part** — the part lands with its peg tip on the constructed floor
   minus the clearance, its axis on the constructed axis, and (bore two) its
   rib in the constructed keyway; the report's own verification numbers agree
   with an independent recomputation from the part's new matrix;
3. **the ledger survives the seat** — the boss record and its retained cutters
   move with the part, so a seated part can still be detached;
4. **the measured peg fallback** lands in the same place as the ledger route;
5. **refusals** — no bore near the hint, a peg too fat for the bore, a
   verification failure (an obstruction beside the mouth that the part's body
   would cut into), a keyed peg into an unkeyed bore, and a stale ledger;
6. **determinism** — the whole seat run twice from scratch reports identical
   numbers.
"""

import hashlib
import json
import math
import os
import socket as socketlib
import sys
import threading
import time
import traceback

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9917  # not 9876 (a live session) and not 9878..9916 (every other suite)

MM = 0.001
M_TO_MM = 1000.0

# --- the constructed fixture ------------------------------------------------

SLAB = "base_slab"
SLAB_SIZE_MM = (80.0, 60.0, 24.0)
SLAB_TOP_MM = SLAB_SIZE_MM[2] * 0.5

SEGMENTS = 48
BORE_RADIUS_MM = 3.2
BORE_DEPTH_MM = 10.5
BORE_OVERSHOOT_MM = 4.0  # how far the cutter pokes out above the face

#: Ground truth: where each bore's mouth sits on the top face, and which way it
#: goes in.  Socket A is straight down; socket B is tilted 25 degrees about Y,
#: which is the ellipse case.
SOCKET_A_MOUTH = Vector((-20.0, 0.0, SLAB_TOP_MM))
SOCKET_A_AXIS = Vector((0.0, 0.0, -1.0))
SOCKET_B_TILT_DEG = 25.0
SOCKET_B_MOUTH = Vector((20.0, 0.0, SLAB_TOP_MM))
SOCKET_B_AXIS = Vector((-math.sin(math.radians(SOCKET_B_TILT_DEG)), 0.0,
                        -math.cos(math.radians(SOCKET_B_TILT_DEG)))).normalized()
#: The keyway on socket B, 40 degrees round from the tilt's own perpendicular.
SOCKET_B_KEY_DEG = 40.0
SOCKET_B_KEY = (Quaternion(SOCKET_B_AXIS, math.radians(SOCKET_B_KEY_DEG))
                @ Vector((math.cos(math.radians(SOCKET_B_TILT_DEG)), 0.0,
                          -math.sin(math.radians(SOCKET_B_TILT_DEG)))).normalized())
KEY_SLOT_OUT_MM = 0.8   # how far the slot reaches past the bore wall
KEY_SLOT_IN_MM = 0.4    # how far it overlaps into the bore, so it is one cavity
KEY_SLOT_WIDTH_MM = 2.2

#: The obstruction that sabotages socket A in the refusal test: a 2 mm wall
#: standing 7 mm from the socket axis and 10 mm proud of the face.  Seated, the
#: part's sphere has its centre 11 mm above the face, so at 7 mm off the axis
#: its surface runs from z = 19.1 to 26.9 mm and the wall's top at 22 mm is
#: inside that — a collision of about 3 mm, in a place that leaves the bore
#: itself untouched.
WALL_X_MM = (-13.0, -11.0)
WALL_Y_MM = (-15.0, 15.0)
WALL_TOP_MM = SLAB_TOP_MM + 10.0

#: The third fixture, and the one that is actually shaped like the job: a solid
#: 30 mm cylinder with a bore drilled radially into its rim, found by the
#: ``socket_angle_deg`` / ``socket_z_mm`` hint form rather than by a point.
#: This is the eevee-bowl geometry — a socket on a round rim, its mouth curved
#: rather than flat — and it is where hand arithmetic went wrong the first time.
TUBE = "base_tube"
TUBE_RADIUS_MM = 30.0
TUBE_HEIGHT_MM = 40.0
TUBE_SEGMENTS = 64
RIM_ANGLE_DEG = 55.0
RIM_Z_MM = 10.0
RIM_MOUTH = Vector((TUBE_RADIUS_MM * math.cos(math.radians(RIM_ANGLE_DEG)),
                    TUBE_RADIUS_MM * math.sin(math.radians(RIM_ANGLE_DEG)),
                    RIM_Z_MM))
RIM_AXIS = -Vector((math.cos(math.radians(RIM_ANGLE_DEG)),
                    math.sin(math.radians(RIM_ANGLE_DEG)), 0.0)).normalized()

PART = "part"
PART_RADIUS_MM = 8.0
PEG_BASE_Z_MM = 7.0  # the boss's local Z=0, 1 mm inside the sphere's pole
#: Long enough that the sphere's shoulder clears the slab at the TILTED socket.
#: A round body cannot sit flush on a flat face at an angle: the worst case is
#: at radius 3.38 mm, where the sphere is 0.827 mm below its own pole while the
#: slab's face has already dropped 1.58 mm, so the peg has to hold the body
#: about 0.83 mm further out than the flush position.  14 mm leaves it 3 mm out
#: and the clearance measured (see the reported shoulder gap).
PEG_LENGTH_MM = 14.0
PEG_DIAMETER_MM = 6.0
PEG_TIP_LOCAL_MM = PEG_BASE_Z_MM + PEG_LENGTH_MM

PEG_SPEC = {"kind": "peg", "diameter_mm": PEG_DIAMETER_MM, "length_mm": PEG_LENGTH_MM}
RIB_SPEC = {"width_mm": 1.8, "height_mm": 0.6}
PEG_SPEC_KEYED = dict(PEG_SPEC, rib=dict(RIB_SPEC))
FAT_PEG_SPEC = {"kind": "peg", "diameter_mm": 8.0, "length_mm": PEG_LENGTH_MM}

#: An arbitrary pose for the part before the boss is attached, so the seat has a
#: real rigid transform to find.  Set BEFORE the attach, because the ledger
#: records a world matrix and the module refuses a stale one.
PART_POSE = (Matrix.Translation(Vector((45.0, -28.0, 20.0)) * MM)
             @ Euler((math.radians(17.0), math.radians(-43.0), math.radians(88.0)),
                     "XYZ").to_matrix().to_4x4())

INSERTION_CLEARANCE_MM = 0.5

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


# --- wire -------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def _roundtrip(payload, timeout=180.0):
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


# --- fixture builders -------------------------------------------------------

def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _object_from_bmesh(bm, name):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def bore_frame(mouth, axis, key_direction=None):
    """A 4x4 whose origin is the mouth, +Z is the insertion axis, +X the keyway."""
    axis = Vector(axis).normalized()
    if key_direction is None:
        reference = min((Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)),
                         Vector((0.0, 0.0, 1.0))),
                        key=lambda candidate: abs(candidate.dot(axis)))
        key_direction = (reference - axis * reference.dot(axis)).normalized()
    key_direction = Vector(key_direction).normalized()
    lateral = axis.cross(key_direction).normalized()
    rotation = Matrix(((key_direction.x, lateral.x, axis.x),
                       (key_direction.y, lateral.y, axis.y),
                       (key_direction.z, lateral.z, axis.z))).to_4x4()
    return Matrix.Translation(Vector(mouth) * MM) @ rotation


def _cut(target, cutter):
    reply = call("boolean", object=target.name, operand=cutter.name,
                 operation="DIFFERENCE", apply=True, delete_operand=True,
                 solver="EXACT")
    if reply.get("status") != "success":
        raise RuntimeError("fixture boolean failed: %s" % reply.get("message"))


def drill(target, mouth, axis, key_direction=None, depth=BORE_DEPTH_MM):
    """Cut one bore (and its keyway, when asked) into ``target``."""
    frame = bore_frame(mouth, axis, key_direction)
    length = depth + BORE_OVERSHOOT_MM
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=SEGMENTS,
                          radius1=BORE_RADIUS_MM * MM, radius2=BORE_RADIUS_MM * MM,
                          depth=length * MM)
    bmesh.ops.translate(bm, verts=list(bm.verts),
                        vec=Vector((0.0, 0.0, (depth - BORE_OVERSHOOT_MM) * 0.5 * MM)))
    cutter = _object_from_bmesh(bm, "bore_cutter")
    cutter.matrix_world = frame
    bpy.context.view_layer.update()
    _cut(target, cutter)

    if key_direction is not None:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        span_x = KEY_SLOT_OUT_MM + KEY_SLOT_IN_MM
        bmesh.ops.scale(bm, verts=list(bm.verts),
                        vec=Vector((span_x * MM, KEY_SLOT_WIDTH_MM * MM, length * MM)))
        bmesh.ops.translate(
            bm, verts=list(bm.verts),
            vec=Vector(((BORE_RADIUS_MM - KEY_SLOT_IN_MM + span_x * 0.5) * MM, 0.0,
                        (depth - BORE_OVERSHOOT_MM) * 0.5 * MM)))
        slot = _object_from_bmesh(bm, "key_cutter")
        slot.matrix_world = frame
        bpy.context.view_layer.update()
        _cut(target, slot)


def build_base(obstructed=False, shallow_b=False):
    """The slab with both bores.  Deterministic: no randomness anywhere in it."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=list(bm.verts),
                    vec=Vector([value * MM for value in SLAB_SIZE_MM]))
    slab = _object_from_bmesh(bm, SLAB)
    if obstructed:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        size = (WALL_X_MM[1] - WALL_X_MM[0], WALL_Y_MM[1] - WALL_Y_MM[0],
                WALL_TOP_MM + SLAB_TOP_MM)
        bmesh.ops.scale(bm, verts=list(bm.verts),
                        vec=Vector([value * MM for value in size]))
        bmesh.ops.translate(bm, verts=list(bm.verts), vec=Vector((
            sum(WALL_X_MM) * 0.5 * MM, sum(WALL_Y_MM) * 0.5 * MM,
            (WALL_TOP_MM - SLAB_TOP_MM) * 0.5 * MM)))
        wall = _object_from_bmesh(bm, "wall")
        reply = call("boolean", object=slab.name, operand=wall.name,
                     operation="UNION", apply=True, delete_operand=True, solver="EXACT")
        if reply.get("status") != "success":
            raise RuntimeError("fixture union failed: %s" % reply.get("message"))
    drill(slab, SOCKET_A_MOUTH, SOCKET_A_AXIS)
    drill(slab, SOCKET_B_MOUTH, SOCKET_B_AXIS, SOCKET_B_KEY,
          depth=4.0 if shallow_b else BORE_DEPTH_MM)
    return bpy.data.objects[SLAB]


def build_tube():
    """A solid cylinder with one bore drilled radially into its rim."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=TUBE_SEGMENTS,
                          radius1=TUBE_RADIUS_MM * MM, radius2=TUBE_RADIUS_MM * MM,
                          depth=TUBE_HEIGHT_MM * MM)
    tube = _object_from_bmesh(bm, TUBE)
    drill(tube, RIM_MOUTH, RIM_AXIS)
    return bpy.data.objects[TUBE]


def matrix_to_mm(matrix):
    rows = []
    for row_index in range(4):
        row = list(matrix[row_index])
        if row_index < 3:
            row[3] *= M_TO_MM
        rows.extend(float(value) for value in row)
    return rows


def build_part(spec=PEG_SPEC, pose=None, name=PART):
    """A sphere with a real attach_boss peg, posed BEFORE the attach."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24,
                              radius=PART_RADIUS_MM * MM)
    obj = _object_from_bmesh(bm, name)
    obj.matrix_world = PART_POSE if pose is None else pose
    bpy.context.view_layer.update()
    boss_world = obj.matrix_world @ Matrix.Translation(
        Vector((0.0, 0.0, PEG_BASE_Z_MM)) * MM)
    reply = call("attach_boss", object=name, spec=spec,
                 matrix_mm=matrix_to_mm(boss_world), label="seat peg")
    if reply.get("status") != "success":
        raise RuntimeError("fixture attach_boss failed: %s" % reply.get("message"))
    return bpy.data.objects[name]


def fresh(spec=PEG_SPEC, obstructed=False, shallow_b=False):
    clear_scene()
    base = build_base(obstructed=obstructed, shallow_b=shallow_b)
    part = build_part(spec)
    return base, part


# --- ground truth -----------------------------------------------------------

def floor_center(mouth, axis, depth=BORE_DEPTH_MM):
    return Vector(mouth) + Vector(axis).normalized() * depth


def expected_tip(mouth, axis, clearance=INSERTION_CLEARANCE_MM, depth=BORE_DEPTH_MM):
    return floor_center(mouth, axis, depth) - Vector(axis).normalized() * clearance


def part_peg_now(part):
    """Where the part's peg is, read from the part's matrix and nothing else."""
    matrix = part.matrix_world
    rotation = matrix.to_3x3()
    tip = Vector(matrix @ (Vector((0.0, 0.0, PEG_TIP_LOCAL_MM)) * MM)) * M_TO_MM
    return {
        "tip": tip,
        "axis": (rotation @ Vector((0.0, 0.0, 1.0))).normalized(),
        "rib": (rotation @ Vector((1.0, 0.0, 0.0))).normalized(),
    }


def angle_deg(first, second):
    return math.degrees(Vector(first).normalized().angle(Vector(second).normalized()))


def hint_in(mouth, axis, depth=1.5):
    """A hint point on the bore's own axis, just inside the mouth."""
    return [round(value, 6)
            for value in (Vector(mouth) + Vector(axis).normalized() * depth)]


# --- tests ------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import registry

    for name in ("seat_part", "measure_socket", "measure_peg"):
        check("%s is a protocol command" % name, registry.has_command(name))
    check("and every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "boolean", "attach_boss", "detach_boss", "move_boss",
               "list_bosses", "export_stl", "build_floorplan")),
          "%d commands registered" % len(registry.command_names()))

    from forge.tools import seating

    check("the dihedral cap is the derived 35 deg",
          abs(seating.MAX_DIHEDRAL_DEG - 35.0) < 1e-12, seating.MAX_DIHEDRAL_DEG)
    check("the insertion clearance default matches forge_lib's depth extra",
          abs(seating.DEFAULT_INSERTION_CLEARANCE_MM - 0.5) < 1e-12,
          seating.DEFAULT_INSERTION_CLEARANCE_MM)


def _assert_socket(label, report, mouth, axis, key=None, depth=BORE_DEPTH_MM):
    measured_axis = Vector(report["axis_unit"])
    truth_axis = Vector(axis).normalized()
    error = angle_deg(measured_axis, truth_axis)
    check("%s: the measured axis is the constructed one" % label, error < 0.01,
          "%.6f deg off (measured %s, built %s)"
          % (error, [round(v, 6) for v in measured_axis],
             [round(v, 6) for v in truth_axis]))

    truth_floor = floor_center(mouth, axis, depth)
    distance = (Vector(report["floor_center_mm"]) - truth_floor).length
    check("%s: the measured floor is the cutter's own bottom cap" % label,
          distance < 0.02,
          "%.6f mm off (measured %s, built %s)"
          % (distance, report["floor_center_mm"],
             [round(v, 6) for v in truth_floor]))

    # 1e-3 mm, not zero: Blender's EXACT solver places the vertices it creates
    # to about a micron, so a micron is the fixture's own precision and nothing
    # measured off it can be tighter.
    check("%s: the fitted radius is the cutter's radius" % label,
          abs(report["radius_mm"] - BORE_RADIUS_MM) < 1.0e-3,
          "%.6f mm against %.4f mm" % (report["radius_mm"], BORE_RADIUS_MM))
    inscribed_truth = BORE_RADIUS_MM * math.cos(math.pi / SEGMENTS)
    check("%s: the inscribed radius is R*cos(pi/segments), not R" % label,
          abs(report["inscribed_radius_mm"] - inscribed_truth) < 1.0e-3,
          "%.6f mm against the %.6f mm a %d-gon actually gives"
          % (report["inscribed_radius_mm"], inscribed_truth, SEGMENTS))
    note("%s: radius %.5f mm at the vertices, %.5f mm at the walls — %.5f mm of "
         "polygon sag the formula would have missed"
         % (label, report["radius_mm"], report["inscribed_radius_mm"],
            report["polygon_sag_mm"]))
    check("%s: the fit is round to the solver's own precision" % label,
          report["residual_rms_mm"] < 5.0e-3,
          "%.8f mm RMS over %d rings" % (report["residual_rms_mm"],
                                         report["ring_count"]))
    check("%s: several rings were sliced out of the wall" % label,
          report["ring_count"] >= 4, report["ring_count"])

    if key is None:
        check("%s: no keyway is reported where none was cut" % label,
              report.get("keyway") is None, report.get("keyway"))
    else:
        keyway = report.get("keyway")
        if not check("%s: the keyway was found" % label, keyway is not None):
            return
        error = angle_deg(Vector(keyway["direction_unit"]), Vector(key))
        check("%s: the keyway points where the slot was cut" % label, error < 1.0,
              "%.5f deg off" % error)
        check("%s: the keyway is the slot's own width" % label,
              abs(keyway["width_mm"] - KEY_SLOT_WIDTH_MM) < 0.05,
              "%.5f mm against %.4f mm" % (keyway["width_mm"], KEY_SLOT_WIDTH_MM))
        check("%s: the keyway reaches past the bore wall" % label,
              keyway["radial_depth_mm"] >= KEY_SLOT_OUT_MM - 1.0e-3,
              "%.5f mm past a %.4f mm bore" % (keyway["radial_depth_mm"],
                                               BORE_RADIUS_MM))


def test_measure_socket():
    section("measure_socket — the bore against the cylinder that cut it")
    fresh()
    reply = call("measure_socket", object=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    if check("measure_socket succeeded on the straight bore",
             reply.get("status") == "success", reply.get("message")):
        report = result_of(reply)
        _assert_socket("straight bore", report, SOCKET_A_MOUTH, SOCKET_A_AXIS)
        check("the straight bore's depth is the cut depth",
              abs(report["depth_mm"] - BORE_DEPTH_MM) < 0.01,
              "%.5f mm against %.4f mm" % (report["depth_mm"], BORE_DEPTH_MM))
        check("the floor was found as a cap face, not guessed from the wall",
              report["floor_source"] == "cap face", report["floor_source"])

    reply = call("measure_socket", object=SLAB,
                 socket_hint_mm=hint_in(SOCKET_B_MOUTH, SOCKET_B_AXIS))
    if check("measure_socket succeeded on the tilted bore",
             reply.get("status") == "success", reply.get("message")):
        report = result_of(reply)
        _assert_socket("tilted bore", report, SOCKET_B_MOUTH, SOCKET_B_AXIS,
                       SOCKET_B_KEY)
        ellipse = 2.0 * BORE_RADIUS_MM * math.tan(math.radians(SOCKET_B_TILT_DEG))
        check("the tilted bore's mouth ellipse really does span %.3f mm of depth"
              % ellipse,
              abs(report["wall_span_mm"] - (BORE_DEPTH_MM + ellipse * 0.5)) < 0.15,
              "wall span %.4f mm; the ellipse alone is %.4f mm"
              % (report["wall_span_mm"], ellipse))
        note("the tilted bore's mouth is an ellipse %.4f mm deep and the fit still "
             "found %d rings in it" % (ellipse, report["ring_count"]))


def _assert_seat(label, report, part, mouth, axis, key=None):
    measured = report["verification"]["measured"]
    check("%s: the report says every check passed" % label,
          report["verification"]["passed"] is True,
          [row for row in report["verification"]["checks"] if not row["passed"]])
    check("%s: the seat was applied" % label, report.get("applied") is True)

    truth_tip = expected_tip(mouth, axis)
    peg = part_peg_now(part)
    distance = (peg["tip"] - truth_tip).length
    check("%s: the peg tip lands on the constructed floor minus the clearance" % label,
          distance < 0.03,
          "%.6f mm off (%s against the built %s)"
          % (distance, [round(v, 5) for v in peg["tip"]],
             [round(v, 5) for v in truth_tip]))
    error = angle_deg(peg["axis"], axis)
    check("%s: the part's peg axis is the constructed socket axis" % label,
          error < 0.02, "%.6f deg off" % error)
    note("%s: tip %.6f mm from ground truth, axis %.6f deg off it"
         % (label, distance, error))

    check("%s: the report's own tip offset agrees it is on the axis" % label,
          measured["tip_axis_offset_mm"] < 1.0e-4,
          "%.8f mm" % measured["tip_axis_offset_mm"])
    check("%s: the floor gap is the insertion clearance" % label,
          abs(measured["floor_gap_mm"] - INSERTION_CLEARANCE_MM) < 0.01,
          "%.6f mm against %.4f mm" % (measured["floor_gap_mm"],
                                       INSERTION_CLEARANCE_MM))
    check("%s: the axis angle is inside the derived tolerance" % label,
          measured["axis_angle_deg"] <= measured["axis_angle_tolerance_deg"],
          (measured["axis_angle_deg"], measured["axis_angle_tolerance_deg"],
           measured["axis_angle_tolerance_basis"]))
    note("%s: angle tolerance %.6f deg (%s)"
         % (label, measured["axis_angle_tolerance_deg"],
            measured["axis_angle_tolerance_basis"]))
    check("%s: nothing of the part is inside the base outside the bore" % label,
          measured["overlap"]["inside_base_outside_bore"] == 0,
          measured["overlap"])
    check("%s: the shoulder gap was measured and is positive" % label,
          measured["shoulder_gap_mm"] is not None and measured["shoulder_gap_mm"] > 0.0,
          measured["shoulder_gap_mm"])
    note("%s: shoulder gap %.4f mm — the seam a render would show, measured rather "
         "than looked at" % (label, measured["shoulder_gap_mm"]))
    # 1e-3 mm, not 0: mathutils is single precision, so a 50 mm coordinate
    # carries about 6 nanometres of representation error and two routes to the
    # same point differ by a few of those. A micron is agreement.
    check("%s: the ledger route agrees with the transform composed from it" % label,
          measured.get("ledger_matrix_agreement_mm", 1.0) < 1.0e-3,
          measured.get("ledger_matrix_agreement_mm"))

    if key is not None:
        error = angle_deg(peg["rib"], key)
        check("%s: the rib turned onto the keyway" % label, error < 1.0,
              "%.5f deg off the constructed slot" % error)
        check("%s: the report measured the same rib error" % label,
              measured["rib_alignment_error_deg"] is not None
              and measured["rib_alignment_error_deg"] < 1.0,
              measured["rib_alignment_error_deg"])
        check("%s: the roll was computed, not left at zero" % label,
              abs(report["seat"]["roll_auto_deg"]) > 1.0,
              report["seat"]["roll_auto_deg"])
        note("%s: auto roll %.4f deg (%s)" % (label, report["seat"]["roll_auto_deg"],
                                              report["seat"]["roll_basis"]))


def test_rim_angle_hint():
    section("the rim form — a socket on a round rim, named by its angle")
    clear_scene()
    build_tube()
    build_part(PEG_SPEC)

    reply = call("measure_socket", object=TUBE, socket_angle_deg=RIM_ANGLE_DEG,
                 socket_z_mm=RIM_Z_MM)
    if not check("measure_socket found the bore from an angle and a height",
                 reply.get("status") == "success", reply.get("message")):
        return
    report = result_of(reply)
    check("the hint came from a measured rim radius, not a guessed one",
          "rim radius measured" in (report.get("hint_basis") or ""),
          report.get("hint_basis"))
    note("rim hint: %s -> %s" % (report["hint_basis"], report["hint_mm"]))
    _assert_socket("rim socket", report, RIM_MOUTH, RIM_AXIS)
    # The depth is measured from the shallowest point of the wall, and on a
    # round rim that point sits on the rim itself — so the answer is the cut
    # depth less however far this 64-gon's facet sits inside the nominal 30 mm
    # at the bore's own azimuth, at most 30*(1 - cos(pi/64)) = 0.0361 mm.  (The
    # rim's 0.171 mm dip at the bore's circumferential edge is real too, but it
    # makes the wall's DEEPEST start, not its shallowest.)  This is a small
    # number and it is exactly the kind of small number a formula gets wrong in
    # the wrong direction.
    sag = TUBE_RADIUS_MM * (1.0 - math.cos(math.pi / TUBE_SEGMENTS))
    check("the measured depth is the cut depth less the rim facet's own sag",
          BORE_DEPTH_MM - sag - 1.0e-3 <= report["depth_mm"] <= BORE_DEPTH_MM + 1.0e-3,
          "%.5f mm against %.4f mm less up to %.4f mm of sag"
          % (report["depth_mm"], BORE_DEPTH_MM, sag))
    note("rim socket: measured depth %.5f mm = the %.4f mm cut less %.5f mm, and the "
         "64-gon rim can sit up to %.5f mm inside its nominal radius"
         % (report["depth_mm"], BORE_DEPTH_MM, BORE_DEPTH_MM - report["depth_mm"], sag))

    reply = call("seat_part", object=PART, base=TUBE, socket_angle_deg=RIM_ANGLE_DEG,
                 socket_z_mm=RIM_Z_MM)
    if not check("seat_part seats into a rim socket named by its angle",
                 reply.get("status") == "success", reply.get("message")):
        return
    _assert_seat("rim", result_of(reply), bpy.data.objects[PART], RIM_MOUTH, RIM_AXIS)


def test_seat_straight():
    section("seat_part — the straight bore, plain peg")
    base, part = fresh(PEG_SPEC)
    before = part.matrix_world.copy()
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    if not check("seat_part succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    report = result_of(reply)
    _assert_seat("straight", report, bpy.data.objects[PART],
                 SOCKET_A_MOUTH, SOCKET_A_AXIS)
    check("the part actually moved", (part.matrix_world.translation
                                      - before.translation).length > 1e-6)
    check("the peg came from the ledger, not a measurement",
          report["peg"]["source"] == "ledger", report["peg"]["source"])
    check("no keyway means the roll basis says so",
          "no keyway" in report["seat"]["roll_basis"], report["seat"]["roll_basis"])


def test_seat_tilted_and_keyed():
    section("seat_part — the tilted bore, keyed peg into a measured keyway")
    base, part = fresh(PEG_SPEC_KEYED)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_B_MOUTH, SOCKET_B_AXIS))
    if not check("seat_part succeeded on the tilted socket",
                 reply.get("status") == "success", reply.get("message")):
        return
    report = result_of(reply)
    _assert_seat("tilted", report, bpy.data.objects[PART], SOCKET_B_MOUTH,
                 SOCKET_B_AXIS, SOCKET_B_KEY)
    check("the report says the rib fits the keyway",
          any(row["check"] == "the rib fits the keyway" and row["passed"]
              for row in report["verification"]["checks"]),
          report["verification"]["checks"])


def test_roll_override():
    section("roll_deg — an offset on top of the keyway alignment")
    # A 1.8 mm rib in a 2.2 mm slot has 0.2 mm of play per side at a 3.6 mm
    # radius, so it turns about 3 degrees before its corner reaches the slot
    # wall.  2 degrees fits; 30 does not, and the verification is supposed to
    # know the difference rather than take the parameter's word for it.
    fresh(PEG_SPEC_KEYED)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_B_MOUTH, SOCKET_B_AXIS),
                 roll_deg=2.0)
    if not check("seat_part with a roll inside the slot's play succeeded",
                 reply.get("status") == "success", reply.get("message")):
        return
    report = result_of(reply)
    peg = part_peg_now(bpy.data.objects[PART])
    error = angle_deg(peg["rib"], SOCKET_B_KEY)
    check("the rib now sits 2 deg off the keyway, by measurement",
          abs(error - 2.0) < 0.2, "%.5f deg" % error)
    check("and the report says the roll carried the extra 2 deg",
          abs(report["seat"]["roll_deg"] - report["seat"]["roll_auto_deg"] - 2.0) < 1e-4,
          (report["seat"]["roll_deg"], report["seat"]["roll_auto_deg"]))
    note("roll %.4f deg = %.4f auto + 2.0 given; rib measured %.4f deg off the slot"
         % (report["seat"]["roll_deg"], report["seat"]["roll_auto_deg"], error))

    fresh(PEG_SPEC_KEYED)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_B_MOUTH, SOCKET_B_AXIS),
                 roll_deg=30.0)
    message = reply.get("message") or ""
    check("a roll that turns the rib out of its slot is refused",
          reply.get("status") == "error" and "does not cut into the base" in message,
          message[:300])
    check("and the part stayed where it was",
          (bpy.data.objects[PART].matrix_world.translation
           - PART_POSE.translation).length < 1e-12)


def test_ledger_survives():
    section("the boss ledger survives the seat")
    base, part = fresh(PEG_SPEC)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    if not check("seat succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    report = result_of(reply)
    check("the report says the ledger was carried",
          report["ledger"]["carried"] is True and len(report["ledger"]["ids"]) == 1,
          report["ledger"])
    check("both retained objects moved with the part",
          len(report["ledger"]["retained_moved"]) == 2,
          report["ledger"]["retained_moved"])

    listed = result_of(call("list_bosses", object=PART))
    check("list_bosses still calls the boss reversible",
          listed["bosses"] and listed["bosses"][0]["reversible"] is True,
          listed.get("bosses"))
    recorded = Vector(listed["bosses"][0]["transform"]["location_mm"])
    expected_base = Vector(bpy.data.objects[PART].matrix_world
                           @ (Vector((0.0, 0.0, PEG_BASE_Z_MM)) * MM)) * M_TO_MM
    check("the recorded boss matrix followed the part",
          (recorded - expected_base).length < 1.0e-3,
          "%s against %s" % ([round(v, 5) for v in recorded],
                             [round(v, 5) for v in expected_base]))

    volume_before = _volume(bpy.data.objects[PART])
    reply = call("detach_boss", object=PART, boss_id="boss-01")
    check("a seated part can still have its boss detached",
          reply.get("status") == "success", reply.get("message"))
    if reply.get("status") == "success":
        removed = result_of(reply)
        check("and the cut removed what the attach had added",
              abs(removed["volume_removed_mm3"] - removed["expected_removed_mm3"])
              <= 0.05 * removed["expected_removed_mm3"],
              (removed["volume_removed_mm3"], removed["expected_removed_mm3"]))
        note("detach after the seat removed %.3f mm3 of the %.3f mm3 the attach added "
             "(part was %.1f mm3)" % (removed["volume_removed_mm3"],
                                      removed["expected_removed_mm3"], volume_before))


def _volume(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    try:
        return abs(bm.calc_volume(signed=True)) * 1.0e9
    finally:
        bm.free()


def test_measured_peg_fallback():
    section("the measured-peg fallback lands in the same place")
    fresh(PEG_SPEC)
    ledger = call("seat_part", object=PART, base=SLAB,
                  socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    if not check("the ledger seat succeeded", ledger.get("status") == "success",
                 ledger.get("message")):
        return
    ledger_tip = part_peg_now(bpy.data.objects[PART])["tip"].copy()

    fresh(PEG_SPEC)
    part = bpy.data.objects[PART]
    hint = Vector(part.matrix_world @ (Vector((0.0, 3.5, 13.0)) * MM)) * M_TO_MM
    measured = call("measure_peg", object=PART,
                    peg_hint_mm=[round(value, 6) for value in hint])
    if check("measure_peg found the peg", measured.get("status") == "success",
             measured.get("message")):
        report = result_of(measured)
        check("it measures the peg's radius",
              abs(report["radius_mm"] - PEG_DIAMETER_MM * 0.5) < 0.02,
              "%.5f mm against %.4f mm" % (report["radius_mm"],
                                           PEG_DIAMETER_MM * 0.5))
        truth_tip = Vector(part.matrix_world
                           @ (Vector((0.0, 0.0, PEG_TIP_LOCAL_MM)) * MM)) * M_TO_MM
        check("and its tip, within a facet of the real one",
              (Vector(report["tip_mm"]) - truth_tip).length < 0.05,
              "%.6f mm off" % (Vector(report["tip_mm"]) - truth_tip).length)
        note("measured peg: %.4f mm of exposed wall against an %.4f mm spec length "
             "(%.4f mm of it is buried in the sphere)"
             % (report["length_mm"], PEG_LENGTH_MM,
                PEG_LENGTH_MM - report["length_mm"]))

    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS),
                 peg_hint_mm=[round(value, 6) for value in hint])
    if not check("seat_part with a measured peg succeeded",
                 reply.get("status") == "success", reply.get("message")):
        return
    report = result_of(reply)
    check("it says the peg was measured, not read",
          report["peg"]["source"] == "measured", report["peg"]["source"])
    measured_tip = part_peg_now(bpy.data.objects[PART])["tip"]
    check("the measured route seats the peg's own tip where the ledger route did",
          (measured_tip - ledger_tip).length < 0.06,
          "%.6f mm apart" % (measured_tip - ledger_tip).length)
    note("ledger route and measured route put the tip %.5f mm apart"
         % (measured_tip - ledger_tip).length)


def test_refusals():
    section("refusals — every one of them numeric")
    fresh(PEG_SPEC)

    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=[0.0, 0.0, SLAB_TOP_MM + 2.0])
    message = reply.get("message") or ""
    check("a hint on flat plate refuses rather than finding the bore 20 mm away",
          reply.get("status") == "error"
          and "No interior cylinder could be fitted" in message
          and "off its axis" in message, message)
    note("flat-plate refusal: %s" % message.split("The last said:")[-1].strip()[:180])
    check("and nothing moved", abs(bpy.data.objects[PART].matrix_world.translation[0]
                                   - PART_POSE.translation[0]) < 1e-12)

    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=[400.0, 400.0, 400.0])
    check("a hint nowhere near the base refuses with the distance",
          reply.get("status") == "error"
          and "within" in (reply.get("message") or "")
          and "nearest surface" in (reply.get("message") or ""),
          reply.get("message"))

    # too fat
    fresh(FAT_PEG_SPEC)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    message = reply.get("message") or ""
    check("a peg too fat for the bore is refused",
          reply.get("status") == "error" and "peg fits the bore" in message,
          message)
    check("and the refusal quotes the clearance it is short by",
          "clearance -" in message and "NOTHING was moved" in message, message)
    note("fat-peg refusal said: %s"
         % " / ".join(line.strip() for line in message.splitlines()
                      if "clearance" in line))
    check("the fat part did not move",
          abs(bpy.data.objects[PART].matrix_world.translation[0]
              - PART_POSE.translation[0]) < 1e-12)

    # a keyed peg into an unkeyed bore: the rib has nowhere to go
    fresh(PEG_SPEC_KEYED)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    message = reply.get("message") or ""
    check("a keyed peg into an unkeyed bore is refused by the intersection test",
          reply.get("status") == "error"
          and "does not cut into the base" in message, message)
    note("keyed-into-unkeyed refusal: %s"
         % " / ".join(line.strip() for line in message.splitlines()
                      if "sampled part vertices" in line))

    # a stale ledger: move the part after the attach
    fresh(PEG_SPEC)
    bpy.data.objects[PART].matrix_world = (
        Matrix.Translation(Vector((10.0, 10.0, 10.0)) * MM)
        @ bpy.data.objects[PART].matrix_world)
    bpy.context.view_layer.update()
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    check("a ledger left stale by moving the part is refused, with the distance",
          reply.get("status") == "error" and "stale" in (reply.get("message") or ""),
          reply.get("message"))


def test_sabotaged_socket():
    section("a sabotaged socket — the verification catches what a render would")
    fresh(PEG_SPEC, obstructed=True)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    message = reply.get("message") or ""
    check("the seat into an obstructed mouth is refused",
          reply.get("status") == "error", message[:200])
    check("the refusal is the intersection check, not something vaguer",
          "does not cut into the base" in message, message[:400])
    check("it quotes how many vertices and how deep",
          "sampled part vertices are inside" in message and "deepest" in message,
          message[:400])
    check("it says nothing was moved",
          "NOTHING was moved" in message, message[:200])
    check("and nothing was: the part is still at its pose",
          (bpy.data.objects[PART].matrix_world.translation
           - PART_POSE.translation).length < 1e-12)
    for line in message.splitlines():
        if "sampled part vertices" in line or "would-be seat" in line:
            note(line.strip())

    # the same socket, unobstructed, seats: the refusal was the obstruction and
    # not the fixture being wrong.
    fresh(PEG_SPEC, obstructed=False)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    check("the identical seat into the unobstructed socket succeeds",
          reply.get("status") == "success", reply.get("message"))


def test_dry_run():
    section("dry_run — all the numbers, none of the movement")
    fresh(PEG_SPEC)
    before = bpy.data.objects[PART].matrix_world.copy()
    reply = call("seat_part", object=PART, base=SLAB, dry_run=True,
                 socket_hint_mm=hint_in(SOCKET_A_MOUTH, SOCKET_A_AXIS))
    if not check("dry_run succeeded", reply.get("status") == "success",
                 reply.get("message")):
        return
    report = result_of(reply)
    check("it reports applied=false", report.get("applied") is False)
    check("it still verified everything",
          report["verification"]["passed"] is True
          and len(report["verification"]["checks"]) >= 5,
          len(report["verification"]["checks"]))
    check("and the part did not move",
          (bpy.data.objects[PART].matrix_world.translation
           - before.translation).length < 1e-12)


def _seat_digest():
    """One whole seat from a fresh fixture, as a digest of its own numbers."""
    fresh(PEG_SPEC_KEYED)
    reply = call("seat_part", object=PART, base=SLAB,
                 socket_hint_mm=hint_in(SOCKET_B_MOUTH, SOCKET_B_AXIS))
    if reply.get("status") != "success":
        return None, reply.get("message")
    report = result_of(reply)
    measured = report["verification"]["measured"]
    socket = report["socket"]
    blob = "|".join([
        "%.9f" % value for value in
        (socket["axis_unit"] + socket["floor_center_mm"] + socket["mouth_center_mm"]
         + [socket["radius_mm"], socket["inscribed_radius_mm"], socket["depth_mm"],
            measured["tip_axis_offset_mm"], measured["axis_angle_deg"],
            measured["floor_gap_mm"], measured["shoulder_gap_mm"],
            report["seat"]["roll_deg"]]
         + report["seat"]["part_matrix_after_mm"])])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16], blob


def test_determinism():
    section("determinism — two runs, the same numbers")
    first, first_blob = _seat_digest()
    second, second_blob = _seat_digest()
    if not check("both runs seated", first is not None and second is not None,
                 first_blob if first is None else second_blob):
        return
    check("the measured seat is byte-identical run to run", first == second,
          (first, second))
    note("seat digest %s on both runs" % first)
    note("  (axis, floor, mouth, radii, depth, offsets, roll and the part's new "
         "matrix, to 9 decimals)")


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
    print("Forge add-on measured auto-seating headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)
    try:
        test_registration()
        test_measure_socket()
        test_rim_angle_hint()
        test_seat_straight()
        test_seat_tilted_and_keyed()
        test_roll_override()
        test_ledger_survives()
        test_measured_peg_fallback()
        test_refusals()
        test_sabotaged_socket()
        test_dry_run()
        test_determinism()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
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
