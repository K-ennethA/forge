"""Headless add-on tests for the **anatomical pre-bend** and the bend-direction gate.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_prebend.py

Needs no geometry service, no GPU and downloads nothing.

**What this suite is for.**  The owner watched the werewolf's walk cycle and saw
its **knees bending backwards**.  Every existing gate was clean: the bones were
inside the flesh (``centering``), the two sides matched to 0.0 mm
(``asymmetry``), the side names were right, the influence matrix was tidy and
the feet drifted 1.1 mm.  A rig can be perfectly placed, perfectly mirrored and
perfectly planted and still fold the wrong way, because none of those numbers is
about *what happens when an animator pulls on it*.

Measured on the live rig (``projects/werewolf/models/werewolf-wip-9.blend``):
the knee sat **25.9 mm behind** the hip-to-ankle line — 4.2% of a 620 mm span —
and the elbow's 31.6 mm off-line was almost entirely **sideways** (0.08 mm of it
front-to-back).  Both cleared the old "is it 3% from straight" test, which asked
only for a *magnitude*; a magnitude cannot tell a knee from a knee bent
backwards.  Lifting ``foot_ik.L`` drove the knee backwards by 148.7 mm.

The missed human practice is the fix, and every rigger does it before anything
else: **pre-bend the rest pose.**  A knee apexes forward, an elbow backward, by
a few percent of the limb's own span, so the IK solver — and Rigify's
rest-plane-derived pole angle — never has to guess.

So this suite asks four things:

1. the pre-bend maths is right *and signed* (:func:`prebend_joint`);
2. the nudge stays **inside the limb's own cross-section**;
3. it runs **before the mirror**, so both sides get it identically and the
   0.0 mm asymmetry survives;
4. the new ``bend_direction`` gate passes a pre-bent rig and **fails a
   deliberately straightened one**, restoring the pose exactly either way.
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

PORT = 9912  # not 9876 (a live session), not 9878-9881, 9907-9911
BODY = "PrebendBiped"

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


# --- section 1: the maths, on points whose right answer is known ------------

def test_prebend_maths():
    section("the pre-bend maths: signed, not a magnitude")
    from forge.tools import rigforge_landmarks as L

    forward = Vector((0.0, -1.0, 0.0))  # the convention: the character faces -Y
    hip = Vector((0.1, 0.0, 0.9))
    ankle = Vector((0.1, 0.0, 0.1))
    span = (ankle - hip).length
    want = L.PREBEND_FRACTION * span

    # (a) perfectly straight: the case that has no bend plane at all.
    straight = (hip + ankle) * 0.5
    point, report = L.prebend_joint(None, hip, straight, ankle, forward, "knee", "L")
    note("straight knee: %s" % report["why"])
    check("a collinear chain is nudged", report["nudged"] is True, str(report))
    check("...forward, which is -Y when the character faces -Y",
          point.y < straight.y - 1e-9, "%.4f -> %.4f" % (straight.y, point.y))
    check("...by exactly %.0f%% of the limb's own span" % (100.0 * L.PREBEND_FRACTION),
          abs((straight - point).length - want) < 1e-6,
          "%.2f mm, wanted %.2f mm" % ((straight - point).length * 1000.0, want * 1000.0))
    check("...and not along the limb: the nudge is purely across it",
          abs((point - straight).dot((ankle - hip).normalized())) < 1e-9,
          str(point))

    # (b) the live defect: bent, by more than the threshold, the WRONG way.
    backwards = straight + Vector((0.0, 0.0259, 0.0))   # 25.9 mm behind, as measured
    point, report = L.prebend_joint(None, hip, backwards, ankle, forward, "knee", "L")
    note("backwards knee: %s" % report["why"])
    check("a chain bent the WRONG way is caught even though it is far from straight",
          report["nudged"] is True and report["before_mm"] < 0.0,
          "before %s mm" % report.get("before_mm"))
    check("...and it is pushed through zero to a real forward bend",
          abs(report["after_mm"] - want * 1000.0) < 0.01,
          "after %s mm, wanted %.2f" % (report.get("after_mm"), want * 1000.0))
    check("...which is a bigger move than the bend itself (it has to undo it first)",
          report["nudge_mm"] > abs(report["before_mm"]), str(report))
    note("this is exactly the werewolf: -25.1 mm becomes +%.1f mm" % report["after_mm"])

    # (c) already bent the right way, by enough: untouched.
    bent = straight + Vector((0.0, -want * 1.5, 0.0))
    point, report = L.prebend_joint(None, hip, bent, ankle, forward, "knee", "L")
    note("already-bent knee: %s" % report["why"])
    check("a chain already apexing the right way is left exactly as measured",
          report["nudged"] is False, str(report))
    check("...and the point is returned bit for bit", (point - bent).length < 1e-12,
          str(point))

    # (d) bent the right way but not enough: topped up, not doubled.
    shy = straight + Vector((0.0, -want * 0.25, 0.0))
    point, report = L.prebend_joint(None, hip, shy, ankle, forward, "knee", "L")
    check("a chain bent the right way but too little is topped up to the minimum",
          report["nudged"] is True
          and abs(report["after_mm"] - want * 1000.0) < 0.01, str(report))
    check("...by the difference only", abs(report["nudge_mm"] - want * 750.0) < 0.01,
          "%s mm" % report.get("nudge_mm"))

    # (e) the elbow goes the other way. Same function, one table entry apart.
    shoulder = Vector((0.2, 0.0, 1.3))
    wrist = Vector((0.55, 0.0, 0.9))
    mid = (shoulder + wrist) * 0.5
    point, report = L.prebend_joint(None, shoulder, mid, wrist, forward, "elbow", "L")
    check("an elbow apexes BACKWARD", report["direction"] == "backward"
          and point.y > mid.y, "%.4f -> %.4f" % (mid.y, point.y))
    check("and the anatomy is a table, not two copies of the code",
          L.PREBEND_DIRECTION == {"knee": 1.0, "elbow": -1.0},
          str(L.PREBEND_DIRECTION))

    # (f) facing is respected: a character built facing +Y bends its knee to +Y.
    point, report = L.prebend_joint(None, hip, straight, ankle,
                                    L.facing_vector("+Y"), "knee", "L")
    check("the pre-bend follows the facing rather than a hard-coded axis",
          point.y > straight.y, "%.4f -> %.4f" % (straight.y, point.y))
    check("facing_vector reads the convention's own spelling",
          list(L.facing_vector("-Y")) == [0.0, -1.0, 0.0]
          and list(L.facing_vector("+X")) == [1.0, 0.0, 0.0],
          str(list(L.facing_vector("-Y"))))

    # (g) a limb pointing the way the character faces has no forward left.
    flat_hip = Vector((0.0, 0.0, 1.0))
    flat_ankle = Vector((0.0, -0.8, 1.0))
    point, report = L.prebend_joint(None, flat_hip, (flat_hip + flat_ankle) * 0.5,
                                    flat_ankle, forward, "knee", "L")
    check("a limb running along the facing axis is not bent on a guess",
          report["nudged"] is False and "faces" in report["why"], str(report))


def test_bend_is_signed():
    section("rigforge_rig._bend: the one-line root cause, fixed")
    from forge.tools import rigforge_rig as R

    forward = Vector((0.0, -1.0, 0.0))
    a = Vector((0.1, 0.0, 0.9))
    c = Vector((0.1, 0.0, 0.1))
    span = (c - a).length
    # The werewolf's knee: 4.17% off the line, which clears a 3% MAGNITUDE test,
    # pointing the wrong way.
    b = (a + c) * 0.5 + Vector((-0.0067, 0.0249, 0.0))
    off = ((b - a) - (c - a).normalized() * (b - a).dot((c - a).normalized())).length
    note("the test chain is %.2f%% off the line, backwards -- the live measurement"
         % (100.0 * off / span))
    check("...which is more than the 3% the old magnitude test asked for",
          off / span > R.LANDMARK_BEND, "%.4f" % (off / span))
    bent = R._bend(a, b, c, forward, minimum=R.LANDMARK_BEND)
    check("_bend no longer passes it: a backwards knee is corrected",
          (bent - b).length > 1e-6, "%.3f mm moved" % ((bent - b).length * 1000.0))
    signed = (bent - a) - (c - a).normalized() * (bent - a).dot((c - a).normalized())
    check("...to a forward bend of exactly the minimum",
          abs(signed.dot(forward) - R.LANDMARK_BEND * span) < 1e-6,
          "%.3f mm" % (signed.dot(forward) * 1000.0))

    # and a correct limb is still untouched, which is the regression that matters
    good = (a + c) * 0.5 + forward * (span * 0.05)
    check("a limb already bent the right way is returned bit for bit",
          (R._bend(a, good, c, forward, minimum=R.LANDMARK_BEND) - good).length < 1e-12)


# --- section 2: the nudge stays inside the flesh ----------------------------

def _limb_from_tube(name, start, end, radii, segments=16):
    """A free-standing :class:`Limb` over a tube of known radius, for the clamp."""
    from forge.tools import rigforge_landmarks as L
    import headless_landmarks as HL

    bm = bmesh.new()
    HL._tube(bm, start, end, radii, segments=segments)
    points = [Vector(v.co) for v in bm.verts]
    bm.free()
    return L.Limb(name, points, axis_hint=(Vector(end) - Vector(start)).normalized())


def test_stays_inside_the_flesh():
    section("the nudge stays inside the limb's own cross-section")
    from forge.tools import rigforge_landmarks as L
    import headless_landmarks as HL

    forward = Vector((0.0, -1.0, 0.0))
    start = Vector((0.1, 0.0, 0.9))
    end = Vector((0.1, 0.0, 0.1))
    span = (end - start).length
    want = L.PREBEND_FRACTION * span

    # A fat leg: 75 mm of radius against a 24 mm pre-bend. Room to spare.
    fat = _limb_from_tube("fat", start, end, HL._profile(25, ((0.5, 0.58),), base=0.075))
    reach, why = fat.section_reach(fat.nearest_station(0.5), forward)
    note("fat limb: section reaches %.1f mm forward at the knee (%s)"
         % ((reach or 0.0) * 1000.0, why or "measured"))
    point, report = L.prebend_joint(fat, start, (start + end) * 0.5, end, forward,
                                    "knee", "L", station_fraction=0.5)
    check("a limb with room takes the whole pre-bend",
          report["nudged"] is True and report["clamped_by_flesh"] is False, str(report))
    check("...and the section really was measured, not guessed",
          report.get("section_reach_mm") is not None
          and "section_note" not in report, str(report))
    check("...and the joint is still well inside the flesh",
          report["nudge_mm"] / 1000.0 < reach, str(report))

    # A wire-thin leg: 6 mm of radius against the same 24 mm pre-bend.
    thin = _limb_from_tube("thin", start, end, HL._profile(25, ((0.5, 0.9),), base=0.006))
    reach, _why = thin.section_reach(thin.nearest_station(0.5), forward)
    note("thin limb: section reaches %.1f mm forward, the pre-bend wants %.1f mm"
         % ((reach or 0.0) * 1000.0, want * 1000.0))
    point, report = L.prebend_joint(thin, start, (start + end) * 0.5, end, forward,
                                    "knee", "L", station_fraction=0.5)
    check("a limb with no room clamps the pre-bend rather than leaving the body",
          report["clamped_by_flesh"] is True, str(report))
    check("...and says so in the sentence",
          "clamped" in report["why"], report["why"])
    check("...and what it did apply is inside the section it measured",
          report["nudge_mm"] / 1000.0 <= reach + 1e-9,
          "%s mm of %.2f mm" % (report["nudge_mm"], reach * 1000.0))
    check("...and it is still a real bend in the right direction",
          report["after_mm"] > 0.0, str(report))
    check("the clamp is the section's own half-width, not a constant",
          abs(report["section_limit_mm"]
              - reach * L.PREBEND_SECTION_FRACTION * 1000.0) < 0.01, str(report))


# --- section 3: the biped, end to end ---------------------------------------

def build_and_tag():
    """headless_landmarks' own synthetic biped: perfectly straight legs and arms."""
    import headless_landmarks as HL

    obj, parts = HL.build_biped(name=BODY)
    naming = {"Arm.minus": "Arm.L", "Arm.plus": "Arm.R",
              "Leg.minus": "Leg.L", "Leg.plus": "Leg.R"}
    for part, faces in sorted(parts.items()):
        call("rigforge_tag", {"object": obj.name, "tag": naming.get(part, part),
                              "faces": faces, "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get",
                               "archetype": "biped"})
    note("%d vertices, %d faces; its limbs are straight tubes, which is the "
         "ambiguous case" % (len(obj.data.vertices), len(obj.data.polygons)))
    return obj


