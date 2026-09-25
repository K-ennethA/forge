"""protagonist_human game drop: forge werewolf Form A (the human form) -> git/werewolf model-swap contract.

Run headless on a COPY of projects/werewolf/models/werewolf-wip-17.blend (the
script never saves, and refuses to run on the source file itself):

    blender --background --factory-startup <copy.blend> --python build_protagonist_human.py -- <out.glb>

What it does, in order (numbers measured 2026-09-24):
  0. the look (look_protagonist_human.py): custom normals dropped, 31 n-gons
     triangulated so tangents can be built (2 folded duplicate pairs removed), winding recomputed outward, fully
     smooth shading (no sharps), and region colour from the tags + the palette
     sample_palette.py derives from refs/form-a-front.png (palette.json), shipped as
     COLOR_0 through one Principled material. Replaces the generator material whose
     atlas no longer matched this retopo's UVs (the patchy "choppy" look).
  1. LOCOMOTION IS REAL MOCAP (2026-09-24; the procedural run was judged "clearly
     broken": 4.0 steps/s, a motionless trunk). The frozen selection in mocap/
     (PROVENANCE.json: dataset, zip, member, rows, sha256, licence, credit line;
     freeze_mocap.py re-derives it) is sha256-checked, then each take goes
     through rigforge_mocap_clip (IK legs, in place, flat-stance foot
     calibration, ball-anchored plants locked to the floor, hips lowered where a
     leg runs out of reach, loop window found and its seam closed):
       walk-loop        CMU 08_01  (replaces the wip blend's procedural walk)
       run-loop         CMU 16_46
       crouch_walk-loop 100STYLE Crouched_FW
       crouch_idle-loop 100STYLE Crouched_ID
     No sprint clip ships: no available take reaches the game's sprint band
     honestly (the fastest clean run is 3.54 m/s in game against sprint >= 6.5),
     so the game's own fallback plays run-loop for sprint (character_model.gd
     LOCO_FALLBACK sprint -> run, time-scaled by its speed_scale).
  2. idle-loop is authored (48 frames = 2.0 s, last frame == first) through
     rigforge_action + the walk machinery (_set_world / _key_transform): soft
     knees, two breaths and one weight shift per cycle, chest tipped 5 deg
     forward. Legs stay IK on rest-keyed targets. Gated by animation_check
     (loop_seam_closure + planted-foot slide); the build stops if either fails.
  2b. fall (18 f, airborne hold, arm flail, periodic because the game loops it) and
     land (12 f, absorb into idle-loop's frame 1) from clips_protagonist_human.py,
     unchanged. Gated per clip (slide, stretch/reach, seam, the gaits' motion-
     quality tier, speed inside the game's band, land->idle pop on the mesh);
     every gate prints before a failure stops the build. The shipped jump's
     readings are printed alongside as the reference deformation level.
  3. The rig object is yawed 180 deg. The rig faces Blender -Y (its toes), the
     glTF exporter maps Blender -Y to glTF +Z, and Godot forward is -Z. Measured,
     not assumed: the shipped werewolf-form-a.glb has its toes at +Z.
  4. Export through rigforge_export_godot (deform-only rig, B-Bones flattened,
     IK baked visually, driven correctives sampled per clip) with
     export_anim_slide_to_zero, so every clip starts at t=0 and a loop's wrap
     carries no one-frame hold.

  6. Image inventory is pinned: the glb's glTF images[] (index order) must equal
     protagonist_human.images.json or the build fails - Godot extracts embedded images
     as <kind>_Image_N.png and the game commits them, so count/order/names must not
     churn. The colour ships as COLOR_0, so the pinned inventory is empty ([]); the
     generator's three 2048 px maps (Image_2, Image_1, Image_0) are gone.
  7. manifest.json is written beside <out.glb>: scale, clip_map, walk_speed_mps,
     run_speed_mps and crouch_walk_speed_mps (each in-place gait's natural speed
     at game scale = the take's hip-joint speed at rig scale x scale). No
     sprint_speed_mps: there is no sprint clip for it to describe.

Verify the result with verify_game_glb.py next to this file.
"""
import bpy, sys, os, math, json
from mathutils import Matrix

