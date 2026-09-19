"""Headless add-on tests for the IK / locomotion layer.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_rigik.py

Needs no geometry service and downloads nothing.  The character is Phase 3's
synthetic sculpt, tagged the way Phase 4 expects, carried through retopo ->
metarig -> generate, so what is under test is the rig the pipeline actually
produces rather than a hand-built skeleton that agrees with the test.

**What this suite is for.**  An audit of a generated character found a walk
cycle keyed on ``thigh_fk`` / ``shin_fk`` — a pure-FK leg, which has nothing at
all holding the foot on the ground between keys.  That is the oldest artefact
in game animation and it is *measurable*, so this suite measures it:

* the generated rig really does carry leg IK, arm IK, pole targets, a
  three-pivot foot roll and a per-limb FK/IK switch (``rigforge_ik``);
* the IK is **control-layer only** — the deform set is byte-identical before
  and after, which is why skinning, ``rig_check`` and the Godot export cannot
  notice it;
* ``rigforge_walk`` keys the feet through those IK targets with the stance
  phases world-locked;
* ``animation_check`` measures stance drift in millimetres, and **proves
  itself both ways**: the IK walk comes in near zero, a deliberately FK-keyed
  walk comes in at centimetres;
* the Godot export bakes the IK result onto the deform bones.

The two ``--background`` facts that shape this file are the usual ones: no
event loop (the harness drains the server queue from the main thread) and no
window.
"""

import inspect
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

PORT = 9907  # not 9876 (a live session) and not 9878-9881 (phases 2-5)
SCULPT = "Sculpt"
RETOPO = SCULPT + "_retopo"

WALK = "walk"
WALK_LOOP = WALK + "-loop"
TREADMILL = "walk-inplace"
TREADMILL_LOOP = TREADMILL + "-loop"
FKWALK = "fkwalk"
FKWALK_LOOP = FKWALK + "-loop"

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
    """Phase 3's sculpt, tagged, retopologised, metarigged and generated."""
    section("the character (Phase 3/4 builders, reused)")
    import headless_phase4 as phase4

    obj, regions = phase4.build_tagged_biped()
    for name, faces in sorted(regions.items()):
        if faces:
            call("rigforge_tag", {"object": obj.name, "tag": name, "faces": faces,
                                  "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get",
                               "archetype": "biped"})
    started = time.monotonic()
    call("rigforge_retopo", {"object": obj.name, "target_faces": 4000,
                             "platform": "mobile", "lods": 0})
    retopo = bpy.data.objects.get(RETOPO)
    if not check("the retopo mesh exists", retopo is not None):
        raise AssertionError("no retopo mesh")

    meta = call("rigforge_metarig", {"object": RETOPO, "archetype": "auto"})
    generated = call("rigforge_generate_rig", {"metarig": meta["metarig"],
                                               "mesh": RETOPO})
    rig = bpy.data.objects.get(generated["rig"])
    check("the rig exists", rig is not None, generated["rig"])
    note("retopo+rig took %.1fs" % (time.monotonic() - started))
    return retopo, rig, meta, generated


# --- what the rig builder actually is ---------------------------------------

def test_what_the_builder_is(meta, generated, rig):
    section("the rig builder: Rigify, not a 29-bone hand-build")

    check("the metarig is one of Rigify's own templates",
          "metarig" in (generated.get("metarig") or "")
          or meta.get("metarig_operator", "").startswith("armature_"),
          "%s / %s" % (generated.get("metarig"), meta.get("metarig_operator")))
    note("metarig %r: %d bone(s), preset %s (%s)"
         % (meta["metarig"], meta["bone_count"], meta.get("preset"),
            meta.get("preset_reason")))
    note("generated rig %r: %d bone(s), %d deform, %d control"
         % (generated["rig"], generated["bone_count"], generated["deform_bones"],
            generated["control_bones"]))

    # The audit's "29 bones" is the METARIG. Generation multiplies it.
    check("generation turns the metarig into a much larger control rig",
          generated["bone_count"] > 4 * meta["bone_count"],
          "%d metarig bones -> %d rig bones"
          % (meta["bone_count"], generated["bone_count"]))
    check("and most of those bones are controls, not deform bones",
          generated["control_bones"] > 3 * generated["deform_bones"],
          "%d control vs %d deform"
          % (generated["control_bones"], generated["deform_bones"]))

    ik = generated.get("ik") or {}
    check("generate_rig reports the IK layer it left the rig on", bool(ik), str(ik)[:160])
    check("with all four biped limbs",
          sorted(ik.get("limb_names") or []) == ["arm.L", "arm.R", "leg.L", "leg.R"],
          str(ik.get("limb_names")))
    check("legs default to IK and arms to FK (the game convention)",
          (ik.get("convention") or {}) == {"leg": "ik", "arm": "fk"},
          str(ik.get("convention")))
    check("and the pole targets were switched on",
          len(ik.get("poles") or []) == 4, str(ik.get("poles")))
    note("ik says: %s" % ik.get("says"))


