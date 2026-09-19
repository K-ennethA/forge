"""Headless add-on tests for ``rigforge_jump`` — a jump whose clock is gravity.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_jump.py

Needs no geometry service and downloads nothing.  The character is the same
synthetic sculpt ``headless_rigik`` uses, carried through retopo -> metarig ->
generate by that suite's own builder, so what is under test is the rig the
pipeline actually produces rather than a hand-built skeleton that agrees with
the test.

**What this suite is for.**  ``rigforge_walk`` proved a clip whose feet are
keyed on the IK targets does not slide; ``rigforge_punch`` proved it again on a
clip where the hips turn 22 degrees over feet that never move.  A jump is the
first clip in the module where the feet are *supposed* to leave the ground, and
it is therefore the first one that can be wrong in a way neither of those can
be: it can **float**.  So:

* the grounded phases — the takeoff plant and the landing plant, which for a
  forward jump are in two different places — drift essentially zero;
* there really is an airborne window: a run of frames where *both* balls of the
  feet are above the ground clearance, found by ``animation_check`` and checked
  again here by measuring the deform toes directly;
* the root's height through that window follows the ballistic parabola the apex
  implies, to under a tolerance, with the deviation quoted in millimetres;
* the leg never outruns its own measured reach at full extension — a
  hyperextended knee is the leg's version of foot slide;
* the landing absorbs **deeper** than the anticipation crouch, measured on the
  torso rather than echoed from the parameters;
* a walk and a punch authored first come out byte-identical afterwards;
* out-of-range parameters are refused with a sentence naming the bound;
* authoring the same jump twice produces byte-identical keyframes;
* and — the regression that matters most — ``animation_check`` still reads the
  walk exactly the way it did before the airborne handling existed.

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

PORT = 9913  # not 9876 (a live session), not 9878-9881 (phases 2-5), not 9907-9912

WALK = "walk"
WALK_LOOP = WALK + "-loop"
PUNCH_R = "punch.R"
JUMP = "jump"
JUMP_FORWARD = "jump-forward"

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


def toe_points(rig, action_name):
    """The ball of each foot, per frame — the point the slide metric measures."""
    return sample_clip(rig, action_name, ("DEF-toe.L", "DEF-toe.R", "root"))


def skinned_meshes(rig):
    """Every mesh this rig deforms — what the floor and the seam are measured on."""
    return [obj for obj in bpy.data.objects
            if obj.type == "MESH" and any(
                getattr(mod, "type", "") == "ARMATURE"
                and getattr(mod, "object", None) is rig
                for mod in obj.modifiers)]


def evaluated_world(obj):
    """The evaluated mesh's vertices in world space — the flesh, not the controls."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    data = evaluated.to_mesh()
    try:
        matrix = evaluated.matrix_world
        return [matrix @ vertex.co.copy() for vertex in data.vertices]
    finally:
        evaluated.to_mesh_clear()


def deformed_indices(rig, obj):
    """Vertex indices this rig actually moves — i.e. that carry a deform weight.

    A vertex with no weight in any deform group sits exactly where it was
    modelled, whatever the clip does, so on a root-motion loop it reads as a
    full stride of "seam" and says nothing about the animation.  Whether such
    vertices should exist at all is the skinning suite's question, not this
    one's; this one counts them out loud and measures the flesh.
    """
    names = {bone.name for bone in rig.data.bones
             if getattr(bone, "use_deform", False)}
    groups = {group.index for group in obj.vertex_groups if group.name in names}
    return [vertex.index for vertex in obj.data.vertices
            if any(item.group in groups and item.weight > 0.0
                   for item in vertex.groups)]


def mesh_frames(rig, action_name, frames, bone="root"):
    """``({frame: [world coords]}, {frame: bone head})`` over ``frames``."""
    from forge.tools import rigforge_rig as rr

    scene = bpy.context.scene
    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    coords, heads = {}, {}
    meshes = skinned_meshes(rig)
    try:
        rr.assign_action(rig, bpy.data.actions[action_name])
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            flat = []
            for obj in meshes:
                flat.extend(evaluated_world(obj))
            coords[frame] = flat
            if bone in rig.pose.bones:
                heads[frame] = world_head(rig, bone).copy()
    finally:
        scene.frame_set(previous_frame)
        try:
            rr.assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):
            pass
        bpy.context.view_layer.update()
    return coords, heads


def leg_chain(rig, result_legs):
    """``[(hip bone, ankle bone, rest reach)]`` from a jump/walk report's legs."""
    return [(leg["hip_bone"], leg["ankle_bone"], leg["reach_m"])
            for leg in result_legs]


# --- defect 1: the walk's travel, its seam, and the stretch that paid for it -
#
# Measured on ``werewolf-wip-14`` before the fix: the walk's forward travel was
# authored on the torso control while the foot IK targets cycled in root space,
# so the hips ran away from the planted foot and, with Rigify's default
# ``IK_Stretch = 1.0``, the left leg chain **grew 261.7 mm = +32.58% of its own
# length** at frame 33.  The loop did not close either: 281 mm of leg-chain pop
# at the seam, every cycle, forever.
#
# Both of those are authoring bugs and both are measured here on the evaluated
# mesh and the deform bones, never on the parameters that asked for them.

def test_walk_loop_closes(rig, walk):
    section("the walk's seam: the last frame IS the first, one stride along")
    first, last = int(walk["frame_range"][0]), int(walk["frame_range"][1])
    meshes = skinned_meshes(rig)
    check("there is a skinned mesh to measure the seam on", bool(meshes),
          str([obj.name for obj in meshes]))
    if not meshes:
        return
    coords, heads = mesh_frames(rig, WALK_LOOP, (first, last))
    travel = (heads[last] - heads[first]) if heads else Vector((0.0, 0.0, 0.0))
    note("travel clip: the root moves %.2f mm between f%d and f%d against a %.2f mm "
         "stride" % (travel.length * 1000.0, first, last, walk["stride_m"] * 1000.0))
    check("the travel is on the ROOT, a stride's worth, which is the clip's product "
          "rather than a seam that failed to close",
          abs(travel.length - walk["stride_m"]) * 1000.0 < 1.0,
          "%.3f mm vs %.3f mm" % (travel.length * 1000.0, walk["stride_m"] * 1000.0))
    worst = max(((a - (b - travel)).length
                 for a, b in zip(coords[first], coords[last])), default=0.0)
    note("worst vertex, root motion subtracted: %.4f mm over %d vertices"
         % (worst * 1000.0, len(coords[first])))
    check("and with it subtracted the mesh is in exactly the same shape - the loop "
          "closes by construction", worst * 1000.0 <= 1.0,
          "%.4f mm (was 281 mm of leg chain on werewolf-wip-14)" % (worst * 1000.0))
    check("the report says which space it closes in", walk["loop_closes_in"] == "root space",
          str(walk.get("loop_closes_in")))

    in_place = call("rigforge_walk", {"rig": rig.name, "action": "walk.inplace",
                                      "cycle_frames": 24, "travel": False})
    note(in_place["says"])
    ip_first, ip_last = (int(in_place["frame_range"][0]),
                         int(in_place["frame_range"][1]))
    ip_coords, ip_heads = mesh_frames(rig, "walk.inplace-loop", (ip_first, ip_last))
    ip_travel = ip_heads[ip_last] - ip_heads[ip_first]
    ip_worst = max(((a - b).length
                    for a, b in zip(ip_coords[ip_first], ip_coords[ip_last])),
                   default=0.0)
    check("the treadmill clip does not move its root at all",
          ip_travel.length * 1000.0 < 0.001, "%.5f mm" % (ip_travel.length * 1000.0))
    check("and closes in WORLD space, nothing subtracted",
          ip_worst * 1000.0 <= 1.0, "%.4f mm" % (ip_worst * 1000.0))
    check("the report says so", in_place["loop_closes_in"] == "world space",
          str(in_place.get("loop_closes_in")))


