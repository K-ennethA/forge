"""Goo Shield -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/goo_shield_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x2: Goo Shield, Fogeist" (section GOO SHIELD) + the sheet
design/reference/goo_shield-character-sheet.png (intake: design/reference/goo_shield/intake.json, --bg-tol 12, cuts
410,783, side axis 1190). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass,
render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. Three TRUE orthos:
  FRONT view (the unit's left on image right):  X = (x_px - 211) / 100     ; z = (750 - y_px) / 100
  SIDE view (the face on image RIGHT):          -Y = (x_px - 1190) / 100   ; z = (745 - y_px) / 100
  BACK view:                                     X = -(x_px - 601) / 100   ; z = (752 - y_px) / 100
Floor z = 0 at the puddle bottom. Front = -Y, +X = the unit's LEFT. The goo body's own centre in the front view sits at
x 207-208 px (3 px left of the intake axis 211): widths are measured as half the left-right span, the eyes about 208.
The views disagree in places (a painterly sheet); each constant says which view it follows, every disagreement is in
the report's 'sheet_deviations'.

Pipeline:
  1. GOO BODY = ONE SDF: the neck / head COLUMN (an S-curving lathe measured row by row off the front + side views), the
     lobed PUDDLE BASE (five flattened lobes: front, two sides, two back corners, smooth-unioned), the TRAIL (a lathe along
     +Y tapering far back to a blunt nose, its underside on the floor), all clipped flat by the floor. Marching tets =
     the HIGH (bake source); collapse decimation = the LOW shell. Regions: goo (column + trail) / goo_mid (the lobes,
     cut along the lobe-ownership line, i.e. the smooth-union crease) / goo_rim (the bright band under RIM_Z).
  2. ROCKS (closed SDF solids, decimated): the HEAD ARMOUR = a peaked superellipsoid cap (lower edge cut flat at the
     sides / back, at the brim above the eyes) and a thick rounded NOSE-GUARD TAB down between the eyes (artist v1 verdict:
     the side flaps removed); the BODY ARMOUR =
     a big tilted superellipsoid boulder on the back. Both intersected with seeded tangent-plane cuts (goo_shield_parts.
     facet_planes) = flat rock facets; per-facet tone by the facet normal (dark / body / lit / highlight).
  3. EYES (goo_shield_parts.oval_decal): two closed oval decals projected onto the face (outline / pale ring / iris).
  4. palette (palettes.py regions -> Col + Col alpha; no glow), flat shaded, Smart UV, bake normal + AO from the goo SDF
     high + the rocks + eyes, the normal baked against the low's FLAT faces (style guide 'face zone smooth-read'),
     wired into the goo only; AO floored.
  5. RIG: root > body.001 (base / puddle) > body.002 > neck > head (helmet + eyes); body.001 > trail.001 > .002 > .003;
     the body armour rides body.002. Clips PLACEHOLDER (movement not given): idle = goo wobble / breathing squash, walk
     = slug slide (a lift wave + sway travelling down the trail, the base on the floor). glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K               # noqa: E402  (read-only use)
import palettes as PAL           # noqa: E402  (read-only use)
import magmoo_sdf as SD          # noqa: E402  (read-only use: the SDF kit)
import goo_shield_parts as FP    # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "goo_shield"
CHAR_ID = "goo_shield"                # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.03                       # "goo surface finish": SDF sampling step of the goo body (the bake high)
SDF_BAND = 0.32                       # BAND >= max smooth-union k (TRAIL_K 0.25) + 2 steps (contract hazard note)
BODY_TRIS = 10500                     # "goo mesh detail": the goo shell's decimation target (before the colour cuts)
CUT_SNAP = 0.15                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- NECK / HEAD COLUMN: knots (z, half-width, centre y). Half-width = front-view goo half-span (rows y 750 - 100 z):
#   z 1.2 1.11 / 1.5 1.03 / 1.8 1.06 / 2.0 1.10 / 2.4 1.01 / 2.7 0.93 / 3.0 0.84 / 3.5 0.85 (face, between the flaps).
#   Centre y = the side view's goo front edge (x_px -> -(x - 1190)/100: z 1.2 -0.94 / 1.5 -0.95 / 1.8 -0.89 / 2.1 -0.74 /
#   2.4 -0.69 / 2.7 -0.72 / 3.5 -0.79 / 3.7 -0.73) + the half-depth (= half-width x COLUMN_DEPTH). Head check: side view
#   z 3.85 goo spans y -0.73 .. +1.04 (centre +0.155, half 0.885) vs front half-width 0.86 -> COLUMN_DEPTH 1.03.
#   The low knots (z < 0.8) sit inside the puddle lobes; the top (z > 4.0) is hidden in the helmet (rounded off at 4.5).
#   The base FLARES into a puddle skirt (front view half-span 1.11 at z 1.2 / 1.22 at 1.0 / 1.33 at 0.9 / 1.48 at 0.8 /
#   1.64 at 0.7 / 1.72 at 0.6, the lobes on top of it) while its front stays at the side view's -0.62 .. -0.80 (z 0.4 -
#   0.8): the skirt is elliptic (depth ratio < 1). Knots (z, half-width, centre y, depth ratio = half-depth / half-width).
COLUMN = [(0.00, 1.40, 0.55, 0.71), (0.35, 1.50, 0.43, 0.70), (0.70, 1.38, 0.29, 0.76), (0.95, 1.22, 0.21, 0.89),
          (1.20, 1.11, 0.20, 1.03), (1.50, 1.04, 0.11, 1.03), (1.80, 1.06, 0.20, 1.03), (2.10, 1.10, 0.38, 1.03),
          (2.40, 1.01, 0.34, 1.03), (2.70, 0.93, 0.24, 1.03), (3.00, 0.86, 0.14, 1.03), (3.40, 0.86, 0.12, 1.03),
          (3.80, 0.85, 0.13, 1.03), (4.10, 0.80, 0.14, 1.03), (4.35, 0.60, 0.14, 1.03), (4.50, 0.0, 0.14, 1.03)]
COLUMN_DEPTH = 1.03                   # "neck depth": side half-depth / front half-width (sheet head row z 3.85)
# ---- PUDDLE BASE LOBES: (centre, radii). Front view: half-span 1.72 at z 0.4 / 1.74 at 0.5 / 1.64 at 0.7 / 1.48 at 0.8
#   (the side lobes, shoulders y 665-670 -> z 0.80-0.85); the front-centre lobe x 115-310 px (half 0.98), the lowest
#   (bottom y 745). Side view base front: z 0.35 y -0.60 / 0.45 -0.67. Back view: the same 1.71-1.76 spread at z 0.3-0.5.
LOBES = [((0.0, -0.16, 0.28), (1.00, 0.48, 0.38)),
         ((1.22, 0.02, 0.32), (0.56, 0.70, 0.42)), ((-1.22, 0.02, 0.32), (0.56, 0.70, 0.42)),
         ((0.98, 0.95, 0.30), (0.68, 0.70, 0.40)), ((-0.98, 0.95, 0.30), (0.68, 0.70, 0.40))]
LOBE_K = 0.22                         # "lobe blend": smooth-union k of the lobes into the skirt (soft, keeps a crease)
# ---- TRAIL: knots (y, half-width, top z, bottom z). Side view top edge (the silhouette's rear extent per row):
#   z 1.25 at y 2.78 (x 912 y 620, where it leaves the armour) / 0.9 at 3.27 / 0.8 at 3.63 / 0.7 at 3.80 / 0.6 at 3.91 /
#   0.5 at 3.97 / 0.4 at 4.00 / 0.3 at 4.01 (a blunt nose); its underside lifts at the nose (z 0.2 at y 3.73), on the
#   floor before that (brief: 'sticking to the floor'). Half-width: back view centre lobe x 490-725 px (half 1.18) at
#   z <= 0.6; built tapering 1.2 -> 0.65 toward the nose.
TRAIL = [(0.60, 1.20, 1.45, -0.15), (1.60, 1.22, 1.45, -0.15), (2.30, 1.18, 1.35, -0.15), (2.80, 1.10, 1.22, -0.15),
         (3.25, 1.00, 0.92, -0.15), (3.55, 0.92, 0.80, -0.10), (3.78, 0.84, 0.70, 0.02), (3.93, 0.70, 0.55, 0.12),
         (4.01, 0.45, 0.40, 0.20), (4.05, 0.10, 0.30, 0.26)]
TRAIL_K = 0.25                        # "trail blend" into the base
RIM_Z = 0.16                          # "bright rim band": the goo below this height is goo_rim (sheet: the vivid lime edge
                                      #   along the puddle bottom / lobe feet; 9-17 % of the goo pixels below z 0.3)
MID_Z = 0.72                          # "lime puddle": the goo below this height (plus every lobe) is goo_mid. Sheet goo
                                      #   pixels nearest 210,246,160: 42 % (front) / 26 % (side) at z 0.3-0.9, 58-60 % below
                                      #   z 0.3, 7-19 % above z 0.9; overall 21.9 % front / 19.4 % side
GOO_TRANSLUCENT = False               # "goo see-through": False = opaque goo (no dither grain; gate 2026-10-10); True = the
                                      #   firefly alpha path (DITHERED, glTF BLEND) with the palette's region alpha
GOO_ALPHA_NOTE = ("goo OPAQUE (GOO_TRANSLUCENT False): the dithered 0.88 alpha read as grey grain over the backdrop"
                  if not GOO_TRANSLUCENT else "palette goo / goo_mid alpha 0.88, goo_rim 0.90")
# ---- HEAD ARMOUR (helmet). Envelope = a superellipsoid cap: centre (x, y, z), half sizes x / y, z above / below the
#   centre, exponents (top, bottom) on the x-z section (the front view's PEAKED top: half-widths z 5.2 0.20 / 5.0 0.55 /
#   4.8 0.86 / 4.6 1.10 / 4.4 1.18 / 4.0 1.32 / 3.8 1.37 widest / 3.4 1.26 / 3.2 1.20 / 3.0 1.10; apex y 221 -> 5.29);
#   the y-z section stays elliptic (the side view's ROUNDED top). Side view: front y -1.38 .. -1.41 at z 3.5-4.0 (the hood
#   flaps), -1.09 at 4.5, -0.48 at 4.8; back +1.0 .. +1.11 at z 4.0-4.6.
HELM_C = (0.0, -0.15, 4.05)           # the v1 crown (kept above the hugging sides): centre, half sizes, exponents
HELM_R = (1.37, 1.27)                 #   (front view's PEAKED top: apex y 221 -> z 5.29)
HELM_RZ = (1.24, 1.45)
HELM_P = (1.5, 2.5)
# ARTIST v2 VERDICT (review-log "2026-10-10 - Goo Shield v2 verdict", markup design/reference/goo_shield-v2-helmet-
#   annotation.png): no mushroom brim -- the helmet's sides come straight down HUGGING the goo head, a thin even
#   overhang proud of it; the brow sweeps in over the eyes to the centre; the nose tab hangs a little lower; craggier rock.
HELM_BRIM_Z = 3.90                    # the head is measured at this height for the hugging sides
HELM_OVERHANG = 0.15                  # "helmet overhang": the sides stand this far off the goo head (front + side views)
BROW = (3.62, 3.98, 0.30, 0.80, 4.08) # "brow line": lower edge z at the centre / at the sides, the |x| range it sweeps
                                      #   over (in over the eyes: eye top 3.765 at |x| 0.64), the edge z at the back
RIM_NOTCH = (24, 0.16, 31)            # "chipped rim": zig-zag knots round the lower edge, max chip height, seed
                                      #   (chips only RAISE the edge: never down over the eyes)
VISOR_Y = -0.45                       # the nose-guard's back face (buried in the head) sits at this y
TAB_BOT = 3.07                        # "nose-guard length": its rounded bottom z, just under the eyes' lower edge
                                      #   (3.515 - 0.25 = 3.265; v2 markup: ~0.2 lower than v2's 3.27)
TAB_W = 0.15                          # "nose-guard width": shaft half-width (the eyes' inner edge is |x| 0.56 at z 3.30)
TAB_FLARE = (3.50, 3.82, 0.40)        # "nose-guard flare": from z0 the shaft widens to this half-width at z1 (the brow);
                                      #   clear of the eyes: half 0.28 at z 3.70 vs the eye's inner edge 0.54 there
TAB_TOP = 3.98                        # the tab is unioned onto the helmet below this z
VISOR_T = 0.26                        # "nose-guard thickness": its front stands this far off the face (the helmet sides
                                      #   only HELM_OVERHANG) and follows the face curve; its solid runs back to VISOR_Y
HELM_FACETS = (180, (0.015, 0.05), 7, 0.18, 0.04)  # "helmet crag": plane count, cut depth range, seed, tilt jitter
                                      #   (rad) round the head / up-down on the straight sides (a vertical tilt on a
                                      #   straight wall would slice a long wedge, so it stays small there)
HELM_LUMPS = (0.06, 12, 11.0, 41)     # "helmet lumps": amplitude, waves, wavenumber (/unit), seed -- a low, smooth,
                                      #   OUTWARD-only irregular swell under the facets (decimation turns it into crag,
                                      #   no ledges); the facet cuts stay < the overhang so the goo never shows through
HELM_STEP = 0.02
HELM_TRIS = 2200                      # "helmet mesh detail": decimation target (craggier = more facets to keep)
# ---- BODY ARMOUR (the shield rock on the back) = the VISUAL HULL of the two views that draw it: the side outline
#   (extruded across x) intersected with the back outline (extruded along y), then faceted.
#   SIDE outline (y, z): the convex hull of the side view's armour pixels (x 880-1200, y 363-667 px), simplified to 12
#   corners (6 px tolerance): a tilted slab, its front edge straight down at y ~0 (x 1195-1200 px).
ARM_SIDE_POLY = [(3.10, 1.90), (2.85, 2.70), (1.62, 3.73), (1.32, 3.82), (1.04, 3.76), (-0.06, 3.06), (-0.10, 2.81),
                 (0.13, 1.69), (0.39, 1.37), (1.64, 0.78), (2.30, 0.86), (3.01, 1.56)]
#   BACK outline (|x|, z), mirrored: back view x 471-749 px, a rounded diamond, top point (615, 362) -> z 3.9, bottom
#   point (612, 652) -> 1.0, widest z 2.3-2.5 (half 1.28 L / 1.47 R; the views disagree: the FRONT view hides the armour
#   behind the 0.93-1.10 neck entirely) -> outline half 1.30, ~1.2 after the facet cuts (the side view needs the flank
#   outside the 1.04-1.10 neck to hide it).
ARM_BACK_POLY = [(0.0, 3.90), (0.60, 3.62), (1.08, 3.25), (1.30, 2.75), (1.30, 2.25), (1.08, 1.65), (0.60, 1.25),
                 (0.0, 1.00)]
ARM_C = (0.0, 1.50, 2.35)             # the hull's centre (inside both outlines)
ARM_FACETS = (60, (0.03, 0.09), 11, 0.08)   # "armour facets": plane count, cut depth range, seed, tilt jitter
ARM_STEP = 0.025
ARM_TRIS = 1000                       # "armour mesh detail": decimation target
ROCK_LIGHT = (0.20, -0.45, 0.87)      # facet tone: the sheet's light (top-front, a touch from +X: front view image right)
ROCK_TONE = (0.88, 0.55, -0.30)       # n.L above -> rock_hi / above -> rock_lit / below -> rock_dark (else rock)
# ---- EYES (front view, centres about the goo's own centre x 208 px): left (144.8, 398.5) / right (272.4, 399.0) px ->
#   |x| 0.64, z 3.515; outline 31 x 50 px (half 0.155 x 0.25), line ~3.5 px; iris 12 x 25 px (half 0.06 x 0.125).
#   The side view puts the eye at y -0.50 (x 1240 px) -- the front-projected oval lands there on the built head.
EYE_C = (0.64, 3.515)
EYE_OUTER = (0.155, 0.25)
EYE_LINE = 0.035                      # "eye outline" width
EYE_IRIS = (0.06, 0.125)
EYE_SEG, EYE_PROUD, EYE_DEPTH = 28, 0.012, 0.04
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
AO_SAMPLES = 48
AO_FLOOR = 0.70                       # "AO floor": baked AO is remapped to [AO_FLOOR, 1] (no black grime on the pale goo)
# ---- rig
BASE_Y = 0.20                         # the column's bones run up this y
HEAD_Z = (2.95, 3.25)                 # neck -> head weight blend band (the face + eyes are 100 % head)
NECK_Z = (2.00, 2.40)                 # body.002 -> neck
BODY_Z = (0.80, 1.20)                 # body.001 -> body.002
TRAIL_PTS = [(1.0, 0.62), (2.0, 0.60), (3.0, 0.45), (4.0, 0.22)]   # trail chain (y, z) along the trail's mid-line
TRAIL_W_Y = (1.00, 1.80)              # base -> trail weight blend along y
TRAIL_W_ZCAP = (1.40, 2.00)           # ... only below this height (the neck back never follows the trail)
# ---- clips (PLACEHOLDER: the artist has not given Goo Shield's movement). 24 fps; integer harmonics per loop ->
#   frame N+1 == frame 1. Rest pose: the puddle bottom on the floor (z 0); nothing ever goes below it.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BREATHE = (0.03, 0.025, 2)       # "breathing squash": base spread +-, upper-body counter-swell +-, breaths per loop
IDLE_WOBBLE = (1.4, 1.8, 1.2)         # "goo wobble": body.002 / neck / head sway deg (1 cyc, each lagging the one below)
IDLE_TRAIL_SWAY = (1.0, 3.0, 1, 0.9)  # trail sway (root deg, tip deg, cycles, lag rad per bone)
WALK_N = 48                           # "slide loop": 2 s
WALK_HUMP = (0.06, 2, 1.1)            # "slide wave": hump lift (u), waves per loop, phase lag per trail bone (travels back)
WALK_SWAY = (2.0, 6.0, 2, 0.9)        # trail side sway travelling down the trail (root deg, tip deg, cycles, lag)
WALK_SURGE = (2.5, 1.5, 0.02)         # "surge": body.002 forward lean deg + its pulse deg (2 cyc), base spread pulse
WALK_HEAD = 1.5                       # head / neck counter-nod deg

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
          "design/reference/goo_shield-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; three true orthos; floor z = 0 at the puddle bottom",
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


def decimate(V, F, target, name):
    """collapse decimation (deterministic) + specks dropped + the hull-fold gate (BUILD_CONTRACT hazard note)"""
    tmp = new_obj("dec_" + name, V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / len(F))
    LV_, LF_ = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV_, LF_, specks_ = keep_islands(LV_, LF_, 0.01)
    bh = BVHTree.FromPolygons(V.tolist(), F)
    d1 = np.array([bh.find_nearest(Vector(p))[3] for p in LV_])
    bl = BVHTree.FromPolygons(LV_.tolist(), LF_)
    hs = np.arange(0, len(V), max(1, len(V) // 20000))
    d2 = np.array([bl.find_nearest(Vector(V[i]))[3] for i in hs])
    bb_h, bb_l = np.ptp(V, 0), np.ptp(LV_, 0)
    rep_ = {"decimated_tris": tri_count_F(LF_), "specks": specks_,
            "low_to_high": {"mean": round(float(d1.mean()), 5), "p99": round(float(np.percentile(d1, 99)), 5),
                            "max": round(float(d1.max()), 5)},
            "high_to_low_sampled": {"p99": round(float(np.percentile(d2, 99)), 5), "max": round(float(d2.max()), 5)},
            "bbox_low_over_high": (bb_l / bb_h).round(5).tolist()}
    assert np.all(bb_l / bb_h > 0.97), (name, rep_)
    return LV_, LF_, rep_, d2


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. goo body: one SDF
t_sdf = time.time()
_ck = np.array(COLUMN, float)
_cd = SD.catmull(_ck, 12)
_o = np.argsort(_cd[:, 0], kind="stable")
CZ, CHW, CCY, CDR = _cd[_o, 0], np.clip(_cd[_o, 1], 0.0, None), _cd[_o, 2], _cd[_o, 3]


def col_at(z):
    return np.interp(z, CZ, CHW, left=CHW[0], right=0.0), np.interp(z, CZ, CCY)


def sd_column(P):
    hw, cy = col_at(P[:, 2])
    d = np.hypot(P[:, 0], (P[:, 1] - cy) / np.interp(P[:, 2], CZ, CDR)) - hw
    return np.maximum(d, np.maximum(-0.2 - P[:, 2], P[:, 2] - CZ[-1]))


def sd_lobes(P):
    return np.min(np.stack([SD.sd_ellipsoid(P, c, r) for c, r in LOBES], 1), 1)


_tk = np.array(TRAIL, float)
_td = SD.catmull(_tk, 12)
_o = np.argsort(_td[:, 0], kind="stable")
TY, THX, TTOP, TBOT = _td[_o, 0], np.clip(_td[_o, 1], 1e-3, None), _td[_o, 2], _td[_o, 3]


def sd_trail(P):
    hx = np.interp(P[:, 1], TY, THX)
    top = np.interp(P[:, 1], TY, TTOP); bot = np.interp(P[:, 1], TY, TBOT)
    zc, hz = 0.5 * (top + bot), np.maximum(0.5 * (top - bot), 1e-3)
    d = np.hypot(P[:, 0], (P[:, 2] - zc) * hx / hz) - hx
    return np.maximum(d, np.maximum(TY[0] - P[:, 1], P[:, 1] - TY[-1]))


PART_SDF = {"column": sd_column, "lobes": sd_lobes, "trail": sd_trail}
GROUPS = list(PART_SDF)
g = SD.Grid((-1.9, -1.1, -0.2), (1.9, 4.15, 4.6), SDF_STEP, SDF_BAND)
g.apply(sd_column, (-1.6, -1.1, -0.2), (1.6, 1.7, 4.55), 0.0)
for c_, r_ in LOBES:
    g.apply(lambda P, c_=c_, r_=r_: SD.sd_ellipsoid(P, c_, r_), np.array(c_) - r_, np.array(c_) + r_, LOBE_K)
g.apply(sd_trail, (-1.3, TY[0], -0.2), (1.3, TY[-1], float(TTOP.max())), TRAIL_K)
g.floor(0.0)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": g.ops, "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs, "high_min_z": round(float(HV[:, 2].min()), 6),
                 "seconds": round(time.time() - t_sdf, 1)}
del g
assert HV[:, 2].min() > -1e-3, "goo high below the floor"
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low goo shell
t = time.time()
LV, LF, rt_body, dev2 = decimate(HV, HF, BODY_TRIS, "goo")
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "goo": rt_body, "seconds": round(time.time() - t, 1)}
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))
print("RETOPO", json.dumps(report["retopo"]))
BVH_LO = BVHTree.FromPolygons(LV.tolist(), LF)

# ---- regions: lobe ownership (the smooth-union crease) + the rim band, every boundary iso-cut
DGR = np.stack([PART_SDF[gname](LV) for gname in GROUPS], 1)


def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
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


# the snap moves the FIELD value only; snapped vertices keep their position (the cut line sits on the vertex)
t = time.time()
cut_log = [iso_cut("f_lobes", 0.0), iso_cut("z", MID_Z), iso_cut("z", RIM_Z)]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
shell_reg = np.full(len(CF), "goo", dtype=object)
shell_reg[(FVAL["f_lobes"] > 0.0) | (FVAL["z"] < MID_Z)] = "goo_mid"
shell_reg[FVAL["z"] < RIM_Z] = "goo_rim"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 3. rocks: helmet + body armour (closed SDF solids)
HC_ = np.array(HELM_C, float)
_hcol_hw = float(col_at(np.array([3.6]))[0][0]); _hcol_cy = float(col_at(np.array([3.6]))[1][0])
GOO_HW_BRIM = float(col_at(np.array([HELM_BRIM_Z]))[0][0])         # goo head half-width at the brim height
HELM_HW = GOO_HW_BRIM + HELM_OVERHANG                                # helmet half-width (front view)
HELM_HD = GOO_HW_BRIM * COLUMN_DEPTH + HELM_OVERHANG                 # helmet half-depth (side view)


def crown_half(z):
    """the v1 peaked crown's half-width (front, superellipse) / half-depth (side, ellipse) at z"""
    t = np.clip((z - HC_[2]) / HELM_RZ[0], 0.0, 1.0)
    hx = HELM_R[0] * np.clip(1.0 - t ** HELM_P[0], 0.0, 1.0) ** (1.0 / HELM_P[0])
    hy = HELM_R[1] * np.sqrt(np.clip(1.0 - t * t, 0.0, 1.0))
    return hx, hy


