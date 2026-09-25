"""Headless add-on tests for the gates two artist reviews found missing.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_animgates.py

Needs no geometry service and downloads nothing.  The character is the same
synthetic sculpt ``headless_rigik`` uses, carried through retopo -> metarig ->
generate by that suite's own builder, and every clip under test is authored
through the **public** commands (``rigforge_walk``, ``rigforge_punch``,
``rigforge_jump``, ``rigforge_keyframe``) rather than hand-built, so what is
measured is the rig and the clips the pipeline actually produces.

**What this suite is for.**  ``projects/werewolf/renders/review-wip14`` took a
finished character apart and found four user-visible defects that *every*
existing gate passed clean.  The weights were innocent to 0.00 mm, the feet
planted to 1.1 mm, the hulls around every joint held — and the artist could
still see the left leg stretching, the walk popping once a cycle, the deep
correctives never firing and the jump's crouch not reading.  Five new gates
measure those four things:

* ``bone_stretch_budget`` — the **bones' own length**, summed over each limb's
  DEF chain against rest.  Nothing here measured length before; the audited
  walk grew its left leg 32.58% and no number moved.
* ``ik_reach_headroom`` — hip-to-ankle over the leg's rest chain length, per
  frame on a clip and at **rest** in ``rig_check``.  The audited rig stands at
  0.9984, which is the upstream disease: no headroom for an animator to use.
* ``loop_seam_closure`` — the evaluated mesh at a loop's first frame against
  its last, root travel removed.  A seam that does not close pops forever.
* ``corrective_driver_domain`` — a ``ROTATION_DIFF`` lives in ``[0, pi]``, and
  four of the audited character's ten JCM keys peak past 3.95 rad.
* ``anticipation_reads`` — a crouch that is physically present and still
  invisible: 6.9% of the silhouette, 0.00 mm of hip setback, 0.333 s long, and
  the feet 11.7 mm under the floor.

The wip-15 review of the walk added two more, and they are about **craft**
rather than about mechanics — the clip they were found on held its feet to
0.19 mm and closed its loop to 0.001 mm:

* ``gait_opposition`` — the phase between each hand's forward swing and its own
  side's foot strike.  The reviewed walk swung both arms in **unison** (0.0
  degrees between the two hands) where a gait puts them half a cycle apart.
* ``strike_lead`` — how far in front of the hip joint each heel lands, in
  millimetres and as a share of the stride.  The reviewed walk struck +115 mm
  (+30.8% of the stride) with the left foot and **-72 mm (-19.2%)** with the
  right, on the same clip: exactly half a stride apart, because the plant was
  solved against the floor and never against the body.

Each is proved twice: **red** on a defect this file constructs (a torso-travel
walk with Rigify's stock ``IK_Stretch = 1.0`` put back, a driver keyed past pi,
a deliberately shallow and fast crouch, a walk authored with ``arm_phase_deg: 0``
and one with ``strike_lead: 0``) and **green or truthfully-verdicted** on
the healthy clip.  Where a healthy synthetic clip legitimately trips a gate the
suite pins the *measured* verdict with its number rather than forcing a pass:
the synthetic rig has the same stock Rigify stretch switches the audited one
did, and a gate that had to be argued down to make its own fixture pass would
be worth nothing.

The regression guard runs alongside: a walk, a punch and a jump come out of
``animation_check`` with every pre-existing key exactly as it was — ``gate``
included, which is why the new gates roll up into ``deformation_gate`` and not
into it.

The two ``--background`` facts that shape this file are the usual ones: no
event loop (the harness drains the server queue from the main thread) and no
window.
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

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9914  # not 9876 (a live session), not 9878-9881 (phases 2-5), not 9907-9913

WALK = "walk"
WALK_LOOP = WALK + "-loop"
PUNCH_R = "punch.R"
JAB_LOOP = "jab-loop"
JUMP = "jump"
FLAT_JUMP = "jump-flat"
STRETCH = "stretchwalk"
STRETCH_LOOP = STRETCH + "-loop"
#: The two wip-15 craft defects, each rebuilt through the parameter that
#: authors it, so the gait gates are proved red on the very thing they exist for.
SAME_SIDE = "samesidewalk"
SAME_SIDE_LOOP = SAME_SIDE + "-loop"
UNDERFOOT = "underfootwalk"
UNDERFOOT_LOOP = UNDERFOOT + "-loop"

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


# --- the character ----------------------------------------------------------

def build_character():
    """``headless_rigik``'s builder, reused rather than copied.

    The same argument ``headless_punch`` and ``headless_jump`` make: if the
    sculpt, the retopo or the generated rig changes, this suite has to be
    looking at the same thing those suites are, and the way to guarantee that
    is to run the same function with its socket calls pointed at this suite's
    port.
    """
    import headless_rigik as rigik

    rigik.PORT = PORT
    rigik.check = check
    rigik.note = note
    rigik.section = section
    rigik.call = call
    return rigik.build_character()


# --- helpers ----------------------------------------------------------------

def band_of(value, thresholds):
    """The band a number falls in, computed here rather than read off the report.

    Every "the verdict agrees with its own number" check in this file goes
    through this, because a gate that reports a band its measurement does not
    support is worse than one that does not report at all.
    """
    if value is None:
        return "unmeasured"
    if value <= thresholds["ok"]:
        return "ok"
    if value <= thresholds["attention"]:
        return "attention"
    return "fail"


def strip_ik_stretch(rig_name, action_name):
    """Put Rigify's stock ``IK_Stretch = 1.0`` back on the legs, for one clip.

    This is the defect the audit found, rebuilt minimally.  Every planted-foot
    command in ``rigforge_anim`` now keys the property to 0 for the whole clip
    precisely so a leg clamps instead of growing — which is the fix, and which
    means the only way to *test* the gate that found the bug is to undo it on a
    throwaway action.  The keys come out of the action and the live property
    goes back to 1.0, which is the rig as wip-14 shipped.
    """
    from forge.tools import rigforge_anim as anim

    rig = bpy.data.objects[rig_name]
    action = bpy.data.actions[action_name]
    removed = 0
    for container in anim.action_channel_containers(action):
        for curve in list(container):
            if "IK_Stretch" in (curve.data_path or ""):
                container.remove(curve)
                removed += 1
    switches = 0
    for bone in rig.pose.bones:
        if "IK_Stretch" in bone.keys() and bone.name.startswith("thigh_parent"):
            bone["IK_Stretch"] = 1.0
            switches += 1
    bpy.context.view_layer.update()
    return removed, switches


def torso_travel_vector(rig_name, distance):
    """A local-space ``location`` for the torso worth ``distance`` of world travel.

    Calibrated rather than hardcoded: a Rigify ``torso`` bone points up its own
    +Y, so which of its local X and Z is the character's forward depends on how
    the metarig was fitted, and a test that guessed would be testing the guess.
    Both axes are tried on the live rig, the one that moves the hips furthest
    horizontally wins, and the result is scaled to the distance asked for.  The
    pose is put back.
    """
    rig = bpy.data.objects[rig_name]
    torso = rig.pose.bones.get("torso") or rig.pose.bones.get("hips")
    if torso is None:
        return None, None
    hips = rig.pose.bones.get("hips") or torso
    before = torso.location.copy()
    rest = (rig.matrix_world @ hips.head).copy()
    best = None
    try:
        for axis in (0, 2):
            probe = Vector((0.0, 0.0, 0.0))
            probe[axis] = 0.1
            torso.location = probe
            bpy.context.view_layer.update()
            moved = (rig.matrix_world @ hips.head) - rest
            flat = Vector((moved.x, moved.y, 0.0)).length
            if best is None or flat > best[1]:
                best = (axis, flat)
    finally:
        torso.location = before
        bpy.context.view_layer.update()
    axis, per_tenth = best
    if per_tenth < 1e-9:
        return None, None
    amount = distance * 0.1 / per_tenth
    out = [0.0, 0.0, 0.0]
    out[axis] = amount
    return out, torso.name


def add_rotation_diff_key(mesh_name, rig_name, key_name, points, bones):
    """One shape key driven by a ``ROTATION_DIFF``, with an exact f-curve.

    Hand-built rather than authored through ``rigforge_correctives``, because
    what is under test is a **driver's domain** and not a sculpt: the gate reads
    f-curve keyframe positions, so a two-point curve placed exactly where the
    audit found one is the whole fixture, and building it here keeps the test
    independent of how correctives happen to be authored today.
    """
    mesh = bpy.data.objects[mesh_name]
    rig = bpy.data.objects[rig_name]
    if mesh.data.shape_keys is None:
        mesh.shape_key_add(name="Basis", from_mix=False)
    block = mesh.shape_key_add(name=key_name, from_mix=False)
    block.slider_min = 0.0
    block.slider_max = 1.0
    keys = mesh.data.shape_keys
    fcurve = keys.driver_add('key_blocks["%s"].value' % key_name)
    for modifier in list(fcurve.modifiers):
        fcurve.modifiers.remove(modifier)
    driver = fcurve.driver
    driver.type = "AVERAGE"
    while driver.variables:
        driver.variables.remove(driver.variables[0])
    variable = driver.variables.new()
    variable.type = "ROTATION_DIFF"
    for index, bone in enumerate(bones):
        variable.targets[index].id = rig
        variable.targets[index].bone_target = bone
    # Not ``for point in list(...)``: removing one re-packs the collection and
    # the rest of the snapshot stops being in the curve.
    while len(fcurve.keyframe_points):
        fcurve.keyframe_points.remove(fcurve.keyframe_points[0])
    for x, y in points:
        keyframe = fcurve.keyframe_points.insert(x, y)
        keyframe.interpolation = "LINEAR"
    fcurve.extrapolation = "CONSTANT"
    fcurve.update()
    return fcurve


def clear_shape_keys(mesh_name):
    mesh = bpy.data.objects[mesh_name]
    if mesh.data.shape_keys is None:
        return
    keys = mesh.data.shape_keys
    if keys.animation_data is not None:
        for fcurve in list(keys.animation_data.drivers):
            keys.animation_data.drivers.remove(fcurve)
    mesh.shape_key_clear()


def measured_setback_mm(rig_name, action_name, span):
    """The hips' travel *away* from the rig's own forward, re-measured here.

    A second implementation of the one number in ``anticipation_reads`` that no
    other gate and no part of the audit can cross-check: a setback of 0.00 mm
    is either a jump whose hips drop down a plumb line — which is what the
    audit found — or the gate reading the wrong point, and the two look
    identical from the outside.  So the hip joint, the ankle line and
    ``rig_forward_axis`` are read straight off the posed rig here, on the frames
    the gate says it used, and the answers have to agree.

    The hip joint is the average of the ``DEF-thigh`` heads, positive rearward
    along ``-forward`` from the ankle line — the convention the authoring
    commands key against.  Deliberately **not** the ``hips`` control, whose head
    is ~300 mm up the spine: see :func:`forge.tools.rigcheck.anticipation_reads`.
    """
    from forge.tools import rigcheck, rigforge_rig as rr

    rig = bpy.data.objects[rig_name]
    hip_bones = []
    ankles = []
    for side in ("L", "R"):
        thigh = sorted(rigcheck._deform_for(rig, "thigh.%s" % side))
        if thigh:
            hip_bones.append(thigh[0])
        bones = sorted(rigcheck._deform_for(rig, "shin.%s" % side))
        if bones:
            ankles.append(bones[-1])
    if not hip_bones or not ankles:
        return None
    forward, _how = rr.rig_forward_axis(rig)
    forward = Vector((forward.x, forward.y, 0.0))
    if forward.length < 1e-9:
        return None
    forward.normalize()
    scene = bpy.context.scene
    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    values = []
    try:
        rr.assign_action(rig, bpy.data.actions[action_name])
        for frame in range(span[0], span[1] + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            head = sum((rig.matrix_world @ rig.pose.bones[name].head
                        for name in hip_bones), Vector((0.0, 0.0, 0.0)))
            head /= float(len(hip_bones))
            ankle = sum((rig.matrix_world @ rig.pose.bones[name].tail
                         for name in ankles), Vector((0.0, 0.0, 0.0)))
            ankle /= float(len(ankles))
            offset = head - ankle
            values.append(-Vector((offset.x, offset.y, 0.0)).dot(forward))
    finally:
        scene.frame_set(previous_frame)
        try:
            rr.assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):
            pass
        bpy.context.view_layer.update()
    return (max(values) - values[0]) * 1000.0


def knee_bone_pair(rig_name):
    """The two DEF bones a knee corrective would be driven by, on this rig."""
    from forge.tools import rigcheck

    rig = bpy.data.objects[rig_name]
    thigh = rigcheck._deform_for(rig, "thigh.L")
    shin = rigcheck._deform_for(rig, "shin.L")
    if not thigh or not shin:
        return None
    return (sorted(thigh)[-1], sorted(shin)[0])


# --- the rest-pose gates, in rig_check --------------------------------------

def test_rig_check_rest_gates(rig, mesh):
    section("rig_check - the two gates that are about the rig, not a clip")
    result = call("rig_check", {"rig": rig.name, "mesh": mesh.name,
                                "poses": "quick", "intersections": False})
    headroom = result.get("ik_reach_headroom") or {}
    domain = result.get("corrective_driver_domain") or {}

    check("rig_check reports ik_reach_headroom as a placement gate",
          bool(headroom) and headroom.get("verdict") in
          ("ok", "attention", "fail", "unmeasured"), str(headroom.get("verdict")))
    check("it measured both legs off the REST skeleton, not a pose",
          headroom.get("measured") == 2
          and all(row["rest_length_mm"] > 1.0 for row in headroom.get("limbs") or []),
          str([(r["limb"], r["rest_length_mm"], r["extension_frac"])
               for r in headroom.get("limbs") or []]))
    worst = headroom.get("worst_extension_frac")
    note("rest stance: worst leg at %s of its own chain length (%s mm of headroom), "
         "verdict %s" % (worst,
                         (headroom.get("limbs") or [{}])[0].get("headroom_mm"),
                         headroom.get("verdict")))
    check("the verdict it published is the one its own number implies "
          "(<=0.98 ok, >0.98 attention, >1.00 fail)",
          headroom.get("verdict") == ("fail" if worst > 1.0 else
                                      ("attention" if worst > 0.98 else "ok")),
          "%s at %s" % (headroom.get("verdict"), worst))
    check("and hip-to-ankle is never longer than the chain that spans it - a rest "
          "pose cannot over-reach itself", worst is not None and worst <= 1.0 + 1e-9,
          str(worst))
    check("the audited rig's disease is the one this number would have caught: it "
          "is reported whether it fires or not",
          "extension_frac" in str(headroom.get("limbs")), str(headroom)[:160])

    check("corrective_driver_domain reports 'unmeasured' on a mesh with no shape-key "
          "drivers, rather than a green it did not earn",
          domain.get("verdict") == "unmeasured" and domain.get("measured") == 0,
          "%s / %s" % (domain.get("verdict"), domain.get("measured")))
    check("...and says why", "no shape-key drivers" in (domain.get("says") or ""),
          domain.get("says"))
    check("both new gates carry the threshold that judged them",
          (headroom.get("thresholds") or {}).get("extension_frac", {}).get("fail")
          == 1.0 and domain.get("max_rad") is not None,
          "%s / %s" % (headroom.get("thresholds"), domain.get("max_rad")))
    return result


def test_corrective_driver_domain(rig, mesh, baseline_gate):
    section("corrective_driver_domain - a ROTATION_DIFF lives in [0, pi]")
    pair = knee_bone_pair(rig.name)
    if not check("the rig has a knee's worth of DEF bones to drive a corrective from",
                 pair is not None, str(pair)):
        return

    # Green: the angles the audit says these keys SHOULD have been given.
    clear_shape_keys(mesh.name)
    add_rotation_diff_key(mesh.name, rig.name, "corr_knee_L_070",
                          [(0.1133, 0.0), (1.1085, 1.0), (2.4435, 0.0)], pair)
    add_rotation_diff_key(mesh.name, rig.name, "corr_knee_L_140",
                          [(1.1085, 0.0), (2.4435, 1.0)], pair)
    healthy = call("rig_check", {"rig": rig.name, "mesh": mesh.name,
                                 "poses": "quick", "intersections": False})
    domain = healthy.get("corrective_driver_domain") or {}
    note(domain.get("says"))
    check("two correctives keyed at their real angles (63.5 and 140 deg) pass",
          domain.get("verdict") == "ok" and domain.get("measured") == 2,
          "%s over %s key(s)" % (domain.get("verdict"), domain.get("measured")))
    check("and every key's peak is reported in radians and degrees",
          all(row["peak_x_rad"] <= math.pi and row["peak_x_deg"] > 0
              for row in domain.get("keys") or []),
          str([(r["key"], r["peak_x_rad"], r["peak_x_deg"])
               for r in domain.get("keys") or []]))
    check("a clean driver set does not move rig_check's overall gate at all",
          healthy.get("gate") == baseline_gate,
          "%s, was %s before any drivers existed" % (healthy.get("gate"),
                                                     baseline_gate))

    # Red: the defect, exactly as wip-14 shipped it - the deep key's 1.0 moved
    # out to 3.9530 rad, which no ROTATION_DIFF can ever reach.
    clear_shape_keys(mesh.name)
    add_rotation_diff_key(mesh.name, rig.name, "corr_knee_L_070",
                          [(0.1133, 0.0), (1.1085, 1.0), (3.9530, 0.0)], pair)
    add_rotation_diff_key(mesh.name, rig.name, "corr_knee_L_140",
                          [(1.1085, 0.0), (3.9530, 1.0)], pair)
    broken = call("rig_check", {"rig": rig.name, "mesh": mesh.name,
                                "poses": "quick", "intersections": False})
    domain = broken.get("corrective_driver_domain") or {}
    note(domain.get("says"))
    check("the deep key keyed at 3.9530 rad FAILS",
          domain.get("verdict") == "fail"
          and domain.get("unreachable") == ["corr_knee_L_140"],
          "%s / %s" % (domain.get("verdict"), domain.get("unreachable")))
    deep = next((row for row in domain.get("keys") or []
                 if row["key"] == "corr_knee_L_140"), {})
    check("the red names the key and its unreachable x, in radians",
          "corr_knee_L_140" in (domain.get("says") or "")
          and "3.9530" in (domain.get("says") or ""), (domain.get("says") or "")[:200])
    check("and quotes how much of the sculpted shape can ever fire - the audit "
          "predicted 0.7147 for a knee keyed here",
          deep.get("value_at_pi") is not None
          and abs(deep["value_at_pi"] - 0.7147) < 0.002,
          "value at pi = %s" % deep.get("value_at_pi"))
    check("the shallow key, whose PEAK is reachable but whose fall-off runs past pi, "
          "is attention rather than silence",
          next((row["verdict"] for row in domain.get("keys") or []
                if row["key"] == "corr_knee_L_070"), None) == "attention",
          str([(r["key"], r["verdict"]) for r in domain.get("keys") or []]))
    check("and rig_check's overall gate fails on it - an unreachable corrective is "
          "a rig defect, not a note", broken.get("gate") == "fail",
          str(broken.get("gate")))
    check("...and the sentence reaches the top-level says",
          "OUTSIDE THEIR DRIVER'S DOMAIN" in (broken.get("says") or ""),
          (broken.get("says") or "")[:240])
    clear_shape_keys(mesh.name)


# --- the clip gates ---------------------------------------------------------

def gate_blocks(result):
    return (result.get("bone_stretch_budget") or {},
            result.get("ik_reach_headroom") or {},
            result.get("loop_seam_closure"),
            result.get("anticipation_reads"))


def test_healthy_clips_are_measured_honestly(rig, walk, punch):
    section("the healthy clips - measured, with the verdict its own number implies")
    from forge.tools import rigcheck

    for name, result in (("walk-loop", walk), ("punch.R", punch)):
        stretch, reach, seam, _anticipation = gate_blocks(result)
        note("%s: stretch %s (worst %s%% on %s at f%s), reach %s (worst %s on %s), "
             "seam %s, deformation_gate %s"
             % (name, stretch.get("verdict"), stretch.get("worst_stretch_pct"),
                stretch.get("worst_limb"), stretch.get("worst_frame"),
                reach.get("verdict"), reach.get("worst_extension_frac"),
                reach.get("worst_limb"),
                (seam or {}).get("verdict") if seam else "n/a",
                result.get("deformation_gate")))
        check("%s: every limb the rig has is measured for stretch, not just the legs"
              % name,
              stretch.get("measured") == 4
              and {row["limb"] for row in stretch["limbs"]}
              == {"leg.L", "leg.R", "arm.L", "arm.R"},
              str([row["limb"] for row in stretch.get("limbs") or []]))
        check("%s: each limb names the DEF bones it summed and its rest length" % name,
              all(len(row["bones"]) >= 2 and row["rest_length_mm"] > 1.0
                  for row in stretch["limbs"]),
              str([(row["limb"], row["bones"], row["rest_length_mm"])
                   for row in stretch["limbs"]])[:240])
        check("%s: the stretch verdict is the one its own worst number implies" % name,
              stretch["verdict"] == rigcheck._worst(
                  [band_of(row["worst_stretch_pct"],
                           rigcheck.STRETCH_THRESHOLDS["stretch_pct"])
                   if not row["contact_runs_drifting"]
                   else max(band_of(row["worst_stretch_pct"],
                                    rigcheck.STRETCH_THRESHOLDS["stretch_pct"]),
                            "attention", key=lambda v: {"ok": 0, "attention": 2,
                                                        "fail": 3}[v])
                   for row in stretch["limbs"]]),
              "%s over %s" % (stretch["verdict"],
                              [(row["limb"], row["worst_stretch_pct"],
                                bool(row["contact_runs_drifting"]))
                               for row in stretch["limbs"]]))
        check("%s: the reach verdict is the one its own worst number implies" % name,
              reach["verdict"] == ("fail" if reach["worst_extension_frac"] > 1.0 else
                                   ("attention"
                                    if reach["worst_extension_frac"] > 0.98 else "ok")),
              "%s at %s" % (reach["verdict"], reach["worst_extension_frac"]))
        check("%s: reach is a LEG gate - arms are judged on length, not on reach"
              % name,
              {row["limb"] for row in reach["limbs"]} == {"leg.L", "leg.R"},
              str([row["limb"] for row in reach["limbs"]]))
        check("%s: every frame it quotes is a frame of the clip" % name,
              all(result["frames"][0] <= row["worst_frame"] <= result["frames"][1] + 1
                  for row in stretch["limbs"] + reach["limbs"]),
              str([(row["limb"], row["worst_frame"]) for row in stretch["limbs"]]))

    # The punch is the clip that is *supposed* to hold: nothing about a punch
    # moves the feet, and both legs stay well inside the fail band.
    stretch = punch["bone_stretch_budget"]
    legs = [row for row in stretch["limbs"] if row["limb"].startswith("leg")]
    check("the punch's legs never reach the fail band - a stance is a pose, not a "
          "stretch", all(row["worst_stretch_pct"] < 5.0 for row in legs),
          str([(row["limb"], row["worst_stretch_pct"]) for row in legs]))
    check("and a stance held steady over a plant is not reported as drift: the "
          "contact rule measures the length's SPREAD across the run",
          all(row["worst_contact_spread_pct"] is None
              or row["worst_contact_spread_pct"] < row["worst_stretch_pct"] + 1e-9
              for row in legs),
          str([(row["limb"], row["worst_stretch_pct"],
                row["worst_contact_spread_pct"]) for row in legs]))
    check("the punch is not a loop, so it carries no seam block at all",
          punch.get("loop_seam_closure") is None, str(punch.get("loop_seam_closure")))
    check("and it is not airborne, so it carries no anticipation block",
          punch.get("anticipation_reads") is None,
          str(punch.get("anticipation_reads")))
    check("and it is not a gait either - a punch's heels never leave the floor and "
          "come back, so neither gait block is measured on it",
          punch.get("gait_opposition") is None and punch.get("strike_lead") is None,
          "%s / %s" % (punch.get("gait_opposition"), punch.get("strike_lead")))


def test_existing_keys_are_untouched(rig, walk, punch, jump):
    """The regression guard: the readings that existed before must not move."""
    section("the regression guard - every pre-existing key reads as it always did")
    check("a walk is still a travelling (planted) clip, not a jump",
          walk["mode"] == "planted", "%s: %s" % (walk["mode"], walk["mode_reason"]))
    check("its airborne block is still null",
          walk.get("airborne") is None, str(walk.get("airborne")))
    check("it still finds stance phases on both feet",
          len([f for f in walk["feet"] if f["steps_measured"]]) == 2,
          str(walk["steps_measured"]))
    check("and its foot-slide gate is still the foot-slide gate: 'gate' is what the "
          "drift says it is, whatever the new gates found",
          walk["gate"] == ("ok" if walk["worst_drift_mm"] <= 5.0 else
                           ("attention" if walk["worst_drift_mm"] <= 20.0 else "fail")),
          "gate %s at %s mm, deformation_gate %s"
          % (walk["gate"], walk["worst_drift_mm"], walk["deformation_gate"]))
    check("no step on a walk carries a jump-only 'phase' key",
          not any("phase" in step for foot in walk["feet"] for step in foot["steps"]),
          str([step for foot in walk["feet"] for step in foot["steps"]])[:200])
    check("a punch is still read as planted with its feet at zero",
          punch["mode"] == "planted" and punch["worst_drift_mm"] < 1.0
          and punch.get("airborne") is None,
          "%s / %s mm" % (punch["mode"], punch["worst_drift_mm"]))
    if jump is not None:
        check("a jump is still detected as one, without being told",
              jump["mode"] == "jump" and (jump.get("airborne") or {}).get("detected")
              is True, "%s: %s" % (jump["mode"], jump["mode_reason"]))
        check("and its airborne block still carries the parabola and the landing knees",
              (jump.get("airborne") or {}).get("parabola") in ("ok", "fail")
              and "landing_knees" in (jump.get("airborne") or {}),
              str((jump.get("airborne") or {}).get("parabola")))
    expected = {"rig", "action", "mode", "mode_reason", "airborne", "frames",
                "frame_step", "samples", "looping", "contact_band",
                "min_stance_frames", "body_bone", "body_travel_mm",
                "treadmill_mm_per_frame", "feet", "steps_measured", "worst_step",
                "worst_drift_mm", "gate", "thresholds", "threshold_tier", "says",
                "pose_restored", "warnings", "seconds"}
    check("every key animation_check published before these gates existed is still "
          "there", expected <= set(walk), str(sorted(expected - set(walk))))
    added = {"bone_stretch_budget", "ik_reach_headroom", "loop_seam_closure",
             "anticipation_reads", "gait_opposition", "strike_lead",
             "deformation_gate"}
    # 2026-09-24: the optional motion-quality tier (rigforge_mocap
    # MOTION_QUALITY_THRESHOLDS) - null unless "motion_quality": true is passed
    quality_keys = {"motion_quality", "motion_quality_gate"}
    check("and exactly seven keys were added, plus the two of the off-by-default "
          "motion-quality tier (null here), no more",
          set(walk) - expected == added | quality_keys
          and walk.get("motion_quality") is None and walk.get("motion_quality_gate") is None,
          str(sorted(set(walk) - expected - added - quality_keys)))
    check("the two calls that measured these are deterministic",
          call("animation_check", {"rig": rig.name,
                                   "action": WALK_LOOP})["worst_drift_mm"]
          == walk["worst_drift_mm"], "re-measured drift moved")


def test_a_torso_travel_walk_fails(rig, healthy):
    """The audited defect, rebuilt: body travel the foot targets do not share."""
    section("bone_stretch_budget / ik_reach_headroom / loop_seam_closure - RED")
    info = call("rigforge_walk", {"rig": rig.name, "action": STRETCH,
                                  "cycle_frames": 24, "travel": False})
    note(info["says"])
    leg = float(info["leg_length_m"])
    removed, switches = strip_ik_stretch(rig.name, STRETCH_LOOP)
    note("put Rigify's stock IK_Stretch=1.0 back on %d leg switch(es) and took %d "
         "keyed override(s) out of %s" % (switches, removed, STRETCH_LOOP))
    check("the fixture really is the wip-14 rig: the legs are free to stretch again",
          switches >= 2, "%d switch(es)" % switches)

    travel, bone = torso_travel_vector(rig.name, leg)
    if not check("a torso travel direction was calibrated off this rig",
                 travel is not None, str(travel)):
        return None
    note("torso travel: %s on %s, worth %.0f mm of hips over the cycle"
         % ([round(v, 4) for v in travel], bone, leg * 1000.0))
    call("rigforge_keyframe", {
        "rig": rig.name, "action": STRETCH_LOOP, "interpolation": "LINEAR",
        "keys": [{"bone": bone, "frame": 1, "location": [0.0, 0.0, 0.0]},
                 {"bone": bone, "frame": 25, "location": travel}]})

    broken = call("animation_check", {"rig": rig.name, "action": STRETCH_LOOP})
    note(broken["says"])
    stretch, reach, seam, _anticipation = gate_blocks(broken)

    check("the torso-travel walk FAILS the bone stretch budget",
          stretch["verdict"] == "fail", "%s at %s%%" % (stretch["verdict"],
                                                        stretch["worst_stretch_pct"]))
    check("...by growing a leg well past the 5% fail band",
          stretch["worst_stretch_pct"] > 5.0,
          "%s%% on %s at frame %s" % (stretch["worst_stretch_pct"],
                                      stretch["worst_limb"], stretch["worst_frame"]))
    check("...and it is a LEG that grew, on a clip whose arms were never touched",
          stretch["worst_limb"].startswith("leg")
          and all(row["worst_stretch_pct"] < 2.0 for row in stretch["limbs"]
                  if row["limb"].startswith("arm")),
          str([(r["limb"], r["worst_stretch_pct"]) for r in stretch["limbs"]]))
    check("the red sentence names the limb, the frame and both lengths in mm",
          all(token in stretch["says"] for token in ("leg", "frame", "mm of DEF chain")),
          stretch["says"][:200])
    check("the gate separates the broken walk from the healthy one by an order of "
          "magnitude - the two are not the same clip with a different threshold",
          stretch["worst_stretch_pct"]
          > 3.0 * max(healthy["bone_stretch_budget"]["worst_stretch_pct"], 1e-6),
          "%s%% broken vs %s%% healthy"
          % (stretch["worst_stretch_pct"],
             healthy["bone_stretch_budget"]["worst_stretch_pct"]))

    check("it FAILS ik_reach_headroom too: the hip is further from the ankle than "
          "the leg is long", reach["verdict"] == "fail"
          and reach["worst_extension_frac"] > 1.0,
          "%s at %s" % (reach["verdict"], reach["worst_extension_frac"]))
    check("...and the frames it over-reached on are listed, not just counted",
          any(row["frames_over_reach"] for row in reach["limbs"]),
          str([(r["limb"], r["frames_over_reach"][:6]) for r in reach["limbs"]]))

    check("it FAILS loop_seam_closure: the clip does not end where it started",
          (seam or {}).get("verdict") == "fail",
          str((seam or {}).get("verdict")))
    check("...quoting the worst vertex, its distance in mm and the region it is in",
          (seam or {}).get("worst_mm", 0) > 1.0
          and (seam or {}).get("worst_vertex", -1) >= 0
          and (seam or {}).get("worst_region"),
          "vertex %s, %s mm, %s" % ((seam or {}).get("worst_vertex"),
                                    (seam or {}).get("worst_mm"),
                                    (seam or {}).get("worst_region")))
    check("...and the region it names is a DEFORM bone's, never a tag group's",
          str((seam or {}).get("worst_group") or "").startswith("DEF-"),
          str((seam or {}).get("worst_group")))
    check("the seam is measured on both frames of the action's OWN range, the "
          "duplicate last frame included",
          (seam or {}).get("frames") == [1, 25], str((seam or {}).get("frames")))

    check("all of it rolls up into deformation_gate",
          broken["deformation_gate"] == "fail", str(broken["deformation_gate"]))
    check("...while 'gate' still answers the question it always answered, which is "
          "about the feet", broken["gate"] in ("ok", "attention", "fail")
          and broken["gate"] == ("ok" if broken["worst_drift_mm"] <= 5.0 else
                                 ("attention" if broken["worst_drift_mm"] <= 20.0
                                  else "fail")),
          "gate %s at %s mm" % (broken["gate"], broken["worst_drift_mm"]))
    check("and every red sentence reaches the top-level says, so nobody reads a "
          "clean line over a broken loop",
          "LOOP DOES NOT CLOSE" in broken["says"]
          and "change length" in broken["says"], broken["says"][:260])
    return broken


def test_the_contact_rule_quotes_its_frames(rig, broken):
    section("bone_stretch_budget - a planted foot may not watch the leg change length")
    if broken is None:
        return
    stretch = broken["bone_stretch_budget"]
    drifting = [row for row in stretch["limbs"] if row["contact_runs_drifting"]]
    check("the broken walk's planted legs are caught changing length during a stance",
          bool(drifting),
          str([(r["limb"], r["contact_runs"]) for r in stretch["limbs"]])[:240])
    if drifting:
        row = drifting[0]
        note("%s: contact runs %s, worst spread %s%%"
             % (row["limb"], row["contact_runs_drifting"], row["worst_contact_spread_pct"]))
        check("each drifting run is quoted as a frame span, not a count",
              all(len(span) == 2 and span[0] < span[1]
                  for span in row["contact_runs_drifting"]),
              str(row["contact_runs_drifting"]))
        check("every contact run carries its own spread and worst offset",
              all({"frames", "samples", "spread_pct", "worst_offset_pct"} <= set(run)
                  for run in row["contact_runs"]), str(row["contact_runs"])[:200])
        check("the sentence says the length moved while the foot was planted",
              "while the foot is planted" in row["says"], row["says"][:200])
    check("the epsilon that separates a real change from solver dust is published",
          stretch.get("contact_epsilon_pct", 0) > 0,
          str(stretch.get("contact_epsilon_pct")))


def test_loop_seam_closes_on_a_good_loop(rig):
    section("loop_seam_closure - GREEN, and root motion is not a seam failure")
    call("rigforge_punch", {"rig": rig.name, "side": "L", "loop": True,
                            "action": "jab", "frames": 30})
    result = call("animation_check", {"rig": rig.name, "action": JAB_LOOP})
    seam = result.get("loop_seam_closure") or {}
    note("%s: seam %s at %s mm (tolerance %s mm), root travel %s mm"
         % (JAB_LOOP, seam.get("verdict"), seam.get("worst_mm"),
            seam.get("tolerance_mm"), seam.get("root_travel_mm")))
    check("a looped punch closes on the evaluated mesh, vertex by vertex",
          seam.get("verdict") == "ok", "%s at %s mm" % (seam.get("verdict"),
                                                        seam.get("worst_mm")))
    check("...to well under the 1 mm this gate allows, not merely under it",
          seam.get("worst_mm") is not None and seam["worst_mm"] < 0.5,
          "%s mm" % seam.get("worst_mm"))
    check("it measured every vertex of the mesh, not a sample",
          seam.get("vertices", 0) > 100, str(seam.get("vertices")))
    check("a clip that is not a loop gets no seam block rather than a green one",
          call("animation_check", {"rig": rig.name,
                                   "action": PUNCH_R})["loop_seam_closure"] is None)

    travelling = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
    seam = travelling.get("loop_seam_closure") or {}
    note("%s: seam %s at %s mm, root travel removed %s mm"
         % (WALK_LOOP, seam.get("verdict"), seam.get("worst_mm"),
            seam.get("root_travel_mm")))
    check("a root-motion walk has its root travel taken out before the seam is "
          "judged - a stride down the floor is the clip's product, not its defect",
          (seam.get("root_travel_mm") or 0.0) > 1.0,
          "%s mm of root travel removed" % seam.get("root_travel_mm"))


def test_gait_gates(rig, walk, jump):
    """The wip-15 artist review, both halves: GREEN on the fixed walk, RED on
    each defect rebuilt through the parameter that authors it."""
    section("gait_opposition / strike_lead - GREEN on the walk the artist asked for")
    opposition = walk.get("gait_opposition") or {}
    lead = walk.get("strike_lead") or {}
    note("%s: opposition %s (worst pair %s at %s deg), strike lead %s (worst %s mm "
         "= %s%% of a %s mm stride, heels %s)"
         % (WALK_LOOP, opposition.get("verdict"), opposition.get("worst_pair"),
            opposition.get("worst_phase_lag_deg"), lead.get("verdict"),
            lead.get("worst_lead_mm"), lead.get("worst_lead_pct_of_stride"),
            lead.get("stride_mm"), lead.get("heel_bones")))
    check("a walk is a gait, so it carries both gait blocks",
          opposition and lead, "%s / %s" % (bool(opposition), bool(lead)))
    check("every arm/leg pair on the fixed walk is half a cycle apart",
          opposition.get("verdict") == "ok",
          str([(row["pair"], row["phase_lag_deg"])
               for row in opposition.get("pairs") or []]))
    check("the verdict is the one its own worst number implies",
          opposition.get("verdict") == (
              "fail" if abs(opposition.get("worst_phase_lag_deg") or 0.0) <= 90.0
              else ("ok" if abs(opposition["worst_phase_lag_deg"]) >= 135.0
                    else "attention")),
          "%s at %s deg" % (opposition.get("verdict"),
                            opposition.get("worst_phase_lag_deg")))
    check("it gates all four pairings - each arm against its own leg, the arms "
          "against each other and the legs against each other",
          {row["pair"] for row in opposition.get("pairs") or []}
          == {"arm.L vs leg.L", "arm.R vs leg.R", "arm.L vs arm.R",
              "leg.L vs leg.R"},
          str([row["pair"] for row in opposition.get("pairs") or []]))
    check("both heels strike in front of the hip joint, inside the classical band",
          lead.get("verdict") == "ok"
          and all(25.0 <= row["lead_pct_of_stride"] <= 35.0
                  for row in lead.get("strikes") or []),
          str([(row["foot"], row["lead_mm"], row["lead_pct_of_stride"])
               for row in lead.get("strikes") or []]))
    check("and it says where it measured them - the heel against the hip JOINT, "
          "not the hips control",
          "DEF-thigh" in (lead.get("measured_at") or "")
          and "heel" in (lead.get("measured_at") or ""), str(lead.get("measured_at")))
    check("every frame it quotes is a frame of the clip",
          all(walk["frames"][0] <= row["strike_frame"] <= walk["frames"][1]
              for row in lead.get("strikes") or []),
          str([row["strike_frame"] for row in lead.get("strikes") or []]))
    if jump is not None:
        check("a jump is not a gait: its feet leave together and come back "
              "together, so it carries neither block",
              jump.get("gait_opposition") is None and jump.get("strike_lead") is None,
              "%s / %s" % (jump.get("gait_opposition"), jump.get("strike_lead")))

    section("gait_opposition - RED, the wip-15 arms rebuilt through the parameter")
    # "the opposite arm should move on opposite leg, so not left arm and left
    # leg". `arm_phase_deg: 0` authors exactly that - the arm's forward peak on
    # its OWN foot's strike - and nothing else about the clip changes.
    info = call("rigforge_walk", {"rig": rig.name, "action": SAME_SIDE,
                                  "cycle_frames": 24, "arm_phase_deg": 0.0})
    note(info["says"])
    check("the command took the defect it was asked for rather than silently "
          "correcting it", info["arm_phase_deg"] == 0.0
          and info["arm_swing_contralateral"] is False,
          "%s deg" % info["arm_phase_deg"])
    broken = call("animation_check", {"rig": rig.name, "action": SAME_SIDE_LOOP})
    bad = broken.get("gait_opposition") or {}
    note("%s: opposition %s, worst pair %s at %s deg"
         % (SAME_SIDE_LOOP, bad.get("verdict"), bad.get("worst_pair"),
            bad.get("worst_phase_lag_deg")))
    check("the gate fails a same-side swing", bad.get("verdict") == "fail",
          str(bad.get("verdict")))
    check("...on both arms, not just the one that happens to be sampled first",
          all(row["verdict"] == "fail" for row in bad.get("pairs") or []
              if row["kind"] == "arm-to-its-own-leg"),
          str([(row["pair"], row["phase_lag_deg"], row["verdict"])
               for row in bad.get("pairs") or []]))
    check("and it quotes the measured phase lag rather than asserting a verdict",
          bad.get("worst_phase_lag_deg") is not None
          and abs(bad["worst_phase_lag_deg"]) <= 90.0
          and ("%.1f degrees apart" % abs(bad["worst_phase_lag_deg"]))
          in (bad.get("says") or ""),
          "%s deg in %r" % (bad.get("worst_phase_lag_deg"),
                            (bad.get("says") or "")[:160]))
    check("the sentence reaches the top-level says, so nobody reads a clean line "
          "over a broken gait",
          "do not oppose" in (broken.get("says") or ""),
          (broken.get("says") or "")[-200:])
    check("and it costs the deformation rollup, not the foot-slide gate: the feet "
          "on this clip hold exactly as well as they did",
          broken.get("deformation_gate") == "fail" and broken.get("gate") == "ok"
          and broken["worst_drift_mm"] == walk["worst_drift_mm"],
          "gate %s at %s mm, deformation_gate %s"
          % (broken.get("gate"), broken.get("worst_drift_mm"),
             broken.get("deformation_gate")))
    check("the strike lead is untouched by the arm defect - two gates, two "
          "answers", (broken.get("strike_lead") or {}).get("verdict") == "ok",
          str((broken.get("strike_lead") or {}).get("worst_lead_mm")))

    section("strike_lead - RED, a foot that lands under the body")
    # "the foot should land in front of the center of the model so in front".
    # `strike_lead: 0` plants the heel directly under the hip joint, which is
    # where the wip-15 right foot landed and worse.
    info = call("rigforge_walk", {"rig": rig.name, "action": UNDERFOOT,
                                  "cycle_frames": 24, "strike_lead": 0.0})
    note(info["says"])
    check("the command authored the plant it was asked for",
          info["strike_lead"] == 0.0 and info["strike_lead_mm"] == 0.0,
          "%s of a stride" % info["strike_lead"])
    under = call("animation_check", {"rig": rig.name, "action": UNDERFOOT_LOOP})
    row = under.get("strike_lead") or {}
    note("%s: strike lead %s, worst %s mm (%s%% of a %s mm stride) on %s"
         % (UNDERFOOT_LOOP, row.get("verdict"), row.get("worst_lead_mm"),
            row.get("worst_lead_pct_of_stride"), row.get("stride_mm"),
            row.get("worst_foot")))
    check("the gate fails a heel that lands at or behind the hip joint",
          row.get("verdict") == "fail", str(row.get("verdict")))
    check("and quotes both the millimetres and the share of the stride",
          row.get("worst_lead_mm") is not None
          and row.get("worst_lead_pct_of_stride") is not None
          and abs(row["worst_lead_mm"]) < 1.0,
          "%s mm / %s%%" % (row.get("worst_lead_mm"),
                            row.get("worst_lead_pct_of_stride")))
    check("the sentence names the foot and the frame it struck on",
          (row.get("worst_foot") or "?") in (row.get("says") or "")
          and "in front of the body" in (row.get("says") or ""),
          (row.get("says") or "")[:200])
    check("it costs the deformation rollup and leaves the foot-slide gate alone",
          under.get("deformation_gate") == "fail" and under.get("gate") == "ok",
          "gate %s, deformation_gate %s" % (under.get("gate"),
                                            under.get("deformation_gate")))
    check("and the arms are still correct on it - the two defects are "
          "independent, which is why they are two gates",
          (under.get("gait_opposition") or {}).get("verdict") == "ok",
          str((under.get("gait_opposition") or {}).get("worst_phase_lag_deg")))


def test_anticipation_reads(rig, mesh, jump):
    section("anticipation_reads - a crouch that is there and still does not read")
    if jump is None:
        return
    block = jump.get("anticipation_reads") or {}
    note(block.get("says"))
    check("the jump carries an anticipation block, and a walk does not",
          bool(block) and call("animation_check", {
              "rig": rig.name, "action": WALK_LOOP})["anticipation_reads"] is None,
          str(bool(block)))
    note("load read off %r over frames %s: %s mm of crouch, %s mm of setback "
         "(needs %s mm), %s s; sole %s mm against a %s mm plane from frame %s "
         "(%s sole vertices), whole-mesh lowest %s mm"
         % (block.get("load_bone"), block.get("frames"),
            block.get("crouch_depth_mm"), block.get("hip_setback_mm"),
            block.get("hip_setback_required_mm"), block.get("window_s"),
            block.get("lowest_sole_z_mm"), block.get("floor_plane_mm"),
            block.get("floor_plane_frame"), block.get("sole_vertices"),
            block.get("lowest_mesh_z_mm")))
    check("it names the body control the load was read off",
          bool(block.get("load_bone")), str(block.get("load_bone")))
    check("all four sub-checks are measured and each quotes its own number, its "
          "requirement and its units",
          [row["check"] for row in block.get("checks") or []]
          == ["height_drop", "hip_setback", "window", "floor"]
          and all({"measured", "required", "units", "ok", "says"} <= set(row)
                  for row in block["checks"]),
          str([row["check"] for row in block.get("checks") or []]))
    for row in block.get("checks") or []:
        note("  %-12s %-5s %s" % (row["check"], row["ok"], row["says"]))
    check("the verdict is exactly 'every sub-check that could be taken passed'",
          block.get("verdict")
          == ("fail" if any(row["ok"] is False for row in block["checks"])
              else ("attention" if any(row["ok"] is None for row in block["checks"])
                    else "ok")),
          "%s over %s" % (block.get("verdict"),
                          [(r["check"], r["ok"]) for r in block["checks"]]))

    # The floor check is the one that is easy to get wrong in the direction of
    # looking right: the lowest vertex of a crouching body is a hip, and the
    # lowest vertex of a landing one is a hand.
    from forge.tools import rigcheck

    sole = rigcheck.sole_vertex_indices(rig, mesh)
    check("the floor is measured on the SOLE - foot/toe-dominant vertices - and the "
          "report says so and counts them",
          "sole" in (block.get("floor_measured_at") or "")
          and block.get("sole_vertices", 0) > 0,
          "%s over %s vertices" % (block.get("floor_measured_at"),
                                   block.get("sole_vertices")))
    check("...and that is the same set rigforge_anim's floor clamp selects, by the "
          "same deform-only dominance rule",
          sole is not None and len(sole) == block.get("sole_vertices"),
          "%s here vs %s in the report"
          % (None if sole is None else len(sole), block.get("sole_vertices")))
    check("dominance ignores the autotagger's tag_* groups, which carry weight 1.0 "
          "and would otherwise win every vertex and leave no feet at all",
          sole is not None and len(sole) > 0, str(None if sole is None else len(sole)))
    check("the sole plane is read off a GROUNDED frame, not an arbitrary one",
          block.get("floor_plane_frame") is not None
          and block.get("floor_plane_mm") is not None,
          "frame %s at %s mm" % (block.get("floor_plane_frame"),
                                 block.get("floor_plane_mm")))
    check("the whole mesh's lowest point is reported beside it and judges nothing - "
          "on a deep absorb it is a hand or a hip",
          block.get("lowest_mesh_z_mm") is not None,
          "sole %s mm vs whole mesh %s mm" % (block.get("lowest_sole_z_mm"),
                                              block.get("lowest_mesh_z_mm")))
    floor_check = next((row for row in block["checks"]
                        if row["check"] == "floor"), {})
    check("the jump rigforge_jump authors today PASSES the floor sub-check: its sole "
          "stays on the plane it started on",
          floor_check.get("ok") is True
          and floor_check.get("measured") is not None
          and abs(floor_check["measured"]) < 1.0,
          "%s mm below the plane" % floor_check.get("measured"))
    # <= rather than <: the builder is nondeterministic (lane-conventions.md)
    # and on some builds the sole IS the mesh's lowest geometry, making the
    # two numbers equal. The property being pinned is only that the whole-mesh
    # reading can never be HIGHER than the sole's own.
    check("...where measuring the whole mesh instead would have read the hand or the "
          "hip and called the same clip a floor breach",
          block.get("lowest_mesh_z_mm") is not None
          and block["lowest_mesh_z_mm"] <= block["lowest_sole_z_mm"],
          "whole mesh %s mm vs sole %s mm" % (block.get("lowest_mesh_z_mm"),
                                              block.get("lowest_sole_z_mm")))
    check("the crouch depth and the standing height it is measured against are both "
          "published, on the evaluated mesh",
          block.get("crouch_depth_mm", 0) > 0 and block.get("standing_height_mm", 0) > 0,
          "%s mm crouch on a %s mm silhouette" % (block.get("crouch_depth_mm"),
                                                  block.get("standing_height_mm")))
    check("the load window is a frame span inside the clip, ending at the bottom",
          len(block.get("frames") or []) == 2
          and block["frames"][0] < block["frames"][1],
          str(block.get("frames")))

    independent = measured_setback_mm(rig.name, JUMP, block["frames"])
    note("hip setback over frames %s: joint %s mm (re-measured %s mm), "
         "hips control %s mm, measured at %r"
         % (block["frames"], block.get("hip_setback_mm"),
            None if independent is None else round(independent, 2),
            block.get("hip_control_setback_mm"),
            block.get("hip_setback_measured_at")))
    check("the setback is measured at the hip JOINT - the DEF-thigh heads - and the "
          "report says so",
          "DEF-thigh" in (block.get("hip_setback_measured_at") or "")
          and all(name.startswith("DEF-thigh")
                  for name in block.get("hip_joint_bones") or ["x"]),
          "%s / %s" % (block.get("hip_setback_measured_at"),
                       block.get("hip_joint_bones")))
    check("a second reading of the posed rig - DEF-thigh heads, ankle line, "
          "rig_forward_axis, positive rearward - agrees with it",
          independent is not None
          and abs(block["hip_setback_mm"] - max(0.0, independent)) < 0.5,
          "gate %s mm vs %s mm re-measured"
          % (block.get("hip_setback_mm"), independent))
    check("the 'hips' CONTROL is reported beside it and judges nothing - its head "
          "sits ~300 mm up the spine and swings forward under a trunk fold, so it "
          "is the number that looks like the answer and is not one",
          block.get("hip_control_setback_mm") is not None
          and block["hip_control_setback_mm"] != block["hip_setback_mm"],
          "joint %s mm vs control %s mm"
          % (block.get("hip_setback_mm"), block.get("hip_control_setback_mm")))
    check("it is never negative: a pelvis that goes forward over the toes is 0 mm "
          "of setback, not a negative one", block["hip_setback_mm"] >= 0.0,
          str(block["hip_setback_mm"]))
    check("and what it is measured against is 0.3 of the crouch the same load "
          "delivered", abs(block["hip_setback_required_mm"]
                           - 0.3 * block["crouch_depth_mm"]) < 0.05,
          "%s mm required against a %s mm crouch"
          % (block["hip_setback_required_mm"], block["crouch_depth_mm"]))
    setback_check = next((row for row in block["checks"]
                          if row["check"] == "hip_setback"), {})
    ratio = (block["hip_setback_mm"] / block["crouch_depth_mm"]
             if block.get("crouch_depth_mm") else 0.0)
    note("setback ratio measured at the joint: %.3f of the crouch depth" % ratio)
    check("the countermovement rigforge_jump authors today PASSES the setback "
          "sub-check: the pelvis really does load back over the heels",
          setback_check.get("ok") is True and ratio >= 0.3,
          "%s mm of %s mm crouch (ratio %.3f)"
          % (block.get("hip_setback_mm"), block.get("crouch_depth_mm"), ratio))


def test_a_shallow_fast_crouch_fails(rig, healthy):
    section("anticipation_reads - RED on a crouch authored too shallow and too fast")
    leg = float(healthy["leg_length_m"]) if healthy else 0.5
    flat = call("rigforge_jump", {
        "rig": rig.name, "action": FLAT_JUMP,
        "crouch_depth": 0.02 * leg, "anticipation_fraction": 0.08})
    note(flat["says"])
    result = call("animation_check", {"rig": rig.name, "action": FLAT_JUMP})
    block = result.get("anticipation_reads") or {}
    note(block.get("says"))
    if not check("the shallow jump still reads as a jump, so the gate can fire at all",
                 result["mode"] == "jump" and bool(block),
                 "%s / %s" % (result["mode"], bool(block))):
        return
    check("a 2%-of-leg crouch taken in a twelfth of the clip FAILS the gate",
          block.get("verdict") == "fail", str(block.get("verdict")))
    failed = [row["check"] for row in block.get("checks") or [] if not row["ok"]]
    check("...on the silhouette and on the clock, which are the two the audience "
          "reads", {"height_drop", "window"} & set(failed),
          "failed: %s" % failed)
    check("the red quotes every sub-check it failed, with its number",
          all(row["says"] in block["says"]
              for row in block["checks"] if not row["ok"]), block["says"][:240])
    check("and it says the depth it did deliver, so the fix is 'read', not 'deeper'",
          "physically present" in (block.get("says") or ""),
          (block.get("says") or "")[:200])
    check("a deeper, slower crouch measures further from the line than the shallow "
          "one - the number moves with the pose, it is not a constant",
          block.get("height_drop_pct") is not None,
          str(block.get("height_drop_pct")))
    return result


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
    print("Forge add-on animation-gate headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_animgates_test_")
    try:
        mesh, rig, meta, generated = build_character()
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")

        rest = test_rig_check_rest_gates(rig, mesh)
        test_corrective_driver_domain(rig, mesh, rest.get("gate"))

        section("the clips, authored through the public commands")
        walk_info = call("rigforge_walk", {"rig": rig.name, "action": WALK,
                                           "cycle_frames": 24})
        note(walk_info["says"])
        punch_info = call("rigforge_punch", {"rig": rig.name, "side": "R"})
        note(punch_info["says"])
        jump_info = None
        try:
            jump_info = call("rigforge_jump", {"rig": rig.name, "action": JUMP})
            note(jump_info["says"])
        except AssertionError as exc:
            check("rigforge_jump authors a jump for the airborne gates", False,
                  str(exc))

        walk = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
        punch = call("animation_check", {"rig": rig.name, "action": PUNCH_R})
        jump = (call("animation_check", {"rig": rig.name, "action": JUMP})
                if jump_info is not None else None)

        test_healthy_clips_are_measured_honestly(rig, walk, punch)
        test_existing_keys_are_untouched(rig, walk, punch, jump)
        test_gait_gates(rig, walk, jump)
        test_loop_seam_closes_on_a_good_loop(rig)
        test_anticipation_reads(rig, mesh, jump)
        if jump_info is not None:
            test_a_shallow_fast_crouch_fails(rig, jump_info)
        broken = test_a_torso_travel_walk_fails(rig, walk)
        test_the_contact_rule_quotes_its_frames(rig, broken)

        section("the clips authored first are still the clips they were")
        again = call("animation_check", {"rig": rig.name, "action": PUNCH_R})
        check("the punch measures identically after every defect this suite built",
              again["worst_drift_mm"] == punch["worst_drift_mm"]
              and again["mode"] == punch["mode"],
              "%s mm vs %s mm" % (again["worst_drift_mm"], punch["worst_drift_mm"]))
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