ADDON = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      os.pardir, os.pardir, os.pardir, os.pardir, "addon"))
_here = os.path.normpath(bpy.data.filepath)
if os.path.basename(_here).startswith("werewolf-wip-") and \
        os.path.dirname(_here).endswith(os.path.join("werewolf", "models")):
    raise SystemExit("Run this on a COPY of the wip blend, not the source.")
sys.path.insert(0, ADDON)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import addon_utils
addon_utils.enable("forge", default_set=True, persistent=False)
from forge.tools import rigforge_rig as rr, rigforge_anim as ra
from forge.tools.registry import dispatch

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not argv:
    raise SystemExit("usage: ... --python build_protagonist_human.py -- <out.glb>")
OUT = argv[0]
YAW = 180.0
# Game-side scale of this kind, set by the game session at the recast
# (werewolf d1905e5): 0.9444 x the 1.853 m idle-pose height = 1.75 m, the
# contract height for a humanoid.
SCALE = 0.9444
# No "sprint": the game steps sprint down to run (LOCO_FALLBACK) - see note 1.
CLIP_MAP = {"idle": "idle", "walk": "walk", "run": "run",
            "crouch_idle": "crouch_idle", "crouch_walk": "crouch_walk",
            "jump": "jump", "fall": "fall", "land": "land", "attack_1": "punch_R",
            "attack_2": "punch_L"}
# The game's locomotion bands (in-game m/s, after SCALE), werewolf
# characters/character_model.gd: walk [WALK_MIN 0.25, RUN_MIN 3.0), run
# [3.0, SPRINT_MIN 6.5); crouch_walk plays at any speed >= WALK_MIN. The lane's
# target band for run is 3.5-5.5 (printed, not gated: the game's band is the contract).
SPEED_BANDS = {"walk-loop": (0.25, 3.0), "run-loop": (3.0, 6.5),
               "crouch_walk-loop": (0.25, 3.0)}
SPEED_TARGETS = {"run-loop": (3.5, 5.5)}
# The take's hip-joint speed (the retarget's natural_speed) and animation_check's
# pooled in-place treadmill (the planted balls' belt) must agree to within this
# fraction: the controller moves the body at the first, the feet show the second.
SPEED_AGREEMENT = 0.03
MOCAP_DIR = os.path.join(HERE, "mocap")
# (clip, frozen file, rigforge_mocap_clip params beyond target_rig/source_path).
# Gaits run the motion-quality tier; the crouch idle is not a gait - its heels
# shift, so gait_opposition reads a phase it has no business reading - and is
# gated on slide, stretch, reach and seam one by one instead of the rollup.
MOCAP = [
    ("walk", "cmu_08_01.bvh", {"mapping": "cmu", "motion_quality": True}),
    ("run", "cmu_16_46.bvh", {"mapping": "cmu", "motion_quality": True}),
    ("crouch_walk", "s100_Crouched_FW.bvh", {"mapping": "100style", "motion_quality": True}),
    ("crouch_idle", "s100_Crouched_ID.bvh", {"mapping": "100style", "loop_min_s": 2.0,
                                             "loop_max_s": 5.0, "check_mode": "planted"}),
]
GAITS = ("walk-loop", "run-loop", "crouch_walk-loop")
# deformation_gate levels allowed on the mocap gaits: 'attention' is what every real
# take reads on this rig (measured 2026-09-24): the stance knee flexes so the DEF
# chain's length moves under a planted foot (any spread over 0.1% is attention by
# rule; the shipped jump reads 2.06%), and gait_opposition / strike_lead - bands
# written as craft checks for authored gaits - read the real walks at 'attention'
# (08_01 heel lands 7.4% of its stride ahead of the hips against the 25-35% walking
# reference the gate quotes). A 'fail' in any block still stops the build.
GAIT_DEFORMATION_OK = ("ok", "attention")
# land keeps both feet planted through a 99 mm absorb, so its chain length moves 1.40%
# while planted (the jump's own landing: 2.06%) - 'attention' by the same rule.
LAND_DEFORMATION_OK = ("ok", "attention")
# land's last frame vs idle-loop's first, on the evaluated mesh: the seam tolerance
# (rigcheck LOOP_SEAM_TOLERANCE_MM), since the game blends one into the other.
LAND_POP_MM = 1.0

