"""protagonist_human locomotion clip wave: run-loop, sprint-loop, fall, land.

Called by build_protagonist_human.py (after idle-loop, before the gates) on the COPY
of the wip blend. Every clip is in place (the game's controller moves the body).

GAITS (run-loop, sprint-loop)
  The feet, the IK/FK convention, the planted-stretch keys, the heel roll, the
  reach clamp and the contralateral arm swing are rigforge_walk's, called with
  travel=false and a stance fraction under 0.5 - so both feet are off the ground
  between the stance phases (the airborne phase). Three things a walk does not do
  are layered on top, keyed on the same frames, all periodic in the cycle so the
  seam stays closed:
    * the bounce: rigforge_walk is called with hip_drop=0 and the hips are then
      lowered by BOB x (1 + cos 4pi(t - stance/2)) / 2 - lowest at each foot's
      mid-stance, back up to the level the reach clamp solved at mid-flight. The
      walk's own bob bottoms out at contact, which is a walk's vault, not a run.
      The dip only ever lowers the hips below the solved level, so no planted
      frame asks the leg for more reach than the clamp cleared.
    * the lean: the torso is turned forward about the hip JOINT (the DEF-thigh
      heads, lane conventions), so the sockets stay where the reach was solved,
      and the chest adds its own share on top.
    * the running arm: a constant extra elbow flexion on top of the walk's swing.
    * the heel kick: extra early-swing foot lift, capped per frame by knee flexion.
  The head is then turned back so it pitches by HEAD_KEEP of the trunk's lean,
  measured on the posed head, not assumed from Rigify's follow settings.
  The natural stride speed is the authored stride over the cycle time, and
  animation_check's in-place treadmill speed measures it back.

FALL (non-loop name, periodic content)
  A held airborne pose: legs IK with the feet drawn up under the hips (knees bent,
  toes pointed) and held still against the body, arms raised out to the sides with a
  counter-phased flail, the chest rolling with them. The game's loader forces LOOP_LINEAR
  on "fall" whatever the glTF name says (werewolf characters/character_kinds.gd
  LOOPING), so its last frame repeats its first and the seam is gated like a loop.

LAND (non-loop)
  Contact to absorb to idle: feet planted at rest on every frame, the hips drop and
  set back into the absorb then rise to exactly idle-loop's frame-1 torso and chest
  keys, arms come down from the fall's raised pose to rest. The pop into idle is
  measured on the evaluated mesh (land's last frame against idle's first).
"""
import math
import bpy
from mathutils import Matrix, Vector

from forge.tools import rigforge_rig as rr, rigforge_anim as ra

PLANTED = ["root", "foot_ik.L", "foot_ik.R", "foot_heel_ik.L", "foot_heel_ik.R",
           "toe_ik.L", "toe_ik.R"]