# --- rigforge_ik ------------------------------------------------------------

def test_ik_report(rig):
    section("rigforge_ik - what the control layer has")
    result = call("rigforge_ik", {"rig": rig.name})
    limbs = {entry["name"]: entry for entry in result["limbs"]}
    check("it finds both legs and both arms",
          sorted(limbs) == ["arm.L", "arm.R", "leg.L", "leg.R"], str(sorted(limbs)))
    check("report changes nothing", result["changed"] == [] and result["action"] == "report",
          str(result["changed"]))

    for side in ("L", "R"):
        leg = limbs.get("leg.%s" % side) or {}
        check("leg.%s has a foot IK target" % side,
              leg.get("ik_target") == "foot_ik.%s" % side, str(leg.get("ik_target")))
        check("leg.%s has a knee pole target" % side,
              leg.get("pole_target") == "thigh_ik_target.%s" % side,
              str(leg.get("pole_target")))
        check("leg.%s's pole is switched on" % side, leg.get("pole_enabled") is True,
              str(leg.get("pole_enabled")))
        check("leg.%s has the three foot-roll pivots" % side,
              sorted((leg.get("roll_pivots") or {})) == ["heel", "spin", "toe"],
              str(leg.get("roll_pivots")))
        check("leg.%s's FK/IK switch is a property on thigh_parent.%s" % (side, side),
              leg.get("switch_bone") == "thigh_parent.%s" % side
              and leg.get("switch_prop") == "IK_FK",
              "%s[%s]" % (leg.get("switch_bone"), leg.get("switch_prop")))
        check("leg.%s is on IK" % side, leg.get("mode") == "ik", str(leg.get("ik_fk")))
        constraints = leg.get("ik_constraints") or []
        check("leg.%s really carries an IK constraint, with a target" % side,
              bool(constraints) and all(c["subtarget"] for c in constraints),
              str(constraints)[:200])
        check("and it solves a two-bone chain (thigh + shin)",
              leg.get("chain_count") == 2, str(leg.get("chain_count")))
        check("leg.%s names the deform bones it drives" % side,
              len(leg.get("deform_bones") or []) >= 4, str(leg.get("deform_bones")))

        arm = limbs.get("arm.%s" % side) or {}
        check("arm.%s has a hand IK target and an elbow pole" % side,
              arm.get("ik_target") == "hand_ik.%s" % side
              and arm.get("pole_target") == "upper_arm_ik_target.%s" % side,
              "%s / %s" % (arm.get("ik_target"), arm.get("pole_target")))
        check("arm.%s is on FK (arms swing on arcs; only the legs are planted)" % side,
              arm.get("mode") == "fk", str(arm.get("ik_fk")))

    reply = call("rigforge_ik", {"rig": rig.name, "limbs": ["tail.L"]}, expect_error=True)
    check("an unknown limb is refused with the limbs the rig does have",
          reply.get("status") == "error" and "leg.L" in (reply.get("message") or ""),
          (reply.get("message") or "")[:200])
    return result


def test_ik_is_control_layer_only(rig, mesh):
    section("the IK layer touches no deform bone")
    before_bones = sorted(b.name for b in rig.data.bones)
    before_deform = sorted(b.name for b in rig.data.bones if b.use_deform)
    before_groups = sorted(g.name for g in mesh.vertex_groups)

    call("rigforge_ik", {"rig": rig.name, "action": "set", "mode": "fk"})
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})

    check("no bone was added or removed",
          sorted(b.name for b in rig.data.bones) == before_bones,
          str(set(b.name for b in rig.data.bones) ^ set(before_bones)))
    check("the deform set is identical",
          sorted(b.name for b in rig.data.bones if b.use_deform) == before_deform,
          "%d vs %d" % (len([b for b in rig.data.bones if b.use_deform]),
                        len(before_deform)))
    check("and so are the mesh's vertex groups (skinning cannot notice IK)",
          sorted(g.name for g in mesh.vertex_groups) == before_groups,
          str(set(g.name for g in mesh.vertex_groups) ^ set(before_groups)))


# --- the chains actually solve ----------------------------------------------

def world_head(rig, name):
    return rig.matrix_world @ rig.pose.bones[name].head


def world_tail(rig, name):
    return rig.matrix_world @ rig.pose.bones[name].tail


def _clear_pose(rig):
    for bone in rig.pose.bones:
        bone.matrix_basis.identity()
    bpy.context.view_layer.update()


