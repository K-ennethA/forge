"""Headless add-on tests for **where the hand points and how high the foot runs**.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_handfoot.py

Needs no geometry service, no GPU, opens no window and downloads nothing.  It
works on a **copy** of ``projects/werewolf/models/werewolf-wip-10.blend`` in a
scratch folder; the original is never opened for writing.

**What this suite is for.**

Two defects, both found the way every defect in this pipeline is found — the
owner looked at a rest-pose render and saw a bone outside the character:

1. **the hand bones point outboard.**  ``DEF-hand.L`` runs from the wrist at
   (297.6, 8.9, 950.2) mm to (358.4, 1.3, 905.1) mm — almost pure ``+X``, away
   from the body, its tail **29.7 mm outside the mesh** — while the hand it is
   supposed to drive hangs straight *down* beside the thigh.  The wrist is right;
   the direction is the arm's, not the palm's.  Both sides mirror it exactly.
2. **the ankle is 65 mm up the shin and the toe is 8 cm off the ground.**
   ``DEF-foot.L``'s head sits at ``z = 173 mm`` on a 1.88 m character whose foot
   mass stops at 110, and ``DEF-toe.L`` runs at ``z = 78.6 mm``.

Both cleared all six existing placement gates, and they had to: ``centering``
deliberately does not judge a hand, a toe or a foot (they have no centreline of
their own), both sides were wrong identically so ``asymmetry`` read 0.0, the
names were right, the weights were tidy, and ``bend_direction`` only drives knees
and elbows.  A defect nothing measures is a defect that comes back.

So this suite asks, in order:

* the **before** numbers on the copy, quoted, so the defect is a measurement and
  not a description;
* that the two new gates — ``hand_containment`` and ``foot_height`` — go **red**
  on that state and say the numbers;
* that the new deterministic rules
  (:func:`~forge.tools.rigforge_landmarks.hand_axis` and the collapse-and-lift
  rules inside :func:`~forge.tools.rigforge_landmarks.foot_landmarks`) measure
  the palm direction and the ankle height from the mesh, **per side, with no
  symmetry assumed**;
* that applying them puts both hands inside their palms and both foot chains at
  the height the sole says, and the gates go **green**;
* that the wrist did not move, that the sole fit's forward/back extents did not
  move, and that the synthetic hand cases the landmark suite relies on still
  measure what they measured.
"""

import math
import os
import shutil
import sys
import tempfile
import traceback

import bmesh
import bpy
from mathutils import Vector

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
SOURCE_BLEND = os.path.join(REPO_DIR, "projects", "werewolf", "models",
                            "werewolf-wip-10.blend")
MESH_NAME = "werewolf-form-a_retopo"
RIG_NAME = "werewolf-form-a_retopo_rig"

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


# --- section 1: the geometry rules, on shapes whose right answer is known ----

