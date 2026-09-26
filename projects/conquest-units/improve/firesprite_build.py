"""Fire Sprite / Demon -- the MODEL (from-scratch Conquest unit, no source sculpt), one headless run.

    blender --background --factory-startup --python improve/firesprite_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-09-26 "NEW UNIT: Fire Sprite / Demon" + design/reference/firesprite-character-sheet.webp
(three-view: ornamental dark-charcoal crown with two horns + diamond boss, flame rising through its open top; gnarled
angular FIRE WAND; body "pure hellfire, fluid / flowing, no solid form"; glowing eye holes; palette two charcoals, two
oranges, dark red) + design/reference/firesprite-sketch.webp (the artist's drawing: SQUARE one-piece head+torso block wearing
the crown, jagged hole eyes + zigzag hole mouth, ragged flame arms, two flame legs). ONE binding deviation from the sheet
(artist verbatim): "make the body more square shape so the head and torso blend as one piece like this other reference".

SCOPE: the model only. Movement identity is unanswered (float vs hop; grounded vs hovering rest; flame-flow wildness;
casting = attack wave, deferred), so the only clip is a minimal flame-waver PLACEHOLDER named 'idle' and there is NO walk
clip (clip_names fails on the missing walk -- expected until the movement wave).

UNITS: 'sheet units' -- 1 unit = 100 px of the reference sheet's FRONT view (orthographic), floor z = 0 at the sheet's
foot line (y 605 px): z = (605 - y_px) / 100; x = (x_px - 225) / 100 (the character axis). Natural proportions; cell fit
is report-only (scale policy 2026-09-25). Design frame: -Y is the front, +X the unit's LEFT (L), -X its right (R, wand hand).

Pipeline:
  1. BODY = one SDF (magmoo_sdf banded grid + smooth unions): the tapered rounded BLOCK (head + torso one piece), ragged
     flame LICKS (tapered bezier cone chains) up the sides, rising off the back and hanging off the hem, two flame ARMS
     (the R hand grips the wand; the L hand ends in claw licks), two flame LEGS with toe licks -- all smooth-unioned into
     one fire mass (limbs FUSED, no gaps) -- minus the two angled jagged EYE pockets and the zigzag MOUTH pocket carved
     into the flat front (2D polygon prisms), clamped flat on the floor. Marching tets -> collapse decimation.
  2. BODY REGIONS on the low shell (iso-cut, clean colour lines): pocket walls / floors (glowing holes), licks + limb tips
     (dark red), the face shadow (the sheet's dark face under the crown, wavy lower edge), bright flame tongues rising
     from the hem round the block, the rest orange.
  3. BODY CORE: a smaller hot block inside (the translucent shell shows it through: brighter interior tier).
  4. CROWN: solid SDF: flared band hugging the block's top edge (open on top), trim ridges on both edges, a bevelled
     diamond boss front AND back with a raised inner diamond, two crescent horns. Regions by part ownership.
  5. CROWN FLAME: multi-tongue SDF flame rising through the open crown (outer + nested hot core), tips cut by arc.
  6. WAND (own object 'firesprite_wand', own bones): kinked angular shaft (straight cone runs, hard joints), broken twig
     nubs, a claw of prongs cradling the WAND FLAME (outer + core, same flame language).
  7. palette (palettes.py regions -> Col / Glow + Col alpha), Smart UV, bake normal + AO (wired into the crown only).
  8. RIG: root > body > crown > crown_flame.{0,1}; body > arm.{L,R}.{0,1,2}; arm.R.2 > wand > wand_flame; body >
     leg.{L,R}.{0,1}. PLACEHOLDER idle (flame waver + crown/wand flame flicker). glb export + variant skin.
"""
import bpy, sys, os, math, json, time, hashlib, ast, tempfile
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
import firesprite_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, floor z = 0 at the foot line)
UNIT = "firesprite"
CHAR_ID = "firesprite"                # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit (see report 'tier_rationale')
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
NODE_EPS = 0.10                       # SDF grid values closer than this x step to zero are pushed off (no node crossings)
# ---- body block (the sketch's square one-piece head + torso)
BLOCK_HALF = (0.58, 0.40)             # "body width / depth" (half; sheet face 1.1 wide, band 1.25)
BLOCK_Z = (0.95, 2.98)                # "body bottom / top" (the hips / the crown seat)
BLOCK_TAPER = 0.90                    # "hem narrowing": hem half-width as a fraction of the top's
BLOCK_ROUND = 0.15                    # "corner rounding" of the block
BODY_STEP, BODY_BAND = 0.016, 0.06    # "body surface finish": SDF step
BODY_TRIS = 7200                      # "body mesh detail": decimation target of the fire shell
# ---- flame licks on the block: sides (per side: z, y, reach out, rise, root radius), back (x, root z, tip dx, tip z, r),
#      hem (azimuth deg from the front, hang length, root radius)
SIDE_LICKS = [(1.16, -0.10, 0.30, 0.48, 0.14), (1.50, 0.14, 0.28, 0.54, 0.13), (2.28, -0.08, 0.26, 0.50, 0.12),
              (2.60, 0.14, 0.22, 0.44, 0.11)]
SIDE_R_DZ = 0.07                      # the right side's licks sit this much higher (no mirror twin look)
BACK_LICKS = [(-0.30, 2.15, -0.12, 3.25, 0.15), (0.04, 2.30, 0.06, 3.52, 0.17), (0.33, 2.10, 0.14, 3.18, 0.14)]
HEM_LICKS = [(40.0, 0.30, 0.12), (70.0, 0.40, 0.13), (100.0, 0.44, 0.13), (130.0, 0.34, 0.12), (160.0, 0.42, 0.13),
             (-165.0, 0.36, 0.12), (-135.0, 0.40, 0.13), (-105.0, 0.46, 0.13), (-72.0, 0.36, 0.12), (-42.0, 0.28, 0.11)]
LICK_K = 0.05                         # licks melt into the block over this (smooth union)
LICK_DEEP_T = 0.52                    # "dark-red lick tips": the outer part of every lick past this fraction
LICK_SHARP = 1.4                      # "lick tip whip": lick radius falls as (1 - u)^this (flame tongue, not a thorn)
# ---- arms (R = wand hand, -X; L = free claw hand, +X)
ARM_R = {"shoulder": (-0.50, -0.02, 2.02), "ctrl": (-0.96, -0.14, 2.00), "r": (0.17, 0.085), "hand_r": 0.11}
ARM_L = {"shoulder": (0.50, 0.0, 2.02), "ctrl": (0.98, 0.02, 1.96), "hand": (1.03, -0.06, 1.30), "r": (0.17, 0.09),
         "hand_r": 0.10}
ARM_LICKS = [(0.25, (0.14, 0.05, 0.32), 0.09), (0.50, (0.16, 0.0, 0.30), 0.085), (0.75, (0.12, -0.04, 0.24), 0.07)]   # (arc frac, tip offset (out, y,
                                      #   up), root radius) -- the ragged flames trailing up off each arm
CLAW = [((0.10, -0.06, -0.30), 0.070), ((0.0, -0.11, -0.34), 0.072), ((-0.08, 0.02, -0.26), 0.060)]   # L hand claw licks
ARM_K = 0.10
ARM_DEEP_T = {"L": 0.84, "R": 9.0}    # the free hand darkens at its tip; the wand hand stays orange (it grips)
# ---- legs
LEG = {"hip": (0.29, 0.0, 1.22), "ctrl": (0.35, -0.03, 0.55), "tip": (0.31, -0.06, -0.03), "r": (0.29, 0.05), "sharp": 0.85}
TOES = [((0.33, -0.05, 0.28), (0.50, -0.15, 0.04), 0.08), ((0.25, 0.06, 0.32), (0.17, 0.21, 0.06), 0.07)]
LEG_K = 0.18                          # legs melt into the block over this: the sketch's block SPLITS into two legs
LEG_DEEP_T = 0.70
# ---- face: glowing HOLES carved into the flat front (sketch: jagged eyes, zigzag mouth; sheet: angled eyes)
POCKET_DEPTH = 0.10                   # "hole depth"
EYE_L = [(0.08, 2.40), (0.40, 2.55), (0.42, 2.36), (0.36, 2.24), (0.31, 2.31), (0.24, 2.19), (0.19, 2.28), (0.12, 2.22),
         (0.08, 2.30)]                # "eye shape" (x, z) of the unit's LEFT eye: angry slant (inner corner low) + the
                                      #   sketch's jagged drips along the bottom; the right eye mirrors it