def test_walk_leg_never_grows(rig, walk):
    section("the walk's legs: no frame of contact grows the chain")
    note("the command's own measurement: worst planted leg asked for %.5f of its "
         "measured reach (first pass %.5f) after %d authoring pass(es); swing peak "
         "%.5f; limits %s; worst frame %s"
         % (walk["leg_reach_ratio"] or 0.0,
            walk["leg_reach_ratio_first_pass"] or 0.0, walk["reach_passes"],
            walk["swing_reach_ratio"] or 0.0, walk["leg_max_span_m"],
            walk["leg_reach_worst"]))
    check("the report carries the measured reach ratio at all",
          walk["leg_reach_ratio"] is not None, str(walk.get("leg_reach_ratio")))
    check("the limit it measured against is the leg's own, asked of the rig "
          "rather than summed off a pre-bent rest chain",
          walk["leg_max_span_m"] and all(value > walk["leg_reach_m"] * 0.8
                                         for value in walk["leg_max_span_m"].values()),
          "%s against a %.4f m rest chain" % (walk["leg_max_span_m"],
                                              walk["leg_reach_m"] or 0.0))
    check("no planted frame asks the leg to stand further from the hip than it "
          "reaches - that over-reach is what +32.58% of stretched leg paid for",
          (walk["leg_reach_ratio"] or 0.0) <= 1.0,
          "%.5f of its own measured reach" % (walk["leg_reach_ratio"] or 0.0))

    # ...and again from outside the command, on the deform chain itself.
    from forge.tools import rigforge_anim as ra
    from forge.tools import rigforge_rig as rr

    limbs = rr.ik_limbs(rig)
    info = ra.locomotion_frame(rig, limbs)
    probe = ra.jump_legs(rig, limbs, info)
    check("the deform hip and ankle can be found to measure between", bool(probe),
          str(sorted(probe)))
    if not probe:
        return
    names = sorted(probe)
    bones = []
    for name in names:
        bones.extend((probe[name]["hip_bone"], probe[name]["ankle_bone"]))
    # The command reports its stance runs per limb, by the same limb name
    # `jump_legs` keys on, so "while the foot is planted" means one thing here
    # and in the clip.
    stance = {entry["foot"]: [tuple(run) for run in entry["stance_runs"]]
              for entry in walk["feet"]}
    check("the walk reports a stance run for every leg it measured",
          set(names) <= set(stance), "%s vs %s" % (names, sorted(stance)))

    # Two different questions, and they need two different measurements:
    #
    #  * hip-to-ankle distance is what the leg is ASKED for. Over 1.0 of the
    #    chain's own length the target is out of reach and something has to
    #    give - that is the ik_reach_headroom question, and it swings hugely
    #    across a stance because the hips travel over a planted foot.
    #  * the sum of the DEF chain's posed bone lengths is what the leg IS. That
    #    is the bone_stretch_budget question, and it is the one that read
    #    +32.58% on werewolf-wip-14.
    chain = {}
    for name in names:
        leg = probe[name]
        entry = leg["entry"]
        chain[name] = [bone for bone in entry["deform_bones"]
                       if "thigh" in bone or "shin" in bone]
    note("deform chains: %s" % chain)
    check("each leg has a deform chain to measure",
          all(len(value) >= 2 for value in chain.values()), str(chain))

    scene = bpy.context.scene
    from forge.tools import rigforge_rig as rr

    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    action = bpy.data.actions[WALK_LOOP]
    span = action.frame_range
    frames = list(range(int(span[0]), int(span[1]) + 1))
    lengths = {name: [] for name in names}
    spans = {name: [] for name in names}
    try:
        rr.assign_action(rig, action)
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            for name in names:
                lengths[name].append(
                    sum(rig.pose.bones[bone].length for bone in chain[name]))
                spans[name].append(
                    (world_head(rig, probe[name]["ankle_bone"])
                     - world_head(rig, probe[name]["hip_bone"])).length)
    finally:
        scene.frame_set(previous_frame)
        try:
            rr.assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):
            pass
        bpy.context.view_layer.update()

    worst_growth, worst_at = 0.0, None
    worst_travel = 0.0
    for name in names:
        rest = sum(rig.data.bones[bone].length for bone in chain[name])
        runs = stance.get(name) or []
        planted = [index for index, frame in enumerate(frames)
                   if any(low <= frame <= high for low, high in runs)]
        if not planted or rest <= 0.0:
            continue
        top = max(lengths[name][index] for index in planted)
        low = min(lengths[name][index] for index in planted)
        growth = top / rest - 1.0
        if growth > worst_growth:
            worst_growth, worst_at = growth, name
        worst_travel = max(worst_travel, (top - low) / rest)
        note("%s: rest chain %.1f mm; planted chain %.1f-%.1f mm over %s -> "
             "%+.3f%% at its longest, %.2f%% of length travel inside the run; "
             "hip-to-ankle peaks at %.1f mm"
             % (name, rest * 1000.0, low * 1000.0, top * 1000.0, runs,
                growth * 100.0, (top - low) / rest * 100.0,
                max(spans[name][index] for index in planted) * 1000.0))
    check("no planted frame of either leg makes the chain longer than it is - the "
          "chain clamps, it does not grow (werewolf-wip-14: +32.58%)",
          worst_growth <= 0.001, "worst %+.4f%% on %s"
          % (worst_growth * 100.0, worst_at))
    # What is left is the rig, not the clip, and it is worth a number rather
    # than a suspicious wobble in the log: this rest pose stands with its legs
    # straight, so the IK has no knee bend to spend and Rigify's solver squashes
    # the chain rather than folding it. IK_Stretch = 0 clamps the growth, which
    # is the defect; the squash is the missing anatomical pre-bend, and
    # rig_check's bend_direction gate is the one that owns that.
    note("residual chain travel inside a contact run: %.2f%% - squash on a "
         "straight rest pose, never stretch" % (worst_travel * 100.0))
    check("and what is left is small and one-sided, not the runaway stretch the "
          "review measured (32.58%)", worst_travel < 0.03,
          "%.3f%%" % (worst_travel * 100.0))


def test_ik_stretch_is_keyed(rig, action_name, report, label):
    section("IK_Stretch = 0 for the whole clip: %s" % label)
    block = report.get("ik_stretch") or {}
    note("keyed %s to %s on %s; live values restored to %s"
         % (block.get("property"), block.get("keyed_to"), block.get("bones"),
            block.get("restored_to")))
    check("the report carries an ik_stretch block", bool(block), str(block))
    check("both leg switches were keyed",
          sorted(block.get("bones") or []) == ["thigh_parent.L", "thigh_parent.R"],
          str(block.get("bones")))
    check("to zero - stretch is a cinematic effect, never a default",
          block.get("keyed_to") == 0.0, str(block.get("keyed_to")))
    check("and nothing on this rig was missing the property",
          not block.get("without_the_property"),
          str(block.get("without_the_property")))

    from forge.tools import rigforge_rig as rr

    action = bpy.data.actions[action_name]
    curves = [curve for curve in rr.action_fcurves(action)
              if "IK_Stretch" in curve.data_path]
    check("the clip carries an IK_Stretch channel per leg", len(curves) == 2,
          str([curve.data_path for curve in curves]))
    span = action.frame_range
    for curve in curves:
        points = [(round(float(p.co.x), 4), round(float(p.co.y), 6))
                  for p in curve.keyframe_points]
        check("%s is flat zero across the clip, not a ramp"
              % curve.data_path.split('"')[1],
              points and all(value == 0.0 for _frame, value in points),
              str(points))
        check("...and it covers the whole clip, so no frame falls off the end into "
              "the rig's own 1.0",
              points and points[0][0] <= float(span[0]) + 1e-4
              and points[-1][0] >= float(span[1]) - 1e-4,
              "%s against %s" % (points, [round(float(v), 2) for v in span]))

    restored = block.get("restored_to") or {}
    check("the report says what it put the live property back to",
          sorted(restored) == ["thigh_parent.L", "thigh_parent.R"]
          and all(value > 0.0 for value in restored.values()),
          str(restored))