def test_palm_axis_on_a_bent_arm():
    section("the palm axis: a hand that does not continue the arm")
    import headless_landmarks as HL
    from forge.tools import rigforge_landmarks as L

    # A forearm running +X that ends in a hand turning DOWN off it: the
    # werewolf's shape, built so the right answer is a number the builder chose.
    # The turn is 50 degrees, half again what the live character measures (31 on
    # the left, 39 on the right), which is enough to break a slab cut in the
    # arm's frame and still leave the wrist rule a girth profile to read.
    elbow = Vector((0.20, 0.0, 1.10))
    wrist = Vector((0.40, 0.0, 1.10))
    tip = wrist + Vector((0.643, 0.0, -0.766)) * 0.14   # 140 mm of hand, 50 deg down
    bm = bmesh.new()
    HL._tube(bm, elbow, wrist, [0.050, 0.046, 0.042, 0.038, 0.034, 0.030],
             segments=16)
    HL._tube(bm, wrist, tip, [0.030, 0.040, 0.048, 0.050, 0.046, 0.038, 0.030, 0.024],
             segments=16)
    points = [Vector(v.co) for v in bm.verts]
    bm.free()

    arm_axis = Vector((1.0, 0.0, 0.0))
    want = (tip - wrist).normalized()
    palm = L.hand_axis(points, wrist, arm_axis)
    check("a hand region is found past the wrist", palm is not None, str(palm))
    if palm is None:
        return
    angle = math.degrees(palm["direction"].angle(want, 0.0))
    note("palm axis %s, %.1f degrees from the builder's own hand direction; it is "
         "%.1f degrees off the arm axis"
         % ([round(v, 3) for v in palm["direction"]], angle, palm["arm_angle_deg"]))
    note("converged in %d pass(es): %s degrees"
         % (len(palm["turns_deg"]),
            ", ".join("%.2f" % t for t in palm["turns_deg"]) or "0"))
    check("THE PALM AXIS IS THE HAND'S, NOT THE ARM'S", angle < 10.0,
          "%.1f degrees off" % angle)
    check("...and it is nowhere near the arm's own direction, which is the defect",
          palm["arm_angle_deg"] > 35.0, "%.1f degrees" % palm["arm_angle_deg"])
    check("the region's principal axis is measured and reported, never used",
          palm["principal_angle_deg"] is not None,
          str(palm.get("principal_angle_deg")))
    reach = palm["reach"]
    check("the hand's reach is its own length, not the arm's",
          abs(reach - (tip - wrist).length) < 0.030,
          "%.1f mm vs a %.1f mm hand"
          % (reach * 1000.0, (tip - wrist).length * 1000.0))

    hand = L.hand_landmarks(points, elbow, arm_axis)
    check("the bent arm reads as a hand", hand is not None, str(hand))
    if hand is None:
        return
    wrist_err = (Vector(hand["wrist"]) - wrist).length * 1000.0
    bone = (Vector(hand["knuckle"]) - Vector(hand["wrist"])).normalized()
    bone_angle = math.degrees(bone.angle(want, 0.0))
    note("hand_landmarks put the wrist at %s (the builder's is %s), %.1f mm off"
         % ([round(v, 4) for v in hand["wrist"]], [round(v, 4) for v in wrist],
            wrist_err))
    note("hand bone %.1f degrees off the hand's own direction, against an arm axis "
         "%.1f degrees off it; %s"
         % (bone_angle, math.degrees(arm_axis.angle(want, 0.0)),
            hand["detail"]["knuckle"]["how"]))
    check("the wrist still lands on the wrist ring, near enough", wrist_err < 50.0,
          "%.1f mm off" % wrist_err)
    check("THE HAND BONE FOLLOWS THE PALM, not the arm it hangs off",
          bone_angle < math.degrees(arm_axis.angle(want, 0.0)) * 0.7,
          "%.1f degrees off, against the arm's %.1f"
          % (bone_angle, math.degrees(arm_axis.angle(want, 0.0))))
    check("...and the rule agrees with the axis it measured",
          hand["bone_vs_palm_axis_deg"] < 20.0,
          "%.1f degrees" % hand["bone_vs_palm_axis_deg"])
    check("...and the tail lands inside the hand's own length",
          0.35 < hand["knuckle_fraction"] < 0.90,
          "%.2f of the way along" % hand["knuckle_fraction"])


def test_the_palm_axis_is_stable_on_a_tiny_hand():
    section("the palm axis on a two-ring hand: a fixed point, not a hill")
    import headless_landmarks as HL
    from forge.tools import rigforge_landmarks as L

    # The synthetic biped's hand is this: the last 8% of a tapered arm tube, two
    # edge loops and 32 vertices, flaring back out to a blunt palm with no
    # fingers. It broke the rule the first time round, and the break was silent:
    # picking the distal slab by RANK puts a partial ring in it the moment the
    # aim tilts by a hair, the partial ring's centroid sits out on the rim, and
    # the next aim tilts further towards it. The loop still "converged" -- onto
    # the rim, 26 degrees off the tube's own axis -- and the placement pass and
    # the gate, starting from slightly different clouds, converged onto OPPOSITE
    # rims, 38 degrees apart. The slab is cut by distance now, so a cone's slab
    # is a whole ring and its centroid is on the axis.
    wrist = Vector((0.40, 0.0, 1.10))
    axis = Vector((0.661, 0.0, -0.750)).normalized()
    tip = wrist + axis * 0.045
    bm = bmesh.new()
    HL._tube(bm, wrist, tip, [0.033, 0.045, 0.055], segments=16)
    region = [Vector(v.co) for v in bm.verts]
    bm.free()
    note("%d vertices in the whole hand, in %d rings" % (len(region), 3))

    palm = L.hand_axis(region, wrist, axis)
    check("a two-ring hand still measures an axis", palm is not None, str(palm))
    if palm is None:
        return
    off = math.degrees(palm["direction"].angle(axis, 0.0))
    note("palm axis %s, %.2f degrees off the cone's own axis; turns %s"
         % ([round(v, 4) for v in palm["direction"]], off,
            ", ".join("%.2f" % t for t in palm["turns_deg"]) or "0"))
    check("THE AXIS OF A CONE IS THE CONE'S AXIS, not a point on its rim",
          off < 2.0, "%.2f degrees off" % off)

    # ...and it must not depend on which cloud asked. The placement pass reads
    # the tag and the gate reads the deform weights: two samplings of one
    # surface. They must land on one axis, and the bug was that they did not --
    # 38 degrees apart, on opposite rims. Same cone, coarser sampling.
    bm = bmesh.new()
    HL._tube(bm, wrist, tip, [0.033, 0.045, 0.055], segments=11)
    other_region = [Vector(v.co) for v in bm.verts]
    bm.free()
    other = L.hand_axis(other_region, wrist, axis)
    check("the same cone sampled differently still measures an axis",
          other is not None, str(other))
    if other is None:
        return
    spread = math.degrees(palm["direction"].angle(other["direction"], 0.0))
    note("a %d-vertex sampling and a %d-vertex sampling of one cone land %.2f "
         "degrees apart" % (len(region), len(other_region), spread))
    check("THE RULE AND THE GATE CANNOT LAND ON OPPOSITE RIMS", spread < 2.0,
          "%.2f degrees apart" % spread)