MOUTH_X, MOUTH_Z = 0.34, (1.74, 2.02)  # "mouth width (half) / bottom, top"
MOUTH_TEETH, MOUTH_TOOTH = (5, 4), 0.085   # "zigzag": teeth on the top / bottom edge, tooth depth
MOUTH_SAG = (0.06, 0.03)              # the mouth corners pinch in (top edge drops, bottom edge rises toward the ends)
# ---- colour zoning on the block
FACE_SHADOW = True                    # "dark face": the sheet's dark ember face under the crown (eyes + mouth inside it)
SH_Z0, SH_WAVE = 1.60, (0.07, 9.0, 0.6)   # its wavy lower edge (height, (amplitude, frequency, phase))
SH_THETA = 0.95                       # how far round the block it wraps (rad from the front centre)
TONGUES = [(0, 0.50, 9), (26, 0.66, 9), (-24, 0.60, 9), (52, 0.95, 11), (-55, 0.90, 11), (90, 1.45, 15),
           (-92, 1.38, 17), (125, 1.20, 15), (-128, 1.25, 15), (158, 1.50, 14), (-160, 1.35, 14), (180, 0.90, 12)]
                                      # "bright flame tongues" rising from the hem: (azimuth deg, height, half-width deg)
TONGUE_WOB = (0.10, 5.0)              # tongue wobble (rad amplitude, per-unit-height frequency)
SH_PEAKS = [(-0.47, 0.30, 0.07), (-0.22, 0.12, 0.05), (0.0, 0.10, 0.05), (0.24, 0.12, 0.05), (0.47, 0.26, 0.07)]
                                      # orange flame points licking UP into the dark face: (x, height, half-width)
SH_SIDE_WAVE = (0.12, 10.0)           # the face shadow's side edges wave too (rad amplitude, per-unit-height frequency)
POCKET_REC = 0.006                    # a hole face must sit this far behind the front plane (no rim-to-rim slivers)
# ---- hot core inside the block: a FLAME shape (base blob + rising tongues), seen through the translucent shell
CORE_BASE, CORE_BASE_R = (0.0, 0.0, 1.45), (0.36, 0.20, 0.34)
CORE_TONGUES = [([(0.0, 0.0, 0.10), (0.08, 0.0, 0.50), (-0.05, 0.0, 0.85), (0.02, 0.0, 1.10)], 0.20),
                ([(0.18, 0.0, 0.05), (0.30, 0.0, 0.35), (0.22, 0.0, 0.60), (0.30, 0.0, 0.80)], 0.13),
                ([(-0.18, 0.0, 0.05), (-0.30, 0.0, 0.32), (-0.22, 0.0, 0.55), (-0.30, 0.0, 0.74)], 0.13)]
CORE_STEP, CORE_TRIS = 0.02, 700
# ---- crown (solid, dark charcoal)
CROWN_Z = (2.62, 3.06)                # "band bottom / top"
CROWN_OFF, CROWN_T = 0.012, 0.07      # band gap off the block / thickness
CROWN_FLARE = 0.10                    # "band flare": outward lean per unit height
CROWN_TRIM = (0.045, 0.018)           # "edge trim" ridge height / extra thickness
BOSS = {"zc": 2.76, "a": 0.20, "b": 0.36, "proud": 0.075, "bevel": 0.35}   # "diamond boss" (half w, half h, stand-off)
GEM = {"scale": 0.50, "proud": 0.022, "bevel": 0.30}                       # the raised inner diamond
HORN = {"z": 2.95, "ctrl": (0.40, -0.02, 0.16), "tip": (0.16, -0.06, 0.80), "r": 0.13}   # "horns" (offsets from the root)
CROWN_STEP, CROWN_TRIS = 0.010, 2800
# ---- crown flame (rising through the open crown): base blob + tongues (root, ctrl, tip offsets from the base, radius)
CF_BASE, CF_BASE_R = (0.0, 0.02, 2.96), (0.46, 0.32, 0.24)
CF_TONGUES = [([(0.0, 0.0, 0.05), (0.10, -0.02, 0.45), (-0.06, 0.02, 0.85), (0.04, 0.04, 1.30)], 0.30),
              ([(0.20, 0.0, 0.02), (0.40, 0.0, 0.32), (0.34, 0.02, 0.60), (0.46, 0.03, 0.90)], 0.18),
              ([(-0.20, 0.0, 0.02), (-0.42, 0.02, 0.28), (-0.36, 0.04, 0.52), (-0.48, 0.04, 0.78)], 0.17),
              ([(0.0, 0.12, 0.02), (0.05, 0.30, 0.35), (-0.04, 0.36, 0.60), (0.02, 0.46, 0.85)], 0.16),
              ([(0.12, -0.12, 0.04), (0.26, -0.20, 0.28), (0.20, -0.18, 0.46), (0.28, -0.20, 0.62)], 0.11),
              ([(-0.14, -0.12, 0.04), (-0.30, -0.16, 0.24), (-0.22, -0.14, 0.40), (-0.30, -0.14, 0.54)], 0.10)]
                                      # "crown flame tongues": S-curve paths (offsets from the base) + root radius
FLAME_CORE = (0.55, 0.68, 3)          # "flame core": radius scale, length scale, tongues kept
FLAME_SHARP = 1.5                     # "flame tip whip": tongue radius falls as (1 - u)^this
FLAME_K = 0.10
FLAME_TIP_T = 0.60                    # "flame tips": the outer part of every tongue past this fraction (cooler red)
CF_STEP, CF_TRIS, CF_CORE_TRIS = 0.016, 1500, 450
# ---- wand (own object + bones): kinked shaft (t along the axis, offsets on the two perpendiculars, radius)
WAND_BOTTOM, WAND_TOP = (-0.72, -0.22, 0.10), (-1.20, -0.48, 3.02)   # sheet: butt near the floor, leaning out + forward
HAND_Z = 1.62                         # "grip height" (the R hand sits on the wand axis here)
WAND_KINKS = [(0.00, 0.0, 0.0, 0.018), (0.05, 0.010, 0.0, 0.040), (0.17, -0.035, 0.020, 0.052),
              (0.30, 0.030, -0.025, 0.056), (0.44, -0.030, 0.015, 0.058), (0.58, 0.035, 0.020, 0.056),
              (0.72, -0.025, -0.030, 0.058), (0.85, 0.030, 0.010, 0.062), (0.95, -0.010, 0.0, 0.070), (1.0, 0.0, 0.0, 0.066)]
WAND_NUBS = [(0.26, 60.0, 0.16, 0.034), (0.50, 200.0, 0.14, 0.030), (0.68, 320.0, 0.18, 0.034)]   # broken twig stubs
WAND_PRONGS = [(20.0, 0.25, 0.46), (110.0, 0.22, 0.40), (200.0, 0.26, 0.48), (290.0, 0.21, 0.38)]   # the claw round the
                                      #   flame: (azimuth deg, spread, height)
WAND_PRONG_R = 0.045
WAND_STEP, WAND_TRIS = 0.008, 1400
WF_LIFT, WF_BASE_R = 0.22, (0.14, 0.12, 0.12)   # wand flame: base above the shaft top, base blob radii
WF_TONGUES = [([(0.0, 0.0, 0.04), (0.07, -0.02, 0.36), (-0.05, 0.0, 0.66), (0.02, 0.0, 0.98)], 0.17),
              ([(0.10, 0.0, 0.02), (0.22, 0.0, 0.22), (0.17, 0.02, 0.40), (0.25, 0.02, 0.58)], 0.10),
              ([(-0.10, 0.0, 0.02), (-0.22, 0.02, 0.20), (-0.16, 0.0, 0.34), (-0.24, 0.0, 0.50)], 0.10),
              ([(0.0, 0.09, 0.02), (0.03, 0.18, 0.22), (-0.02, 0.20, 0.38), (0.03, 0.24, 0.54)], 0.09)]