RIG = "werewolf-form-a_retopo_rig"
MESH = "werewolf-form-a_retopo"
IDLE = "idle"
CYCLE = 48  # frames, 2.0 s at 24 fps; last frame repeats the first


def run(name, params):
    status, result, message = dispatch(name, params)
    if status != "success":
        raise RuntimeError("%s failed: %s" % (name, message))
    return result


def upd():
    bpy.context.view_layer.update()


scene = bpy.context.scene
rig = bpy.data.objects[RIG]
mesh = bpy.data.objects[MESH]
print("FPS", scene.render.fps)

# --- 0. look: shading + region colour
import look_protagonist_human as look
look_report = look.apply(mesh, os.path.join(HERE, "palette.json"))
print("LOOK", json.dumps(look_report, default=str))


def reset_pose():
    """Every pose bone at rest, so no unkeyed channel leaks into a bake."""
    for pb in rig.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)
    upd()


# --- 1. the frozen mocap selection is what PROVENANCE.json says it is
import hashlib
PROVENANCE = json.load(open(os.path.join(MOCAP_DIR, "PROVENANCE.json")))
_frozen = {e["file"]: e for e in PROVENANCE["clips"]}
for _clip, _file, _params in MOCAP:
    _sha = hashlib.sha256(open(os.path.join(MOCAP_DIR, _file), "rb").read()).hexdigest()
    _entry = _frozen[_file]
    print("MOCAP %-12s %-22s %s rows %s sha256 %s (%s)" % (
        _clip, _file, _entry["source"]["member"], _entry["source"]["rows"], _sha[:16],
        "matches PROVENANCE" if _sha == _entry["crop_sha256"] else "MISMATCH"))
    if _sha != _entry["crop_sha256"]:
        raise SystemExit("%s is not the frozen crop PROVENANCE.json records (%s != %s); "
                         "re-derive it with mocap/freeze_mocap.py" % (_file, _sha, _entry["crop_sha256"]))

# --- 2. idle-loop
# Unassign first: evaluating the depsgraph flushes the assigned clip's values back
# onto the pose - that is how walk-loop's frame-1 heel roll (left toe 77.9 mm up)
# leaked into the first idle attempt.
rr.assign_action(rig, None)
reset_pose()
res = run("rigforge_action", {"action": "new", "name": IDLE, "loop": True, "rig": RIG})
idle_name = res["name"]
idle = bpy.data.actions[idle_name]
print("IDLE action", idle_name)

limbs = rr.ik_limbs(rig)
info = ra.locomotion_frame(rig, limbs)
fwd, up, right = info["forward"], info["up"], info["right"]
leg = info["leg_length"]
print("FRAME forward", tuple(round(v, 4) for v in fwd), "right", tuple(round(v, 4) for v in right),
      "leg_length %.4f" % leg)

LOWER = 0.035 * leg        # soft knees, constant (25.8 mm on this rig)
BOB = 0.008 * leg          # breathing bob, 2 breaths per cycle
SWAY = 0.012 * leg         # weight shift, 1 per cycle
CHEST_FWD = math.radians(5.0)
CHEST_BREATH = math.radians(1.5)
ARM_SWAY = math.radians(2.0)

pb = rig.pose.bones
torso = pb["torso"]; chest = pb["chest"]
planted = ["root", "foot_ik.L", "foot_ik.R", "foot_heel_ik.L", "foot_heel_ik.R",
           "toe_ik.L", "toe_ik.R"]
arms = ["upper_arm_fk.L", "upper_arm_fk.R", "forearm_fk.L", "forearm_fk.R"]
first, last = 1, 1 + CYCLE