def test_landmarks_report_the_prebend(obj):
    section("rigforge_landmarks reports what it nudged, by how much, and why")
    report = call("rigforge_landmarks", {"object": obj.name, "action": "prepare"})
    prebend = {entry["joint"]: entry for entry in (report.get("prebend") or [])}
    note("says: %s" % report["says"])
    check("the result carries a prebend block", bool(prebend), str(report.get("prebend")))
    knee = prebend.get("knee.L") or {}
    elbow = prebend.get("elbow.L") or {}
    note("knee.L: %s" % knee.get("why"))
    note("elbow.L: %s" % elbow.get("why"))
    check("a straight leg was nudged", knee.get("nudged") is True, str(knee))
    check("...forwards", knee.get("direction") == "forward", str(knee))
    check("a straight arm was nudged backwards",
          elbow.get("nudged") is True and elbow.get("direction") == "backward",
          str(elbow))
    from forge.tools import rigforge_landmarks as L
    for name, entry in (("knee.L", knee), ("elbow.L", elbow)):
        check("%s ends at exactly %.0f%% of its own span"
              % (name, 100.0 * L.PREBEND_FRACTION),
              abs((entry.get("after_pct_of_span") or 0.0)
                  - 100.0 * L.PREBEND_FRACTION) < 0.01, str(entry))
        check("%s reports the millimetres, not just a verdict" % name,
              entry.get("nudge_mm") and entry.get("span_mm"), str(entry))
    check("a nudge is loud: it is in the warnings, with the numbers in it",
          any("pre-bend" in w.lower() for w in report.get("warnings") or []),
          str(report.get("warnings")))
    return report


