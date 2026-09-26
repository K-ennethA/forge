"""The approved MPFB v4 protagonist carrying the full retargeted clip set (draft -> gated set).

Run headless on a COPY of projects/werewolf/models/mpfb-custom-draft.blend (never on the source):

    blender -b --factory-startup <copy.blend> --python mpfb_clips.py -- build <out.blend> <report.json> [clip ...]
    blender -b --factory-startup <out.blend>  --python mpfb_clips.py -- render <prefix> [clip ...]

TARGET-RIG PREP (why a proxy). The v4 body carries MPFB's game_engine rig (53 bones, UE-mannequin
names: pelvis, spine_01..03, neck_01, head, clavicle/upperarm/lowerarm/hand, thigh/calf/foot/ball,
fingers). forge's retarget (rigforge_retarget: rest-relative transfer, IK legs, flat-stance foot
calibration, ball plants locked to the floor, hip lowering at the reach cap, loop search + seam
closure) and its gates (animation_check, motion_stats) are written against a Rigify control rig. So
the body is NOT re-rigged: a Rigify PROXY is generated whose metarig is fitted bone-for-bone onto the
game_engine joints (same heads, tails and rolls - the rest matrices are equal, measured below), the
take is retargeted onto the proxy with the production defaults, the proxy's gates run, and the
proxy's ORG bones are copied back onto the game_engine bones frame by frame (armature-space
rotations; pelvis also translation). The transfer's fidelity (every mapped joint, every frame) is
quoted, so the proxy gates are gates on the game rig.

Canonical naming: the game_engine (UE mannequin) names STAY on the rig - they are what the fitted
clothes' vertex groups and the MPFB weights are bound to, and what Godot's humanoid profile reads.
The reconciliation lives in one table (GAME_FROM_ORG below): neck_01 <- the Rigify neck pair,
calf <- shin, ball <- toe, clavicle <- shoulder, lowerarm <- forearm, spine_01..03 <- spine.001..003.

Fingers: CMU / 100STYLE carry no fingers (the hand is one joint) - fingers hold the MPFB rest,
except where an authored layer poses them (run-loop: SOFT_FIST_CURL_DEG).
Mixamo takes carry all 15 per hand; they are transferred straight onto the game fingers (local
rest-relative rotations conjugated through anatomical hand frames), so the punches close into fists.
"""
import bpy, sys, os, math, json, time, hashlib
from mathutils import Vector, Matrix, Quaternion
from mathutils.bvhtree import BVHTree

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERE = os.path.dirname(os.path.abspath(__file__))
FORGE = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
ADDON = os.path.join(FORGE, "addon")
MOCAP_DIR = os.path.join(HERE, "mocap")
MIXAMO_DIR = "C:/forge-assets/thirdparty/mixamo"
sys.path.insert(0, ADDON)

GAME_RIG = "Protagonist.rig"
BODY = "Protagonist"
JACKET = "Protagonist.jacket"
TEE = "Protagonist.elvs_crude_t-shirt_male"
PROXY_META = "FORGE_proxy_metarig"
PROXY = "FORGE_proxy_rig"
FPS = 24
# Game-side scale: the humanoid contract height (1.75 m, werewolf CharacterKinds) over this body's
# measured height (build_custom TARGET_H 1.8825 m, feet to crown). In-game speeds below = rig-scale
# speed x GAME_SCALE. The delivery lane owns the manifest; this lane quotes.
CONTRACT_H = 1.75

# ================================================================================================
# ARTIST KNOBS - authored layers on top of the takes. Each is a one-line edit: change the number,
# rerun the two commands in the docstring (build, then render the clip), done.
# ================================================================================================
# RUN - "the running hands should be in a soft closed position" (review-log 2026-09-25, clip
# previews verdict). The CMU run carries no finger data, so its hands rode at the MPFB rest (open).
# A static soft-fist layer curls every finger joint by these degrees - (knuckle, middle, tip) - about
# the finger's own flex axis (bone-local +X: the axis every Mixamo fist on this rig curls about,
# measured, X component 0.90-1.00 on all 30 finger bones of heavy / attack_3). Soft = roughly half
# a clenched fist (the heavy punch's fist on this rig reads knuckle 86-94, middle 100-119, tip
# 104-124). The pinky side curls a little more than the index side, as a relaxed hand does; the
# thumb folds in to rest along the index. Bigger numbers = tighter hand.
SOFT_FIST_CURL_DEG = {
    "index":  (30.0, 45.0, 25.0),
    "middle": (35.0, 50.0, 30.0),
    "ring":   (40.0, 55.0, 30.0),
    "pinky":  (45.0, 55.0, 30.0),
    "thumb":  (10.0, 20.0, 20.0),
}
# WALK carries the same soft fist at a fraction of the run's curl - "not as closed as running but
# a little more [than] rest" (artist, 2026-09-25). 0.0 = the open rest hand, 1.0 = the run's fist.
WALK_SOFT_FIST_SCALE = 0.55
# CROUCH_WALK - "he should have his legs more spread apart". Each foot's IK target is moved this
# far OUTWARD (mm, per foot - the stance widens by twice this); the hips are not moved by this
# layer, the knees follow the feet (the knee pole moves with its foot when the clip keys one).
# Applied to crouch_walk-loop and crouch_idle-loop (so the two crouches share a stance).
CROUCH_STANCE_WIDEN_MM = 70.0
# ... and the knees go WITH the feet: the pole-less leg IK keeps the knee in the take's leg plane,
# so moving only the feet left the thighs where they were and the knees caved in (measured on the
# first build: knees 64-79 mm INSIDE the hip-ankle line on both crouches). Each leg is turned about
# its own hip-ankle line (feet, plants and knee bend untouched) this fraction of the way toward
# "knee over the toes": 1.0 = the knee tracks straight over the foot, 0.0 = the IK's knee.
CROUCH_KNEE_OVER_TOES = 1.0
# WALK - "his arms are too close to his side and it makes his movement feel too rigid". Each whole
# arm is carried this many degrees further out from the trunk (about the body's forward axis
# through the shoulder, the crouch flare's machinery), on top of the take's swing - whose timing
# and amplitude stay the take's.
WALK_ARM_CARRIAGE_DEG = 12.0
# CROUCH (both clips) - "arms flared out at an angle" (review-log 2026-09-25 mocap verdict (c)):
# each whole arm turned outward about the body's forward axis through the shoulder by this much on
# top of the take. The take's own mean abduction and the result are both quoted (arm_abduction).
ARM_FLARE_DEG = 32.0
# CROUCH (both clips) - "should have the character bend the knees": the takes carry a real bend
# (measured on the MPFB proxy: Crouched_ID 55 deg of knee flexion, a 'squat slightly' style), which
# the artist read as none. This layer lowers the hips (the proxy's torso control; the IK feet stay
# planted, so the knees fold) until the straighter leg's mean flexion reaches this - a deep game
# crouch, not a squat. Before/after quoted per clip.
CROUCH_KNEE_DEG = 75.0

# metarig bone -> (game bone for head, game bone for tail | "tail" | point fn), roll from the game bone
SIDES = (("L", "l"), ("R", "r"))


def upd():
    bpy.context.view_layer.update()


def game_scale():
    return CONTRACT_H / 1.8825


# ------------------------------------------------------------------------------------------------
# proxy
# ------------------------------------------------------------------------------------------------
def meta_fit(game):
    """{metarig bone: (head, tail, z_axis)} in world space, read off the game_engine rest."""
    mw = game.matrix_world
    B = game.data.bones

    def ht(name):
        b = B[name]
        return mw @ b.head_local, mw @ b.tail_local, (mw.to_3x3() @ b.matrix_local.to_3x3() @ Vector((0, 0, 1))).normalized()

    fit = {}
    for m, g in (("spine", "pelvis"), ("spine.001", "spine_01"), ("spine.002", "spine_02"),
                 ("spine.003", "spine_03"), ("spine.006", "head")):
        fit[m] = ht(g)
    nh, nt, nz = ht("neck_01")
    mid = (nh + nt) / 2
    fit["spine.004"] = (nh, mid, nz)
    fit["spine.005"] = (mid, nt, nz)
    for S, s in SIDES:
        for m, g in (("shoulder", "clavicle"), ("upper_arm", "upperarm"), ("forearm", "lowerarm"),
                     ("hand", "hand"), ("thigh", "thigh"), ("shin", "calf"), ("foot", "foot"), ("toe", "ball")):
            fit["%s.%s" % (m, S)] = ht("%s_%s" % (g, s))
        ankle = mw @ B["foot_%s" % s].head_local
        sgn = 1.0 if S == "L" else -1.0
        # heel pivot: on the floor under the back of the heel (the boot sole), across the foot
        fit["heel.02.%s" % S] = (Vector((ankle.x - sgn * 0.035, ankle.y + 0.055, 0.0)),
                                 Vector((ankle.x + sgn * 0.035, ankle.y + 0.055, 0.0)), Vector((0, 0, 1)))
    return fit


def build_proxy(game):
    from forge.tools import rigforge_rig as rr
    rr.ensure_rigify()
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.object.armature_basic_human_metarig_add()
    meta = bpy.context.view_layer.objects.active
    meta.name = PROXY_META
    fit = meta_fit(game)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = meta.data.edit_bones
    for name in ("breast.L", "breast.R", "pelvis.L", "pelvis.R"):
        if name in eb:
            eb.remove(eb[name])
    # the neck starts off the top of spine_03 (game_engine: neck_01 is a child of spine_03 with a gap)
    eb["spine.004"].use_connect = False
    for name, (h, t, z) in fit.items():
        b = eb[name]
        b.use_connect = False
    for name, (h, t, z) in fit.items():
        b = eb[name]
        b.head = h
        b.tail = t
        b.align_roll(z)
    for name in ("spine.001", "spine.002", "spine.003", "spine.005", "spine.006"):
        eb[name].use_connect = True
    for S, _s in SIDES:
        for name in ("upper_arm", "forearm", "hand", "shin", "foot", "toe"):
            eb["%s.%s" % (name, S)].use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")
    # super_head would chain the neck onto the torso's tip; game_engine leaves a gap there
    prm = meta.pose.bones["spine.004"].rigify_parameters
    if hasattr(prm, "connect_chain"):
        prm.connect_chain = False
    bpy.ops.object.select_all(action="DESELECT")
    meta.select_set(True)
    bpy.context.view_layer.objects.active = meta
    before = set(bpy.data.objects)
    status = bpy.ops.pose.rigify_generate()
    new = [o for o in bpy.data.objects if o not in before and o.type == "ARMATURE"]
    if "FINISHED" not in status or not new:
        raise RuntimeError("rigify_generate: %s" % status)
    proxy = new[0]
    proxy.name = PROXY
    # The game_engine chain cannot stretch, so neither may the proxy's: Rigify leaves ik_stretch 0.1
    # on its IK solver bones, and with the knee nearly straight at the MPFB rest the solver shortened
    # the leg instead of bending it (measured on CMU 08_01 before this line: ORG-thigh 3.5% short at
    # frame 1, the game ankle 29 mm off the proxy's gated one).
    for pb in proxy.pose.bones:
        pb.ik_stretch = 0.0
    for S, _s in SIDES:
        for sw in ("thigh_parent.%s" % S, "upper_arm_parent.%s" % S):
            pb = proxy.pose.bones.get(sw)
            if pb is not None and "IK_Stretch" in pb.keys():
                pb["IK_Stretch"] = 0.0
    meta.hide_render = True
    meta.hide_set(True)
    proxy.hide_render = True
    upd()
    return proxy, fit


# game bone <- proxy ORG bone (rotation, armature space); pelvis also takes the translation.
GAME_FROM_ORG = {"pelvis": "ORG-spine", "spine_01": "ORG-spine.001", "spine_02": "ORG-spine.002",
                 "spine_03": "ORG-spine.003", "head": "ORG-spine.006"}