def helm_env(P):
    """the helmet envelope: an elliptic section hugging the goo head (HELM_HW x HELM_HD round the head's centre line)
    straight up until the v1 peaked crown is narrower, then the crown to the apex. < 0 inside."""
    z = P[:, 2]
    cx, cy_ = crown_half(z)
    hx = np.maximum(np.minimum(HELM_HW, cx), 1e-3); hy = np.maximum(np.minimum(HELM_HD, cy_), 1e-3)
    d = np.hypot(P[:, 0], (P[:, 1] - _hcol_cy) * hx / hy) - hx
    return np.maximum(d, z - (HC_[2] + HELM_RZ[0]))


def head_front(x):
    """the face (column) front y at the eye band, per |x|"""
    u = np.clip(np.abs(x) / _hcol_hw, 0.0, 1.0)
    return _hcol_cy - _hcol_hw * COLUMN_DEPTH * np.sqrt(1.0 - u * u)


# crag facets: tangent planes of the envelope at seeded surface points (sides + crown), each normal tilted by a seeded
# jitter and cut in by a seeded depth -- many small varied facets (v2: 'more craggly')
_rh = np.random.default_rng(HELM_FACETS[2])
H_N, H_O = [], []
_hgrad_h = 1e-4
while len(H_N) < HELM_FACETS[0]:
    th_ = _rh.uniform(0.0, 2.0 * math.pi)
    z_ = _rh.uniform(BROW[0] - 0.05, HC_[2] + HELM_RZ[0] - 0.02)
    cx_, cy2_ = crown_half(np.array([z_]))
    hx_, hy_ = min(HELM_HW, float(cx_[0])), min(HELM_HD, float(cy2_[0]))
    if hx_ < 0.03:
        continue
    S_ = np.array([hx_ * math.cos(th_), _hcol_cy + hy_ * math.sin(th_), z_])
    g_ = np.array([(helm_env((S_ + _hgrad_h * e)[None])[0] - helm_env((S_ - _hgrad_h * e)[None])[0]) / (2 * _hgrad_h)
                   for e in np.eye(3)])
    g_ = unit(g_)
    tz = _rh.normal(0.0, HELM_FACETS[4] if hx_ >= HELM_HW - 1e-6 else HELM_FACETS[3])   # wall: small up-down tilt
    e_t = unit(np.cross([0.0, 0.0, 1.0], g_)) if abs(g_[2]) < 0.99 else np.array([1.0, 0.0, 0.0])
    e_v = np.cross(g_, e_t)
    n_ = unit(g_ + _rh.normal(0.0, HELM_FACETS[3]) * e_t + tz * e_v)
    H_N.append(n_); H_O.append(float(n_ @ S_) - _rh.uniform(*HELM_FACETS[1]))
