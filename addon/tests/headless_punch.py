"""Headless add-on tests for ``rigforge_punch`` — a punch on planted feet.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_punch.py

Needs no geometry service and downloads nothing.  The character is the same
synthetic sculpt ``headless_rigik`` uses, carried through retopo -> metarig ->
generate by that suite's own builder, so what is under test is the rig the
pipeline actually produces rather than a hand-built skeleton that agrees with
the test.

**What this suite is for.**  ``rigforge_walk`` proved that a clip whose feet are
keyed on the IK targets does not slide.  A punch is where that claim gets its
second, harder test: nothing about a punch is *supposed* to move the feet, and
yet the hips turn 22 degrees and travel 30 mm over them.  A pelvis keyed
without planted feet drags the whole stance with it and nobody notices, because
there is no stride to compare it against.  So:

* both feet measure **zero** drift across the whole clip, both sides, through
  ``animation_check``;
* the fist lands on its target, and **never** exceeds the arm's measured reach —
  a hyperextended elbow is the arm's version of foot slide, and it is measured
  here on the deform bones, frame by frame, not asserted;
* the rotation really does travel up the body: the pelvis's peak comes before
  the chest's, the chest's before the shoulder's, the shoulder's before the
  fist's full extension;
* authoring a punch does not eat the walk that was already in the file;
* out-of-range parameters are refused with a sentence naming the bound;
* authoring the same punch twice produces byte-identical keyframes.

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

PORT = 9908  # not 9876 (a live session), not 9878-9881 (phases 2-5), not 9907

WALK = "walk"
WALK_LOOP = WALK + "-loop"
PUNCH_R = "punch.R"
PUNCH_L = "punch.L"

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

    If the sculpt, the retopo or the generated rig changes, this suite has to
    be looking at the same thing that suite is; the way to guarantee that is to
    run the same function, with its socket calls pointed at this suite's port
    and its checks counted in this suite's tally.
    """
    import headless_rigik as rigik

    rigik.PORT = PORT
    rigik.check = check
    rigik.note = note
    rigik.section = section
    rigik.call = call
    return rigik.build_character()


# --- helpers ----------------------------------------------------------------

def action_bones(name):
    from forge.tools import rigforge_rig as rr

    action = bpy.data.actions.get(name)
    if action is None:
        return set()
    names = set()
    for curve in rr.action_fcurves(action):
        if curve.data_path.startswith('pose.bones["'):
            names.add(curve.data_path.split('"')[1])
    return names


def action_signature(name):
    """Every keyframe of an action as plain numbers — what determinism means here."""
    from forge.tools import rigforge_rig as rr

    action = bpy.data.actions.get(name)
    if action is None:
        return None
    out = []
    for curve in rr.action_fcurves(action):
        out.append((curve.data_path, int(curve.array_index),
                    tuple((round(float(point.co.x), 6), round(float(point.co.y), 9))
                          for point in curve.keyframe_points)))
    return sorted(out)


def world_head(rig, name):
    return rig.matrix_world @ rig.pose.bones[name].head


def sample_clip(rig, action_name, bones):
    """``{bone: [world head per frame]}`` over the whole action, pose restored."""
    from forge.tools import rigforge_rig as rr

    action = bpy.data.actions[action_name]
    scene = bpy.context.scene
    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    span = action.frame_range
    frames = list(range(int(math.floor(span[0])), int(math.ceil(span[1])) + 1))
    out = {name: [] for name in bones}
    try:
        rr.assign_action(rig, action)
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            for name in bones:
                out[name].append(world_head(rig, name).copy())
    finally:
        scene.frame_set(previous_frame)
        try:
            rr.assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):
            pass
        bpy.context.view_layer.update()
    return frames, out


def distance_to_segment(point, start, end):
    line = end - start
    length = line.length
    if length < 1e-9:
        return (point - start).length
    t = max(0.0, min(1.0, (point - start).dot(line) / (length * length)))
    return (point - (start + line * t)).length


# --- authoring --------------------------------------------------------------