def test_the_mitten_still_measures_what_it_measured():
    section("the straight mitten the landmark suite pins: unchanged answers")
    import headless_landmarks as HL
    from forge.tools import rigforge_landmarks as L

    elbow = Vector((0.2, 0.0, 1.1))
    tip = Vector((0.62, 0.0, 1.1))
    bm = bmesh.new()
    HL._tube(bm, elbow, tip, [
        0.052, 0.048, 0.044, 0.040, 0.036, 0.032,
        0.030,
        0.040, 0.050, 0.055, 0.056, 0.055,
        0.050,
        0.042, 0.036, 0.030, 0.024,
    ], segments=20)
    points = [Vector(v.co) for v in bm.verts]
    bm.free()
    length = (tip - elbow).length
    axis = (tip - elbow).normalized()
    truth_wrist = elbow + axis * (length * 6.0 / 16.0)
    truth_knuckle = elbow + axis * (length * 12.0 / 16.0)

    hand = L.hand_landmarks(points, elbow, axis)
    check("the mitten is still read as a hand", hand is not None, str(hand))
    if hand is None:
        return
    wrist_err = (Vector(hand["wrist"]) - truth_wrist).length * 1000.0
    knuckle_err = (Vector(hand["knuckle"]) - truth_knuckle).length * 1000.0
    note("wrist %.1f mm off; knuckles %.1f mm off (%s)"
         % (wrist_err, knuckle_err, hand["detail"]["knuckle"]["how"]))
    check("the wrist lands at the wrist", wrist_err < 30.0, "%.1f mm" % wrist_err)
    check("the hand bone ends at the knuckle line", knuckle_err < 30.0,
          "%.1f mm" % knuckle_err)
    check("...and on a straight arm the palm axis IS the arm axis",
          hand["arm_angle_deg"] < 10.0, "%.1f degrees" % hand["arm_angle_deg"])
    check("the palm is still measured fatter than the wrist",
          hand["palm_girth_mm"] > hand["wrist_girth_mm"],
          "%s vs %s mm" % (hand["palm_girth_mm"], hand["wrist_girth_mm"]))

    bm = bmesh.new()
    HL._tube(bm, elbow, elbow + axis * (length * 0.45),
             [0.052, 0.048, 0.044, 0.040, 0.036, 0.032, 0.030], segments=20)
    bare = [Vector(v.co) for v in bm.verts]
    bm.free()
    check("an arm tagged only to the wrist still gets no hand",
          L.hand_landmarks(bare, elbow, axis) is None,
          str(L.hand_landmarks(bare, elbow, axis)))


#: The boot the landmark suites build their booted biped with: heel 90 mm
#: behind, toe 310 mm in front, 100 mm tall, 65 mm half-width, toe box from 62%.
BOOT = (-0.09, 0.31, 0.10, 0.065, 0.62, 0.045)