H_N, H_O = np.array(H_N), np.array(H_O)
_rl = np.random.default_rng(HELM_LUMPS[3])
_LK = np.stack([unit(v) for v in _rl.normal(0.0, 1.0, (HELM_LUMPS[1], 3))]) * HELM_LUMPS[2]
_LP = _rl.uniform(0.0, 2.0 * math.pi, HELM_LUMPS[1])


def helm_lumps(P):
    return HELM_LUMPS[0] * np.clip(0.5 + np.sin(P @ _LK.T + _LP).mean(1) * math.sqrt(2.0) * 0.5, 0.0, 1.0)  # outward only


_nk = np.random.default_rng(RIM_NOTCH[2]).uniform(0.0, 0.5, RIM_NOTCH[0]) * RIM_NOTCH[1]
_nk[1::2] += 0.5 * RIM_NOTCH[1]                                     # alternate low / high knots: a clean zig-zag of chips


def rim_notch(P):
    """a periodic zig-zag of chip heights round the lower edge (azimuth about the head's centre line)"""
    phi = np.arctan2(P[:, 1] - _hcol_cy, P[:, 0])
    u = (phi / (2.0 * math.pi) % 1.0) * RIM_NOTCH[0]
    k = np.floor(u).astype(int) % RIM_NOTCH[0]
    f = u - np.floor(u)
    return _nk[k] * (1 - f) + _nk[(k + 1) % RIM_NOTCH[0]] * f