def test_punch_authors(rig, side, action_name):
    section("rigforge_punch - the %s hand" % {"L": "left", "R": "right"}[side])
    result = call("rigforge_punch", {"rig": rig.name, "side": side})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    note(result["says"])

    check("the action is named for the side it punches with",
          result["action"] == action_name and result["side"] == side,
          "%s / %s" % (result["action"], result["side"]))
    check("it is a one-shot, not a loop", result["loop"] is False, str(result["loop"]))
    check("it names both feet as the ones it planted",
          sorted(result["feet_planted"]) == ["foot_ik.L", "foot_ik.R"],
          str(result["feet_planted"]))

    bones = action_bones(action_name)
    check("both feet are keyed on their IK targets",
          {"foot_ik.L", "foot_ik.R"} <= bones, str(sorted(bones)))
    check("NOT on the FK leg chain - a pelvis that turns over FK legs drags the feet "
          "with it", not ({"thigh_fk.L", "thigh_fk.R", "shin_fk.L", "shin_fk.R"}
                          & bones),
          str(sorted(name for name in bones if "_fk" in name)))
    check("the fist is keyed on the arm IK target, so its path is a straight line "
          "rather than an FK arc", "hand_ik.%s" % side in bones, str(sorted(bones)))
    check("the off hand holds guard on its own IK target",
          "hand_ik.%s" % ("R" if side == "L" else "L") in bones, str(sorted(bones)))
    check("the hips, the chest and the shoulder all turn",
          {"hips", "chest", "shoulder.%s" % side} <= bones, str(sorted(bones)))
    check("the head is keyed, so the chest cannot swing it off the target",
          "head" in bones, str(sorted(bones)))
    check("the FK/IK switches are keyframed, so the export bake resolves what was "
          "authored", {"thigh_parent.L", "thigh_parent.R",
                       "upper_arm_parent.%s" % side} <= bones, str(sorted(bones)))
    return result


def test_defaults_are_derived(rig, result):
    section("every default is a fraction of this rig, not a constant in metres")
    reach = result["arm_reach_m"]
    check("the arm's reach was measured off the rig (upper arm + forearm at rest)",
          0.05 < reach < 3.0, "%.4f m" % reach)
    check("the target sits at shoulder height by default, derived rather than given",
          abs(result["target_height_m"]
              - (result["target"][2])) < 1e-6, str(result["target"]))
    check("the default target is inside the arm, not beyond it",
          result["target_reach_clamped"] is False
          and result["extension_within_cap"] is True,
          "%.3f of reach (cap %.2f)" % (result["extension_ratio"],
                                        result["max_extension_ratio"]))
    check("the guard is a fraction of reach above and in front of the shoulder",
          0.0 < result["guard_rise_m"] < reach
          and 0.0 < result["chamber_draw_m"] < reach,
          "rise %.3f, chamber %.3f" % (result["guard_rise_m"],
                                       result["chamber_draw_m"]))
    check("the weight transfer is a fraction of the leg, and survived the stance "
          "solve", 0.0 < result["weight_shift_m"] < result["leg_length_m"],
          "%.4f m on a %.3f m leg" % (result["weight_shift_m"],
                                      result["leg_length_m"]))
    note("stance: hips lowered %.0f mm (deepened=%s), transfer %.0f mm (clamped=%s)"
         % (result["hip_lower_m"] * 1000.0, result["hip_lower_deepened"],
            result["weight_shift_m"] * 1000.0, result["weight_shift_clamped"]))