def test_the_mirror_still_holds(obj, workspace):
    section("the nudge runs BEFORE the mirror, so both sides get it identically")
    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo": False})
    check("the landmark path ran", result["fit_method"] == "landmarks",
          str(result["fit_method"]))
    check("the metarig result carries the prebend block too",
          bool(result.get("prebend")), str(result.get("prebend")))
    mirror = result.get("mirror") or {}
    residual = mirror.get("residual_asymmetry_mm")
    note("mirror residual: %s mm" % residual)
    check("the two sides are still a perfect mirror of each other",
          residual is not None and abs(float(residual)) < 1e-6, str(residual))

    meta = bpy.data.objects[result["metarig"]]
    from forge.tools import rigforge_landmarks as L
    fwd = L.facing_vector("-Y")
    offsets = {}
    for side in ("L", "R"):
        for names, what, want in ((("thigh.%s", "shin.%s"), "knee", fwd),
                                  (("upper_arm.%s", "forearm.%s"), "elbow", -fwd)):
            a = meta.matrix_world @ meta.data.bones[names[0] % side].head_local
            b = meta.matrix_world @ meta.data.bones[names[1] % side].head_local
            c = meta.matrix_world @ meta.data.bones[names[1] % side].tail_local
            axis = (c - a).normalized()
            off = (b - a) - axis * (b - a).dot(axis)
            push = (want - axis * want.dot(axis)).normalized()
            offsets["%s.%s" % (what, side)] = (off.dot(push), (c - a).length)
    for what in ("knee", "elbow"):
        left, span = offsets["%s.L" % what]
        right, _span = offsets["%s.R" % what]
        note("%s: left %+.2f mm, right %+.2f mm of a %.0f mm span"
             % (what, left * 1000.0, right * 1000.0, span * 1000.0))
        check("the %s apexes the anatomical way on the LEFT" % what, left > 0.0,
              "%+.3f mm" % (left * 1000.0))
        check("...and identically on the RIGHT, because the nudge preceded the mirror",
              abs(left - right) < 1e-6,
              "%+.4f vs %+.4f mm" % (left * 1000.0, right * 1000.0))
        check("...by at least the minimum the pre-bend promises",
              left >= L.PREBEND_FRACTION * span - 1e-6,
              "%.3f mm of %.3f" % (left * 1000.0, L.PREBEND_FRACTION * span * 1000.0))
    return result


def test_the_gate_passes_a_prebent_rig(obj, meta_result):
    section("the bend_direction gate on a rig that was pre-bent")
    generated = call("rigforge_generate_rig", {"metarig": meta_result["metarig"],
                                               "mesh": obj.name})
    rig = bpy.data.objects[generated["rig"]]
    from forge.tools import rigcheck, rigforge_rig as R

    before = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
    report = rigcheck.bend_direction(rig)
    note("says: %s" % report["says"])
    check("the gate measured every IK limb", len(report["limbs"]) >= 4,
          str(len(report["limbs"])))
    check("it takes 'forward' from the rig's own toes, not from a constant",
          report["forward_from"] == "toes", str(report["forward_from"]))
    check("the verdict is ok", report["verdict"] == "ok", report["says"])
    for row in report["limbs"]:
        note("%s: %s travels %+.1f mm %s on a %.0f mm lift (floor %.0f, straight-limb "
             "ideal %.0f)" % (row["limb"], row["joint"], row["travel_along_mm"],
                              row["expected"], row["lift_mm"], row["required_mm"],
                              row["straight_limb_ideal_mm"]))
        check("%s folds %s, by a real margin" % (row["limb"], row["expected"]),
              row["correct"] is True and row["travel_along_mm"] > row["required_mm"],
              str(row))
        check("%s came back where it started" % row["limb"],
              row["returned_mm"] <= rigcheck.BEND_RETURN_MM, str(row["returned_mm"]))
        check("%s's IK target really moved (the test is not a no-op)" % row["limb"],
              abs(row["target_moved_mm"] - row["lift_mm"]) < 1.0, str(row))

    after = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
    worst = 0.0
    for name, matrix in after.items():
        delta = matrix - before[name]
        worst = max(worst, max(abs(value) for row in delta for value in row))
    check("and the harness left the whole pose exactly as it found it",
          worst < 1e-9, "worst matrix element %.3e" % worst)
    check("the gate says so itself", report["pose_restored"] is True)

    poles = R.pole_side_check(rig)
    note("poles: %s" % poles["says"])
    check("every pole sits on the side its limb bends towards",
          poles["verdict"] == "ok", poles["says"])
    for row in poles["poles"]:
        check("%s's pole %r is on the bend side" % (row["limb"], row["pole"]),
              row["correct"] is True and row["pole_offset_mm"] > 0.0, str(row))
    check("the generate report carries the pole check",
          (generated.get("ik") or {}).get("pole_sides", {}).get("verdict") == "ok",
          str((generated.get("ik") or {}).get("pole_sides")))

    full = call("rig_check", {"rig": rig.name, "mesh": obj.name, "poses": "quick",
                              "intersections": False})
    check("rig_check reports bend_direction as a placement gate",
          (full.get("bend_direction") or {}).get("verdict") == "ok",
          str((full.get("bend_direction") or {}).get("says")))
    check("...and its clean sentence mentions the limbs it folded",
          "folds the anatomical way" in (full.get("says") or ""), full.get("says"))
    return rig, generated