def brow_z(ax, y):
    """the helmet's lower edge: the brow sweeping in over the eyes at the front, rising to BROW[4] at the back"""
    zf = BROW[0] + (BROW[1] - BROW[0]) * smoothstep(BROW[2], BROW[3], ax)
    return zf + (BROW[4] - zf) * smoothstep(-0.3, 0.6, y)


def guard_front(x, z):
    """the nose-guard's front surface y: the face's ellipse grown VISOR_T, easing back to the helmet's own overhang just
    under TAB_TOP so the tab runs into the brow without a shelf"""
    t_ = VISOR_T - (VISOR_T - HELM_OVERHANG) * smoothstep(TAB_TOP - 0.45, TAB_TOP, z)
    a_ = _hcol_hw + t_
    u = np.clip(np.abs(x) / a_, 0.0, 1.0)
    return _hcol_cy - (_hcol_hw * COLUMN_DEPTH + t_) * np.sqrt(1.0 - u * u)


def tab_2d(ax, z):
    """front-projection field of the nose-guard outline (< 0 inside): a rounded-bottom shaft flaring into the brow"""
    zc = TAB_BOT + TAB_W
    w = TAB_W + (TAB_FLARE[2] - TAB_W) * smoothstep(TAB_FLARE[0], TAB_FLARE[1], z) ** 1.5
    return np.where(z >= zc, ax - w, np.hypot(ax, z - zc) - TAB_W)