def test_feet_never_move(rig, action_name):
    section("the feet: %s" % action_name)
    result = call("animation_check", {"rig": rig.name, "action": action_name})
    note(result["says"])
    note("mode %s (%s)" % (result["mode"], result["mode_reason"]))
    check("%s passes the foot-slide gate" % action_name, result["gate"] == "ok",
          "%s (%s mm)" % (result["gate"], result["worst_drift_mm"]))
    check("and the worst planted step drifts essentially zero, not merely 'under the "
          "threshold'", (result["worst_drift_mm"] or 0.0) < 1.0,
          "%s mm" % result["worst_drift_mm"])
    check("both feet were measured", len(result["feet"]) == 2
          and all(foot["steps_measured"] for foot in result["feet"]),
          str([(f["bone"], f["steps_measured"]) for f in result["feet"]]))

    # The gate measures stance runs. A punch has no swing phase at all, so the
    # stronger statement is available: measure every frame of the clip.
    frames, tracks = sample_clip(rig, action_name, ("DEF-toe.L", "DEF-toe.R"))
    worst = 0.0
    for name, track in tracks.items():
        first = track[0]
        worst = max(worst, max((point - first).length for point in track))
    check("measured directly, neither ball of the foot moves across any frame of the "
          "clip", worst * 1000.0 < 1.0, "%.4f mm over %d frames"
          % (worst * 1000.0, len(frames)))
    note("whole-clip ball travel: %.4f mm" % (worst * 1000.0))
    return result


def test_legs_cannot_stretch(rig, action_name, report):
    """A punch is the planted-foot clip, so its legs may not change length.

    Rigify ships ``IK_Stretch = 1.0``, which means an IK target the chain
    cannot reach makes the chain *longer* rather than clamping it — measured on
    ``werewolf-wip-14``, 99.84% leg extension at rest and every hip translation
    immediately cashed out in stretched deform bones.  A punch turns the hips
    22 degrees over feet that never move, so it spends the whole clip asking
    the legs that question.  Stretch is a cinematic effect; an authored clip
    with planted feet keys it off.
    """
    section("the legs cannot stretch: %s" % action_name)
    block = report.get("ik_stretch") or {}
    note("keyed %s to %s on %s; restored to %s"
         % (block.get("property"), block.get("keyed_to"), block.get("bones"),
            block.get("restored_to")))
    check("the report carries the ik_stretch block", bool(block), str(block))
    check("both leg switches were keyed to zero for the clip",
          sorted(block.get("bones") or []) == ["thigh_parent.L", "thigh_parent.R"]
          and block.get("keyed_to") == 0.0,
          "%s -> %s" % (block.get("bones"), block.get("keyed_to")))

    from forge.tools import rigforge_rig as rr

    action = bpy.data.actions[action_name]
    curves = [curve for curve in rr.action_fcurves(action)
              if "IK_Stretch" in curve.data_path]
    check("and the channel is in the clip, flat zero, spanning it",
          len(curves) == 2
          and all(point.co.y == 0.0 for curve in curves
                  for point in curve.keyframe_points)
          and all(min(float(p.co.x) for p in curve.keyframe_points)
                  <= float(action.frame_range[0]) + 1e-4
                  and max(float(p.co.x) for p in curve.keyframe_points)
                  >= float(action.frame_range[1]) - 1e-4
                  for curve in curves),
          str([(curve.data_path.split('"')[1],
                [(round(float(p.co.x), 2), round(float(p.co.y), 3))
                 for p in curve.keyframe_points]) for curve in curves]))
    restored = block.get("restored_to") or {}
    check("and the report says what it put the live property back to, so clearing "
          "the clip leaves the rig as it was found",
          sorted(restored) == ["thigh_parent.L", "thigh_parent.R"]
          and all(value > 0.0 for value in restored.values()), str(restored))

    # ...and the thing the property exists to prevent, measured on the deform
    # chain the solver drives rather than on the controls that sit still.
    from forge.tools import rigforge_anim as ra

    limbs = rr.ik_limbs(rig)
    info = ra.locomotion_frame(rig, limbs)
    probe = ra.jump_legs(rig, limbs, info)
    check("there is a deform hip and ankle to measure between", bool(probe),
          str(sorted(probe)))
    if not probe:
        return
    bones = []
    for leg in probe.values():
        bones.extend((leg["hip_bone"], leg["ankle_bone"]))
    frames, tracks = sample_clip(rig, action_name, tuple(sorted(set(bones))))
    worst = 0.0
    for name in sorted(probe):
        leg = probe[name]
        spans = [(tracks[leg["ankle_bone"]][index]
                  - tracks[leg["hip_bone"]][index]).length
                 for index in range(len(frames))]
        growth = max(spans) / leg["reach"] - 1.0
        worst = max(worst, growth)
        note("%s: rest reach %.1f mm, span %.1f-%.1f mm across %d frames -> "
             "%+.3f%% at its longest"
             % (name, leg["reach"] * 1000.0, min(spans) * 1000.0,
                max(spans) * 1000.0, len(frames), growth * 100.0))
    check("no frame of a planted clip asks either leg for more than its own "
          "length", worst <= 0.001, "worst %+.4f%%" % (worst * 100.0))


