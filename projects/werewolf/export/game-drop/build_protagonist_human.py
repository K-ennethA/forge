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
  1. walk-loop goes in place. The source walk is a root-motion clip (root travels
     0.5889 m over its 32-frame cycle = 0.4417 m/s); the game's controller moves
     the body (CharacterModel.set_locomotion), so the root travel is zeroed and
     animation_check's in_place mode gates the treadmill.
  2. idle-loop is authored (48 frames = 2.0 s, last frame == first) through
     rigforge_action + the walk machinery (_set_world / _key_transform): soft
     knees, two breaths and one weight shift per cycle, chest tipped 5 deg
     forward. Legs stay IK on rest-keyed targets. Gated by animation_check
     (loop_seam_closure + planted-foot slide); the build stops if either fails.
  2b. the locomotion wave (clips_protagonist_human.py), all in place:
     run-loop (12 f, 2.10 m stride = 4.200 m/s raw) and sprint-loop (10 f, 3.40 m =
     8.160 m/s raw) through rigforge_walk (travel=false, stance 0.30 / 0.20 so
     both feet leave the ground) plus a mid-stance bounce, a lean about the hip
     joint, a running arm and a knee-capped heel kick; fall (18 f, airborne hold, arm flail, periodic
     because the game loops it); land (12 f, absorb into idle-loop's frame 1).
     Gated per clip (slide, stretch/reach, seam, gait_opposition + strike_lead
     on the gaits, speed inside the game's band, knee fold under the cap,
     land->idle pop on the mesh);
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
     run_speed_mps and sprint_speed_mps (each in-place gait's natural speed at game
     scale = stride speed x scale).

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
# contract height for a humanoid. walk_speed_mps follows as 0.4417 x scale.
SCALE = 0.9444
CLIP_MAP = {"idle": "idle", "walk": "walk", "run": "run", "sprint": "sprint",
            "jump": "jump", "fall": "fall", "land": "land", "attack_1": "punch_R",
            "attack_2": "punch_L"}
# The game's locomotion bands (in-game m/s, after SCALE) each authored gait must land
# its natural speed in - the lane brief's targets inside werewolf's run >= 3 and
# sprint >= 6.5 thresholds (characters/character_model.gd).
SPEED_BANDS = {"run-loop": (3.5, 5.5), "sprint-loop": (7.0, 9.0)}
# The authored stride speed and animation_check's pooled in-place treadmill speed
# must agree to within this fraction.
SPEED_AGREEMENT = 0.03
# deformation_gate levels allowed, each measured (2026-09-24) and no worse than a
# shipped clip's own reading on this rig:
#  * every gait flexes the stance knee, so the DEF leg chain's length moves while the
#    foot is planted (contact spread: walk-loop 0.78%, run 1.12%, sprint 1.14%; the
#    shipped jump 2.06%) - 'attention' by rule for any spread over 0.1%;
#  * the swing knee's flex compresses the chain (run 2.48%, sprint 2.48%, jump 2.35%,
#    all over the 2% 'ok' line, far under the 5% fail) - see clips.KNEE_FLEX_CAP_DEG
#    for the fold the gaits are held to;
#  * sprint's heel lands 10.1% of its stride ahead of the hips, under the 15% the
#    strike_lead gate calls ok. That band is the walking reference; at 8 m/s a
#    15% lead puts the heel 0.51 m ahead, past this leg's reach without a 0.15 m
#    crouch (the reach clamp's ceiling), and sprinters land closer under the body.
GAIT_DEFORMATION_OK = {"run-loop": ("ok", "attention"), "sprint-loop": ("ok", "attention")}
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


# --- 1. walk-loop in place
walk = bpy.data.actions["walk-loop"]
stride = None
for c in rr.action_fcurves(walk):
    if c.data_path == 'pose.bones["root"].location':
        vals = [p.co.y for p in c.keyframe_points]
        if c.array_index == 1:
            stride = vals[-1] - vals[0]
        for p in c.keyframe_points:
            p.co.y = 0.0
            p.handle_left.y = 0.0
            p.handle_right.y = 0.0
        c.update()
walk_frames = walk.frame_range[1] - walk.frame_range[0]
walk_mps = abs(stride) / (walk_frames / scene.render.fps)
print("WALK root travel removed: stride_m=%.4f over %d frames = %.4f m/s"
      % (abs(stride), walk_frames, walk_mps))

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

# --- 2b. the locomotion wave (clips_protagonist_human.py): run-loop, sprint-loop,
# fall, land. Every clip in place; see that module for how each is authored.
import clips_protagonist_human as clips
GAITS = {}
for spec in (clips.RUN, clips.SPRINT):
    rep = clips.author_gait(rig, scene, run, spec, info)
    GAITS[rep["action"]] = rep
    print("GAIT", json.dumps(rep, default=str))
fall_action, fall_rep = clips.author_fall(rig, scene, run, info)
print("FALL", fall_action.name, tuple(fall_action.frame_range), json.dumps(fall_rep, default=str))
land_action, land_rep = clips.author_land(rig, scene, run, info, idle_name)
print("LAND", land_action.name, tuple(land_action.frame_range), json.dumps(land_rep, default=str))
KNEE = {}
for _name in ("walk-loop", "jump", "run-loop", "sprint-loop", fall_action.name,
              land_action.name):
    KNEE[_name] = clips.knee_flex_max(rig, scene, bpy.data.actions[_name])
    print("KNEE %-11s deepest flex %s" % (_name, json.dumps(KNEE[_name])))
rr.assign_action(rig, None)
reset_pose()


# --- 3. gates. Every gate is run and printed before any failure stops the build.
FAILED = []


def gate(action, mode, deformation_ok=("ok",), seam_required=None, reference=False):
    r = run("animation_check", {"rig": RIG, "action": action, "mode": mode})
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
    ok = r.get("gate") == "ok" and r.get("deformation_gate") in deformation_ok
    need_seam = action.endswith("-loop") if seam_required is None else seam_required
    if need_seam and seam.get("verdict") != "ok":
        ok = False
    if not ok and not reference:
        FAILED.append("%s: %s" % (action, r.get("says")))
    return r


gate(idle_name, "planted")
# walk-loop's legs compress 1.43% at frames 13/29 - the unmodified source walk
# measures the same 'attention', so it is inherited, not introduced here.
gate("walk-loop", "in_place", deformation_ok=("ok", "attention"))
# the shipped jump, unmodified: the deformation level a deep knee bend already
# reads at on this rig, which the new clips are compared against
gate("jump", "auto", reference=True)

for name, rep in GAITS.items():
    r = gate(name, "in_place", deformation_ok=GAIT_DEFORMATION_OK[name])
    if not r.get("gait_opposition") or r["gait_opposition"].get("verdict") != "ok":
        FAILED.append("%s: gait_opposition %s" % (name, (r.get("gait_opposition") or {}).get("verdict")))
    measured = (r.get("treadmill_mm_per_frame") or 0.0) * scene.render.fps / 1000.0
    rep["treadmill_raw_mps"] = measured
    game = rep["natural_speed_raw_mps"] * SCALE
    lo, hi = SPEED_BANDS[name]
    agree = abs(measured - rep["natural_speed_raw_mps"]) / rep["natural_speed_raw_mps"]
    print("SPEED %-11s stride %.4f m / %d frames = %.4f m/s raw (treadmill measures %.4f, "
          "%.2f%% apart) -> %.4f m/s in game, band [%.1f, %.1f]"
          % (name, rep["stride_m"], rep["cycle_frames"], rep["natural_speed_raw_mps"],
             measured, 100.0 * agree, game, lo, hi))
    if not lo <= game <= hi:
        FAILED.append("%s: natural speed %.4f m/s in game is outside [%s, %s]" % (name, game, lo, hi))
    if KNEE[name]["deg"] > clips.KNEE_FLEX_CAP_DEG + 0.05:
        FAILED.append("%s: knee folds %.2f deg, over the %.0f deg cap"
                      % (name, KNEE[name]["deg"], clips.KNEE_FLEX_CAP_DEG))
    if agree > SPEED_AGREEMENT:
        FAILED.append("%s: treadmill %.4f vs authored %.4f m/s" % (name, measured,
                                                                   rep["natural_speed_raw_mps"]))

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
                    "run-loop", "sprint-loop", fall_action.name, land_action.name],
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
manifest = {"scale": SCALE, "walk_speed_mps": round(walk_mps * SCALE, 4),
            "run_speed_mps": round(GAITS["run-loop"]["natural_speed_raw_mps"] * SCALE, 4),
            "sprint_speed_mps": round(GAITS["sprint-loop"]["natural_speed_raw_mps"] * SCALE, 4),
            "clip_map": CLIP_MAP}
with open(os.path.join(os.path.dirname(os.path.abspath(OUT)), "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print("MANIFEST", json.dumps(manifest))
print("DONE")