def sd_helmet(P):
    ax = np.abs(P[:, 0]); y, z = P[:, 1], P[:, 2]
    crown = np.maximum.reduce([helm_env(P) - helm_lumps(P), (P @ H_N.T - H_O).max(1),
                               brow_z(ax, y) + rim_notch(P) - z])                # hugging crown, crag, chipped rim
    tab = np.maximum.reduce([y - VISOR_Y, tab_2d(ax, z), guard_front(P[:, 0], z) - y, z - TAB_TOP])
    return np.minimum(crown, tab)                                                 # the thick nose-guard joins the brow


ARM_C_ = np.array(ARM_C, float)


def poly_gauge(poly, c):
    """convex polygon (CCW or CW) about an inside point c -> per edge (unit outward normal, support distance): the
    polygon's gauge max_i n_i.(q - c) / h_i is homogeneous of degree 1 and = 1 on the outline"""
    P = np.asarray(poly, float) - np.asarray(c, float)
    out = []
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        n = np.array([b[1] - a[1], -(b[0] - a[0])]); n /= np.linalg.norm(n)
        if n @ a < 0:
            n = -n
        out.append((n, float(n @ a)))
    assert all(h > 0 for _, h in out), "centre outside the outline"
    return np.array([o[0] for o in out]), np.array([o[1] for o in out])


_bp = [(x, z) for x, z in ARM_BACK_POLY] + [(-x, z) for x, z in ARM_BACK_POLY[::-1] if x > 0]
ARM_SN, ARM_SH = poly_gauge(ARM_SIDE_POLY, (ARM_C[1], ARM_C[2]))
ARM_BN, ARM_BH = poly_gauge(_bp, (0.0, ARM_C[2]))


def arm_f(Q):
    """visual hull envelope (homogeneous degree 1 about ARM_C): max(side outline gauge, back outline gauge)"""
    fs = ((Q[:, 1:3] @ ARM_SN.T) / ARM_SH).max(1)
    fb = ((Q[:, [0, 2]] @ ARM_BN.T) / ARM_BH).max(1)
    return np.maximum(fs, fb)


A_DIRS = FP.fib_dirs(ARM_FACETS[0], ARM_FACETS[2])
A_N, A_O = FP.facet_planes(arm_f, ARM_C_, np.eye(3), A_DIRS, ARM_FACETS[1], ARM_FACETS[2] + 100, tilt=ARM_FACETS[3])


def sd_armour(P):
    d = (arm_f(P - ARM_C_) - 1.0) * 1.5
    return np.maximum(d, (P @ A_N.T - A_O).max(1))


def rock(name, fn, lo, hi, step, target):
    gg = SD.Grid(lo, hi, step, 4 * step)
    gg.apply(fn, lo, hi, 0.0)
    V_, F_ = SD.polygonise(gg)
    F_ = [list(map(int, f)) for f in F_]
    V_, F_, cr = keep_islands(V_, F_, 0.02)
    hi_tris = len(F_)
    LV_, LF_, rep_, _ = decimate(V_, F_, target, name)
    rep_.update({"step": step, "high_tris": hi_tris, "crumbs": cr, "planes": int(len((H_N if name == "helmet" else A_N)))})
    return LV_, LF_, rep_


t = time.time()
HLV, HLF, rt_h = rock("helmet", sd_helmet, (-1.5, -1.55, 2.7), (1.5, 1.25, 5.4), HELM_STEP, HELM_TRIS)
ALV, ALF, rt_a = rock("armour", sd_armour, (-1.3, -0.35, 0.6), (1.3, 3.35, 4.0), ARM_STEP, ARM_TRIS)
report["retopo"]["helmet"] = rt_h; report["retopo"]["armour"] = rt_a
report["helmet"] = {"goo_half_width_at_brim": round(GOO_HW_BRIM, 4), "brim_z": HELM_BRIM_Z,
                    "helmet_half_width": round(HELM_HW, 4), "helmet_half_depth": round(HELM_HD, 4),
                    "overhang": HELM_OVERHANG, "crag_planes": int(len(H_N)), "rim_chip_knots": RIM_NOTCH[0],
                    "tab_bottom_z": round(float(HLV[:, 2].min()), 4), "tab_shaft_width": 2 * TAB_W,
                    "tab_flare_width": 2 * TAB_FLARE[2], "tab_standoff": VISOR_T,
                    "rule": "artist v2 verdict: sides hug the goo head (+overhang), brow over the eyes, lower tab, craggier"}