def test_fist_reaches_without_hyperextending(rig, result, side):
    section("the fist: it arrives, and it never outruns the arm")
    reach = result["arm_reach_m"]
    check("the fist lands on the target at the strike frame, measured on the deform "
          "hand", result["fist_landed_mm"] is not None
          and result["fist_landed_mm"] < max(5.0, 0.02 * reach * 1000.0),
          "%s mm off a %.0f mm target" % (result["fist_landed_mm"], reach * 1000.0))
    check("the arm really is at full extension at the strike - within a few percent "
          "of the plan, and under the hyperextension cap",
          abs(result["extension_m"] - result["extension_planned_m"]) < 0.03 * reach
          and result["extension_within_cap"] is True,
          "%.0f mm measured vs %.0f mm planned of %.0f mm reach (%.1f%%, cap %.0f%%)"
          % (result["extension_m"] * 1000.0, result["extension_planned_m"] * 1000.0,
             reach * 1000.0, result["extension_ratio"] * 100.0,
             result["max_extension_ratio"] * 100.0))
    check("which means the target was solved against the shoulder as it is at impact, "
          "not as it sits at rest",
          result["extension_ratio"] > 0.9 * result["max_extension_ratio"],
          "%.3f of reach" % result["extension_ratio"])

    # Frame by frame, on the deform bones: the shoulder-to-wrist distance is
    # what a hyperextended elbow actually is, and it is measurable.
    names = ("DEF-upper_arm.%s" % side, "DEF-hand.%s" % side, "hand_ik.%s" % side)
    frames, tracks = sample_clip(rig, result["action"], names)
    spans = [(tracks[names[1]][i] - tracks[names[0]][i]).length
             for i in range(len(frames))]
    worst = max(spans)
    cap = result["max_extension_ratio"] * reach
    check("and no frame of the clip puts the wrist further from the shoulder than the "
          "cap", worst <= cap * 1.01 + 1e-4,
          "worst %.1f mm vs cap %.1f mm" % (worst * 1000.0, cap * 1000.0))
    note("shoulder-to-wrist across the clip: %.0f mm min, %.0f mm max, cap %.0f mm"
         % (min(spans) * 1000.0, worst * 1000.0, cap * 1000.0))

    # The drive is a straight line: that is the entire reason the fist is keyed
    # on an IK target and not on two FK rotations.
    target = Vector(result["target"])
    chamber, strike = result["chamber_frame"], result["strike_frame"]
    start = tracks[names[2]][frames.index(chamber)]
    off_line = max(distance_to_segment(tracks[names[2]][frames.index(frame)],
                                       start, target)
                   for frame in range(chamber, strike + 1))
    check("the drive from the chamber to the target is a straight line",
          off_line * 1000.0 < 1.0, "%.4f mm off the line" % (off_line * 1000.0))

    speeds = [0.0] + [(tracks[names[2]][i] - tracks[names[2]][i - 1]).length
                      for i in range(1, len(frames))]
    fastest = frames[speeds.index(max(speeds))]
    check("the report's peak fist speed frame is the one the clip actually has",
          fastest == result["peak_fist_speed_frame"],
          "measured f%d, reported f%s" % (fastest, result["peak_fist_speed_frame"]))
    check("and the fist is fastest inside the drive and before the strike, not on it "
          "- a fist still accelerating at impact is an arm being thrown, not punched",
          chamber < result["peak_fist_speed_frame"] < result["strike_frame"],
          "peak f%s, drive f%d-f%d" % (result["peak_fist_speed_frame"], chamber,
                                       result["strike_frame"]))
    note("peak fist speed %.2f m/s on frame %d of %d"
         % (result["peak_fist_speed_m_per_s"], result["peak_fist_speed_frame"],
            result["frames"]))