def test_ik_solves(rig):
    section("the leg IK chain solves")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    _clear_pose(rig)
    rest = {name: world_head(rig, name).copy()
            for name in ("DEF-thigh.L", "DEF-shin.L", "DEF-foot.L")}
    lift = 0.08 * max(rig.dimensions)

    foot = rig.pose.bones["foot_ik.L"]
    foot.location = (0.0, 0.0, lift)
    bpy.context.view_layer.update()
    moved = {name: (world_head(rig, name) - point).length * 1000.0
             for name, point in rest.items()}
    note("moving foot_ik.L up %.0f mm: %s"
         % (lift * 1000.0, {k: round(v, 1) for k, v in moved.items()}))
    check("moving the foot IK target moves the shin",
          moved["DEF-shin.L"] > 5.0, "%.2f mm" % moved["DEF-shin.L"])
    check("and the foot",
          moved["DEF-foot.L"] > 5.0, "%.2f mm" % moved["DEF-foot.L"])
    check("but not the thigh's root - IK solves upward from the target, it does not "
          "drag the hip", moved["DEF-thigh.L"] < 1.0, "%.2f mm" % moved["DEF-thigh.L"])

    # FK mode: the same target must now do nothing at all. This is the false
    # pass every FK-only pipeline lives inside.
    call("rigforge_ik", {"rig": rig.name, "action": "set", "mode": "fk"})
    bpy.context.view_layer.update()
    fk_moved = (world_head(rig, "DEF-foot.L") - rest["DEF-foot.L"]).length * 1000.0
    check("with the limb switched to FK the same target moves nothing",
          fk_moved < 1.0, "%.2f mm" % fk_moved)
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    _clear_pose(rig)


def test_pole_targets(rig):
    section("the knee pole target steers the knee")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": True})
    _clear_pose(rig)
    # Bend the knee first: a straight leg has no plane for a pole to rotate.
    rig.pose.bones["foot_ik.L"].location = (0.0, 0.0, 0.12 * max(rig.dimensions))
    bpy.context.view_layer.update()
    bent = world_head(rig, "DEF-shin.L").copy()

    # Several directions, largest wins: a pole that happens to land in the leg's
    # existing plane rotates the knee by nothing at all, and which direction that
    # is depends on the sculpt this rig was fitted to.
    reach = 0.25 * max(rig.dimensions)
    offsets = [(reach, 0.0, 0.0), (-reach, 0.0, 0.0), (0.0, reach, 0.0),
               (0.0, -reach, 0.0), (0.0, 0.0, reach)]
    pole = rig.pose.bones["thigh_ik_target.L"]

    def travel_for(base):
        worst = 0.0
        for offset in offsets:
            pole.location = offset
            bpy.context.view_layer.update()
            worst = max(worst, (world_head(rig, "DEF-shin.L") - base).length * 1000.0)
        pole.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        return worst

    with_pole = travel_for(bent)

    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": False})
    bpy.context.view_layer.update()
    without_pole = travel_for(world_head(rig, "DEF-shin.L").copy())

    note("knee travel over %d pole offsets of %.0f mm: %.1f mm with poles on, "
         "%.1f mm off" % (len(offsets), reach * 1000.0, with_pole, without_pole))
    check("with the pole enabled the knee follows it", with_pole > 5.0,
          "%.2f mm" % with_pole)
    check("with it disabled the pole bone is inert - which is what the rig ships as, "
          "and why enabling it is the refinement", without_pole < 1.0,
          "%.2f mm" % without_pole)

    pole.location = (0.0, 0.0, 0.0)
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk",
                         "poles": True})
    _clear_pose(rig)


def test_foot_roll(rig):
    section("the three-pivot foot roll")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    _clear_pose(rig)
    heel = rig.pose.bones["foot_heel_ik.L"]
    heel.rotation_mode = "XYZ"
    rest = {"ankle": world_head(rig, "DEF-foot.L").copy(),
            "ball": world_head(rig, "DEF-toe.L").copy(),
            "toe": world_tail(rig, "DEF-toe.L").copy()}

    heel.rotation_euler = (math.radians(25.0), 0.0, 0.0)
    bpy.context.view_layer.update()
    ball_move = (world_head(rig, "DEF-toe.L") - rest["ball"]).length * 1000.0
    toe_move = (world_tail(rig, "DEF-toe.L") - rest["toe"]).length * 1000.0
    ankle_move = (world_head(rig, "DEF-foot.L") - rest["ankle"]).length * 1000.0
    note("heel control +25 deg: ankle %.1f mm, ball %.1f mm, toe %.1f mm"
         % (ankle_move, ball_move, toe_move))
    check("rolling the heel control forward lifts the ankle", ankle_move > 5.0,
          "%.2f mm" % ankle_move)
    check("while the ball of the foot stays planted - this is the ball pivot, and it "
          "is why the metric measures the ball and not the ankle",
          ball_move < 0.5 and toe_move < 0.5,
          "ball %.3f mm, toe %.3f mm" % (ball_move, toe_move))

    heel.rotation_euler = (math.radians(-25.0), 0.0, 0.0)
    bpy.context.view_layer.update()
    strike_ball = (world_head(rig, "DEF-toe.L") - rest["ball"]).length * 1000.0
    check("rolling it back pivots about the heel instead (the strike): the ball lifts",
          strike_ball > 5.0, "%.2f mm" % strike_ball)

    heel.rotation_euler = (0.0, 0.0, 0.0)
    _clear_pose(rig)


