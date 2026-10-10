"""Shadowbun -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/shadowbun_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-09 "NEW UNIT: Shadowbun (artist reference sheet)" + "Shadowbun answers" (artist:
"floaty waddle, 2 eyes one on each side, can add a small glow, this reference didnt give a full front reference but
should be like a thick bunny") + the sheet design/reference/shadowbun-character-sheet.webp (intake:
design/reference/shadowbun/intake.json). Contract: improve/templates/new_unit/BUILD_CONTRACT.md.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The sheet's FRONT is drawn three-quarter, so every constant is
measured off the two TRUE orthos (intake ortho_frame.views = back, side):
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 905) / 100 ; z = (767 - y_px) / 100
  BACK view (the unit's right on image right):          X = -(x_px - 624) / 100 ; z = (777 - y_px) / 100
(the intake axes / floor lines; X +ve = the unit's LEFT (L)). The FRONT width is the BACK view's width mirrored (artist
2026-10-09: "should be like a thick bunny" -- never slimmed toward the 3/4 drawing). The sheet is drawn with a slight
high, rotated camera: features nearer the viewer sit lower / shifted on the page (side-view near foot sole y 782 vs the
far foot ~760; back-view head top y 265 vs side y 275); constants split such pairs and say so.

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid + smooth unions): the tall EGG/PEAR (no neck; the head is its narrow
     top) from a (z, front, back, half-width) profile, the rounded MUZZLE proud at the front-top, two short thick ARMS
     (round-cone chains hanging close at the sides), two stubby LEGS + big rounded FEET with TOE clumps, the cloud-puff
     TAIL (a core + rounded fur clumps) low on the back, and the FUR: ~170 directional tuft cones (fur flicks pointing
     down / out along the fur flow) smooth-unioned on the arms, hand ends, legs, lower body, shoulders and tail; clamped
     flat on the floor. Marching tets = the HIGH (bake source; it carries every tuft into the normal map); collapse
     decimation = the LOW shell (deterministic).
  2. REGIONS on the low shell, every boundary iso-cut: part ownership (fur / tail), the round BELLY patch (an ellipse in
     (body azimuth, height) whose top edge is SCALLOPED into cloud bumps) and the thin BELLY_LINE band just inside the
     scalloped top edge.
  3. PARTS (shadowbun_parts.py, closed): two leaf EARS (flattened lens tubes, serrated fur edges, accent lavender);
     closed conforming DECALS on the low shell: two lateral EYE dots (one per side, artist B2) + the cloud GRIN (a row of
     soft domed tooth-bumps along the muzzle's front-lower edge, rising toward each cheek).
  4. palette (palettes.py regions -> Col / Glow), Smart UV, bake normal + AO from the SDF high + parts at seed 0.
     Glow: eye + grin only (small, artist B3); one opaque material.
  5. RIG: root > hips > body > head > ear.{L,R}.{0,1}; body > arm.{L,R}.{0,1}; hips > leg.{L,R}.{0,1} (1 = the foot);
     hips > tail.{0,1}. Clips: idle (4 s breathing, ear twitches, tail fluff, arm sway, feet planted) + walk (2 s
     FLOATY WADDLE, artist B1: side-to-side rocking steps over each foot's outer edge, soft hang time, eased landings,
     ears + tail trailing). glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K            # noqa: E402  (read-only use)
import palettes as PAL        # noqa: E402  (read-only use)
import magmoo_sdf as SD       # noqa: E402  (read-only use: the SDF kit)
import shadowbun_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "shadowbun"
CHAR_ID = "shadowbun"                 # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.025                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.26                       # must cover the largest smooth-union k (PUFF_K)
BODY_TRIS = 13000                     # "body mesh detail": the shell's decimation target (before the colour cuts)
CORNER_CLAMP = 0.1                    # grid values within this x step of 0 are pushed off it (see the polygonise note)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- the EGG / PEAR body (head = its narrow top, no neck). Rows (z, front Y, back Y, half-width X):
#   front / back from the SIDE view outline (y px -> z, x px -> Y): e.g. belly front x 837 @ y 540 (z 2.27), back x 1089
#   @ y 580 (z 1.87), throat crease x 885 @ y 402 (z 3.65), head top y 275; half-width from the BACK view (mirrored = the
#   front width): e.g. x 553-697 @ y 300 (0.72), 528-720 @ y 380 (0.96), 490-760 @ y 600 (1.35) -- head rows shifted
#   -0.15 u (the back view's head top sits 10-15 px higher than the side view's: drawn from slightly above)
EGG_PROFILE = [(0.79, 0.75, 0.75, 0.00), (0.82, 0.55, 0.95, 0.50), (0.90, 0.20, 1.30, 0.86), (1.02, -0.10, 1.57, 1.15),
               (1.17, -0.30, 1.70, 1.26), (1.37, -0.47, 1.79, 1.31), (1.57, -0.57, 1.83, 1.34), (1.87, -0.65, 1.84, 1.35),
               (2.27, -0.68, 1.80, 1.32), (2.67, -0.67, 1.71, 1.27), (3.07, -0.62, 1.61, 1.19), (3.27, -0.53, 1.55, 1.14),
               (3.47, -0.38, 1.48, 1.08), (3.65, -0.27, 1.42, 1.03), (3.87, -0.30, 1.37, 0.97), (4.07, -0.35, 1.31, 0.92),
               (4.27, -0.38, 1.26, 0.87), (4.47, -0.36, 1.21, 0.81), (4.67, -0.24, 1.13, 0.73), (4.84, -0.02, 0.99, 0.58),
               (4.94, 0.22, 0.80, 0.40), (4.99, 0.44, 0.62, 0.20), (5.01, 0.53, 0.53, 0.00)]
EGG_WIDTH = 1.0                       # "thick bunny": front-view width multiplier on the back-view half-widths
# ---- muzzle (rounded, proud at the front-top): side view tip x 852 @ y 352 (Y -0.53, z 4.15), chin x 868 @ y 385
MUZZLE_C, MUZZLE_R, MUZZLE_K = (0.0, -0.21, 4.08), (0.37, 0.32, 0.31), 0.10
# ---- arms (short, thick, hanging close at the sides): spine knots (X for the L side, Y, z) + radius per knot.
#   BACK view: outer edge x 455-467 @ y 480-540 (X 1.57-1.69), inner crease x 520 (X 1.04), shoulder fringe from y 395;
#   SIDE view: arm x 935-1010 (Y 0.30..1.05), end tufts y 605-615 (z 1.57); back view ends y 575-585 (z 1.95): the end
#   splits the two (z ~1.72) -- the drawn views disagree by 0.4 u (perspective)
ARM_SPINE = [(0.93, 0.56, 3.55), (1.20, 0.58, 2.88), (1.36, 0.56, 2.30), (1.40, 0.54, 2.04)]
ARM_R = [0.27, 0.33, 0.35, 0.31]
ARM_K = 0.12                          # "shoulder blend" into the egg
# ---- legs (stubby) + feet (big, rounded) + toe clumps. BACK view: leg outer x 497 (X 1.27) / feet x 500-555; SIDE
#   view: near leg x 932-1060, far foot front x 875 (the drawing's camera turns ~20 deg: near / far legs straddle Y 0.55)
HIP = (0.66, 0.56, 1.05)              # thigh top (L side)
ANKLE = (0.70, 0.52, 0.48)
LEG_R = (0.52, 0.50)                  # "leg thickness" (hip, ankle)
LEG_K = 0.20                          # legs melt into the egg bottom over this
FOOT_C, FOOT_R, FOOT_K = (0.70, 0.40, 0.28), (0.50, 0.76, 0.36), 0.14   # "foot size": the rounded foot blob (floor-cut)
TOES = [(-0.27, 0.15), (0.0, 0.16), (0.27, 0.15)]   # "toe clumps": (X offset, radius) along the foot front
TOE_Y, TOE_Z, TOE_K = -0.20, 0.17, 0.06
# ---- tail (cloud puff, lighter lavender): SIDE view top y 445 @ x 1140, back end x 1220, bottom y 712 (clears the floor
#   by 0.55 u -- the side view is binding: the back view draws it reaching the floor), BACK view width x 518-768 (2.5 u)
TAIL_C = (0.0, 2.20, 1.90)
TAIL_R = (1.20, 0.86, 1.32)           # "tail size" (half width / depth / height)
TAIL_CORE = 0.56                      # the core ellipsoid as a fraction of TAIL_R
TAIL_PUFFS, TAIL_PUFF_R = 12, (0.46, 0.68)   # "cloud bulges": large rounded puffs (fibonacci spread), radius range
TAIL_PUFF_REACH = 1.00                # the big bulges sit this far out (the soft blend adds the rest)
TAIL_RIM, TAIL_RIM_R = 22, (0.26, 0.36)     # "cloud edge": small rim bulges scalloping the outline
TAIL_RIM_REACH = 1.10                 # the rim bulges stand this far out (fraction of TAIL_R)
TAIL_K, PUFF_K, RIM_K = 0.16, 0.12, 0.07    # soft smooth unions (big k = cloudy, no crevice points)
# ---- fur tufts (silhouette fringe): flicks = round cones pointing down / out along the fur flow
TUFT_K = 0.06                         # v1.1: tufts melt deep into the surface (soft, never scales / spikes)
TUFT_R = (0.075, 0.040)               # "tuft thickness" (root, BLUNT tip), root jittered +-25 %
TUFT_LEN = 0.13                       # "tuft length" (jittered +-40 %)
TUFT_FLOW = 0.85                      # how hard the tufts point DOWN (vs straight out of the surface)
TUFT_SEED = 3.0
# ---- belly patch (round, lighter lavender) with a SCALLOPED top edge + the thin pale line along it
#   SIDE view: patch top edge y 445-455 (z 3.15-3.2), bottom y 650 (z 1.17); front-view scallops ~0.3-0.4 u wide
BELLY_ZC, BELLY_ZB, BELLY_ZT = 2.15, 1.17, 3.10   # patch centre / bottom / top-edge base (z)
BELLY_AZ = 1.06                       # "belly width": half-extent round the body, rad (elliptical azimuth, 0 = front)
SCALLOPS, SCALLOP_A = 5, 0.12        # "scallops": cloud bumps across the top edge, bump height (fraction of the radius)
SCALLOP_ARC = 0.27                    # the scallops run over the upper arc of the edge from this angle (rad) to pi - it
LINE_W = 0.032                        # "belly line" width (field units ~ sheet units: the drawn line is 3-4 px)
# ---- ears (leaf, upright, slight V, accent lavender): BACK view tips x 575 / 672 @ y 172 (X +-0.49, z 5.95), bases
#   x 600 / 650 @ y 285; SIDE view tips x 965 / 1018 @ y 168 (Y ~0.86), base ~x 965 @ y 290 (Y 0.6)
EAR_BASE = (0.24, 0.62)               # where each ear leaves the head top (X, Y); z found on the shell
EAR_TIP = (0.49, 0.86, 5.96)          # "ear tip" (L side)
EAR_SINK = 0.18                       # the ear root is buried this far into the head
EAR_W, EAR_T = 0.36, 0.13             # "ear width" (half, widest at ~40 %) / "ear thickness" (half)
EAR_YAW = 30.0                        # the leaf face turns this far from the front toward its side (reads back AND side)
EAR_BOW = 0.06                        # backward bow of the ear spine
EAR_FRINGE = (5, (0.07, 0.15), (0.025, 0.05))   # "soft fur fringe": rounded notches per edge, depth range, width range
EAR_CUP = 0.20                        # "ear cup": the edges curl toward the front by this share of the half-width
EAR_ST, EAR_RING = 46, 12
# ---- face decals (closed, conforming): eyes + the cloud grin. Positions cast horizontally onto the shell from the head
#   axis (Y 0.45) at (azimuth from the front, z). SIDE view eye centre x 907 @ y 327 (Y 0.02, z 4.40), 23 px across.
HEAD_AXIS_Y = 0.45
EYE_AZ, EYE_Z, EYE_R = 34.0, 4.40, 0.12   # "eye placement" (two eyes, one per side, mirrored: artist B2) + "eye size".
                                          #   v1.2 artist markup (design/reference/shadowbun-v11-eye-annotation.png): "move the eyes
                                          #   slightly more inward" -- 58 -> 34 deg: front-render eye half-span 96.2 -> 64.7 px vs the
                                          #   marked 0.675 x (64.9 px), measured on preview renders at 58 / 38 / 34
#   grin: side view teeth from x 858 @ y 368 (tip) to x 900 @ y 356 (cheek), ~5 bumps a side, radii 6-8 px
TEETH = [(0.0, 3.97, 0.088), (8.0, 3.975, 0.086), (16.0, 3.985, 0.083), (25.0, 4.00, 0.080), (34.0, 4.025, 0.076),
         (43.0, 4.055, 0.071), (51.0, 4.09, 0.065), (59.0, 4.125, 0.058)]   # (azimuth deg, z, radius): overlapping bumps = one connected cloud grin
DECAL_UP, DECAL_DN, DECAL_DOME = 0.010, 0.03, 0.022   # decal top offset / sink / dome height (eye x 1.2)
TOOTH_DOME = 0.014                    # the grin bumps are flatter (soft cloud, not pearls)
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands, z)
HIP_BAND = (0.95, 1.45)               # hips -> body blend on the egg
HEAD_BAND = (3.30, 3.95)              # body -> head blend on the egg (wide: no seam at the head / body band)
FOOT_BAND = (0.36, 0.62)              # foot -> thigh blend on the legs
LEG_TOP = (0.90, 1.25)                # thigh -> hips blend at the leg tops
OWN_TAU = 0.06                        # soft part ownership for weights
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1)
IDLE_N = 96                           # "idle loop": 4 s
BREATH = 0.016                        # "breathing": body scale +- (2 breaths per loop)
IDLE_SWAY = (1.0, 0.8)                # body roll / head nod deg
EAR_IDLE = (2.0, 4.0, 1, 0.7)         # ear sway (root deg, tip deg, cyc, lag)
EAR_TWITCH = {"L": (0.28, 9.0), "R": (0.71, 8.0)}   # "ear twitch": de-synced per ear (loop phase, deg)
TWITCH_W, TWITCH_HZ = 0.035, 12       # twitch pulse width (loop fraction), flutter harmonic (3 Hz)
TAIL_IDLE = (3.0, 5.0, 2, 0.8)        # "tail fluff" wag (root, tip deg, cyc, lag)
TAIL_PUFF = 0.035                     # tail puff scale +- (tail.1, 2 cyc)
ARM_IDLE = (2.5, 1.5)                 # "arm sway": fore-aft deg (1 cyc), out deg (2 cyc)
WALK_N = 48                           # "waddle loop": 2 s = two steps (L stance, R stance)
WADDLE_ROLL = 7.0                     # "waddle rock": hips roll over the stance foot's outer edge, deg
WADDLE_COUNTER = 0.35                 # the upper body undoes this share of the rock (the head stays steadier)
WADDLE_YAW = 4.0                      # twist toward the stepping foot, deg
WADDLE_LIFT = 0.06                    # "float": the body lifts this much over each step and drifts back down
WADDLE_LIFT_LAG = 0.06                # the float trails the rock by this share of a step (drifts down after landing)
WADDLE_SWING = 6.0                    # the swing leg reaches forward this many deg (in place)
WADDLE_STRETCH, WADDLE_SQUASH = 0.015, 0.02   # stretch at the float / soft squash just after each landing
EAR_TRAIL = (0.9, 0.06)               # ears trail the rock: share of the rock (opposite), lag per bone (loop fraction)
EAR_FLOAT = 8.0                       # ears dip back as the body floats up / spring forward as it settles, deg
TAIL_TRAIL = (1.3, 0.08)              # tail sways behind the rock: share, lag per bone
ARM_TRAIL = (0.8, 0.05, 4.0)          # arms swing out behind the rock: share, lag, fore-aft swing deg
FLOOR_EPS = 1e-4

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(ROOT, "rigged", UNIT + ".glb")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": "none: from-scratch unit (sheet "
          "design/reference/shadowbun-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; measured off the TRUE orthos (back + side); floor z = 0 at the soles",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def mesh_arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], [list(map(int, f)) for f in F])
    me.update()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    return ob


def evaluated_arrays(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me_l = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    V, F = mesh_arrays(me_l)
    bpy.data.meshes.remove(me_l)
    return V, F


def keep_islands(V, F, min_frac):
    n = len(V)
    par = np.arange(n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for f in F:
        r0 = find(f[0])
        for v in f[1:]:
            r1 = find(v)
            if r1 != r0:
                par[r1] = r0
    roots = np.array([find(i) for i in range(n)])
    _, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_v = (cnt >= min_frac * n)[inv]
    remap = -np.ones(n, dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "dropped_verts": int((~keep_v).sum())}


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body shell SDF
t_sdf = time.time()
_eg = SD.catmull(np.array(EGG_PROFILE, float), 10)
_eg = _eg[np.argsort(_eg[:, 0], kind="stable")]
EZ = _eg[:, 0]
EF, EB = _eg[:, 1], np.maximum(_eg[:, 2], _eg[:, 1])
EYC = 0.5 * (EF + EB)
ERY = np.clip(0.5 * (EB - EF), 0.0, None)
ERX = np.clip(_eg[:, 3], 0.0, None) * EGG_WIDTH
ERX[0] = ERX[-1] = ERY[0] = ERY[-1] = 0.0


def egg_at(z):
    return (np.interp(z, EZ, EYC), np.interp(z, EZ, ERY, left=0.0, right=0.0), np.interp(z, EZ, ERX, left=0.0, right=0.0))


def sd_egg(P):
    yc, ry, rx = egg_at(P[:, 2])
    q = np.hypot(P[:, 0] / np.maximum(rx, 1e-3), (P[:, 1] - yc) / np.maximum(ry, 1e-3))
    d = (q - 1.0) * np.maximum(0.5 * (rx + ry), 1e-3)
    return np.maximum(d, np.maximum(EZ[0] - P[:, 2], P[:, 2] - EZ[-1]))


def sd_muzzle(P):
    return SD.sd_ellipsoid(P, MUZZLE_C, MUZZLE_R)


def mir(p, sg):
    return np.array([sg * p[0], p[1], p[2]], float)


SIDES = (("L", 1.0), ("R", -1.0))
ARM_PTS = {s: [mir(p, sg) for p in ARM_SPINE] for s, sg in SIDES}
ARM_CONES = {s: [(ARM_PTS[s][i], ARM_PTS[s][i + 1], ARM_R[i], ARM_R[i + 1]) for i in range(len(ARM_SPINE) - 1)]
             for s, _ in SIDES}


def cones_sdf(P, cones):
    d = np.full(len(P), 1e9)
    for a, b, r1, r2 in cones:
        d = np.minimum(d, SD.sd_round_cone(P, a, b, r1, r2))
    return d


def cones_box(cones, pad=0.0):
    A = np.array([c[0] for c in cones] + [c[1] for c in cones])
    R = max(max(c[2], c[3]) for c in cones)
    return A.min(0) - R - pad, A.max(0) + R + pad


LEG = {}
for s, sg in SIDES:
    LEG[s] = {"thigh": (mir(HIP, sg), mir(ANKLE, sg), LEG_R[0], LEG_R[1]), "foot": mir(FOOT_C, sg),
              "toes": [(np.array([sg * (FOOT_C[0] + dx), TOE_Y, TOE_Z]), r) for dx, r in TOES]}


def sd_leg(P, s, parts=("thigh", "foot", "toes")):
    L_ = LEG[s]
    d = np.full(len(P), 1e9)
    if "thigh" in parts:
        d = np.minimum(d, SD.sd_round_cone(P, *L_["thigh"]))
    if "foot" in parts:
        d = np.minimum(d, SD.sd_ellipsoid(P, L_["foot"], FOOT_R))
    if "toes" in parts:
        for c, r in L_["toes"]:
            d = np.minimum(d, SD.sd_sphere(P, c, r))
    return d


# tail (v1.1 gate fix, "soft, rounded fur / cloud-like shape"): a CLOUD PUFF = a core + large overlapping ROUNDED bulges
# blended with a big smooth-union k, plus a ring of smaller rim bulges that scallop the outline into a cloud edge (no
# locks, no points). Fibonacci-spread; none pointing into the body.
TC, TR_ = np.array(TAIL_C, float), np.array(TAIL_R, float)
CLUMPS = []                                   # (centre, radius, k)


def _fib(n, seed):
    out = []
    for i in range(n):
        zz = 1.0 - 2.0 * (i + 0.5) / n
        rr = math.sqrt(max(0.0, 1.0 - zz * zz))
        th = math.pi * (3.0 - math.sqrt(5.0)) * i + seed
        out.append(np.array([rr * math.cos(th), rr * math.sin(th), zz]))
    return out


for i, d in enumerate(_fib(TAIL_PUFFS + 5, 0.0)):
    if d[1] < -0.30:
        continue
    r_c = TAIL_PUFF_R[0] + (TAIL_PUFF_R[1] - TAIL_PUFF_R[0]) * FP.hash01(i, 1.7)
    ext = TR_ * d
    CLUMPS.append((TC + ext * TAIL_PUFF_REACH * (1.0 - r_c / max(np.linalg.norm(ext), 1e-6)), r_c, PUFF_K))
for i, d in enumerate(_fib(TAIL_RIM + 6, 0.9)):
    if d[1] < -0.20:
        continue
    r_c = TAIL_RIM_R[0] + (TAIL_RIM_R[1] - TAIL_RIM_R[0]) * FP.hash01(i, 4.3)
    ext = TR_ * d
    CLUMPS.append((TC + ext * (TAIL_RIM_REACH - r_c / max(np.linalg.norm(ext), 1e-6)), r_c, RIM_K))


def sd_tail(P, with_tufts=False):
    d = SD.sd_ellipsoid(P, TC, TR_ * TAIL_CORE)
    for c, r, _ in CLUMPS:
        d = np.minimum(d, SD.sd_sphere(P, c, r))
    if with_tufts:
        d = np.minimum(d, tufts_sdf(P, "tail"))
    return d


# ---- fur tufts (deterministic): (group, root a, tip b, r_root, r_tip)
TUFTS = []
_tk = [0]


def add_tuft(group, S, nrm, length=None, flow=None, embed=0.08, radii=None):
    k = _tk[0]; _tk[0] += 1
    j = np.array([FP.hash01(k, TUFT_SEED) - 0.5, FP.hash01(k, TUFT_SEED + 1) - 0.5, FP.hash01(k, TUFT_SEED + 2) - 0.5])
    fl = TUFT_FLOW if flow is None else flow
    nrm = unit(nrm)
    dvec = unit(nrm * (1.0 - fl) + np.array([0.0, 0.0, -1.0]) * fl + 0.25 * j)
    if dvec @ nrm < 0.25:                          # never into the surface
        dvec = unit(dvec + nrm * (0.25 - dvec @ nrm + 0.1))
    L_ = (TUFT_LEN if length is None else length) * (0.6 + 0.8 * FP.hash01(k, TUFT_SEED + 3))
    a = np.asarray(S, float) - nrm * embed
    r_ = TUFT_R if radii is None else radii
    rj = 0.75 + 0.5 * FP.hash01(k, TUFT_SEED + 4)
    TUFTS.append((group, a, np.asarray(S, float) + dvec * L_, r_[0] * rj, min(r_[1] * rj, 0.8 * r_[0] * rj)))


def perp_frame(t):
    t = unit(t)
    e1 = unit(np.cross(t, [0.0, 1.0, 0.0]) if abs(t[1]) < 0.9 else np.cross(t, [1.0, 0.0, 0.0]))
    return e1, np.cross(t, e1)


for s, sg in SIDES:
    P_ = np.array(ARM_PTS[s]); al = SD.arclen(P_)
    for st, fr in enumerate(np.linspace(0.22, 0.90, 4)):          # arm sides (skip the inner face against the body)
        x_ = fr * al[-1]
        C_ = np.array([np.interp(x_, al, P_[:, k]) for k in range(3)])
        r_ = np.interp(x_, al, ARM_R)
        e1, e2 = perp_frame(P_[-1] - P_[0])
        for m in range(6):
            th = 2 * math.pi * (m + 0.5 * (st % 2) + 0.3 * (FP.hash01(st * 7 + m, 8.1) - 0.5)) / 6
            rad = math.cos(th) * e1 + math.sin(th) * e2
            if rad[0] * sg < -0.35 or (rad[1] > 0.6 and abs(rad[0]) < 0.55):   # v1.1: silhouette edges only (outer side,
                continue                                                     #   front edge); no back-face surface bumps
            add_tuft("arm." + s, C_ + rad * r_, rad)
    for m in range(8):                                            # tufted hand ends (pointing down)
        th = 2 * math.pi * m / 8
        dn = unit([0.55 * math.cos(th), 0.55 * math.sin(th), -1.0])
        add_tuft("arm." + s, P_[-1] + dn * ARM_R[-1] * 0.95, dn, length=TUFT_LEN * 0.85, flow=0.4)
    for m in range(4):                                            # shoulder fringe (outer top of the arm)
        th = math.radians(-50 + 33 * m)
        rad = unit([sg * math.cos(th), math.sin(th), 0.25])
        add_tuft("arm." + s, P_[0] + np.array([0, 0, -0.15]) + rad * ARM_R[0] * 1.05, rad, length=TUFT_LEN * 0.8)
    a_, b_, r0_, r1_ = LEG[s]["thigh"]
    for zf, nm_ in ((0.62, 9), (0.86, 9)):                        # leg fringe
        for m in range(nm_):
            th = 2 * math.pi * (m + 0.5 * (zf > 0.7)) / nm_
            rad = np.array([math.cos(th), math.sin(th), 0.0])
            if rad[0] * sg < -0.5:                                # (the inner leg faces the other leg)
                continue
            C_ = a_ + (b_ - a_) * ((a_[2] - zf) / (a_[2] - b_[2]))
            add_tuft("leg." + s, C_ + rad * (r0_ + (r1_ - r0_) * (a_[2] - zf) / (a_[2] - b_[2])), rad,
                     length=TUFT_LEN * 0.75)
for zr, n_, skip in ((1.08, 18, 0.95), (1.40, 16, 0.80)):   # lower-body fringe rings (v1.1: the upper-back ring read as warts)
    yc_, ry_, rx_ = [float(v[0]) for v in egg_at(np.array([zr]))]
    for m in range(n_):
        th = 2 * math.pi * (m + 0.5) / n_                         # azimuth from the front
        if abs(math.cos(th)) > skip and math.cos(th) > 0:          # the belly front stays smooth (painterly patch)
            continue
        if zr > 3.0 and (math.cos(th) > -0.15 and math.cos(th) < 0.98 and abs(math.sin(th)) < 0.55):
            continue
        S_ = np.array([rx_ * math.sin(th), yc_ - ry_ * math.cos(th), zr])
        n_v = unit([math.sin(th) / max(rx_, 1e-3), -math.cos(th) / max(ry_, 1e-3), 0.15])
        add_tuft("torso", S_, n_v, length=TUFT_LEN * (0.7 if zr > 3 else 0.9))
TUFT_GROUPS = sorted({t[0] for t in TUFTS})


def tufts_sdf(P, group):
    return cones_sdf(P, [(a, b, r1, r2) for g_, a, b, r1, r2 in TUFTS if g_ == group]) if any(
        t[0] == group for t in TUFTS) else np.full(len(P), 1e9)


GROUPS = ["torso", "arm.L", "arm.R", "leg.L", "leg.R", "tail"]
GROUP_REGION = {"torso": "fur", "arm.L": "fur", "arm.R": "fur", "leg.L": "fur", "leg.R": "fur", "tail": "tail"}


def group_sdf(P, tufts=True):
    cols = []
    for g_ in GROUPS:
        if g_ == "torso":
            d = np.minimum(sd_egg(P), sd_muzzle(P))
        elif g_.startswith("arm"):
            d = cones_sdf(P, ARM_CONES[g_[-1]])
        elif g_.startswith("leg"):
            d = sd_leg(P, g_[-1])
        else:
            d = sd_tail(P)
        if tufts:
            d = np.minimum(d, tufts_sdf(P, g_))
        cols.append(d)
    return np.stack(cols, 1)


g = SD.Grid((-1.95, -0.85, -0.05), (1.95, 3.40, 5.10), SDF_STEP, SDF_BAND)
g.apply(sd_egg, (-1.4, -0.75, 0.75), (1.4, 1.9, 5.05), 0.0)
g.apply(sd_muzzle, np.array(MUZZLE_C) - MUZZLE_R, np.array(MUZZLE_C) + MUZZLE_R, MUZZLE_K)
for s, _ in SIDES:
    lo_, hi_ = cones_box([LEG[s]["thigh"]])
    g.apply(lambda P, s=s: sd_leg(P, s, ("thigh",)), lo_, hi_, LEG_K)
    g.apply(lambda P, s=s: sd_leg(P, s, ("foot",)), LEG[s]["foot"] - FOOT_R, LEG[s]["foot"] + FOOT_R, FOOT_K)
    for c, r in LEG[s]["toes"]:
        g.apply(lambda P, c=c, r=r: SD.sd_sphere(P, c, r), c - r, c + r, TOE_K)
    lo_, hi_ = cones_box(ARM_CONES[s])
    g.apply(lambda P, s=s: cones_sdf(P, ARM_CONES[s]), lo_, hi_, ARM_K)
g.apply(lambda P: SD.sd_ellipsoid(P, TC, TR_ * TAIL_CORE), TC - TR_, TC + TR_, TAIL_K)
for c, r, k_c in CLUMPS:
    g.apply(lambda P, c=c, r=r: SD.sd_sphere(P, c, r), c - r, c + r, k_c)
for g_, a, b, r1, r2 in TUFTS:
    g.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2),
            np.minimum(a, b) - r1, np.maximum(a, b) + r1, TUFT_K)
g.floor(0.0)
# zero-crossings AT a grid corner make coincident marching-tets vertices + zero-area slivers; Blender's collapse decimation
# of the ~0.8 M-tri high then degenerates into a hull (measured 2026-10-09: low bbox 1.36 x 4.45 u vs the high's 1.80 x 5.01,
# high->low p99 1.09 u). Pushing |F| < CORNER_CLAMP x step off zero (surface shift <= 0.0025 u) keeps every crossing
# strictly inside its edge: manifold high, clean decimation (a merge-by-distance weld instead broke manifoldness: 93 open).
_ce = CORNER_CLAMP * SDF_STEP
g.F = np.where(np.abs(g.F) < _ce, np.where(g.F < 0, -_ce, _ce), g.F)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF), "high_verts": len(HV),
                 "tufts": {gg: sum(1 for t in TUFTS if t[0] == gg) for gg in TUFT_GROUPS}, "tail_bulges": len(CLUMPS),
                 "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation
t = time.time()

tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
_fk = [tuple(sorted(f)) for f in LF]
_fc = {}
for k_ in _fk:
    _fc[k_] = _fc.get(k_, 0) + 1
LF = [f for f, k_ in zip(LF, _fk) if _fc[k_] == 1]            # collapse leftovers: doubled faces (a zero-volume fold)
DOUBLED = int(sum(c for c in _fc.values() if c > 1))
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "seconds": round(time.time() - t, 1)}
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))


# =========================================================================== 3. region fields + iso cuts on the shell
def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


def belly_fields(P):
    """-> (field: > 0 inside the belly patch, = 1 - e / rho with the scalloped top edge; lw: > 0 on the upper arc where
    the pale line runs). e = elliptical distance in (body azimuth / BELLY_AZ, height / half-extent)."""
    yc, ry, rx = egg_at(P[:, 2])
    az = np.arctan2(P[:, 0] / np.maximum(rx, 1e-3), -(P[:, 1] - yc) / np.maximum(ry, 1e-3))
    a = az / BELLY_AZ
    z = P[:, 2]
    b = (z - BELLY_ZC) / np.where(z > BELLY_ZC, BELLY_ZT - BELLY_ZC, BELLY_ZC - BELLY_ZB)
    e = np.hypot(a, b)
    phi = np.arctan2(b, a)
    tt = (phi - SCALLOP_ARC) / (math.pi - 2 * SCALLOP_ARC)
    fr = (tt * SCALLOPS) % 1.0
    bump = np.sqrt(np.clip(1.0 - (2.0 * fr - 1.0) ** 2, 0.0, 1.0))
    win = (tt >= 0) & (tt <= 1)
    rho = 1.0 + SCALLOP_A * bump * win
    return 1.0 - e / rho, np.sin(phi) - math.sin(SCALLOP_ARC)


DGR = group_sdf(LV)
FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
FIELDS["belly"], FIELDS["lw"] = belly_fields(LV)
FIELDS["z"] = LV[:, 2].copy()
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None):
    L = LAY[key]
    eps = 1e-5 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < CUT_SNAP:
                a[L] = tau
            elif tt > 1 - CUT_SNAP:
                b[L] = tau
    cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
    pairs = []
    for f in bm.faces:
        vs = list(f.verts)
        sides = [side(v) for v in vs]
        if not (1 in sides and -1 in sides):
            continue
        cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
        if len(cv) == 2:
            pairs.append(cv)
    for cv in pairs:
        bmesh.ops.connect_verts(bm, verts=cv)
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts), "face_connects": len(pairs)}


TOL = 0.01
in_torso = lambda a, b: a[LAY["f_torso"]] > -TOL and b[LAY["f_torso"]] > -TOL
in_line = lambda a, b: in_torso(a, b) and a[LAY["lw"]] > -0.08 and b[LAY["lw"]] > -0.08
near_edge = lambda a, b: in_torso(a, b) and -0.04 < a[LAY["belly"]] < LINE_W + 0.04 and -0.04 < b[LAY["belly"]] < LINE_W + 0.04
CUTS = [("f_" + gname, 0.0, None) for gname in GROUPS if gname != "torso"]
CUTS += [("belly", 0.0, in_torso), ("belly", LINE_W, in_line), ("lw", 0.0, near_edge)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
owner = np.argmax(np.stack([FVAL["f_" + gname] for gname in GROUPS], 1), 1)
shell_reg = np.array([GROUP_REGION[GROUPS[o]] for o in owner], dtype=object)
is_torso = owner == GROUPS.index("torso")
shell_reg[is_torso & (FVAL["belly"] > 0)] = "belly"
shell_reg[is_torso & (FVAL["belly"] > 0) & (FVAL["belly"] < LINE_W) & (FVAL["lw"] > 0)] = "belly_line"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts: ears + face decals
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=6.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


def project(pts, n):
    return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])


def face_anchor(az_deg, z):
    """horizontal ray from outside toward the head axis at azimuth az (0 = front, + toward +X) -> surface point + a
    smoothed normal (mean of a small ring of hits)."""
    az = math.radians(az_deg)
    dvec = np.array([math.sin(az), -math.cos(az), 0.0])
    ax = np.array([0.0, HEAD_AXIS_Y, z])
    S, _ = cast(ax + dvec * 3.0, -dvec)
    ns = []
    for da, dz in ((0, 0), (4, 0), (-4, 0), (0, 0.04), (0, -0.04)):
        a2 = math.radians(az_deg + da)
        d2 = np.array([math.sin(a2), -math.cos(a2), 0.0])
        ns.append(cast(np.array([0.0, HEAD_AXIS_Y, z + dz]) + d2 * 3.0, -d2)[1])
    return S, unit(np.mean(ns, 0))


PARTS = []
FACE = {}
for s, sg in SIDES:
    S, n = face_anchor(sg * EYE_AZ, EYE_Z)
    V, F, R = FP.dome_decal(S, n, EYE_R, project, DECAL_UP, DECAL_DN, DECAL_DOME * 1.2, 4, 20, "eye")
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": n.round(4).tolist()}
for k, (az, z, r) in enumerate(TEETH):
    for s, sg in (SIDES if az > 0 else (("C", 1.0),)):
        S, n = face_anchor(sg * az, z)
        V, F, R = FP.dome_decal(S, n, r, project, DECAL_UP, DECAL_DN, TOOTH_DOME, 3, 10, "mouth", squash=(1.0, 1.1))
        PARTS.append(FP.Part("tooth.%d.%s" % (k, s), V, F, R, bone="head"))
EAR = {}
for s, sg in SIDES:
    bx, by = sg * EAR_BASE[0], EAR_BASE[1]
    Sb, _ = cast((bx, by, 6.5), (0, 0, -1), 3.0)
    tip = mir(EAR_TIP, sg)
    ax = unit(tip - Sb)
    root = Sb - ax * EAR_SINK
    mid = 0.5 * (root + tip) + np.array([0.0, EAR_BOW, 0.0])
    tt = np.linspace(0, 1, EAR_ST)[:, None]
    spine = (1 - tt) ** 2 * root + 2 * (1 - tt) * tt * mid + tt ** 2 * tip
    al = SD.arclen(spine); sf = al / al[-1]
    shape = np.sin(math.pi * np.clip(sf, 0, 1) ** 0.75)
    shape[-1] = 0.0
    hw_l = EAR_W * shape * FP.soft_fringe(sf, 0.28, 0.95, EAR_FRINGE[0], EAR_FRINGE[1], EAR_FRINGE[2], 11.0 + sg)
    hw_r = EAR_W * shape * FP.soft_fringe(sf, 0.28, 0.95, EAR_FRINGE[0], EAR_FRINGE[1], EAR_FRINGE[2], 23.0 + sg)
    ht = EAR_T * np.sin(math.pi * np.clip(sf, 0, 1) ** 0.8) ** 0.75
    yaw = math.radians(EAR_YAW)
    V, F, R, S_, C_ = FP.leaf(spine, (sg * math.sin(yaw), -math.cos(yaw), 0.0), hw_l, hw_r, ht, EAR_RING, cup=EAR_CUP)
    p = FP.Part("ear." + s, V, F, R)
    s_surf = float(np.linalg.norm(Sb - root))
    p.chain = ("ear." + s, S_ * al[-1], al[-1], "head", 2, s_surf)
    PARTS.append(p)
    EAR[s] = {"spine": C_, "length": float(al[-1]), "surface": Sb, "root": root, "tip": tip}

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, RANGE = [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    nv_ += len(V)
VALL = np.vstack(VALL)
HV_s = HV - SHIFT
REG = ["fur", "ear", "belly", "belly_line", "tail", "mouth", "eye"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.96 + 0.08 * j


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                  # opaque closed body: single-sided (glTF doubleSided false)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


def glow_check(pal):
    """artist B3 (2026-10-09): a SMALL glow on the eye + grin only; every other region zero; values <= the 2.0 phone cap."""
    lit = {n: (v.get("emission"), v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    peak = {n: round(max(PAL.srgb_to_linear(e)) * sc * float(pal["material"].get("emission_strength", 1.0)), 4)
            for n, (e, sc) in lit.items()}
    return {"skin": pal["skin"], "glowing_regions": sorted(lit), "emission_scale": {n: sc for n, (e, sc) in lit.items()},
            "peak_linear_x_strength": peak, "only_eye_mouth": sorted(lit) == ["eye", "mouth"],
            "under_phone_cap_2.0": all(v <= 2.0 for v in peak.values()), "low_tier_le_0.5": all(v <= 0.5 for v in peak.values())}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
report["glow"] = glow_check(pal_default)
assert report["glow"]["only_eye_mouth"] and report["glow"]["under_phone_cap_2.0"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, jitter(VALL, FALL))
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the muzzle tip (midline)
_hz = 4.40
HEAD_CTR = np.array([0.0, float(egg_at(np.array([_hz]))[0][0]), _hz])
MUZZLE_TIP = np.array([0.0, MUZZLE_C[1] - MUZZLE_R[1], MUZZLE_C[2]])
anchor = HEAD_CTR - SHIFT
landmark = MUZZLE_TIP - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (z 4.40 on the egg axis) -> muzzle tip on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE

# ---- flat + UV
low.data.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

tris_main = tri_count_F(FALL)
_part_tris = {}
for p in PARTS:
    key = p.name.split(".")[0] + ("s" if p.name.startswith("tooth") else "")
    key = {"eye": "eyes", "ear": "ears", "tooths": "grin_teeth"}.get(key, key)
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The fur is where the budget goes: the shell "
                            "carries the tufted silhouette (arms, hand ends, legs, lower body, tail fluff) and the bake "
                            "carries the tufts at full density into the normal map." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, ears, decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
_eg_w = 2 * float(ERX.max()); _eg_d = 2 * float(ERY.max())
_body_band = (CV[:, 2] > 1.2) & (CV[:, 2] < 3.4) & (CV[:, 1] < 1.95)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1)},
                     "front_width_vs_side_depth": {
                         "egg_width": round(_eg_w, 4), "egg_depth": round(_eg_d, 4), "egg_ratio": round(_eg_w / _eg_d, 4),
                         "shell_width_z1.2_3.4_incl_arms": round(float(np.ptp(CV[_body_band, 0])), 4),
                         "shell_depth_z1.2_3.4_no_tail": round(float(np.ptp(CV[_body_band, 1])), 4),
                         "rule": "front width = the BACK view's width (true ortho, mirrored; artist 'thick bunny')"},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
report["measure"]["front_width_vs_side_depth"]["shell_ratio"] = round(
    report["measure"]["front_width_vs_side_depth"]["shell_width_z1.2_3.4_incl_arms"] /
    report["measure"]["front_width_vs_side_depth"]["shell_depth_z1.2_3.4_no_tail"], 4)
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "regular"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch: design/reference/shadowbun-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_ear_pts = np.vstack([VALL[RANGE["ear." + s][0]:RANGE["ear." + s][1]] for s in ("L", "R")])
_tail_pts = np.array([c for c, r, _ in CLUMPS]) - SHIFT
_tail_lo = _tail_pts.min(0) - 0.3; _tail_hi = _tail_pts.max(0) + 0.3
low["conquest_focus"] = json.dumps({
    "face": [(np.array([0.0, -0.75, 3.70]) - SHIFT).tolist(), (np.array([0.95, 0.75, 4.80]) - SHIFT).tolist()],
    "ear": [_ear_pts.min(0).tolist(), _ear_pts.max(0).tolist()],
    "tail": [_tail_lo.tolist(), _tail_hi.tolist()],
    "belly": [(np.array([-1.1, -0.9, 1.0]) - SHIFT).tolist(), (np.array([1.1, 0.2, 3.45]) - SHIFT).tolist()],
    "views": {"face": [90.0, 5.0], "ear": [35.0, 12.0], "tail": [145.0, 10.0], "belly": [-20.0, 5.0]},
    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "face")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
for name, V, F, R, p in ISL[1:]:
    PH_V.append(V - SHIFT); PH_F += [[i + o_ for i in f] for f in F]; o_ += len(V)
HIGH = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.cycles.seed = 0
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
nt_ = MAT_BODY.node_tree
tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
nf = len(me.polygons)
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
ls_ = np.concatenate([[0], np.cumsum(lt_)[:-1]])
UVl = UVn.reshape(-1, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for fi in np.nonzero(mask_faces)[0]:
        poly = UVl[ls_[fi]:ls_[fi] + lt_[fi]] * res
        for j in range(1, len(poly) - 1):
            p = np.array([poly[0], poly[j], poly[j + 1]])
            x0_, y0_ = np.floor(p.min(0)).astype(int); x1_, y1_ = np.ceil(p.max(0)).astype(int)
            xs, ys = np.meshgrid(np.arange(max(x0_, 0), min(x1_, res)), np.arange(max(y0_, 0), min(y1_, res)))
            if xs.size == 0:
                continue
            q = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
            a, b, c = p

            def ed(u_, v_):
                return (v_[0] - u_[0]) * (q[:, 1] - u_[1]) - (v_[1] - u_[1]) * (q[:, 0] - u_[0])
            d1, d2, d3 = ed(a, b), ed(b, c), ed(c, a)
            ins = ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
            m[ys.ravel()[ins], xs.ravel()[ins]] = True
    return m.ravel()


tb = time.time()
for o in scene.objects:
    o.select_set(o is HIGH or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt_.nodes.active = node
    scene.cycles.samples = samples
    t = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
me.shade_flat()
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
body_faces = np.ones(nf, bool)
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets, every fur tuft at full density) + every closed part",
    "body_uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_body_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & alltex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_body_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_normal_twin.npy" if DIGEST_ONLY else "_normal_main.npy")), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_ao_twin.npy" if DIGEST_ONLY else "_ao_main.npy")), pa[:, :1])
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
bsdf = nt_.nodes["Principled BSDF"]
mul = nt_.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt_.links.new(nt_.nodes["col"].outputs["Color"], ia); nt_.links.new(ta.outputs["Color"], ib)
nt_.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt_.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt_.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt_.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGH.data
bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


def geometry_digest(obs):
    h = hashlib.sha256()
    for o in obs:
        me_ = o.data
        co = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", co)
        lv = np.empty(len(me_.loops), dtype=np.int64); me_.loops.foreach_get("vertex_index", lv)
        h.update(np.round(co, 6).astype(np.float32).tobytes()); h.update(lv.tobytes())
        for nm in ("Col", "Glow"):
            cd = np.empty(len(me_.loops) * 4, dtype=np.float32); me_.color_attributes[nm].data.foreach_get("color", cd)
            h.update(np.round(cd, 5).tobytes())
        uv = np.empty(len(me_.loops) * 2); me_.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


DIG["geometry_colour_uv"] = geometry_digest([low])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "pixel-sampled from the sheet's chips + drawn surfaces (see the palette notes)"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 7. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


_yc = lambda z: float(egg_at(np.array([z]))[0][0])
TAIL_SPINE = np.array([[0.0, 1.50, 1.90], [0.0, TAIL_C[1], TAIL_C[2] + 0.02], [0.0, TAIL_C[1] + TAIL_R[1], TAIL_C[2] + 0.05]])
BONES = [("hips", P_((0, _yc(0.85), 0.85)), P_((0, _yc(1.25), 1.25)), "root"),
         ("body", P_((0, _yc(1.25), 1.25)), P_((0, _yc(3.45), 3.45)), "hips"),
         ("head", P_((0, _yc(3.45), 3.45)), P_((0, _yc(4.95), 4.95)), "body")]
for s, sg in SIDES:
    A_ = np.array(ARM_PTS[s]); al = SD.arclen(A_)
    mid = np.array([np.interp(al[-1] / 2, al, A_[:, k]) for k in range(3)])
    end = A_[-1] - unit(A_[-1] - A_[-2]) * 0.0 + np.array([0, 0, -ARM_R[-1]])
    BONES += [("arm.%s.0" % s, P_(A_[0]), P_(mid), "body"), ("arm.%s.1" % s, P_(mid), P_(end), "arm.%s.0" % s)]
    th_ = LEG[s]["thigh"]
    toe = np.array([sg * FOOT_C[0], TOE_Y - 0.12, 0.22])
    BONES += [("leg.%s.0" % s, P_(th_[0]), P_(th_[1]), "hips"), ("leg.%s.1" % s, P_(th_[1]), P_(toe), "leg.%s.0" % s)]
BONES += [("tail.0", P_(TAIL_SPINE[0]), P_(TAIL_SPINE[1]), "hips"), ("tail.1", P_(TAIL_SPINE[1]), P_(TAIL_SPINE[2]), "tail.0")]
for s, _ in SIDES:
    C_ = EAR[s]["spine"]; al = SD.arclen(C_)
    pts = [np.array([np.interp(x, al, C_[:, k]) for k in range(3)]) for x in np.linspace(0, al[-1], 3)]
    BONES += [("ear.%s.0" % s, P_(pts[0]), P_(pts[1]), "head"), ("ear.%s.1" % s, P_(pts[1]), P_(pts[2]), "ear.%s.0" % s)]
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.4); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_); e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = nm.endswith(".1") or nm == "body"
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
GD = group_sdf(Pw)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
zz = Pw[:, 2]
Ws = np.zeros((b_ - a_, len(DEFORM)))
wh = smoothstep(HEAD_BAND[0], HEAD_BAND[1], zz)
wl = smoothstep(HIP_BAND[1], HIP_BAND[0], zz)
gi = GROUPS.index("torso")
Ws[:, J["head"]] += Wg[:, gi] * wh
Ws[:, J["hips"]] += Wg[:, gi] * wl
Ws[:, J["body"]] += Wg[:, gi] * np.clip(1.0 - wh - wl, 0, None)
LIMB_W = {}
for s, _ in SIDES:
    gi = GROUPS.index("arm." + s)
    _, s_arc, _ = FP.spine_param(Pw, np.array(ARM_PTS[s]))
    L_ = float(SD.arclen(np.array(ARM_PTS[s]))[-1])
    Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, 2)
    Ws[:, J["body"]] += Wg[:, gi] * Wv[:, 0]
    Ws[:, J["arm.%s.0" % s]] += Wg[:, gi] * Wv[:, 1]
    Ws[:, J["arm.%s.1" % s]] += Wg[:, gi] * Wv[:, 2]
    gi = GROUPS.index("leg." + s)
    wf = smoothstep(FOOT_BAND[1], FOOT_BAND[0], zz)
    wt = smoothstep(LEG_TOP[0], LEG_TOP[1], zz)
    Ws[:, J["leg.%s.1" % s]] += Wg[:, gi] * wf
    Ws[:, J["leg.%s.0" % s]] += Wg[:, gi] * (1 - wf) * (1 - wt)
    Ws[:, J["hips"]] += Wg[:, gi] * (1 - wf) * wt
    LIMB_W["arm." + s] = {"length": round(L_, 4), "dominant_verts": int((np.argmax(Wg, 1) == GROUPS.index("arm." + s)).sum())}
gi = GROUPS.index("tail")
_, s_arc, _ = FP.spine_param(Pw, TAIL_SPINE)
L_ = float(SD.arclen(TAIL_SPINE)[-1])
Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, 2)
Ws[:, J["hips"]] += Wg[:, gi] * Wv[:, 0]
Ws[:, J["tail.0"]] += Wg[:, gi] * Wv[:, 1]
Ws[:, J["tail.1"]] += Wg[:, gi] * Wv[:, 2]
Wt[a_:b_] = Ws
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if p.chain:
        chain, s_arc, L_, parent, nb, s0 = p.chain
        Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, nb, parent_s0=s0)
        Wt[a_:b_, J[parent]] += Wv[:, 0]
        for k in range(nb):
            Wt[a_:b_, J["%s.%d" % (chain, k)]] += Wv[:, k + 1]
    else:
        Wt[a_:b_, J[p.bone]] = 1.0
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx_ = np.argsort(-Wt, 1, kind="stable")[:, 4:]
    np.put_along_axis(Wt, idx_, 0.0, 1)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "limbs": LIMB_W,
                  "rule": "shell: soft SDF ownership (tau %.2f) between the egg, arms, legs, tail; the egg share runs hips "
                          "(below HIP_BAND) -> body -> head (above HEAD_BAND) by wide smoothsteps (no seam at the head band); "
                          "arms / tail: rigkit.vine_weights along their spines (roots blend into body / hips); legs: foot "
                          "below FOOT_BAND, thigh, top blending into hips; ears: vine_weights from the head surface; "
                          "eyes + grin decals rigid on head" % OWN_TAU}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame with no action bound (pass 1, floor
#      guard lifting the hips if any vertex dips below z 0), keyed from the stored channels (pass 2, frame N+1 = frame 1),
#      measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def world_rot(rots):
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
    return Rw


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ world_rot(rots) @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


def wrapd(u, u0):
    d = (u - u0) % 1.0
    return d - 1.0 if d > 0.5 else d


def hang(s):
    """one step's float envelope in [0, 1]: zero value AND zero slope at both ends (eased landings), flat near the top
    (hang time)."""
    g_ = math.sin(math.pi * (s % 1.0)) ** 2
    return 1.0 - (1.0 - g_) ** 2


def step_of(u):
    k = int(2.0 * (u % 1.0)) % 2
    return k, (2.0 * (u % 1.0)) % 1.0, (1.0 if k == 0 else -1.0)


def rock(u):
    """hips roll (deg, + tips the top toward +X = onto the L foot) at loop phase u"""
    k, s, sg = step_of(u)
    return WADDLE_ROLL * sg * hang(s)


FOOT_HALF_X = FOOT_R[0] * math.sqrt(max(0.0, 1.0 - (FOOT_C[2] / FOOT_R[2]) ** 2))   # the sole's half-width on the floor
PIVOT_X = FOOT_C[0] + FOOT_HALF_X                                              # the stance foot's outer sole edge


def idle_pose(u):
    P = {}
    br = math.sin(TAU * 2 * u)
    P["body"] = (None, [("Y", IDLE_SWAY[0] * math.sin(TAU * u + 0.4))], (1 + BREATH * br, 1 + 0.5 * BREATH * br, 1 + BREATH * br))
    P["head"] = (None, [("X", IDLE_SWAY[1] * math.sin(TAU * 2 * u - 0.9)), ("Y", -0.6 * IDLE_SWAY[0] * math.sin(TAU * u + 0.1))], None)
    for s, sg in SIDES:
        ph = 0.0 if s == "L" else 2.1
        u0, amp = EAR_TWITCH[s]
        pulse = math.exp(-(wrapd(u, u0) / TWITCH_W) ** 2)
        for k in range(2):
            bn = "ear.%s.%d" % (s, k)
            sway = K.vine_wave(u, k, 2, EAR_IDLE[0], EAR_IDLE[1], EAR_IDLE[3], EAR_IDLE[2], ph)
            tw = amp * (0.6 + 0.4 * k) * pulse * math.sin(TAU * TWITCH_HZ * u - 0.8 * k)
            P[bn] = (None, [("X", sway + tw), ("Y", sg * (0.5 * sway + 0.5 * tw))], None)
        P["arm.%s.0" % s] = (None, [("X", ARM_IDLE[0] * math.sin(TAU * u + ph)), ("Y", sg * ARM_IDLE[1] * math.sin(TAU * 2 * u + ph + 0.7))], None)
        P["arm.%s.1" % s] = (None, [("X", 0.6 * ARM_IDLE[0] * math.sin(TAU * u + ph - 0.7))], None)
    for k in range(2):
        bn = "tail.%d" % k
        wag = K.vine_wave(u, k, 2, TAIL_IDLE[0], TAIL_IDLE[1], TAIL_IDLE[3], TAIL_IDLE[2], 0.3)
        sc = None
        if k == 1:
            s_ = 1.0 + TAIL_PUFF * math.sin(TAU * 2 * u + 1.2)
            sc = (s_, s_, s_)
        P[bn] = (None, [("Z", wag), ("X", 0.5 * K.vine_wave(u, k, 2, 1.5, 3.0, 0.7, 1, 1.0))], sc)
    return P, {"lift": 0.0, "roll": 0.0}


def walk_pose(u):
    """FLOATY WADDLE (artist B1): two steps per loop. Step k rocks the hips over the stance foot's OUTER sole edge
    (k 0 = L stance, the R foot lifts), eased into and out of each landing with hang time at the top; the upper body
    floats up and drifts down (lagging), half-undoes the rock, twists toward the step; the swing leg reaches forward;
    ears, tail and arms trail the rock."""
    k, s, sg = step_of(u)
    phi = rock(u)
    piv = np.array([sg * PIVOT_X, FOOT_C[1], 0.0]) - SHIFT
    Rw = np.array(world_rot([("Y", phi)]))
    h = REST_H["hips"]
    off = Rw @ (h - piv) + piv - h
    P = {"hips": (off, [("Y", phi)], None)}
    lift_e = hang(s - WADDLE_LIFT_LAG)
    land = math.sin(math.pi * min(1.0, ((s - WADDLE_LIFT_LAG) % 1.0) / 0.3)) ** 2 if ((s - WADDLE_LIFT_LAG) % 1.0) < 0.3 else 0.0
    sy = 1.0 + WADDLE_STRETCH * lift_e - WADDLE_SQUASH * land
    sxz = 1.0 / math.sqrt(sy)
    P["body"] = ((0.0, 0.0, WADDLE_LIFT * lift_e), [("Y", -WADDLE_COUNTER * phi), ("Z", -sg * WADDLE_YAW * hang(s))],
                 (sxz, sy, sxz))
    P["head"] = (None, [("Y", -0.25 * phi), ("X", 2.0 * lift_e - 1.0)], None)
    swing = "R" if k == 0 else "L"
    for s_, sg_ in SIDES:
        P["leg.%s.0" % s_] = (None, [("X", -WADDLE_SWING * hang(s))] if s_ == swing else [], None)
        P["leg.%s.1" % s_] = (None, [("X", 0.5 * WADDLE_SWING * hang(s))] if s_ == swing else [], None)
        for kb in range(2):
            lag = rock(u - ARM_TRAIL[1] * (kb + 1))
            fa = ARM_TRAIL[2] * (sg_ * sg) * hang(s - ARM_TRAIL[1] * (kb + 1)) * (1.0 if kb == 0 else 0.5)
            P["arm.%s.%d" % (s_, kb)] = (None, [("Y", -ARM_TRAIL[0] * (0.5 if kb else 1.0) * (lag - (phi if kb == 0 else 0.0))
                                                 * 0.6), ("X", fa)], None)
        for kb in range(2):
            lag = rock(u - EAR_TRAIL[1] * (kb + 1))
            fl = EAR_FLOAT * (hang(s - WADDLE_LIFT_LAG - EAR_TRAIL[1] * (kb + 1)) - 0.5) * (0.6 + 0.4 * kb)
            P["ear.%s.%d" % (s_, kb)] = (None, [("Y", -EAR_TRAIL[0] * (lag - phi * (1.0 - WADDLE_COUNTER - 0.25)) * 0.5),
                                                ("X", fl)], None)
    for kb in range(2):
        lag = rock(u - TAIL_TRAIL[1] * (kb + 1))
        P["tail.%d" % kb] = (None, [("Z", TAIL_TRAIL[0] * lag * (0.7 + 0.3 * kb)), ("X", 2.0 * hang(s - 0.1 * (kb + 1)) - 1.0)],
                             None)
    return P, {"lift": WADDLE_LIFT * lift_e, "roll": phi}


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def apply_pose(P, guard_z):
    for bn in KEY_BONES:
        pb = rig.pose.bones[bn]
        loc, rots, sc = P.get(bn, (None, [], None))
        dw = np.zeros(3) if loc is None else np.asarray(loc, float)
        if bn == "hips":
            dw = dw + np.array([0.0, 0.0, guard_z])
        pb.location = local_loc(bn, dw)
        pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
        pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)


def bake_clip(name, N, pose_fn):
    if rig.animation_data is not None:
        rig.animation_data.action = None
    frames, guard, info = [], [], []
    for f in range(N):
        P, inf = pose_fn(f / N)
        gz = 0.0
        for it in range(6):
            apply_pose(P, gz)
            bpy.context.view_layer.update()
            mz = float(eval_coords(low)[:, 2].min())
            if mz >= -1e-6:
                break
            gz += -mz + FLOOR_EPS
        frames.append({bn: (rig.pose.bones[bn].location.copy(), rig.pose.bones[bn].rotation_quaternion.copy(),
                            rig.pose.bones[bn].scale.copy()) for bn in KEY_BONES})
        guard.append(gz); info.append(inf)
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    rows = []
    for f in range(N + 1):
        fr = frames[f % N]
        for bn in KEY_BONES:
            pb = rig.pose.bones[bn]
            pb.location, pb.rotation_quaternion, pb.scale = fr[bn]
            for ch in ("location", "rotation_quaternion", "scale"):
                pb.keyframe_insert(ch, frame=f + 1)
            rows.append(list(fr[bn][0]) + list(fr[bn][1]) + list(fr[bn][2]))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["loop_seconds"] = N / K.FPS
    rig.animation_data.action = None
    return act, rows, guard, info


CLIPS = {"idle": (IDLE_N, idle_pose), "walk": (WALK_N, walk_pose)}
NEW_ACTS, key_rows, GUARD, INFO = {}, [], {}, {}
t_clip = time.time()
for cn, (N, fn) in CLIPS.items():
    NEW_ACTS[cn], rows, GUARD[cn], INFO[cn] = bake_clip(cn, N, fn)
    key_rows += rows
DIG["keys"] = sha(np.array(key_rows))
_dom = np.argmax(Wt, 1)
SOLE = {s: np.nonzero(_dom == J["leg.%s.1" % s])[0] for s, _ in SIDES}
samples, clip_rep = [], {}
for cn, (N, fn) in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    rows = []
    first = last = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        hy = pbs["hips"].matrix.col[2].xyz           # hips bone local Z: its roll shows as the X lean
        bh = pbs["body"].head
        rows.append({"minz": float(C[:, 2].min()), "sole_L": float(C[SOLE["L"], 2].min()), "sole_R": float(C[SOLE["R"], 2].min()),
                     "hips_roll": math.degrees(math.atan2(pbs["hips"].matrix.col[1].xyz.x, pbs["hips"].matrix.col[1].xyz.z)),
                     "body_z": float(bh.z), "head_z": float(pbs["head"].tail.z),
                     "ear_tip_L": list(pbs["ear.L.1"].tail), "tail_tip": list(pbs["tail.1"].tail),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - REST_H["body"][2]
    et = np.array([r["ear_tip_L"] for r in R_]); tt_ = np.array([r["tail_tip"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "sole_min_z": {"L": [round(float(g_("sole_L").min()), 4), round(float(g_("sole_L").max()), 4)],
                                   "R": [round(float(g_("sole_R").min()), 4), round(float(g_("sole_R").max()), 4)]},
                    "hips_roll_deg": [round(float(g_("hips_roll").min()), 3), round(float(g_("hips_roll").max()), 3)],
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "ear_tip_L_p2p_xyz": (et.max(0) - et.min(0)).round(4).tolist(),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist()}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["waddle"] = {"roll_deg": WADDLE_ROLL, "pivot": "the stance foot's outer sole edge (X +-%.3f)" % PIVOT_X,
                 "float_lift": WADDLE_LIFT, "counter_roll_share": WADDLE_COUNTER, "seconds": round(time.time() - t_clip, 1)}
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("shadowbun v1: root > hips > body > head > ear.{L,R}.{0,1}; body > arm.{L,R}.{0,1}; "
                       "hips > leg.{L,R}.{0,1} (1 = foot); hips > tail.{0,1}")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("idle = v1 per the approved plan (4 s: breathing, de-synced ear twitches, tail fluff, small "
                               "arm sway, feet planted); walk = the artist-chosen FLOATY WADDLE (B1, 2026-10-09), v1 "
                               "tuning (2 s: two rocking steps over each foot's outer edge, soft float + eased landings, "
                               "ears + tail trailing; in place, root still)")
low["conquest_locomotion"] = "walker: floaty waddle in place (the game glides the unit)"
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris": report["tris"]["total"], "seconds": round(time.time() - T0, 1)},
              open(DIGEST_ONLY, "w"), indent=1)
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 8. glb
for o in scene.objects:
    o.select_set(o in (rig, low))
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, NEW_ACTS["idle"])
t = time.time()
import export_glb as _EG; _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False, export_attributes=True)
rig.animation_data.action = None


def glb_carries(path):
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    prims = [(m.get("name"), p) for m in js.get("meshes", []) for p in m["primitives"]]
    return {"materials": [{"name": m.get("name"), "alphaMode": m.get("alphaMode", "OPAQUE"),
                           "doubleSided": m.get("doubleSided", False),
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "attributes": sorted(p["attributes"])} for n, p in prims],
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'shadowbun' (one opaque body material); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (artist: alternate colours later)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