for _S, _s in SIDES:
    for _m, _g in (("shoulder", "clavicle"), ("upper_arm", "upperarm"), ("forearm", "lowerarm"),
                   ("hand", "hand"), ("thigh", "thigh"), ("shin", "calf"), ("foot", "foot"), ("toe", "ball")):
        GAME_FROM_ORG["%s_%s" % (_g, _s)] = "ORG-%s.%s" % (_m, _S)
# neck_01 spans the proxy's two neck bones: aimed from ORG-spine.004's head at ORG-spine.006's head
# (the head joint lands exactly where the proxy put it), twisted as ORG-spine.005.
NECK = ("neck_01", "ORG-spine.004", "ORG-spine.005", "ORG-spine.006")
# The upper body is carried by ROTATION only: every game bone takes its ORG bone's armature-space
# orientation (equal rests, so its direction is the proxy segment's direction). Rigify's
# basic_spine lets its ORG segments change length along the tweak chain (measured: a 68 mm spine
# segment stretching up to ~30 mm on stagger), which a rigid game chain cannot and should not do,
# so upper-body joint POSITIONS differ from the proxy by that stretch; the first draft aimed each
# game spine bone at the next ORG joint instead, which bent the chain to chase the stretch (no
# better in position, worse in orientation) and was dropped.
SPINE_AIM = {}


def rest_equality(game, proxy):
    """Largest rest-matrix difference game bone vs its ORG source (mm, deg)."""
    worst_mm, worst_deg, worst = 0.0, 0.0, None
    for g, o in GAME_FROM_ORG.items():
        a = game.data.bones[g].matrix_local
        b = proxy.data.bones[o].matrix_local
        mm = ((a.to_translation() - b.to_translation()).length) * 1000
        deg = math.degrees(a.to_quaternion().rotation_difference(b.to_quaternion()).angle)
        if deg > 180:
            deg = 360 - deg
        if mm + deg > worst_mm + worst_deg:
            worst_mm, worst_deg, worst = mm, deg, g
    return {"worst_bone": worst, "head_mm": round(worst_mm, 4), "roll_deg": round(worst_deg, 4)}


# ------------------------------------------------------------------------------------------------
# proxy -> game_engine transfer
# ------------------------------------------------------------------------------------------------
def _hier(game):
    out, seen = [], set()

    def visit(b):
        out.append(b.name)
        for c in b.children:
            visit(c)
    for b in game.data.bones:
        if b.parent is None:
            visit(b)
    return out


def _rot3(m):
    return m.to_3x3().normalized()


def _write_action(game, action_name, frames_basis):
    """frames_basis: list (per frame) of {bone: (loc Vector, rot Quaternion)} -> a keyed action on the
    game rig, every bone keyed every frame (quaternion hemisphere kept continuous)."""
    old = bpy.data.actions.get(action_name)
    if old is not None:
        old.use_fake_user = False
        bpy.data.actions.remove(old)
    act = bpy.data.actions.new(action_name)
    act.use_fake_user = True
    if game.animation_data is None:
        game.animation_data_create()
    game.animation_data.action = act
    for pb in game.pose.bones:
        pb.rotation_mode = "QUATERNION"
    n = len(frames_basis)
    names = list(frames_basis[0].keys())
    for name in names:
        prev = None
        locs, rots = [], []
        for fb in frames_basis:
            loc, q = fb[name]
            q = q.copy()
            if prev is not None and prev.dot(q) < 0:
                q.negate()
            prev = q
            locs.append(loc)
            rots.append(q)
        base = 'pose.bones["%s"].' % name
        for path, count, vals in (("location", 3, locs), ("rotation_quaternion", 4, rots)):
            for i in range(count):
                fc = act.fcurve_ensure_for_datablock(game, base + path, index=i, group_name=name)
                fc.keyframe_points.add(n)
                co = []
                for k in range(n):
                    co += [k + 1, vals[k][i]]
                fc.keyframe_points.foreach_set("co", co)
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.update()
    from forge.tools import rigforge_rig as rr
    rr.assign_action(game, act)
    return act




def sample_transfer(proxy, game, n_frames, fingers=None, adjust=None):
    """Per proxy frame 1..n: the game_engine bases that reproduce the proxy's ORG pose.
    fingers: optional list (per frame) of {game finger bone: local basis Quaternion}.
    adjust(frame, want): optional authored layer, edits the armature-space rotations in place."""
    scene = bpy.context.scene
    order = _hier(game)
    GB = game.data.bones
    PB = proxy.data.bones
    offs = {g: _rot3(PB[o].matrix_local).inverted() @ _rot3(GB[g].matrix_local) for g, o in GAME_FROM_ORG.items()}
    off5 = _rot3(PB[NECK[2]].matrix_local).inverted() @ _rot3(GB[NECK[0]].matrix_local)
    out = []
    for f in range(1, n_frames + 1):
        scene.frame_set(f)
        P = proxy.pose.bones
        want = {g: P[o].matrix.to_3x3().normalized() @ offs[g] for g, o in GAME_FROM_ORG.items()}
        base5 = P[NECK[2]].matrix.to_3x3().normalized() @ off5
        a, b = P[NECK[1]].head, P[NECK[3]].head
        ydir = base5 @ Vector((0, 1, 0))
        want[NECK[0]] = ydir.rotation_difference((b - a).normalized()).to_matrix() @ base5
        if adjust is not None:
            adjust(f, want)
        pelvis_head = P[GAME_FROM_ORG["pelvis"]].head.copy()
        aim_at = {g: getattr(P[o], end).copy() for g, (o, end) in SPINE_AIM.items()}
        pose, basis = {}, {}
        fb = fingers[f - 1] if fingers else {}
        for name in order:
            bone = GB[name]
            if bone.parent is not None:
                basem = pose[bone.parent.name] @ (bone.parent.matrix_local.inverted() @ bone.matrix_local)
            else:
                basem = bone.matrix_local.copy()
            if name in want:
                loc = pelvis_head if name == "pelvis" else basem.to_translation()
                R = want[name]
                if name in aim_at and (aim_at[name] - loc).length > 1e-6:
                    y = R @ Vector((0, 1, 0))
                    R = y.rotation_difference((aim_at[name] - loc).normalized()).to_matrix() @ R
                M = Matrix.Translation(loc) @ R.to_4x4()
            elif name in fb:
                M = basem @ fb[name].to_matrix().to_4x4()
            else:
                M = basem
            pose[name] = M
            B = basem.inverted() @ M
            basis[name] = (B.to_translation(), B.to_quaternion().normalized())
        out.append(basis)
    return out


LEG_BONES = {"pelvis"} | {"%s_%s" % (b, s) for s in ("l", "r") for b in ("thigh", "calf", "foot", "ball")}


def transfer_fidelity(proxy, game, n_frames, step=1, skip=()):
    """Game joint vs proxy ORG joint over the clip: legs + pelvis by POSITION (heads and tails, mm -
    what every plant/slide gate reads), the upper body by ORIENTATION (bone direction, deg - what
    the rotation transfer carries) with its positional offset quoted (the proxy spine's stretch)."""
    scene = bpy.context.scene
    worst = {"legs": (0.0, None, None), "upper": (0.0, None, None), "upper_deg": (0.0, None, None)}
    for f in range(1, n_frames + 1, step):
        scene.frame_set(f)
        P, G = proxy.pose.bones, game.pose.bones
        for g, o in GAME_FROM_ORG.items():
            if g in skip:
                continue
            part = "legs" if g in LEG_BONES else "upper"
            for end in ("head", "tail"):
                d = ((game.matrix_world @ getattr(G[g], end)) - (proxy.matrix_world @ getattr(P[o], end))).length * 1000
                if d > worst[part][0]:
                    worst[part] = (d, "%s.%s" % (g, end), f)
            if part == "upper":
                a = (G[g].tail - G[g].head).angle(P[o].tail - P[o].head, 0.0)
                if math.degrees(a) > worst["upper_deg"][0]:
                    worst["upper_deg"] = (math.degrees(a), g, f)
    out = {k: {"worst_mm": round(v[0], 3), "where": v[1], "frame": v[2]} for k, v in worst.items() if k != "upper_deg"}
    out["upper_deg"] = {"worst_deg": round(worst["upper_deg"][0], 4), "where": worst["upper_deg"][1],
                        "frame": worst["upper_deg"][2]}
    return out


# ------------------------------------------------------------------------------------------------
# Mixamo fingers (the retarget maps none: its slot table stops at the hand)
# ------------------------------------------------------------------------------------------------
FINGER_NAMES = {"Thumb": "thumb", "Index": "index", "Middle": "middle", "Ring": "ring", "Pinky": "pinky"}


def _hand_frame(heads, hand, index, middle, pinky):
    y = (heads[middle] - heads[hand]).normalized()
    t = heads[index] - heads[pinky]
    z = t.cross(y).normalized()
    x = y.cross(z).normalized()
    return Matrix((x, y, z)).transposed()


def mixamo_fingers(path, rt, n_out):
    """[{game finger bone: local basis Quaternion}] per output frame, timed as the retarget timed
    the body (its resample + loop window), conjugated through anatomical hand frames."""
    from forge.tools import rigforge_mocap as mocap
    scene = bpy.context.scene
    fps0 = (scene.render.fps, scene.render.fps_base)
    frame0 = scene.frame_current
    before = set(bpy.data.objects)
    acts0 = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=True, automatic_bone_orientation=True,
                             ignore_leaf_bones=True, global_scale=1.0)
    src_fps = scene.render.fps / scene.render.fps_base
    scene.render.fps, scene.render.fps_base = fps0
    new = [o for o in bpy.data.objects if o not in before]
    arm = [o for o in new if o.type == "ARMATURE"][0]
    game = bpy.data.objects[GAME_RIG]
    try:
        act = arm.animation_data.action
        first = int(act.frame_range[0])
        nsrc = int(act.frame_range[1]) - first + 1
        smw, gmw = arm.matrix_world, game.matrix_world
        pairs = []
        for side, s in (("Left", "l"), ("Right", "r")):
            for fb, fg in FINGER_NAMES.items():
                for k in (1, 2, 3):
                    src = "mixamorig:%sHand%s%d" % (side, fb, k)
                    if src in arm.pose.bones:
                        pairs.append((src, "%s_%02d_%s" % (fg, k, s), side, s))
        sh = {b.name: smw @ b.head_local for b in arm.data.bones}
        gh = {b.name: gmw @ b.head_local for b in game.data.bones}
        A = {}
        for side, s in (("Left", "l"), ("Right", "r")):
            p = "mixamorig:%sHand" % side
            Fs = _hand_frame(sh, p, p + "Index1", p + "Middle1", p + "Pinky1")
            Ft = _hand_frame(gh, "hand_" + s, "index_01_" + s, "middle_01_" + s, "pinky_01_" + s)
            A[side] = Ft @ Fs.transposed()
        conj = {}
        for src, dst, side, s in pairs:
            Rs = (smw @ arm.data.bones[src].matrix_local).to_3x3().normalized()
            Rt = (gmw @ game.data.bones[dst].matrix_local).to_3x3().normalized()
            conj[dst] = (Rt.inverted() @ A[side] @ Rs).to_quaternion()
        raw = {dst: [] for _s, dst, _a, _b in pairs}
        for i in range(nsrc):
            scene.frame_set(first + i)
            for src, dst, _a, _b in pairs:
                raw[dst].append(arm.pose.bones[src].matrix_basis.to_quaternion().normalized())
        raw = {k: mocap.same_hemisphere(v) for k, v in raw.items()}
        lw = rt.get("loop_window") or {}
        s0 = (lw.get("window_frames") or [1])[0] - 1
        fps_out = float(rt.get("fps") or FPS)
        tracks = {}
        for dst, qs in raw.items():
            c = conj[dst]
            seq = []
            for k in range(n_out):
                u = ((s0 + k) / fps_out) * src_fps
                i0 = max(0, min(int(math.floor(u + 1e-9)), nsrc - 2))
                q = mocap.slerp(qs[i0], qs[i0 + 1], min(1.0, max(0.0, u - i0)))
                seq.append((c @ q @ c.inverted()).normalized())
            tracks[dst] = {"rot": seq, "loc": None}
        if rt.get("loop"):
            mocap.close_seam(tracks)
        out = [dict() for _ in range(n_out)]
        for dst, t in tracks.items():
            for k in range(n_out):
                out[k][dst] = t["rot"][k]
        return out, {"bones": len(pairs), "source_fps": src_fps}
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        for a in [a for a in bpy.data.actions if a not in acts0]:
            bpy.data.actions.remove(a)
        scene.frame_set(frame0)