# IK/FK layer: legs IK (0), arms FK (1), no stretch - keyed on both ends
for f in (first, last):
    for side in ("L", "R"):
        tp = pb["thigh_parent." + side]; ap = pb["upper_arm_parent." + side]
        tp["IK_FK"] = 0.0; tp["IK_Stretch"] = 0.0; ap["IK_FK"] = 1.0
        tp.keyframe_insert('["IK_FK"]', frame=f)
        tp.keyframe_insert('["IK_Stretch"]', frame=f)
        ap.keyframe_insert('["IK_FK"]', frame=f)
    for name in planted:
        pb[name].location = (0, 0, 0); pb[name].rotation_quaternion = (1, 0, 0, 0)
        pb[name].rotation_euler = (0, 0, 0)
        ra._key_transform(pb[name], f)

rest_torso = ra._rest_world(rig, torso)
for f in range(first, last + 1):
    t = (f - first) / CYCLE
    breath = 0.5 - 0.5 * math.cos(4 * math.pi * t)     # 0 at the seam, 1 mid-breath
    sway = math.sin(2 * math.pi * t)
    scene.frame_set(f)
    for name in ["torso", "chest"] + arms:
        pb[name].location = (0, 0, 0)
        pb[name].rotation_quaternion = (1, 0, 0, 0)
        pb[name].rotation_euler = (0, 0, 0)
    upd()
    offset = -up * (LOWER + BOB * breath) + right * (SWAY * sway)
    ra._set_world(rig, torso, Matrix.Translation(offset) @ rest_torso)
    upd()
    ra._key_transform(torso, f)
    cw = ra._world_matrix(rig, chest)
    pitch = CHEST_FWD - CHEST_BREATH * breath
    # A positive turn about 'right' tips the chest BACK (measured: head lead
    # -24.1 mm at +5 deg), so forward is the negative angle; asserted below.
    ra._set_world(rig, chest, ra._rotate_about(cw, right, -pitch, cw.translation.copy()))
    upd()
    ra._key_transform(chest, f)
    for name in arms[:2]:
        aw = ra._world_matrix(rig, pb[name])
        ra._set_world(rig, pb[name], ra._rotate_about(aw, right, ARM_SWAY * sway,
                                                      aw.translation.copy()))
        upd()
        ra._key_transform(pb[name], f)
    for name in arms[2:]:
        ra._key_transform(pb[name], f)
for c in rr.action_fcurves(idle):
    for p in c.keyframe_points:
        p.interpolation = "LINEAR"
    c.update()

scene.frame_set(first)
head_rest = ra._rest_world(rig, pb["head"]).translation
head_now = ra._world_matrix(rig, pb["head"]).translation
lead = (head_now - head_rest).dot(fwd)
print("IDLE head lead along forward at frame 1: %.4f m (drop %.4f m)"
      % (lead, (head_rest - head_now).z))
if lead <= 0:
    raise RuntimeError("chest pitch sign is backwards: head lead %.4f" % lead)
print("IDLE range", tuple(idle.frame_range), "fcurves", len(rr.action_fcurves(idle)))