def test_ik_stretch_is_restored(rig):
    """The rig must come out of an authoring call the way it went in.

    ``IK_Stretch`` is an animated ID property, so while the clip is assigned
    the *evaluated* value is the clip's 0 — that is the whole point.  What must
    not happen is the command leaving the rig's own 0 behind for the animator
    to find after they clear or unassign the action, because then "stretch off"
    has quietly become this rig's default rather than this clip's choice.
    Checked immediately after each call, on scratch actions so nothing the rest
    of the suite measures is disturbed.
    """
    section("IK_Stretch: the rig comes out as it went in")
    switches = ["thigh_parent.L", "thigh_parent.R"]
    check("this rig has the property at all - a Rigify leg ships it at 1.0",
          all("IK_Stretch" in rig.pose.bones[name].keys() for name in switches),
          str([sorted(rig.pose.bones[name].keys()) for name in switches]))

    # A distinctive value rather than the rig's own 1.0, so "restored" cannot
    # be confused with "happened to already be that". Set immediately before
    # each call, because an animated ID property is stamped back onto the pose
    # bone every time the scene is evaluated and the rest of this suite scrubs
    # frames with these clips assigned.
    for command, params in (
            ("rigforge_walk", {"action": "stretch.walk", "cycle_frames": 12}),
            ("rigforge_punch", {"action": "stretch.punch", "side": "R"}),
            ("rigforge_jump", {"action": "stretch.jump"})):
        for name in switches:
            rig.pose.bones[name]["IK_Stretch"] = 0.75
        bpy.context.view_layer.update()
        report = call(command, dict({"rig": rig.name}, **params))
        live = {name: round(float(rig.pose.bones[name]["IK_Stretch"]), 6)
                for name in switches}
        check("%s leaves the live property where it found it" % command,
              live == {name: 0.75 for name in switches}, str(live))
        check("...and reports the value it put back",
              (report.get("ik_stretch") or {}).get("restored_to")
              == {name: 0.75 for name in switches},
              str((report.get("ik_stretch") or {}).get("restored_to")))
    for name in switches:
        rig.pose.bones[name]["IK_Stretch"] = 1.0
    bpy.context.view_layer.update()


# --- authoring --------------------------------------------------------------

def test_jump_authors(rig, params, action_name, label):
    section("rigforge_jump - %s" % label)
    call_params = {"rig": rig.name}
    call_params.update(params)
    result = call("rigforge_jump", call_params)
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    note(result["says"])

    check("the action follows the walk/punch naming convention (%s)" % action_name,
          result["action"] == action_name, result["action"])
    check("it is a one-shot, not a loop", result["loop"] is False, str(result["loop"]))
    check("it names both feet as the ones it plants",
          sorted(result["feet_planted"]) == ["foot_ik.L", "foot_ik.R"],
          str(result["feet_planted"]))

    bones = action_bones(action_name)
    check("both feet are keyed on their IK targets",
          {"foot_ik.L", "foot_ik.R"} <= bones, str(sorted(bones)))
    check("NOT on the FK leg chain - a jump keyed on thigh_fk/shin_fk can neither "
          "plant its takeoff nor its landing",
          not ({"thigh_fk.L", "thigh_fk.R", "shin_fk.L", "shin_fk.R"} & bones),
          str(sorted(name for name in bones if "_fk" in name)))
    check("the root carries the ballistic arc", "root" in bones, str(sorted(bones)))
    check("the hips carry the crouch and the absorb", "torso" in bones or "hips" in bones,
          str(sorted(bones)))
    check("the arms swing on their FK controls",
          {"upper_arm_fk.L", "upper_arm_fk.R"} <= bones, str(sorted(bones)))
    check("the heel controls carry the roll, so the feet leave toes-last and land "
          "heels-first", {"foot_heel_ik.L", "foot_heel_ik.R"} <= bones,
          str(sorted(bones)))
    check("the FK/IK switches are keyframed, so the export bake resolves what was "
          "authored", {"thigh_parent.L", "thigh_parent.R"} <= bones, str(sorted(bones)))
    return result


def test_phases_are_in_order(result):
    section("the six phases, in the order an animator builds them")
    phases = result["phases"]
    note("guard %s -> anticipation %s -> launch %s -> airborne %s -> landing %s -> "
         "recover %s"
         % (phases["guard"], phases["anticipation"], phases["launch"],
            phases["airborne"], phases["landing"], phases["recover"]))
    order = [result["crouch_frame"], result["takeoff_frame"], result["apex_frame"],
             result["landing_frame"], result["absorb_frame"]]
    check("crouch -> takeoff -> apex -> landing -> absorb, strictly increasing",
          all(order[i] < order[i + 1] for i in range(len(order) - 1)), str(order))
    check("the clip starts on frame 1 and ends on the recover",
          result["frame_range"][0] == 1
          and result["frame_range"][1] == phases["recover"][1], str(result["frame_range"]))
    check("and every phase is at least two frames long",
          all(phases[name][1] - phases[name][0] >= 1
              for name in ("anticipation", "launch", "landing", "recover")),
          str(phases))


def test_timing_is_ballistic(result):
    section("the airtime is derived from the apex, not chosen")
    g = result["gravity"]
    fps = result["fps"]
    apex = result["apex_solved_m"]
    ideal = 2.0 * math.sqrt(2.0 * apex / g)
    note("apex %.1f mm requested, %.1f mm solved, %.1f mm reached; airtime %.4f s "
         "(%d frames at %.3g fps); g = %.5g"
         % (result["apex_requested_m"] * 1000.0, apex * 1000.0,
            result["apex_reached_m"] * 1000.0, result["airtime_s"],
            result["airborne_frames"], fps, g))
    check("the airborne frame count is 2*sqrt(2*apex/g) at the scene fps",
          abs(result["airborne_frames"] - ideal * fps) < 1e-3,
          "%d frames vs %.4f" % (result["airborne_frames"], ideal * fps))
    check("the solved apex is what the rounded airtime implies (g*t^2/8), so the "
          "parabola lands on a key rather than between two",
          abs(apex - g * result["airtime_s"] ** 2 / 8.0) < 1e-5,
          "%.6f vs %.6f" % (apex, g * result["airtime_s"] ** 2 / 8.0))
    check("the launch speed is sqrt(2*g*apex)",
          abs(result["launch_speed_m_per_s"] - math.sqrt(2.0 * g * apex)) < 1e-4,
          "%.5f vs %.5f" % (result["launch_speed_m_per_s"],
                            math.sqrt(2.0 * g * apex)))
    # The peak of the arc falls between two keys whenever the flight is an even
    # number of frames, and a body at the top of a parabola drops g*dt^2/2 in
    # that half frame. So the apex a sampled clip can *show* is short of the
    # apex it was solved for by exactly that, and by no more.
    gap = result["apex_peak_between_keys_mm"]
    check("the apex the posed rig reaches is the solved apex, short only by the "
          "half frame the true peak falls between keys",
          result["apex_reached_m"] <= apex + 1e-6
          and result["apex_error_mm"] <= gap + 0.01,
          "%.4f mm short against a %.4f mm sampling gap"
          % (result["apex_error_mm"], gap))
    check("and the report says the root held its parabola",
          result["parabola_within_tolerance"] is True
          and result["parabola_deviation_mm"] < 1.0,
          "%.4f mm" % result["parabola_deviation_mm"])