print("HELMET", json.dumps(report["helmet"]))
report["retopo"]["rock_seconds"] = round(time.time() - t, 1)
print("ROCKS", json.dumps({"helmet": rt_h, "armour": rt_a}))
_L = unit(ROCK_LIGHT)


def rock_regions(V, F):
    R = []
    for f in F:
        n = unit(np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]))
        d = float(n @ _L)
        R.append("rock_hi" if d > ROCK_TONE[0] else "rock_lit" if d > ROCK_TONE[1] else
                 "rock_dark" if d < ROCK_TONE[2] else "rock")
    return R


PARTS = [FP.Part("helmet", HLV, HLF, rock_regions(HLV, HLF), bone="head"),
         FP.Part("armour", ALV, ALF, rock_regions(ALV, ALF), bone="body.002")]

# =========================================================================== 4. eyes (closed decals on the face)


def project(x, z):
    loc, nrm, _, _ = BVH_LO.ray_cast(Vector((x, -6.0, z)), Vector((0.0, 1.0, 0.0)))
    assert loc is not None, ("eye ray missed the face", x, z)
    n = np.array(nrm)
    if n[1] > 0:
        n = -n
    return np.array(loc), n


_ia, _ib = EYE_IRIS
_oa, _ob = EYE_OUTER
EYE_RINGS = [(0.5 * _ia, 0.5 * _ib), (_ia, _ib), (0.5 * (_ia + _oa - EYE_LINE), 0.5 * (_ib + _ob - EYE_LINE)),
             (_oa - EYE_LINE, _ob - EYE_LINE), (_oa, _ob)]