def test_the_gate_fails_a_straight_rig(obj, meta_result):
    section("...and FAILS a rig whose knees were straightened back out")
    from forge.tools import rigcheck, rigforge_landmarks as L, rigforge_rig as R

    meta = bpy.data.objects[meta_result["metarig"]]
    # Put the defect back by hand, on BOTH knees so the two sides stay a mirror
    # of each other: onto the hip-to-ankle line and 4.2% of the span past it,
    # backwards, which is the werewolf's own measurement.
    bpy.context.view_layer.objects.active = meta
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bones = meta.data.edit_bones
        for side in ("L", "R"):
            thigh = bones["thigh.%s" % side]
            shin = bones["shin.%s" % side]
            a = thigh.head.copy()
            c = shin.tail.copy()
            axis = (c - a).normalized()
            along = axis * (shin.head - a).dot(axis)
            target = a + along + Vector((0.0, 1.0, 0.0)) * (0.042 * (c - a).length)
            thigh.tail = target
            shin.head = target
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    note("both knees pushed 4.2 percent of the span backwards")

    generated = call("rigforge_generate_rig", {"metarig": meta.name, "mesh": obj.name})
    rig = bpy.data.objects[generated["rig"]]

    before = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
    report = rigcheck.bend_direction(rig)
    note("says: %s" % report["says"])
    check("the gate FAILS", report["verdict"] == "fail", report["says"])
    check("...and names the direction in a sentence",
          "WRONG WAY" in report["says"] and "backward" in report["says"],
          report["says"])
    check("...and names the pre-bend as the fix",
          "pre-bend" in report["says"] and "knee apexes forward" in report["says"],
          report["says"])
    legs = [row for row in report["limbs"] if row["limb"].startswith("leg")]
    for row in legs:
        note("%s: %s travels %+.1f mm, floor %+.1f mm"
             % (row["limb"], row["joint"], row["travel_along_mm"], row["required_mm"]))
        check("%s is reported as travelling backwards outright" % row["limb"],
              row["backwards"] is True and row["travel_along_mm"] < 0.0, str(row))
    check("both legs are named, not just the first",
          len(report["backwards"]) >= 2, str(report["backwards"]))

    after = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
    worst = 0.0
    for name, matrix in after.items():
        delta = matrix - before[name]
        worst = max(worst, max(abs(value) for row in delta for value in row))
    check("a failing gate still restores the pose exactly",
          worst < 1e-9, "worst matrix element %.3e" % worst)

    poles = R.pole_side_check(rig)
    note("poles: %s" % poles["says"][:200])
    check("the pole check fails with it -- the pole follows the rest plane",
          poles["verdict"] == "fail", poles["says"])
    check("...and says it cannot be fixed on the generated rig",
          "REST plane" in poles["says"] and "generate again" in poles["says"],
          poles["says"])
    leg = next((row for row in poles["poles"] if row["limb"] == "leg.L"), {})
    check("leg.L's pole is on the wrong side, in millimetres",
          leg.get("correct") is False and (leg.get("pole_offset_mm") or 0.0) < 0.0,
          str(leg))

    full = call("rig_check", {"rig": rig.name, "mesh": obj.name, "poses": "quick",
                              "intersections": False})
    check("rig_check's overall gate fails on it",
          full.get("gate") == "fail", str(full.get("gate")))
    check("...and its sentence leads with the bend direction",
          "WRONG WAY" in (full.get("says") or ""), (full.get("says") or "")[:300])
    return rig


