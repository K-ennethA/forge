"""Headless add-on tests for the human rigger's workflow (orient, symmetrize,
landmark, mirror, inspect).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_landmarks.py

Needs no geometry service and downloads nothing.

**What this suite is for.**  An audit of a live character measured three defects
that no human rigger could have produced with Blender's own tools: left/right
bone asymmetry of 6-24 mm on every limb, bones off the limb centreline, and —
worst — **side names mirrored**, ``DEF-shin.L`` at ``x = -191 mm`` while the
character's left leg centres at ``x = +182 mm``.  Automatic weights hid the
third one for months, because they bind by proximity.

So the character here is built to carry all three diseases on purpose:

* it **faces +Y**, which is backwards (Blender's convention is -Y);
* it is **15 mm asymmetric**, one leg shifted sideways;
* its leg tags are **swapped**, ``Leg.L`` painted on the geometry that is the
  character's right.

and every test below asks whether the pipeline noticed without being told.
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

PORT = 9909  # not 9876 (a live session), not 9878-9881, 9907, 9908
BODY = "Biped"

#: The deliberate mesh asymmetry, in metres.  15 mm: far above the 0.5 mm
#: symmetry tolerance, far below anything an artist would call a design.
ASYMMETRY_M = 0.015

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


# --- the synthetic biped ----------------------------------------------------
#
# Built out of primitives rather than sculpted, because every landmark this
# suite checks has to have a *known* right answer: the knee is at the ring where
# the tube is thinnest, and the test knows which ring that was.

SEGMENTS = 16


def _box(bm, low, high):
    verts = [bm.verts.new(Vector(p)) for p in (
        (low[0], low[1], low[2]), (high[0], low[1], low[2]),
        (high[0], high[1], low[2]), (low[0], high[1], low[2]),
        (low[0], low[1], high[2]), (high[0], low[1], high[2]),
        (high[0], high[1], high[2]), (low[0], high[1], high[2]))]
    for quad in ((0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
                 (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
        bm.faces.new([verts[i] for i in quad])


def _tube(bm, start, end, radii, segments=SEGMENTS):
    """A tube from ``start`` to ``end`` whose radius per ring is ``radii``.

    The radius profile is the whole point: a limb with a waist at the middle is
    a limb with a knee, and ``Limb.girth_minimum`` has to find that ring.
    """
    start = Vector(start)
    end = Vector(end)
    axis = (end - start)
    length = axis.length
    axis = axis / length
    up = Vector((0.0, 0.0, 1.0))
    if abs(axis.dot(up)) > 0.95:
        up = Vector((0.0, 1.0, 0.0))
    side = axis.cross(up).normalized()
    other = axis.cross(side).normalized()
    rings = []
    for index, radius in enumerate(radii):
        centre = start + axis * (length * index / float(len(radii) - 1))
        ring = []
        for step in range(segments):
            angle = 2.0 * math.pi * step / segments
            ring.append(bm.verts.new(centre + side * (radius * math.cos(angle))
                                     + other * (radius * math.sin(angle))))
        rings.append(ring)
    for index in range(len(rings) - 1):
        for step in range(segments):
            a = rings[index][step]
            b = rings[index][(step + 1) % segments]
            c = rings[index + 1][(step + 1) % segments]
            d = rings[index + 1][step]
            bm.faces.new((a, b, c, d))
    # caps
    for ring in (rings[0], rings[-1]):
        try:
            bm.faces.new(ring)
        except ValueError:
            pass


def _profile(count, dips, base=1.0):
    """Radii for a tube with a girth minimum at each ``(fraction, factor)`` dip.

    Two per limb: the crease in the middle (a knee, an elbow) and the narrow
    joint at the far end (an ankle, a wrist).  Both are what a real limb has and
    both are what the landmark pass is supposed to find — a limb with no ankle
    would leave ``girth_minimum`` picking whatever the distal band happened to
    contain, which is a test of nothing.
    """
    out = []
    for index in range(count):
        t = index / float(count - 1)
        radius = base
        for position, factor in dips:
            weight = math.exp(-((t - position) ** 2) / (2.0 * 0.07 ** 2))
            radius *= (1.0 - (1.0 - factor) * weight)
        out.append(radius)
    return out


#: Where each landmark really is, so the test can grade the measurement.  Built
#: facing +Y; the pipeline is expected to rotate the whole thing 180 degrees.
TRUTH = {
    "hip": Vector((0.09, 0.0, 0.84)),
    "knee": Vector((0.09, 0.0, 0.48)),        # the leg tube's waist: z 0.86 -> 0.10
    "ankle": Vector((0.09, 0.0, 0.176)),      # its second dip, at 90% of the tube
    "shoulder": Vector((0.19, 0.0, 1.28)),
    "elbow": Vector((0.375, 0.0, 1.07)),      # the arm tube's waist
    "wrist": Vector((0.53, 0.0, 0.894)),      # its second dip, at 92%
}


#: The default foot: ``(back, front, height, half_width, ball_fraction,
#: toe_half_width)``, in metres, along the **+Y** axis this character is built
#: facing.  One box when ``ball_fraction`` is ``None``; otherwise a wide midfoot
#: and a narrower toe box meeting at ``ball_fraction`` of the length, which is
#: what gives the sole a measurable **taper** — the thing
#: ``rigforge_landmarks.foot_landmarks`` finds the ball of the foot by.
PLAIN_FOOT = (-0.06, 0.19, 0.08, 0.05, None, None)


def _foot(bm, centre_x, boot=None):
    """A foot at ``centre_x``, reaching forward: the other half of "facing"."""
    back, front, height, half, ball, toe_half = boot or PLAIN_FOOT
    if ball is None:
        _box(bm, (centre_x - half, back, 0.0), (centre_x + half, front, height))
        return
    split = back + (front - back) * ball
    _box(bm, (centre_x - half, back, 0.0), (centre_x + half, split, height))
    _box(bm, (centre_x - toe_half, split, 0.0),
         (centre_x + toe_half, front, height * 0.75))


def build_biped(name=BODY, asymmetry=ASYMMETRY_M, boot=None):
    """Cylinders and boxes: a biped facing **+Y**, 15 mm out of symmetry.

    The character's left is the -X side while it faces +Y (up cross forward), so
    the deliberate asymmetry goes there: after the orientation gate rotates it
    onto the convention, that same limb is the ``+X`` one the landmark pass
    authors from.
    """
    bm = bmesh.new()
    parts = {}

    def record(tag, before):
        parts.setdefault(tag, []).extend(range(before, len(bm.faces)))

    before = len(bm.faces)
    _box(bm, (-0.16, -0.10, 0.80), (0.16, 0.11, 1.35))       # torso
    record("Torso", before)

    before = len(bm.faces)
    _box(bm, (-0.11, -0.09, 1.35), (0.11, 0.10, 1.60))       # head
    _box(bm, (-0.03, 0.10, 1.42), (0.03, 0.20, 1.50))        # nose: this is the front
    record("Head", before)

    for sign, side in ((1.0, "plus"), (-1.0, "minus")):
        # Exactly one limb is lopsided — the -X leg — which is the shape the live
        # defect had. Shifting *every* limb on one side would move the body's own
        # midplane with them, and then nothing is asymmetric at all: it is a
        # character standing to one side of the origin.
        shift = asymmetry if sign < 0 else 0.0
        before = len(bm.faces)
        _tube(bm,
              (sign * 0.19, 0.0, 1.28),
              (sign * 0.56, 0.0, 0.86),
              _profile(21, ((0.5, 0.55), (0.92, 0.6)), base=0.055))
        record("Arm.%s" % side, before)

        before = len(bm.faces)
        _tube(bm,
              (sign * (0.09 + shift), 0.0, 0.86),
              (sign * (0.09 + shift), 0.0, 0.10),
              _profile(25, ((0.5, 0.58), (0.9, 0.55)), base=0.075))
        # the foot: a box reaching forward, which is the other half of "facing"
        _foot(bm, sign * (0.09 + shift), boot)
        record("Leg.%s" % side, before)

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, parts


def tag_biped(obj, parts, swap_leg_names=True):
    """Tag it — with the **leg tags deliberately swapped**, which is the live bug.

    Facing +Y, the character's left is -X.  Painting ``Leg.L`` onto the +X
    geometry is therefore exactly the defect the audit found, and the pipeline
    has to notice it from the geometry alone.
    """
    # Facing +Y: character-left is -X ("minus").
    naming = {"Arm.minus": "Arm.L", "Arm.plus": "Arm.R",
              "Leg.minus": "Leg.L", "Leg.plus": "Leg.R"}
    if swap_leg_names:
        naming["Leg.minus"] = "Leg.R"
        naming["Leg.plus"] = "Leg.L"
    for part, faces in sorted(parts.items()):
        tag = naming.get(part, part)
        call("rigforge_tag", {"object": obj.name, "tag": tag, "faces": faces,
                              "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get",
                               "archetype": "biped"})


# --- tests: the orientation gate --------------------------------------------

def test_orientation_gate(obj):
    section("step 1: the orientation gate")
    report = call("rigforge_landmarks", {"object": obj.name, "action": "report"})
    orientation = report["orientation"]
    note("orientation: %s" % json.dumps(
        {k: orientation[k] for k in ("faces", "left_right_axis", "mirror_residual_mm",
                                     "other_axis_residual_mm", "toe_protrusion_mm",
                                     "nose_protrusion_mm", "confident")}))
    check("the gate measures the left/right axis as X (the mirror plane it has)",
          orientation["left_right_axis"] == "X", str(orientation["left_right_axis"]))
    check("it reads the facing off the toes and the nose, and says +Y",
          orientation["faces"] == "+Y", str(orientation["faces"]))
    check("it is confident (both evidence lines agree and the axis is decisive)",
          orientation["confident"] is True, orientation.get("why"))
    check("and it says that is NOT the convention",
          orientation["matches_convention"] is False, str(orientation))
    check("a report changes nothing: the mesh still faces +Y",
          call("rigforge_landmarks", {"object": obj.name})["orientation"]["faces"] == "+Y")
    check("the convention is quoted rather than assumed",
          report["convention"]["faces"] == "-Y"
          and report["convention"]["character_left"] == "+X",
          str(report["convention"]))
    return report


def test_orientation_refusal():
    section("step 1: a character with no front is refused, in a sentence")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, radius=0.5)
    mesh = bpy.data.meshes.new("Ball")
    bm.to_mesh(mesh)
    bm.free()
    ball = bpy.data.objects.new("Ball", mesh)
    bpy.context.scene.collection.objects.link(ball)
    bpy.context.view_layer.update()
    try:
        report = call("rigforge_landmarks", {"object": "Ball", "action": "report"})
        check("a sphere is not confident about which way it faces",
              report["orientation"]["confident"] is False,
              str(report["orientation"]["faces"]))
        check("and the report says why in words",
              len(report["orientation"].get("why") or "") > 40,
              report["orientation"].get("why"))
        # The default is to get on with it — a ball has no front and there is
        # nothing to fix — but it says so, loudly, because every side name below
        # rides on the assumption it just made.
        relaxed = call("rigforge_landmarks", {"object": "Ball", "action": "prepare"})
        check("by default it assumes the convention rather than blocking the artist",
              relaxed["orientation"].get("assumed") is True,
              str(relaxed["orientation"].get("action")))
        check("and warns that every side name rides on that assumption",
              any("assumed" in w for w in relaxed["warnings"]),
              str(relaxed["warnings"])[:200])

        reply = call("rigforge_landmarks", {"object": "Ball", "action": "prepare",
                                            "orient": "strict"}, expect_error=True)
        message = reply.get("message") or ""
        check("orient='strict' refuses it instead", reply.get("status") == "error",
              message[:120])
        check("the refusal is a sentence that says what to do about it",
              "Refusing to rig" in message and "Numpad 1" in message, message[:200])
        note(message[:220])
    finally:
        bpy.data.objects.remove(ball, do_unlink=True)


# --- tests: symmetry --------------------------------------------------------

def test_symmetry_and_tags(obj):
    section("step 2: symmetrize first, then re-derive the sides from the geometry")
    before = call("rigforge_landmarks", {"object": obj.name, "action": "report"})
    residual = before["symmetry"]["measured_mm"]
    note("asymmetry before: %.2f mm p95 (mean %.2f, worst %.2f mm)"
         % (residual, before["symmetry"]["mean_mm"], before["symmetry"]["max_mm"]))
    check("the 15 mm the builder put in is measured, not assumed",
          residual is not None and residual > 5.0, "%s mm" % residual)
    check("the statistic that decides is named in the report",
          "p95" in (before["symmetry"].get("statistic") or ""),
          str(before["symmetry"].get("statistic")))

    prepared = call("rigforge_landmarks", {"object": obj.name, "action": "prepare"})
    note("prepare says: %s" % prepared["says"])
    orientation = prepared["orientation"]
    check("the gate rotated it onto the convention",
          orientation["faces"] == "-Y" and orientation["matches_convention"] is True,
          str(orientation["faces"]))
    check("by a whole 180 degrees, not a fudge",
          abs(abs(orientation.get("rotated_deg") or 0.0) - 180.0) < 1e-6,
          str(orientation.get("rotated_deg")))
    after = prepared["symmetry"].get("after_mm")
    note("asymmetry after symmetrize: %s mm" % after)
    check("symmetrize removed it", after is not None and after <= 0.5, str(after))
    check("and the residual it removed is reported, not hidden",
          prepared["symmetry"].get("removed_mm") is not None
          and prepared["symmetry"]["removed_mm"] > 5.0,
          str(prepared["symmetry"].get("removed_mm")))
    check("the mesh survived symmetrizing (it still has geometry)",
          len(obj.data.vertices) > 100, str(len(obj.data.vertices)))

    tags = prepared["side_tags"]
    check("the swapped leg tags were caught from the geometry alone",
          "Leg" in (tags.get("swapped_tags") or []), str(tags.get("swapped_tags")))
    check("and the arm tags, which were right, were left alone",
          "Arm" not in (tags.get("swapped_tags") or []), str(tags.get("swapped_tags")))
    check("the swap is reported as a sentence an artist can act on",
          "convention" in (tags.get("says") or ""), (tags.get("says") or "")[:160])
    note(tags.get("says") or "")

    group = obj.vertex_groups.get("tag_Leg.L")
    matrix = obj.matrix_world
    xs = [(matrix @ v.co).x for v in obj.data.vertices
          for entry in v.groups if entry.group == group.index and entry.weight > 0.0]
    mean_x = sum(xs) / len(xs)
    note("tag_Leg.L now centres at x = %+.1f mm" % (mean_x * 1000.0))
    check("Leg.L now names the geometry on the character's left (+X)", mean_x > 0.0,
          "%.1f mm" % (mean_x * 1000.0))
    return prepared


# --- tests: landmarks -------------------------------------------------------

def test_landmarks(obj):
    section("step 3: landmarks are cross-section centroids, not fractions")
    report = call("rigforge_landmarks", {"object": obj.name, "action": "report"})
    points = report["landmarks"]
    check("the landmark pass authored the character's left only",
          all(not role.endswith(".R") for role in points), str(sorted(points)))
    for role in ("hip.L", "knee.L", "ankle.L", "shoulder.L", "elbow.L", "wrist.L",
                 "hips", "neck_base", "head_top"):
        check("it found %s" % role, role in points, str(sorted(points)))

    for role, key in (("knee.L", "knee"), ("elbow.L", "elbow"),
                      ("ankle.L", "ankle"), ("wrist.L", "wrist")):
        info = points[role]
        measured = Vector([v / 1000.0 for v in info["mm"]])
        # The builder put the crease at exactly half the limb's length, so the
        # height is the thing to grade: it is the same whichever side the mirror
        # authored from and whatever the 180 degree rotation did to X and Y.
        error = abs(measured.z - TRUTH[key].z) * 1000.0
        note("%s: %s, how=%s, %.1f mm from the ring the builder put there"
             % (role, [round(v, 1) for v in info["mm"]], info["how"], error))
        # A girth minimum, however it was reached. The wrist and the ankle have
        # their own end-of-limb rules now (the hand's first girth minimum, the
        # height where the leg stops being long front to back) because the plain
        # "narrowest station in the distal band" walked past the palm into the
        # fingers and sat the ankle high and behind a boot. What is being
        # asserted here is unchanged and is the point: it is a **measurement**,
        # never a fraction of the limb somebody typed.
        how = str(info["how"])
        check("%s is a girth minimum, measured, not a fraction" % role,
              ("girth minimum" in how or "minimum girth" in how
               or "shortest front to back" in how) and "fallback" not in how,
              str(info))
        check("%s lands on the crease the builder made (within 30 mm)" % role,
              error < 30.0, "%.1f mm" % error)

    midplane = report["midplane_mm"]
    note("the measured midplane is x = %+.2f mm" % midplane)
    for role in ("hips", "spine_01", "spine_02", "spine_03", "neck_base", "neck_top",
                 "head_top"):
        if role not in points:
            continue
        x = points[role]["mm"][0]
        check("%s sits on the midplane by construction" % role,
              abs(x - midplane) < 0.01, "%.3f mm vs %.3f mm" % (x, midplane))

    # The centreline test: the knee landmark must be on the limb's own axis, not
    # merely at the right height. This is defect (2) from the audit.
    #
    # With one deliberate exception, which is the other half of a correct rig:
    # the knee carries the **anatomical pre-bend** and therefore sits a little
    # way forward of the centreline on purpose (rigforge_landmarks.prebend_joint
    # — a straight limb is ambiguous to IK and folds whichever way it falls).
    # So the offset is split: across the facing axis it must still be zero, and
    # along it, it must be exactly the pre-bend.  That is a stricter test than
    # the one it replaces, not a weaker one.
    knee = Vector([v / 1000.0 for v in points["knee.L"]["mm"]])
    matrix = obj.matrix_world
    group = obj.vertex_groups.get("tag_Leg.L")
    band = [matrix @ v.co for v in obj.data.vertices
            for entry in v.groups
            if entry.group == group.index and entry.weight > 0.0
            and abs((matrix @ v.co).z - knee.z) < 0.02]
    centre = sum(band, Vector()) / len(band)
    delta = Vector((knee.x, knee.y, 0.0)) - Vector((centre.x, centre.y, 0.0))
    prebend = {entry["joint"]: entry for entry in (report.get("prebend") or [])}
    knee_prebend = prebend.get("knee.L") or {}
    stance = {entry["joint"]: entry for entry in (report.get("stance") or [])}
    knee_stance = stance.get("knee.L") or {}
    # The character has been oriented to face -Y by this point, so "forward" is
    # -Y and "sideways" is X.
    note("knee landmark is %.1f mm sideways and %+.1f mm forward of the leg's own "
         "cross-section centroid at that height; the pre-bend asked for %+.1f mm and "
         "the flexed stance took it to %+.1f mm"
         % (abs(delta.x) * 1000.0, -delta.y * 1000.0,
            knee_prebend.get("nudge_mm") or 0.0,
            knee_stance.get("knee_offline_mm") or 0.0))
    check("the knee landmark is ON the limb centreline ACROSS the facing axis",
          abs(delta.x) < 0.005, "%.2f mm sideways" % (abs(delta.x) * 1000.0))
    # Two rules put the knee forward and NOTHING else does: the pre-bend gives
    # IK its plane, and the flexed stance (which subsumes it) gives the chain
    # its reach headroom. The stance's own report says where it left the joint,
    # so this is measured against that number rather than against a constant.
    # 5 mm of slack because the two are not the same measurement: the stance
    # measures perpendicular to the hip-to-ankle chord, this measures the Y gap
    # to the cross-section centroid in a 40 mm slab at the knee's own height,
    # and the chord is a couple of degrees off vertical (the hip sits inside
    # the pelvis, the ankle on the leg's own axis).
    check("and the only things that moved it off are the pre-bend and the stance, "
          "forwards",
          -delta.y > 0.0 and abs(-delta.y * 1000.0
                                 - (knee_stance.get("knee_offline_mm") or 0.0)) < 5.0,
          "%+.2f mm forward vs a %+.2f mm stance offset (pre-bend alone was %+.2f mm)"
          % (-delta.y * 1000.0, knee_stance.get("knee_offline_mm") or 0.0,
             knee_prebend.get("nudge_mm") or 0.0))
    check("...and the stance is the bigger of the two, because a pre-bend is a plane "
          "and a stance is a pose",
          (knee_stance.get("knee_offline_mm") or 0.0)
          > (knee_prebend.get("nudge_mm") or 0.0),
          "%s vs %s" % (knee_stance.get("knee_offline_mm"),
                        knee_prebend.get("nudge_mm")))
    return report


# --- tests: the metarig fit and the mirror ----------------------------------

def test_metarig(obj, workspace):
    section("steps 3-4 in the default path: rigforge_metarig fits landmarks and mirrors")
    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo_dir": os.path.join(workspace, "echo")})
    note("metarig says: %s" % result["says"])
    check("the landmark workflow is the DEFAULT path (nothing was passed to ask for it)",
          result["fit_method"] == "landmarks", str(result["fit_method"]))
    check("it reports the orientation it found",
          (result.get("orientation") or {}).get("faces") == "-Y",
          str((result.get("orientation") or {}).get("faces")))
    check("it reports the mesh symmetry it measured",
          (result.get("symmetry") or {}).get("mean_mm") is not None,
          str(result.get("symmetry")))

    mirror = result.get("mirror") or {}
    note("mirror: %d bone(s), residual %.6f mm"
         % (mirror.get("mirrored", 0), mirror.get("residual_asymmetry_mm", -1)))
    check("every sided bone was mirrored from the left",
          mirror.get("mirrored", 0) >= 8, str(mirror.get("mirrored")))
    check("the asymmetry the mirror leaves is EXACTLY 0.0 (the human number)",
          mirror.get("residual_asymmetry_mm") == 0.0,
          str(mirror.get("residual_asymmetry_mm")))
    check("the centre chain was snapped onto the midplane",
          isinstance(mirror.get("centred_bones"), list), str(mirror.get("centred_bones")))

    meta = bpy.data.objects.get(result["metarig"])
    check("the metarig exists", meta is not None, result["metarig"])

    # The bone-level version of the same claim, measured off the armature.
    plane = (result.get("midplane_mm") or 0.0) / 1000.0
    worst = 0.0
    pairs = 0
    for bone in meta.data.bones:
        if not bone.name.endswith(".L"):
            continue
        twin = meta.data.bones.get(bone.name[:-2] + ".R")
        if twin is None:
            continue
        pairs += 1
        for left, right in ((bone.head_local, twin.head_local),
                            (bone.tail_local, twin.tail_local)):
            mirrored = Vector((2.0 * plane - left.x, left.y, left.z))
            worst = max(worst, (mirrored - right).length)
    note("%d L/R pairs, worst asymmetry %.6f mm" % (pairs, worst * 1000.0))
    check("every L/R pair on the armature is an exact mirror (< 1 micron)",
          worst * 1000.0 < 0.001, "%.6f mm" % (worst * 1000.0))

    # Defect (3), pinned: the .L bones must be on the character's left.
    thigh = meta.data.bones.get("thigh.L")
    upper = meta.data.bones.get("upper_arm.L")
    note("thigh.L head x = %+.1f mm, upper_arm.L head x = %+.1f mm"
         % (thigh.head_local.x * 1000.0, upper.head_local.x * 1000.0))
    check("thigh.L is on +X — the character's left, given it faces -Y",
          thigh.head_local.x > 0.0, "%.1f mm" % (thigh.head_local.x * 1000.0))
    check("upper_arm.L is too", upper.head_local.x > 0.0,
          "%.1f mm" % (upper.head_local.x * 1000.0))
    # The tags were already corrected by the earlier prepare pass, so what is
    # pinned here is that they *stayed* correct: Leg.L on the +X geometry, and a
    # thigh.L bone fitted to it.
    group = obj.vertex_groups.get("tag_Leg.L")
    matrix = obj.matrix_world
    xs = [(matrix @ v.co).x for v in obj.data.vertices
          for entry in v.groups if entry.group == group.index and entry.weight > 0.0]
    check("thigh.L was fitted to the geometry Leg.L actually covers (+X)",
          (sum(xs) / len(xs)) > 0.0 and thigh.head_local.x > 0.0,
          "tag at %+.1f mm, bone at %+.1f mm"
          % (sum(xs) / len(xs) * 1000.0, thigh.head_local.x * 1000.0))

    # The knee bone, not just the landmark.
    shin = meta.data.bones.get("shin.L")
    check("the knee joint is where the crease is (the shin's head)",
          abs(shin.head_local.z - TRUTH["knee"].z) < 0.05,
          "%.1f mm vs %.1f mm" % (shin.head_local.z * 1000.0, TRUTH["knee"].z * 1000.0))

    echo = result.get("skeleton_echo") or {}
    images = echo.get("images") or []
    check("the skeleton echo was rendered before anything was skinned",
          len(images) == 2, str(echo.get("says"))[:160])
    for entry in images:
        check("the %s echo is a real PNG on disk" % entry["view"],
              os.path.exists(entry["path"]) and entry["bytes"] > 2000,
              "%s (%s bytes)" % (entry["path"], entry.get("bytes")))
    note("echo: %s" % ", ".join(entry["path"] for entry in images))
    return result, meta


def test_landmark_fallback(workspace):
    section("the fallback: a mesh with no landmarks falls back and SAYS so")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    mesh = bpy.data.meshes.new("Slab")
    bm.to_mesh(mesh)
    bm.free()
    slab = bpy.data.objects.new("Slab", mesh)
    bpy.context.scene.collection.objects.link(slab)
    bpy.context.view_layer.update()
    try:
        faces = [p.index for p in slab.data.polygons]
        call("rigforge_tag", {"object": "Slab", "tag": "Torso", "faces": faces,
                              "replace": True})
        reply = call("rigforge_metarig", {"object": "Slab", "archetype": "biped",
                                          "orient": "skip", "symmetry": False,
                                          "echo": False}, expect_error=True)
        if reply.get("status") == "error":
            check("a cube with one tag is refused with a sentence",
                  len(reply.get("message") or "") > 40, (reply.get("message") or "")[:160])
            note((reply.get("message") or "")[:200])
        else:
            result = reply.get("result") or reply
            check("it fell back to the tag fit", result.get("fit_method") == "tags",
                  str(result.get("fit_method")))
            check("and said why, in the warnings",
                  any("landmark" in w.lower() for w in result.get("warnings") or []),
                  str(result.get("warnings"))[:200])
    finally:
        for name in ("Slab", "Slab_metarig"):
            found = bpy.data.objects.get(name)
            if found is not None:
                bpy.data.objects.remove(found, do_unlink=True)


# --- tests: the self-checks in rig_check ------------------------------------

def generate(obj, meta):
    section("generate the rig (so there are weights to inspect)")
    result = call("rigforge_generate_rig", {"metarig": meta.name, "mesh": obj.name})
    rig = bpy.data.objects.get(result["rig"])
    check("the rig generated", rig is not None, str(result.get("rig")))
    note("%d bones, %d deform" % (result["bone_count"], result["deform_bones"]))
    return rig, result


def test_the_landmark_rig_still_ik_solves(rig):
    section("a landmark-fitted rig is still a rig: the IK pole steers the knee")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": True})
    for bone in rig.pose.bones:
        bone.matrix_basis.identity()
    bpy.context.view_layer.update()

    # The landmark fit places the knee on the crease, which on a straight limb
    # is dead straight — and a limb with no bend at rest gives Rigify no pole
    # plane to derive an angle from. That is why the fit keeps a 3% nudge, and
    # this is the check that proves the nudge is still doing its job.
    lift = 0.12 * max(rig.dimensions)
    rig.pose.bones["foot_ik.L"].location = (0.0, 0.0, lift)
    bpy.context.view_layer.update()
    bent = (rig.matrix_world @ rig.pose.bones["DEF-shin.L"].head).copy()
    reach = 0.25 * max(rig.dimensions)
    pole = rig.pose.bones["thigh_ik_target.L"]
    worst = 0.0
    for offset in ((reach, 0, 0), (-reach, 0, 0), (0, reach, 0), (0, -reach, 0)):
        pole.location = offset
        bpy.context.view_layer.update()
        worst = max(worst, ((rig.matrix_world @ rig.pose.bones["DEF-shin.L"].head)
                            - bent).length * 1000.0)
    pole.location = (0.0, 0.0, 0.0)
    for bone in rig.pose.bones:
        bone.matrix_basis.identity()
    bpy.context.view_layer.update()
    note("the pole sweep moves the knee %.1f mm" % worst)
    check("the knee follows its pole target on a landmark-fitted leg", worst > 5.0,
          "%.2f mm" % worst)


def test_placement_gates(rig, mesh):
    section("rig_check reports the three placement gates on EVERY run")
    result = call("rig_check", {"rig": rig.name, "mesh": mesh.name,
                                "poses": [45.0], "intersections": False})
    for block in ("centering", "asymmetry", "side_naming", "overlap"):
        check("rig_check reports %s without being asked" % block,
              isinstance(result.get(block), dict), str(type(result.get(block))))

    asymmetry = result["asymmetry"]
    note("asymmetry: %s" % asymmetry["says"])
    # Microns, not millimetres: the metarig's mirror is exact, and what is left
    # here is Rigify's own float noise carrying it into the generated rig.
    check("the mirrored rig measures 0.0 asymmetry, the number a human gets",
          (asymmetry.get("worst_asymmetry_mm") or 0.0) <= 0.01,
          str(asymmetry.get("worst_asymmetry_mm")))
    check("and it is a pass", asymmetry["verdict"] == "ok", str(asymmetry["verdict"]))
    check("the table names the pairs it measured",
          len(asymmetry.get("pairs") or []) >= 8, str(len(asymmetry.get("pairs") or [])))

    naming = result["side_naming"]
    note("side naming: %s" % naming["says"])
    check("the side-naming gate reads the facing off the MESH",
          naming.get("faces") == "-Y", str(naming.get("faces")))
    check("and passes: .L is on the character's left",
          naming["verdict"] == "ok", str(naming.get("wrong")))

    centering = result["centering"]
    note("centering: %s" % centering["says"])
    check("centering measured the limb bones",
          centering.get("measured", 0) >= 4, str(centering.get("measured")))
    check("every bone reports millimetres AND a percentage of its own section radius",
          all(row.get("head_offset_mm") is not None
              and row.get("worst_offset_pct_of_radius") is not None
              for row in centering["bones"]),
          str(centering["bones"][:1]))
    # The rig rests on a flexed knee (rigforge_landmarks.stance_flex), and the
    # mesh was sculpted standing straight, so a correct thigh bone really does
    # sit well off the middle of the flesh at that height. The gate reports that
    # raw offset and judges what is LEFT once the chain's own rest flex is paid
    # for, so both numbers have to be there and the verdict has to follow the
    # second one.
    check("every bone also reports the rest flex its chain carries, and the excess",
          all(row.get("rest_flex_mm") is not None
              and row.get("worst_excess_mm") is not None
              and row.get("worst_excess_pct_of_radius") is not None
              for row in centering["bones"]),
          str({k: v for k, v in centering["bones"][0].items() if k != "stations"}))
    worst = next((row for row in centering["bones"] if row["gated"]), None)
    note("worst gated bone %s: %.1f mm off raw (%.0f%% of radius), %.1f mm of that is "
         "the stance, %.1f mm excess (%.0f%%)"
         % (worst["bone"], worst["worst_offset_mm"],
            worst["worst_offset_pct_of_radius"] or 0.0, worst["rest_flex_mm"] or 0.0,
            worst["worst_excess_mm"] or 0.0,
            worst["worst_excess_pct_of_radius"] or 0.0))
    check("the raw offset is bigger than the excess -- the stance is real and it is "
          "being accounted for, not ignored",
          (worst["worst_offset_mm"] or 0.0) > (worst["worst_excess_mm"] or 0.0),
          "%s vs %s" % (worst["worst_offset_mm"], worst["worst_excess_mm"]))
    check("the landmark-fitted rig is centred in its limbs",
          centering["verdict"] == "ok", centering["says"])
    check("the gate judges the long bones and says which ones",
          all("thigh" in name or "shin" in name or "arm" in name
              for name in centering["gated_bones"]),
          str(centering["gated_bones"]))
    check("but the bones it does not gate are still measured and reported",
          any(not row["gated"] for row in centering["bones"]),
          str([row["bone"] for row in centering["bones"]][:8]))
    check("every station says how much of the section it actually found",
          all(station.get("section_gap_deg") is not None
              for row in centering["bones"] for station in row["stations"]),
          str(centering["bones"][0]["stations"][:1]))

    overlap = result["overlap"]
    note("overlap: %s" % overlap["says"])
    check("the overlap matrix has rows", len(overlap.get("pairs") or []) > 4,
          str(overlap.get("pairs_total")))
    check("touching bones overlap (that is what a blend band IS)",
          any(row["kind"] == "blend band" for row in overlap["pairs"]),
          str(overlap["pairs"][:2]))
    check("every row says how far apart the two bones really are",
          all(row.get("gap_mm") is not None for row in overlap["pairs"]),
          str(overlap["pairs"][:1]))
    check("the heaviest overlaps are blend bands, not strays",
          overlap["pairs"][0]["kind"] == "blend band", str(overlap["pairs"][0]))
    check("the gate is the worst of the poses and the placements",
          result["gate"] in ("pass", "attention", "fail"), str(result["gate"]))
    check("and a clean rig's sentence quotes the asymmetry number worth quoting",
          "Placement is clean" in result["says"]
          and "left/right asymmetry" in result["says"], result["says"][:200])
    note(result["says"][:220])
    return result


def test_side_swap_screams(rig, mesh):
    section("the live bug, pinned: a mirrored side name must scream")
    from forge.tools import rigforge_landmarks

    good = rigforge_landmarks.side_naming(rig, mesh)
    check("the rig starts clean", good["verdict"] == "ok", good["says"])

    # Swap the two thighs: exactly the defect measured on the live character,
    # where DEF-shin.L sat at -191 mm and the left leg's flesh at +182 mm.
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    saved = {}
    try:
        for name in ("DEF-thigh.L", "DEF-shin.L", "DEF-thigh.R", "DEF-shin.R"):
            bone = rig.data.edit_bones.get(name)
            if bone is None:
                continue
            saved[name] = (bone.head.copy(), bone.tail.copy())
        for name, (head, tail) in saved.items():
            bone = rig.data.edit_bones[name]
            bone.head = Vector((-head.x, head.y, head.z))
            bone.tail = Vector((-tail.x, tail.y, tail.z))
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    broken = rigforge_landmarks.side_naming(rig, mesh)
    note("after the swap: %s" % broken["says"])
    check("the gate fails", broken["verdict"] == "fail", str(broken["verdict"]))
    check("it names the bones that are on the wrong half",
          any("thigh.L" in name for name in broken["wrong"]), str(broken["wrong"]))
    check("and quotes the distance in millimetres, not a shrug",
          broken["worst_distance_mm"] > 50.0, str(broken["worst_distance_mm"]))
    check("the sentence says why automatic weights hid it",
          "proximity" in broken["says"], broken["says"][:200])

    asym = rigforge_landmarks.bone_asymmetry(rig)
    check("the asymmetry table is unmoved by a pure swap (both sides moved)",
          asym["verdict"] in ("ok", "attention", "fail"), str(asym["verdict"]))

    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        for name, (head, tail) in saved.items():
            bone = rig.data.edit_bones[name]
            bone.head = head
            bone.tail = tail
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    restored = rigforge_landmarks.side_naming(rig, mesh)
    check("and putting them back clears the gate", restored["verdict"] == "ok",
          restored["says"][:160])


def test_asymmetry_screams(rig):
    section("the other live number: 6-24 mm of L/R asymmetry must fail")
    from forge.tools import rigforge_landmarks

    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = rig.data.edit_bones.get("DEF-foot.R")
    saved = None
    try:
        if bone is not None:
            saved = (bone.head.copy(), bone.tail.copy())
            # 23.5 mm: the number the live DEF-foot measured.
            bone.head = bone.head + Vector((0.0, 0.0235, 0.0))
            bone.tail = bone.tail + Vector((0.0, 0.0235, 0.0))
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    if saved is None:
        check("the rig has a DEF-foot.R to break", False)
        return
    broken = rigforge_landmarks.bone_asymmetry(rig)
    note("after a 23.5 mm nudge: %s" % broken["says"])
    check("the asymmetry gate fails", broken["verdict"] == "fail", str(broken["verdict"]))
    check("it names the bone", broken["worst"] and "foot" in broken["worst"],
          str(broken["worst"]))
    check("and measures it, in millimetres, to the tenth",
          abs(broken["worst_asymmetry_mm"] - 23.5) < 0.5,
          str(broken["worst_asymmetry_mm"]))
    check("the sentence quotes what a mirrored rig would have measured",
          "0.0" in broken["says"], broken["says"][:160])

    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bone = rig.data.edit_bones["DEF-foot.R"]
        bone.head, bone.tail = saved
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    check("and restoring it clears the gate",
          rigforge_landmarks.bone_asymmetry(rig)["verdict"] == "ok")


def test_centering_screams(rig, mesh):
    section("a bone dragged off the limb's centreline must be caught")
    from forge.tools import rigforge_landmarks

    before = rigforge_landmarks.bone_centering(rig, mesh)
    rows = {row["bone"]: row for row in before["bones"]}
    target = "DEF-shin.L" if "DEF-shin.L" in rows else (
        sorted(rows)[0] if rows else None)
    if target is None:
        check("there is a deform bone to move", False)
        return
    radius = max(rows[target]["stations"][0]["radius_mm"], 1.0) / 1000.0
    note("%s starts %.1f mm off centre (%.0f%% of a %.1f mm radius)"
         % (target, rows[target]["worst_offset_mm"],
            rows[target]["worst_offset_pct_of_radius"], radius * 1000.0))

    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    saved = None
    try:
        bone = rig.data.edit_bones[target]
        saved = (bone.head.copy(), bone.tail.copy())
        # Sideways: the limb is round, so X is as off-centre as Y, and it does
        # not fight the small forward offset the IK plane wants.
        push = Vector((radius * 0.9, 0.0, 0.0))
        bone.head = bone.head + push
        bone.tail = bone.tail + push
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    after = rigforge_landmarks.bone_centering(rig, mesh)
    moved = {row["bone"]: row for row in after["bones"]}.get(target) or {}
    note("after the push: %.1f mm (%.0f%% of radius), verdict %s"
         % (moved.get("worst_offset_mm", -1),
            moved.get("worst_offset_pct_of_radius", -1), moved.get("verdict")))
    check("the centring gate sees the bone leave the centreline",
          (moved.get("worst_offset_pct_of_radius") or 0.0)
          > (rows[target]["worst_offset_pct_of_radius"] or 0.0) + 20.0,
          "%s -> %s" % (rows[target]["worst_offset_pct_of_radius"],
                        moved.get("worst_offset_pct_of_radius")))
    check("and calls it out", moved.get("verdict") in ("attention", "fail"),
          str(moved.get("verdict")))

    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bone = rig.data.edit_bones[target]
        bone.head, bone.tail = saved
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def test_overlap_flags_pollution(rig, mesh):
    section("the overlap matrix: a thigh bone touching a hand vertex is a defect")
    from forge.tools import rigforge_landmarks

    clean = rigforge_landmarks.influence_overlap(rig, mesh)
    before = {tuple(sorted(entry["bones"])) for entry in clean["outliers"]}
    note("the untouched rig has %d stray pair(s), %.2f of stray mass"
         % (len(before), clean["stray_mass"]))

    # Find a vertex the hand owns and hand a slice of it to the thigh.
    hand = None
    for candidate in ("DEF-hand.L", "DEF-forearm.L", "DEF-foot.L"):
        if mesh.vertex_groups.get(candidate) is not None:
            hand = candidate
            break
    thigh = "DEF-thigh.R" if mesh.vertex_groups.get("DEF-thigh.R") else "DEF-thigh.L"
    check("there is a far-away pair to pollute", hand is not None and thigh is not None,
          "%s / %s" % (hand, thigh))
    if hand is None:
        return
    index = mesh.vertex_groups[hand].index
    victims = [v.index for v in mesh.data.vertices
               for entry in v.groups
               if entry.group == index and entry.weight > 0.5][:6]
    check("the %s group has vertices to borrow" % hand, bool(victims), str(len(victims)))
    mesh.vertex_groups[thigh].add(victims, 0.45, "REPLACE")

    dirty = rigforge_landmarks.influence_overlap(rig, mesh)
    note("polluted: %s" % dirty["says"])
    wanted = tuple(sorted([hand, thigh]))
    found = [entry for entry in dirty["outliers"]
             if tuple(sorted(entry["bones"])) == wanted]
    check("the matrix flags the pair that was polluted, by name", bool(found),
          str([entry["bones"] for entry in dirty["outliers"]]))
    check("which was not flagged before", wanted not in before, str(sorted(before)))
    if found:
        worst = found[0]
        check("it measures how far apart the two bones actually are",
              (worst.get("gap_mm") or 0.0) > 100.0, str(worst.get("gap_mm")))
        check("and how far the worst vertex is from the bone that should not have it",
              any(key.startswith("distance_to_") and (worst[key] or 0) > 100.0
                  for key in worst), str(worst))
        check("the sentence is readable by a person",
              "moved by both" in worst["says"], worst["says"][:160])
        note(worst["says"])
    check("the stray mass went up", dirty["stray_mass"] > clean["stray_mass"],
          "%s -> %s" % (clean["stray_mass"], dirty["stray_mass"]))

    mesh.vertex_groups[thigh].remove(victims)
    cleaned = rigforge_landmarks.influence_overlap(rig, mesh)
    check("removing the pollution clears that pair",
          not any(tuple(sorted(entry["bones"])) == wanted
                  for entry in cleaned["outliers"]),
          str([entry["bones"] for entry in cleaned["outliers"]]))


def test_weight_report_carries_the_matrix(rig, mesh):
    section("the weight report answers 'what does one movement do to another'")
    result = call("rigforge_weights", {"object": mesh.name, "rig": rig.name,
                                       "action": "report"})
    report = result["report"]
    check("it still counts influences per bone", bool(report.get("bones")),
          str(len(report.get("bones") or {})))
    overlap = report.get("overlap") or {}
    check("and it carries the overlap matrix", bool(overlap.get("pairs")),
          str(overlap.get("says"))[:120])
    check("whose rows name two bones, a mass and the gap between them",
          all(len(row["bones"]) == 2 and row.get("mass") is not None
              and row.get("gap_mm") is not None for row in overlap["pairs"]),
          str(overlap["pairs"][:1]))
    note(overlap.get("says") or "")


def test_weight_maps(rig, mesh, workspace):
    section("step 5: the maps are LOOKED at — per-bone renders")
    directory = os.path.join(workspace, "weights")
    result = call("rigforge_weight_maps", {"rig": rig.name, "mesh": mesh.name,
                                           "dir": directory, "max_bones": 3,
                                           "resolution": 256})
    note("weight maps: %s" % result["says"])
    check("it rendered a map per bone", len(result["images"]) == 3,
          str(len(result["images"])))
    for entry in result["images"]:
        check("%s's map is a real PNG" % entry["bone"],
              os.path.exists(entry["path"]) and entry["bytes"] > 1000,
              "%s (%s bytes)" % (entry["path"], entry.get("bytes")))
    check("the ramp is named so the picture can be read",
          "blue" in result["ramp"] and "red" in result["ramp"], result["ramp"])
    check("the overlap matrix rides along with the pictures",
          isinstance(result.get("overlap"), dict), str(type(result.get("overlap"))))
    check("and the mesh is left exactly as it was found (no colour attribute)",
          mesh.data.color_attributes.get("forge_weight_view") is None,
          str([a.name for a in mesh.data.color_attributes]))

    reply = call("rigforge_weight_maps", {"rig": rig.name, "mesh": mesh.name,
                                          "dir": directory, "bones": ["no-such-bone"]},
                 expect_error=True)
    check("an unknown bone is refused with the bones it does have",
          reply.get("status") == "error" and "DEF-" in (reply.get("message") or ""),
          (reply.get("message") or "")[:160])


def test_echo_command(rig, mesh, workspace):
    section("the echo-back on demand")
    directory = os.path.join(workspace, "echo2")
    result = call("rigforge_echo_skeleton", {"rig": rig.name, "mesh": mesh.name,
                                             "dir": directory, "resolution": 256})
    check("it drew both views", len(result["images"]) == 2, str(result["images"]))
    check("it drew the deform bones", result["bones_drawn"] > 8,
          str(result["bones_drawn"]))
    for entry in result["images"]:
        check("%s exists" % entry["view"], os.path.exists(entry["path"]),
              entry["path"])
    check("the body is ghosted, not hidden", result["ghost_alpha"] == 0.22,
          str(result["ghost_alpha"]))
    check("nothing was left in the scene",
          bpy.data.objects.get("Forge Skeleton Echo") is None)

    reply = call("rigforge_echo_skeleton", {"rig": rig.name, "mesh": mesh.name},
                 expect_error=True)
    check("a missing folder is refused in a sentence",
          reply.get("status") == "error" and "dir" in (reply.get("message") or ""),
          (reply.get("message") or "")[:120])


def test_asymmetric_character_is_allowed(workspace):
    section("symmetry=false: the artist's asymmetric character is legitimate")
    obj, parts = build_biped("Lopsided", asymmetry=0.06)
    try:
        tag_biped(obj, parts, swap_leg_names=False)
        result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                           "symmetry": False, "echo": False})
        note("says: %s" % result["says"])
        check("it rigs rather than refusing", bool(result.get("metarig")),
              str(result.get("metarig")))
        check("it did NOT mirror (each side came from its own geometry)",
              result.get("mirror") is None, str(result.get("mirror")))
        check("and it warns that this was the artist's call, not an accident",
              any("symmetry=false" in w for w in result.get("warnings") or []),
              str(result.get("warnings"))[:200])
        meta = bpy.data.objects.get(result["metarig"])
        left = meta.data.bones.get("thigh.L").head_local.x
        right = meta.data.bones.get("thigh.R").head_local.x
        note("thigh heads: %+.1f mm / %+.1f mm" % (left * 1000.0, right * 1000.0))
        check("the two sides differ, because the mesh does",
              abs(abs(left) - abs(right)) * 1000.0 > 5.0,
              "%.1f mm" % (abs(abs(left) - abs(right)) * 1000.0))
    finally:
        for name in ("Lopsided", "Lopsided_metarig"):
            found = bpy.data.objects.get(name)
            if found is not None:
                bpy.data.objects.remove(found, do_unlink=True)


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
    print("Forge add-on landmark-rigging headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_landmarks_test_")
    try:
        section("the character: a biped facing the wrong way, lopsided, mis-tagged")
        obj, parts = build_biped()
        tag_biped(obj, parts)
        note("%d vertices, %d faces" % (len(obj.data.vertices), len(obj.data.polygons)))

        test_orientation_gate(obj)
        test_orientation_refusal()
        test_symmetry_and_tags(obj)
        test_landmarks(obj)
        result, meta = test_metarig(obj, workspace)
        test_landmark_fallback(workspace)

        rig, _generated = generate(obj, meta)
        test_the_landmark_rig_still_ik_solves(rig)
        test_placement_gates(rig, obj)
        test_side_swap_screams(rig, obj)
        test_asymmetry_screams(rig)
        test_centering_screams(rig, obj)
        test_overlap_flags_pollution(rig, obj)
        test_weight_report_carries_the_matrix(rig, obj)
        test_weight_maps(rig, obj, workspace)
        test_echo_command(rig, obj, workspace)
        test_asymmetric_character_is_allowed(workspace)
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