# --- authoring --------------------------------------------------------------

def _action_bones(action):
    from forge.tools import rigforge_rig as rr

    names = set()
    for curve in rr.action_fcurves(action):
        path = curve.data_path
        if path.startswith('pose.bones["'):
            names.add(path.split('"')[1])
    return names


def test_walk(rig):
    section("rigforge_walk - a walk keyed on the IK targets")
    result = call("rigforge_walk", {"rig": rig.name, "action": WALK,
                                    "cycle_frames": 32})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    note(result["says"])
    check("the action was created under the -loop name",
          result["action"] == WALK_LOOP and result["loop"] is True, result["action"])
    check("it is a travelling clip by default (root motion)",
          result["travel"] is True, str(result["travel"]))
    check("the stride is derived from this rig's own leg, not a constant in metres",
          0.3 * result["leg_length_m"] < result["stride_m"] < 2.2 * result["leg_length_m"],
          "stride %.3f m on a %.3f m leg" % (result["stride_m"], result["leg_length_m"]))
    check("the default stride is inside the leg's reach (no stretch, no clamp)",
          result["step_length_reach_clamped"] is False,
          str(result["step_length_reach_clamped"]))
    check("it names the forward axis it derived from the toes",
          len(result["forward_axis"]) == 3 and abs(
              max(result["forward_axis"], key=abs)) > 0.9,
          str(result["forward_axis"]))

    action = bpy.data.actions.get(WALK_LOOP)
    if not check("the action exists in the file", action is not None):
        return None
    bones = _action_bones(action)
    check("both feet are keyed on their IK targets",
          {"foot_ik.L", "foot_ik.R"} <= bones, str(sorted(bones)))
    check("NOT on the FK leg chain - that is the anti-pattern this replaces",
          not ({"thigh_fk.L", "thigh_fk.R", "shin_fk.L", "shin_fk.R"} & bones),
          str(sorted(n for n in bones if "_fk" in n)))
    check("the foot roll is keyed on the heel pivots",
          {"foot_heel_ik.L", "foot_heel_ik.R"} <= bones, str(sorted(bones)))
    check("the root carries the travel", "root" in bones, str(sorted(bones)))
    check("the arms swing in FK", {"upper_arm_fk.L", "upper_arm_fk.R"} <= bones,
          str(sorted(bones)))
    check("the FK/IK switches are keyframed, so the export bake resolves what the "
          "animator saw", {"thigh_parent.L", "thigh_parent.R"} <= bones,
          str(sorted(bones)))

    ik = call("rigforge_ik", {"rig": rig.name})
    modes = {entry["name"]: entry["mode"] for entry in ik["limbs"]}
    check("authoring a walk left the legs on IK", modes.get("leg.L") == "ik"
          and modes.get("leg.R") == "ik", str(modes))

    stance = {entry["foot"]: entry["stance_frames"] for entry in result["feet"]}
    check("each foot spends most of the cycle planted",
          all(value >= 0.5 * result["cycle_frames"] for value in stance.values()),
          str(stance))

    section("rigforge_walk - a stride the leg cannot reach")
    # A leg that cannot reach its own target does not arrive where it was keyed,
    # so it slides on the deform bones while the control sits still. The command
    # bends the knees for it, and shortens the step only when that runs out.
    longer = call("rigforge_walk", {"rig": rig.name, "action": "stride",
                                    "cycle_frames": 24,
                                    "step_length": 0.6 * result["leg_length_m"]})
    for warning in longer.get("warnings") or []:
        note("warning: %s" % warning)
    # Not refused, and paid for in the right order: the knees bend first and
    # the stride gives only when the crouch has spent everything it is allowed.
    # Measured against this build's own ceiling rather than against the default
    # walk's crouch, because on a rig with no anatomical pre-bend the default
    # walk is already at that ceiling - which is a fact about the rig, not a
    # failure of this clip.
    check("a long stride is bought with knee bend before it is ever refused",
          longer["hip_lower_deepened"] is True
          and (not longer["step_length_reach_clamped"]
               or longer["hip_lower_m"] >= longer["max_hip_lower_m"] - 1e-6),
          "hips at %.3f m of a %.3f m ceiling (default walk needed %.3f m), "
          "clamped=%s" % (longer["hip_lower_m"], longer["max_hip_lower_m"],
                          result["hip_lower_m"],
                          longer["step_length_reach_clamped"]))
    # The contract, not the wish.  `rigforge_walk` no longer takes the rest-pose
    # solve's word for what the leg can reach: it authors the cycle, measures
    # the hip-to-ankle span on the deform chain, and re-keys with deeper hips -
    # or, when the crouch has run out, a shorter step - until no frame asks the
    # leg for more than it has.  So whether a 0.6-leg stride survives is a fact
    # about *this build's* geometry, and the test is that the command tells the
    # truth about which case it is: the step survives when the legs can reach
    # it, and shortens with a warning quoting the measured reach when they
    # cannot.  Asserting the step unconditionally was only ever passing because
    # the old solve was optimistic - on the werewolf that optimism was 1.3258 of
    # the leg's own length, paid for in stretched deform bones.
    asked = 0.6 * result["leg_length_m"]
    reached = longer["leg_reach_ratio"]
    note("asked %.3f m; got %.3f m with the worst planted leg at %.5f of its own "
         "measured reach after %d authoring pass(es); hips at %.3f m of a %.3f m "
         "ceiling"
         % (asked, longer["step_length_m"], reached or 0.0,
            longer["reach_passes"], longer["hip_lower_m"],
            longer["max_hip_lower_m"]))
    note("worst frame: %s" % longer["leg_reach_worst"])
    if not longer["step_length_reach_clamped"]:
        check("and the step asked for survives, because this build's legs can "
              "reach it", abs(longer["step_length_m"] - asked) < 1e-4,
              "%.4f m against %.4f m asked" % (longer["step_length_m"], asked))
    else:
        check("and where this build's legs cannot reach it the step is shortened "
              "rather than stretched into - by exactly what the measured reach "
              "left, after the crouch had spent everything it was allowed",
              longer["step_length_m"] < asked
              and longer["hip_lower_m"] >= longer["max_hip_lower_m"] - 1e-6,
              "%.4f m of %.4f m asked, hips %.4f m of %.4f m"
              % (longer["step_length_m"], asked, longer["hip_lower_m"],
                 longer["max_hip_lower_m"]))
        check("...and says so in a warning that quotes the reach it measured",
              any("reach" in warning for warning in longer.get("warnings") or []),
              str(longer.get("warnings"))[:240])
    check("either way no planted leg is asked to stand further from the hip than "
          "it reaches - which is the whole reason the step may not survive",
          reached is not None and reached <= 1.0,
          "%.5f of its own measured reach" % (reached or 0.0))

    greedy = call("rigforge_walk", {"rig": rig.name, "action": "lunge",
                                    "cycle_frames": 24,
                                    "step_length": 3.0 * result["leg_length_m"]})
    for warning in greedy.get("warnings") or []:
        note("warning: %s" % warning)
    check("but a stride longer than the leg is shortened rather than stretched into",
          greedy["step_length_reach_clamped"] is True
          and greedy["step_length_m"] < greedy["leg_length_m"],
          "%.3f m step on a %.3f m leg" % (greedy["step_length_m"],
                                           greedy["leg_length_m"]))
    check("and it says so in a warning rather than silently",
          any("reach" in w for w in greedy.get("warnings") or []),
          str(greedy.get("warnings"))[:200])
    for name in ("stride-loop", "lunge-loop"):
        reached = call("animation_check", {"rig": rig.name, "action": name})
        note("%s: %s" % (name, reached["says"]))
        check("%s still plants its feet" % name, reached["gate"] == "ok",
              "%s (%s mm)" % (reached["gate"], reached["worst_drift_mm"]))

    section("rigforge_walk - in place (the treadmill clip)")
    second = call("rigforge_walk", {"rig": rig.name, "action": TREADMILL,
                                    "cycle_frames": 32, "travel": False})
    check("the in-place clip does not key the root travel",
          second["travel"] is False, str(second["travel"]))
    note(second["says"])
    return result