# --- 2b. locomotion from the frozen mocap (note 1), then fall and land
# (clips_protagonist_human.py, unchanged). Every clip in place.
import clips_protagonist_human as clips
MOCAP_RESULT = {}
for _clip, _file, _params in MOCAP:
    rr.assign_action(rig, None)
    reset_pose()
    _res = run("rigforge_mocap_clip", dict(_params, target_rig=RIG, clip=_clip,
                                           source_path=os.path.join(MOCAP_DIR, _file)))
    _rt = _res["retarget"]
    _c = _res["contract"]
    MOCAP_RESULT[_c["action"]] = _res
    print("RETARGET %-16s from %s: %d frames (window %s s, seam cost %.2f deg), natural %.4f m/s "
          "at rig scale, scale %.6f, mapped %d, unmapped %s" % (
              _c["action"], _file, _c["frames"], _rt["loop_window"]["window_s"],
              _rt["loop_window"]["seam_cost_deg"], _c["natural_speed_mps"] or 0.0,
              _rt["scale"], _rt["mapped_count"], _rt["unmapped"]))
    print("   fidelity transfer_check worst %.4f deg (%s) / %.4f mm; rest_alignment_deg %s" % (
        _rt["transfer_check"]["worst_rotation_deg"], _rt["transfer_check"]["worst_rotation_bone"],
        _rt["transfer_check"]["worst_position_mm"], json.dumps(_rt["rest_alignment_deg"])))
    print("   cleanup ground %s | flat %s | anchor %s | hips lowered %s | seam residual "
          "%.2f deg (%s) %.1f mm" % (
              json.dumps(_rt["ground"]), json.dumps(_rt["foot_flat_calibration"]),
              json.dumps(_rt["ik_anchor"]), json.dumps(_rt["hip_lowering"]),
              _rt["seam_residual"]["worst_deg"], _rt["seam_residual"]["worst_bone"],
              _rt["seam_residual"]["worst_mm"]))
    _st = _res["motion_stats"]
    print("   motion_stats trunk/leg speed %s trunk_pitch_std %s head_pitch_std %s step %s s "
          "(cv %s)" % (_st["trunk_to_leg_speed_ratio"], _st["posture"]["trunk_pitch_std_deg"],
                       _st["posture"]["head_pitch_std_deg"],
                       _st["footfall"]["step_interval_mean_s"], _st["footfall"]["step_interval_cv"]))
    for _w in _rt["warnings"]:
        print("   warning:", _w)
rr.assign_action(rig, None)
reset_pose()
fall_action, fall_rep = clips.author_fall(rig, scene, run, info)
print("FALL", fall_action.name, tuple(fall_action.frame_range), json.dumps(fall_rep, default=str))
land_action, land_rep = clips.author_land(rig, scene, run, info, idle_name)
print("LAND", land_action.name, tuple(land_action.frame_range), json.dumps(land_rep, default=str))
KNEE = {}
for _name in ("jump", fall_action.name, land_action.name) + GAITS + ("crouch_idle-loop",):
    KNEE[_name] = clips.knee_flex_max(rig, scene, bpy.data.actions[_name])
    print("KNEE %-16s deepest flex %s" % (_name, json.dumps(KNEE[_name])))
rr.assign_action(rig, None)
reset_pose()


# --- 3. gates. Every gate is run and printed before any failure stops the build.
FAILED = []


def gate(action, mode, deformation_ok=("ok",), seam_required=None, reference=False,
         quality=False, rollup=True):
    r = run("animation_check", {"rig": RIG, "action": action, "mode": mode,
                                "motion_quality": quality})
    if reference:
        print("REFERENCE (printed, not gated) - the existing clip's own readings:")
    seam = r.get("loop_seam_closure") or {}
    print("GATE %-11s mode=%-8s gate=%s worst_drift_mm=%s deformation_gate=%s seam=%s worst_mm=%s tol=%s"
          % (action, r.get("mode"), r.get("gate"), r.get("worst_drift_mm"),
             r.get("deformation_gate"), seam.get("verdict"), seam.get("worst_mm"),
             seam.get("tolerance_mm")))
    for foot in r.get("feet", []):
        print("   foot %s ground_mm=%s steps=%s worst_drift_mm=%s" % (
            foot.get("bone"), foot.get("ground_mm"), foot.get("steps_measured"),
            foot.get("worst_drift_mm")))
    for limb in (r.get("bone_stretch_budget") or {}).get("limbs", []):
        print("   stretch %s worst %.2f%% (frame %s, contact spread %s%%) %s" % (
            limb["limb"], limb["worst_stretch_pct"], limb["worst_frame"],
            limb.get("worst_contact_spread_pct"), limb["verdict"]))
    head = r.get("ik_reach_headroom") or {}
    print("   ik_reach worst_extension_frac=%s %s" % (head.get("worst_extension_frac"),
                                                   head.get("verdict")))
    opp = r.get("gait_opposition")
    if opp:
        print("   gait_opposition %s worst %s at %s deg (%s)" % (
            opp.get("verdict"), opp.get("worst_pair"), opp.get("worst_phase_lag_deg"),
            ", ".join("%s %s" % (p["pair"], p["phase_lag_deg"]) for p in opp["pairs"])))
    lead = r.get("strike_lead")
    if lead:
        print("   strike_lead %s worst %s mm = %s%% of a %s mm stride" % (
            lead.get("verdict"), lead.get("worst_lead_mm"),
            lead.get("worst_lead_pct_of_stride"), lead.get("stride_mm")))
    print("   treadmill %.3f mm/frame = %.4f m/s" % (
        r.get("treadmill_mm_per_frame") or 0.0,
        (r.get("treadmill_mm_per_frame") or 0.0) * scene.render.fps / 1000.0))
    mq = r.get("motion_quality")
    if mq:
        print("   motion_quality %s: %s" % (mq["verdict"], ", ".join(
            "%s %s (floor %s) %s" % (c["metric"], c["value"], c["min"], c["verdict"])
            for c in mq["checks"])))
    if rollup:
        ok = r.get("gate") == "ok" and r.get("deformation_gate") in deformation_ok
    else:
        # the blocks one by one, without the gait-only ones (see MOCAP)
        blocks = {"stretch": (r.get("bone_stretch_budget") or {}).get("verdict"),
                  "reach": (r.get("ik_reach_headroom") or {}).get("verdict")}
        print("   gated without the gait blocks (not a gait): slide %s, %s; gait_opposition "
              "%s and strike_lead %s printed only" % (
                  r.get("gate"), blocks, (r.get("gait_opposition") or {}).get("verdict"),
                  (r.get("strike_lead") or {}).get("verdict")))
        ok = r.get("gate") == "ok" and all(v in deformation_ok for v in blocks.values())
    need_seam = action.endswith("-loop") if seam_required is None else seam_required
    if need_seam and seam.get("verdict") != "ok":
        ok = False
    if quality and r.get("motion_quality_gate") != "ok":
        ok = False
    if not ok and not reference:
        FAILED.append("%s: %s" % (action, r.get("says")))
    return r