# ------------------------------------------------------------------------------------------------
# measurements on the game rig (world space; every clip faces -Y, up +Z)
# ------------------------------------------------------------------------------------------------
FWD, UP = Vector((0, -1, 0)), Vector((0, 0, 1))


def joint_series(game, n, step=1):
    scene = bpy.context.scene
    G = game.pose.bones
    mw = game.matrix_world
    rows = []
    for f in range(1, n + 1, step):
        scene.frame_set(f)
        r = {"f": f}
        for s in ("l", "r"):
            r["hip_" + s] = mw @ G["thigh_" + s].head
            r["knee_" + s] = mw @ G["calf_" + s].head
            r["ankle_" + s] = mw @ G["foot_" + s].head
            r["shoulder_" + s] = mw @ G["upperarm_" + s].head
            r["elbow_" + s] = mw @ G["lowerarm_" + s].head
            r["clav_" + s] = (mw @ G["clavicle_" + s].tail) - (mw @ G["clavicle_" + s].head)
        r["pelvis"] = mw @ G["pelvis"].head
        r["neck"] = mw @ G["neck_01"].head
        rows.append(r)
    return rows


def leg_readings(rows):
    """Knee flexion (deg), thigh swing in the sagittal plane (world, and at the hip joint = relative
    to the trunk line), and the hip joint's own fore-aft travel (the 'slide' the artist saw)."""
    out = {}
    for s in ("l", "r"):
        knee, swing, hipj, fore = [], [], [], []
        for r in rows:
            th = r["knee_" + s] - r["hip_" + s]
            sh = r["ankle_" + s] - r["knee_" + s]
            knee.append(math.degrees(th.angle(sh)))
            a = math.atan2(th.dot(FWD), -th.dot(UP))
            t = r["neck"] - r["pelvis"]
            pitch = math.atan2(t.dot(FWD), t.dot(UP))
            swing.append(math.degrees(a))
            hipj.append(math.degrees(a - pitch))
            fore.append(r["hip_" + s].dot(FWD) * 1000)
        out[s] = {"knee_flex_deg": {"min": round(min(knee), 1), "max": round(max(knee), 1),
                                    "mean": round(sum(knee) / len(knee), 1)},
                  "thigh_swing_deg": {"min": round(min(swing), 1), "max": round(max(swing), 1),
                                      "amplitude": round(max(swing) - min(swing), 1)},
                  "hip_joint_rotation_deg": {"min": round(min(hipj), 1), "max": round(max(hipj), 1),
                                             "amplitude": round(max(hipj) - min(hipj), 1)},
                  "hip_joint_fore_aft_travel_mm": round(max(fore) - min(fore), 1)}
    return out


def clavicle_elevation(rows, game):
    """Clavicle bone above horizontal (deg) through the clip, against its rest - the 'weird
    shoulder' reading: a shrug raises the shoulder joint by clavicle length x sin(delta)."""
    out = {}
    for s in ("l", "r"):
        b = game.data.bones["clavicle_" + s]
        v0 = b.tail_local - b.head_local
        rest = math.degrees(math.atan2(v0.z, math.hypot(v0.x, v0.y)))
        vals = [math.degrees(math.atan2(r["clav_" + s].z, math.hypot(r["clav_" + s].x, r["clav_" + s].y)))
                for r in rows]
        out[s] = {"rest": round(rest, 1), "min": round(min(vals), 1), "max": round(max(vals), 1),
                  "max_rise_above_rest": round(max(vals) - rest, 1)}
    return out


def arm_abduction(rows):
    """Upper arm away from the trunk in the frontal plane (deg): 0 = hanging along the trunk."""
    vals = {"l": [], "r": []}
    for r in rows:
        t = (r["neck"] - r["pelvis"]).normalized()
        left = t.cross(FWD).normalized()   # up x forward(-Y) points to +X, the character's left
        for s, sg in (("l", 1.0), ("r", -1.0)):
            u = r["elbow_" + s] - r["shoulder_" + s]
            vals[s].append(math.degrees(math.atan2(u.dot(left) * sg, -u.dot(t))))
    return {s: {"min": round(min(v), 1), "max": round(max(v), 1), "mean": round(sum(v) / len(v), 1)}
            for s, v in vals.items()}


def mesh_objects(game):
    return [o for o in bpy.data.objects if o.type == "MESH" and any(
        m.type == "ARMATURE" and m.object is game for m in o.modifiers)]