# --- gait parameters. Lengths are fractions of the leg (hip to ankle, 0.7362 m on
# this rig) except "stride" (metres, raw); speeds are raw (Blender) m/s, and the game
# sees them x SCALE (0.9444). What pins them (measured 2026-09-24):
#  * speed = stride / (cycle / 24 fps). run 2.10 m / 12 f = 4.200 m/s raw = 3.967 in
#    game (band 3.5-5.5); sprint 3.40 m / 10 f = 8.160 raw = 7.706 in game (7-9).
#  * the planted foot has to reach lead x stride ahead of the hip joint and
#    (stance - lead) x stride behind it; rigforge_walk's reach clamp lowers the hips
#    to buy that (run 95 mm, sprint 103 mm) under the 0.20 x leg ceiling, stride kept.
#    The run's cadence is what sets that crouch: the same 4.2 m/s as 2.45 m / 14 f
#    needed 126 mm and rendered as a crouched speed-walk.
#  * stance x cycle is what animation_check gets to see planted: 0.30 x 12 and
#    0.20 x 10 give each ball >= 3 samples down (MIN_STANCE_FRAMES) and each heel a
#    3-sample strike run, so gait_opposition and strike_lead are measured, not skipped.
#    That is also why sprint's heel roll is 10 deg: at 25 deg no ball holds 3 samples
#    in the contact band, so the slide reads 'unmeasured' (0 steps) and the treadmill
#    speed 0 - the gate would have nothing to judge.
#  * lead: run 0.155 clears the strike_lead gate's 0.15; sprint 0.10 (see the build's
#    GAIT_DEFORMATION_OK for why a sprint does not take the walking band).
#  * odd cycles sample the two feet on different sub-frame phases: a 13-frame run
#    measured arm.R vs leg.R at 166 deg and a 3.71 mm drift against 180 / 1.78 at 14.
#  * heel_kick is the extra early-swing lift (see author_gait), bisected down to
#    KNEE_FLEX_CAP_DEG; step_height is the walk's own swing lift, and sprint's is 0.25
#    because at 0.28 the walk's lift alone folded the knee to 117.4 deg, over the cap.
RUN = {
    "action": "run", "cycle": 12, "stride": 2.10, "stance": 0.30, "lead": 0.155,
    "step_height": 0.26, "foot_roll_deg": 18.0, "arm_swing_deg": 34.0,
    "elbow_bend_deg": 20.0, "elbow_extra_deg": 62.0, "hip_twist_deg": 7.0,
    "hip_sway": 0.012, "bob": 0.055, "lean_torso_deg": 9.0, "lean_chest_deg": 6.0,
    "head_keep": 0.35, "max_hip_lower": 0.20, "heel_kick": 0.30,
}
SPRINT = {
    "action": "sprint", "cycle": 10, "stride": 3.40, "stance": 0.20, "lead": 0.10,
    "step_height": 0.25, "foot_roll_deg": 10.0, "arm_swing_deg": 46.0,
    "elbow_bend_deg": 20.0, "elbow_extra_deg": 70.0, "hip_twist_deg": 8.0,
    "hip_sway": 0.008, "bob": 0.05, "lean_torso_deg": 14.0, "lean_chest_deg": 8.0,
    "head_keep": 0.3, "max_hip_lower": 0.20, "heel_kick": 0.40,
}

# The heel kick's lift is bisected per swing frame so the knee (DEF thigh vs shin)
# never folds past this. Measured 2026-09-24: an uncapped 0.30 x leg kick folded the
# run's knee to 153.8 deg and the sprint's to 166.0 deg, and the jeans/boot mesh
# crumpled into a wad behind the knee on the render; the shipped jump's deepest fold
# is 99.2 deg (walk 64.0). At a 125 deg cap the side renders still showed the calf
# pressed into the thigh and the jeans bunched behind the knee on the kicking leg;
# 115 is the value the delivered previews were reviewed at. The build fails if a gait
# folds past it.
KNEE_FLEX_CAP_DEG = 115.0

FALL_FRAMES = 18     # 0.75 s at 24 fps; frame 19 repeats frame 1
LAND_FRAMES = 12      # 0.5 s at 24 fps (frames 1..13)


def _upd():
    bpy.context.view_layer.update()


def _hip_joint(rig):
    pts = [rig.matrix_world @ rig.pose.bones["DEF-thigh.%s" % s].head for s in ("L", "R")]
    return (pts[0] + pts[1]) / 2.0


def _pitch(rig, pb, fwd, up):
    """Forward pitch of a bone's own axis in the forward/up plane, radians (+ = forward)."""
    axis = (rig.matrix_world.to_3x3() @ pb.matrix.to_3x3()) @ Vector((0.0, 1.0, 0.0))
    return math.atan2(axis.dot(fwd), axis.dot(up))


def _turn(rig, pb, axis, angle, pivot=None):
    """Turn a pose bone in world space about ``axis`` through ``pivot`` (default: its head)."""
    m = ra._world_matrix(rig, pb)
    ra._set_world(rig, pb, ra._rotate_about(m, axis, angle,
                                            m.translation.copy() if pivot is None else pivot))
    _upd()


def _rest_keys(rig, frames, names):
    pb = rig.pose.bones
    for f in frames:
        for name in names:
            pb[name].location = (0, 0, 0)
            pb[name].rotation_quaternion = (1, 0, 0, 0)
            pb[name].rotation_euler = (0, 0, 0)
            ra._key_transform(pb[name], f)


