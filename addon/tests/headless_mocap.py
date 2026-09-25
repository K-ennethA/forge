"""Headless tests for the mocap production path (retarget -> contract clip).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_mocap.py

Port 9925. Downloads nothing: every clip it reads is a synthesized fixture in
``addon/tests/fixtures/mocap/`` written by ``make_bvh_fixtures.py`` from
closed-form motion, and the suite first proves those files are the frozen ones
(regenerated in memory, sha256 against the pinned digest AND the committed
bytes - docs/recipes/fixture-freezing.md).

The target is the game character itself: a temp COPY of
``projects/werewolf/models/werewolf-wip-17.blend`` (its Rigify control rig,
33 DEF bones + root). The source file is never opened for writing.

Correctness is measured against the generator's own world-space joint truth
(``make_bvh_fixtures.truth``), not against Blender's importer or the code
under test: after a retarget, each DEF limb segment (hip->knee, knee->ankle,
shoulder->elbow, elbow->wrist) must point where the source segment pointed,
mapped into the rig's axes.

What is covered: fixture freeze; BVH validation and its refusals (NaN/inf,
truncated rows, upper-body-only skeleton); rest-relative transfer on a Z-up
inch 120 fps CMU-named take and a Y-up cm 30 fps Mixamo-named take yawed 30
degrees; fps resampling; scale (leg chain over leg chain); heading; the three
root-motion modes; the naming presets; IK legs; the loop search, its seam
closure and its refusal; the driver's contract block; motion_stats emission;
bake determinism; and that nothing is left behind.
"""

import hashlib
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
from mathutils import Matrix, Quaternion, Vector

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
FORGE_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
FIXTURES = os.path.join(TESTS_DIR, "fixtures", "mocap")
WIP = os.path.join(FORGE_DIR, "projects", "werewolf", "models", "werewolf-wip-17.blend")

PORT = 9925  # 9876 live; 9878-9924 taken by the other suites
RIG = "werewolf-form-a_retopo_rig"
CMU = os.path.join(FIXTURES, "walk_cmu_zup_in120.bvh")
MIXAMO = os.path.join(FIXTURES, "walk_mixamo_yup_cm30.bvh")
UPPER = os.path.join(FIXTURES, "broken_upper_body.bvh")
NAN = os.path.join(FIXTURES, "broken_nan.bvh")
TRUNCATED = os.path.join(FIXTURES, "broken_truncated.bvh")