def eval_coords(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    pts = [obj.matrix_world @ v.co for v in me.vertices]
    ev.to_mesh_clear()
    return pts


def seam_mm(game, n):
    """Loop seam on the flesh: worst vertex distance frame 1 vs frame n over every bound mesh."""
    scene = bpy.context.scene
    worst = (0.0, None)
    scene.frame_set(1)
    objs = mesh_objects(game)
    a = {o.name: eval_coords(o) for o in objs}
    scene.frame_set(n)
    for o in objs:
        b = eval_coords(o)
        d = max((p - q).length for p, q in zip(a[o.name], b)) * 1000 if b else 0.0
        if d > worst[0]:
            worst = (d, o.name)
    return {"worst_mm": round(worst[0], 4), "mesh": worst[1]}


class FleshProbe:
    """Shoulder deformation + sleeve-in-motion readings on the real meshes.

    shoulder CAP: body skin (masks off) + jacket vertices whose dominant deform group is a clavicle
      or upper arm, above the armpit (rest z >= ARMPIT_TOP_Z) and facing up or outward (rest
      normal: z > 0.3, or pointing away from the midline) - the surface a viewer reads as the
      shoulder. Per frame: edge-length strain vs rest (%), and faces whose normal turned > 90 deg
      from the rest normal carried rigidly by the face's dominant bone (folded through). The
      ARMPIT crease below it (a linear-blend fold when an A-pose arm comes down to the side) is
      quoted separately.
    SLEEVE (the artist's open question: the tight v4 sleeves in motion): per side, the arm skin
      under the sleeve (upperarm past the armpit junction, t >= 0.15 along the bone, and forearm;
      masks off - this is the arm the sleeve has to contain) tested against THAT side's sleeve
      faces only (jacket faces dominated by that side's upper arm / forearm, rims excluded): a
      skin vertex in front of its nearest sleeve face by > 0.5 mm (build_custom.poke_check's rule)
      has poked through the sleeve. The tee's own sleeves (upper-arm-dominant tee vertices) are
      tested against the same faces. 'visible' = skin vertices the body's delete masks render.
      The first draft tested against the whole jacket and read the inner arm as "outside" the
      torso panel beside it (the nearest face at the armpit) - 100+ false hits, all hidden.
    """
    CLAV = ("clavicle_l", "clavicle_r")
    UPPER = ("upperarm_l", "upperarm_r")
    ARMPIT_TOP_Z = 1.46

    def __init__(self, game):
        import build_custom as bc
        self.bc = bc
        self.game = game
        self.body = bpy.data.objects[BODY]
        self.jacket = bpy.data.objects[JACKET]
        self.tee = bpy.data.objects[TEE]
        self.masks = [m for m in self.body.modifiers if m.type == "MASK"]
        bones = {b.name for b in game.data.bones}
        self.dom = {}
        for o in (self.body, self.jacket, self.tee):
            names = {g.index: g.name for g in o.vertex_groups}
            dom = []
            for v in o.data.vertices:
                best, bw = None, 0.0
                for g in v.groups:
                    n = names.get(g.group)
                    if n in bones and g.weight > bw:
                        best, bw = n, g.weight
                dom.append(best)
            self.dom[o.name] = dom
        self._masks(False)
        full = eval_coords(self.body)
        self._masks(True)
        shown = eval_coords(self.body)
        from mathutils.kdtree import KDTree
        kd = KDTree(len(shown))
        for i, p in enumerate(shown):
            kd.insert(p, i)
        kd.balance()
        self.hidden = {i for i, p in enumerate(full) if kd.find(p)[2] > 1e-6}
        GB = game.data.bones
        mw = game.matrix_world
        # skin under each sleeve
        self.skin = {}
        for s in ("l", "r"):
            ua = GB["upperarm_" + s]
            h, t = mw @ ua.head_local, mw @ ua.tail_local
            idx = []
            for i, d in enumerate(self.dom[BODY]):
                if d == "lowerarm_" + s:
                    idx.append(i)
                elif d == "upperarm_" + s:
                    p = full[i]
                    tt = (p - h).dot(t - h) / (t - h).length_squared
                    if tt >= 0.15:
                        idx.append(i)
            self.skin[s] = idx
        self.tee_idx = {s: [i for i, d in enumerate(self.dom[TEE]) if d == "upperarm_" + s] for s in ("l", "r")}
        self.tee_masks = [m for m in self.tee.modifiers if m.type == "MASK"]
        self.tee_hidden = set()
        if self.tee_masks:
            self._tee_masks(False)
            tfull = eval_coords(self.tee)
            self._tee_masks(True)
            tshown = eval_coords(self.tee)
            tk = KDTree(max(1, len(tshown)))
            for i, p in enumerate(tshown):
                tk.insert(p, i)
            tk.balance()
            self.tee_hidden = {i for i, p in enumerate(tfull) if not tshown or tk.find(p)[2] > 1e-6}
        jm = self.jacket.data
        kinds = None
        if "kind" in jm.attributes and len(jm.attributes["kind"].data) == len(jm.polygons):
            kinds = [a.value for a in jm.attributes["kind"].data]
        jd = self.dom[JACKET]
        self.sleeve_faces = {}
        for s in ("l", "r"):
            want = {"upperarm_" + s, "lowerarm_" + s, "hand_" + s}
            self.sleeve_faces[s] = [tuple(p.vertices) for p in jm.polygons
                                    if all(jd[v] in want for v in p.vertices)
                                    and (kinds is None or kinds[p.index] in (0, 1))]
        # shoulder cap and armpit regions, both meshes
        self.regions = {}
        for o in (self.body, self.jacket):
            self._masks(False)
            me = o.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
            nrm = [o.matrix_world.to_3x3() @ v.normal for v in me.vertices]
            pts = [o.matrix_world @ v.co for v in me.vertices]
            o.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
            self._masks(True)
            dom = self.dom[o.name]
            cap, pit = set(), set()
            for i, d in enumerate(dom):
                if d not in self.CLAV + self.UPPER:
                    continue
                p, n = pts[i], nrm[i]
                if p.z >= self.ARMPIT_TOP_Z and (n.z > 0.3 or n.x * (1 if p.x > 0 else -1) > 0.3):
                    cap.add(i)
                elif p.z < self.ARMPIT_TOP_Z and d in self.UPPER:
                    pit.add(i)
            reg = {}
            for name, idx in (("cap", cap), ("armpit", pit)):
                reg[name] = {"edges": [(e.vertices[0], e.vertices[1]) for e in o.data.edges
                                       if e.vertices[0] in idx and e.vertices[1] in idx],
                             "faces": [tuple(p.vertices) for p in o.data.polygons
                                       if all(v in idx for v in p.vertices)]}
            self.regions[o.name] = reg
        self._masks(False)
        self.rest = {o.name: eval_coords(o) for o in (self.body, self.jacket)}
        self._masks(True)
        self.rest_bone = {b.name: b.matrix_local.to_3x3().normalized() for b in game.data.bones}
        self.info = {"sleeve_skin_vertices": {s: len(v) for s, v in self.skin.items()},
                     "sleeve_skin_visible": {s: len([i for i in v if i not in self.hidden])
                                             for s, v in self.skin.items()},
                     "sleeve_faces": {s: len(v) for s, v in self.sleeve_faces.items()},
                     "tee_sleeve_vertices": {s: len(v) for s, v in self.tee_idx.items()},
                     "regions": {o: {r: {"edges": len(v["edges"]), "faces": len(v["faces"])}
                                     for r, v in reg.items()} for o, reg in self.regions.items()}}

    def _masks(self, on):
        for m in self.masks:
            m.show_viewport = on
        upd()

    @staticmethod
    def _normal(pts, f):
        a, b, c = pts[f[0]], pts[f[1]], pts[f[2]]
        return (b - a).cross(c - a)

    @staticmethod
    def _poke(points, tree, reach=0.03, tol=0.0005):
        bad, worst = 0, 0.0
        for p in points:
            loc, n, _i, _d = tree.find_nearest(p, reach)
            if loc is None:
                continue
            v = p - loc
            s = v.dot(n)
            if s > tol and v.length > 1e-9 and s / v.length > 0.82:
                bad += 1
                worst = max(worst, s)
        return bad, worst

    def frame(self):
        """Readings at the current frame (the rig must already be evaluated there)."""
        out = {}
        G = self.game.pose.bones
        self._masks(False)
        body_pts = None
        for o in (self.body, self.jacket):
            pts = eval_coords(o)
            rest = self.rest[o.name]
            dom = self.dom[o.name]
            for rname, reg in self.regions[o.name].items():
                strains = []
                for a, b in reg["edges"]:
                    l0 = (rest[a] - rest[b]).length
                    if l0 > 1e-7:
                        strains.append(abs((pts[a] - pts[b]).length / l0 - 1.0))
                flips = 0
                for f in reg["faces"]:
                    bone = dom[f[0]]
                    R = G[bone].matrix.to_3x3().normalized() @ self.rest_bone[bone].inverted()
                    if (R @ self._normal(rest, f)).dot(self._normal(pts, f)) < 0:
                        flips += 1
                strains.sort()
                key = "%s_%s" % ("shoulder_cap" if rname == "cap" else "armpit", "body" if o is self.body else "jacket")
                out[key] = {"strain_max_pct": round(100 * strains[-1], 2) if strains else 0.0,
                            "strain_p99_pct": round(100 * strains[int(0.99 * (len(strains) - 1))], 2) if strains else 0.0,
                            "flipped_faces": flips}
            if o is self.body:
                body_pts = pts
        self._masks(True)
        jpts = eval_coords(self.jacket)
        self._tee_masks(False)
        tee = eval_coords(self.tee)
        self._tee_masks(True)
        sk, skv, tt, ttv = [0, 0.0], [0, 0.0], [0, 0.0], [0, 0.0]
        for s in ("l", "r"):
            tree = BVHTree.FromPolygons(jpts, self.sleeve_faces[s])
            b, w = self._poke([body_pts[i] for i in self.skin[s]], tree)
            sk = [sk[0] + b, max(sk[1], w)]
            b, w = self._poke([body_pts[i] for i in self.skin[s] if i not in self.hidden], tree)
            skv = [skv[0] + b, max(skv[1], w)]
            b, w = self._poke([tee[i] for i in self.tee_idx[s]], tree)
            tt = [tt[0] + b, max(tt[1], w)]
            b, w = self._poke([tee[i] for i in self.tee_idx[s] if i not in self.tee_hidden], tree)
            ttv = [ttv[0] + b, max(ttv[1], w)]
        out["sleeve_skin"] = {"outside": sk[0], "worst_mm": round(sk[1] * 1000, 2)}
        out["sleeve_skin_visible"] = {"outside": skv[0], "worst_mm": round(skv[1] * 1000, 2)}
        out["tee_sleeve_outside_jacket"] = {"outside": tt[0], "worst_mm": round(tt[1] * 1000, 2)}
        out["tee_sleeve_outside_jacket_visible"] = {"outside": ttv[0], "worst_mm": round(ttv[1] * 1000, 2)}
        return out

    def _tee_masks(self, on):
        for m in self.tee_masks:
            m.show_viewport = on
        upd()


SLEEVE_TOUCHUP_CUFF_KEEP_M = 0.04


def sleeve_touchup(game):
    """The v4 sleeves in motion (the artist's open question), measured on the first full set:
    (1) the tee's own short sleeves poke out through the jacket sleeve's INNER face at the armpit
        (upper-arm t 0.12-0.30, inner side): 9-12 mm on walk/run, 24 mm on death-react. The tee
        sleeve lies wholly under the jacket sleeve (0 tee vertices outside it at rest), so it is
        hidden the way MPFB hides covered skin - a MASK on a vertex group of the tee's
        upper-arm-dominant vertices;
    (2) a few forearm skin vertices the body's jacket delete-group left RENDERED mid-sleeve (2 on
        the inner right forearm, t=0.46) poke 11.8 mm through it on run-loop: every arm-skin
        vertex under the sleeve and more than SLEEVE_TOUCHUP_CUFF_KEEP_M from the cuff opening
        joins a delete group (the ring near the cuff stays, as DELETE_MARGIN_RINGS keeps it, so
        no hole shows at the opening).
    Returns what was hidden."""
    body = bpy.data.objects[BODY]
    tee = bpy.data.objects[TEE]
    jacket = bpy.data.objects[JACKET]
    bones = {b.name for b in game.data.bones}

    def dominant(o):
        names = {g.index: g.name for g in o.vertex_groups}
        out = []
        for v in o.data.vertices:
            best, bw = None, 0.0
            for g in v.groups:
                n = names.get(g.group)
                if n in bones and g.weight > bw:
                    best, bw = n, g.weight
            out.append(best)
        return out

    rep = {}
    td = dominant(tee)
    idx = [i for i, d in enumerate(td) if d in ("upperarm_l", "upperarm_r")]
    g = tee.vertex_groups.get("forge_under_jacket_sleeve") or tee.vertex_groups.new(name="forge_under_jacket_sleeve")
    g.add(idx, 1.0, "REPLACE")
    m = tee.modifiers.get("forge_hide_under_sleeve") or tee.modifiers.new("forge_hide_under_sleeve", "MASK")
    m.vertex_group = g.name
    m.invert_vertex_group = True
    # keep the mask ahead of the armature (as MPFB's body masks sit after it, either order hides)
    rep["tee_sleeve_vertices_hidden"] = len(idx)
    # the cuff openings: jacket boundary vertices dominated by a forearm or hand
    jd = dominant(jacket)
    count = {}
    for e in jacket.data.edges:
        k = tuple(sorted(e.vertices))
        count[k] = count.get(k, 0)
    for p in jacket.data.polygons:
        for k in p.edge_keys:
            count[tuple(sorted(k))] = count.get(tuple(sorted(k)), 0) + 1
    cuff = {v for k, c in count.items() if c == 1 for v in k if jd[v] in ("lowerarm_l", "lowerarm_r", "hand_l", "hand_r")}
    jw = [jacket.matrix_world @ v.co for v in jacket.data.vertices]
    cuff_pts = [jw[i] for i in cuff]
    bd = dominant(body)
    masks = [mm for mm in body.modifiers if mm.type == "MASK"]
    for mm in masks:
        mm.show_viewport = False
    upd()
    full = eval_coords(body)
    for mm in masks:
        mm.show_viewport = True
    upd()
    shown = eval_coords(body)
    from mathutils.kdtree import KDTree
    kd = KDTree(len(shown))
    for i, p in enumerate(shown):
        kd.insert(p, i)
    kd.balance()
    kc = KDTree(len(cuff_pts))
    for i, p in enumerate(cuff_pts):
        kc.insert(p, i)
    kc.balance()
    extra = []
    for i, d in enumerate(bd):
        if d not in ("upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r"):
            continue
        if kd.find(full[i])[2] > 1e-6:
            continue            # already hidden
        if cuff_pts and kc.find(full[i])[2] > SLEEVE_TOUCHUP_CUFF_KEEP_M:
            extra.append(i)
    gb = body.vertex_groups.get("Delete.forge_sleeve_interior") or body.vertex_groups.new(name="Delete.forge_sleeve_interior")
    gb.add(extra, 1.0, "REPLACE")
    mb = body.modifiers.get("Delete.forge_sleeve_interior") or body.modifiers.new("Delete.forge_sleeve_interior", "MASK")
    mb.vertex_group = gb.name
    mb.invert_vertex_group = True
    upd()
    rep["cuff_boundary_vertices"] = len(cuff)
    rep["arm_skin_newly_hidden"] = len(extra)
    return rep


def flesh_over_clip(probe, n, step):
    scene = bpy.context.scene
    agg, at = {}, {}
    frames = list(range(1, n + 1, step))
    if frames[-1] != n:
        frames.append(n)
    for f in frames:
        scene.frame_set(f)
        for k, v in probe.frame().items():
            a = agg.setdefault(k, {})
            for kk, vv in v.items():
                if kk == "tested" or vv is None:
                    continue
                if vv > a.get(kk, -1):
                    a[kk] = vv
                    at.setdefault(k, {})[kk] = f
    for k in agg:
        agg[k]["at_frame"] = at.get(k, {})
    agg["frames_sampled"] = len(frames)
    return agg


# ------------------------------------------------------------------------------------------------
# the clip set
# ------------------------------------------------------------------------------------------------
# One-shots keep the take's own travel on the hips (heading "rest": a dodge back or a fall back
# must not be turned round to face its travel); the game drives the capsule, so each one-shot's
# travel is quoted for the delivery lane.
ONESHOT = {"mapping": "mixamo", "loop": False, "root_motion": "keep", "heading": "rest",
           "check_mode": "planted",
           # every one-shot opens standing in a fighting idle: the flat-foot calibration reads
           # its first half second (a fall's lowest ankles are lying down - see flat_window_s)
           "flat_window_s": [0.0, 0.5]}
# One-shot slide gate: the retarget must ADD no slide. A dodge, a stumble or a fall drags and
# pivots its feet in the take itself - animation_check on the source X Bot's toe bases reads
# dodging-back 595 mm, hit-body 180, death-backward 891, death-react 121, stagger-stunned 31
# (the standing strikes <= 1.8). And the gate's contact band is relative to each ball's own
# vertical range, so the same skid can split into runs differently on the two skeletons
# (stagger: the take's left toe skids 235 mm along the floor, z 21-80 mm, as the hips drop - the
# take's band reads 31 mm, the rig's 212). So the gate compares motion, not two readings: per
# low-ball run on the rig, its horizontal drift minus the take toe's drift (scaled, yaw-aligned,
# same frames) - added_slide - must stay <= 5 mm (the gate's ok band). Both gate readings quoted.
ONESHOT_SLIDE_ADDED_MM = 5.0


def arm_paths_vs_take(path, rt, game, n):
    """Worst angle (deg) between the rig's shoulder->hand line and the take's
    (scaled by the retarget), sampled on the retarget's clock: proves an arm reading (e.g. the
    sprint's gait_opposition) belongs to the take, not to the transfer."""
    scene = bpy.context.scene
    fps0 = (scene.render.fps, scene.render.fps_base)
    frame0 = scene.frame_current
    before = set(bpy.data.objects)
    acts0 = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=True, automatic_bone_orientation=True,
                             ignore_leaf_bones=True, global_scale=1.0)
    src_fps = scene.render.fps / scene.render.fps_base
    scene.render.fps, scene.render.fps_base = fps0
    new = [o for o in bpy.data.objects if o not in before]
    try:
        arm = [o for o in new if o.type == "ARMATURE"][0]
        first = int(arm.animation_data.action.frame_range[0])
        s0 = ((rt.get("loop_window") or {}).get("window_frames") or [1])[0] - 1
        scale = float(rt.get("scale") or 1.0)
        nsrc = int(arm.animation_data.action.frame_range[1]) - first + 1
        P = arm.pose.bones
        raw = []
        for i in range(nsrc):
            scene.frame_set(first + i)
            raw.append({s: ((arm.matrix_world @ P["mixamorig:%sHand" % side].head)
                            - (arm.matrix_world @ P["mixamorig:%sArm" % side].head)) * scale
                        for side, s in (("Left", "l"), ("Right", "r"))})
        src = []
        for k in range(n):
            u = ((s0 + k) / float(FPS)) * src_fps
            i0 = max(0, min(int(math.floor(u + 1e-9)), nsrc - 2))
            t = min(1.0, max(0.0, u - i0))
            src.append({s: raw[i0][s].lerp(raw[i0 + 1][s], t) for s in ("l", "r")})
        worst = 0.0
        G = game.pose.bones
        for k in range(n):
            scene.frame_set(k + 1)
            for s in ("l", "r"):
                d = game.matrix_world.to_3x3() @ (G["hand_" + s].head - G["upperarm_" + s].head)
                worst = max(worst, math.degrees(d.angle(src[k][s], 0.0)))
        return round(worst, 2)
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        for a in [a for a in bpy.data.actions if a not in acts0]:
            bpy.data.actions.remove(a)
        scene.frame_set(frame0)