WF_STEP, WF_TRIS, WF_CORE_TRIS = 0.012, 700, 250
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig + PLACEHOLDER idle
ARM_BONES, LEG_BONES, CF_BONES = 3, 2, 2
OWN_TAU = 0.05                        # limb / block weight blend softness (SDF ownership)
IDLE_N = 48                           # 2 s loop
BODY_SWAY = (1.0, 0.6)                # body waver deg (side, front-back)
ARM_WAVE = {"L": (2.0, 5.0), "R": (0.8, 1.6)}   # arm chain waver root / tip deg (the wand arm barely moves)
LEG_WAVE = (1.0, 2.5)
CF_WAVE, CF_PULSE = (3.0, 7.0), 0.05  # crown flame sway deg root/tip, stretch flicker (2 per loop)
WF_SWAY, WF_PULSE = 4.0, 0.06         # wand flame sway deg, stretch flicker (3 per loop)

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
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": "none: from scratch (sheet "
          "design/reference/firesprite-character-sheet.webp + sketch design/reference/firesprite-sketch.webp)",
          "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px of the sheet's front view; floor z = 0 at the foot line (sheet y 605)",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


unit = FP.unit


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


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

SDF_LOG, HIGH = {}, {}


def sdf_mesh(name, lo, hi, step, band, ops, floor=False):
    """ops: [(fn, lo, hi, k, mode)] -> the polygonised high (V, F) with crumbs dropped + audit."""
    t = time.time()
    g = FP.Grid(lo, hi, step, band)
    for fn, a, b, k, mode in ops:
        g.apply(fn, a, b, k, mode)
    if floor:
        g.floor(0.0)
    exact0 = int((g.F == 0.0).sum())
    # crossings ON a grid node make coincident vertices -> zero-area triangles that stall the collapse decimator
    # (first preview: body 9885, crown 38705 of them -> junk lows). Push |F| < NODE_EPS * step off the node; the surface
    # moves by at most that (0.1 step = 1.6 mm on the body).
    eps_ = NODE_EPS * step
    near0 = np.abs(g.F) < eps_
    g.F = np.where(near0, np.where(g.F < 0.0, -eps_, eps_), g.F)
    V, T = SD.polygonise(g)
    F = [list(map(int, f)) for f in T]
    V, F, crumbs = FP.keep_islands(V, F, 0.02)
    Ta = np.asarray(F)
    area = 0.5 * np.linalg.norm(np.cross(V[Ta[:, 1]] - V[Ta[:, 0]], V[Ta[:, 2]] - V[Ta[:, 0]]), axis=1)
    e = np.sort(np.vstack([Ta[:, [0, 1]], Ta[:, [1, 2]], Ta[:, [2, 0]]]), 1)
    _, ec = np.unique(e, axis=0, return_counts=True)
    SDF_LOG[name] = {"step": step, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(F), "high_verts": len(V),
                     "crumbs": crumbs, "open_edges": int((ec == 1).sum()), "nonmanifold_edges": int((ec > 2).sum()),
                     "zero_area_tris": int((area < 1e-12).sum()), "grid_exact_zero": exact0,
                     "nodes_pushed_off_surface": int(near0.sum()),
                     "seconds": round(time.time() - t, 1)}
    HIGH[name] = (V, F)
    return V, F


RETOPO = {}


def lowpoly(name, V, F, target):
    t = time.time()
    tmp = new_obj("dec_" + name, V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / len(F))
    LV, LF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF, specks = FP.keep_islands(LV, LF, 0.01)
    bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
    hs = np.arange(0, len(V), max(1, len(V) // 15000))
    dev2 = np.array([bvh_lo.find_nearest(Vector(V[i]))[3] for i in hs])
    RETOPO[name] = {"target": target, "tris": tri_count_F(LF), "specks": specks, "open_edges": FP.open_edges(LF),
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "seconds": round(time.time() - t, 1)}
    return LV, LF


def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where column j owns the surface


# =========================================================================== 1. BODY: block + licks + limbs - face holes
BW, BD = BLOCK_HALF
ZB0, ZB1 = BLOCK_Z
HZ, ZC = (ZB1 - ZB0) / 2.0, (ZB1 + ZB0) / 2.0
FACE_Y = -BD


def hx_of(z):
    return BW * (BLOCK_TAPER + (1.0 - BLOCK_TAPER) * np.clip((np.asarray(z, float) - ZB0) / (ZB1 - ZB0), 0.0, 1.0))


def sd_block(P):
    half = np.stack([hx_of(P[:, 2]), np.full(len(P), BD), np.full(len(P), HZ)], 1)
    return FP.sd_round_box(P, (0.0, 0.0, ZC), half, BLOCK_ROUND)


def theta_of(P):
    """azimuth round the block, 0 at the front centre (-Y), +pi/2 at the unit's left side (+X)."""
    return np.arctan2(P[:, 0] / hx_of(P[:, 2]), -P[:, 1] / BD)


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---- the elements (every flame lick / limb is a tapered cone chain with a spine: ownership, dark tips, weights)
EL = []


def add_el(name, group, cones_pts, deep_t, k):
    cones, pts = cones_pts
    EL.append({"name": name, "group": group, "cones": cones, "spine": np.asarray(pts, float), "deep_t": deep_t, "k": k})


for side, sg in (("L", 1.0), ("R", -1.0)):
    for i, (z, y, reach, rise, r0) in enumerate(SIDE_LICKS):
        z_ = z + (0.0 if side == "L" else SIDE_R_DZ)
        y_ = y if side == "L" else -0.6 * y
        root = np.array([sg * (float(hx_of(z_)) - 0.06), y_, z_])
        path = [root, root + np.array([sg * reach * 0.6, 0.0, rise * 0.12]),
                root + np.array([sg * reach * 0.95, 0.025, rise * 0.5]), root + np.array([sg * reach * 0.7, 0.05, rise])]
        add_el("lick.side.%s.%d" % (side, i), "block", FP.lick_path(path, r0, n=12, rtip=0.012, sharp=LICK_SHARP),
               LICK_DEEP_T, LICK_K)
for i, (x, z0, dx, z1, r0) in enumerate(BACK_LICKS):
    path = [(x, BD - 0.06, z0), (x + 0.3 * dx, BD + 0.22, z0 + 0.3 * (z1 - z0)), (x + 0.8 * dx, BD + 0.30, z0 + 0.65 * (z1 - z0)),
            (x + dx, BD + 0.40, z1)]
    add_el("lick.back.%d" % i, "block", FP.lick_path(path, r0, n=12, rtip=0.012, sharp=LICK_SHARP), LICK_DEEP_T, LICK_K)
for i, (az, ln, r0) in enumerate(HEM_LICKS):
    d = np.array([math.sin(math.radians(az)), -math.cos(math.radians(az))])
    hxb = float(hx_of(ZB0))
    s_ = 1.0 / max(abs(d[0]) / hxb, abs(d[1]) / BD)
    root = np.array([d[0] * s_ * 0.90, d[1] * s_ * 0.90, ZB0 + 0.14])
    out3, perp = np.array([d[0], d[1], 0.0]), np.array([-d[1], d[0], 0.0]) * (1.0 if i % 2 else -1.0)
    path = [root, root + out3 * 0.10 + np.array([0, 0, -0.30 * ln]),
            root + out3 * 0.05 + perp * 0.05 + np.array([0, 0, -0.65 * ln]), root + out3 * 0.14 - perp * 0.02 + np.array([0, 0, -ln])]
    add_el("lick.hem.%d" % i, "block", FP.lick_path(path, r0, n=10, rtip=0.012, sharp=LICK_SHARP), LICK_DEEP_T, LICK_K)

# wand axis first (the R hand sits on it)
WB, WT = np.array(WAND_BOTTOM, float), np.array(WAND_TOP, float)
WAX = unit(WT - WB)
HAND_T = (HAND_Z - WB[2]) / (WT[2] - WB[2])
HAND_R_PT = WB + HAND_T * (WT - WB)
ARM_SPINE = {}
for side, sg, A in (("R", -1.0, ARM_R), ("L", 1.0, ARM_L)):
    sh_ = np.array(A["shoulder"], float)
    hand = HAND_R_PT if side == "R" else np.array(A["hand"], float)
    cones, pts = FP.lick(sh_, np.array(A["ctrl"], float), hand, A["r"][0], n=12, rtip=A["r"][1], sharp=1.0)
    hb = hand + np.array([0.0, 0.0, 0.012])
    cones = cones + [(hand, hb, A["hand_r"], A["hand_r"])]
    EL.append({"name": "arm." + side, "group": "arm." + side, "cones": cones, "spine": pts, "deep_t": ARM_DEEP_T[side],
               "k": ARM_K})
    ARM_SPINE[side] = pts
    al = SD.arclen(pts)
    for j, (fr, off, r0) in enumerate(ARM_LICKS):
        root = np.array([np.interp(fr * al[-1], al, pts[:, k]) for k in range(3)])
        o_ = np.array([sg * off[0], off[1], off[2]])
        path = [root, root + o_ * np.array([0.5, 0.3, 0.2]), root + o_ * np.array([0.9, 0.6, 0.6]), root + o_ * np.array([0.8, 1.0, 1.0])]
        add_el("lick.arm.%s.%d" % (side, j), "arm." + side, FP.lick_path(path, r0, n=10, rtip=0.01, sharp=LICK_SHARP),
               LICK_DEEP_T, ARM_K * 0.5)
    if side == "L":
        for j, (off, r0) in enumerate(CLAW):
            tip = hand + np.array(off)
            ctrl = hand + np.array(off) * np.array([0.5, 0.5, 0.45])
            add_el("claw.L.%d" % j, "arm.L", FP.lick(hand, ctrl, tip, r0, n=8, rtip=0.01, sharp=1.0), 0.45, 0.04)
LEG_SPINE = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    m = np.array([sg, 1.0, 1.0])
    cones, pts = FP.lick(np.array(LEG["hip"]) * m, np.array(LEG["ctrl"]) * m, np.array(LEG["tip"]) * m, LEG["r"][0], n=12,
                         rtip=LEG["r"][1], sharp=LEG["sharp"])
    EL.append({"name": "leg." + side, "group": "leg." + side, "cones": cones, "spine": pts, "deep_t": LEG_DEEP_T, "k": LEG_K})
    LEG_SPINE[side] = pts
    for j, (root, tip, r0) in enumerate(TOES):
        root = np.array(root) * m; tip = np.array(tip) * m
        add_el("toe.%s.%d" % (side, j), "leg." + side, FP.lick(root, 0.5 * (root + tip) + np.array([0, 0, 0.04]), tip, r0,
                                                                n=7, rtip=0.01, sharp=1.0), 0.40, 0.05)


def mouth_outline():
    W = MOUTH_X
    z0, z1 = MOUTH_Z
    nt, nb = MOUTH_TEETH
    top = []
    for k, x in enumerate(np.linspace(-W, W, 2 * nt + 1)):
        top.append((x, z1 - MOUTH_SAG[0] * (x / W) ** 2 - (MOUTH_TOOTH if k % 2 else 0.0)))
    bot = []
    for k, x in enumerate(np.linspace(W, -W, 2 * nb + 1)):
        bot.append((x, z0 + MOUTH_SAG[1] * (x / W) ** 2 + (MOUTH_TOOTH if k % 2 else 0.0)))
    return np.array(top + bot)


EYE_Lp = np.array(EYE_L, float)
EYE_Rp = (EYE_Lp * np.array([-1.0, 1.0]))[::-1]
MOUTH = mouth_outline()
HOLES = {"eye.L": EYE_Lp, "eye.R": EYE_Rp, "mouth": MOUTH}


def sd_holes2d(P):
    Q = P[:, [0, 2]]
    return np.minimum.reduce([FP.sd_poly2d(Q, V) for V in HOLES.values()])


def sd_pockets(P):
    return np.maximum(sd_holes2d(P), P[:, 1] - (FACE_Y + POCKET_DEPTH))


t_body = time.time()
_boxes = [FP.cones_box(e["cones"]) for e in EL]
BODY_LO = np.minimum.reduce([b[0] for b in _boxes] + [np.array([-BW, -BD, 0.0])]) - 0.06
BODY_HI = np.maximum.reduce([b[1] for b in _boxes] + [np.array([BW, BD, ZB1])]) + 0.06
BODY_LO[2] = -0.06
ops = [(sd_block, (-BW, -BD, ZB0), (BW, BD, ZB1), 0.0, "union")]
for e in EL:
    lo_, hi_ = FP.cones_box(e["cones"])
    ops.append((lambda P, c=e["cones"]: FP.cones_sdf(P, c), lo_, hi_, e["k"], "union"))
_hl = np.vstack(list(HOLES.values()))
ops.append((sd_pockets, (_hl[:, 0].min() - 0.02, -BD - 0.30, _hl[:, 1].min() - 0.02),
            (_hl[:, 0].max() + 0.02, FACE_Y + POCKET_DEPTH + 0.02, _hl[:, 1].max() + 0.02), 0.006, "subtract"))
HV_body, HF_body = sdf_mesh("body", BODY_LO, BODY_HI, BODY_STEP, BODY_BAND, ops, floor=True)
LV, LF = lowpoly("body", HV_body, HF_body, BODY_TRIS)

# ---- body region fields on the low shell
GROUPS = ["block", "arm.L", "arm.R", "leg.L", "leg.R"]


def group_sdf(P):
    """(n, len(GROUPS)) SDF per weight group (the block group = the block + its side / back / hem licks)."""
    D = np.full((len(P), len(GROUPS)), 1e9)
    D[:, 0] = sd_block(P)
    for e in EL:
        j = GROUPS.index(e["group"])
        D[:, j] = np.minimum(D[:, j], FP.cones_sdf(P, e["cones"]))
    return D


def element_fields(P):
    """owner element (block = -1) -> deep = arc fraction past the element's dark-tip threshold (block: -1)."""
    D = np.stack([sd_block(P)] + [FP.cones_sdf(P, e["cones"]) for e in EL], 1)
    own = np.argmin(D, 1)
    deep = np.full(len(P), -1.0)
    for j, e in enumerate(EL):
        m = own == j + 1
        if m.any():
            t_, _, _ = FP.spine_param(P[m], e["spine"])
            deep[m] = t_ - e["deep_t"]
    own_block = np.delete(D, 0, axis=1).min(1) - D[:, 0]
    return deep, own_block


def tongue_field(P):
    th = theta_of(P); z = P[:, 2]
    best = np.full(len(P), -1.0)
    for i, (deg, h, wdeg) in enumerate(TONGUES):
        t = (z - ZB0) / h
        c = math.radians(deg) + TONGUE_WOB[0] * np.sin(TONGUE_WOB[1] * z + 1.7 * i) * np.clip(t, 0, 1)
        w = math.radians(wdeg) * np.clip(1.0 - t, 0.0, 1.0) ** 0.8
        best = np.maximum(best, w - np.abs(wrap(th - c)) - 1e-3)
    return best


def shadow_field(P):
    x = P[:, 0]
    zl = SH_Z0 + SH_WAVE[0] * np.sin(SH_WAVE[1] * x + SH_WAVE[2])
    for xc, h, w in SH_PEAKS:
        zl = zl + h * np.clip(1.0 - np.abs(x - xc) / w, 0.0, 1.0) ** 1.5
    th_edge = SH_THETA + SH_SIDE_WAVE[0] * np.sin(SH_SIDE_WAVE[1] * P[:, 2])
    return np.minimum(P[:, 2] - zl, 0.5 * (th_edge - np.abs(theta_of(P))))


deep, own_block = element_fields(LV)
FIELDS = {"f_pocket": -sd_pockets(LV) - sd_block(LV), "y_pocket": LV[:, 1] - (FACE_Y + POCKET_DEPTH * 0.45),
          "y_rec": LV[:, 1] - (FACE_Y + POCKET_REC),
          "deep": deep, "own_block": own_block, "tongue": tongue_field(LV), "shadow": shadow_field(LV)}
IC = FP.IsoCutter(LV, LF, FIELDS, CUT_SNAP)
TOL = 0.01
cut_log = [IC.cut("f_pocket", 0.0), IC.cut("y_pocket", 0.0, IC.gate_pos("f_pocket", TOL)), IC.cut("deep", 0.0)]
if FACE_SHADOW:
    cut_log.append(IC.cut("shadow", 0.0, IC.gate_pos("own_block", TOL)))
cut_log.append(IC.cut("tongue", 0.0, IC.gate_pos("own_block", TOL)))
SV, SF, FV = IC.finish()
sreg = np.array(["fire"] * len(SF), dtype=object)
blk = FV["own_block"] > 0
sreg[blk & (FV["tongue"] > 0)] = "fire_bright"
if FACE_SHADOW:
    sreg[blk & (FV["shadow"] > 0)] = "fire_shadow"
sreg[FV["deep"] > 0] = "fire_deep"
pk = (FV["f_pocket"] > 0) & (FV["y_rec"] > 0)
sreg[pk] = "hole_wall"
sreg[pk & (FV["y_pocket"] > 0)] = "hole_glow"
report["body"] = {"cuts": cut_log, "shell_tris_after_cuts": len(SF), "elements": len(EL),
                  "limb_join": "FUSED: arms (k %.2f) and legs (k %.2f) smooth-union into the block -- one fire mass, no gaps"
                               % (ARM_K, LEG_K), "seconds": round(time.time() - t_body, 1)}

# =========================================================================== 2. body core (hot interior)
_cb, _cr = np.array(CORE_BASE), np.array(CORE_BASE_R)
_cels = [FP.lick_path([_cb + np.array(q) for q in path], r0, n=12, rtip=0.01, sharp=1.5)[0] for path, r0 in CORE_TONGUES]
_clo = np.minimum.reduce([FP.cones_box(c)[0] for c in _cels] + [_cb - _cr]) - 0.03
_chi = np.maximum.reduce([FP.cones_box(c)[1] for c in _cels] + [_cb + _cr]) + 0.03
HV_core, HF_core = sdf_mesh("core", _clo, _chi, CORE_STEP, 0.08,
                            [(lambda P: SD.sd_ellipsoid(P, _cb, _cr), _cb - _cr, _cb + _cr, 0.0, "union")] +
                            [(lambda P, c=c: FP.cones_sdf(P, c), *FP.cones_box(c), 0.10, "union") for c in _cels])
CV_core, CF_core = lowpoly("core", HV_core, HF_core, CORE_TRIS)

# =========================================================================== 3. crown (solid)
CZ0, CZ1 = CROWN_Z
HXT = float(hx_of(CZ0))


def band_off(z):
    return CROWN_OFF + CROWN_FLARE * (np.asarray(z, float) - CZ0)


def plan_d(P):
    return FP.sd_round_rect(P[:, :2], (HXT, BD), BLOCK_ROUND)


def sd_band(P):
    o = band_off(P[:, 2])
    shell = np.abs(plan_d(P) - o - CROWN_T / 2) - CROWN_T / 2
    return np.maximum(shell, np.maximum(CZ0 - P[:, 2], P[:, 2] - CZ1))


def sd_trim(P):
    o = band_off(P[:, 2])
    shell = np.abs(plan_d(P) - o - CROWN_T / 2) - (CROWN_T / 2 + CROWN_TRIM[1])
    h = CROWN_TRIM[0]
    top = np.maximum(CZ1 - h - P[:, 2], P[:, 2] - CZ1)
    bot = np.maximum(CZ0 - P[:, 2], P[:, 2] - (CZ0 + h))
    return np.maximum(shell, np.minimum(top, bot))


BOSS_Y = BD + float(band_off(BOSS["zc"])) + CROWN_T          # the band's outer face at the boss height


def sd_boss(P):
    return np.minimum(*[FP.sd_diamond(P, (0.0, f * BOSS_Y, BOSS["zc"]), BOSS["a"], BOSS["b"], BOSS["proud"], BOSS["bevel"], f)
                        for f in (-1.0, 1.0)])


def sd_gem(P):
    s = GEM["scale"]
    a, b = BOSS["a"] * s, BOSS["b"] * s
    return np.minimum(*[FP.sd_diamond(P, (0.0, f * (BOSS_Y + BOSS["proud"]), BOSS["zc"]), a, b, GEM["proud"], GEM["bevel"], f)
                        for f in (-1.0, 1.0)])


HORNS = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    root = np.array([sg * (HXT + float(band_off(HORN["z"])) + CROWN_T * 0.5), 0.0, HORN["z"]])
    m = np.array([sg, 1.0, 1.0])
    HORNS[side] = FP.lick(root, root + np.array(HORN["ctrl"]) * m, root + np.array(HORN["tip"]) * m, HORN["r"], n=14,
                          rtip=0.012, sharp=1.0)


def sd_horns(P):
    return np.minimum(FP.cones_sdf(P, HORNS["L"][0]), FP.cones_sdf(P, HORNS["R"][0]))


CROWN_PARTS = {"band": sd_band, "trim": sd_trim, "boss": sd_boss, "gem": sd_gem, "horn": sd_horns}
CROWN_REG = {"band": "crown", "trim": "crown_trim", "boss": "crown_boss", "gem": "crown_gem", "horn": "horn"}
_ro = HXT + float(band_off(CZ1)) + CROWN_T + CROWN_TRIM[1] + 0.02
_hb = [FP.cones_box(HORNS[s_][0]) for s_ in ("L", "R")]
CR_LO = np.minimum(np.array([-_ro, -(BD + _ro - HXT) - BOSS["proud"] - GEM["proud"], BOSS["zc"] - BOSS["b"]]), np.minimum(*[b[0] for b in _hb])) - 0.04
CR_HI = np.maximum(np.array([_ro, (BD + _ro - HXT) + BOSS["proud"] + GEM["proud"], CZ1]), np.maximum(*[b[1] for b in _hb])) + 0.04
_bb = (np.array([-_ro, -(BD + _ro - HXT), CZ0 - 0.01]), np.array([_ro, BD + _ro - HXT, CZ1 + 0.01]))
_bossb = (np.array([-BOSS["a"], -BOSS_Y - BOSS["proud"] - GEM["proud"], BOSS["zc"] - BOSS["b"]]),
          np.array([BOSS["a"], BOSS_Y + BOSS["proud"] + GEM["proud"], BOSS["zc"] + BOSS["b"]]))
crown_ops = [(sd_band, *_bb, 0.0, "union"), (sd_trim, *_bb, 0.004, "union"), (sd_boss, *_bossb, 0.008, "union"),
             (sd_gem, *_bossb, 0.003, "union"), (sd_horns, np.minimum(*[b[0] for b in _hb]), np.maximum(*[b[1] for b in _hb]),
                                                   0.02, "union")]
HV_crown, HF_crown = sdf_mesh("crown", CR_LO, CR_HI, CROWN_STEP, 0.04, crown_ops)
CRV, CRF = lowpoly("crown", HV_crown, HF_crown, CROWN_TRIS)
_names = list(CROWN_PARTS)
DCR = np.stack([CROWN_PARTS[n](CRV) for n in _names], 1)
IC = FP.IsoCutter(CRV, CRF, {"f_" + n: own_field(DCR, j) for j, n in enumerate(_names)}, CUT_SNAP)
crown_cuts = [IC.cut("f_" + n, 0.0) for n in _names if n != "band"]
CRV, CRF, FVc = IC.finish()
_own = np.argmax(np.stack([FVc["f_" + n] for n in _names], 1), 1)
creg = np.array([CROWN_REG[_names[o]] for o in _own], dtype=object)
report["crown"] = {"cuts": crown_cuts, "boss_face_y": round(BOSS_Y, 4), "band_z": list(CROWN_Z), "open_top": True}


# =========================================================================== 4. flames (crown flame; wand flame later)
def flame(name, base, base_r, tongues, step, tris, core_tris):
    """-> dict(outer=(V, F, regions), core=(V, F, regions)); tongue tips (arc past FLAME_TIP_T) = flame_tip."""
    base = np.asarray(base, float)
    out = {}
    for layer in ("outer", "core"):
        rs, ls, nk = (1.0, 1.0, len(tongues)) if layer == "outer" else FLAME_CORE
        br = np.array(base_r) * rs
        els = [FP.lick_path([base + np.array(p) * np.array([rs, rs, ls]) for p in path], r0 * rs, n=14, rtip=0.01,
                            sharp=FLAME_SHARP) for path, r0 in tongues[:nk]]
        lo = np.minimum.reduce([FP.cones_box(c)[0] for c, _ in els] + [base - br]) - 0.03
        hi = np.maximum.reduce([FP.cones_box(c)[1] for c, _ in els] + [base + br]) + 0.03
        ops_ = [(lambda P, br=br: SD.sd_ellipsoid(P, base, br), base - br, base + br, 0.0, "union")]
        ops_ += [(lambda P, c=c: FP.cones_sdf(P, c), *FP.cones_box(c), FLAME_K * rs, "union") for c, _ in els]
        HVf, HFf = sdf_mesh("%s.%s" % (name, layer), lo, hi, step * (1.0 if layer == "outer" else 1.25), 0.05, ops_)
        Vf, Ff = lowpoly("%s.%s" % (name, layer), HVf, HFf, tris if layer == "outer" else core_tris)
        if layer == "core":
            out[layer] = (Vf, Ff, ["flame_core"] * len(Ff))
            continue
        D = np.stack([SD.sd_ellipsoid(Vf, base, br)] + [FP.cones_sdf(Vf, c) for c, _ in els], 1)
        own = np.argmin(D, 1)
        tip = np.full(len(Vf), -1.0)
        for j, (c, pts) in enumerate(els):
            m_ = own == j + 1
            if m_.any():
                tip[m_] = FP.spine_param(Vf[m_], pts)[0] - FLAME_TIP_T
        ic = FP.IsoCutter(Vf, Ff, {"tip": tip}, CUT_SNAP)
        c_ = ic.cut("tip", 0.0)
        Vf, Ff, FVf = ic.finish()
        out[layer] = (Vf, Ff, list(np.where(FVf["tip"] > 0, "flame_tip", "flame_outer")))
        out["tip_cut"] = c_
        out["tongues"] = [pts for _, pts in els]
    return out


CFL = flame("crown_flame", CF_BASE, CF_BASE_R, CF_TONGUES, CF_STEP, CF_TRIS, CF_CORE_TRIS)

# =========================================================================== 5. wand (own object)
_p1 = unit(np.cross(WAX, [0.0, 0.0, 1.0]) if abs(WAX[2]) < 0.99 else [1.0, 0.0, 0.0])
_p2 = np.cross(WAX, _p1)


def wand_pt(t, o1=0.0, o2=0.0):
    return WB + t * (WT - WB) + o1 * _p1 + o2 * _p2


SHAFT = FP.chain([wand_pt(t, a, b) for t, a, b, _ in WAND_KINKS], [r for *_, r in WAND_KINKS])
NUBS = []
for t, az, ln, r0 in WAND_NUBS:
    root = wand_pt(t)
    d = unit(math.cos(math.radians(az)) * _p1 + math.sin(math.radians(az)) * _p2 + 0.9 * WAX)
    NUBS.append(FP.lick(root, root + d * ln * 0.5, root + d * ln, r0, n=5, rtip=0.008, sharp=1.0))
WTOP = wand_pt(WAND_KINKS[-1][0], WAND_KINKS[-1][1], WAND_KINKS[-1][2])
PRONGS = []
for az, spread, h in WAND_PRONGS:
    out_ = math.cos(math.radians(az)) * _p1 + math.sin(math.radians(az)) * _p2
    root = WTOP - WAX * 0.06
    PRONGS.append(FP.lick(root, root + out_ * spread + WAX * h * 0.35, root + out_ * spread * 0.40 + WAX * h,
                          WAND_PRONG_R, n=7, rtip=0.008, sharp=1.0))
WAND_PARTS = {"shaft": [SHAFT[0]], "prong": [c for c, _ in NUBS + PRONGS]}


def sd_wand_group(P, grp):
    return np.minimum.reduce([FP.cones_sdf(P, c) for c in WAND_PARTS[grp]])


_wc = SHAFT[0] + [x for c in WAND_PARTS["prong"] for x in c]
W_LO, W_HI = FP.cones_box(_wc, 0.03)
wand_ops = [(lambda P: FP.cones_sdf(P, SHAFT[0]), *FP.cones_box(SHAFT[0]), 0.0, "union")]
wand_ops += [(lambda P, c=c: FP.cones_sdf(P, c), *FP.cones_box(c), 0.006, "union") for c in WAND_PARTS["prong"]]
HV_wand, HF_wand = sdf_mesh("wand", W_LO, W_HI, WAND_STEP, 0.03, wand_ops)
WV, WF = lowpoly("wand", HV_wand, HF_wand, WAND_TRIS)
DW = np.stack([sd_wand_group(WV, "shaft"), sd_wand_group(WV, "prong")], 1)
IC = FP.IsoCutter(WV, WF, {"f_prong": own_field(DW, 1)}, CUT_SNAP)
wand_cut = IC.cut("f_prong", 0.0)
WV, WF, FVw = IC.finish()
wreg = list(np.where(FVw["f_prong"] > 0, "wand_prong", "wand_bark"))
WF_BASE = WTOP + np.array([0.0, 0.0, WF_LIFT])
WFL = flame("wand_flame", WF_BASE, WF_BASE_R, WF_TONGUES, WF_STEP, WF_TRIS, WF_CORE_TRIS)
report["wand"] = {"bottom": WB.tolist(), "top": WTOP.round(4).tolist(), "hand": HAND_R_PT.round(4).tolist(),
                  "length": round(float(np.linalg.norm(WTOP - WB)), 4), "prong_cut": wand_cut,
                  "rule": "own object 'firesprite_wand' + own bones (wand, wand_flame; wand parented to the R hand bone "
                          "arm.R.2): hide or swap the node"}

# =========================================================================== 6. assemble + centre
# main object: cores first (drawn before the translucent shells), then the shells, then the crown
MAIN = [("core", CV_core, CF_core, ["fire_core"] * len(CF_core), 0),
        ("crown_flame.core", *CFL["core"], 0),
        ("shell", SV, SF, list(sreg), 0),
        ("crown_flame.outer", *CFL["outer"], 0),
        ("crown", CRV, CRF, list(creg), 1)]
WAND = [("wand", WV, WF, wreg, 0), ("wand_flame.core", *WFL["core"], 1), ("wand_flame.outer", *WFL["outer"], 1)]
allV = np.vstack([p[1] for p in MAIN + WAND])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
report["body_axis_offset_from_origin"] = {"x": round(float(-SHIFT[0]), 4), "y": round(float(-SHIFT[1]), 4),
                                          "why": "the contract centres the WHOLE bbox (wand included) on the origin"}


def pack(pieces):
    V, F, R, M, RANGE = [], [], [], [], {}
    n = 0
    for name, V_, F_, R_, mi in pieces:
        RANGE[name] = (n, n + len(V_))
        V.append(np.asarray(V_) - SHIFT)
        F += [[i + n for i in f] for f in F_]
        R += list(R_); M += [mi] * len(F_)
        n += len(V_)
    return np.vstack(V), F, R, M, RANGE


VM, FM, RM, MM, RANGE_M = pack(MAIN)
VW, FW, RW, MW, RANGE_W = pack(WAND)
REG_M = ["fire_shadow", "fire_deep", "fire", "fire_bright", "fire_core", "hole_wall", "hole_glow", "flame_tip", "flame_outer",
         "flame_core", "crown", "crown_trim", "crown_boss", "crown_gem", "horn"]
REG_W = ["wand_bark", "wand_prong", "flame_tip", "flame_outer", "flame_core"]
assert set(RM) <= set(REG_M), sorted(set(RM) - set(REG_M))
assert set(RW) <= set(REG_W), sorted(set(RW) - set(REG_W))
rid_m = np.array([REG_M.index(r) for r in RM], dtype=np.int32)
rid_w = np.array([REG_W.index(r) for r in RW], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.95 + 0.10 * j


# ---- materials
def make_mat(name, kind):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    if kind == "fire":
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"          # Eevee: order-independent see-through (glTF alphaMode BLEND)
        mat.use_transparent_shadow = True
    mat.use_backface_culling = True                      # every piece is a closed solid: single-sided (glTF doubleSided false)
    return mat


def apply_alpha(mat, pal):
    n_ = mat.node_tree.nodes.get("alpha_factor")
    if n_ is not None:
        n_.inputs[1].default_value = float(pal["material"].get("alpha", 1.0))


def paint_alpha(me, pal):
    names, rid_, _ = PAL.read_regions(me)
    a_reg = np.array([float(pal["regions"][n].get("alpha", 1.0)) for n in names], np.float32)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ca = me.color_attributes["Col"]
    cd = np.empty(len(me.loops) * 4, dtype=np.float32); ca.data.foreach_get("color", cd)
    cd = cd.reshape(-1, 4); cd[:, 3] = np.repeat(a_reg[rid_], lt)
    ca.data.foreach_set("color", cd.ravel())
    return {n: float(a) for n, a in zip(names, a_reg)}


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        paint_alpha(o.data, pal)
    for mat_ in (MAT_FIRE, MAT_CROWN, MAT_WAND):
        PAL.apply_material(mat_, pal); apply_alpha(mat_, pal)
    return out


def srgb_lum(rgb):
    c = PAL.srgb_to_linear(rgb)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


GRADE_BODY = ["fire_shadow", "fire_deep", "fire", "fire_bright", "fire_core", "hole_wall", "hole_glow"]
GRADE_FLAME = ["flame_tip", "flame_outer", "flame_core"]
HOTTEST = ["hole_glow", "flame_core"]


def glow_tiers(pal, hue_window=None):
    """hellfire gate: along each grade (body: shadow < dark red < orange < bright orange < core < hole wall < hole floor;
    flame: tip < outer < core) BOTH the emission tier and the base-colour luminance rise strictly; the hottest regions
    (eye/mouth hole floors, flame cores) out-shine every other region; default skin: every fire hue in the red-orange-
    yellow window."""
    import colorsys
    R_ = pal["regions"]
    tier = {n: float(R_[n].get("emission_scale", 0.0)) for n in GRADE_BODY + GRADE_FLAME}
    lum = {n: round(srgb_lum(R_[n]["rgb"]), 4) for n in R_}
    ok = {}
    for gname, gl in (("body", GRADE_BODY), ("flame", GRADE_FLAME)):
        ok[gname + "_emission_rises"] = all(tier[a] < tier[b] for a, b in zip(gl, gl[1:]))
        ok[gname + "_luminance_rises"] = all(lum[a] < lum[b] for a, b in zip(gl, gl[1:]))
    others = [n for n in R_ if n not in HOTTEST]
    ok["hottest_outshine_all"] = min(lum[h] for h in HOTTEST) > max(lum[n] for n in others)
    hues = {}
    for n in GRADE_BODY + GRADE_FLAME:
        h_, s_, v_ = colorsys.rgb_to_hsv(*[c / 255.0 for c in R_[n]["rgb"]])
        hues[n] = round(h_ * 360.0, 1)
    if hue_window is not None:
        ok["hue_in_window"] = all(hue_window[0] <= h <= hue_window[1] for h in hues.values())
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "luminance": {n: lum[n] for n in GRADE_BODY + GRADE_FLAME},
            "hue_deg": hues, "hue_window": hue_window, "gates": ok, "pass": all(ok.values())}


MAT_FIRE = make_mat(UNIT + "_fire", "fire")
MAT_CROWN = make_mat(UNIT + "_crown", "solid")
MAT_WAND = make_mat(UNIT + "_wand", "solid")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default, (0.0, 60.0))}
assert report["glow_tiers"]["default"]["pass"], report["glow_tiers"]

low = new_obj(UNIT, VM, FM)
low.data.materials.append(MAT_FIRE); low.data.materials.append(MAT_CROWN)
low.data.polygons.foreach_set("material_index", np.array(MM, dtype=np.int32))
PAL.store_regions(low.data, REG_M, rid_m, jitter(VM, FM))
wand = new_obj(UNIT + "_wand", VW, FW)
wand.data.materials.append(MAT_WAND); wand.data.materials.append(MAT_FIRE)
wand.data.polygons.foreach_set("material_index", np.array(MW, dtype=np.int32))
PAL.store_regions(wand.data, REG_W, rid_w, jitter(VW, FW))
report["regions_faces"] = repaint([low, wand], pal_default)
report["alpha"] = {"main": paint_alpha(low.data, pal_default), "wand": paint_alpha(wand.data, pal_default),
                   "material_alpha": pal_default["material"].get("alpha", 1.0)}
low.data.update(); wand.data.update()
fa = np.empty(len(low.data.polygons)); low.data.polygons.foreach_get("area", fa)
report["regions_area_share_main"] = {n: round(float(fa[rid_m == j].sum() / fa.sum()), 4) for j, n in enumerate(REG_M)}
low.data["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE_M.items()})
wand.data["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE_W.items()})

# facing landmark: the crown centre -> the front diamond boss's proud face on the midline
anchor = np.array([0.0, 0.0, BOSS["zc"]]) - SHIFT
landmark = np.array([0.0, -(BOSS_Y + BOSS["proud"]), BOSS["zc"]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "crown centre -> front diamond boss face on the midline (the sheet's front boss; the eye "
                            "and mouth holes are on the same face)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

# ---- flat + UV (both objects)
for ob_ in (low, wand):
    ob_.data.shade_flat()
    bpy.context.view_layer.objects.active = ob_
    for o in scene.objects:
        o.select_set(o is ob_)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")

tris_m, tris_w = tri_count_F(FM), tri_count_F(FW)
report["tris"] = {"total": tris_m + tris_w, "main": tris_m, "wand_object": tris_w,
                  **{n: tri_count_F(F_) for n, _, F_, _, _ in MAIN + WAND}}
report["tier_rationale"] = ("REGULAR role (a line caster, not a boss or hero): window [%d, %d], the firefly's regular "
                            "tier. The sheet's read needs the ragged flame silhouette (licks, claw, toes), carved "
                            "jagged holes, an ornamental crown (trim, bevelled bosses, horns) and two multi-tongue "
                            "flames with nested cores -- ~2x crowd density, landing mid-window." % tuple(TRI_BUDGET))
report["sdf"] = SDF_LOG
report["retopo"] = RETOPO
report["open_edges"] = {n: FP.open_edges(F_) for n, _, F_, _, _ in MAIN + WAND}
lo_a = np.vstack([VM, VW]).min(0); hi_a = np.vstack([VM, VW]).max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1)},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
for ob_ in (low, wand):
    ob_["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "regular"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch: design/reference/firesprite-character-sheet.webp + firesprite-sketch.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; fire "
                                 "material (body shell, core, crown flame, wand flame) alphaMode BLEND single-sided; crown + "
                                 "wand opaque")
wand["conquest_toggle"] = "the wand (and its flame) is its own node on its own bones: hide or swap it"
# focus boxes for the close-ups (world, after the centre shift)
_face = np.vstack([np.c_[V_[:, 0], np.full(len(V_), FACE_Y), V_[:, 1]] for V_ in HOLES.values()])
_face = np.vstack([_face.min(0) - np.array([0.12, 0.35, 0.14]), _face.max(0) + np.array([0.12, 0.0, 0.12])]) - SHIFT
_crown = np.vstack([CRV.min(0), CRV.max(0), np.array([0.0, 0.0, CZ1 + 0.25])]) - SHIFT
_wt = np.vstack([wand_pt(0.62), WTOP + np.array([0.0, 0.0, 1.2])])
_wtop = np.vstack([_wt.min(0) - 0.12, _wt.max(0) + 0.12]) - SHIFT
low["conquest_focus"] = json.dumps({"face": [_face.min(0).tolist(), _face.max(0).tolist()],
                                    "crown": [_crown.min(0).tolist(), _crown.max(0).tolist()],
                                    "wand": [_wtop.min(0).tolist(), _wtop.max(0).tolist()],
                                    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o not in (low, wand):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share_main", "glow_tiers", "body")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 7. bake normal + AO (main object; crown wired)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F, o_ = [], [], 0
for nm in ("body", "core", "crown", "crown_flame.outer", "crown_flame.core"):
    V_, F_ = HIGH[nm]
    PH_V.append(V_ - SHIFT); PH_F += [[i + o_ for i in f] for f in F_]; o_ += len(V_)
HIGHO = new_obj(UNIT + "_high", np.vstack(PH_V), PH_F)
BAKE_CAGE = max(0.02, round(3.0 * max(RETOPO[n]["high_to_low_sampled"]["p99"] for n in ("crown", "body")), 4))
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
BAKE_NODES = {}
for mat_ in (MAT_FIRE, MAT_CROWN):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
wand.hide_render = True
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me = low.data
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
    o.select_set(o is HIGHO or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, 16)):
    for mat_ in (MAT_FIRE, MAT_CROWN):
        mat_.node_tree.nodes.active = BAKE_NODES[mat_.name][idx]
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
crown_faces = np.array(MM) == 1
tex_n = texels_of(crown_faces, RN)
tex_a = texels_of(crown_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance of the crown / body (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "every main-object SDF high (body, core, crown, crown flame outer + core)",
    "wired_into": "the crown material only (the fire keeps flat colour + glow + alpha; the wand is flat-shaded colour)",
    "crown_uv_texels_normal": int(tex_n.sum()),
    "normal_baked_pct_of_crown_texels": round(100 * float(cov_n[tex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & tex_n] > 0.05).mean()), 4),
    "ao_baked_pct_of_crown_texels": round(100 * float(cov_a[tex_a].mean()), 2),
    "ao_mean_crown": round(float(pa[cov_a & tex_a, 0].mean()), 4),
    "ao_p05_crown": round(float(np.percentile(pa[cov_a & tex_a, 0], 5)), 4)})
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
nt = MAT_CROWN.node_tree
tn, ta = BAKE_NODES[MAT_CROWN.name]
for n_ in BAKE_NODES[MAT_FIRE.name]:
    MAT_FIRE.node_tree.nodes.remove(n_)
bsdf = nt.nodes["Principled BSDF"]
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(nt.nodes["col"].outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGHO.data
bpy.data.objects.remove(HIGHO, do_unlink=True); bpy.data.meshes.remove(hm_)
wand.hide_render = False
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


DIG["geometry_colour_uv"] = geometry_digest([low, wand])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "the sheet's five palette chips (sampled: charcoal 65,55,56 / 81,72,72, orange 249,121,53 "
                                   "/ 250,143,61, dark red 160,45,28) + the sheet's pale-yellow eye glow and dark face"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 8. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


def resample(pts, n):
    al = SD.arclen(pts)
    return [np.array([np.interp(x, al, pts[:, k]) for k in range(3)]) for x in np.linspace(0, al[-1], n + 1)]


CF_TOP = float(max(p[-1][2] for p in CFL["tongues"]))
BONES = [("body", P_((0, 0, 1.0)), P_((0, 0, 2.6)), "root"),
         ("crown", P_((0, 0, BOSS["zc"])), P_((0, 0, CZ1 + 0.05)), "body")]
_cfz = np.linspace(CF_BASE[2], CF_TOP, CF_BONES + 1)
for k in range(CF_BONES):
    BONES.append(("crown_flame.%d" % k, P_((0, 0, _cfz[k])), P_((0, 0, _cfz[k + 1])), "crown" if k == 0 else "crown_flame.%d" % (k - 1)))
CHAIN_PTS = {}
for side in ("L", "R"):
    CHAIN_PTS["arm." + side] = resample(ARM_SPINE[side], ARM_BONES)
    CHAIN_PTS["leg." + side] = resample(LEG_SPINE[side], LEG_BONES)
for ch, pts in sorted(CHAIN_PTS.items()):
    for k in range(len(pts) - 1):
        BONES.append(("%s.%d" % (ch, k), P_(pts[k]), P_(pts[k + 1]), "body" if k == 0 else "%s.%d" % (ch, k - 1)))
WF_TOP = float(max(p[-1][2] for p in WFL["tongues"]))
BONES.append(("wand", P_(HAND_R_PT), P_(WTOP), "arm.R.%d" % (ARM_BONES - 1)))
BONES.append(("wand_flame", P_(WF_BASE), P_(np.array([WF_BASE[0], WF_BASE[1], WF_TOP])), "wand"))
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
    e.use_connect = nm[-1].isdigit() and not nm.endswith(".0")
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights, main object (<= 4 influences)
Wt = np.zeros((len(VM), len(DEFORM)))
a_, b_ = RANGE_M["shell"]
Pw = VM[a_:b_] + SHIFT                                   # back to the design frame
GD = group_sdf(Pw)
Wg = np.exp(-(GD - GD.min(1, keepdims=True)) / OWN_TAU)
Wg /= Wg.sum(1, keepdims=True)
Ws = np.zeros((b_ - a_, len(DEFORM)))
Ws[:, J["body"]] += Wg[:, 0]
LIMB_W = {}
for gi, grp in enumerate(GROUPS[1:], start=1):
    side = grp.split(".")[1]
    spine = ARM_SPINE[side] if grp.startswith("arm") else LEG_SPINE[side]
    nb = ARM_BONES if grp.startswith("arm") else LEG_BONES
    _, s_arc, _ = FP.spine_param(Pw, spine)
    L_ = float(SD.arclen(spine)[-1])
    Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, nb)
    Ws[:, J["body"]] += Wg[:, gi] * Wv[:, 0]
    for k in range(nb):
        Ws[:, J["%s.%d" % (grp, k)]] += Wg[:, gi] * Wv[:, k + 1]
    LIMB_W[grp] = {"length": round(L_, 4), "dominant_verts": int((np.argmax(Wg, 1) == gi).sum())}
Wt[a_:b_] = Ws
a_, b_ = RANGE_M["core"]; Wt[a_:b_, J["body"]] = 1.0
a_, b_ = RANGE_M["crown"]; Wt[a_:b_, J["crown"]] = 1.0
CF_L = CF_TOP - CF_BASE[2]
for nm in ("crown_flame.core", "crown_flame.outer"):
    a_, b_ = RANGE_M[nm]
    s_ = np.clip(VM[a_:b_, 2] + SHIFT[2] - CF_BASE[2], 0, CF_L)
    Wv = K.vine_weights(s_, CF_L, CF_BONES)
    Wt[a_:b_, J["crown"]] += Wv[:, 0]
    for k in range(CF_BONES):
        Wt[a_:b_, J["crown_flame.%d" % k]] += Wv[:, k + 1]
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
# wand object: the shaft rides 'wand', the flame 'wand_flame'
for n in ("wand", "wand_flame"):
    wand.vertex_groups.new(name=n)
a_, b_ = RANGE_W["wand"]
wand.vertex_groups["wand"].add(list(range(a_, b_)), 1.0, "REPLACE")
wand.vertex_groups["wand_flame"].add(list(range(RANGE_W["wand_flame.core"][0], RANGE_W["wand_flame.outer"][1])), 1.0, "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "limbs": LIMB_W, "wand_object": "shaft 100% 'wand', flame 100% 'wand_flame'",
                  "rule": "fire shell: soft SDF ownership (tau %.2f) between the block (+ its licks) and each limb; a "
                          "limb's share runs down its chain by rigkit.vine_weights arc-length hat weights (the root "
                          "blends into body); core -> body; crown -> crown (rigid); crown flame -> crown_flame chain by "
                          "height (root blends into crown)" % OWN_TAU}
for ob_ in (low, wand):
    ob_.parent = rig
    ob_.matrix_parent_inverse = Matrix.Identity(4)
    am = ob_.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- PLACEHOLDER idle: flame waver (closed-form, integer harmonics -> exact seam)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
act = bpy.data.actions.new("idle")
act.use_fake_user = True
K.assign_action(rig, act)
key_rows = []
TAU2 = 2 * math.pi
for f in range(IDLE_N + 1):
    u = (f % IDLE_N) / IDLE_N
    pose = {"body": (Quaternion((1, 0, 0), math.radians(BODY_SWAY[1] * math.sin(TAU2 * u + 1.1))) @
                     Quaternion((0, 0, 1), math.radians(BODY_SWAY[0] * math.sin(TAU2 * u))), None)}
    for side in ("L", "R"):
        for k in range(ARM_BONES):
            ang = K.vine_wave(u, k, ARM_BONES, ARM_WAVE[side][0], ARM_WAVE[side][1], 0.7, 1, phase=0.0 if side == "L" else 2.0)
            pose["arm.%s.%d" % (side, k)] = (Quaternion((1, 0, 0), math.radians(ang)), None)
        for k in range(LEG_BONES):
            ang = K.vine_wave(u, k, LEG_BONES, LEG_WAVE[0], LEG_WAVE[1], 0.6, 1, phase=0.9 if side == "L" else 2.6)
            pose["leg.%s.%d" % (side, k)] = (Quaternion((0, 0, 1), math.radians(ang)), None)
    for k in range(CF_BONES):
        a1 = K.vine_wave(u, k, CF_BONES, CF_WAVE[0], CF_WAVE[1], 0.9, 1, phase=0.3)
        a2 = K.vine_wave(u, k, CF_BONES, CF_WAVE[0] * 0.6, CF_WAVE[1] * 0.6, 0.9, 2, phase=1.4)
        sc = (1.0, 1.0 + CF_PULSE * math.sin(TAU2 * 2 * u + 0.5), 1.0) if k == 0 else None
        pose["crown_flame.%d" % k] = (Quaternion((1, 0, 0), math.radians(a1)) @ Quaternion((0, 0, 1), math.radians(a2)), sc)
    pose["wand_flame"] = (Quaternion((1, 0, 0), math.radians(WF_SWAY * math.sin(TAU2 * u + 0.4))) @
                          Quaternion((0, 0, 1), math.radians(0.6 * WF_SWAY * math.sin(TAU2 * 2 * u))),
                          (1.0, 1.0 + WF_PULSE * math.sin(TAU2 * 3 * u + 1.0), 1.0))
    for bn, (q, sc) in pose.items():
        pb = rig.pose.bones[bn]
        pb.rotation_quaternion = q
        pb.keyframe_insert("rotation_quaternion", frame=f + 1)
        if sc is not None:
            pb.scale = sc
            pb.keyframe_insert("scale", frame=f + 1)
        key_rows.append(list(pb.rotation_quaternion) + list(pb.scale))
for fc in K.action_fcurves(act):
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
act.use_frame_range = True
act.frame_start, act.frame_end = 1, IDLE_N + 1
act.use_cyclic = True
act["placeholder"] = "PLACEHOLDER idle (flame waver + crown / wand flame flicker): movement identity awaits the artist"
DIG["keys"] = sha(np.array(key_rows))


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


samples, minz, root_off = [], 1e9, 0.0
first = last = wfirst = wlast = None
for f in range(1, IDLE_N + 2):
    scene.frame_set(f)
    C = eval_coords(low); Cw = eval_coords(wand)
    samples.append(C[::7]); samples.append(Cw[::5])
    if f == 1:
        first, wfirst = C, Cw
    if f == IDLE_N + 1:
        last, wlast = C, Cw
    minz = min(minz, float(C[:, 2].min()), float(Cw[:, 2].min()))
    root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = {"idle": {"status": "PLACEHOLDER (movement wave pending the artist's answers)", "frames": [1, IDLE_N + 1],
                         "seconds": IDLE_N / K.FPS, "cyclic": True,
                         "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                         "seam_wand_mm": round(float(np.linalg.norm(wfirst - wlast, axis=1).max()) * 1000, 6),
                         "min_z": round(minz, 4), "body_sway_deg": list(BODY_SWAY), "arm_wave_deg": ARM_WAVE,
                         "leg_wave_deg": list(LEG_WAVE), "crown_flame": {"wave_deg": list(CF_WAVE), "stretch": CF_PULSE},
                         "wand_flame": {"sway_deg": WF_SWAY, "stretch": WF_PULSE}, "root_offset_max": round(root_off, 8)},
                "walk": "NOT BUILT: locomotion identity (float vs hop, grounded vs hovering rest, flame-flow wildness) "
                        "awaits the artist; clip_names fails on the missing walk by design"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("firesprite v1 (model lane): root > body > crown > crown_flame.{0,1}; body > arm.{L,R}.{0,1,2}; "
                       "arm.R.2 > wand > wand_flame; body > leg.{L,R}.{0,1}")
low["conquest_clips"] = ["idle"]
low["conquest_clip_status"] = "idle = PLACEHOLDER flame waver + flame flicker; NO walk yet (movement wave awaits the artist)"
low["conquest_locomotion"] = "UNANSWERED (float vs hop): rest pose stands on the floor per contract (leg tips z 0)"
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

# =========================================================================== 9. glb + skins
for o in scene.objects:
    o.select_set(o in (rig, low, wand))
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, act)
t = time.time()
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False)
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
                            "colour_sets": sorted(k for k in p["attributes"] if k.startswith("COLOR"))} for n, p in prims],
            "nodes": sorted(n.get("name") for n in js.get("nodes", [])),
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0,
            "extensionsUsed": js.get("extensionsUsed", [])}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'firesprite' (fire BLEND + crown opaque primitives) + skinned "
                           "'firesprite_wand' (own node: bark opaque + flame BLEND); natural scale; report-only cell fit "
                           "%.5f" % k_fit}
geo0 = geometry_digest([low, wand])
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]}}
pal_v = PAL.load(UNIT, "soulfire")
gt_v = glow_tiers(pal_v)
assert gt_v["pass"], gt_v
counts_v = repaint([low, wand], pal_v)
out_v = OUT_RIGGED[:-6] + "__soulfire.blend"
bpy.ops.wm.save_as_mainfile(filepath=out_v, copy=True, compress=True, relative_remap=False)
rep["skins"]["soulfire"] = {"file": out_v, "palette": PAL.table(pal_v), "palette_files": pal_v["files"], "glow_tiers": gt_v,
                            "region_faces": counts_v, "geometry_colour_uv_digest": geometry_digest([low, wand])}
repaint([low, wand], pal_default)
rep["skins"]["repaint_proof"] = {"rule": "a skin is a pure palette swap: same regions/faces/vertices; default restored",
                                 "default_digest_before": geo0, "default_digest_after_restore": geometry_digest([low, wand])}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}, default=str))
print("CLIP", json.dumps(rep["clips"]["idle"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