def test_the_booted_leg_still_fits_its_shoe():
    section("the synthetic booted leg: the ankle is above the boot, not up the shin")
    import headless_landmarks as HL
    from forge.tools import rigforge_landmarks as L

    # The same leg-and-boot the landmark suites build, measured directly rather
    # than through a metarig fit: a tapering tube from the hip down to the boot's
    # upper, and a two-box boot under it. The character faces +Y here, which is
    # what those builders make, so "forward" is +Y.
    bm = bmesh.new()
    HL._tube(bm, (0.09, 0.0, 0.86), (0.09, 0.0, 0.10),
             HL._profile(25, ((0.5, 0.58), (0.9, 0.55)), base=0.075))
    HL._foot(bm, 0.09, BOOT)
    points = [Vector(v.co) for v in bm.verts]
    bm.free()
    ground = min(p.z for p in points)
    boot_top = ground + BOOT[2]
    forward = Vector((0.0, 1.0, 0.0))

    foot = L.foot_landmarks(points, Vector((0.09, 0.0, 0.12)),
                            Vector((0.09, 0.0, 0.48)), forward)
    check("the booted leg reads as having a foot", foot is not None, str(foot))
    if foot is None:
        return
    ankle = Vector(foot["ankle"])
    ball = Vector(foot["ball"])
    tip = Vector(foot["toe_tip"])
    front = max(p.y for p in points)
    note("boot upper at z = %.1f mm; ankle at %.1f; ball %.1f; toe tip %.1f"
         % (boot_top * 1000.0, ankle.z * 1000.0, ball.z * 1000.0, tip.z * 1000.0))
    note("the boot reaches y = %.1f mm; the toe tip is at %.1f, %.1f mm short"
         % (front * 1000.0, tip.y * 1000.0, (front - tip.y) * 1000.0))
    note("ankle found by: %s" % foot["detail"]["ankle"]["how"])
    check("THE ANKLE IS ABOVE THE FOOT MASS", ankle.z > boot_top - 1e-6,
          "%.1f vs a %.1f mm boot" % (ankle.z * 1000.0, boot_top * 1000.0))
    check("...and not half way up the shin", ankle.z < ground + BOOT[2] * 4.0,
          "%.1f mm" % (ankle.z * 1000.0))
    check("the toe bone reaches the front of the boot",
          0.0 <= (front - tip.y) * 1000.0 <= L.FOOT_TIP_MARGIN_MM + 6.0,
          "%.1f mm short" % ((front - tip.y) * 1000.0))
    check("the ball sits between the ankle and the tip, in that order",
          tip.y > ball.y > ankle.y,
          "tip %.1f, ball %.1f, ankle %.1f mm"
          % (tip.y * 1000.0, ball.y * 1000.0, ankle.y * 1000.0))
    check("the toe chain runs just above the sole, not through the middle of the boot",
          (ball.z - ground) < BOOT[2] * 0.5,
          "%.1f mm up a %.1f mm boot"
          % ((ball.z - ground) * 1000.0, BOOT[2] * 1000.0))
    check("the heel pivot is still at ground level",
          abs(Vector(foot["heel"]).z - ground) < 1e-9,
          "%.4f" % Vector(foot["heel"]).z)


def test_a_bare_leg_still_gets_no_foot():
    section("a leg tag that stops at the ankle still gets no foot")
    import headless_landmarks as HL
    from forge.tools import rigforge_landmarks as L

    bm = bmesh.new()
    HL._tube(bm, (0.0, 0.0, 0.86), (0.0, 0.0, 0.10),
             HL._profile(25, ((0.5, 0.58), (0.9, 0.55)), base=0.075))
    points = [Vector(v.co) for v in bm.verts]
    bm.free()
    result = L.foot_landmarks(points, Vector((0.0, 0.0, 0.12)),
                              Vector((0.0, 0.0, 0.48)), Vector((0.0, -1.0, 0.0)))
    check("a leg with no foot still returns nothing rather than a guess",
          result is None, str(result))


# --- section 2: the live character, on a copy -------------------------------

def open_copy(workspace):
    target = os.path.join(workspace, "werewolf-wip-10-copy.blend")
    shutil.copyfile(SOURCE_BLEND, target)
    bpy.ops.wm.open_mainfile(filepath=target)
    return bpy.data.objects[MESH_NAME], bpy.data.objects[RIG_NAME]


def bone_world(rig, name):
    bone = rig.data.bones[name]
    return (rig.matrix_world @ bone.head_local,
            rig.matrix_world @ bone.tail_local)