def added_slide(path, rt, game, n, band_mm=30.0):
    """Slide the retarget ADDED over the take, per plant: wherever the rig's ball sits within
    band_mm of its lowest point on consecutive frames, its horizontal drift over that run minus
    the take's own toe drift (scaled, yaw-aligned, on the retarget's clock) over the same frames.
    Positive = the rig skids more than the take did; a lock that removed a slip of the take reads
    negative. Worst run per foot, mm."""
    scene = bpy.context.scene
    fps0 = (scene.render.fps, scene.render.fps_base)
    frame0 = scene.frame_current
    before = set(bpy.data.objects)
    acts0 = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=True, automatic_bone_orientation=True,
                             ignore_leaf_bones=True, global_scale=1.0)
    src_fps = scene.render.fps / scene.render.fps_base
    scene.render.fps, scene.render.fps_base = fps0
    new = [o for o in bpy.data.objects if o not in before]
    try:
        arm = [o for o in new if o.type == "ARMATURE"][0]
        mw = arm.matrix_world
        B = arm.data.bones
        first = int(arm.animation_data.action.frame_range[0])
        nsrc = int(arm.animation_data.action.frame_range[1]) - first + 1
        s0 = ((rt.get("loop_window") or {}).get("window_frames") or [1])[0] - 1
        scale = float(rt.get("scale") or 1.0)
        # yaw between the two bodies' hip lines at rest
        sl = (mw @ B["mixamorig:LeftUpLeg"].head_local) - (mw @ B["mixamorig:RightUpLeg"].head_local)
        tl = game.data.bones["thigh_l"].head_local - game.data.bones["thigh_r"].head_local
        yaw = math.atan2(tl.y, tl.x) - math.atan2(sl.y, sl.x)
        Rz = Matrix.Rotation(yaw, 3, "Z")
        P = arm.pose.bones
        raw = []
        for i in range(nsrc):
            scene.frame_set(first + i)
            raw.append({s: Rz @ (mw @ P["mixamorig:%sToeBase" % side].head) * scale
                        for side, s in (("Left", "l"), ("Right", "r"))})
        take = []
        for k in range(n):
            u = ((s0 + k) / float(FPS)) * src_fps
            i0 = max(0, min(int(math.floor(u + 1e-9)), nsrc - 2))
            t = min(1.0, max(0.0, u - i0))
            take.append({s: raw[i0][s].lerp(raw[i0 + 1][s], t) for s in ("l", "r")})
        G = game.pose.bones
        rig = []
        for k in range(n):
            scene.frame_set(k + 1)
            rig.append({s: game.matrix_world @ G["ball_" + s].head for s in ("l", "r")})
        out = {}
        for s in ("l", "r"):
            lo = min(r[s].z for r in rig)
            low = [r[s].z - lo <= band_mm / 1000.0 for r in rig]
            runs, cur = [], []
            for k in range(n):
                if low[k]:
                    cur.append(k)
                elif cur:
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)
            worst, where = 0.0, None
            for run_ in runs:
                if len(run_) < 2:
                    continue
                a, b = run_[0], run_[-1]
                dr = rig[b][s] - rig[a][s]
                dt = take[b][s] - take[a][s]
                added = (Vector((dr.x, dr.y, 0)).length - Vector((dt.x, dt.y, 0)).length) * 1000
                if added > worst:
                    worst, where = added, [a + 1, b + 1]
            out[s] = {"worst_added_mm": round(worst, 2), "frames": where}
        out["worst_added_mm"] = max(out["l"]["worst_added_mm"], out["r"]["worst_added_mm"])
        return out
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        for a in [a for a in bpy.data.actions if a not in acts0]:
            bpy.data.actions.remove(a)
        scene.frame_set(frame0)


def source_slide(path):
    """animation_check's slide reading on the source skeleton itself (Mixamo toe bases)."""
    scene = bpy.context.scene
    fps0 = (scene.render.fps, scene.render.fps_base)
    frame0 = scene.frame_current
    before = set(bpy.data.objects)
    acts0 = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=True, automatic_bone_orientation=True,
                             ignore_leaf_bones=True, global_scale=1.0)
    scene.render.fps, scene.render.fps_base = fps0
    new = [o for o in bpy.data.objects if o not in before]
    try:
        arm = [o for o in new if o.type == "ARMATURE"][0]
        r = run("animation_check", {"rig": arm.name, "mode": "planted",
                                    "feet": ["mixamorig:LeftToeBase:head", "mixamorig:RightToeBase:head"]})
        return {"gate": r.get("gate"), "worst_drift_mm": r.get("worst_drift_mm")}
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        for a in [a for a in bpy.data.actions if a not in acts0]:
            bpy.data.actions.remove(a)
        scene.frame_set(frame0)
CLIPS = [
    # action,            role,          source (MOCAP_DIR / MIXAMO_DIR),          retarget params,  extras
    # authored layers (knobs at the top): walk arm carriage, run soft fist, crouch stance width.
    # sprint-loop keeps its Mixamo take's own fingers - already curled (see the report's
    # sprint finger reading), so no soft-fist override.
    ("walk-loop", "walk", "cmu_08_01.bvh", {"mapping": "cmu", "motion_quality": True},
     {"gait": True, "arm_carriage": True, "soft_fist": WALK_SOFT_FIST_SCALE}),
    ("run-loop", "run", "cmu_16_46.bvh", {"mapping": "cmu", "motion_quality": True},
     {"gait": True, "soft_fist": True}),
    ("sprint-loop", "sprint", "mixamo:sprint-forward.fbx",
     {"mapping": "mixamo", "motion_quality": True, "loop_whole_take": True},
     # a 7 m/s stance lasts ~0.1 s = 2.4 frames at 24 fps: the plant lock's 3-sample minimum
     # missed the right foot's plants (1 of 3 locked); 2 samples for this clip only. Its slide
     # gate is the belt truth: every locked frame moves exactly the natural belt (measured 0.0
     # mm/frame), while animation_check pools touch-down/toe-off samples into its belt (7.50 vs
     # 7.76 m/s) and reads that 3.3% as 29-33 mm of "drift" per 3-frame plant
     # its arms swing across the body nearly in phase IN THE TAKE (hand paths on the rig match
     # the X Bot to the centimetre): gait_opposition reads the take; proved per build
     {"gait": True, "lock_min_samples": 2, "belt_gate": True, "arms_from_take": True}),
    ("crouch_walk-loop", "crouch_walk", "s100_Crouched_FW.bvh",
     {"mapping": "100style", "motion_quality": True}, {"gait": True, "crouch": True, "stance_widen": True}),
    ("crouch_idle-loop", "crouch_idle", "s100_Crouched_ID.bvh",
     {"mapping": "100style", "loop_min_s": 2.0, "loop_max_s": 5.0, "check_mode": "planted"},
     {"crouch": True, "stance_widen": True}),
    ("attack_3", "attack_3", "mixamo:punch-combo.fbx", ONESHOT, {"combat": True}),
    ("heavy", "heavy", "mixamo:hook-punch-heavy.fbx", ONESHOT, {"combat": True}),
    ("counter", "counter", "mixamo:center-block-counter.fbx", ONESHOT, {"combat": True}),
    ("dodge", "dodge", "mixamo:dodging-back.fbx", ONESHOT, {"combat": True}),
    ("hit", "hit", "mixamo:hit-head.fbx", ONESHOT, {"combat": True}),
    ("hit_alt", "hit (alt)", "mixamo:hit-body.fbx", ONESHOT, {"combat": True}),
    ("stagger", "stagger", "mixamo:stagger-stunned.fbx", ONESHOT, {"combat": True}),
    ("death", "death", "mixamo:death-backward.fbx", ONESHOT, {"combat": True}),
    # death-react lies still on its back for its last second: the plant lock pinned the resting feet
    # while the body settled, and the reach solve lowered the hips up to 208 mm to meet them (the
    # pelvis joint 9 mm UNDER the floor at frame 74). Unlocked, the hips drop 68 mm at most and the
    # added slide over the take is 0.01 mm.
    ("death_alt", "death (alt)", "mixamo:death-react.fbx", dict(ONESHOT, foot_lock=False), {"combat": True}),
    # a free-climb cycle: the take IS one cycle rising 1.26 m; in place, the seam closure spreads
    # the rise out of the hips; FK legs (the feet are on a wall, not a floor to lock to)
    ("climb-loop", "climb", "mixamo:climbing-up-wall.fbx",
     {"mapping": "mixamo", "loop_whole_take": True, "legs": "fk", "foot_lock": False,
      "reach_limit": False, "heading": "rest", "root_motion": "in_place", "check_mode": "planted"},
     {"no_floor": True}),
    # talking.fbx is SEATED (hips 0.575 m, knees 93.6 deg, legs still for all 44 s): its upper
    # body is layered on the standing rest (see standing_upper_body)
    ("talk-loop", "talk", "mixamo:talking.fbx",
     {"mapping": "mixamo", "loop_min_s": 6.0, "loop_max_s": 10.0, "legs": "fk", "foot_lock": False,
      "reach_limit": False, "heading": "rest", "check_mode": "planted"},
     {"standing_upper": True, "no_floor": True}),
]

# Speed bands, in-game m/s (werewolf characters/character_model.gd): walk [WALK_MIN 0.25, RUN_MIN
# 3.0), run [3.0, SPRINT_MIN 6.5), sprint >= 6.5; crouch_walk plays at any speed >= WALK_MIN.
SPEED_BANDS = {"walk-loop": (0.25, 3.0), "run-loop": (3.0, 6.5), "sprint-loop": (6.5, 99.0),
               "crouch_walk-loop": (0.25, 3.0)}
SPEED_AGREEMENT = 0.03
SEAM_TOL_MM = 1.0
# Transfer fidelity: the legs + pelvis (what every plant/slide gate reads) must land on the proxy's
# joints to 1 mm; the upper body must point where the proxy's segments point to 0.5 deg. Its
# joint positions differ from the proxy's by the Rigify spine's own segment stretch (quoted, not
# gated - see SPINE_AIM). A real transfer fault reads hundreds of mm / tens of deg (measured on the
# first draft's action-name clash: 714 mm).
FIDELITY_TOL = {"legs": ("worst_mm", 1.0), "upper_deg": ("worst_deg", 0.5)}

# The authored-layer knobs (ARM_FLARE_DEG, CROUCH_KNEE_DEG, CROUCH_STANCE_WIDEN_MM,
# WALK_ARM_CARRIAGE_DEG, SOFT_FIST_CURL_DEG) live in the ARTIST KNOBS block at the top.
# Belt-truth slide (gaits): a planted ball of an in-place clip must travel backward at exactly the
# speed the game moves the capsule (the take's natural speed, the manifest's). Plant = the ball
# within BELT_BAND_MM of its lowest point on two consecutive frames; per plant, the summed
# difference from the belt. Same 5 mm tolerance as animation_check's slide gate.
BELT_BAND_MM = 3.0
BELT_TOL_MM = 5.0