gate(idle_name, "planted")
# the shipped jump, unmodified: the deformation level a deep knee bend already
# reads at on this rig, which the new clips are compared against
gate("jump", "auto", reference=True)

SPEEDS = {}
for name in GAITS:
    r = gate(name, "in_place", deformation_ok=GAIT_DEFORMATION_OK, quality=True)
    natural = MOCAP_RESULT[name]["contract"]["natural_speed_mps"]
    measured = (r.get("treadmill_mm_per_frame") or 0.0) * scene.render.fps / 1000.0
    game = natural * SCALE
    SPEEDS[name] = game
    lo, hi = SPEED_BANDS[name]
    agree = abs(measured - natural) / natural
    target = SPEED_TARGETS.get(name)
    print("SPEED %-16s take's hip-joint speed %.4f m/s at rig scale (treadmill measures %.4f, "
          "%.2f%% apart) -> %.4f m/s in game, band [%.2f, %.2f)%s"
          % (name, natural, measured, 100.0 * agree, game, lo, hi,
             (", lane target [%.1f, %.1f] %s" % (target[0], target[1],
                                                  "met" if target[0] <= game <= target[1]
                                                  else "NOT met")) if target else ""))
    if not lo <= game < hi:
        FAILED.append("%s: natural speed %.4f m/s in game is outside [%s, %s)" % (name, game, lo, hi))
    if agree > SPEED_AGREEMENT:
        FAILED.append("%s: treadmill %.4f vs the take's %.4f m/s" % (name, measured, natural))
gate("crouch_idle-loop", "planted", deformation_ok=("ok", "attention"), rollup=False)
# The sprint the game asks for (speed_sprint 7.0 m/s) plays run-loop through the
# fallback at this playback rate - quoted, not hidden in a faster clip:
print("SPRINT none shipped: the game plays run-loop at speed_scale %.3f for 7.0 m/s "
      "(%.3f for the 6.5 m/s sprint floor); an honest sprint needs a take reaching "
      ">= 6.5 m/s at game scale (%.2f m/s at rig scale)"
      % (7.0 / SPEEDS["run-loop"], 6.5 / SPEEDS["run-loop"], 6.5 / SCALE))