def test_the_before_numbers(rig):
    section("the defect, measured on the copy before anything is changed")
    from forge.tools import rigcheck, rigforge_landmarks as L

    mesh = bpy.data.objects[MESH_NAME]
    before = {}
    for side in ("L", "R"):
        head, tail = bone_world(rig, "DEF-hand.%s" % side)
        before["hand.%s" % side] = (head, tail)
        note("DEF-hand.%s head (%.4f, %.4f, %.4f) tail (%.4f, %.4f, %.4f), "
             "direction %s"
             % (side, head.x, head.y, head.z, tail.x, tail.y, tail.z,
                [round(v, 3) for v in (tail - head).normalized()]))
    foot_head, foot_tail = bone_world(rig, "DEF-foot.L")
    toe_head, toe_tail = bone_world(rig, "DEF-toe.L")
    note("DEF-foot.L head z = %.4f m, DEF-toe.L z = %.4f m"
         % (foot_head.z, toe_head.z))
    check("the hand defect reproduces: DEF-hand.L runs mostly +X",
          (before["hand.L"][1] - before["hand.L"][0]).normalized().x > 0.7,
          str([round(v, 3) for v in
               (before["hand.L"][1] - before["hand.L"][0]).normalized()]))
    check("the foot defect reproduces: the ankle is at z = 0.173 m",
          abs(foot_head.z - 0.1730) < 0.001, "%.4f" % foot_head.z)
    check("the toe defect reproduces: the toe is at z = 0.0786 m",
          abs(toe_head.z - 0.0786) < 0.001, "%.4f" % toe_head.z)

    hands = rigcheck.hand_containment(rig, mesh)
    feet = rigcheck.foot_height(rig, mesh)
    note("hand_containment: %s" % hands["says"])
    note("foot_height: %s" % feet["says"])
    for row in hands["hands"]:
        note("  %-14s angle %6.1f deg  tail outside %6.1f mm  worst %6.1f mm at %s"
             % (row["bone"], row.get("angle_deg") or 0.0,
                row.get("tail_outside_mm") or 0.0,
                row.get("worst_outside_mm") or 0.0,
                row.get("worst_at_fraction")))
    for row in feet["feet"]:
        note("  side %s  ankle %6.1f mm (mesh says %6.1f)  toe %6.1f mm (mesh says "
             "%6.1f)" % (row["side"], row.get("ankle_height_mm") or 0.0,
                         row.get("ankle_target_mm") or 0.0,
                         row.get("toe_height_mm") or 0.0,
                         row.get("toe_target_mm") or 0.0))

    check("THE HAND GATE GOES RED ON THE BEFORE STATE",
          hands["verdict"] == "fail", "%s: %s" % (hands["verdict"], hands["says"]))
    check("...with the numbers quoted, not a description",
          "mm outside" in hands["says"] and "degrees off" in hands["says"],
          hands["says"])
    check("...on both hands, which is why asymmetry reads 0.0",
          len([r for r in hands["hands"] if r.get("verdict") == "fail"]) == 2,
          str([r.get("verdict") for r in hands["hands"]]))
    check("THE FOOT GATE GOES RED ON THE BEFORE STATE",
          feet["verdict"] == "fail", "%s: %s" % (feet["verdict"], feet["says"]))
    check("...with the numbers quoted", "mm above the sole" in feet["says"],
          feet["says"])
    worst_hand = max((r.get("worst_outside_mm") or 0.0) for r in hands["hands"])
    worst_ankle = max((r.get("ankle_error_mm") or 0.0) for r in feet["feet"])
    return {"hands": hands, "feet": feet,
            "worst_hand_outside_mm": worst_hand,
            "worst_ankle_error_mm": worst_ankle,
            "wrists": {side: before["hand.%s" % side][0] for side in ("L", "R")},
            "foot_head": foot_head, "toe_head": toe_head, "toe_tail": toe_tail}


def measure_hands(mesh, rig):
    """The palm direction and the new tail, per side, from the mesh alone.

    Anchored at the rig's **own** wrist, because the wrist is not what is wrong:
    the rule being exercised is the direction and the length, and anchoring it
    here is what lets the test prove the wrist did not move.
    """
    from forge.tools import rigforge_landmarks as L

    out = {}
    matrix = mesh.matrix_world
    groups = L._deform_groups(rig, mesh)
    for side in ("L", "R"):
        name = "DEF-hand.%s" % side
        bone = rig.data.bones[name]
        parent = bone.parent
        wrist = rig.matrix_world @ bone.head_local
        arm = ((rig.matrix_world @ parent.tail_local)
               - (rig.matrix_world @ parent.head_local)).normalized()
        wanted = {index for index, owner in groups.items()
                  if owner in (name, parent.name)}
        points = []
        for vertex in mesh.data.vertices:
            for entry in vertex.groups:
                if entry.group in wanted and entry.weight >= 0.2:
                    points.append(matrix @ vertex.co)
                    break
        palm = L.hand_axis(points, wrist, arm, label=name)
        band = max(palm["reach"] / float(L.HAND_STATIONS - 1), 1e-6) * 0.75
        tail = L._hand_section_centre(palm["region"], wrist, palm["direction"],
                                      L.HAND_KNUCKLE_FRACTION * palm["reach"], band)
        out[side] = {"wrist": wrist, "tail": tail, "palm": palm}
    return out