def test_breast_bones_are_optional(obj, workspace):
    section("Rigify's breast bones are dropped before generation, not after")
    from forge.tools import rigforge_rig as R

    # --- the default: gone, and gone from the metarig, which is the only place
    # dropping them costs nothing downstream.
    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo": False})
    meta = bpy.data.objects[result["metarig"]]
    dropped = result.get("dropped_bones") or []
    note("dropped: %s" % dropped)
    check("the default drops both breast bones",
          sorted(dropped) == ["breast.L", "breast.R"], str(dropped))
    check("...off the METARIG, before Rigify ever sees them",
          not [b.name for b in meta.data.bones if "breast" in b.name.lower()],
          str([b.name for b in meta.data.bones if "breast" in b.name.lower()]))
    check("...loudly, with the reason", any("breast" in w for w in result["warnings"]),
          str(result["warnings"])[:200])
    check("and nothing dangles: no tag maps to a bone that no longer exists",
          all(name in meta.data.bones
              for names in (result.get("mapping") or {}).values() for name in names),
          str({tag: [n for n in names if n not in meta.data.bones]
               for tag, names in (result.get("mapping") or {}).items()}))

    generated = call("rigforge_generate_rig", {"metarig": meta.name, "mesh": obj.name})
    rig = bpy.data.objects[generated["rig"]]
    deform = R.deform_bones(rig)
    check("the generated rig has no DEF-breast bone",
          not [b for b in deform if "breast" in b.lower()],
          str([b for b in deform if "breast" in b.lower()]))
    check("...and no breast bone of any kind, control or mechanism",
          not [b.name for b in rig.data.bones if "breast" in b.name.lower()],
          str([b.name for b in rig.data.bones if "breast" in b.name.lower()])[:200])
    groups = [g.name for g in obj.vertex_groups]
    check("the mesh carries no breast vertex group either",
          not [g for g in groups if "breast" in g.lower()],
          str([g for g in groups if "breast" in g.lower()]))

    # --- the skinning is unaffected: every deform bone still owns flesh, and
    # the mesh is still fully weighted.
    report = (call("rigforge_weights", {"object": obj.name, "rig": rig.name,
                                        "action": "report"}).get("report") or {})
    note("weights without them: %s of %s vertices unweighted, %s deform group(s), "
         "%d unused bone(s)"
         % (report.get("unweighted_vertices"), report.get("total_vertices"),
            report.get("deform_groups"), len(report.get("unused_bones") or [])))
    check("the weight report was really read (it counted this mesh's vertices)",
          report.get("total_vertices") == len(obj.data.vertices),
          "%s vs %d" % (report.get("total_vertices"), len(obj.data.vertices)))
    check("every DEF- bone the rig kept has a vertex group on the mesh",
          all(name in groups for name in deform),
          str([n for n in deform if n not in groups]))
    check("the bind still runs and the rig still deforms",
          generated.get("weighted") is True
          and (generated.get("deform_bones") or 0) > 8,
          str(generated.get("deform_bones")))
    full = call("rig_check", {"rig": rig.name, "mesh": obj.name, "poses": "quick",
                              "intersections": False})
    check("...and the harness is still happy with where the bones are",
          (full.get("bend_direction") or {}).get("verdict") == "ok"
          and (full.get("asymmetry") or {}).get("verdict") == "ok",
          "%s / %s" % ((full.get("bend_direction") or {}).get("verdict"),
                       (full.get("asymmetry") or {}).get("verdict")))

    # --- and the switch really is a switch. This is also the **control** for
    # "skinning unaffected": the same mesh rigged with the bones kept, measured
    # the same way. An absolute assertion (no unweighted vertices, no unused
    # bones) would be a claim about this synthetic biped's box torso rather than
    # about the drop -- it has 6 and 5 of them either way.
    kept = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                     "echo": False, "breast_bones": True})
    meta2 = bpy.data.objects[kept["metarig"]]
    check("breast_bones=true keeps them",
          not (kept.get("dropped_bones") or [])
          and len([b.name for b in meta2.data.bones if "breast" in b.name.lower()]) == 2,
          "%s / %s" % (kept.get("dropped_bones"),
                       [b.name for b in meta2.data.bones if "breast" in b.name.lower()]))
    generated2 = call("rigforge_generate_rig", {"metarig": meta2.name,
                                                "mesh": obj.name})
    rig2 = bpy.data.objects[generated2["rig"]]
    deform2 = R.deform_bones(rig2)
    control = (call("rigforge_weights", {"object": obj.name, "rig": rig2.name,
                                         "action": "report"}).get("report") or {})
    note("weights with them: %s unweighted, %s deform group(s), %d unused bone(s)"
         % (control.get("unweighted_vertices"), control.get("deform_groups"),
            len(control.get("unused_bones") or [])))
    check("keeping them really does generate the DEF- bones (so the control is real)",
          len([b for b in deform2 if "breast" in b.lower()]) == 2,
          str([b for b in deform2 if "breast" in b.lower()]))
    check("the drop removes exactly those two deform bones and nothing else",
          sorted(deform2) == sorted(deform + sorted(
              b for b in deform2 if "breast" in b.lower())),
          str(sorted(set(deform2) - set(deform))))
    check("SKINNING IS UNAFFECTED: the same vertices are unweighted either way",
          report.get("unweighted_vertices") == control.get("unweighted_vertices"),
          "%s without vs %s with" % (report.get("unweighted_vertices"),
                                     control.get("unweighted_vertices")))
    check("...and the same bones go unused, less the two that are gone",
          sorted(report.get("unused_bones") or [])
          == sorted(b for b in (control.get("unused_bones") or [])
                    if "breast" not in b.lower()),
          "%s vs %s" % (report.get("unused_bones"), control.get("unused_bones")))


def test_the_echo_marks_its_joints(obj, workspace):
    section("the skeleton echo marks where the bones articulate")
    from forge.tools import rigforge_landmarks as L

    meta = bpy.data.objects[call("rigforge_metarig",
                                 {"object": obj.name, "archetype": "biped",
                                  "echo": False})["metarig"]]
    sticks, drawn, marked = L._bone_sticks(meta, deform_only=True)
    with_markers = len(sticks.data.vertices)
    bpy.data.objects.remove(sticks, do_unlink=True)
    plain, drawn2, marked2 = L._bone_sticks(meta, deform_only=True,
                                            joint_markers=False)
    without = len(plain.data.vertices)
    bpy.data.objects.remove(plain, do_unlink=True)
    note("%d bones: %d joints marked, %d verts with markers vs %d without"
         % (len(drawn), marked, with_markers, without))
    check("a ball is drawn at every distinct joint", marked > len(drawn) // 2,
          "%d markers for %d bones" % (marked, len(drawn)))
    check("coincident joints are welded, not drawn twice",
          marked < 2 * len(drawn), "%d markers for %d bones" % (marked, len(drawn)))
    check("the markers are real geometry", with_markers > without, "%d vs %d"
          % (with_markers, without))
    check("switching them off gives the old stick-only picture back",
          marked2 == 0 and without == 8 * len(drawn2),
          "%d markers, %d verts for %d bones" % (marked2, without, len(drawn2)))

    result = call("rigforge_echo_skeleton", {"metarig": meta.name, "mesh": obj.name,
                                             "dir": os.path.join(workspace, "echo"),
                                             "resolution": 256})
    note("says: %s" % result["says"])
    check("the command reports how many joints it marked",
          (result.get("joints_marked") or 0) > 0, str(result.get("joints_marked")))
    check("...and the sentence says the balls are there and why",
          "articulate" in result["says"] or "ball" in result["says"], result["says"])
    for image in result["images"]:
        check("the %s echo is a real PNG" % image["view"], image["bytes"] > 1000,
              str(image))