#: internal generator frame (x forward, y up, z right) -> the rig's axes
#: (forward -Y, up +Z, right -X). Measured on the rig, not assumed: the suite
#: checks rig_forward_axis says -Y before it relies on this.
INTERNAL_TO_RIG = Matrix(((0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))

#: (generator segment start, end, DEF head at start, DEF head at end)
SEGMENTS = (
    ("femur", "tibia", "DEF-thigh.%s", "DEF-shin.%s"),
    ("tibia", "foot", "DEF-shin.%s", "DEF-foot.%s"),
    ("humerus", "radius", "DEF-upper_arm.%s", "DEF-forearm.%s"),
    ("radius", "wrist", "DEF-forearm.%s", "DEF-hand.%s"),
)

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


def call(command, params=None, timeout=600.0, expect_error=False):
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


def generator():
    if FIXTURES not in sys.path:
        sys.path.insert(0, FIXTURES)
    import make_bvh_fixtures
    return make_bvh_fixtures


def rig():
    return bpy.data.objects[RIG]


def reset_pose():
    from forge.tools import rigforge_rig as rr
    rr.assign_action(rig(), None)
    for pb in rig().pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()


def scene_state():
    return {
        "objects": set(bpy.data.objects.keys()),
        "temp": "FORGE_RETARGET_TEMP" in bpy.data.collections,
        "constraints": sorted("%s:%s" % (b.name, c.name) for b in rig().pose.bones
                              for c in b.constraints if c.name.startswith("Forge Retarget")),
    }


def direction_errors(action, start_s, yaw_truth_deg, yaw_rig_deg, step=2):
    """Worst angle (deg) per segment between the rig's DEF chain and the truth."""
    from forge.tools import rigforge_rig as rr
    g = generator()
    r = rig()
    rr.assign_action(r, action)
    scene = bpy.context.scene
    turn = Matrix.Rotation(math.radians(yaw_rig_deg), 3, "Z")
    worst = {}
    first, last = int(action.frame_range[0]), int(action.frame_range[1])
    for frame in range(first, last + 1, step):
        scene.frame_set(frame)
        truth = g.truth(start_s + (frame - first) / 24.0, yaw_truth_deg)
        for a, b, da, db in SEGMENTS:
            for side in ("L", "R"):
                want = turn @ (INTERNAL_TO_RIG @ (Vector(truth[b + "." + side])
                                                  - Vector(truth[a + "." + side]))).normalized()
                pa = r.matrix_world @ r.pose.bones[da % side].head
                pb = r.matrix_world @ r.pose.bones[db % side].head
                angle = math.degrees(want.angle((pb - pa).normalized()))
                key = "%s.%s" % (a, side)
                worst[key] = max(worst.get(key, 0.0), angle)
    rr.assign_action(r, None)
    return worst


def truth_speed(start_s, end_s, scale):
    """Least-squares hip-joint speed of the generator between two times, scaled."""
    g = generator()
    ts = [start_s + i / 120.0 for i in range(int(round((end_s - start_s) * 120)) + 1)]
    xs = [g.travel_at(t) for t in ts]
    tm = sum(ts) / len(ts)
    xm = sum(xs) / len(xs)
    slope = sum((t - tm) * (x - xm) for t, x in zip(ts, xs)) / sum((t - tm) ** 2 for t in ts)
    return slope * scale


def rig_leg_chain():
    r = rig()
    total = []
    for side in ("L", "R"):
        heads = [r.matrix_world @ r.data.bones[n % side].head_local
                 for n in ("thigh_fk.%s", "shin_fk.%s", "foot_fk.%s")]
        total.append((heads[1] - heads[0]).length + (heads[2] - heads[1]).length)
    return sum(total) / 2.0


def action_digest(action):
    from forge.tools import rigforge_rig as rr
    h = hashlib.sha256()
    for curve in sorted(rr.action_fcurves(action), key=lambda c: (c.data_path, c.array_index)):
        h.update(("%s[%d]" % (curve.data_path, curve.array_index)).encode())
        for point in curve.keyframe_points:
            h.update(("%.6f:%.6f;" % (point.co.x, point.co.y)).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------


def test_fixtures_frozen():
    section("fixtures: synthesized locally, frozen by digest")
    g = generator()
    rows = g.check_all()
    for name, built, pinned, committed in rows:
        check("%s regenerates to its pinned digest and matches the committed file" % name,
              built == pinned == committed,
              "built %s pinned %s committed %s" % (built[:12], (pinned or "-")[:12],
                                                   (committed or "-")[:12]))
    again = {name: g.digest(g.build(name)) for name, *_rest in rows}
    check("a second build in the same process is byte-identical",
          all(again[name] == built for name, built, _p, _c in rows))


def test_bvh_validation():
    section("BVH validation, before any importer sees the file")
    from forge.tools import rigforge_mocap as mocap
    from forge.tools.registry import ForgeError

    info = mocap.validate_bvh(mocap.read_bvh(CMU), CMU)
    check("the CMU take parses: 29 joints, 90 channels, 313 frames at 120 fps",
          (info["joints"], info["channels_per_frame"], info["frames"], round(info["fps"]))
          == (29, 90, 313, 120), str(info))
    info = mocap.validate_bvh(mocap.read_bvh(MIXAMO), MIXAMO)
    check("the Mixamo take parses: 24 joints, 79 frames at 30 fps",
          (info["joints"], info["frames"], round(info["fps"])) == (24, 79, 30), str(info))
    for path, needles in ((NAN, ("2 non-finite", "frame 7", "frame 12", "nan", "inf")),
                          (TRUNCATED, ("declares 30 frames and 24 motion rows",
                                       "has 89 values"))):
        try:
            mocap.validate_bvh(mocap.read_bvh(path), path)
            message = None
        except ForgeError as exc:
            message = str(exc)
        check("%s is refused with the numbers (%s)" % (os.path.basename(path),
                                                        ", ".join(needles)),
              message is not None and all(n in message for n in needles), message)
    info = mocap.validate_bvh(mocap.read_bvh(UPPER), UPPER)
    check("the upper-body take is a VALID file (its refusal is the mapping's job)",
          info["joints"] == 17, str(info))


def test_refusals():
    section("retarget refusals leave the file untouched")
    before = scene_state()
    for path, params, needles in (
            (NAN, {}, ("non-finite", "frame 7", "Nothing was imported")),
            (TRUNCATED, {}, ("declares 30 frames",)),
            (UPPER, {"require_slots": ["hips", "thigh", "shin", "foot"]},
             ("6 required slot(s) are missing", "thigh.L", "foot.R", "17 joints")),
            (UPPER, {"legs": "ik"}, ("legs: \"ik\" needs",)),
            (CMU, {"mapping": "mixamo", "require_slots": ["hips", "thigh"]},
             ("required slot(s) are missing",)),
            (CMU, {"mapping": "bogus"}, ("'mapping' must be",)),
            (CMU, {"require_slots": ["tail"]}, ("'tail' is not a slot",)),
            (CMU, {"find_loop": True, "loop_min_s": 3.0}, ("No loop window fits",
                                                           "63 frames")),
            (CMU, {"find_loop": True, "loop_max_residual_deg": 0.01, "legs": "ik",
                   "root_motion": "in_place"},
             ("leaves", "deg off at the seam", "0.0 deg 'loop_max_residual_deg'"))):
        params = dict(params, target_rig=RIG, source_path=path, action_name="refused")
        reply = call("rigforge_retarget", params, expect_error=True)
        message = reply.get("message") or ""
        check("%s %s -> refused: %s" % (os.path.basename(path),
                                        json.dumps({k: v for k, v in params.items()
                                                    if k not in ("target_rig", "source_path",
                                                                 "action_name")}),
                                        " / ".join(needles)),
              reply.get("status") == "error" and all(n in message for n in needles),
              message[:600])
    after = scene_state()
    check("no object, temp collection or constraint survived the refusals",
          after == before, str({k: (before[k], after[k]) for k in before
                                if before[k] != after[k]})[:400])
    check("no 'refused' action was left behind", "refused" not in bpy.data.actions
          and "refused-loop" not in bpy.data.actions)
    reset_pose()


def test_cmu_fk():
    section("CMU take (Z-up, +X facing, inches, 120 fps, ZYX) -> FK, rest heading")
    from forge.tools import rigforge_rig as rr
    forward, how = rr.rig_forward_axis(rig())
    check("the rig faces -Y (what INTERNAL_TO_RIG assumes), measured off its %s" % how,
          forward.dot(Vector((0.0, -1.0, 0.0))) > 0.999, str(forward))
    before = scene_state()
    result = call("rigforge_retarget", {"target_rig": RIG, "source_path": CMU,
                                        "action_name": "cmu_fk", "heading": "rest"})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    check("resampled 313 frames at 120 fps to 63 at 24 fps (2.6 s both)",
          result["resampled"] == {"source_fps": 120.0, "source_frames": 313,
                                  "output_fps": 24.0, "output_frames": 63}
          and result["frame_range"] == [1, 63], str(result["resampled"]))
    g = generator()
    expected = rig_leg_chain() / ((g.FEMUR + g.TIBIA) / 0.0254)
    check("scale is leg chain over leg chain: %.6f (inches -> metres x proportion)"
          % expected, result["scale_basis"] == "leg_chain"
          and abs(result["scale"] - expected) / expected < 1e-4, str(result["scale"]))
    o = result["orientation"]
    check("the Z-up source's up and the rig's up both snapped to world axes (lean %s / %s deg)"
          % (o.get("source_lean_deg"), o.get("target_lean_deg")),
          o.get("source_lean_deg") is not None and o.get("target_lean_deg") is not None
          and o["target_up"] == [0.0, 0.0, 1.0], json.dumps(o))
    align = result["rest_alignment_deg"]
    check("T-pose arms were reconciled onto the rig's A-pose (upper arm swing %.1f deg)"
          % align.get("upper_arm_fk.L", 0.0),
          align.get("upper_arm_fk.L", 0.0) > 45.0 and align.get("upper_arm_fk.R", 0.0) > 45.0,
          json.dumps(align))
    worst = direction_errors(bpy.data.actions["cmu_fk"], 0.0, 0.0, 0.0)
    note("worst segment error vs truth (deg): %s" % json.dumps(
        {k: round(v, 3) for k, v in worst.items()}))
    check("every DEF limb segment points where the source's did, within 0.5 deg",
          max(worst.values()) <= 0.5, json.dumps(worst))
    rm = result["root_motion"]
    want = truth_speed(0.0, 2.6, result["scale"] / 0.0254)
    check("keep: the travel came across at the source's speed x scale (%.4f m/s, want %.4f)"
          % (rm["speed_mps"], want), abs(rm["speed_mps"] - want) / want < 0.01, json.dumps(rm))
    check("and along the rig's forward axis (%.4f)" % rm["travel_along_forward"],
          rm["travel_along_forward"] > 0.999)
    tc = result["transfer_check"]
    check("transfer_check: the baked controls sit where they were sent "
          "(worst %.3f deg, %.3f mm)" % (tc["worst_rotation_deg"], tc["worst_position_mm"]),
          tc["worst_rotation_deg"] < 1.0 and tc["worst_position_mm"] < 1.0, json.dumps(tc))
    check("the auto mapping skipped the CMU extras with reasons",
          {"lhipjoint", "upperneck", "lthumb"} <= set(result["unmapped"]),
          str(result["unmapped"]))
    after = scene_state()
    check("the import left nothing behind", after == before)
    reset_pose()
    return result


def test_mixamo_heading_fps():
    section("Mixamo take (Y-up, cm, 30 fps, ZXY, performance yawed 30 deg) -> FK")
    result = call("rigforge_retarget", {"target_rig": RIG, "source_path": MIXAMO,
                                        "action_name": "mixamo_fk", "mapping": "mixamo",
                                        "heading": "travel"})
    check("the mixamo preset was used and is labelled an assumption",
          result["mapping"] == "mixamo"
          and "assumption" in (result.get("mapping_preset_status") or ""),
          result.get("mapping_preset_status"))
    check("30 -> 24 fps: 79 source frames become 63",
          result["resampled"]["output_frames"] == 63, str(result["resampled"]))
    yaw = result["heading"]["yaw_deg"]
    check("heading from the travel: the performance is turned back %.3f deg (want -30 +- 0.5)"
          % yaw, abs(yaw + 30.0) < 0.5, json.dumps(result["heading"]))
    check("so it travels along forward (%.4f)" % result["root_motion"]["travel_along_forward"],
          result["root_motion"]["travel_along_forward"] > 0.999)
    worst = direction_errors(bpy.data.actions["mixamo_fk"], 0.0, 30.0, yaw)
    note("worst segment error vs truth (deg): %s" % json.dumps(
        {k: round(v, 3) for k, v in worst.items()}))
    # The truth is continuous and the take is sampled at 30 fps: a slerp between
    # samples dt = 1/30 s apart errs by up to a * dt^2 / 8. motion_stats measures
    # the retargeted walk's leg acceleration at p99 4917 / max 8805 deg/s^2, so
    # 0.68 / 1.22 deg, plus the 0.14 deg the heading estimate reads off a take of
    # 2.47 cycles: 1.5 deg (measured 0.94 at the right thigh). The 120 fps take
    # above holds 0.5.
    check("every DEF limb segment within 1.5 deg through the 30->24 fps slerp",
          max(worst.values()) <= 1.5, json.dumps(worst))
    rest = call("rigforge_retarget", {"target_rig": RIG, "source_path": MIXAMO,
                                      "action_name": "mixamo_rest", "heading": "rest"})
    along = rest["root_motion"]["travel_along_forward"]
    check("heading 'rest' keeps the rest facing, so the take walks 30 deg off forward "
          "(cos = %.4f, want 0.866 +- 0.01)" % along, abs(along - math.cos(math.radians(30)))
          < 0.01, json.dumps(rest["root_motion"]))
    reset_pose()


def test_presets_and_root_modes():
    section("the CMU preset, and the three root-motion modes")
    from forge.tools import rigforge_anim as ra
    preset = call("rigforge_retarget", {"target_rig": RIG, "source_path": CMU,
                                        "action_name": "cmu_preset", "mapping": "cmu"})
    mapped = {m["source"]: m["target"] for m in preset["mapped"]}
    check("the cmu preset maps lowerback onto the spine and thorax onto the chest",
          mapped.get("lowerback") == "spine_fk.001" and mapped.get("thorax") == "chest",
          json.dumps(mapped))
    reasons = {m["source"]: m["reason"] for m in preset["unmapped_detail"]}
    check("and leaves upperback/upperneck/lhipjoint out on purpose, saying so",
          all("on purpose" in reasons.get(n, "") for n in ("upperback", "upperneck",
                                                             "lhipjoint")),
          json.dumps(reasons)[:300])
    keep = preset
    in_place = call("rigforge_retarget", {"target_rig": RIG, "source_path": CMU,
                                          "action_name": "cmu_inplace",
                                          "root_motion": "in_place"})
    root = call("rigforge_retarget", {"target_rig": RIG, "source_path": CMU,
                                      "action_name": "cmu_root", "root_motion": "root"})

    def span(action_name, bone, index=1):
        curve = ra.find_fcurve(bpy.data.actions[action_name],
                               'pose.bones["%s"].location' % bone, index)
        if curve is None:
            return None
        values = [p.co.y for p in curve.keyframe_points]
        return max(values) - min(values)

    keep_span = span("cmu_preset", "torso")
    place_span = span("cmu_inplace", "torso")
    root_span = span("cmu_root", "root")
    torso_root = span("cmu_root", "torso")
    note("torso location Y span: keep %.4f, in_place %.4f; root mode: root %.4f, torso %.4f"
         % (keep_span or 0, place_span or 0, root_span or 0, torso_root or 0))
    check("keep: the torso carries the ~2 m of travel", keep_span is not None
          and keep_span > 1.5, str(keep_span))
    check("in_place: the torso only oscillates (under 5%% of keep's span) and %.4f m was "
          "removed" % in_place["root_motion"]["removed_m"],
          place_span is not None and place_span < 0.05 * keep_span
          and in_place["root_motion"]["removed_m"] > 1.5, str(place_span))
    check("root: the travel moved onto the root control and off the torso",
          root_span is not None and root_span > 1.5 and torso_root < 0.05 * keep_span,
          "root %s torso %s" % (root_span, torso_root))
    check("all three report the same natural speed (%.4f / %.4f / %.4f m/s)"
          % (keep["root_motion"]["speed_mps"], in_place["root_motion"]["speed_mps"],
             root["root_motion"]["speed_mps"]),
          abs(keep["root_motion"]["speed_mps"] - in_place["root_motion"]["speed_mps"]) < 1e-3
          and abs(keep["root_motion"]["speed_mps"] - root["root_motion"]["speed_mps"]) < 1e-3)
    reset_pose()


def test_driver_contract():
    section("rigforge_mocap_clip: CMU take -> IK legs, in place, loop found -> contract")
    from forge.tools import rigforge_anim as ra
    g = generator()
    stretch_before = {s: rig().pose.bones["thigh_parent." + s].get("IK_Stretch")
                      for s in ("L", "R")}
    before = scene_state()
    started = time.monotonic()
    result = call("rigforge_mocap_clip", {"target_rig": RIG, "source_path": CMU,
                                          "clip": "mocap_walk"})
    note("driver took %.1fs" % (time.monotonic() - started))
    contract = result["contract"]
    retarget = result["retarget"]
    note("contract: %s" % json.dumps(contract))
    check("the contract action is 'mocap_walk-loop' and it exists",
          contract["action"] == "mocap_walk-loop" and "mocap_walk-loop" in bpy.data.actions)
    check("the contract clip is ready (slide ok, deformation ok/attention, seam closed)",
          contract["ready"] is True, json.dumps(contract["failed"]))
    check("foot slide under the 5 mm gate on IK legs: worst %.2f mm" % contract["worst_drift_mm"],
          contract["gate"] == "ok" and contract["worst_drift_mm"] < 5.0)
    window = retarget["loop_window"]
    cycle = g.CYCLE_S * 24.0
    k = max(1, round(window["length_frames"] / cycle))
    check("the loop window is a whole number of gait cycles: %d frames vs %.2f x %d"
          % (window["length_frames"], cycle, k),
          abs(window["length_frames"] - k * cycle) <= 1.0, json.dumps(window))
    seam = contract["seam"]
    check("the seam closes on the flesh: %.4f mm (tolerance 1 mm)" % seam["worst_mm"],
          seam["verdict"] == "ok" and seam["worst_mm"] < 1.0)
    residual = retarget["seam_residual"]
    note("seam residual spread: worst %.3f deg (%s), %.2f mm" % (
        residual["worst_deg"], residual["worst_bone"], residual["worst_mm"]))
    check("the residual that was spread is small: under 2 deg per bone",
          residual["worst_deg"] < 2.0, json.dumps(residual["residual_deg"]))
    want = truth_speed(window["window_s"][0], window["window_s"][1],
                       retarget["scale"] / 0.0254)
    got = contract["natural_speed_mps"]
    check("natural speed %.4f m/s is the take's own over the window x scale (%.4f)"
          % (got, want), abs(got - want) / want < 0.01)
    check("the legs were retargeted through IK (foot, pole, toe targets baked)",
          {"foot_ik.L", "foot_ik.R", "thigh_ik_target.L", "toe_ik.R"}
          <= set(retarget["legs"]["ik_controls"]), str(retarget["legs"]))
    action = bpy.data.actions["mocap_walk-loop"]

    def keyed(path):
        curve = ra.find_fcurve(action, path, 0)
        return None if curve is None else sorted({round(p.co.y, 4)
                                                  for p in curve.keyframe_points})

    check("legs keyed IK (IK_FK 0), arms keyed FK (IK_FK 1)",
          keyed('pose.bones["thigh_parent.L"]["IK_FK"]') == [0.0]
          and keyed('pose.bones["upper_arm_parent.R"]["IK_FK"]') == [1.0])
    check("IK_Stretch keyed to 0 in the clip, and the live value put back",
          keyed('pose.bones["thigh_parent.L"]["IK_Stretch"]') == [0.0]
          and {s: rig().pose.bones["thigh_parent." + s].get("IK_Stretch")
               for s in ("L", "R")} == stretch_before, str(stretch_before))
    check("the last frame is the first (loop convention): %d frames, range %s"
          % (contract["frames"], contract["frame_range"]),
          contract["frame_range"] == [1, contract["frames"] + 1])
    gates = result["animation_check"]
    check("gait gates read the retargeted walk as a gait: opposition %s, strike lead %s"
          % ((gates.get("gait_opposition") or {}).get("verdict"),
             (gates.get("strike_lead") or {}).get("verdict")),
          (gates.get("gait_opposition") or {}).get("verdict") == "ok"
          and (gates.get("strike_lead") or {}).get("verdict") == "ok")
    after = scene_state()
    check("the driver left no import, empty or constraint behind",
          after["objects"] == before["objects"] and not after["temp"]
          and not after["constraints"])
    reset_pose()
    return result


def test_motion_stats(driver_result):
    section("motion_stats: the quality-gate scaffold (measured, no thresholds)")
    g = generator()
    stats = driver_result["motion_stats"]
    check("the scaffold says it has no thresholds, and has none",
          stats["thresholds"] is None and "NO pass/fail" in stats["status"])
    joints = stats["joints"]
    frames = stats["frames"][1] - stats["frames"][0]
    check("angular speed pooled over %d DEF joints x %d steps" % (joints, frames),
          stats["angular_speed_deg_s"]["all"]["n"] == joints * frames,
          str(stats["angular_speed_deg_s"]["all"]))
    for block in ("angular_speed_deg_s", "angular_accel_deg_s2"):
        check("%s carries p50/p90/p99/max for all, legs, arms, trunk" % block,
              all(stats[block][group] and {"p50", "p90", "p99", "max"} <= set(stats[block][group])
                  for group in ("all", "legs", "arms", "trunk")))
    foot = stats["footfall"]
    half = g.CYCLE_S / 2.0
    check("footfall: both heels strike once per loop, a step every %.4f s (take: %.4f)"
          % (foot["step_interval_mean_s"] or -1, half),
          len(foot["strike_frames"].get("L", [])) == 1
          and len(foot["strike_frames"].get("R", [])) == 1
          and abs(foot["step_interval_mean_s"] - half) <= 1.0 / 24.0, json.dumps(foot))
    check("and the two feet sit half a cycle apart (phase %.3f)" % foot["left_to_right_phase"],
          abs(foot["left_to_right_phase"] - 0.5) <= 0.05)
    posture = stats["posture"]
    check("posture variance is emitted (trunk, head, hips, per-joint deviation)",
          all(posture.get(k) is not None for k in
              ("trunk_pitch_std_deg", "trunk_roll_std_deg", "head_pitch_std_deg",
               "hip_height_std_mm", "hip_lateral_std_mm", "joint_deviation_deg")))
    walk = call("motion_stats", {"rig": RIG, "action": "walk-loop"})
    note("procedural walk-loop: speed p50 %s deg/s, accel p50 %s deg/s^2, peak/median p90 %s, "
         "trunk speed p50 %s" % (walk["angular_speed_deg_s"]["all"]["p50"],
                                 walk["angular_accel_deg_s2"]["all"]["p50"],
                                 walk["accel_peak_to_median"]["p90"],
                                 walk["angular_speed_deg_s"]["trunk"]["p50"]))
    check("the same command reads the procedural walk-loop (the comparison baseline)",
          walk["action"] == "walk-loop" and walk["footfall"]["strike_frames"].get("L"))
    reply = call("motion_stats", {"rig": RIG, "action": "nope"}, expect_error=True)
    check("an unknown action is refused by name", reply.get("status") == "error"
          and "nope" in (reply.get("message") or ""))


def test_determinism():
    section("the bake is deterministic and blind to the live pose")
    digests = []
    for index in range(2):
        if index == 0:
            # a live pose the clip must not inherit: the root displaced the way
            # evaluating walk-loop leaves it (0.5889 m), the torso turned
            rig().pose.bones["root"].location = (0.0, 0.5889, 0.0)
            rig().pose.bones["torso"].rotation_quaternion = Quaternion((0, 0, 1), 0.3)
            bpy.context.view_layer.update()
        result = call("rigforge_retarget", {"target_rig": RIG, "source_path": MIXAMO,
                                            "action_name": "det", "legs": "ik",
                                            "root_motion": "in_place", "find_loop": True,
                                            "loop": True})
        digests.append(action_digest(bpy.data.actions[result["action"]]))
        if index == 0:
            check("the live pose was put back after the bake",
                  abs(rig().pose.bones["root"].location.y - 0.5889) < 1e-6)
        reset_pose()
    check("a retarget over a displaced live pose and one over rest bake identical curves "
          "(%s)" % digests[0][:12], digests[0] == digests[1], str(digests))


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


def main():
    print("Forge mocap production path headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))
    started = time.monotonic()
    workspace = tempfile.mkdtemp(prefix="forge_mocap_test_")
    try:
        if not os.path.isfile(WIP):
            raise AssertionError("the game character is missing: %s" % WIP)
        copy = os.path.join(workspace, "werewolf-copy.blend")
        shutil.copyfile(WIP, copy)
        bpy.ops.wm.open_mainfile(filepath=copy, load_ui=False)
        if ADDON_DIR not in sys.path:
            sys.path.insert(0, ADDON_DIR)
        import addon_utils
        addon_utils.enable("forge", default_set=True, persistent=False)
        from forge import server as forge_server
        forge_server.start_server(host="127.0.0.1", port=PORT)
        check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())
        check("the game character's rig is loaded (%d DEF bones)"
              % len([b for b in rig().data.bones if b.name.startswith("DEF-")]),
              len([b for b in rig().data.bones if b.name.startswith("DEF-")]) == 33)
        reset_pose()
        test_fixtures_frozen()
        test_bvh_validation()
        test_refusals()
        test_cmu_fk()
        test_mixamo_heading_fps()
        test_presets_and_root_modes()
        driver = test_driver_contract()
        test_motion_stats(driver)
        test_determinism()
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
    print("\n%d checks, %d failed (%.1fs)" % (len(_RESULTS), len(failed),
                                              time.monotonic() - started))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