def _ik_convention(rig, frames):
    """Legs IK without stretch, arms FK - keyed at both ends, like idle-loop."""
    pb = rig.pose.bones
    for f in frames:
        for side in ("L", "R"):
            tp = pb["thigh_parent." + side]; ap = pb["upper_arm_parent." + side]
            tp["IK_FK"] = 0.0; tp["IK_Stretch"] = 0.0; ap["IK_FK"] = 1.0
            tp.keyframe_insert('["IK_FK"]', frame=f)
            tp.keyframe_insert('["IK_Stretch"]', frame=f)
            ap.keyframe_insert('["IK_FK"]', frame=f)


def _linear(action):
    for c in rr.action_fcurves(action):
        for p in c.keyframe_points:
            p.interpolation = "LINEAR"
        c.update()


def _abduct_signs(rig, fwd):
    """Which sign of a turn about ``forward`` raises each hand, asked of the rig."""
    pb = rig.pose.bones
    signs = {}
    for side in ("L", "R"):
        up_arm = pb["upper_arm_fk." + side]
        hand = pb["hand_fk." + side]
        up_arm.matrix_basis.identity(); _upd()
        before = (rig.matrix_world @ hand.head).z
        _turn(rig, up_arm, fwd, math.radians(10.0))
        up_arm.location = (0, 0, 0); _upd()
        after = (rig.matrix_world @ hand.head).z
        signs[side] = 1.0 if after > before else -1.0
        up_arm.matrix_basis.identity(); _upd()
    return signs