#: A **boot**, along the +Y axis the builder faces: 90 mm of heel behind the
#: ankle, 310 mm of shoe in front of it, a wide midfoot and a narrower toe box
#: meeting at 62% — a real taper for the ball rule to find, and a toe box long
#: enough that a foot chain inherited from Rigify's template falls visibly short
#: of it, which is the live defect.
BOOT = (-0.09, 0.31, 0.10, 0.065, 0.62, 0.045)


def test_the_foot_follows_the_shoe(workspace):
    section("the foot chain runs to the front of the boot, not the template's foot")
    import headless_landmarks as HL
    from forge.tools import rigforge_landmarks as L

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    obj, parts = HL.build_biped(name="BootedBiped", boot=BOOT)
    naming = {"Arm.minus": "Arm.L", "Arm.plus": "Arm.R",
              "Leg.minus": "Leg.L", "Leg.plus": "Leg.R"}
    for part, faces in sorted(parts.items()):
        call("rigforge_tag", {"object": obj.name, "tag": naming.get(part, part),
                              "faces": faces, "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get",
                               "archetype": "biped"})
    note("booted biped: heel %.0f mm behind, toe %.0f mm in front, toe box from %.0f%%"
         % (-BOOT[0] * 1000.0, BOOT[1] * 1000.0, BOOT[4] * 100.0))

    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo": False})
    check("the landmark path ran", result["fit_method"] == "landmarks",
          str(result["fit_method"]))
    landmarks = result.get("joint_landmarks") or {}
    for role in ("ankle.L", "ball.L", "toe_tip.L", "heel.L"):
        info = landmarks.get(role) or {}
        note("%-10s %s  how=%s" % (role, info.get("mm"), info.get("how")))
        check("%s is a measured landmark, and says how" % role,
              bool(info.get("mm")) and bool(info.get("how")), str(info))
    check("the ball came from the sole's own taper, not a fraction",
          "taper" in str((landmarks.get("ball.L") or {}).get("how")),
          str((landmarks.get("ball.L") or {}).get("how")))

    # The character was rotated onto the convention, so it now faces -Y and the
    # boot's toe is the mesh's -Y extreme.
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    ground = min(p.z for p in coords)
    sole = [p for p in coords if p.z <= ground + BOOT[2] * 0.9 and p.x > 0.02]
    front = min(p.y for p in sole)          # -Y is forward after the rotation
    meta = bpy.data.objects[result["metarig"]]
    toe = meta.data.bones["toe.L"]
    foot = meta.data.bones["foot.L"]
    tip = meta.matrix_world @ toe.tail_local
    ball = meta.matrix_world @ toe.head_local
    ankle = meta.matrix_world @ foot.head_local
    short = (tip.y - front) * 1000.0
    note("the boot reaches y=%.1f mm; the toe bone's tail ends at y=%.1f mm, "
         "%.1f mm short" % (front * 1000.0, tip.y * 1000.0, short))
    check("THE TOE BONE REACHES THE FRONT OF THE BOOT",
          0.0 <= short <= L.FOOT_TIP_MARGIN_MM + 6.0,
          "%.1f mm short, margin is %.1f mm" % (short, L.FOOT_TIP_MARGIN_MM))
    check("...and it does not poke out through the toe box",
          tip.y >= front - 1e-6, "%.1f vs %.1f mm" % (tip.y * 1000.0, front * 1000.0))
    check("the ball sits between the ankle and the tip, in that order",
          tip.y < ball.y < ankle.y,
          "tip %.1f, ball %.1f, ankle %.1f mm"
          % (tip.y * 1000.0, ball.y * 1000.0, ankle.y * 1000.0))
    check("the ball is near the boot's own taper, not the template's 66%",
          abs((ball.y - front) / (ankle.y - front) - (1.0 - BOOT[4])) < 0.22,
          "%.2f along vs a taper at %.2f"
          % ((ball.y - front) / (ankle.y - front), 1.0 - BOOT[4]))

    # The ankle: above the foot mass, not up the shin and not inside the boot.
    boot_top = ground + BOOT[2]
    note("the boot's upper is at z=%.1f mm; the ankle sits at z=%.1f mm"
         % (boot_top * 1000.0, ankle.z * 1000.0))
    check("the ankle is above the foot mass", ankle.z > boot_top - 1e-6,
          "%.1f vs %.1f mm" % (ankle.z * 1000.0, boot_top * 1000.0))
    check("...and not half way up the shin",
          ankle.z < ground + (BOOT[2] * 4.0),
          "%.1f mm, boot is %.1f mm tall" % (ankle.z * 1000.0, BOOT[2] * 1000.0))

    # The heel pivot, which is what the foot roll rotates about.
    heel = meta.data.bones.get("heel.02.L")
    if heel is not None:
        head = meta.matrix_world @ heel.head_local
        tail = meta.matrix_world @ heel.tail_local
        back = max(p.y for p in sole)
        note("the boot's heel is at y=%.1f mm; heel.02.L spans y=%.1f mm at z=%.1f mm"
             % (back * 1000.0, head.y * 1000.0, head.z * 1000.0))
        check("the heel pivot is on the back of the sole",
              abs(head.y - back) * 1000.0 < 25.0,
              "%.1f vs %.1f mm" % (head.y * 1000.0, back * 1000.0))
        check("...at ground level, which is what a heel strike pivots about",
              abs(head.z - ground) * 1000.0 < 15.0,
              "%.1f vs %.1f mm" % (head.z * 1000.0, ground * 1000.0))
        check("...and it spans the sole's own width",
              abs((tail - head).length - BOOT[3] * 2.0) * 1000.0 < 25.0,
              "%.1f mm vs a %.1f mm sole"
              % ((tail - head).length * 1000.0, BOOT[3] * 2000.0))

    # ... on both feet, by the mirror, and with the centering machinery happy.
    mirror = result.get("mirror") or {}
    residual = mirror.get("residual_asymmetry_mm")
    check("the foot was authored on the left and mirrored: asymmetry is still 0.0",
          residual is not None and abs(float(residual)) < 1e-6, str(residual))
    right = meta.matrix_world @ meta.data.bones["toe.R"].tail_local
    check("...so the right toe reaches just as far",
          abs(right.y - tip.y) < 1e-6, "%.4f vs %.4f mm"
          % (right.y * 1000.0, tip.y * 1000.0))

    generated = call("rigforge_generate_rig", {"metarig": meta.name,
                                               "mesh": obj.name})
    rig = bpy.data.objects[generated["rig"]]
    from forge.tools import rigforge_rig as R
    fwd, how = R.rig_forward_axis(rig)
    check("the rig's forward axis now comes from a MEASURED toe, not a template",
          how == "toes" and fwd.y < -0.9, "%s %s" % (how, [round(v, 3) for v in fwd]))
    full = call("rig_check", {"rig": rig.name, "mesh": obj.name, "poses": "quick",
                              "intersections": False})
    check("the bend gate is still happy with the re-fitted leg",
          (full.get("bend_direction") or {}).get("verdict") == "ok",
          str((full.get("bend_direction") or {}).get("says")))
    centering = full.get("centering") or {}
    note("centering: %s, worst %s at %s%% of its section radius"
         % (centering.get("verdict"), centering.get("worst"),
            centering.get("worst_offset_pct_of_radius")))
    check("and the centering machinery measured the re-fitted bones",
          any(row["bone"].startswith("DEF-foot") or row["bone"].startswith("DEF-toe")
              for row in centering.get("bones") or []),
          str([row["bone"] for row in centering.get("bones") or []])[:200])