EYE_REG = ["eye_iris", "eye_iris", "eye_white", "eye_white", "eye_line"]
EYES = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    cxz = (sg * EYE_C[0], EYE_C[1])
    V, F, R = FP.oval_decal(cxz, EYE_RINGS, EYE_REG, EYE_SEG, project, EYE_PROUD, EYE_DEPTH, "eye_line")
    PARTS.append(FP.Part("eye." + side, V, F, R, bone="head"))
    EYES[side] = project(*cxz)[0]

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
for name, V, F, R, p in ISL:                                # nothing dips below the floor (lesson 2026-10-10)
    assert V[:, 2].min() > -1e-3, (name, float(V[:, 2].min()))
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, MATI, RANGE = [], [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    MATI += [1 if name == "shell" else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
HV_s = HV - SHIFT
REG = ["goo", "goo_mid", "goo_rim", "eye_line", "eye_white", "eye_iris", "rock_dark", "rock", "rock_lit", "rock_hi"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [n for j, n in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty
GOO_REG = {"goo", "goo_mid", "goo_rim"}
assert all((RNAME[i] in GOO_REG) == (MATI[i] == 1) for i in range(len(RNAME))), "goo region on an opaque part or vice versa"


def make_mat(name, kind):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    if kind == "goo" and GOO_TRANSLUCENT:
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"          # Eevee: order-independent see-through (glTF alphaMode BLEND)
        mat.use_backface_culling = True                  # a closed solid blob: single-sided (its inside never shows)
        mat.use_transparent_shadow = True
    else:
        mat.use_backface_culling = True                  # opaque closed solids: single-sided (glTF doubleSided false)
    return mat


def apply_alpha(mat, pal):
    n_ = mat.node_tree.nodes.get("alpha_factor")
    if n_ is not None:
        n_.inputs[1].default_value = float(pal["material"].get("alpha", 1.0))


def paint_alpha(me, pal):
    names, rid_, _ = PAL.read_regions(me)
    a_reg = np.array([float(pal["regions"][n].get("alpha", 1.0)) if GOO_TRANSLUCENT else 1.0 for n in names], np.float32)
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
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
            if s_.material.name == UNIT + "_goo":                 # the goo is glossy, the rock matte (palette material)
                s_.material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = \
                    float(pal["material"]["goo_roughness"])
    return out


def glow_check(pal):
    """no glow: the sheet's bright lime is paint (brief) -- no region may carry emission"""
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    return {"skin": pal["skin"], "emissive_regions": lit, "pass": not lit,
            "rule": "the bright lime 147,250,11 (rim, iris) is colour, not emission"}


MAT_BODY = make_mat(UNIT + "_body", "body")
MAT_GOO = make_mat(UNIT + "_goo", "goo")
pal_default = PAL.load(UNIT, "default")
report["glow"] = {"default": glow_check(pal_default)}
assert report["glow"]["default"]["pass"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_GOO)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
PAL.store_regions(me, REG, rid, np.ones(len(FALL)))       # no per-face jitter: flat paint (no speckle on the goo / facets)
report["regions_faces"] = repaint([low], pal_default)
report["alpha"] = {"main": paint_alpha(me, pal_default), "material_alpha": pal_default["material"].get("alpha", 1.0),
                   "note": GOO_ALPHA_NOTE}
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
_fn = np.array([p.normal[2] for p in me.polygons])
_gv = (np.array(MATI) == 1) & ~((_fn < -0.99) & (np.array([p.center[2] for p in me.polygons]) < SHIFT[2] * 0 + 0.01))
report["goo_visible_share"] = {n: round(float(fa[_gv & (rid == REG.index(n))].sum() / fa[_gv].sum()), 4)
                               for n in ("goo", "goo_mid", "goo_rim")}
report["goo_visible_share"]["rule"] = ("goo area excluding the flat floor-contact face; sheet goo pixels nearest pale "
                                       "231,248,204 (+ chip 242,253,224) / mid 210,246,160 / rim 147,250,11: front 72.0 / "
                                       "21.9 / 2.5 %, side 72.4 / 19.4 / 4.1 % (+ 3.6-4.2 % outline line art)")
_rock = np.isin(rid, [REG.index(r) for r in ("rock_dark", "rock", "rock_lit", "rock_hi")])
report["rock_tone_share"] = {n: round(float(fa[rid == REG.index(n)].sum() / fa[_rock].sum()), 4)
                             for n in ("rock_dark", "rock", "rock_lit", "rock_hi")}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head-column centre at eye height -> the midpoint of the two eye decals (on the midline, -Y)
anchor = np.array([0.0, _hcol_cy, EYE_C[1]]) - SHIFT
landmark = 0.5 * (EYES["L"] + EYES["R"]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head-column centre at eye height -> the midpoint of the two eyes (the face looks -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

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
report["tris"] = {"total": tris_main, "window": TRI_BUDGET, "per_part": {"goo_shell": tri_count_F(CF),
                  **{p.name: p.tris() for p in PARTS}},
                  "per_region": {n: int((rid == j).sum()) for j, n in enumerate(REG)}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The goo shell carries most of the budget (a "
                            "smooth glossy blob needs density to read round under flat shading); the two rocks are "
                            "decimated faceted solids, the eyes two small decals." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"per_part": open_e, "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (SDF goo shell, SDF rocks, closed eye decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet": {"front_height": 5.29, "back_height": 5.37, "side_height": 5.02, "front_width": 3.75,
                               "side_depth": 5.5},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
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
low["conquest_source"] = "from scratch: design/reference/goo_shield-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; all zero (no glow)"
low["conquest_alpha_channel"] = (("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; goo "
                                  "material alphaMode BLEND single-sided glossy; rock + eyes opaque") if GOO_TRANSLUCENT else
                                 "all opaque (GOO_TRANSLUCENT False): the goo is a glossy opaque material, Col alpha 1")
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_hb = (HLV.min(0), HLV.max(0))
_ab = (ALV.min(0), ALV.max(0))
_face = np.array([[-1.45, -1.6, 2.75], [1.45, 0.2, 4.6]])
_trail = np.array([[-1.3, 1.6, 0.0], [1.3, 4.1, 1.6]])
low["conquest_focus"] = json.dumps({
    "eyes_helmet": [(_face[0] - SHIFT).tolist(), (_face[1] - SHIFT).tolist()],
    "head_armor": [(_hb[0] - SHIFT).tolist(), (_hb[1] - SHIFT).tolist()],
    "body_armor": [(_ab[0] - SHIFT).tolist(), (_ab[1] - SHIFT).tolist()],
    "goo_trailing": [(_trail[0] - SHIFT).tolist(), (_trail[1] - SHIFT).tolist()],
    "views": {"eyes_helmet": [0.0, 6.0], "head_armor": [-90.0, 8.0], "body_armor": [-110.0, 12.0],
              "goo_trailing": [-90.0, 6.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "rock_tone_share", "goo_visible_share", "glow")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 6. bake normal + AO (high = goo SDF + rocks + eyes)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
for p in PARTS:
    PH_V.append(p.V - SHIFT); PH_F += [[i + o_ for i in f] for f in p.F]; o_ += len(p.V)
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
BAKE_NODES = {}
for mat_ in (MAT_BODY, MAT_GOO):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_flat()                                   # FLAT reference: the normal map carries flat face -> smooth high
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
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, AO_SAMPLES)):
    for mat_ in (MAT_BODY, MAT_GOO):
        mat_.node_tree.nodes.active = BAKE_NODES[mat_.name][idx]
    scene.cycles.samples = samples
    t = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
goo_faces = np.array(MATI) == 1
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
gootex_n = texels_of(goo_faces, RN)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the goo high->low sampled p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA}, "ao_samples": AO_SAMPLES,
    "high_tris": len(PH_F), "high_rule": "goo SDF high + the rocks (their lows: they are the final facets) + the eyes",
    "normal_reference": "FLAT low faces (the goo reads smooth under the flat_shaded contract); wired into the goo only",
    "uv_texels_normal": int(alltex_n.sum()),
    "normal_baked_pct_of_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_baked_pct_of_goo_texels": round(100 * float(cov_n[gootex_n].mean()), 2),
    "normal_goo_dev_p50_p95": [round(float(np.percentile(devn[cov_n & gootex_n], 50)), 4),
                               round(float(np.percentile(devn[cov_n & gootex_n], 95)), 4)],
    "ao_baked_pct_of_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_raw_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_raw_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4),
    "ao_floor": AO_FLOOR})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0
pa[:, :3] = AO_FLOOR + (1.0 - AO_FLOOR) * pa[:, :3]
img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_normal_twin.npy" if DIGEST_ONLY else "_normal_main.npy")), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), UNIT + ("_ao_twin.npy" if DIGEST_ONLY else "_ao_main.npy")), pa[:, :1])
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k != "pixel_sha"}))
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
# AO into both materials (contact shade under the rocks); the normal map into the goo only (the rock facets are the
# geometry itself -- a map on them would double the facet turn)
for mat_ in (MAT_BODY, MAT_GOO):
    nt = mat_.node_tree
    tn, ta = BAKE_NODES[mat_.name]
    bsdf = nt.nodes["Principled BSDF"]
    mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
    mul.inputs["Factor"].default_value = 1.0
    ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
    oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
    nt.links.new(nt.nodes["col"].outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
    nt.links.new(oc, bsdf.inputs["Base Color"])
    if mat_ is MAT_GOO:
        nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
        nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        nt.nodes.remove(tn)
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
                     "provenance": "pixel-sampled from the sheet's six palette chips (intake swatches) + drawn-surface "
                                   "samples (see the palette notes)"}
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


TRAIL_BONES = len(TRAIL_PTS) - 1
BONES = [("body.001", P_((0.0, BASE_Y, 0.0)), P_((0.0, BASE_Y, BODY_Z[1] - 0.2)), "root"),
         ("body.002", P_((0.0, BASE_Y, BODY_Z[1] - 0.2)), P_((0.0, 0.30, 2.2)), "body.001"),
         ("neck", P_((0.0, 0.30, 2.2)), P_((0.0, _hcol_cy, 3.1)), "body.002"),
         ("head", P_((0.0, _hcol_cy, 3.1)), P_((0.0, _hcol_cy, 4.6)), "neck")]
for k in range(TRAIL_BONES):
    (y0, z0), (y1, z1) = TRAIL_PTS[k], TRAIL_PTS[k + 1]
    BONES.append(("trail.%03d" % (k + 1), P_((0.0, y0, z0)), P_((0.0, y1, z1)), "body.001" if k == 0 else "trail.%03d" % k))
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
    e.use_connect = False                          # the trail bones take location keys (the slide wave lifts them)
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences): the goo column blends by height, the rear by the trail chain; rigid parts 1.0
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VALL[a_:b_] + SHIFT
zz, yy = Pw[:, 2], Pw[:, 1]
s_b2 = smoothstep(BODY_Z[0], BODY_Z[1], zz); s_nk = smoothstep(NECK_Z[0], NECK_Z[1], zz); s_hd = smoothstep(HEAD_Z[0], HEAD_Z[1], zz)
Wc = np.stack([1.0 - s_b2, s_b2 - s_nk, s_nk - s_hd, s_hd], 1)
tf = smoothstep(TRAIL_W_Y[0], TRAIL_W_Y[1], yy) * (1.0 - smoothstep(TRAIL_W_ZCAP[0], TRAIL_W_ZCAP[1], zz))
_tp = np.array(TRAIL_PTS)
_tl = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(_tp[:, 0]), np.diff(_tp[:, 1])))])
TRAIL_LEN = float(_tl[-1])
s_tr = np.interp(yy, _tp[:, 0], _tl)
Wv = K.vine_weights(np.clip(s_tr, 0.0, TRAIL_LEN), TRAIL_LEN, TRAIL_BONES)
for j, nm in enumerate(["body.001", "body.002", "neck", "head"]):
    Wt[a_:b_, J[nm]] = Wc[:, j] * (1.0 - tf)