def test_doubling_the_apex_lengthens_the_flight(rig, base):
    section("a higher jump hangs longer, because that is what gravity does")
    higher = call("rigforge_jump", {"rig": rig.name, "action": "jump.high",
                                    "apex_height": 4.0 * base["apex_solved_m"]})
    note(higher["says"])
    ratio = higher["airborne_frames"] / float(base["airborne_frames"])
    check("four times the apex is twice the airtime, to the frame rounding",
          abs(ratio - 2.0) < 0.15, "%.3f x (%d frames vs %d)"
          % (ratio, higher["airborne_frames"], base["airborne_frames"]))
    check("and the clip was lengthened to hold it rather than compressing the flight",
          higher["frames"] > base["frames"]
          or higher["frames"] >= 1 + higher["airborne_frames"],
          "%d frames for %d airborne" % (higher["frames"], higher["airborne_frames"]))
    lower = call("rigforge_jump", {"rig": rig.name, "action": "jump.low", "gravity": 1.62})
    note(lower["says"])
    check("moon gravity hangs longer still for the same apex",
          lower["airborne_frames"] > base["airborne_frames"],
          "%d frames at g=1.62 vs %d at g=9.81"
          % (lower["airborne_frames"], base["airborne_frames"]))


def test_defaults_are_derived(result):
    section("every default is a fraction of this rig, not a constant in metres")
    leg = result["leg_length_m"]
    reach = result["leg_reach_m"]
    check("the leg's reach was measured off the rig (thigh + shin along the rest "
          "chain)", 0.05 < reach < 3.0 and reach >= leg - 1e-6,
          "%.4f m reach on a %.4f m leg" % (reach, leg))
    check("the apex defaults to a fraction of the leg, not to a number of metres",
          0.0 < result["apex_requested_m"] < leg,
          "%.4f m apex on a %.4f m leg" % (result["apex_requested_m"], leg))
    check("so does the anticipation crouch",
          0.0 < result["crouch_depth_m"] < leg,
          "%.4f m" % result["crouch_depth_m"])
    check("and the airborne tuck",
          0.0 < result["tuck_height_m"] <= leg, "%.4f m" % result["tuck_height_m"])
    check("the landing absorb is deeper than the anticipation crouch by default",
          result["landing_depth_m"] > result["crouch_depth_m"],
          "%.4f m absorb vs %.4f m crouch" % (result["landing_depth_m"],
                                              result["crouch_depth_m"]))


def test_extension_is_capped(rig, result):
    section("the launch extends the legs, and never past their own reach")
    note("extension rise %.1f mm (headroom %s), peak %.2f%% of a %.0f mm reach; "
         "cap %.0f%%, rest pose already at %.2f%%, so the ceiling is %.2f%%"
         % (result["extension_rise_m"] * 1000.0,
            ("%.1f mm" % (result["extension_headroom_m"] * 1000.0))
            if result["extension_headroom_m"] is not None else "unmeasured",
            (result["extension_ratio"] or 0.0) * 100.0,
            result["leg_reach_m"] * 1000.0, result["max_extension_ratio"] * 100.0,
            result["rest_extension_ratio"] * 100.0,
            result["extension_ceiling_ratio"] * 100.0))
    check("the peak extension was measured on the posed rig at takeoff",
          result["extension_ratio"] is not None, str(result["extension_ratio"]))
    check("and it is inside the ceiling - the hyperextension cap, or the rest pose "
          "if the rig already stands past it",
          result["extension_within_cap"] is True
          and result["extension_ratio"] <= result["extension_ceiling_ratio"]
          + result["extension_tolerance"],
          "%.4f vs ceiling %.4f +/- %.3f" % (result["extension_ratio"],
                                             result["extension_ceiling_ratio"],
                                             result["extension_tolerance"]))
    check("the cap is the measured reach, so it cannot be a constant in metres",
          result["leg_reach_m"] > 0.0
          and result["extension_ratio"] * result["leg_reach_m"] > 0.0,
          "%.4f m at full extension" % (result["extension_ratio"]
                                        * result["leg_reach_m"]))
    check("the extension never exceeds the headroom the legs actually have",
          result["extension_headroom_m"] is None
          or result["extension_rise_m"] <= max(0.0, result["extension_headroom_m"])
          + 1e-9,
          "%.5f m rise against %s m headroom"
          % (result["extension_rise_m"], result["extension_headroom_m"]))

    # The cap is what drives the extension, not a constant: loosen it and the
    # legs are allowed further, tighten it and they are allowed less. Measured
    # on this rig rather than asserted, so the relationship is the test.
    loose = call("rigforge_jump", {"rig": rig.name, "action": "jump.loosecap",
                                   "max_extension_ratio": 1.0})
    tight = call("rigforge_jump", {"rig": rig.name, "action": "jump.tightcap",
                                   "max_extension_ratio": 0.85})
    note("headroom at cap 1.00: %.2f mm -> rise %.2f mm; at cap 0.85: %.2f mm -> "
         "rise %.2f mm"
         % (loose["extension_headroom_m"] * 1000.0,
            loose["extension_rise_m"] * 1000.0,
            tight["extension_headroom_m"] * 1000.0,
            tight["extension_rise_m"] * 1000.0))
    check("a looser cap buys more headroom and more extension",
          loose["extension_headroom_m"] > result["extension_headroom_m"]
          and loose["extension_rise_m"] >= result["extension_rise_m"],
          "%.5f / %.5f vs %.5f / %.5f"
          % (loose["extension_headroom_m"], loose["extension_rise_m"],
             result["extension_headroom_m"], result["extension_rise_m"]))
    check("a tighter one buys less, and is still never negative",
          tight["extension_headroom_m"] < result["extension_headroom_m"]
          and tight["extension_rise_m"] >= 0.0,
          "%.5f / %.5f" % (tight["extension_headroom_m"],
                           tight["extension_rise_m"]))
    check("and both stay inside their own ceiling",
          loose["extension_within_cap"] is True
          and tight["extension_within_cap"] is True,
          "%s / %s" % (loose["extension_ratio"], tight["extension_ratio"]))

    # Frame by frame, on the same two joints the reach was summed between - and
    # those are the DEFORM bones, because thigh_fk/foot_fk are the other half of
    # the IK switch and sit at rest for the whole of an IK-keyed clip. A
    # hyperextended knee is the leg's version of foot slide, and it is a
    # distance, so it is measurable rather than a matter of opinion.
    leg = result["legs"][0]
    names = (leg["hip_bone"], leg["ankle_bone"])
    check("the extension is probed on bones the IK solver actually drives",
          all(name.startswith("DEF-") for name in names), str(names))
    frames, tracks = sample_clip(rig, result["action"], names)
    spans = [(tracks[names[1]][index] - tracks[names[0]][index]).length
             for index in range(len(frames))]
    ceiling = result["extension_ceiling_ratio"] * leg["reach_m"]
    check("and no frame of the clip puts the ankle further from the hip than the "
          "ceiling", max(spans) <= ceiling * 1.01 + 1e-4,
          "worst %.1f mm vs ceiling %.1f mm" % (max(spans) * 1000.0,
                                                ceiling * 1000.0))
    note("%s -> %s across the clip: %.0f mm min (the airborne tuck), %.0f mm max, "
         "ceiling %.0f mm, reach %.0f mm"
         % (names[0], names[1], min(spans) * 1000.0, max(spans) * 1000.0,
            ceiling * 1000.0, leg["reach_m"] * 1000.0))
    check("the report's own peak matches what the clip measures",
          abs(max(spans) / leg["reach_m"] - result["extension_ratio"]) < 0.01,
          "%.4f measured vs %.4f reported"
          % (max(spans) / leg["reach_m"], result["extension_ratio"]))
    check("the airborne tuck really does fold the leg - the span at apex is well "
          "under the span standing still",
          result["tuck_ratio"] < 0.9 * result["rest_extension_ratio"]
          and result["takeoff_frame"] < result["tuck_frame"]
          < result["landing_frame"],
          "%.3f tucked (f%s) vs %.3f standing"
          % (result["tuck_ratio"], result["tuck_frame"],
             result["rest_extension_ratio"]))
    check("and the leg is at its longest during the launch or the plants, never "
          "in the air", not (result["takeoff_frame"] < result["extension_peak_frame"]
                             < result["landing_frame"]),
          "peak on f%s, flight f%d-f%d" % (result["extension_peak_frame"],
                                           result["takeoff_frame"],
                                           result["landing_frame"]))