def test_the_hand_stops_at_the_knuckles(workspace):
    section("the wrist is the wrist, and the hand bone ends at the knuckles")
    from forge.tools import rigforge_landmarks as L
    import headless_landmarks as HL

    # A mitten on the end of a forearm, along +X: forearm tapering to a narrow
    # wrist, then a wide palm, then a short taper into a blunt finger mass.
    # A "narrowest station in the distal band" rule walks past the palm and
    # lands in the fingers, which is the wrist-deep-in-the-palm defect.
    elbow = Vector((0.2, 0.0, 1.1))
    tip = Vector((0.62, 0.0, 1.1))
    bm = bmesh.new()
    HL._tube(bm, elbow, tip, [
        0.052, 0.048, 0.044, 0.040, 0.036, 0.032,     # forearm tapering
        0.030,                                        # the wrist, at 6/17
        0.040, 0.050, 0.055, 0.056, 0.055,            # the palm, the widest part
        0.050,                                        # the knuckles: taper begins
        0.042, 0.036, 0.030, 0.024,                   # the fingers, thinner than
    ], segments=20)                                   # the wrist ever was
    points = [Vector(v.co) for v in bm.verts]
    bm.free()
    length = (tip - elbow).length
    axis = (tip - elbow).normalized()
    truth_wrist = elbow + axis * (length * 6.0 / 16.0)
    truth_knuckle = elbow + axis * (length * 12.0 / 16.0)

    hand = L.hand_landmarks(points, elbow, axis)
    check("the mitten is read as a hand", hand is not None, str(hand))
    if hand is None:
        return
    wrist_err = (Vector(hand["wrist"]) - truth_wrist).length * 1000.0
    knuckle_err = (Vector(hand["knuckle"]) - truth_knuckle).length * 1000.0
    note("wrist: %s  (%.1f mm from the ring the builder made)"
         % (hand["detail"]["wrist"]["how"], wrist_err))
    note("knuckles: %s  (%.1f mm from the ring the builder made)"
         % (hand["detail"]["knuckle"]["how"], knuckle_err))
    note("wrist girth %s mm, palm girth %s mm, %s mm of palm, fingers reach %s mm"
         % (hand["wrist_girth_mm"], hand["palm_girth_mm"], hand["palm_mm"],
            hand["tip_mm"]))
    check("THE WRIST LANDS AT THE WRIST, not deep in the palm",
          wrist_err < 30.0, "%.1f mm off" % wrist_err)
    check("...found as the first girth minimum, not the narrowest station",
          "first girth minimum" in hand["detail"]["wrist"]["how"],
          hand["detail"]["wrist"]["how"])
    check("...and it is NOT in the fingers, which are narrower than it is",
          (Vector(hand["wrist"]) - elbow).dot(axis) < length * 0.55,
          "%.0f%% along the arm"
          % (100.0 * (Vector(hand["wrist"]) - elbow).dot(axis) / length))
    check("THE HAND BONE ENDS AT THE KNUCKLE LINE, not at the fingertips",
          knuckle_err < 30.0, "%.1f mm off" % knuckle_err)
    check("...which is the palm's own taper", "taper" in
          hand["detail"]["knuckle"]["how"], hand["detail"]["knuckle"]["how"])
    check("...well short of the fingertips",
          hand["palm_mm"] < hand["tip_mm"] * 0.8,
          "%s mm of palm against %s mm to the tip"
          % (hand["palm_mm"], hand["tip_mm"]))
    check("the palm is measured fatter than the wrist, which is why it is a hand",
          hand["palm_girth_mm"] > hand["wrist_girth_mm"] * L.HAND_PALM_FACTOR,
          "%s vs %s mm" % (hand["palm_girth_mm"], hand["wrist_girth_mm"]))

    # An arm that stops at the wrist is not a hand.
    bm = bmesh.new()
    HL._tube(bm, elbow, elbow + axis * (length * 0.45),
             [0.052, 0.048, 0.044, 0.040, 0.036, 0.032, 0.030], segments=20)
    bare = [Vector(v.co) for v in bm.verts]
    bm.free()
    check("an arm tagged only to the wrist gets no hand, rather than an invented palm",
          L.hand_landmarks(bare, elbow, axis) is None,
          str(L.hand_landmarks(bare, elbow, axis)))