def author_gait(rig, scene, run, spec, frame_info):
    """rigforge_walk feet + run bounce, lean, running arms. Returns a report dict."""
    rr.assign_action(rig, None)
    _reset(rig)
    leg = frame_info["leg_length"]
    fwd, up, right = frame_info["forward"], frame_info["up"], frame_info["right"]
    fps = scene.render.fps
    res = run("rigforge_walk", {
        "rig": rig.name, "action": spec["action"], "loop": True, "travel": False,
        "cycle_frames": spec["cycle"], "step_length": spec["stride"] / 2.0,
        "stance_fraction": spec["stance"], "strike_lead": spec["lead"],
        "step_height": spec["step_height"] * leg, "hip_drop": 0.0,
        "hip_sway": spec["hip_sway"] * leg, "hip_twist_deg": spec["hip_twist_deg"],
        "arm_swing_deg": spec["arm_swing_deg"], "arm_phase_deg": 180.0,
        "elbow_bend_deg": spec["elbow_bend_deg"], "foot_roll_deg": spec["foot_roll_deg"],
        "max_hip_lower": spec["max_hip_lower"] * leg,
    })
    action = bpy.data.actions[res["action"]]
    rr.assign_action(rig, action)
    pb = rig.pose.bones
    torso, chest, head = pb["torso"], pb["chest"], pb["head"]
    signs = res["arm_forward_sign"]
    cycle, stance = spec["cycle"], spec["stance"]
    bob = spec["bob"] * leg
    lean_t = math.radians(spec["lean_torso_deg"])
    lean_c = math.radians(spec["lean_chest_deg"])
    extra = math.radians(spec["elbow_extra_deg"])
    head_pitch = []
    trunk = []
    rest_p = _pitch_rest(rig, head, fwd, up)
    rest_trunk = _trunk_pitch(rig, fwd, up, rest=True)
    kick = spec["heel_kick"] * leg
    feet = frame_info["feet"]
    base_flex, kick_flex, capped = [], [], []
    for f in range(1, cycle + 2):
        t = (f - 1) / cycle
        scene.frame_set(f)
        _upd()
        # bounce: lowest at each foot's mid-stance, solved level at mid-flight
        dip = bob * 0.5 * (1.0 + math.cos(4.0 * math.pi * (t - stance / 2.0)))
        m = ra._world_matrix(rig, torso)
        ra._set_world(rig, torso, Matrix.Translation(-up * dip) @ m)
        _upd()
        # lean about the hip joint (a positive turn about `right` tips forward-pointing
        # axes UP, i.e. the trunk BACK - so forward is the negative angle)
        _turn(rig, torso, right, -lean_t, _hip_joint(rig))
        ra._key_transform(torso, f)
        # heel kick (after the hips are placed, so the knee cap is solved on the final
        # hip): the walk's swing lift is a symmetric sine; a running foot rises early,
        # behind the body, and comes through high. Added on the swing frames only (same
        # phase rule as rigforge_walk: L strikes at t=0, R at t=0.5), so every stance
        # key is the walk's own.
        for foot in feet.values():
            u = (t - (0.0 if foot["side"] == "L" else 0.5)) % 1.0
            if u <= stance:
                continue
            p = (u - stance) / (1.0 - stance)
            target = pb[foot["target"]]
            m = ra._world_matrix(rig, target).copy()
            lift = kick * math.sin(math.pi * p ** 0.7)

            def flex_at(scale):
                ra._set_world(rig, target, Matrix.Translation(up * (lift * scale)) @ m)
                _upd()
                return _knee_flex(rig, foot["side"])
            base_flex.append(flex_at(0.0))
            scale = 1.0
            if flex_at(1.0) > KNEE_FLEX_CAP_DEG:
                lo, hi = 0.0, 1.0      # bisect the lift to the cap, on the posed rig
                for _ in range(12):
                    mid = 0.5 * (lo + hi)
                    lo, hi = (mid, hi) if flex_at(mid) <= KNEE_FLEX_CAP_DEG else (lo, mid)
                scale = lo
                capped.append(f)
            kick_flex.append(flex_at(scale))
            ra._key_transform(target, f)
        # the walk leaves the chest unkeyed, so once this loop has keyed it the next
        # frame_set hands back THIS frame's lean - start every frame from identity
        chest.matrix_basis.identity(); _upd()
        _turn(rig, chest, right, -lean_c)
        ra._key_transform(chest, f)
        # head: pitch forward by only HEAD_KEEP of the trunk lean, measured on the head
        head.matrix_basis.identity(); _upd()
        want = (lean_t + lean_c) * spec["head_keep"]
        now = _pitch(rig, head, fwd, up)
        _turn(rig, head, right, (now - rest_p) - want)
        ra._key_transform(head, f)
        head_pitch.append(math.degrees(_pitch(rig, head, fwd, up) - rest_p))
        trunk.append(math.degrees(_trunk_pitch(rig, fwd, up) - rest_trunk))
        # running arm: constant extra elbow flexion, the flexion sign the walk probed
        for side in ("L", "R"):
            fore = pb["forearm_fk." + side]
            _turn(rig, fore, right, extra * signs.get(side, 1.0))
            fore.location = (0, 0, 0)
            _upd()
            ra._key_transform(fore, f)
    _linear(action)
    stride = res["stride_m"]
    seconds = cycle / float(fps)
    return {
        "action": action.name, "cycle_frames": cycle, "stride_m": stride,
        "stride_requested_m": spec["stride"],
        "natural_speed_raw_mps": stride / seconds,
        "hip_lower_mm": res["hip_lower_m"] * 1000.0, "step_clamped": res["step_length_reach_clamped"],
        "leg_reach_ratio": res["leg_reach_ratio"], "swing_reach_ratio": res["swing_reach_ratio"],
        "strike_lead_mm_authored": res["strike_lead_mm"], "arm_forward_sign": signs,
        "head_pitch_deg": [round(min(head_pitch), 2), round(max(head_pitch), 2)],
        "trunk_lean_deg": [round(min(trunk), 2), round(max(trunk), 2)],
        "trunk_rest_deg": round(math.degrees(rest_trunk), 2),
        "swing_knee_flex_deg_walk_lift": round(max(base_flex), 2),
        "swing_knee_flex_deg_with_kick": round(max(kick_flex), 2),
        "kick_capped_frames": capped,
        "walk_warnings": res["warnings"],
    }


def _knee_flex(rig, side):
    mw = rig.matrix_world
    hip = mw @ rig.pose.bones["DEF-thigh.%s" % side].head
    knee = mw @ rig.pose.bones["DEF-shin.%s" % side].head
    ankle = mw @ rig.pose.bones["DEF-foot.%s" % side].head
    return math.degrees((knee - hip).angle(ankle - knee))