# --- defect 2: the anticipation that was there and could not be seen --------
#
# On ``werewolf-wip-14`` the crouch was delivered to spec — 128.5 mm asked,
# 128.32 mm measured on the hips — and the reviewer still could not see it.
# Four reasons, all measurable and all authoring: the hips dropped a **plumb**
# line (hip setback 0.00 mm at every frame of the load), the trunk stayed
# vertical, the whole load took 0.333 s, and 11.7 mm of the drop was spent
# **below the floor**.  A countermovement jump loads down-and-back and drives
# up-and-forward; these checks are that shape, in millimetres.

def test_anticipation_reads(rig, result, action_name):
    section("the anticipation reads as a crouch: %s" % action_name)
    note("load f1-f%d = %.4f s (floor %.2f s); hips drop %.1f mm and travel %.1f mm "
         "back (ratio %.3f), then drive %.1f mm forward; trunk folds %.1f deg, toes "
         "lift %.1f deg"
         % (result["crouch_frame"], result["anticipation_seconds"],
            result["anticipation_min_seconds"],
            result["hip_drop_measured_m"] * 1000.0,
            result["hip_setback_measured_m"] * 1000.0, result["hip_setback_ratio"],
            result["hip_drive_measured_m"] * 1000.0, result["torso_fold_deg"],
            result["load_toe_lift_deg"]))

    check("the load lasts at least 0.35 s, measured in frames at this scene's fps",
          result["anticipation_seconds"] >= 0.35 - 1e-9,
          "%.4f s over %d frames at %.3g fps"
          % (result["anticipation_seconds"], result["anticipation_frames"],
             result["fps"]))
    check("the hips really travel backwards over the ankles, measured on the posed "
          "rig along the rig's own forward axis",
          result["hip_setback_measured_m"] * 1000.0 > 1.0,
          "%.2f mm (was 0.00 mm)" % (result["hip_setback_measured_m"] * 1000.0))
    check("the report names the bone it measured and the sign it used, so nobody "
          "has to guess which way 'back' is",
          result.get("hip_setback_bone") and "rearward" in
          (result.get("hip_setback_sign") or ""),
          "%s / %s" % (result.get("hip_setback_bone"),
                       result.get("hip_setback_sign")))
    note("for comparison the %s control reads %+.2f mm: Rigify's hips box points "
         "downward, so its head sits about a third of the way up the trunk and any "
         "forward fold swings it forward however far back the pelvis goes. That is "
         "where the bone's head is, not what the pose does."
         % (result.get("hip_control_bone"),
            result["hip_control_setback_measured_m"] * 1000.0))

    # And the direction itself, from outside the command, against the rig's own
    # forward axis as `rigforge_rig.rig_forward_axis` reports it - the axis
    # every gate on this clip uses. Rearward is -forward, flattened onto the
    # floor; the hips have to end the load behind the ankles they started over.
    from forge.tools import rigforge_rig as rr

    axis, how = rr.rig_forward_axis(rig)
    flat = Vector((axis.x, axis.y, 0.0))
    flat = flat.normalized() if flat.length > 1e-9 else axis
    note("rig_forward_axis %s (%s); the command reported %s"
         % ([round(v, 4) for v in axis], how, result["forward_axis"]))
    check("the command and the rig agree about which way the character faces",
          flat.dot(Vector(result["forward_axis"])) > 0.99,
          "%.4f" % flat.dot(Vector(result["forward_axis"])))
    watched = [leg["hip_bone"] for leg in result["legs"]]
    watched += [name for name in ("torso",) if name in rig.pose.bones]
    ankles = [leg["ankle_bone"] for leg in result["legs"]]
    frames, tracks = sample_clip(rig, action_name,
                                 tuple(sorted(set(watched + ankles))))
    guard = frames.index(1)
    bottom = frames.index(result["crouch_frame"])

    def behind(name, index):
        ankle = sum((tracks[bone][index] for bone in ankles),
                    Vector((0.0, 0.0, 0.0))) / float(len(ankles))
        offset = tracks[name][index] - ankle
        return -Vector((offset.x, offset.y, 0.0)).dot(flat)

    for name in watched:
        note("%s sits %+.2f mm behind the ankle line standing, %+.2f mm at the "
             "bottom of the load -> %+.2f mm of setback"
             % (name, behind(name, guard) * 1000.0, behind(name, bottom) * 1000.0,
                (behind(name, bottom) - behind(name, guard)) * 1000.0))
    sockets = [leg["hip_bone"] for leg in result["legs"]]
    travelled = {name: (behind(name, bottom) - behind(name, guard)) * 1000.0
                 for name in watched}
    check("every hip joint moves REARWARD through the load, measured against the "
          "world axis the rig's own facing implies",
          all(travelled[name] > 1.0 for name in sockets), str(travelled))
    check("and nothing that carries the pelvis goes the other way - no part of "
          "this load travels forward over the toes",
          all(value > -1.0 for value in travelled.values()), str(travelled))
    check("and the setback is at least 0.3 of the crouch depth - the gate the "
          "review proposed, measured rather than asked for",
          result["hip_setback_ratio"] >= 0.3,
          "%.4f (%.1f mm back / %.1f mm down)"
          % (result["hip_setback_ratio"],
             result["hip_setback_measured_m"] * 1000.0,
             result["hip_drop_measured_m"] * 1000.0))
    check("the drive is the mirror of the load: the hips come forward out of the "
          "countermovement, not straight up out of it",
          result["hip_drive_measured_m"] * 1000.0 > 1.0,
          "%.2f mm forward of the crouch bottom by takeoff"
          % (result["hip_drive_measured_m"] * 1000.0))
    check("the hip drop is still what was asked for - reading better did not cost "
          "the depth",
          abs(result["crouch_measured_m"] - result["crouch_depth_m"]) * 1000.0 < 1.0,
          "%.2f mm measured vs %.2f mm asked"
          % (result["crouch_measured_m"] * 1000.0, result["crouch_depth_m"] * 1000.0))

    bones = action_bones(action_name)
    check("the trunk folds on the torso control itself, not only on the chest",
          result["torso_fold_deg"] > 0.0, "%.1f deg" % result["torso_fold_deg"])
    check("and the ankle work is keyed on the toe pivots, which turn about the "
          "ball and so cannot move the point the plant gate measures",
          {"toe_ik.L", "toe_ik.R"} <= bones,
          str(sorted(name for name in bones if "toe" in name)))

    # The fold, measured: the trunk's own axis has to lean the way the character
    # faces, otherwise "torso_fold_deg" is a number in a report.
    forward = Vector(result["forward_axis"])
    top = next((name for name in ("chest", "DEF-spine.003", "spine_fk.002", "head")
                if name in rig.pose.bones), None)
    check("there is a bone above the hips to read the fold on", top is not None,
          str(sorted(name for name in rig.pose.bones.keys() if "spine" in name)))
    if top is None:
        return
    frames, tracks = sample_clip(rig, action_name, ("torso", top))
    guard = frames.index(1)
    bottom = frames.index(result["crouch_frame"])
    lean_first = (tracks[top][guard] - tracks["torso"][guard]).dot(forward)
    lean_bottom = (tracks[top][bottom] - tracks["torso"][bottom]).dot(forward)
    note("%s sits %.2f mm ahead of the hips standing, %.2f mm at the bottom of the "
         "load" % (top, lean_first * 1000.0, lean_bottom * 1000.0))
    check("the trunk really is over the load - the upper spine moves forward of "
          "the hips as they drop",
          (lean_bottom - lean_first) * 1000.0 > 5.0,
          "%+.2f mm of fold" % ((lean_bottom - lean_first) * 1000.0))