# fall: an airborne hold whose feet never touch the floor, so there is no plant to
# slide; what it is gated on is the limbs (stretch/reach) and - because the game
# loops it (werewolf LOOPING) - its seam. Blender's cyclic flag is raised for the
# check only, so animation_check measures the seam on the flesh, then dropped.
fall_action.use_cyclic = True
gate(fall_action.name, "planted", seam_required=True)
fall_action.use_cyclic = False
gate(land_action.name, "planted", deformation_ok=LAND_DEFORMATION_OK)


def _evaluated(action, frame):
    rr.assign_action(rig, action)
    scene.frame_set(frame)
    upd()
    ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    pts = [mesh.matrix_world @ v.co for v in me.vertices]
    ev.to_mesh_clear()
    return pts


# land -> idle: the game blends land's last frame into idle's first; measured on the
# evaluated mesh, vertex by vertex, the same way loop_seam_closure measures a seam.
_a = _evaluated(land_action, int(land_action.frame_range[1]))
_b = _evaluated(idle, 1)
land_pop_mm = max((p - q).length for p, q in zip(_a, _b)) * 1000.0
print("LAND->IDLE worst vertex %.3f mm (tolerance %.1f mm)" % (land_pop_mm, LAND_POP_MM))
if land_pop_mm > LAND_POP_MM:
    FAILED.append("land ends %.3f mm from idle-loop frame 1" % land_pop_mm)
rr.assign_action(rig, None)
reset_pose()
if FAILED:
    raise RuntimeError("gates failed:\n  " + "\n  ".join(FAILED))

# --- 4. facing
rr.assign_action(rig, None)
reset_pose()
rig.rotation_mode = "XYZ"
rig.rotation_euler = (0.0, 0.0, math.radians(YAW))
upd()

# --- 5. export through the stage-7 command, clips slid to t=0
_orig = rr.op_kwargs


def _patched(operator, kwargs):
    if "export_yup" in kwargs:
        kwargs = dict(kwargs, export_anim_slide_to_zero=True)
    return _orig(operator, kwargs)


rr.op_kwargs = _patched
try:
    res = run("rigforge_export_godot", {
        "rig": RIG, "path": OUT, "godot_import_script": False,
        "actions": [idle_name, "jump", "punch.L", "punch.R", "walk-loop",
                    "run-loop", "crouch_idle-loop", "crouch_walk-loop",
                    fall_action.name, land_action.name],
    })
finally:
    rr.op_kwargs = _orig
for k in ("path", "actions", "deform_bone_count", "bbone_flattened", "morph_targets",
          "morph_animation", "unit_scale", "y_up", "warnings", "seconds"):
    v = res.get(k)
    if k == "bbone_flattened" and isinstance(v, dict):
        v = {kk: v.get(kk) for kk in ("count", "max_segments_before", "segments_after")}
    if k == "morph_animation" and isinstance(v, dict):
        v = {clip: len(names) for clip, names in v.items()}
    print("EXPORT", k, json.dumps(v, default=str))

# --- 6. image inventory, pinned
import struct
with open(OUT, "rb") as f:
    blob = f.read()
_jlen = struct.unpack_from("<I", blob, 12)[0]
_gltf = json.loads(blob[20:20 + _jlen])
inventory = [[im.get("name"), im.get("mimeType")] for im in _gltf.get("images", [])]
pinned = json.load(open(os.path.join(HERE, "protagonist_human.images.json")))
print("IMAGES", json.dumps(inventory), "pinned", json.dumps(pinned))
if inventory != pinned:
    raise RuntimeError("image inventory %s != pinned %s: Godot would re-extract and churn "
                       "the committed <kind>_Image_N.png files" % (inventory, pinned))

# --- 7. manifest beside the glb
manifest = {"scale": SCALE, "walk_speed_mps": round(SPEEDS["walk-loop"], 4),
            "run_speed_mps": round(SPEEDS["run-loop"], 4),
            "crouch_walk_speed_mps": round(SPEEDS["crouch_walk-loop"], 4),
            "clip_map": CLIP_MAP}
with open(os.path.join(os.path.dirname(os.path.abspath(OUT)), "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print("MANIFEST", json.dumps(manifest))
print("DONE")