def knee_flex_max(rig, scene, action):
    """Deepest knee flexion over a clip, degrees (thigh vs shin, DEF chain), and its frame."""
    rr.assign_action(rig, action)
    worst = (0.0, None, None)
    f0, f1 = int(action.frame_range[0]), int(action.frame_range[1])
    mw = rig.matrix_world
    for f in range(f0, f1 + 1):
        scene.frame_set(f)
        _upd()
        for s in ("L", "R"):
            hip = mw @ rig.pose.bones["DEF-thigh.%s" % s].head
            knee = mw @ rig.pose.bones["DEF-shin.%s" % s].head
            ankle = mw @ rig.pose.bones["DEF-foot.%s" % s].head
            ang = math.degrees((knee - hip).angle(ankle - knee))
            if ang > worst[0]:
                worst = (ang, f, s)
    return {"deg": round(worst[0], 2), "frame": worst[1], "side": worst[2]}


def _trunk_pitch(rig, fwd, up, rest=False):
    """Forward pitch of hip joint -> neck base (DEF-spine.003 tail), radians."""
    if rest:
        mw = rig.matrix_world
        bones = rig.data.bones
        hip = sum((mw @ bones["DEF-thigh.%s" % s].head_local for s in ("L", "R")),
                  Vector()) / 2.0
        neck = mw @ bones["DEF-spine.003"].tail_local
    else:
        hip = _hip_joint(rig)
        neck = rig.matrix_world @ rig.pose.bones["DEF-spine.003"].tail
    v = neck - hip
    return math.atan2(v.dot(fwd), v.dot(up))


def _pitch_rest(rig, pb, fwd, up):
    axis = (rig.matrix_world.to_3x3() @ pb.bone.matrix_local.to_3x3()) @ Vector((0.0, 1.0, 0.0))
    return math.atan2(axis.dot(fwd), axis.dot(up))


def _reset(rig):
    for p in rig.pose.bones:
        p.location = (0, 0, 0)
        p.rotation_quaternion = (1, 0, 0, 0)
        p.rotation_euler = (0, 0, 0)
        p.scale = (1, 1, 1)
    _upd()


def _new_action(rig, run, name, loop=False):
    rr.assign_action(rig, None)
    _reset(rig)
    res = run("rigforge_action", {"action": "new", "name": name, "loop": loop, "rig": rig.name})
    return bpy.data.actions[res["name"]]


# --- fall / land shape (fractions of the leg; degrees)
# The feet hold still against the body: a foot that bobs "leaves the ground and comes
# back" as far as animation_check can tell, which makes an airborne hold read as a
# gait (measured: gait_opposition and strike_lead both fired on the first attempt).
FALL_FOOT = {"L": (0.15, 0.06, -22.0), "R": (0.18, -0.05, -28.0)}  # up, forward, toe pitch
FALL_ARM_UP = 58.0             # arm abduction, degrees
FALL_ARM_FLAIL = 9.0           # +- degrees, 2 per clip, L/R counter-phased
FALL_CHEST_ROLL = 2.5          # +- degrees about forward, riding the arms
FALL_ARM_FWD = 12.0            # arms carried a little forward
FALL_ELBOW = 32.0
FALL_ELBOW_FLAIL = 8.0
FALL_TORSO_BACK = 4.0          # trunk tipped back about the hip joint
FALL_HEAD_DOWN = 8.0           # looking for the ground

LAND_DEPTH = 0.135             # absorb depth, fraction of leg (99 mm on this rig)
LAND_SETBACK = 0.035           # hips back over the heels at the bottom
LAND_FOLD = 9.0                # trunk fold at the bottom, degrees (about the hip joint)
LAND_CHEST = 11.0              # chest extra at the bottom, on top of idle's 5
LAND_BOTTOM = 4                # frame of the absorb's bottom (0.125 s after contact)
LAND_HEAD_NOD = 7.0