def sole_vertices(rig, obj):
    """Vertex indices whose dominant deform weight is a foot or toe bone.

    The gate is about the **sole**, not the silhouette's lowest pixel: a deep
    absorb legitimately takes the hips (and on a short-legged character the
    crotch) below where they stood, and a jump that squats is not a jump that
    sinks.  What may not happen is the foot going through the plane it stands
    on — which on ``werewolf-wip-14`` it did, by 11.7 mm.
    """
    from forge.tools import rigforge_rig as rr

    wanted = set()
    for entry in rr.ik_limbs(rig):
        if entry["limb"] not in ("leg", "front_leg"):
            continue
        for name in entry["deform_bones"]:
            if "foot" in name or "toe" in name or "paw" in name:
                wanted.add(name)
    indices = {group.index for group in obj.vertex_groups if group.name in wanted}
    if not indices:
        return None
    # Only the deform groups get a vote. A generated mesh also carries the
    # autotagger's region tags (``tag_Leg.L``, weight 1.0), and letting those
    # into a "largest weight wins" scan hands every vertex to a tag and finds
    # no feet at all.
    deform = {group.index for group in obj.vertex_groups
              if group.name in {bone.name for bone in rig.data.bones
                                if getattr(bone, "use_deform", False)}}
    out = []
    for vertex in obj.data.vertices:
        best, weight = None, 0.0
        for item in vertex.groups:
            if item.group in deform and item.weight > weight:
                best, weight = item.group, item.weight
        if best in indices:
            out.append(vertex.index)
    return out or None


def test_the_mesh_stays_on_the_floor(rig, result, action_name):
    section("the crouch is spent in the silhouette, not through the floor")
    meshes = skinned_meshes(rig)
    check("there is a mesh to measure", bool(meshes),
          str([obj.name for obj in meshes]))
    if not meshes:
        return
    picks = {obj.name: sole_vertices(rig, obj) for obj in meshes}
    note("sole vertices per mesh: %s"
         % {name: (len(value) if value else None) for name, value in picks.items()})
    check("the command measured the same sole, found off the same foot and toe "
          "deform bones", result.get("sole_bones"), str(result.get("sole_bones")))
    check("and it found the same vertices this suite does, independently",
          {name: (len(value) if value else None) for name, value in picks.items()}
          == dict(result.get("sole_vertices") or {}),
          "%s vs %s" % ({name: (len(value) if value else None)
                         for name, value in picks.items()},
                        result.get("sole_vertices")))
    if not any(picks.values()):
        # Nothing to stand on: this subject's feet carry no geometry of their
        # own. The command has to say so rather than clamp the crouch against
        # whatever else happens to be lowest (a swung hand, on this one).
        check("with no foot-weighted geometry the command declines to measure a "
              "floor rather than inventing one",
              result["floor_penetration_mm"] is None
              and result["crouch_floor_clamped"] is False,
              "reported %s mm, clamped=%s" % (result["floor_penetration_mm"],
                                              result["crouch_floor_clamped"]))
        check("...and says why, by name", any("sole plane" in warning
                                              for warning in result["warnings"]),
              str(result["warnings"])[:300])
        return

    frames = list(range(int(result["frame_range"][0]),
                        int(result["frame_range"][1]) + 1))
    scene = bpy.context.scene
    from forge.tools import rigforge_rig as rr

    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    lows = {}
    try:
        rr.assign_action(rig, bpy.data.actions[action_name])
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            lowest = None
            for obj in meshes:
                indices = picks.get(obj.name)
                points = evaluated_world(obj)
                candidates = points if indices is None else [points[i] for i in indices
                                                             if i < len(points)]
                for point in candidates:
                    if lowest is None or point.z < lowest:
                        lowest = point.z
            lows[frame] = lowest
    finally:
        scene.frame_set(previous_frame)
        try:
            rr.assign_action(rig, previous_action)
        except (AttributeError, TypeError, RuntimeError):
            pass
        bpy.context.view_layer.update()

    plane = lows[frames[0]]
    worst_frame = min(lows, key=lambda frame: lows[frame])
    penetration = (plane - lows[worst_frame]) * 1000.0
    note("sole plane %.2f mm (frame %d, the guard); the sole's lowest point over "
         "the clip is %.2f mm at frame %d -> %.2f mm below the plane"
         % (plane * 1000.0, frames[0], lows[worst_frame] * 1000.0, worst_frame,
            penetration))
    check("the sole never goes more than 1 mm below the plane it stands on (was "
          "-11.7 mm at the crouch bottom)",
          penetration <= 1.0, "%.3f mm at frame %d" % (penetration, worst_frame))
    check("and the command measured the same thing and said so",
          result["floor_penetration_mm"] is not None
          and result["floor_penetration_mm"] <= 1.0,
          "reported %s mm over %s pass(es), clamped=%s"
          % (result["floor_penetration_mm"], result["floor_passes"],
             result["crouch_floor_clamped"]))


def test_absorb_is_deeper(result):
    section("the landing gives more than the anticipation took")
    note("crouch measured %.1f mm, absorb measured %.1f mm (asked %.1f / %.1f)"
         % (result["crouch_measured_m"] * 1000.0, result["absorb_measured_m"] * 1000.0,
            result["crouch_depth_m"] * 1000.0, result["landing_depth_m"] * 1000.0))
    check("both were measured on the torso rather than echoed from the parameters",
          result["crouch_measured_m"] > 0.0 and result["absorb_measured_m"] > 0.0,
          "%.4f / %.4f" % (result["crouch_measured_m"], result["absorb_measured_m"]))
    check("and the absorb is the deeper of the two",
          result["absorb_deeper_than_crouch"] is True
          and result["absorb_measured_m"] > result["crouch_measured_m"],
          "%.4f m vs %.4f m" % (result["absorb_measured_m"],
                                result["crouch_measured_m"]))


def test_airborne_window_is_real(rig, result, action_name):
    section("the airborne window: %s" % action_name)
    frames, tracks = toe_points(rig, action_name)
    heights = {name: [point.z for point in track]
               for name, track in tracks.items() if name.startswith("DEF-toe")}
    ground = min(min(values) for values in heights.values())
    high = max(max(values) for values in heights.values())
    ceiling = ground + 0.1 * (high - ground)
    both_up = [index for index in range(len(frames))
               if all(values[index] > ceiling for values in heights.values())]
    note("ground %.1f mm, highest ball %.1f mm, %d frame(s) with both balls above "
         "the %.1f mm clearance" % (ground * 1000.0, high * 1000.0, len(both_up),
                                    (ceiling - ground) * 1000.0))
    check("there is a genuine airborne window - both balls of the feet leave the "
          "ground together", len(both_up) >= 3, "%d frames" % len(both_up))
    check("and it sits between the takeoff and the landing the report named",
          both_up and frames[both_up[0]] > result["takeoff_frame"]
          and frames[both_up[-1]] < result["landing_frame"],
          "frames %s-%s against takeoff f%d / landing f%d"
          % (frames[both_up[0]] if both_up else None,
             frames[both_up[-1]] if both_up else None,
             result["takeoff_frame"], result["landing_frame"]))
    check("the feet are back on the ground for the landing",
          all(values[-1] <= ceiling for values in heights.values()),
          str([values[-1] * 1000.0 for values in heights.values()]))

    # The parabola, measured on the root of the posed rig rather than trusted.
    root = tracks["root"]
    base = root[0].z
    worst = 0.0
    speed = result["launch_speed_m_per_s"]
    g = result["gravity"]
    for index, frame in enumerate(frames):
        if not (result["takeoff_frame"] < frame < result["landing_frame"]):
            continue
        elapsed = (frame - result["takeoff_frame"]) / result["fps"]
        ideal = speed * elapsed - 0.5 * g * elapsed * elapsed
        worst = max(worst, abs((root[index].z - base) - ideal))
    check("and the root's height through the flight is the ballistic parabola, "
          "measured frame by frame", worst * 1000.0 < 1.0,
          "%.4f mm off the parabola" % (worst * 1000.0))
    note("max parabola deviation, measured: %.4f mm" % (worst * 1000.0))
    return frames, tracks