def test_walk_errors(rig):
    section("rigforge_walk - the refusal that teaches")
    empty = bpy.data.objects.new("BareRig", bpy.data.armatures.new("BareRig"))
    bpy.context.scene.collection.objects.link(empty)
    bpy.context.view_layer.update()
    reply = call("rigforge_walk", {"rig": "BareRig", "action": "nope"},
                 expect_error=True)
    message = reply.get("message") or ""
    check("a rig with no IK legs is refused by name",
          reply.get("status") == "error" and "foot_ik" in message, message[:240])
    check("and the message says what to run instead",
          "rigforge_generate_rig" in message and "foot-slide" in message,
          message[:240])
    bpy.data.objects.remove(empty, do_unlink=True)


def author_fk_walk(rig):
    """The anti-pattern, on purpose: a walk keyed on thigh_fk / shin_fk."""
    section("the anti-pattern - a walk keyed in FK")
    keys = []
    for frame in range(1, 34, 4):
        t = (frame - 1) / 32.0
        for side, phase in (("L", 0.0), ("R", math.pi)):
            keys.append({"bone": "thigh_fk.%s" % side, "frame": frame,
                         "rotation_euler_deg":
                             [25.0 * math.sin(2 * math.pi * t + phase), 0.0, 0.0]})
            keys.append({"bone": "shin_fk.%s" % side, "frame": frame,
                         "rotation_euler_deg":
                             [-30.0 * max(0.0, math.sin(2 * math.pi * t + phase + 1.2)),
                              0.0, 0.0]})
    result = call("rigforge_keyframe", {"rig": rig.name, "action": FKWALK, "keys": keys,
                                        "clear": True, "loop": True})
    check("keying the FK leg chain switches those legs to FK",
          sorted(result["fk_limbs"] or []) == ["leg.L", "leg.R"],
          str(result["fk_limbs"]))
    return FKWALK_LOOP