Wt[a_:b_, J["body.001"]] += tf * Wv[:, 0]
for k in range(TRAIL_BONES):
    Wt[a_:b_, J["trail.%03d" % (k + 1)]] += tf * Wv[:, k + 1]
for p in PARTS:
    a_, b_ = RANGE[p.name]
    Wt[a_:b_, J[p.bone]] = 1.0
Wt = np.where(Wt > 1e-4, Wt, 0.0)
if (Wt > 0).sum(1).max() > 4:
    idx_ = np.argsort(-Wt, 1)[:, 4:]
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
                  "rule": "goo: body.001 -> body.002 -> neck -> head by height (bands BODY_Z / NECK_Z / HEAD_Z; the face "
                          "and eyes 100 % head), the rear (y > TRAIL_W_Y, below TRAIL_W_ZCAP) rigkit.vine_weights along "
                          "the trail chain (root blends from body.001); helmet + eyes 1.0 head; body armour 1.0 body.002"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly. Uniform scale only (glTF-safe: no shear).
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        av = Vector(AXV[axis]) if isinstance(axis, str) else Vector(tuple(float(c) for c in axis))
        Rw = Matrix.Rotation(math.radians(deg), 3, av) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


CLIPS = {"idle": IDLE_N, "walk": WALK_N}
TRAILS = ["trail.%03d" % (k + 1) for k in range(TRAIL_BONES)]


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        sp, sq, nb = IDLE_BREATHE
        s1 = 1.0 + sp * math.sin(TAU * nb * u)                       # the puddle spreads / gathers (about its floor point)
        s2 = 1.0 - sq * math.sin(TAU * nb * u - 0.6)                 # the upper body swells as the base gathers (lagging)
        w0, w1, w2 = IDLE_WOBBLE
        P["body.001"] = (None, [], (s1, s1, s1))
        P["body.002"] = (None, [("X", w0 * math.sin(TAU * u)), ("Y", w0 * 0.8 * math.sin(TAU * u + 1.3))], (s2, s2, s2))
        P["neck"] = (None, [("X", w1 * math.sin(TAU * u - 0.7)), ("Y", w1 * 0.8 * math.sin(TAU * u + 0.6))], None)
        P["head"] = (None, [("X", w2 * math.sin(TAU * u - 1.4)), ("Y", w2 * 0.8 * math.sin(TAU * u - 0.1))], None)
        for k, bn in enumerate(TRAILS):
            P[bn] = (None, [("Z", K.vine_wave(u, k, TRAIL_BONES, IDLE_TRAIL_SWAY[0], IDLE_TRAIL_SWAY[1],
                                              IDLE_TRAIL_SWAY[3], IDLE_TRAIL_SWAY[2], 0.4))], None)
        info = {"s1": s1, "s2": s2}
    else:
        A, nw, lag = WALK_HUMP
        lifts = [A * (0.5 - 0.5 * math.cos(TAU * nw * u - k * lag)) for k in range(TRAIL_BONES)]   # >= 0: never below
        lean, pulse, spread = WALK_SURGE
        s1 = 1.0 + spread * math.sin(TAU * 2 * u)
        P["body.001"] = (None, [], (s1, s1, s1))
        P["body.002"] = (None, [("X", -lean - pulse * math.sin(TAU * 2 * u))], None)
        P["neck"] = (None, [("X", WALK_HEAD * math.sin(TAU * 2 * u - 0.8))], None)
        P["head"] = (None, [("X", lean * 0.6 + WALK_HEAD * math.sin(TAU * 2 * u - 1.6))], None)
        prev = 0.0
        for k, bn in enumerate(TRAILS):
            yaw = K.vine_wave(u, k, TRAIL_BONES, WALK_SWAY[0], WALK_SWAY[1], WALK_SWAY[3], WALK_SWAY[2], 0.0)
            P[bn] = ((0.0, 0.0, lifts[k] - prev), [("Z", yaw)], None)   # chain lift: each bone adds its own minus the parent's
            prev = lifts[k]
        info = {"lifts": lifts}
    return P, info


NEW_ACTS = {}
key_rows = []
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1
        P, _ = pose_at(cn, f % N)
        for bn in DEFORM:
            loc, rots, sc = P.get(bn, (None, [], None))
            pb = rig.pose.bones[bn]
            pb.location = local_loc(bn, loc) if loc is not None else Vector((0.0, 0.0, 0.0))
            pb.rotation_quaternion = local_quat(bn, rots) if rots else Quaternion()
            pb.scale = sc if sc is not None else (1.0, 1.0, 1.0)
            for ch in ("location", "rotation_quaternion", "scale"):
                pb.keyframe_insert(ch, frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion) + list(pb.scale))
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["loop_seconds"] = N / K.FPS
    NEW_ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


TRAILV = np.arange(*RANGE["shell"])[tf > 0.5]
samples, clip_rep = [], {}
for cn, N in CLIPS.items():
    K.assign_action(rig, NEW_ACTS[cn])
    first = last = None
    rows = []
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
        if f == N + 1:
            last = C
        pbs = rig.pose.bones
        rows.append({"minz": float(C[:, 2].min()), "trail_maxlift": float((C[TRAILV, 2] - VALL[TRAILV, 2]).max()),
                     "head_z": float(pbs["head"].head.z), "root": float((rig.matrix_world @ pbs["root"].head).length)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "trail_max_lift": round(float(g_("trail_maxlift").max()), 4),
                    "head_z_range": [round(float(g_("head_z").min()), 4), round(float(g_("head_z").max()), 4)]}
    assert clip_rep[cn]["clearance_min_z"] > -2e-3, ("a clip dips below the floor", cn, clip_rep[cn])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("goo_shield v1: root > body.001 (base / puddle) > body.002 > neck > head (helmet + eyes); "
                       "body.001 > trail.001 > trail.002 > trail.003; the body armour rides body.002")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Goo Shield's movement or height; open "
                               "question): idle = 4 s goo wobble + breathing squash (base spread +-%.0f %%, upper body "
                               "counter-swell, layered sway); walk = 2 s in-place slug slide (a %.2f u lift hump + side "
                               "sway travelling down the trail, the base on the floor, a forward surge lean)"
                               % (100 * IDLE_BREATHE[0], WALK_HUMP[0]))
low["conquest_locomotion"] = "slug-slide placeholder: rest pose puddle bottom z 0 per contract; nothing goes below it"
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
                           "roughness": m.get("pbrMetallicRoughness", {}).get("roughnessFactor"),
                           "textures": sorted(k for k in m.get("pbrMetallicRoughness", {}) if k.endswith("Texture"))
                           + (["normalTexture"] if "normalTexture" in m else [])} for m in js.get("materials", [])],
            "primitives": [{"mesh": n, "material": js["materials"][p["material"]]["name"] if "material" in p else None,
                            "attributes": sorted(p["attributes"])} for n, p in prims],
            "animations": [a.get("name") for a in js.get("animations", [])], "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
              "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + skinned 'goo_shield' (opaque rock/eye primitive + alpha-blend glossy goo "
                           "primitive); natural scale; report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]["default"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