def belt_slide(game, n, speed_mps, loop=True):
    scene = bpy.context.scene
    G = game.pose.bones
    belt = -FWD * (speed_mps / FPS)
    count = n - 1 if loop else n
    out = {}
    for s in ("l", "r"):
        pts = []
        for f in range(1, count + 1):
            scene.frame_set(f)
            pts.append(game.matrix_world @ G["ball_" + s].head)
        lo = min(p.z for p in pts)
        down = [p.z - lo <= BELT_BAND_MM / 1000.0 for p in pts]
        steps = []
        for k in range(count if loop else count - 1):
            k1 = (k + 1) % count
            if down[k] and down[k1]:
                steps.append((k, (pts[k1] - pts[k]) - belt))
        # group consecutive steps into plants (cyclic)
        plants, cur, prev = [], [], None
        for k, d in steps:
            if prev is not None and k != (prev + 1) % count:
                plants.append(cur)
                cur = []
            cur.append(d)
            prev = k
        if cur:
            if plants and steps and steps[0][0] == 0 and (steps[-1][0] + 1) % count == 0 and loop:
                plants[0] = cur + plants[0]
            else:
                plants.append(cur)
        worst = max([sum(p, Vector()).length for p in plants] or [0.0]) * 1000
        worst_step = max([d.length for _k, d in steps] or [0.0]) * 1000
        out[s] = {"plants": len(plants), "planted_steps": len(steps),
                  "worst_plant_drift_mm": round(worst, 3), "worst_step_mm": round(worst_step, 3)}
    out["worst_mm"] = max(out["l"]["worst_plant_drift_mm"], out["r"]["worst_plant_drift_mm"])
    out["belt_mps"] = speed_mps
    return out


def mean_min_knee(rows):
    vals = []
    for r in rows:
        k = []
        for s in ("l", "r"):
            th = r["knee_" + s] - r["hip_" + s]
            sh = r["ankle_" + s] - r["knee_" + s]
            k.append(math.degrees(th.angle(sh)))
        vals.append(min(k))
    return sum(vals) / len(vals)


def shift_bone_keys(proxy, action, bone, world_offset):
    """Offset a proxy control's location keys by world_offset (m) on every key of the clip (its
    parent chain is the unposed root, so the bone's rest frame maps world to its location channel).
    Returns how many location curves were keyed (0 = the clip does not key this bone)."""
    from forge.tools import rigforge_rig as rr
    R = proxy.data.bones[bone].matrix_local.to_3x3().normalized()
    local = R.inverted() @ (proxy.matrix_world.to_3x3().inverted() @ world_offset)
    path = 'pose.bones["%s"].location' % bone
    done = 0
    for fc in rr.action_fcurves(action):
        if fc.data_path == path:
            for kp in fc.keyframe_points:
                kp.co.y += local[fc.array_index]
                kp.handle_left.y += local[fc.array_index]
                kp.handle_right.y += local[fc.array_index]
            fc.update()
            done += 1
    return done


def lower_torso(proxy, action, drop_m):
    """Offset the proxy's torso control down by drop_m (world) on every key of the clip."""
    done = shift_bone_keys(proxy, action, "torso", Vector((0, 0, -drop_m)))
    if done != 3:
        raise RuntimeError("torso location curves: %d of 3" % done)


LEFT = Vector((1, 0, 0))    # the character's left: up x forward(-Y) = +X (every clip faces -Y)


def widen_stance(proxy, action, per_foot_mm):
    """CROUCH_STANCE_WIDEN_MM: each foot's IK target (and its knee pole, when keyed) moved
    per_foot_mm outward on every key - a constant offset, so every plant stays exactly as still
    as it was. The hips are not touched."""
    moved = {}
    for S, sg in (("L", 1.0), ("R", -1.0)):
        off = LEFT * (sg * per_foot_mm / 1000.0)
        for bone in ("foot_ik." + S, "thigh_ik_target." + S):
            if bone in proxy.pose.bones:
                n = shift_bone_keys(proxy, action, bone, off)
                if n:
                    moved[bone] = n
        if "foot_ik." + S not in moved:
            raise RuntimeError("stance widen: foot_ik.%s carries no location keys" % S)
    return sorted(moved)


def knees_over_toes(proxy, frac):
    """CROUCH_KNEE_OVER_TOES as a transfer layer: per frame and leg, thigh and calf turned together
    about the hip->ankle line (read off the proxy's ORG joints) by frac of the angle that puts the
    knee in the plane of that line and the foot's ankle->toe direction. A turn about the hip-ankle
    line leaves the hip, the ankle, the foot and the knee angle exactly where they were."""
    P = proxy.pose.bones
    turned = {"l": [], "r": []}

    def adjust(_f, want):
        for S, s in SIDES:
            hip, knee, ankle = P["ORG-thigh." + S].head, P["ORG-shin." + S].head, P["ORG-foot." + S].head
            toe = P["ORG-toe." + S].tail
            a = (ankle - hip).normalized()
            k = (knee - hip) - a * (knee - hip).dot(a)
            d = (toe - ankle) - a * (toe - ankle).dot(a)
            if k.length < 1e-6 or d.length < 1e-6:
                continue
            ang = k.angle(d, 0.0) * (1.0 if k.cross(d).dot(a) >= 0 else -1.0)
            R = Quaternion(a, ang * frac).to_matrix()
            turned[s].append(math.degrees(ang * frac))
            for b in ("thigh_", "calf_"):
                want[b + s] = R @ want[b + s]
    adjust.turned = turned
    return adjust


def chain_adjust(*fns):
    fns = [f for f in fns if f is not None]
    if not fns:
        return None

    def adjust(f, want):
        for fn in fns:
            fn(f, want)
    return adjust


def knee_out_of_line(rows):
    """Knee offset from the hip->ankle line toward the character's outside, mm (negative = the knee
    caves in toward the midline)."""
    v = []
    for r in rows:
        for s, sg in (("l", 1.0), ("r", -1.0)):
            h, k, a = r["hip_" + s], r["knee_" + s], r["ankle_" + s]
            ax = (a - h).normalized()
            v.append((k - (h + ax * (k - h).dot(ax))).dot(LEFT) * sg * 1000)
    return {"mean": round(sum(v) / len(v), 1), "min": round(min(v), 1), "max": round(max(v), 1)}


def stance_width(rows):
    """Lateral (character left-right) separation of the ankles and of the knees, mm, over the clip."""
    out = {}
    for j in ("ankle", "knee"):
        v = [abs((r[j + "_l"] - r[j + "_r"]).dot(LEFT)) * 1000 for r in rows]
        out[j + "_mm"] = {"mean": round(sum(v) / len(v), 1), "min": round(min(v), 1), "max": round(max(v), 1)}
    out["knee_out_of_line_mm"] = knee_out_of_line(rows)
    return out


FINGER_KEYS = ("index", "middle", "ring", "pinky", "thumb")


def soft_fist_pose(scale=1.0):
    """{game finger bone: local basis Quaternion} - SOFT_FIST_CURL_DEG (times scale) about each
    bone's local +X. scale < 1 gives a more at-rest hand (the walk uses WALK_SOFT_FIST_SCALE)."""
    out = {}
    for s in ("l", "r"):
        for f in FINGER_KEYS:
            for k, deg in enumerate(SOFT_FIST_CURL_DEG[f], 1):
                out["%s_%02d_%s" % (f, k, s)] = Quaternion(Vector((1, 0, 0)),
                                                           math.radians(deg * scale))
    return out


def finger_readings(game, n):
    """Read back off the written action: per finger bone, the curl (deg, rest-relative) and its
    swing over the clip (max - min); plus the fingertip-to-thumb-tip clearance the soft fist
    leaves (thumb_03 tail to the nearest point of the index middle/tip bones, mm)."""
    scene = bpy.context.scene
    G = game.pose.bones
    mw = game.matrix_world
    vals = {}
    clear = {"l": [], "r": []}

    def seg_dist(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-12, ab.length_squared)))
        return (p - (a + ab * t)).length
    for f in range(1, n + 1):
        scene.frame_set(f)
        for s in ("l", "r"):
            for fk in FINGER_KEYS:
                for k in (1, 2, 3):
                    name = "%s_%02d_%s" % (fk, k, s)
                    q = G[name].matrix_basis.to_quaternion()
                    vals.setdefault(name, []).append(math.degrees(2 * math.acos(min(1.0, abs(q.w)))))
            tip = mw @ G["thumb_03_" + s].tail
            clear[s].append(min(seg_dist(tip, mw @ G["index_%02d_%s" % (k, s)].head, mw @ G["index_%02d_%s" % (k, s)].tail)
                                for k in (2, 3)) * 1000)
    out = {}
    for s in ("l", "r"):
        out[s] = {fk: [round(sum(vals["%s_%02d_%s" % (fk, k, s)]) / n, 1) for k in (1, 2, 3)] for fk in FINGER_KEYS}
        out[s]["thumb_tip_to_index_mm"] = round(min(clear[s]), 1)
    out["swing_over_clip_deg"] = round(max(max(v) - min(v) for v in vals.values()), 3)
    return out


def deepen_crouch(proxy, action, n, target_deg):
    """Lower the hips until mean(min knee flexion) reaches target_deg; returns the report."""
    from forge.tools import rigforge_rig as rr
    L1 = proxy.data.bones["ORG-thigh.L"].length
    L2 = proxy.data.bones["ORG-shin.L"].length

    def span(k):
        return math.sqrt(L1 * L1 + L2 * L2 + 2 * L1 * L2 * math.cos(math.radians(k)))
    rr.assign_action(proxy, action)
    before = mean_min_knee(proxy_rows(proxy, n))
    total, now = 0.0, before
    for _ in range(4):
        if abs(now - target_deg) < 1.0:
            break
        drop = span(now) - span(target_deg)
        lower_torso(proxy, action, drop)
        total += drop
        rr.assign_action(proxy, action)
        now = mean_min_knee(proxy_rows(proxy, n))
    return {"knee_flex_before_deg": round(before, 1), "knee_flex_after_deg": round(now, 1),
            "target_deg": target_deg, "hips_lowered_mm": round(total * 1000, 1)}


def src_path(src):
    if src.startswith("mixamo:"):
        return os.path.join(MIXAMO_DIR, src.split(":", 1)[1])
    return os.path.join(MOCAP_DIR, src)


def run(name, params):
    from forge.tools.registry import dispatch
    status, result, message = dispatch(name, params)
    if status != "success":
        raise RuntimeError("%s failed: %s" % (name, message))
    return result


def reset_pose(rig):
    from forge.tools import rigforge_rig as rr
    rr.assign_action(rig, None)
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    upd()


def flare_arms(deg):
    rl = Quaternion(FWD, math.radians(deg)).to_matrix()     # +deg about -Y moves the left arm to +X
    rr_ = Quaternion(FWD, -math.radians(deg)).to_matrix()

    def adjust(_f, want):
        for s, R in (("l", rl), ("r", rr_)):
            for b in ("upperarm_", "lowerarm_", "hand_"):
                want[b + s] = R @ want[b + s]
    return adjust


LOWER = ["Root", "pelvis"] + ["%s_%s" % (b, s) for s in ("l", "r") for b in ("thigh", "calf", "foot", "ball")]