def measure_feet(mesh, rig):
    """The ankle, ball and toe tip, per side, from the leg chain's own flesh."""
    from forge.tools import rigforge_landmarks as L, rigforge_rig as R

    forward, _how = R.rig_forward_axis(rig)
    matrix = mesh.matrix_world
    groups = L._deform_groups(rig, mesh)
    out = {}
    for side in ("L", "R"):
        # ...by the side the NAME carries, not by its suffix: a Rigify shin is
        # "DEF-shin.L.001" and endswith(".L") throws half the leg column away.
        names = {name for name in groups.values()
                 if L._side_of(name)[1] == side
                 and ("shin" in name or "foot" in name or "toe" in name)}
        wanted = {index for index, owner in groups.items() if owner in names}
        points = []
        for vertex in mesh.data.vertices:
            for entry in vertex.groups:
                if entry.group in wanted and entry.weight >= 0.2:
                    points.append(matrix @ vertex.co)
                    break
        foot_bone = rig.data.bones["DEF-foot.%s" % side]
        # The knee hint is the shin segment the foot actually hangs off, which is
        # what the gate uses too: on a Rigify leg that is DEF-shin.L.001, and
        # taking DEF-shin.L instead moves the search band by 8 cm.
        shin = foot_bone.parent or rig.data.bones["DEF-shin.%s" % side]
        out[side] = L.foot_landmarks(points,
                                     rig.matrix_world @ foot_bone.head_local,
                                     rig.matrix_world @ shin.head_local,
                                     forward, label="leg.%s" % side)
    return out


def apply_fix(rig, hands, feet):
    """Put the measured answers on the armature, in edit mode, headless."""
    inverse = rig.matrix_world.inverted()
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit = rig.data.edit_bones
        for side, entry in hands.items():
            for name in ("DEF-hand.%s" % side, "ORG-hand.%s" % side,
                         "hand_fk.%s" % side, "hand_ik.%s" % side):
                bone = edit.get(name)
                if bone is None:
                    continue
                bone.head = inverse @ entry["wrist"]
                bone.tail = inverse @ entry["tail"]
        for side, foot in feet.items():
            ankle = inverse @ Vector(foot["ankle"])
            ball = inverse @ Vector(foot["ball"])
            tip = inverse @ Vector(foot["toe_tip"])
            for name in ("DEF-shin.%s.001" % side, "ORG-shin.%s" % side,
                         "shin_fk.%s" % side, "MCH-shin_ik.%s" % side):
                bone = edit.get(name)
                if bone is not None:
                    bone.tail = ankle
            for name in ("DEF-foot.%s" % side, "ORG-foot.%s" % side,
                         "foot_fk.%s" % side, "foot_ik.%s" % side):
                bone = edit.get(name)
                if bone is not None:
                    bone.head = ankle
                    bone.tail = ball
            for name in ("DEF-toe.%s" % side, "ORG-toe.%s" % side,
                         "toe.%s" % side):
                bone = edit.get(name)
                if bone is not None:
                    bone.head = ball
                    bone.tail = tip
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def test_the_rules_measure_both_sides(mesh, rig, before):
    section("the rules, measured per side with no symmetry assumed")
    from forge.tools import rigforge_landmarks as L

    hands = measure_hands(mesh, rig)
    feet = measure_feet(mesh, rig)
    for side in ("L", "R"):
        palm = hands[side]["palm"]
        note("hand.%s palm axis %s, %.1f deg off the forearm, reach %.1f mm, "
             "%d vertices, converged %s"
             % (side, [round(v, 3) for v in palm["direction"]],
                palm["arm_angle_deg"], palm["reach"] * 1000.0, palm["points"],
                ", ".join("%.1f" % t for t in palm["turns_deg"]) or "0"))
        check("hand.%s's palm axis is measured from its own side's flesh" % side,
              palm["points"] > 40 and palm["reach"] > 0.05,
              "%d points, %.1f mm reach" % (palm["points"], palm["reach"] * 1000.0))
        check("hand.%s now points DOWN, the way the hand hangs" % side,
              palm["direction"].z < -0.7,
              str([round(v, 3) for v in palm["direction"]]))
        check("...and it is a long way off the arm's outward direction, which is "
              "the defect it replaces (hand.%s)" % side,
              palm["arm_angle_deg"] > 20.0, "%.1f deg" % palm["arm_angle_deg"])

    left = hands["L"]["palm"]["direction"]
    right = hands["R"]["palm"]["direction"]
    mirrored = Vector((-right.x, right.y, right.z))
    spread = math.degrees(left.angle(mirrored, 0.0))
    note("the two sides were computed independently and land %.1f degrees apart "
         "once mirrored -- a measurement, not an assumption" % spread)
    check("neither side was assumed from the other", spread > 0.0,
          "%.3f degrees" % spread)

    for side in ("L", "R"):
        foot = feet[side]
        check("the %s leg still reads as having a foot" % side, foot is not None,
              str(foot))
        if foot is None:
            continue
        note("leg.%s ankle %.1f mm above the sole (%s); toe %.1f mm; foot %.1f mm long"
             % (side, foot["ankle_height_mm"], foot["detail"]["ankle"]["how"],
                foot["toe_height_mm"], foot["length_mm"]))
        check("the %s ankle came off the forward-reach collapse, not an argmin" % side,
              "collapses" in foot["detail"]["ankle"]["how"],
              foot["detail"]["ankle"]["how"])
        check("...and it dropped a long way from the 173 mm it was at (%s)" % side,
              foot["ankle_height_mm"] < 140.0,
              "%.1f mm above the sole" % foot["ankle_height_mm"])
        check("the %s toe now runs just above the sole, not 79 mm up" % side,
              foot["toe_height_mm"] < 45.0,
              "%.1f mm above the sole" % foot["toe_height_mm"])
        check("...derived from the foot's own local thickness, not a constant (%s)"
              % side, "thickness above the sole" in foot["detail"]["ball"]["how"],
              foot["detail"]["ball"]["how"])
    return hands, feet