# --- the metric -------------------------------------------------------------

def test_foot_slide_metric(rig, fk_action):
    section("animation_check - the foot-slide metric")
    planted = call("animation_check", {"rig": rig.name, "action": WALK_LOOP})
    note(planted["says"])
    note("mode: %s (%s)" % (planted["mode"], planted["mode_reason"]))
    for foot in planted["feet"]:
        note("  %s on %s (%s): %d step(s), worst %s mm, excursion %.0f mm"
             % (foot["foot"], foot["bone"], foot["point"], foot["steps_measured"],
                foot["worst_drift_mm"], foot["excursion_mm"]))
        for step in foot["steps"]:
            note("      step %d frames %s: %.2f mm  [%s]"
                 % (step["step"], step["frames"], step["drift_mm"], step["verdict"]))

    check("it measured the ball of the foot, not the ankle",
          all(foot["bone"].endswith(("toe.L", "toe.R")) or foot["point"] == "tail"
              for foot in planted["feet"]),
          str([(f["bone"], f["point"]) for f in planted["feet"]]))
    check("it detected a travelling clip", planted["mode"] == "planted",
          planted["mode_reason"])
    check("it found stance phases on both feet",
          len([f for f in planted["feet"] if f["steps_measured"]]) == 2
          and planted["steps_measured"] >= 2, str(planted["steps_measured"]))
    check("the IK walk's planted feet hold to within a few millimetres",
          planted["worst_drift_mm"] is not None and planted["worst_drift_mm"] < 5.0,
          "%s mm" % planted["worst_drift_mm"])
    check("so the gate passes", planted["gate"] == "ok", planted["gate"])
    check("and it says which threshold judged it, and that it is a heuristic",
          "heuristic" in planted["threshold_tier"]
          and planted["thresholds"]["drift_mm"]["ok"] > 0,
          str(planted["thresholds"]))
    check("the pose and the action were put back", planted["pose_restored"] is True
          and bpy.context.scene.frame_current >= 0, str(planted["pose_restored"]))

    section("animation_check - the in-place clip is judged against one shared speed")
    treadmill = call("animation_check", {"rig": rig.name, "action": TREADMILL_LOOP})
    note(treadmill["says"])
    check("it detected an in-place clip", treadmill["mode"] == "in_place",
          treadmill["mode_reason"])
    check("and measured the treadmill speed it removed",
          treadmill["treadmill_mm_per_frame"] > 1.0,
          "%.2f mm/frame" % treadmill["treadmill_mm_per_frame"])
    check("the same cycle, authored in place, still passes",
          treadmill["gate"] == "ok" and treadmill["worst_drift_mm"] < 5.0,
          "%s mm, %s" % (treadmill["worst_drift_mm"], treadmill["gate"]))
    check("the caller can force the other reading, and it is a different number",
          call("animation_check", {"rig": rig.name, "action": TREADMILL_LOOP,
                                   "mode": "planted"})["worst_drift_mm"]
          > 10.0 * max(treadmill["worst_drift_mm"], 1e-6),
          "a treadmill clip read as travelling must look like a slide")

    section("animation_check - the metric proves itself on the FK walk")
    slid = call("animation_check", {"rig": rig.name, "action": fk_action})
    note(slid["says"])
    for foot in slid["feet"]:
        note("  %s: %d step(s), worst %s mm" % (foot["bone"], foot["steps_measured"],
                                                foot["worst_drift_mm"]))
    check("the FK-keyed walk slides by centimetres",
          slid["worst_drift_mm"] is not None and slid["worst_drift_mm"] > 20.0,
          "%s mm" % slid["worst_drift_mm"])
    check("so the gate fails", slid["gate"] == "fail", slid["gate"])
    check("and it is at least twenty times the IK walk's drift - the two are not the "
          "same clip with a different threshold",
          slid["worst_drift_mm"] > 20.0 * max(planted["worst_drift_mm"], 1e-6),
          "%s mm vs %s mm" % (slid["worst_drift_mm"], planted["worst_drift_mm"]))
    check("the verdict names the bone and the frames of the worst step",
          slid["worst_step"] and slid["worst_step"]["bone"] in slid["says"]
          and len(slid["worst_step"]["frames"]) == 2, str(slid["worst_step"]))
    check("and points at the fix", "rigforge_walk" in slid["says"], slid["says"][:200])

    section("animation_check - errors that help")
    reply = call("animation_check", {"rig": rig.name, "action": "no-such-clip"},
                 expect_error=True)
    check("an unknown action is refused with the actions that exist",
          reply.get("status") == "error" and WALK_LOOP in (reply.get("message") or ""),
          (reply.get("message") or "")[:200])
    reply = call("animation_check", {"rig": rig.name, "action": WALK_LOOP,
                                     "feet": ["DEF-nothing.L"]}, expect_error=True)
    check("so is a foot bone the rig does not have",
          reply.get("status") == "error"
          and "DEF-nothing.L" in (reply.get("message") or ""),
          (reply.get("message") or "")[:200])
    return planted, slid