def standing_upper_body(game, bases):
    """Seated take -> standing talk: pelvis and legs at the MPFB standing rest, the take's upper body
    kept as LOCAL rotations from spine_01 up, then one constant turn on spine_01 so the mean trunk
    line (pelvis -> neck) stands where it stands at rest (a seated trunk leans back off the pelvis).
    Returns the correction applied (deg)."""
    ident = (Vector(), Quaternion())
    for fb in bases:
        for b in LOWER:
            if b in fb:
                fb[b] = ident
    GB = game.data.bones
    rest_sp = GB["spine_01"].matrix_local
    chain = ["spine_01", "spine_02", "spine_03", "neck_01"]
    rest_dir = (GB["neck_01"].head_local - GB["pelvis"].head_local).normalized()
    acc = Vector()
    for fb in bases:
        M = GB["pelvis"].matrix_local.copy()
        prev = "pelvis"
        for b in chain:
            rel = GB[prev].matrix_local.inverted() @ GB[b].matrix_local
            M = M @ rel @ (Matrix.Translation(fb[b][0]) @ fb[b][1].to_matrix().to_4x4())
            prev = b
        acc += (M.to_translation() - GB["pelvis"].head_local).normalized()
    mean_dir = (acc / len(bases)).normalized()
    R = mean_dir.rotation_difference(rest_dir)
    C = (rest_sp.inverted() @ R.to_matrix().to_4x4() @ rest_sp).to_quaternion()
    for fb in bases:
        loc, q = fb["spine_01"]
        fb["spine_01"] = (loc, (C @ q).normalized())
    return round(math.degrees(R.angle), 2)


def proxy_rows(proxy, n):
    """joint_series off the proxy's ORG joints (the take before any authored layer)."""
    scene = bpy.context.scene
    P = proxy.pose.bones
    mw = proxy.matrix_world
    rows = []
    for f in range(1, n + 1):
        scene.frame_set(f)
        r = {}
        for S, s in SIDES:
            r["hip_" + s] = mw @ P["ORG-thigh." + S].head
            r["knee_" + s] = mw @ P["ORG-shin." + S].head
            r["ankle_" + s] = mw @ P["ORG-foot." + S].head
            r["shoulder_" + s] = mw @ P["ORG-upper_arm." + S].head
            r["elbow_" + s] = mw @ P["ORG-forearm." + S].head
        r["pelvis"] = mw @ P["ORG-spine"].head
        r["neck"] = mw @ P["ORG-spine.004"].head
        rows.append(r)
    return rows


def check_block(r):
    """The animation_check readings worth quoting, compacted."""
    seam = r.get("loop_seam_closure") or {}
    out = {"gate": r.get("gate"), "worst_drift_mm": r.get("worst_drift_mm"),
           "deformation_gate": r.get("deformation_gate"),
           "motion_quality_gate": r.get("motion_quality_gate"),
           "treadmill_mps": round((r.get("treadmill_mm_per_frame") or 0.0) * FPS / 1000.0, 4),
           "stretch_worst_pct": max([l["worst_stretch_pct"] for l in (r.get("bone_stretch_budget") or {}).get("limbs", [])] or [0.0]),
           "stretch_verdict": (r.get("bone_stretch_budget") or {}).get("verdict"),
           "ik_reach": (r.get("ik_reach_headroom") or {}).get("worst_extension_frac"),
           "ik_reach_verdict": (r.get("ik_reach_headroom") or {}).get("verdict"),
           "gait_opposition": (r.get("gait_opposition") or {}).get("verdict"),
           "strike_lead": (r.get("strike_lead") or {}).get("verdict")}
    mq = r.get("motion_quality")
    if mq:
        out["motion_quality"] = {c["metric"]: [c["value"], c["min"], c["verdict"]] for c in mq["checks"]}
    return out


def run_clip(spec, proxy, game, probe, flesh_step):
    from forge.tools import rigforge_rig as rr
    from forge.tools import rigforge_mocap as mocap
    action, role, src, params, extra = spec
    path = src_path(src)
    t0 = time.monotonic()
    rep = {"role": role, "source": src, "source_sha256_16": hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]}
    reset_pose(proxy)
    reset_pose(game)
    loop = action.endswith("-loop")
    base = action[:-5] if loop else action
    from forge.tools import rigforge_anim as ra
    lock_min = ra.LOCK_MIN_SAMPLES
    if extra.get("lock_min_samples"):
        ra.LOCK_MIN_SAMPLES = extra["lock_min_samples"]
        rep["lock_min_samples"] = extra["lock_min_samples"]
    try:
        res = run("rigforge_mocap_clip", dict(params, target_rig=proxy.name, clip=base, source_path=path, loop=loop))
    finally:
        ra.LOCK_MIN_SAMPLES = lock_min
    rt, c = res["retarget"], res["contract"]
    px = bpy.data.actions[c["action"]]
    px.name = "PX_" + action
    n = c["frame_range"][1]
    rep["retarget"] = {k: rt.get(k) for k in ("scale", "mapping", "mapped_count", "unmapped", "loop_window",
                                               "root_motion", "heading", "hip_lowering", "resampled",
                                               "foot_flat_calibration", "warnings")}
    rep["retarget"]["seam_residual"] = {k: (rt.get("seam_residual") or {}).get(k)
                                        for k in ("worst_deg", "worst_bone", "worst_mm")}
    rep["retarget"]["transfer_check"] = {k: rt["transfer_check"].get(k) for k in (
        "worst_rotation_deg", "worst_rotation_bone", "worst_position_mm")}
    rep["frames"] = n
    rep["seconds_long"] = round((n - 1) / float(FPS), 3)
    if extra.get("stance_widen"):
        # before the depth layer, so CROUCH_KNEE_DEG is still what the crouch reaches
        rr.assign_action(proxy, px)
        before = stance_width(proxy_rows(proxy, n))
        moved = widen_stance(proxy, px, CROUCH_STANCE_WIDEN_MM)
        rr.assign_action(proxy, px)
        rep["stance_layer"] = {"per_foot_mm": CROUCH_STANCE_WIDEN_MM, "bones_moved": moved,
                               "take": before, "after_on_proxy": stance_width(proxy_rows(proxy, n))}
    if extra.get("crouch"):
        rep["crouch_depth_layer"] = deepen_crouch(proxy, px, n, CROUCH_KNEE_DEG)
    # proxy-side gates (the retarget's own animation_check ran before the rename and any authored
    # layer; run again on the final proxy action, so the numbers are those of what is transferred)
    rr.assign_action(proxy, px)
    chk = run("animation_check", {"rig": proxy.name, "action": px.name, "mode": params.get("check_mode") or (
        "in_place" if params.get("root_motion", "in_place") == "in_place" else "planted"),
        "motion_quality": bool(params.get("motion_quality"))})
    rep["check"] = check_block(chk)
    st = mocap.motion_statistics(proxy, px)
    rep["motion_stats"] = {"trunk_to_leg_speed_ratio": st["trunk_to_leg_speed_ratio"],
                           "trunk_pitch_std_deg": st["posture"]["trunk_pitch_std_deg"],
                           "head_pitch_std_deg": st["posture"]["head_pitch_std_deg"],
                           "step_interval_mean_s": st["footfall"]["step_interval_mean_s"]}
    rr.assign_action(proxy, px)
    fingers = None
    if src.startswith("mixamo:"):
        fingers, finfo = mixamo_fingers(path, dict(rt, loop=loop), n)
        rep["fingers"] = finfo
        rr.assign_action(proxy, px)
    if extra.get("soft_fist"):
        if fingers:
            raise RuntimeError("soft_fist on a take that carries its own fingers (%s)" % action)
        fist_scale = 1.0 if extra["soft_fist"] is True else float(extra["soft_fist"])
        pose = soft_fist_pose(fist_scale)
        fingers = [pose] * n
        rep["soft_fist_layer_deg"] = {f: tuple(round(d * fist_scale, 1) for d in degs)
                                      for f, degs in SOFT_FIST_CURL_DEG.items()}
        rep["soft_fist_scale"] = fist_scale
    adjust, skip = None, ()
    flare = ARM_FLARE_DEG if extra.get("crouch") else (WALK_ARM_CARRIAGE_DEG if extra.get("arm_carriage") else None)
    if flare is not None:
        rep["arm_abduction_take_deg"] = arm_abduction(proxy_rows(proxy, n))
        adjust = flare_arms(flare)
        skip = tuple("%s_%s" % (b, s) for s in ("l", "r") for b in ("upperarm", "lowerarm", "hand"))
    knees = None
    if extra.get("stance_widen") and CROUCH_KNEE_OVER_TOES:
        knees = knees_over_toes(proxy, CROUCH_KNEE_OVER_TOES)
        adjust = chain_adjust(adjust, knees)
        # the knee moved off the proxy on purpose; the hip, ankle, foot and ball are still gated
        skip = skip + tuple("%s_%s" % (b, s) for s in ("l", "r") for b in ("thigh", "calf"))
    bases = sample_transfer(proxy, game, n, fingers=fingers, adjust=adjust)
    if knees is not None:
        rep["stance_layer"]["knee_over_toes"] = {"fraction": CROUCH_KNEE_OVER_TOES, "turn_deg": {
            s: {"min": round(min(v), 1), "max": round(max(v), 1), "mean": round(sum(v) / len(v), 1)}
            for s, v in knees.turned.items() if v}}
    if extra.get("standing_upper"):
        rep["standing_trunk_correction_deg"] = standing_upper_body(game, bases)
        skip = tuple(GAME_FROM_ORG)   # the whole body moved off the proxy on purpose
    _write_action(game, action, bases)
    rep["transfer_fidelity"] = transfer_fidelity(proxy, game, n, skip=skip) if not extra.get("standing_upper") else None
    rows = joint_series(game, n)
    rep["legs"] = leg_readings(rows)
    rep["clavicle_elevation_deg"] = clavicle_elevation(rows, game)
    if flare is not None:
        rep["arm_abduction_deg"] = arm_abduction(rows)
        rep["arm_flare_layer_deg"] = flare
    if extra.get("stance_widen"):
        rep["stance_layer"]["game"] = stance_width(rows)
    if fingers:
        rep["finger_pose"] = finger_readings(game, n)
    if loop:
        rep["seam_flesh"] = seam_mm(game, n)
    pel = [r["pelvis"] for r in rows]
    flat = lambda v: Vector((v.x, v.y, 0.0))
    rep["pelvis_travel_m"] = {"first_to_last": round((flat(pel[-1]) - flat(pel[0])).length, 3),
                              "max_from_start": round(max((flat(p) - flat(pel[0])).length for p in pel), 3),
                              "drop_m": round(pel[0].z - min(p.z for p in pel), 3),
                              "pelvis_min_z_m": round(min(p.z for p in pel), 3)}
    natural = (rt.get("root_motion") or {}).get("speed_mps")
    if extra.get("gait") and natural:
        rep["speed"] = {"natural_rig_mps": natural, "in_game_mps": round(natural * game_scale(), 4),
                        "treadmill_rig_mps": rep["check"]["treadmill_mps"],
                        "agreement": round(abs(rep["check"]["treadmill_mps"] - natural) / natural, 4),
                        "band": SPEED_BANDS.get(action)}
        rep["belt_slide"] = belt_slide(game, n, natural, loop)
    rep["flesh"] = flesh_over_clip(probe, n, flesh_step(n))
    # --- the clip's verdict
    failed = []
    if extra.get("belt_gate"):
        # stances of 2-3 samples: animation_check's pooled belt reads touch-down/toe-off samples
        # as planted (see CLIPS); the belt truth at the natural speed is the gate, the pooled
        # reading is quoted beside it
        if rep["belt_slide"]["worst_mm"] > BELT_TOL_MM:
            failed.append("belt slide %.2f mm" % rep["belt_slide"]["worst_mm"])
    elif extra.get("combat"):
        src_s = source_slide(path)
        add = added_slide(path, rt, game, n)
        rep["slide_vs_take"] = {"take_gate_mm": src_s["worst_drift_mm"],
                                "rig_gate_mm": rep["check"]["worst_drift_mm"], "added": add,
                                "allowed_added_mm": ONESHOT_SLIDE_ADDED_MM}
        if add["worst_added_mm"] > ONESHOT_SLIDE_ADDED_MM:
            failed.append("the rig skids %.1f mm more than the take (frames %s)" % (
                add["worst_added_mm"], add["l"]["frames"] if add["l"]["worst_added_mm"] >= add["r"]["worst_added_mm"] else add["r"]["frames"]))
    elif not extra.get("no_floor") and rep["check"]["gate"] != "ok":
        failed.append("slide gate %s (%s mm)" % (rep["check"]["gate"], rep["check"]["worst_drift_mm"]))
    if extra.get("gait") and extra.get("arms_from_take") and rep["check"]["gait_opposition"] == "fail":
        # the take's own arms (proved by arm_paths_vs_take): the limb blocks one by one, the
        # opposition reading quoted as the take's
        rep["arm_lines_vs_take_deg"] = arm_paths_vs_take(path, dict(rt), game, n)
        if rep["arm_lines_vs_take_deg"] > 5.0:
            failed.append("gait_opposition fail and the arms are %.1f deg off the take"
                          % rep["arm_lines_vs_take_deg"])
        for key in ("stretch_verdict", "ik_reach_verdict"):
            if rep["check"][key] not in ("ok", "attention", None):
                failed.append("%s %s" % (key, rep["check"][key]))
    elif extra.get("gait"):
        if rep["check"]["deformation_gate"] not in ("ok", "attention"):
            failed.append("deformation gate %s" % rep["check"]["deformation_gate"])
    else:
        # not a gait: gait_opposition / strike_lead are craft bands for authored gaits and read a
        # punch's step or a fall as one (printed only, as build_protagonist_human.py does for
        # the crouch idle); the limb blocks are gated one by one
        for key in ("stretch_verdict", "ik_reach_verdict"):
            if rep["check"][key] not in ("ok", "attention", None):
                failed.append("%s %s" % (key, rep["check"][key]))
    if params.get("motion_quality") and rep["check"]["motion_quality_gate"] != "ok":
        failed.append("motion quality %s" % rep["check"]["motion_quality_gate"])
    if loop and rep["seam_flesh"]["worst_mm"] > SEAM_TOL_MM:
        failed.append("seam %.3f mm on %s" % (rep["seam_flesh"]["worst_mm"], rep["seam_flesh"]["mesh"]))
    for part, (key, tol) in FIDELITY_TOL.items():
        fid = (rep["transfer_fidelity"] or {}).get(part)
        if fid and fid[key] > tol:
            failed.append("transfer fidelity %s %s" % (part, fid))
    sp = rep.get("speed")
    if sp:
        lo, hi = sp["band"]
        if not lo <= sp["in_game_mps"] < hi:
            failed.append("in-game speed %.3f outside [%s, %s)" % (sp["in_game_mps"], lo, hi))
        if sp["agreement"] > SPEED_AGREEMENT and not extra.get("belt_gate"):
            failed.append("treadmill %.4f vs natural %.4f" % (sp["treadmill_rig_mps"], natural))
    rep["ready"] = not failed
    rep["failed"] = failed
    rep["seconds"] = round(time.monotonic() - t0, 1)
    reset_pose(game)
    reset_pose(proxy)
    return rep