def test_the_sole_fit_is_untouched(feet, before):
    section("the sole fit the feet already had: forward and back, still the same fit")
    from forge.tools import rigforge_landmarks as L

    # The sole slab is a fraction of the foot's own height, and the foot's height
    # is the ankle's -- so bringing a 173 mm ankle down to 105 makes the slab
    # 32 mm thick instead of 52, which is the number that constant always meant.
    # The boot's toe curves up, so a thinner sole reads 9.6 mm shorter. That is
    # the ankle fix arriving, not the sole fit moving, and the tolerance here is
    # the margin the toe bone already carries.
    for side in ("L", "R"):
        foot = feet[side]
        if foot is None:
            continue
        tip = Vector(foot["toe_tip"])
        heel = Vector(foot["heel"])
        note("leg.%s toe tip y = %.4f, heel y = %.4f, ball at %.3f of the sole, "
             "foot %.1f mm long (was 337.8 with the old 52 mm sole slab)"
             % (side, tip.y, heel.y, foot["ball_fraction"], foot["length_mm"]))
        check("the %s sole still measures the same foot, front to back" % side,
              abs(foot["length_mm"] - 337.78) < L.FOOT_TIP_MARGIN_MM,
              "%s mm against 337.8" % foot["length_mm"])
        check("...and the ball is still where the sole's taper puts it (%s)" % side,
              abs(foot["ball_fraction"] - 0.688) < 0.02,
              str(foot["ball_fraction"]))
        check("...and the heel is still at ground level (%s)" % side,
              abs(heel.z - foot["ground"]) < 1e-9, "%.5f" % heel.z)
    old_tip = before["toe_tail"]
    new_tip = Vector(feet["L"]["toe_tip"])
    forward_mm = abs(new_tip.y - old_tip.y) * 1000.0
    height_mm = abs(new_tip.z - old_tip.z) * 1000.0
    note("the toe tip moved %.1f mm along the facing axis and %.1f mm in height"
         % (forward_mm, height_mm))
    check("THE TOE TIP'S FORWARD REACH BARELY MOVED: the height is what changed",
          forward_mm < L.FOOT_TIP_MARGIN_MM and height_mm > forward_mm * 5.0,
          "%.1f mm forward against %.1f mm down" % (forward_mm, height_mm))
    check("...and it still stops short of the boot rather than through it",
          new_tip.y > old_tip.y, "%.4f vs %.4f" % (new_tip.y, old_tip.y))