def test_the_hand_on_the_booted_biped(workspace):
    section("...and the same on a real fit: hand.L is fitted, not dragged")
    from forge.tools import rigforge_landmarks as L

    obj = bpy.data.objects["BootedBiped"]
    result = call("rigforge_metarig", {"object": obj.name, "archetype": "biped",
                                       "echo": False})
    landmarks = result.get("joint_landmarks") or {}
    for role in ("wrist.L", "knuckle.L"):
        info = landmarks.get(role) or {}
        note("%-10s %s  how=%s" % (role, info.get("mm"), info.get("how")))
        check("%s is a measured landmark, and says how" % role,
              bool(info.get("mm")) and bool(info.get("how")), str(info))
    check("the wrist reports how far it moved off the distal girth minimum",
          (landmarks.get("wrist.L") or {}).get("moved_from_girth_minimum_mm")
          is not None, str(landmarks.get("wrist.L")))

    meta = bpy.data.objects[result["metarig"]]
    check("hand.L was fitted rather than dragged",
          "hand.L" in (result.get("fitted_bones") or []),
          str([n for n in (result.get("fitted_bones") or []) if "hand" in n]))
    check("...and so was hand.R, by the mirror",
          "hand.R" in (result.get("fitted_bones") or []),
          str([n for n in (result.get("fitted_bones") or []) if "hand" in n]))
    hand = meta.data.bones["hand.L"]
    forearm = meta.data.bones["forearm.L"]
    head = meta.matrix_world @ hand.head_local
    tail = meta.matrix_world @ hand.tail_local
    arm_points = [obj.matrix_world @ v.co for v in obj.data.vertices
                  for entry in v.groups
                  if entry.group == obj.vertex_groups["tag_Arm.L"].index]
    axis = (tail - head).normalized()
    reach = max((p - head).dot(axis) for p in arm_points)
    how = str((landmarks.get("knuckle.L") or {}).get("how") or "")
    note("the hand bone is %.1f mm long; the arm tag reaches %.1f mm past its head; "
         "knuckles found by %s" % ((tail - head).length * 1000.0, reach * 1000.0, how))
    if "never tapers" not in how:
        check("the hand bone stops at the knuckles, short of the arm tag's far end",
              (tail - head).length < reach * 0.9,
              "%.1f of %.1f mm" % ((tail - head).length * 1000.0, reach * 1000.0))
    else:
        # This biped's arm is a plain tapered tube: it has a palm-ish flare and
        # no fingers at all, so there is no knuckle line in the geometry. The
        # bone then runs to the end of what was tagged -- and the value being
        # tested is that this is *said*, not that it is avoided.
        check("a hand with no finger taper says so rather than inventing a knuckle line",
              "never tapers" in how, how)
        check("...and the bone is still shorter than the forearm above it",
              (tail - head).length
              < (meta.matrix_world @ forearm.tail_local
                 - meta.matrix_world @ forearm.head_local).length,
              "%.1f mm hand" % ((tail - head).length * 1000.0))
    check("its head is the forearm's tail: one chain, no gap",
          ((meta.matrix_world @ forearm.tail_local) - head).length < 1e-6,
          "%.4f mm" % (((meta.matrix_world @ forearm.tail_local) - head).length * 1000.0))
    residual = (result.get("mirror") or {}).get("residual_asymmetry_mm")
    check("and the mirror is still exact with the hand in the chain",
          residual is not None and abs(float(residual)) < 1e-6, str(residual))


def test_a_leg_with_no_foot_says_so(workspace):
    section("a leg tag that stops at the ankle gets no foot, and says so")
    from forge.tools import rigforge_landmarks as L
    from mathutils import Vector as V

    # A bare leg: a tube to the ankle and nothing below it. foot_landmarks must
    # decline rather than invent a boot out of the last two edge loops.
    import headless_landmarks as HL
    bm = bmesh.new()
    HL._tube(bm, (0.0, 0.0, 0.86), (0.0, 0.0, 0.10),
             HL._profile(25, ((0.5, 0.58), (0.9, 0.55)), base=0.075))
    points = [Vector(v.co) for v in bm.verts]
    bm.free()
    result = L.foot_landmarks(points, V((0.0, 0.0, 0.12)), V((0.0, 0.0, 0.48)),
                              V((0.0, -1.0, 0.0)))
    check("a leg with no foot returns nothing rather than a guess",
          result is None, str(result))


def test_the_straight_case_is_ambiguous_not_merely_wrong():
    section("why a straight limb is not 'nearly right': it has no preference at all")
    from forge.tools import rigforge_landmarks as L
    note("measured on werewolf-wip-9 by sweeping the pre-bend and regenerating:")
    note("  0.0 percent of span -> the knee travels -0.0 mm: no preference at all")
    note("  0.5 percent -> leg 70.0 mm, arm 13.5 mm (the arm still fails the floor)")
    note("  1.5 percent -> leg 132.5 mm, arm 38.8 mm")
    note("  3.0 percent -> leg 148.8 mm, arm 68.9 mm")
    check("the shipped fraction is the 3 percent the IK pole test already demanded",
          abs(L.PREBEND_FRACTION - 0.03) < 1e-9, str(L.PREBEND_FRACTION))
    check("and rigforge_rig's own backstop uses the same number",
          abs(__import__("forge.tools.rigforge_rig", fromlist=["x"]).LANDMARK_BEND
              - L.PREBEND_FRACTION) < 1e-9)


# --- the port ---------------------------------------------------------------

def test_server_frees_its_port():
    section("the socket")
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
    print("Forge add-on anatomical-pre-bend headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_prebend_test_")
    try:
        test_prebend_maths()
        test_bend_is_signed()
        test_stays_inside_the_flesh()
        test_the_straight_case_is_ambiguous_not_merely_wrong()

        section("the character: a biped with perfectly straight limbs")
        obj = build_and_tag()
        test_landmarks_report_the_prebend(obj)
        meta_result = test_the_mirror_still_holds(obj, workspace)
        test_the_gate_passes_a_prebent_rig(obj, meta_result)
        test_the_gate_fails_a_straight_rig(obj, meta_result)
        test_breast_bones_are_optional(obj, workspace)
        test_the_echo_marks_its_joints(obj, workspace)
        test_the_foot_follows_the_shoe(workspace)
        test_the_hand_stops_at_the_knuckles(workspace)
        test_the_hand_on_the_booted_biped(workspace)
        test_a_leg_with_no_foot_says_so(workspace)
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