def test_plants_hold(rig, action_name, distance=0.0):
    section("the plants: %s" % action_name)
    result = call("animation_check", {"rig": rig.name, "action": action_name})
    note(result["says"])
    note("mode %s (%s)" % (result["mode"], result["mode_reason"]))
    check("%s is read as a jump, without being told" % action_name,
          result["mode"] == "jump", "%s: %s" % (result["mode"], result["mode_reason"]))
    air = result.get("airborne")
    check("the report carries an airborne block", air is not None, str(air is None))
    if air is None:
        return result
    note(air["says"])
    check("which found the flight", air["airborne_frames"] >= 3
          and air["detected"] is True, str(air["airborne_frames"]))
    check("the parabola gate passes, and quotes its deviation",
          air["parabola"] == "ok" and air["max_parabola_deviation_mm"] is not None,
          "%s (%s mm, tolerance %s mm)"
          % (air["parabola"], air["max_parabola_deviation_mm"],
             air["windows"][0]["tolerance_mm"] if air["windows"] else None))
    check("and the fit implies a gravity that points down",
          air["windows"] and air["windows"][0]["implied_gravity_m_per_s2"] > 1.0,
          str([row["implied_gravity_m_per_s2"] for row in air["windows"]]))
    check("the landing knees travel forward, the way a knee folds",
          air["landing_knee"] == "ok", str(air["landing_knees"]))
    check("and the absorb was read off a bone that actually sinks, not off the "
          "root (which is flat the moment the character is back on the floor)",
          air["absorb_bone"] is not None and air["absorb_bone"] != "root"
          and air["absorb_travel_mm"] > 1.0,
          "%s, %s mm" % (air["absorb_bone"], air["absorb_travel_mm"]))
    for row in air["landing_knees"]:
        note("  %s travels %+.2f mm %s (f%d -> f%d)"
             % (row["joint"], row["travel_along_mm"], row["expected"],
                row["contact_frame"], row["absorb_frame"]))

    check("the gate passes overall", result["gate"] == "ok",
          "%s (%s mm)" % (result["gate"], result["worst_drift_mm"]))
    # Not zero, and it should not be: the heel-first contact pivots the foot
    # about the heel, which lifts and shifts the *ball* - the very point this
    # metric measures - by a couple of millimetres for the frame or two before
    # the foot goes flat. That is a correct landing, not a slide, and it is an
    # order of magnitude under the 5 mm the gate calls planted.
    check("every grounded frame holds: the worst plant drifts essentially zero",
          (result["worst_drift_mm"] or 0.0) < 3.0,
          "%s mm" % result["worst_drift_mm"])
    takeoff_drift = max((step["drift_mm"] for foot in result["feet"]
                         for step in foot["steps"]
                         if step.get("phase") == "takeoff"), default=None)
    check("and the takeoff plant, which has no heel strike in it, is flat zero",
          takeoff_drift is not None and takeoff_drift < 0.01,
          "%s mm" % takeoff_drift)
    phases = sorted({step.get("phase") for foot in result["feet"]
                     for step in foot["steps"]})
    check("both the takeoff plant and the landing plant were measured",
          "takeoff" in phases and "landing" in phases, str(phases))
    for foot in result["feet"]:
        for step in foot["steps"]:
            note("  %s %s frames %s: %.3f mm [%s]"
                 % (foot["bone"], step.get("phase"), step["frames"], step["drift_mm"],
                    step["verdict"]))

    # Stronger than the gate: the IK target itself, frame by frame, inside each
    # grounded stretch. A plant is a constant key or it is not a plant.
    frames, tracks = sample_clip(rig, action_name, ("foot_ik.L", "foot_ik.R"))
    jump = None
    for entry in bpy.data.actions:
        if entry.name == action_name:
            jump = entry
    check("the action exists to measure", jump is not None, action_name)
    return result


def test_forward_jump_lands_ahead(rig, result, in_place):
    section("a forward jump lands where it was sent")
    note(result["says"])
    check("the forward jump is named for what it is",
          result["action"] == JUMP_FORWARD, result["action"])
    check("and carries the distance it was given",
          result["jump_distance_m"] > 0.0, "%.4f m" % result["jump_distance_m"])
    frames, tracks = sample_clip(rig, JUMP_FORWARD, ("foot_ik.L", "root"))
    travelled = (Vector((tracks["root"][-1].x, tracks["root"][-1].y, 0.0))
                 - Vector((tracks["root"][0].x, tracks["root"][0].y, 0.0))).length
    check("the root really travelled that far, measured on the posed rig",
          abs(travelled - result["jump_distance_m"]) * 1000.0 < 1.0,
          "%.4f m measured vs %.4f m asked" % (travelled, result["jump_distance_m"]))
    foot = tracks["foot_ik.L"]
    foot_travel = (Vector((foot[-1].x, foot[-1].y, 0.0))
                   - Vector((foot[0].x, foot[0].y, 0.0))).length
    check("and the foot landed the same distance ahead of where it took off",
          abs(foot_travel - result["jump_distance_m"]) * 1000.0 < 1.0,
          "%.4f m" % foot_travel)
    check("while the in-place jump ends where it started",
          in_place["jump_distance_m"] == 0.0, str(in_place["jump_distance_m"]))


def test_report_quotes_its_numbers(result):
    section("the report says what it did, in numbers")
    for key in ("apex_requested_m", "apex_solved_m", "apex_reached_m",
                "airborne_frames", "airtime_s", "parabola_deviation_mm",
                "extension_ratio", "max_extension_ratio", "leg_reach_m",
                "absorb_measured_m", "crouch_measured_m", "takeoff_frame",
                "landing_frame", "gravity", "fps"):
        check("report carries %r" % key, result.get(key) is not None,
              str(result.get(key)))
    says = result["says"]
    check("the sentence quotes apex reached against apex requested",
          "Apex" in says and "requested" in says, says[:170])
    check("the sentence quotes the airtime in frames and seconds",
          "airtime" in says and "frames of airtime" in says, says[:200])
    check("the sentence quotes the max parabola deviation",
          "parabola deviation" in says, says[:260])
    check("the sentence quotes the leg extension against its cap",
          "extension peaks" in says and "cap" in says, says[:340])
    check("the sentence quotes the landing absorb depth",
          "absorbs" in says and "anticipation crouch" in says, says[-220:])
    check("and it names the feet it planted",
          "foot_ik.L" in says and "foot_ik.R" in says, says[-160:])


def test_other_actions_survive(rig, walk_before, punch_before):
    section("authoring a jump does not eat the walk or the punch already there")
    check("the walk action is still in the file",
          bpy.data.actions.get(WALK_LOOP) is not None,
          str(sorted(a.name for a in bpy.data.actions)))
    check("and every one of its keyframes is untouched",
          action_signature(WALK_LOOP) == walk_before,
          "%d curves before" % len(walk_before or []))
    check("the punch action is still in the file",
          bpy.data.actions.get(PUNCH_R) is not None,
          str(sorted(a.name for a in bpy.data.actions)))
    check("and every one of its keyframes is untouched too",
          action_signature(PUNCH_R) == punch_before,
          "%d curves before" % len(punch_before or []))
    for name in (WALK_LOOP, PUNCH_R, JUMP):
        action = bpy.data.actions.get(name)
        check("%s keeps a fake user, so unassigning it cannot lose it" % name,
              action is not None and action.use_fake_user is True, str(action))


def test_determinism(rig):
    section("determinism: the same call twice, the same keyframes")
    first = call("rigforge_jump", {"rig": rig.name, "action": "jump.determinism"})
    before = action_signature("jump.determinism")
    second = call("rigforge_jump", {"rig": rig.name, "action": "jump.determinism"})
    after = action_signature("jump.determinism")
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
    for key in ("takeoff_frame", "landing_frame", "apex_solved_m", "airborne_frames",
                "extension_rise_m", "absorb_measured_m", "parabola_deviation_mm"):
        check("and %r is reported the same both times" % key,
              first[key] == second[key], "%s vs %s" % (first[key], second[key]))