def test_rotation_leads(rig, result):
    section("the kinetic chain: pelvis -> chest -> shoulder -> fist")
    lead = result["rotation_lead"]
    order = [lead["pelvis"]["frame"], lead["chest"]["frame"],
             lead["shoulder"]["frame"], result["strike_frame"]]
    note("peaks: pelvis f%s (%s deg), chest f%s (%s deg), shoulder f%s (%s deg), "
         "fist f%d" % (lead["pelvis"]["frame"], lead["pelvis"]["degrees"],
                       lead["chest"]["frame"], lead["chest"]["degrees"],
                       lead["shoulder"]["frame"], lead["shoulder"]["degrees"],
                       result["strike_frame"]))
    check("the pelvis peaks before the chest, the chest before the shoulder, the "
          "shoulder before the fist is out",
          all(order[i] < order[i + 1] for i in range(3)), str(order))
    check("the command says so itself", result["rotation_leads_in_order"] is True,
          str(result["rotation_leads_in_order"]))
    check("each link's rotation is the sum of the ones below it, so the chest turns "
          "further than the pelvis and the shoulder further than the chest",
          lead["pelvis"]["degrees"] < lead["chest"]["degrees"]
          < lead["shoulder"]["degrees"],
          str([lead[k]["degrees"] for k in ("pelvis", "chest", "shoulder")]))
    check("the peak hip rotation is the cap it was given, measured on the posed bone",
          abs(lead["pelvis"]["degrees"] - result["hip_rotation_deg"]) < 0.5,
          "%s deg measured vs %s deg asked"
          % (lead["pelvis"]["degrees"], result["hip_rotation_deg"]))
    check("and the chest's total is under hip + chest, because it peaks while the "
          "pelvis is already unwinding",
          lead["chest"]["degrees"] <= result["hip_rotation_deg"]
          + result["chest_rotation_deg"] + 1e-6,
          "%s deg vs %s + %s" % (lead["chest"]["degrees"],
                                 result["hip_rotation_deg"],
                                 result["chest_rotation_deg"]))


def test_report_quotes_its_numbers(result):
    section("the report says what it did, in numbers")
    for key in ("peak_fist_speed_frame", "peak_fist_speed_m_per_s", "extension_m",
                "extension_ratio", "arm_reach_m", "fist_landed_mm", "strike_frame",
                "chamber_frame", "hip_lower_m", "weight_shift_m"):
        check("report carries %r" % key, result.get(key) is not None,
              str(result.get(key)))
    says = result["says"]
    check("the sentence quotes the peak fist speed", "m/s" in says, says[:160])
    check("the sentence quotes extension against reach",
          "reach" in says and "%" in says, says[:200])
    check("the sentence quotes the hip and chest peaks",
          "pelvis" in says and "chest" in says and "shoulder" in says, says[:240])
    check("and it names the feet it planted", "foot_ik.L" in says and "foot_ik.R"
          in says, says[-120:])


def test_other_actions_survive(rig, walk_before):
    section("authoring a punch does not eat the walk that was already there")
    check("the walk action is still in the file",
          bpy.data.actions.get(WALK_LOOP) is not None,
          str(sorted(a.name for a in bpy.data.actions)))
    check("and every one of its keyframes is untouched",
          action_signature(WALK_LOOP) == walk_before,
          "%d curves before" % len(walk_before or []))
    walk = bpy.data.actions.get(WALK_LOOP)
    check("it keeps a fake user, so unassigning it cannot lose it",
          walk is not None and walk.use_fake_user is True,
          str(walk.use_fake_user if walk else None))
    for name in (PUNCH_L, PUNCH_R):
        action = bpy.data.actions.get(name)
        check("%s is in the file alongside it" % name, action is not None
              and action.use_fake_user is True, str(action))