def rest_flesh(probe, game):
    reset_pose(game)
    return probe.frame()


def build(out_blend, report_path, only=None):
    import addon_utils
    addon_utils.enable("forge", default_set=True, persistent=False)
    if os.path.normcase(os.path.abspath(bpy.data.filepath)) == os.path.normcase(
            os.path.join(FORGE, "projects", "werewolf", "models", "mpfb-custom-draft.blend")):
        raise SystemExit("Run this on a COPY of mpfb-custom-draft.blend, not the approved source.")
    sys.path.insert(0, HERE)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    game = bpy.data.objects[GAME_RIG]
    REP = {"blend_in": bpy.data.filepath, "fps": FPS, "game_scale": round(game_scale(), 6),
           "contract_height_m": CONTRACT_H, "clips": {}}
    t0 = time.monotonic()
    proxy, _fit = build_proxy(game)
    REP["proxy"] = {"bones": len(proxy.data.bones), "rest_equality": rest_equality(game, proxy)}
    # frozen mocap (CMU / 100STYLE) is what PROVENANCE.json says it is
    prov = {e["file"]: e for e in json.load(open(os.path.join(MOCAP_DIR, "PROVENANCE.json")))["clips"]}
    for spec in CLIPS:
        if not spec[2].startswith("mixamo:"):
            sha = hashlib.sha256(open(src_path(spec[2]), "rb").read()).hexdigest()
            if sha != prov[spec[2]]["crop_sha256"]:
                raise SystemExit("%s is not the frozen crop (%s)" % (spec[2], sha))
    REP["sleeve_touchup"] = sleeve_touchup(game)
    print("SLEEVE TOUCHUP", json.dumps(REP["sleeve_touchup"]))
    probe = FleshProbe(game)
    REP["flesh_probe"] = probe.info
    REP["flesh_rest"] = rest_flesh(probe, game)
    print("REST FLESH", json.dumps(REP["flesh_rest"]))
    step = lambda n: 1 if n <= 40 else (2 if n <= 120 else 4)
    for spec in CLIPS:
        if only and spec[0] not in only:
            continue
        print("CLIP", spec[0], "...", flush=True)
        rep = run_clip(spec, proxy, game, probe, step)
        REP["clips"][spec[0]] = rep
        print("CLIP %s ready=%s %s" % (spec[0], rep["ready"], json.dumps({k: rep.get(k) for k in (
            "frames", "check", "transfer_fidelity", "seam_flesh", "speed", "failed")}, default=str)), flush=True)
        json.dump(REP, open(report_path, "w"), indent=1, default=str)
    # the deliverable carries the game rig's clips, not the proxy
    for a in [a for a in bpy.data.actions if a.name.startswith("PX_")]:
        a.use_fake_user = False
        bpy.data.actions.remove(a)
    for name in (PROXY, PROXY_META):
        o = bpy.data.objects.get(name)
        if o is not None:
            d = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if d is not None and d.users == 0:
                bpy.data.armatures.remove(d)
    reset_pose(game)
    REP["actions_on_rig"] = sorted(a.name for a in bpy.data.actions)
    REP["seconds"] = round(time.monotonic() - t0, 1)
    json.dump(REP, open(report_path, "w"), indent=1, default=str)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_blend), compress=True)
    print("SAVED", out_blend, "actions", REP["actions_on_rig"])




# ------------------------------------------------------------------------------------------------
# previews (the artist's judgment set): same look-dev class as build_custom.render / mpfb_custom4_*
# (EEVEE, key/fill/rim suns, grey world, dark floor), one mp4 per clip at a 3/4-front camera, the
# gaits also from the side, loops played for >= ~3 s, one-shots held 0.5 s on their last pose; plus
# a combat contact strip (6 poses per combat clip).
# ------------------------------------------------------------------------------------------------
SIDE_VIEWS = ("walk-loop", "run-loop", "sprint-loop", "crouch_walk-loop")
PREVIEW_RES = 900
PREVIEW_SAMPLES = 32
HOLD_FRAMES = 12


def _look(scene):
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    try:
        scene.eevee.taa_render_samples = PREVIEW_SAMPLES
    except Exception:
        pass
    world = bpy.data.worlds.new("look")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
    for name, energy, rot, color in (("key", 3.2, (50, 0, 330), (1, 1, 1)),
                                     ("fill", 1.0, (65, 0, 395), (0.85, 0.9, 1.0)),
                                     ("rim", 2.0, (60, 0, 190), (1, 1, 1))):
        d = bpy.data.lights.new(name, "SUN")
        d.energy = energy
        d.color = color
        o = bpy.data.objects.new(name, d)
        scene.collection.objects.link(o)
        o.rotation_euler = [math.radians(a) for a in rot]
    fme = bpy.data.meshes.new("floor")
    fme.from_pydata([(-6, -6, 0), (6, -6, 0), (6, 6, 0), (-6, 6, 0)], [], [(0, 1, 2, 3)])
    floor = bpy.data.objects.new("floor", fme)
    scene.collection.objects.link(floor)
    fm = bpy.data.materials.new("floor")
    fm.use_nodes = True
    fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
    fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
    fme.materials.append(fm)
    cam_d = bpy.data.cameras.new("cam")
    cam_d.lens = 50
    cam = bpy.data.objects.new("cam", cam_d)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def _aim(cam, target, angle, dist, elev):
    a = math.radians(angle)
    cam.location = target + Vector((math.sin(a) * dist, -math.cos(a) * dist, elev))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()


def _video(scene, path):
    if hasattr(scene.render.image_settings, "media_type"):
        scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.filepath = path


def _cyclic(action, n, on):
    action.use_frame_range = on
    if on:
        action.frame_start, action.frame_end = 1, n
    action.use_cyclic = on


def render(prefix, only=None):
    import addon_utils
    addon_utils.enable("forge", default_set=True, persistent=False)
    from forge.tools import rigforge_rig as rr
    prefix = os.path.abspath(prefix)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    game = bpy.data.objects[GAME_RIG]
    cam = _look(scene)
    height = 1.8825
    wrote = []
    strip = []
    for action_name, role, _src, _p, extra in CLIPS:
        if only and action_name not in only:
            continue
        act = bpy.data.actions.get(action_name)
        if act is None:
            print("SKIP (no action)", action_name)
            continue
        rr.assign_action(game, act)
        n = int(round(act.frame_range[1]))
        loop = action_name.endswith("-loop")
        if loop:
            cycles = max(1, int(math.ceil(3.0 / max(1e-6, (n - 1) / FPS))))
            _cyclic(act, n, True)
            last = 1 + cycles * (n - 1)
        else:
            last = n + HOLD_FRAMES
        # frame the pelvis path over the whole clip
        pts = []
        for f in range(1, n + 1, 2):
            scene.frame_set(f)
            pts.append(game.matrix_world @ game.pose.bones["pelvis"].head)
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), 0))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), 0))
        span = (hi - lo).length
        target = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, height * 0.5))
        dist = height * 1.75 + span * 1.2
        scene.frame_start, scene.frame_end = 1, last
        scene.render.resolution_x = scene.render.resolution_y = PREVIEW_RES
        views = [("", 35.0)] + ([("_side", 90.0)] if action_name in SIDE_VIEWS else [])
        for tag, angle in views:
            _aim(cam, target, angle, dist, height * 0.08)
            out = "%s_%s%s.mp4" % (prefix, action_name, tag)
            _video(scene, out)
            t0 = time.monotonic()
            bpy.ops.render.render(animation=True)
            wrote.append(out)
            print("WROTE", out, "%d frames %.0fs" % (last, time.monotonic() - t0), flush=True)
        if extra.get("combat"):
            scene.render.resolution_x = scene.render.resolution_y = 360
            if hasattr(scene.render.image_settings, "media_type"):
                scene.render.image_settings.media_type = "IMAGE"
            scene.render.image_settings.file_format = "PNG"
            _aim(cam, target, 35.0, dist, height * 0.08)
            for k in range(6):
                f = 1 + int(round(k * (n - 1) / 5.0))
                scene.frame_set(f)
                scene.render.filepath = "%s__strip_%s_%d.png" % (prefix, action_name, k)
                bpy.ops.render.render(write_still=True)
                strip.append(scene.render.filepath)
        if loop:
            _cyclic(act, n, False)
    if strip:
        sys.path.insert(0, HERE)
        import build_custom as bc
        bc.grid(prefix + "_combat_strip.png", strip, ncols=6, gap=6)
        for p in strip:
            try:
                os.remove(p)
            except OSError:
                pass
        wrote.append(prefix + "_combat_strip.png")
    print("RENDERED", json.dumps(wrote))


if __name__ == "__main__":
    if ARGS and ARGS[0] == "build":
        build(ARGS[1], ARGS[2], ARGS[3:] or None)
    elif ARGS and ARGS[0] == "render":
        render(ARGS[1], ARGS[2:] or None)
    else:
        print(__doc__)