def test_scoped_fk_switch(rig):
    section("the audit's bug: keying an arm must not take the legs off IK")
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})
    result = call("rigforge_keyframe", {
        "rig": rig.name, "action": "wave", "clear": True,
        "keys": [{"bone": "upper_arm_fk.L", "frame": 1,
                  "rotation_euler_deg": [0.0, 0.0, 0.0]},
                 {"bone": "upper_arm_fk.L", "frame": 10,
                  "rotation_euler_deg": [0.0, 0.0, 40.0]}]})
    check("only the keyed limb was switched", result["fk_limbs"] == ["arm.L"],
          str(result["fk_limbs"]))
    modes = {entry["name"]: entry["mode"]
             for entry in call("rigforge_ik", {"rig": rig.name})["limbs"]}
    check("both legs are still on IK", modes.get("leg.L") == "ik"
          and modes.get("leg.R") == "ik", str(modes))
    check("and the reply warns that the IK legs were left alone",
          any("stayed on IK" in w for w in result.get("warnings") or []),
          str(result.get("warnings"))[:240])

    explicit = call("rigforge_keyframe", {
        "rig": rig.name, "action": "wave", "clear": True, "fk_switch": True,
        "keys": [{"bone": "upper_arm_fk.L", "frame": 1,
                  "rotation_euler_deg": [0.0, 0.0, 0.0]}]})
    modes = {entry["name"]: entry["mode"]
             for entry in call("rigforge_ik", {"rig": rig.name})["limbs"]}
    check("fk_switch:true is still the whole rig, because a full-body mocap bake "
          "wants exactly that",
          explicit["fk_limbs"] is None and all(mode == "fk" for mode in modes.values()),
          "%s / %s" % (explicit["fk_limbs"], modes))
    call("rigforge_ik", {"rig": rig.name, "action": "set", "legs": "ik", "arms": "fk"})


# --- the Godot export -------------------------------------------------------