def test_determinism(rig):
    section("determinism: the same call twice, the same keyframes")
    first = call("rigforge_punch", {"rig": rig.name, "side": "R",
                                    "action": "punch.determinism"})
    before = action_signature("punch.determinism")
    second = call("rigforge_punch", {"rig": rig.name, "side": "R",
                                     "action": "punch.determinism"})
    after = action_signature("punch.determinism")
    check("re-authoring cleared the old curves rather than stacking on them",
          second["cleared_fcurves"] > 0 and second["fcurves"] == first["fcurves"],
          "cleared %d, %d curves vs %d" % (second["cleared_fcurves"],
                                           second["fcurves"], first["fcurves"]))
    differing = []
    for left, right in zip(before or [], after or []):
        if left == right:
            continue
        worst = max((abs(a[1] - b[1]) for a, b in zip(left[2], right[2])), default=0.0)
        differing.append("%s[%d] by %.3g" % (left[0], left[1], worst))
    check("every keyframe value is identical - no noise, no randomness",
          before == after,
          "%d curves, %d differ: %s"
          % (len(before or []), len(differing), "; ".join(differing[:6])))
    for key in ("strike_frame", "peak_fist_speed_frame", "extension_m",
                "hip_lower_m", "weight_shift_m", "target"):
        check("and %r is reported the same both times" % key,
              first[key] == second[key], "%s vs %s" % (first[key], second[key]))


def test_parameters_are_bounded(rig):
    section("the refusals that teach")
    cases = (
        ({"hip_rotation_deg": 500.0}, "hip_rotation_deg", "<="),
        ({"chest_rotation_deg": -5.0}, "chest_rotation_deg", ">="),
        ({"shoulder_rotation_deg": 90.0}, "shoulder_rotation_deg", "<="),
        ({"frames": 2}, "frames", ">="),
        ({"strike_fraction": 0.95}, "strike_fraction", "<="),
        ({"max_extension_ratio": 1.5}, "max_extension_ratio", "<="),
        ({"side": "sideways"}, "side", "one of"),
    )
    for extra, name, fragment in cases:
        params = {"rig": rig.name, "action": "punch.refused"}
        params.update(extra)
        reply = call("rigforge_punch", params, expect_error=True)
        message = reply.get("message") or ""
        check("%s=%r is refused politely, by name and by bound"
              % (name, list(extra.values())[0]),
              reply.get("status") == "error" and name in message
              and fragment in message, message[:180])
    check("and nothing was authored while the parameters were being refused",
          bpy.data.actions.get("punch.refused") is None,
          str(bpy.data.actions.get("punch.refused")))

    # reach_margin is bounded *by* max_extension_ratio, not by a constant: a
    # margin above the hyperextension cap is a contradiction, not a preference.
    reply = call("rigforge_punch", {"rig": rig.name, "action": "punch.refused",
                                    "reach_margin": 0.995}, expect_error=True)
    check("a reach_margin above the hyperextension cap is refused",
          reply.get("status") == "error"
          and "reach_margin" in (reply.get("message") or ""),
          (reply.get("message") or "")[:180])

    empty = bpy.data.objects.new("BarePuncher", bpy.data.armatures.new("BarePuncher"))
    bpy.context.scene.collection.objects.link(empty)
    bpy.context.view_layer.update()
    reply = call("rigforge_punch", {"rig": "BarePuncher"}, expect_error=True)
    message = reply.get("message") or ""
    check("a rig with no IK legs is refused by name, and told what to run instead",
          reply.get("status") == "error" and "foot_ik" in message
          and "rigforge_generate_rig" in message, message[:240])
    check("and the refusal says why the legs matter at all",
          "foot-slide" in message or "foot slide" in message, message[:240])
    bpy.data.objects.remove(empty, do_unlink=True)