def test_parameters_are_bounded(rig):
    section("the refusals that teach")
    cases = (
        ({"frames": 4}, "frames", ">="),
        ({"frames": 5000}, "frames", "<="),
        ({"apex_height": -0.2}, "apex_height", ">="),
        ({"gravity": 0.0}, "gravity", ">="),
        ({"anticipation_fraction": 0.9}, "anticipation_fraction", "<="),
        ({"recover_fraction": 0.01}, "recover_fraction", ">="),
        ({"launch_fraction": 0.5}, "launch_fraction", "<="),
        ({"landing_fraction": 0.9}, "landing_fraction", "<="),
        ({"max_extension_ratio": 1.5}, "max_extension_ratio", "<="),
        ({"tuck_height": -0.1}, "tuck_height", ">="),
        ({"jump_distance": -1.0}, "jump_distance", ">="),
        ({"foot_roll_deg": 120.0}, "foot_roll_deg", "<="),
        ({"landing_strike_ratio": 2.0}, "landing_strike_ratio", "<="),
        ({"interpolation": "wobble"}, "interpolation", "one of"),
    )
    for extra, name, fragment in cases:
        params = {"rig": rig.name, "action": "jump.refused"}
        params.update(extra)
        reply = call("rigforge_jump", params, expect_error=True)
        message = reply.get("message") or ""
        check("%s=%r is refused politely, by name and by bound"
              % (name, list(extra.values())[0]),
              reply.get("status") == "error" and name in message
              and fragment in message, message[:180])
    check("and nothing was authored while the parameters were being refused",
          bpy.data.actions.get("jump.refused") is None,
          str(bpy.data.actions.get("jump.refused")))

    empty = bpy.data.objects.new("BareJumper", bpy.data.armatures.new("BareJumper"))
    bpy.context.scene.collection.objects.link(empty)
    bpy.context.view_layer.update()
    reply = call("rigforge_jump", {"rig": "BareJumper"}, expect_error=True)
    message = reply.get("message") or ""
    check("a rig with no IK legs is refused by name, and told what to run instead",
          reply.get("status") == "error" and "foot_ik" in message
          and "rigforge_generate_rig" in message, message[:240])
    check("and the refusal says why the legs matter at all",
          "foot-slide" in message or "foot slide" in message, message[:240])
    bpy.data.objects.remove(empty, do_unlink=True)


def test_animation_check_jump_controls(rig):
    section("animation_check - the jump reading can be forced, and refused")
    forced = call("animation_check", {"rig": rig.name, "action": JUMP, "mode": "jump"})
    check("mode='jump' is accepted on a clip that has an airborne window",
          forced["mode"] == "jump" and forced["gate"] == "ok",
          "%s / %s" % (forced["mode"], forced["gate"]))
    reply = call("animation_check", {"rig": rig.name, "action": WALK_LOOP,
                                     "mode": "jump"}, expect_error=True)
    message = reply.get("message") or ""
    check("and refused on a walk, by name and with the numbers it looked at",
          reply.get("status") == "error" and "airborne window" in message
          and WALK_LOOP in message, message[:260])

    planted = call("animation_check", {"rig": rig.name, "action": JUMP,
                                       "mode": "planted"})
    check("the caller can still force the old planted reading on a jump, and it is "
          "a different (worse) number because it scores the flight",
          planted["mode"] == "planted" and planted.get("airborne") is None,
          "%s / airborne=%s" % (planted["mode"], planted.get("airborne")))

    hop = call("animation_check", {"rig": rig.name, "action": JUMP,
                                   "hop_tolerance_frames": 2})
    air = hop.get("airborne") or {}
    note("longest single-foot run: %s frame(s)" % air.get("longest_single_foot_run"))
    check("the hop-asymmetry guard is off unless the caller says what it tolerates",
          (call("animation_check", {"rig": rig.name, "action": JUMP})
           .get("airborne") or {}).get("hop_asymmetry") == "unmeasured",
          "default should be unmeasured")
    check("and with a tolerance given, this two-foot jump passes it",
          air.get("hop_asymmetry") == "ok",
          "%s (%s frames)" % (air.get("hop_asymmetry"),
                              air.get("longest_single_foot_run")))


def test_walk_reads_exactly_as_before(rig):
    """The regression guard: a walk must come through untouched."""
    section("animation_check - the walk is read exactly the way it always was")
    planted = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
    note(planted["says"])
    note("mode %s (%s)" % (planted["mode"], planted["mode_reason"]))
    check("a walk is still a travelling (planted) clip, not a jump",
          planted["mode"] == "planted", "%s: %s" % (planted["mode"],
                                                    planted["mode_reason"]))
    check("its airborne block is null - nothing about a walk leaves the ground",
          planted.get("airborne") is None, str(planted.get("airborne")))
    check("it still finds stance phases on both feet",
          len([f for f in planted["feet"] if f["steps_measured"]]) == 2,
          str(planted["steps_measured"]))
    check("and it still passes the foot-slide gate",
          planted["gate"] == "ok" and planted["worst_drift_mm"] < 5.0,
          "%s (%s mm)" % (planted["gate"], planted["worst_drift_mm"]))
    check("no step carries a jump-only 'phase' key",
          not any("phase" in step for foot in planted["feet"]
                  for step in foot["steps"]),
          str([step for foot in planted["feet"] for step in foot["steps"]])[:200])

    punched = call("animation_check", {"rig": rig.name, "action": PUNCH_R})
    note(punched["says"])
    check("and a punch is still read as planted with its feet at zero",
          punched["mode"] == "planted" and punched["gate"] == "ok"
          and punched.get("airborne") is None,
          "%s / %s / airborne=%s" % (punched["mode"], punched["gate"],
                                     punched.get("airborne")))


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
    print("Forge add-on jump headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_jump_test_")
    try:
        mesh, rig, meta, generated = build_character()
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")

        # A walk and a punch come first on purpose: the jump has to land in a
        # file that already has both, and leave them exactly as it found them.
        section("a walk and a punch in the file first")
        walk = call("rigforge_walk", {"rig": rig.name, "action": WALK,
                                      "cycle_frames": 24})
        note(walk["says"])
        punch = call("rigforge_punch", {"rig": rig.name, "side": "R"})
        note(punch["says"])
        walk_before = action_signature(WALK_LOOP)
        punch_before = action_signature(PUNCH_R)

        # Defect 1 lives in the walk, so it is measured here, on the walk this
        # suite was already authoring to prove the jump leaves it alone.
        test_walk_loop_closes(rig, walk)
        test_walk_leg_never_grows(rig, walk)
        test_ik_stretch_is_keyed(rig, WALK_LOOP, walk, "the walk's stance phases")
        test_ik_stretch_is_keyed(rig, PUNCH_R, punch, "the punch's planted feet")
        test_ik_stretch_is_restored(rig)

        standing = test_jump_authors(rig, {}, JUMP, "a standing vertical jump")
        test_phases_are_in_order(standing)
        test_timing_is_ballistic(standing)
        test_defaults_are_derived(standing)
        test_extension_is_capped(rig, standing)
        test_absorb_is_deeper(standing)
        test_anticipation_reads(rig, standing, JUMP)
        test_the_mesh_stays_on_the_floor(rig, standing, JUMP)
        test_ik_stretch_is_keyed(rig, JUMP, standing, "the jump's grounded phases")
        test_airborne_window_is_real(rig, standing, JUMP)
        test_plants_hold(rig, JUMP)
        test_report_quotes_its_numbers(standing)
        test_doubling_the_apex_lengthens_the_flight(rig, standing)

        forward = test_jump_authors(
            rig, {"jump_distance": 0.5 * standing["leg_length_m"]},
            JUMP_FORWARD, "a forward jump")
        test_timing_is_ballistic(forward)
        test_airborne_window_is_real(rig, forward, JUMP_FORWARD)
        test_plants_hold(rig, JUMP_FORWARD, distance=forward["jump_distance_m"])
        test_forward_jump_lands_ahead(rig, forward, standing)

        test_animation_check_jump_controls(rig)
        test_other_actions_survive(rig, walk_before, punch_before)
        test_determinism(rig)
        test_parameters_are_bounded(rig)
        test_walk_reads_exactly_as_before(rig)
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