def test_export_bakes_ik(rig, mesh, workspace):
    section("the Godot export bakes the IK onto the deform bones")
    from forge.tools import rigforge_rig as rr

    source = bpy.data.actions.get(WALK_LOOP)
    source_bones = _action_bones(source)
    check("the source clip keys no deform bone at all - every leg pose in it is an "
          "IK solve", not any(name.startswith("DEF-") for name in source_bones),
          str(sorted(source_bones)))

    # The setting, pinned where it is written: Blender's own exporter samples,
    # and our deform bake is visual, which is what turns constraints into keys.
    bake_source = inspect.getsource(rr.bake_action_onto)
    check("bake_action_onto bakes with visual keying",
          '"visual_keying": True' in bake_source, "visual_keying missing")
    export_source = inspect.getsource(rr.cmd_rigforge_export_godot)
    check("and the glTF export forces sampling rather than trusting the curves",
          '"export_force_sampling": True' in export_source,
          "export_force_sampling missing")
    rna = [prop.identifier
           for prop in bpy.ops.export_scene.gltf.get_rna_type().properties]
    check("the installed exporter really has that property (it is not a no-op kwarg "
          "that op_kwargs quietly drops)", "export_force_sampling" in rna,
          str([name for name in rna if "sampl" in name or "anim" in name]))

    path = os.path.join(workspace, "walker.glb")
    result = call("rigforge_export_godot", {"rig": rig.name, "path": path,
                                            "actions": [WALK_LOOP], "lods": "auto"})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    check("the glTF exists", os.path.isfile(path) and os.path.getsize(path) > 1024,
          "%s bytes" % (os.path.getsize(path) if os.path.exists(path) else "missing"))

    import headless_phase4 as phase4

    doc = phase4.parse_gltf(path)
    animations = [entry.get("name") for entry in doc.get("animations", [])]
    check("the walk shipped under its own name", WALK_LOOP in animations,
          str(animations))
    nodes = doc.get("nodes", [])
    channels = doc["animations"][animations.index(WALK_LOOP)].get("channels", [])
    targets = {nodes[channel["target"]["node"]].get("name") for channel in channels}
    check("every animated node is a deform bone or the root - no IK control leaked",
          targets and all(str(name).startswith("DEF-") or name == "root"
                          for name in targets), str(sorted(targets))[:240])
    legs = {name for name in targets
            if any(part in str(name) for part in ("thigh", "shin", "foot", "toe"))}
    check("and the baked clip reaches the leg deform bones the IK was driving",
          len(legs) >= 4, str(sorted(legs)))

    section("the bake, measured in place")
    # Same machinery the export uses, run here so the result can be inspected.
    collection = bpy.data.collections.new("FORGE_IK_BAKE_TEST")
    bpy.context.scene.collection.children.link(collection)
    baked = None
    try:
        export_rig = rr.build_deform_rig(rig, "%s_ik_bake" % rig.name, collection, [])
        rr.constrain_to(rig, export_rig)
        rr.assign_action(rig, source)
        export_rig.animation_data_create()
        span = source.frame_range
        start, end = int(math.floor(span[0])), int(math.ceil(span[1]))
        baked = rr.bake_action_onto(rig, export_rig, source, start, end, 1)
        rr.strip_constraints(export_rig)
        names = _action_bones(baked)
        check("the bake landed on the deform bones",
              {"DEF-shin.L", "DEF-foot.L"} <= names, str(sorted(names))[:200])
        from forge.tools import rigforge_rig as again

        varied = []
        for curve in again.action_fcurves(baked):
            if 'DEF-shin.L' not in curve.data_path:
                continue
            values = [point.co.y for point in curve.keyframe_points]
            if values and (max(values) - min(values)) > 1e-3:
                varied.append(curve.data_path.rsplit(".", 1)[-1])
        check("and DEF-shin.L really moves across the clip - the IK solve became keys, "
              "which is what Godot needs", bool(varied), str(sorted(set(varied))))
    finally:
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
        if baked is not None and baked.users == 0:
            bpy.data.actions.remove(baked)
    return path


def test_exported_clip_is_still_planted(rig, path):
    section("the exported clip still measures as planted")
    if not hasattr(bpy.ops.import_scene, "gltf"):
        note("this Blender has no glTF importer; the round trip was not run")
        return
    before = set(bpy.data.objects.keys())
    try:
        status = bpy.ops.import_scene.gltf(filepath=path)
    except RuntimeError as exc:
        note("glTF import refused the file (%s); the round trip was not run" % exc)
        return
    if "FINISHED" not in status:
        note("glTF import returned %s; the round trip was not run" % sorted(status))
        return
    imported = [bpy.data.objects[name] for name in set(bpy.data.objects.keys()) - before]
    rigs = [obj for obj in imported if obj.type == "ARMATURE"]
    try:
        if not rigs:
            note("the import produced no armature; the round trip was not run")
            return
        target = rigs[0]
        feet = [name for name in ("DEF-toe.L", "DEF-toe.R")
                if name in target.pose.bones]
        if len(feet) < 2:
            note("the imported skeleton has no DEF-toe bones (%d); not measured"
                 % len(feet))
            return
        clip = None
        for action in bpy.data.actions:
            if action.name.startswith(WALK_LOOP) and action is not bpy.data.actions.get(
                    WALK_LOOP):
                clip = action
        if clip is None:
            clip = target.animation_data.action if target.animation_data else None
        if clip is None:
            note("the imported rig carries no action; not measured")
            return
        result = call("animation_check", {"rig": target.name, "action": clip.name,
                                          "feet": feet})
        note("%s: %s" % (clip.name, result["says"]))
        check("the baked, exported, re-imported walk still holds its feet",
              result["gate"] in ("ok", "attention")
              and (result["worst_drift_mm"] or 0.0) < 20.0,
              "%s mm, %s" % (result["worst_drift_mm"], result["gate"]))
    finally:
        for obj in imported:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (ReferenceError, RuntimeError):
                continue


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
    print("Forge add-on IK / locomotion headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_rigik_test_")
    try:
        mesh, rig, meta, generated = build_character()
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")

        test_what_the_builder_is(meta, generated, rig)
        test_ik_report(rig)
        test_ik_is_control_layer_only(rig, mesh)
        test_ik_solves(rig)
        test_pole_targets(rig)
        test_foot_roll(rig)

        test_walk(rig)
        test_walk_errors(rig)
        fk_action = author_fk_walk(rig)
        test_foot_slide_metric(rig, fk_action)
        test_scoped_fk_switch(rig)

        path = test_export_bakes_ik(rig, mesh, workspace)
        test_exported_clip_is_still_planted(rig, path)
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