def _ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1.0 - (1.0 - x) ** 2


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _arm_pose(rig, frame_info, abduct_sign, forward_sign, side, abduct, flex, elbow):
    """Key-ready FK arm pose: abduction about forward, flexion about right, elbow bend."""
    pb = rig.pose.bones
    fwd, right = frame_info["forward"], frame_info["right"]
    up_arm = pb["upper_arm_fk." + side]; fore = pb["forearm_fk." + side]
    up_arm.matrix_basis.identity(); fore.matrix_basis.identity(); _upd()
    shoulder = ra._world_matrix(rig, up_arm).translation.copy()
    _turn(rig, up_arm, fwd, math.radians(abduct) * abduct_sign[side], shoulder)
    _turn(rig, up_arm, right, math.radians(flex) * forward_sign, shoulder)
    up_arm.location = (0, 0, 0); _upd()
    _turn(rig, fore, right, math.radians(elbow) * forward_sign)
    fore.location = (0, 0, 0); _upd()


def _arm_forward_sign(rig, frame_info):
    """+1 when a positive turn about `right` carries the hand forward (the walk's probe)."""
    pb = rig.pose.bones
    fwd, right = frame_info["forward"], frame_info["right"]
    up_arm = pb["upper_arm_fk.L"]; hand = pb["hand_fk.L"]
    up_arm.matrix_basis.identity(); _upd()
    before = (rig.matrix_world @ hand.head).dot(fwd)
    _turn(rig, up_arm, right, math.radians(10.0))
    up_arm.location = (0, 0, 0); _upd()
    after = (rig.matrix_world @ hand.head).dot(fwd)
    up_arm.matrix_basis.identity(); _upd()
    return 1.0 if after > before else -1.0


def author_fall(rig, scene, run, frame_info):
    action = _new_action(rig, run, "fall", loop=False)
    leg = frame_info["leg_length"]
    fwd, up, right = frame_info["forward"], frame_info["up"], frame_info["right"]
    pb = rig.pose.bones
    abd = _abduct_signs(rig, fwd)
    fsign = _arm_forward_sign(rig, frame_info)
    first, last = 1, 1 + FALL_FRAMES
    _ik_convention(rig, (first, last))
    rest_torso = ra._rest_world(rig, pb["torso"])
    feet = frame_info["feet"]
    for f in range(first, last + 1):
        t = (f - first) / FALL_FRAMES
        scene.frame_set(f)
        _reset(rig)
        _rest_keys(rig, [f], ["root", "foot_heel_ik.L", "foot_heel_ik.R", "toe_ik.L", "toe_ik.R"])
        ra._set_world(rig, pb["torso"], rest_torso); _upd()
        _turn(rig, pb["torso"], right, math.radians(FALL_TORSO_BACK), _hip_joint(rig))
        ra._key_transform(pb["torso"], f)
        _turn(rig, pb["chest"], fwd, math.radians(FALL_CHEST_ROLL) * math.sin(4.0 * math.pi * t))
        ra._key_transform(pb["chest"], f)
        for name, foot in feet.items():
            upv, fwdv, toe = FALL_FOOT[foot["side"]]
            m = foot["rest"].copy()
            m.translation = foot["rest"].translation + up * (upv * leg) + fwd * (fwdv * leg)
            m = ra._rotate_about(m, right, math.radians(toe), m.translation.copy())
            target = pb[foot["target"]]
            ra._set_world(rig, target, m); _upd()
            ra._key_transform(target, f)
        for side in ("L", "R"):
            phase = 0.0 if side == "L" else math.pi
            wave = math.sin(4.0 * math.pi * t + phase)
            _arm_pose(rig, frame_info, abd, fsign, side, FALL_ARM_UP + FALL_ARM_FLAIL * wave,
                      FALL_ARM_FWD, FALL_ELBOW + FALL_ELBOW_FLAIL * wave)
            ra._key_transform(pb["upper_arm_fk." + side], f)
            ra._key_transform(pb["forearm_fk." + side], f)
        _turn(rig, pb["head"], right, -math.radians(FALL_HEAD_DOWN))
        ra._key_transform(pb["head"], f)
    _linear(action)
    return action, {"abduct_sign": abd, "arm_forward_sign": fsign}


