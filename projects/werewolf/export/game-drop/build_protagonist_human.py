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
  7. manifest.json is written beside <out.glb>: scale, clip_map and walk_speed_mps
     (the in-place walk's natural speed at game scale = stride speed x scale).

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
CLIP_MAP = {"idle": "idle", "walk": "walk", "jump": "jump", "attack_1": "punch_R",
            "attack_2": "punch_L"}

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


# --- 3. gates
def gate(action, mode, deformation_ok=("ok",)):
    r = run("animation_check", {"rig": RIG, "action": action, "mode": mode})
    seam = r.get("loop_seam_closure") or {}
    print("GATE %-10s mode=%-8s gate=%s deformation_gate=%s seam=%s worst_mm=%s tol=%s"
          % (action, mode, r.get("gate"), r.get("deformation_gate"), seam.get("verdict"),
             seam.get("worst_mm"), seam.get("tolerance_mm")))
    for foot in r.get("feet", []):
        print("   foot %s ground_mm=%s worst_drift_mm=%s" % (foot.get("bone"), foot.get("ground_mm"),
                                                           foot.get("worst_drift_mm")))
    for limb in (r.get("bone_stretch_budget") or {}).get("limbs", []):
        print("   stretch %s worst %.2f%% %s" % (limb["limb"], limb["worst_stretch_pct"], limb["verdict"]))
    head = r.get("ik_reach_headroom") or {}
    print("   ik_reach worst_extension_frac=%s" % head.get("worst_extension_frac"))
    if r.get("gate") != "ok" or seam.get("verdict") != "ok" \
            or r.get("deformation_gate") not in deformation_ok:
        raise RuntimeError("%s failed its gate: %s" % (action, r.get("says")))
    return r


gate(idle_name, "planted")
# walk-loop's legs compress 1.43% at frames 13/29 - the unmodified source walk
# measures the same 'attention', so it is inherited, not introduced here.
gate("walk-loop", "in_place", deformation_ok=("ok", "attention"))

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
        "actions": [idle_name, "jump", "punch.L", "punch.R", "walk-loop"],
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
manifest = {"scale": SCALE, "walk_speed_mps": round(walk_mps * SCALE, 4), "clip_map": CLIP_MAP}
with open(os.path.join(os.path.dirname(os.path.abspath(OUT)), "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print("MANIFEST", json.dumps(manifest))
print("DONE")