def test_the_gates_go_green(mesh, rig, before):
    section("after the fix: the bones, and both gates")
    from forge.tools import rigcheck

    for side in ("L", "R"):
        head, tail = bone_world(rig, "DEF-hand.%s" % side)
        moved = (head - before["wrists"][side]).length * 1000.0
        note("DEF-hand.%s head (%.4f, %.4f, %.4f) tail (%.4f, %.4f, %.4f) "
             "direction %s"
             % (side, head.x, head.y, head.z, tail.x, tail.y, tail.z,
                [round(v, 3) for v in (tail - head).normalized()]))
        check("THE WRIST DID NOT MOVE (hand.%s)" % side, moved < 0.001,
              "%.4f mm" % moved)
        check("...and the hand bone now points down the palm (hand.%s)" % side,
              (tail - head).normalized().z < -0.7,
              str([round(v, 3) for v in (tail - head).normalized()]))

    foot_head, foot_tail = bone_world(rig, "DEF-foot.L")
    toe_head, toe_tail = bone_world(rig, "DEF-toe.L")
    note("DEF-foot.L head z = %.4f m (was 0.1730), DEF-toe.L z = %.4f m (was 0.0786)"
         % (foot_head.z, toe_head.z))
    shin_head, shin_tail = bone_world(rig, "DEF-shin.L.001")
    check("the shin still ends exactly where the foot begins: one chain, no gap",
          (shin_tail - foot_head).length < 1e-6,
          "%.4f mm" % ((shin_tail - foot_head).length * 1000.0))

    hands = rigcheck.hand_containment(rig, mesh)
    feet = rigcheck.foot_height(rig, mesh)
    note("hand_containment: %s" % hands["says"])
    note("foot_height: %s" % feet["says"])
    for row in hands["hands"]:
        note("  %-14s angle %6.1f deg  tail outside %6.1f mm  worst %6.1f mm"
             % (row["bone"], row.get("angle_deg") or 0.0,
                row.get("tail_outside_mm") or 0.0,
                row.get("worst_outside_mm") or 0.0))
    for row in feet["feet"]:
        note("  side %s  ankle %6.1f mm (mesh says %6.1f)  toe %6.1f mm (mesh says "
             "%6.1f)" % (row["side"], row.get("ankle_height_mm") or 0.0,
                         row.get("ankle_target_mm") or 0.0,
                         row.get("toe_height_mm") or 0.0,
                         row.get("toe_target_mm") or 0.0))

    check("THE HAND GATE GOES GREEN ON THE AFTER STATE",
          hands["verdict"] == "ok", "%s: %s" % (hands["verdict"], hands["says"]))
    check("...on BOTH hands", all(row.get("verdict") == "ok"
                                  for row in hands["hands"]),
          str([(r["bone"], r.get("verdict")) for r in hands["hands"]]))
    check("...and every tail is inside its own palm now",
          all((row.get("tail_outside_mm") or 0.0) <= rigcheck.HAND_CONTAINMENT_MM
              for row in hands["hands"]),
          str([(r["bone"], r.get("tail_outside_mm")) for r in hands["hands"]]))
    check("THE FOOT GATE GOES GREEN ON THE AFTER STATE",
          feet["verdict"] == "ok", "%s: %s" % (feet["verdict"], feet["says"]))
    check("...on both feet", all(row.get("verdict") == "ok" for row in feet["feet"]),
          str([(r["side"], r.get("verdict")) for r in feet["feet"]]))

    worst_after = max((row.get("worst_outside_mm") or 0.0)
                      for row in hands["hands"])
    note("the hand bones' worst excursion went from %.1f mm to %.1f mm; the ankle's "
         "error from %.1f mm to %.1f mm"
         % (before["worst_hand_outside_mm"], worst_after,
            before["worst_ankle_error_mm"],
            max((row.get("ankle_error_mm") or 0.0) for row in feet["feet"])))
    check("the hand numbers improved, and by a lot",
          worst_after < before["worst_hand_outside_mm"] * 0.5,
          "%.1f -> %.1f mm" % (before["worst_hand_outside_mm"], worst_after))


def test_the_hands_and_feet_do_not_interfere(mesh, rig):
    section("the two fixes are independent: neither moved the other's numbers")
    from forge.tools import rigcheck

    hands = rigcheck.hand_containment(rig, mesh)
    feet = rigcheck.foot_height(rig, mesh)
    check("the hand gate still measures both hands after the foot chain moved",
          hands["measured"] == 2, str(hands["measured"]))
    check("the foot gate still measures both feet after the hands moved",
          feet["measured"] == 2, str(feet["measured"]))
    check("...and the sole fit's own numbers are the same on both sides",
          len({row.get("ankle_target_mm") for row in feet["feet"]}) == 1,
          str([row.get("ankle_target_mm") for row in feet["feet"]]))


def main():
    print("Forge headless: the hand's direction and the foot chain's height")
    enable_addon()
    if not os.path.exists(SOURCE_BLEND):
        check("the werewolf blend is where the suite expects it", False, SOURCE_BLEND)
        print("\n%d checks, 1 failed" % len(_RESULTS))
        print("RESULT: FAILURES")
        sys.exit(1)

    workspace = tempfile.mkdtemp(prefix="forge_handfoot_test_")
    try:
        test_palm_axis_on_a_bent_arm()
        test_the_palm_axis_is_stable_on_a_tiny_hand()
        test_the_mitten_still_measures_what_it_measured()
        test_the_booted_leg_still_fits_its_shoe()
        test_a_bare_leg_still_gets_no_foot()

        section("the live character, on a copy in %s" % workspace)
        mesh, rig = open_copy(workspace)
        note("%s: %d vertices; %s: %d bones"
             % (mesh.name, len(mesh.data.vertices), rig.name, len(rig.data.bones)))
        before = test_the_before_numbers(rig)
        hands, feet = test_the_rules_measure_both_sides(mesh, rig, before)
        test_the_sole_fit_is_untouched(feet, before)
        apply_fix(rig, hands, feet)
        test_the_gates_go_green(mesh, rig, before)
        test_the_hands_and_feet_do_not_interfere(mesh, rig)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