def author_land(rig, scene, run, frame_info, idle_name):
    """Contact -> absorb -> idle-loop frame 1, exactly (torso/chest/arms/legs)."""
    idle = bpy.data.actions[idle_name]
    # idle-loop's frame-1 torso and chest local keys: the pose this clip must end on
    end_keys = {}
    for c in rr.action_fcurves(idle):
        for bone in ("torso", "chest"):
            if c.data_path.startswith('pose.bones["%s"].' % bone):
                end_keys[(c.data_path, c.array_index)] = c.evaluate(1.0)
    action = _new_action(rig, run, "land", loop=False)
    leg = frame_info["leg_length"]
    fwd, up, right = frame_info["forward"], frame_info["up"], frame_info["right"]
    pb = rig.pose.bones
    abd = _abduct_signs(rig, fwd)
    fsign = _arm_forward_sign(rig, frame_info)
    first, last = 1, 1 + LAND_FRAMES
    bottom = first + LAND_BOTTOM - 1
    _ik_convention(rig, (first, last))
    rest_torso = ra._rest_world(rig, pb["torso"])
    # idle's constant lowering, read back off its frame-1 torso in world space
    rr.assign_action(rig, idle); scene.frame_set(1); _upd()
    idle_torso_world = ra._world_matrix(rig, pb["torso"]).copy()
    rr.assign_action(rig, action)
    lower_idle = (rest_torso.translation - idle_torso_world.translation).dot(up)
    track = []
    for f in range(first, last + 1):
        scene.frame_set(f)
        _reset(rig)
        _rest_keys(rig, [f], PLANTED)
        if f <= bottom:
            u = _ease_out((f - first) / float(bottom - first))
            depth = LAND_DEPTH * leg * u
            setback = LAND_SETBACK * leg * u
            fold = LAND_FOLD * u
            chest_extra = LAND_CHEST * u
            nod = LAND_HEAD_NOD * u
            arm_w = 1.0 - 0.6 * u
        else:
            u = _smooth((f - bottom) / float(last - bottom))
            depth = LAND_DEPTH * leg + (lower_idle - LAND_DEPTH * leg) * u
            setback = LAND_SETBACK * leg * (1.0 - u)
            fold = LAND_FOLD * (1.0 - u)
            chest_extra = LAND_CHEST * (1.0 - u)
            nod = LAND_HEAD_NOD * (1.0 - u)
            arm_w = 0.4 * (1.0 - u)
        # chest: idle's 5 deg phased in across the clip, the absorb's fold on top
        idle_share = (f - first) / float(last - first)
        track.append((f, round(depth * 1000.0, 2), round(setback * 1000.0, 2)))
        if f == last:
            for (path, index), value in end_keys.items():
                bone = path.split('"')[1]
                prop = path.rsplit(".", 1)[1]
                getattr(pb[bone], prop)[index] = value
            _upd()
            ra._key_transform(pb["torso"], f)
            ra._key_transform(pb["chest"], f)
        else:
            m = rest_torso.copy()
            m.translation = rest_torso.translation - up * depth - fwd * setback
            ra._set_world(rig, pb["torso"], m); _upd()
            _turn(rig, pb["torso"], right, -math.radians(fold), _hip_joint(rig))
            ra._key_transform(pb["torso"], f)
            _turn(rig, pb["chest"], right, -math.radians(5.0 * idle_share + chest_extra))
            ra._key_transform(pb["chest"], f)
        for side in ("L", "R"):
            _arm_pose(rig, frame_info, abd, fsign, side, FALL_ARM_UP * arm_w,
                      FALL_ARM_FWD * arm_w, FALL_ELBOW * arm_w)
            ra._key_transform(pb["upper_arm_fk." + side], f)
            ra._key_transform(pb["forearm_fk." + side], f)
        if f < last:
            _turn(rig, pb["head"], right, -math.radians(nod))
        ra._key_transform(pb["head"], f)
    _linear(action)
    return action, {"idle_lower_mm": round(lower_idle * 1000.0, 2), "depth_track_mm": track}