def test_reach_is_a_cap_not_a_suggestion(rig, reach):
    section("a target further away than the arm is pulled back, not reached for")
    greedy = call("rigforge_punch", {"rig": rig.name, "action": "punch.overreach",
                                     "target_distance": 3.0 * reach})
    for warning in greedy.get("warnings") or []:
        note("warning: %s" % warning)
    check("the target was pulled back rather than stretched into",
          greedy["target_reach_clamped"] is True, str(greedy["target_reach_clamped"]))
    check("and the result sits on the hyperextension cap rather than past it",
          greedy["extension_within_cap"] is True
          and greedy["extension_ratio"] <= greedy["max_extension_ratio"]
          + greedy["extension_tolerance"],
          "%.4f vs cap %.4f +/- %.3f" % (greedy["extension_ratio"],
                                         greedy["max_extension_ratio"],
                                         greedy["extension_tolerance"]))
    check("it says so in a warning rather than silently",
          any("hyperextended" in w for w in greedy.get("warnings") or []),
          str(greedy.get("warnings"))[:220])
    reached = call("animation_check", {"rig": rig.name, "action": "punch.overreach"})
    check("and the clamped punch still plants its feet", reached["gate"] == "ok",
          "%s (%s mm)" % (reached["gate"], reached["worst_drift_mm"]))


def test_loop_and_naming(rig):
    section("the action library conventions")
    looped = call("rigforge_punch", {"rig": rig.name, "side": "L", "loop": True,
                                     "action": "jab", "frames": 30})
    check("loop=true puts Godot's -loop suffix on the name",
          looped["action"] == "jab-loop" and looped["loop"] is True,
          looped["action"])
    frames, tracks = sample_clip(rig, "jab-loop", ("hand_ik.L", "hips"))
    for name, track in tracks.items():
        check("%s ends the clip exactly where it started, so the loop is seamless"
              % name, (track[-1] - track[0]).length * 1000.0 < 0.05,
              "%.4f mm" % ((track[-1] - track[0]).length * 1000.0))
    check("a 30-frame punch really is 30 frames", looped["frames"] == 30
          and looped["frame_range"] == [1, 30], str(looped["frame_range"]))
    reached = call("animation_check", {"rig": rig.name, "action": "jab-loop"})
    check("and it plants its feet too", reached["gate"] == "ok",
          "%s (%s mm)" % (reached["gate"], reached["worst_drift_mm"]))


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
    print("Forge add-on punch headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_punch_test_")
    try:
        mesh, rig, meta, generated = build_character()
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")

        # The walk comes first on purpose: the punch has to land in a file that
        # already has an action in it, and leave it exactly as it found it.
        section("a walk in the file first")
        walk = call("rigforge_walk", {"rig": rig.name, "action": WALK,
                                      "cycle_frames": 24})
        note(walk["says"])
        walk_before = action_signature(WALK_LOOP)

        right = test_punch_authors(rig, "R", PUNCH_R)
        left = test_punch_authors(rig, "L", PUNCH_L)
        test_defaults_are_derived(rig, right)
        test_feet_never_move(rig, PUNCH_R)
        test_feet_never_move(rig, PUNCH_L)
        test_legs_cannot_stretch(rig, PUNCH_R, right)
        test_legs_cannot_stretch(rig, PUNCH_L, left)
        test_fist_reaches_without_hyperextending(rig, right, "R")
        test_fist_reaches_without_hyperextending(rig, left, "L")
        test_rotation_leads(rig, right)
        test_rotation_leads(rig, left)
        test_report_quotes_its_numbers(right)
        test_other_actions_survive(rig, walk_before)
        test_determinism(rig)
        test_parameters_are_bounded(rig)
        test_reach_is_a_cap_not_a_suggestion(rig, right["arm_reach_m"])
        test_loop_and_naming(rig)

        section("the walk still measures as planted after all of that")
        reached = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
        note(reached["says"])
        check("the walk authored before the punches is still a planted walk",
              reached["gate"] == "ok",
              "%s (%s mm)" % (reached["gate"], reached["worst_drift_mm"]))
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
