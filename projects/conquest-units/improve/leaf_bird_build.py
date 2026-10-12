"""Leaf Bird -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/leaf_bird_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x3: Petal Cobra, Crystal Canine, Leaf Bird" (section LEAF BIRD) + the
sheet design/reference/leaf_bird-character-sheet.png (intake: design/reference/leaf_bird/intake.json). The sheet is titled
"LEAF FISH"; the artist corrected it: it is a BIRD (the two big lateral leaves are WINGS, the "dorsal fin" panel is a leaf
CREST on the back, the tail is a bird tail). Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v2 DRAFT.

v2 (review-log 2026-10-10 "Leaf Bird v1 verdict", artist: "get rid of the crist and slim the bird down so its more
leaf likle" + markup design/reference/leaf_bird-v1-crest-annotation.png, a red X over the crest): the back CREST is
REMOVED (part, vein net, bone, clip keys); the body is SLIMMED to a leaf (BODY_KX, SPINE_RSCALE, a slimmer pointed
head), the wings / tail lobes / belly bands refitted to it.

v3 (review-log 2026-10-11 "Artist sizes for the new units", artist: "leaf bird 1ft and its orientation needs to be fixed,
right now thats a top view so height may be wrong"): the REST POSE is re-oriented to a game unit in a level GLIDE. The
whole model is still BUILT in sheet coordinates (every sheet measurement keeps its meaning), then mapped by a piecewise-
rigid BEND along the body's medial curve: each medial segment keeps BEND_KEEP of its sheet tangent angle and the curve is
rotated so its head -> tail chord rises GLIDE_PITCH deg (a slight nose-down). The wings, laid on the medial surface, come
out spread to +-X (near horizontal), then get WING_DIHEDRAL. Front camera: the face + a thin wing line; side: the leaf
body in profile.

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. The sheet draws the bird in a DIVE (nose-down). Every constant is
measured off the SIDE view (the reconciliation view: the three views' drawn heights differ, front 4.5 / back 5.1 / side
4.3 u); the FRONT view supplies only what the side cannot show (the wings, the lateral widths, the eye spacing):
  SIDE view (faces image-left = the unit's front, -Y):  Y = (x_px - 960) / 100 ; z = (683 - y_px) / 100
  FRONT view (the unit's left = +X on image right):     X = (x_px - 235) / 100 ; z = (671 - y_px) / 100
X +ve = the unit's LEFT (L). Rest pose = the drawn dive; min z = 0 at the lowest point (the belly under the head: the
side view's lowest pixels, y 682 at x 832-872).

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf banded grid): the streamlined LEAF BODY = a chain of round cones along the side
     view's medial curve (each knot = the largest inscribed circle of the side silhouette, measured), laterally squeezed
     by BODY_KX (a leaf-flat body: front-view width / side-view depth), tapering to the sharp tail-leaf TIP; the round
     HEAD ellipsoid smooth-unioned at the front. Marching tets = the HIGH (bake source); collapse decimation = the LOW.
  2. REGIONS on the low shell, every boundary iso-cut: the dark MIDRIB line along the back + belly (|X| < MIDRIB_W), the
     thin light-green HIGHLIGHT streak along the top of the back, the dark leaf-vein SEGMENT BANDS on the flanks (the side
     view's belly curves, projected along X onto both flanks).
  3. PARTS (leaf_bird_parts.py, closed): the amber BEAK (smooth oval lathe cone), two round EYES + amber IRISES
     (conforming decals), two swept leaf WINGS (closed pillow sheets laid on the body's medial surface, outline + veins
     from the front view, veins iso-cut), two small TAIL LOBES (closed pillow leaves at the tail base).
  4. palette (palettes.py regions -> Col / Glow, no glow), Smart UV, bake normal + AO from the SDF high + parts, seed 0.
  5. RIG: root > body > head; body > wing_L.001 > wing_L.002 (+ R); body > tail.001 > tail.002. Clips
     (PLACEHOLDER, movement intent open): idle (hover-glide bob + wing flex) + walk (glide with a wing beat). glb.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K             # noqa: E402  (read-only use)
import palettes as PAL         # noqa: E402  (read-only use)
import magmoo_sdf as SD        # noqa: E402  (read-only use: the SDF kit)
import leaf_bird_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "leaf_bird"
CHAR_ID = "leaf_bird"                 # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.20                       # >= the largest smooth-union k (HEAD_K 0.12) + 2 steps (BUILD_CONTRACT hazard)
BODY_TRIS = 8000                      # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- BODY: the side view's medial curve, (Y, z, r) per knot = the centre + radius of the largest circle inscribed in the
#   side silhouette on the column x_px (crest / tail lobe / beak excluded from the outline). Columns x 770..1165 step 30
#   (then 15 / 10 at the tail): e.g. x 950 -> centre y 571, r 86 px; x 1100 -> y 440, r 51.6 px; x 1165 -> y 321, r 11 px.
#   The front knot sits behind the beak (head front x ~750); the last knot is the tail-leaf tip (1173, 262).
SPINE = [(-2.02, 0.56, 0.15), (-1.90, 0.588, 0.241), (-1.60, 0.548, 0.489), (-1.30, 0.621, 0.607), (-1.00, 0.713, 0.683),
         (-0.70, 0.823, 0.759), (-0.40, 0.946, 0.827), (-0.10, 1.117, 0.860), (0.20, 1.271, 0.890), (0.50, 1.470, 0.890),
         (0.80, 1.751, 0.844), (1.10, 2.046, 0.684), (1.40, 2.427, 0.516), (1.60, 2.706, 0.405), (1.80, 3.035, 0.287),
         (1.95, 3.323, 0.192), (2.05, 3.617, 0.110), (2.11, 3.95, 0.05), (2.13, 4.21, 0.012)]
SPINE_RSCALE = 0.80                   # "body depth" (v2 slim): every medial radius x this (the side silhouette slims; the
                                      #   tail tip stays a sharp point)
SPINE_DENSE = 4                       # catmull samples per knot span (smooth chain, no ribbing)
BODY_KX = 0.50                        # "body width": lateral half-width / side half-depth. v1 0.78 (the FRONT view's body
                                      #   between the wing roots); v2 0.50: the artist's "slim the bird down so its more
                                      #   leaf likle" -- a flat leaf blade side to side
HEAD_C, HEAD_R, HEAD_K = (0.0, -1.58, 0.58), (0.40, 0.58, 0.42), 0.12   # "head size": round head (half width / length /
                                      #   height); SIDE x 800 top y 567 / bottom 680 (z 0.03-1.16), head front x ~750;
                                      #   v1 (0.60, 0.55, 0.52); v2 slimmer + longer so the head tapers into the beak
                                      #   like a leaf's point (the eyes + beak stay readable)
# ---- BEAK (amber, smooth oval cone). SIDE: tip (713, 650) -> (-2.47, 0.33); base on the face from (736, 626) to
#   (755, 667) -> centre (-2.14, 0.37), half-height 0.21; FRONT: a diamond +-0.25 wide at its widest (y 640)
BEAK_BASE, BEAK_TIP = (0.0, -1.90, 0.40), (0.0, -2.47, 0.33)   # the base sits inside the head (buried)
BEAK_R = (0.20, 0.22)                 # "beak size": half height / half width at the base (v1 0.22 / 0.26: the slimmer
                                      #   head; the front view diamond is +-0.25)
BEAK_SEG = 24
# ---- EYES (round, off-white) + IRIS (amber). SIDE: white x 773-807 y 606-624 -> centre (-1.70, 0.68); iris x 783-804
#   y 606-620 (its centre 0.04 behind + 0.02 above the white's). FRONT: whites x 178-198 / 272-292 (X -0.47 / +0.47).
EYE_Y, EYE_Z = -1.86, 0.68            # "eye placement" (side view z; Y at the FRONT end of the drawn white x 773-807 --
                                      #   the sheet's centre x 790 sits on the head's flank, edge-on from the front, while
                                      #   the front view draws both eyes facing the viewer); cast onto the head along -X
EYE_R = 0.15                          # "eye size" (round: face panel 'ROUND EYE SHAPE')
EYE_FACE = 1.20                       # the eye faces this share further toward the front than the head normal (the
                                      #   front view draws both eyes facing the viewer)
IRIS_R = 0.085                        # "iris size"
IRIS_OFF = (0.03, 0.025)              # iris centre offset from the eye centre: (back +Y, up +z)
DECAL_UP, DECAL_DN, DECAL_DOME = 0.010, 0.03, 0.022
# ---- WINGS (L side, front-view coords (X, z); R mirrored). Laid on the body's MEDIAL surface (Y follows the side view's
#   medial curve at each z): from the side they are edge-on inside the body silhouette, from the front they are exactly
#   the drawn leaves. FRONT view (image right = +X): root line (wing / face boundary ink) x 296 @ y 460 -> 313 @ y 580 ->
#   305 @ y 610; inner free edge ink (302-307, 450) (338-344, 430) (376-379, 410); outer silhouette x 316 @ y 605,
#   x 385 @ y 525, x 411 @ y 455, x 427 @ y 365; tip ~ (427, 357).
WING_ROOT = [(0.72, 0.60), (0.77, 0.80), (0.78, 0.95), (0.76, 1.11), (0.72, 1.31), (0.67, 1.51), (0.65, 1.71),
             (0.62, 1.91), (0.61, 2.11)]                     # attached root line, bottom -> top
WING_INNER = [(0.61, 2.11), (0.70, 2.21), (0.86, 2.31), (1.06, 2.41), (1.28, 2.51), (1.46, 2.63), (1.62, 2.76),
              (1.76, 2.90), (1.86, 3.03), (1.92, 3.14)]      # free inner (upper) edge -> tip
WING_OUTER = [(0.72, 0.60), (0.81, 0.66), (0.96, 0.76), (1.07, 0.86), (1.17, 0.96), (1.26, 1.06), (1.39, 1.26),
              (1.50, 1.46), (1.60, 1.66), (1.68, 1.86), (1.74, 2.06), (1.79, 2.26), (1.84, 2.46), (1.88, 2.66),
              (1.90, 2.86), (1.92, 3.06), (1.92, 3.14)]      # outer (lower) edge, bottom -> tip
# "wing veins" (front-view ink runs): V1 the main vein / midrib from the root sweeping out to the outer edge at y 468
#   (x 383-403 @ y 470, 365-375 @ 480, 346-353 @ 500, 334-340 @ 520, 322-328 @ 550, 311-321 @ 570); V2, V3 hang from it
#   to the outer edge (x 359-366 @ 500 -> 374-377 @ 540; 339-345 @ 530 -> 349-352 @ 570)
WING_VEINS = [[(1.75, 2.03), (1.58, 2.01), (1.35, 1.91), (1.15, 1.71), (1.02, 1.51), (0.93, 1.31), (0.87, 1.11),
               (0.81, 1.01), (0.78, 0.95)],
              [(1.20, 1.76), (1.27, 1.71), (1.35, 1.61), (1.39, 1.51), (1.41, 1.41), (1.43, 1.30)],
              [(1.05, 1.47), (1.11, 1.31), (1.15, 1.21), (1.16, 1.11), (1.15, 1.01), (1.12, 0.92)]]
WING_VEIN_W = 0.030                   # "vein line width" (half): the ink runs are 5-8 px
WING_ROOT_SINK = 0.45                 # the attached root edge is pushed this far into the body (buried, no gap; v1 0.30,
                                      #   v2 deeper for the slimmer body); ramps in over the first 0.25 so the base is a point
WING_T = 0.030                        # "wing thickness" (half, mid-wing)
WING_NS, WING_NT = 28, 10             # "wing detail": rows root -> tip / columns inner -> outer edge
# ---- (v2) the back CREST is removed (artist v1 verdict: red X over it)
# ---- TAIL LOBES ("small second lobe"): SIDE tip (1210, 383) -> (2.50, 3.00), leaving the tail's lower edge at ~(1150, 455);
#   FRONT the two small leaves tip (170, 345) / (300, 355) -> X +-0.65; BACK tips (511, 318) / (670, 320) (a pair)
LOBE_BASE, LOBE_TIP = (0.06, 1.72, 2.55), (0.65, 2.50, 3.00)   # L side (base inside the slimmer tail; v1 base (0.12, 1.78, 2.42))
LOBE_W, LOBE_T = 0.22, 0.025          # "lobe width" (half, max; side view lobe ~33 px wide) / thickness (half)
LOBE_NS, LOBE_NT = 12, 4
# ---- REST ORIENTATION (v3: a game unit in a level glide; the sheet draws a steep dive seen partly from above)
GLIDE_PITCH = 5.0                     # "glide pitch": the head -> tail chord rises this many deg (nose slightly down)
BEND_KEEP = 0.35                      # "body curve": share of the sheet's medial bend kept (1 = the drawn dive's banana
                                      #   curve, 0 = a straight leaf); 0.35 keeps a gentle leaf curve, tail up
WING_DIHEDRAL = 8.0                   # "wing dihedral": deg the wings rise outboard of WING_DIH_X0
WING_DIH_X0 = 0.35                    # the dihedral starts at this |X| (inside the body: the root stays buried)
# ---- MARKINGS on the shell
MIDRIB_W, MIDRIB_Y0 = 0.035, -1.40    # "midrib": half width; it runs from behind the eyes (front view: the dark centre line
                                      #   x 230-240 from y 265 to 570; back view x 586-592 full length)
HL_W, HL_Y0 = 0.08, -2.15             # "highlight": half width of the light-green streak along the top of the back (side
                                      #   view top edge from the head (749,610) to the tail tip; front face centre streak)
BAND_W, BAND_XG = 0.035, 0.12         # "segment bands": half width; only where |X| > BAND_XG (the flanks)
# side-view belly ink (x -> y of the dark-green run centres): C1 the long curve (880,668) (940,588) (1000,545) (1060,516)
#   (1120,498); B1 (962,573) -> (977,650); B2 (1012,540) -> (1052,618); B3 (1070,513) -> (1108,553)
BANDS = {"C1": [(-0.80, 0.15), (-0.60, 0.50), (-0.40, 0.74), (-0.20, 0.95), (0.0, 1.09), (0.20, 1.25), (0.40, 1.38),
                (0.60, 1.49), (0.80, 1.59), (1.00, 1.67), (1.20, 1.75), (1.40, 1.81), (1.60, 1.85), (1.75, 1.87)],
         "B1": [(0.02, 1.10), (0.10, 0.83), (0.15, 0.58), (0.17, 0.33), (0.18, 0.20)],
         "B2": [(0.52, 1.43), (0.60, 1.31), (0.70, 1.20), (0.80, 1.06), (0.90, 0.80), (0.93, 0.60)],
         "B3": [(1.10, 1.70), (1.20, 1.60), (1.30, 1.54), (1.40, 1.44), (1.48, 1.30), (1.52, 1.20)]}
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig (weight bands, by the Y of the nearest medial-curve point)
NECK_BAND = (-1.33, -0.97)            # head -> body blend
TAIL1_BAND = (0.95, 1.25)             # body -> tail.001
TAIL2_BAND = (1.48, 1.72)             # tail.001 -> tail.002
# ---- clips (24 fps; integer harmonics per loop -> frame N+1 == frame 1). PLACEHOLDER: movement intent + height not given
HOVER_FRAC = 0.08                     # "hover height": both clips lift the body this fraction of the rest height
IDLE_N = 96                           # "idle loop": 4 s
IDLE_BOB = (0.012, 0.004)             # "hover-glide bob": body z amplitude x H at 2 cyc + a 3 cyc overtone
IDLE_PITCH, IDLE_ROLL = 1.5, 1.0      # glide wander deg (1 cyc)
IDLE_HEAD = 1.5                       # head nod deg (2 cyc)
WING_FLEX = (3.0, 6.0, 2, 0.6)        # "wing flex": (root deg, tip deg, cyc, lag) about the wing hinge (root line)
TAIL_IDLE = (2.0, 3.5, 1, 0.7)        # tail flick (pitch) per bone
WALK_N = 48                           # "glide loop": 2 s
WING_BEAT = (14.0, 22.0, 2, 0.5)      # "wing beat": (root deg, tip deg, cyc, lag) -- 2 beats per loop (1 Hz)
WALK_BOB = 0.015                      # body z amplitude x H (lift on the downstroke, 2 cyc)
WALK_PITCH = 1.5                      # pitch wobble deg (2 cyc)
TAIL_WALK = (3.0, 5.0, 2, 0.8)
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
          "design/reference/leaf_bird-character-sheet.png, titled 'LEAF FISH'; artist: it is a leaf BIRD)",
          "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; reconciled on the SIDE view (front view for the wings / widths); "
                   "floor z = 0 at the lowest point (the belly)",
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


def cut_mesh(V, F, fields, cuts):
    """firefly's iso-cutter on any mesh: fields {name: per-vertex array}, cuts [(name, tau, gate)] (gate None or
    [(field, tau), ...]: only edges whose both ends exceed every gate tau are cut) -> every cut line is split into the
    mesh (crossings within CUT_SNAP of a vertex snap to it: no slivers). Returns V, F (triangles), the per-face mean of
    every field, the cut log."""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {k: bm.verts.layers.float.new(k) for k in fields}
    for v in bm.verts:
        for k, arr in fields.items():
            v[LAY[k]] = float(arr[v.index])

    def iso_cut(key, tau, gate=None):
        L = LAY[key]
        eps = 1e-5 * max(1.0, abs(tau))
        on = lambda v: abs(v[L] - tau) <= eps
        side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
        GL = [(LAY[gk], gt) for gk, gt in (gate or [])]           # gate: every (field, tau) > tau at both ends
        ok = lambda a, b: all(a[l_] > t_ and b[l_] > t_ for l_, t_ in GL)
        for e in bm.edges:
            a, b = e.verts
            sa, sb = side(a), side(b)
            if sa * sb < 0 and ok(a, b):
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts_ = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
        for e in cuts_:
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
        return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts_), "face_connects": len(pairs)}
    log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    CV_ = np.array([v.co[:] for v in bm.verts])
    CF_ = [[v.index for v in f.verts] for f in bm.faces]
    FV_ = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
    bm.free()
    return CV_, CF_, FV_, log


for o in list(bpy.data.objects):                 # factory scene: cube, camera, light never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

# =========================================================================== 1. body shell SDF
t_sdf = time.time()
SIDES = (("L", 1.0), ("R", -1.0))
_SP = np.array(SPINE, float); _SP[:, 2] *= SPINE_RSCALE
_dense = SD.catmull(_SP, SPINE_DENSE)                            # (Y, z, r) along the medial curve
_dense[:, 2] = np.clip(_dense[:, 2], 0.008, None)
SPY, SPZ, SPR = _dense[:, 0], _dense[:, 1], _dense[:, 2]
SPINE3 = np.stack([np.zeros_like(SPY), SPY, SPZ], 1)
SP_ARC = SD.arclen(SPINE3)
_spy_mono = np.maximum.accumulate(SPY)
_i0 = int(np.argmin(SPZ[:len(SPZ) // 3]))                          # the medial curve rises monotonically from here
_wz = np.maximum.accumulate(SPZ[_i0:]); _wy = SPY[_i0:]


def zc_of_y(Y):
    """medial-curve height at sagittal Y (the side view)"""
    return np.interp(Y, _spy_mono, SPZ)


def yw_of_z(z):
    """medial-curve Y at height z (the wings lie on the medial surface)"""
    return np.interp(z, _wz, _wy)


def dyw_dz(z, h=0.01):
    return (yw_of_z(np.asarray(z) + h) - yw_of_z(np.asarray(z) - h)) / (2 * h)


def sq(P):
    Q = P.copy(); Q[:, 0] = Q[:, 0] / BODY_KX                  # lateral squeeze: d x KX is a valid lower bound
    return Q


def sd_body(P):
    Q = sq(P)
    d = np.full(len(P), 1e9)
    for k in range(len(_dense) - 1):
        a = (0.0, SPY[k], SPZ[k]); b = (0.0, SPY[k + 1], SPZ[k + 1])
        d = np.minimum(d, cone_sd(Q, a, b, SPR[k], SPR[k + 1]))
    return d * BODY_KX


def cone_sd(Q, a, b, r0, r1):
    L = float(np.linalg.norm(np.subtract(b, a)))
    if L <= abs(r0 - r1) + 1e-4:                               # one sphere swallows the other
        return SD.sd_sphere(Q, a if r0 >= r1 else b, max(r0, r1))
    return SD.sd_round_cone(Q, a, b, r0, r1)


def sd_head(P):
    return SD.sd_ellipsoid(P, HEAD_C, HEAD_R)


GROUPS = ["body", "head"]


def group_sdf(P):
    return np.stack([sd_body(P), sd_head(P)], 1)


g = SD.Grid((-0.78, -2.25, -0.06), (0.78, 2.25, 4.30), SDF_STEP, SDF_BAND)
for k in range(len(_dense) - 1):
    a_ = (0.0, SPY[k], SPZ[k]); b_ = (0.0, SPY[k + 1], SPZ[k + 1]); r0_, r1_ = SPR[k], SPR[k + 1]
    rm = max(r0_, r1_)
    lo_ = np.array([-BODY_KX * rm, min(a_[1], b_[1]) - rm, min(a_[2], b_[2]) - rm])
    hi_ = np.array([BODY_KX * rm, max(a_[1], b_[1]) + rm, max(a_[2], b_[2]) + rm])
    g.apply(lambda P, a_=a_, b_=b_, r0_=r0_, r1_=r1_: cone_sd(sq(P), a_, b_, r0_, r1_) * BODY_KX, lo_, hi_, 0.0)
g.apply(sd_head, np.array(HEAD_C) - HEAD_R, np.array(HEAD_C) + HEAD_R, HEAD_K)
HV, HF = SD.polygonise(g)                         # the kit pushes |F| < NODE_EPS * step off zero (BUILD_CONTRACT hazard)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "spine_knots": len(SPINE), "spine_dense": len(_dense), "crumbs": crumbs,
                 "seconds": round(time.time() - t_sdf, 1)}
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
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks, "doubled_faces_removed": DOUBLED,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(),
                    "seconds": round(time.time() - t, 1)}
print("RETOPO", json.dumps(report["retopo"]))
assert np.all(_bb_l / _bb_h > 0.99), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))


# =========================================================================== 3. markings: iso-cut fields on the shell
GATE_R = 0.15                        # line cuts run only within this distance of their polyline


def line_fields(Q2, lines, prefix):
    """per polyline: signed distance 's<k>' (cut at +-w) + negated true distance 'n<k>' (the cut gate / end test)"""
    out = {}
    for k, pts in enumerate(lines):
        s_, d_ = FP.poly_signed(Q2, pts)
        out["%ss%d" % (prefix, k)] = s_; out["%sn%d" % (prefix, k)] = -d_
    return out


def line_cuts(lines, prefix, w, extra_gate=()):
    return [("%ss%d" % (prefix, k), sg_ * w, [("%sn%d" % (prefix, k), -GATE_R)] + list(extra_gate))
            for k in range(len(lines)) for sg_ in (1.0, -1.0)]


def on_lines(C2, lines, w):
    """faces inside a line band, evaluated EXACTLY at the face centroids C2 (sheet-view 2D coords; the cut lines bound
    every face, so a centroid test is the region test): |s| < w and within the band's end cap"""
    m = np.zeros(len(C2), bool)
    for pts in lines:
        s_, d_ = FP.poly_signed(C2, pts)
        m |= (np.abs(s_) < w) & (d_ < 1.25 * w)
    return m


def centroids(V, F):
    return np.array([V[f].mean(0) for f in F])


def refit_line(pts):
    """the side view's band ink was drawn on the v1-depth body: pull each point toward its nearest medial-curve point by
    SPINE_RSCALE so the band keeps its place on the slimmer flank"""
    out = []
    for y, z in pts:
        k = int(np.argmin((SPY - y) ** 2 + (SPZ - z) ** 2))
        out.append((SPY[k] + (y - SPY[k]) * SPINE_RSCALE, SPZ[k] + (z - SPZ[k]) * SPINE_RSCALE))
    return out


BAND_LINES = [refit_line(v) for v in BANDS.values()]
_LV_fields = {"x": LV[:, 0], "y": LV[:, 1], "nax": -np.abs(LV[:, 0]), "ax": np.abs(LV[:, 0]),
              "dz": LV[:, 2] - zc_of_y(LV[:, 1]), **line_fields(LV[:, 1:3], BAND_LINES, "b")}
_LV_fields["bnear"] = np.max([_LV_fields["bn%d" % k] for k in range(len(BAND_LINES))], 0)
SHELL_CUTS = ([("x", sg_ * MIDRIB_W, [("y", MIDRIB_Y0 - 0.1)]) for sg_ in (1.0, -1.0)] +
              [("y", MIDRIB_Y0, [("nax", -0.1)])] +
              [("x", sg_ * HL_W, [("dz", -0.1), ("y", HL_Y0 - 0.1)]) for sg_ in (1.0, -1.0)] +
              line_cuts(BAND_LINES, "b", BAND_W, [("ax", BAND_XG - 0.06)]) +
              [("ax", BAND_XG, [("bnear", -0.1)])])
t = time.time()
CV, CF, FVAL, cut_log = cut_mesh(LV, LF, _LV_fields, SHELL_CUTS)
CC = centroids(CV, CF)
is_mid = (np.abs(CC[:, 0]) < MIDRIB_W) & (CC[:, 1] > MIDRIB_Y0)
is_hl = (np.abs(CC[:, 0]) < HL_W) & (CC[:, 2] > zc_of_y(CC[:, 1])) & (CC[:, 1] > HL_Y0)
is_band = on_lines(CC[:, 1:3], BAND_LINES, BAND_W) & (np.abs(CC[:, 0]) > BAND_XG)
shell_reg = np.full(len(CF), "body", dtype=object)
shell_reg[is_hl] = "highlight"
shell_reg[is_band] = "marking"
shell_reg[is_mid] = "marking"
_shell_counts = {r: int(np.sum(shell_reg == r)) for r in ("body", "highlight", "marking")}
_feat = {"midrib": int(np.sum(is_mid)), "highlight": int(np.sum(is_hl & ~is_mid)), "bands": int(np.sum(is_band & ~is_mid))}
assert all(v > 0 for v in _feat.values()), "a shell marking is empty (it would borrow a neighbour's paint): %s" % _feat
_m35 = np.abs(CV[:, 1] - 0.35) < 0.03
report["slim"] = {"shell_max_half_width": round(float(np.abs(CV[:, 0]).max()), 3),
                  "mid_body_Y0.35_half_width": round(float(np.abs(CV[_m35, 0]).max()), 3),
                  "mid_body_Y0.35_depth_z": round(float(CV[_m35, 2].max() - CV[_m35, 2].min()), 3),
                  "head_Y-1.6_half_width": round(float(np.abs(CV[np.abs(CV[:, 1] + 1.6) < 0.03, 0]).max()), 3),
                  "v1": {"shell_max_half_width": 0.698, "mid_body_Y0.35_half_width": 0.698,
                         "mid_body_Y0.35_depth_z": 2.111, "head_Y-1.6_half_width": 0.598},
                  "knobs": {"BODY_KX": BODY_KX, "SPINE_RSCALE": SPINE_RSCALE, "HEAD_R": HEAD_R}}
print("SLIM", json.dumps(report["slim"]))
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF),
                      "shell_region_faces": _shell_counts, "feature_faces": _feat}

# =========================================================================== 4. parts
bvh_c = BVHTree.FromPolygons(CV.tolist(), CF)


def cast(origin, d, dist=6.0):
    loc, nrm, idx, dd = bvh_c.ray_cast(Vector(origin), Vector(d), dist)
    if loc is None:
        loc, nrm, idx, dd = bvh_c.find_nearest(Vector(origin))
    return np.array(loc), np.array(nrm)


def project(pts, n):
    return np.array([cast(p + n * 0.4, -n, 1.2)[0] for p in pts])


PARTS = []
# ---- beak
_ba, _bt = np.array(BEAK_BASE, float), np.array(BEAK_TIP, float)
_bax = unit(_bt - _ba); _bl = float(np.linalg.norm(_bt - _ba))
_bprof = [(0.0, -0.05), (1.0, -0.05), (1.0, 0.0), (0.95, 0.30 * _bl), (0.62, 0.60 * _bl), (0.30, 0.84 * _bl), (0.0, _bl)]
V, F, R = FP.lathe(_bprof, "beak", BEAK_SEG, _ba, _bax, up_hint=(0, 0, 1), su=BEAK_R[0], sv=BEAK_R[1])
PARTS.append(FP.Part("beak", V, F, R, bone="head"))
# ---- eyes + irises
FACE = {}
for s, sg in SIDES:
    S, n = cast((sg * 3.0, EYE_Y, EYE_Z), (-sg, 0.0, 0.0))
    ne = unit(n + EYE_FACE * np.array([0.0, -1.0, 0.0]))
    V, F, R = FP.polar_decal(S, ne, FP.circle_curve(EYE_R, 28), project, DECAL_UP, DECAL_DN, DECAL_DOME, 4, "eye")
    PARTS.append(FP.Part("eye." + s, V, F, R, bone="head"))
    Si, _ = cast((sg * 3.0, EYE_Y + IRIS_OFF[0], EYE_Z + IRIS_OFF[1]), (-sg, 0.0, 0.0))
    V, F, R = FP.polar_decal(Si, ne, FP.circle_curve(IRIS_R, 24), project, DECAL_UP + DECAL_DOME + 0.006, DECAL_DN,
                             0.006, 3, "iris")
    PARTS.append(FP.Part("iris." + s, V, F, R, bone="head"))
    FACE["eye." + s] = {"surface": S.round(4).tolist(), "normal": ne.round(4).tolist()}


def finish_leaf(name, V, F, proj, veins, w, bone=None, chain=None):
    """iso-cut the leaf's vein lines (proj(V) = the sheet-view 2D coords they are drawn in) into the closed pillow along
    their offset curves, paint the vein bands 'marking', the rest 'body'"""
    V2, F2, FV, log = cut_mesh(V, F, line_fields(proj(V), veins, "v"), line_cuts(veins, "v", w))
    vein = on_lines(proj(centroids(V2, F2)), veins, w)
    R2 = ["marking" if m_ else "body" for m_ in vein]
    p = FP.Part(name, V2, F2, R2, bone=bone)
    p.chain = chain
    p.cut = {"cuts": len(log), "edge_splits": int(sum(c["edge_splits"] for c in log)), "vein_faces": int(vein.sum())}
    assert vein.sum() > 0, "leaf %s has no vein faces" % name
    return p


# ---- wings (front-view outline on the medial surface)
def to3d(sg, XZ):
    XZ = np.asarray(XZ, float)
    return np.stack([sg * XZ[..., 0], yw_of_z(XZ[..., 1]), XZ[..., 1]], -1)


_root_z = np.array([p[1] for p in WING_ROOT]); _root_x = np.array([p[0] for p in WING_ROOT])


def x_root(z):
    return np.interp(z, _root_z, _root_x)


WING = {}
for s, sg in SIDES:
    _rz = np.array([z for x, z in WING_ROOT])
    _ramp = smoothstep(WING_ROOT[0][1], WING_ROOT[0][1] + 0.25, _rz)       # the leaf base stays a point (no flat cut)
    inner = [(x - WING_ROOT_SINK * r_, z) for (x, z), r_ in zip(WING_ROOT, _ramp)] + list(WING_INNER[1:])
    Ii = FP.resample_poly(inner, WING_NS + 1)
    Oo = FP.resample_poly(WING_OUTER, WING_NS + 1)
    tt = np.linspace(0.0, 1.0, WING_NT + 1)
    G2 = Ii[:, None, :] * (1 - tt)[None, :, None] + Oo[:, None, :] * tt[None, :, None]      # front-view (X, z) grid
    P = to3d(sg, G2)
    N = np.stack([np.zeros(G2.shape[:2]), -np.ones(G2.shape[:2]), dyw_dz(G2[..., 1])], -1)
    N = N / np.linalg.norm(N, axis=-1, keepdims=True)
    ss = np.linspace(0.0, 1.0, WING_NS + 1)
    Hh = WING_T * (np.sin(math.pi * tt)[None, :] ** 0.5) * (1.0 - 0.55 * ss[:, None])
    V, F, R, IJ = FP.pillow(P, N, Hh, lambda i, j, top_: "body")
    p = finish_leaf("wing." + s, V, F, lambda V_: np.stack([np.abs(V_[:, 0]), V_[:, 2]], 1), WING_VEINS, WING_VEIN_W)
    PARTS.append(p)
    WING[s] = {"hinge": (to3d(sg, WING_ROOT[0]), to3d(sg, WING_ROOT[-1])), "bones": [to3d(sg, (0.72, 1.31)),
               to3d(sg, (1.35, 2.00)), to3d(sg, (1.92, 3.14))], "tip": to3d(sg, WING_INNER[-1])}
WING_LAT = float(max(np.max(np.abs(p.V[:, 0]) - x_root(p.V[:, 2])) for p in PARTS if p.name.startswith("wing")))

# ---- tail lobes (small leaves at the tail base)
for s, sg in SIDES:
    b0 = np.array([sg * LOBE_BASE[0], LOBE_BASE[1], LOBE_BASE[2]]); b1 = np.array([sg * LOBE_TIP[0], LOBE_TIP[1], LOBE_TIP[2]])
    ax = unit(b1 - b0)
    c_ = np.array([-sg, 1.0, 0.0]) / math.sqrt(2.0)              # width direction: broad in the front, side AND back views
    W_ = unit(c_ - ax * float(c_ @ ax))
    Nn = unit(np.cross(ax, W_))
    ss = np.linspace(0.0, 1.0, LOBE_NS + 1); tt = np.linspace(-1.0, 1.0, LOBE_NT + 1)
    hw = LOBE_W * np.sin(math.pi * np.clip(ss, 0, 1) ** 0.75) ** 0.9
    P = b0 + ss[:, None, None] * (b1 - b0) + (tt[None, :] * hw[:, None])[..., None] * W_
    Hh = LOBE_T * (1.0 - tt[None, :] ** 2) ** 0.5 * np.sin(math.pi * np.clip(ss, 0, 1) ** 0.75)[:, None] ** 0.5
    V, F, R, IJ = FP.pillow(P, np.broadcast_to(Nn, P.shape), Hh, lambda i, j, top_: "body")
    PARTS.append(FP.Part("lobe." + s, V, F, R, bone="tail.002"))

# =========================================================================== 4b. rest orientation: the glide bend
_EXT = 2.0
_t0 = unit([SPY[1] - SPY[0], SPZ[1] - SPZ[0]]); _t1 = unit([SPY[-1] - SPY[-2], SPZ[-1] - SPZ[-2]])
BP = np.vstack([[SPY[0] - _t0[0] * _EXT, SPZ[0] - _t0[1] * _EXT], np.stack([SPY, SPZ], 1),
                [SPY[-1] + _t1[0] * _EXT, SPZ[-1] + _t1[1] * _EXT]])          # medial curve, straight-extended ends
_seg = np.diff(BP, axis=0); _len = np.linalg.norm(_seg, axis=1); _dir = _seg / _len[:, None]
_th = np.unwrap(np.arctan2(_dir[:, 1], _dir[:, 0]))
_i_head, _i_tail = 1, len(BP) - 2                                          # first / last real medial point


def _bent(c):
    th2 = BEND_KEEP * _th + c
    d2 = np.stack([np.cos(th2), np.sin(th2)], 1)
    pts = np.vstack([[0.0, 0.0], np.cumsum(d2 * _len[:, None], 0)])
    return pts, d2


_p0, _ = _bent(0.0)
_alpha = math.atan2(_p0[_i_tail, 1] - _p0[_i_head, 1], _p0[_i_tail, 0] - _p0[_i_head, 0])
BEND_C = math.radians(GLIDE_PITCH) - _alpha
BP2, BD2 = _bent(BEND_C)
_ch = BP2[_i_tail] - BP2[_i_head]


def glide_map(P, wing=False):
    """sheet coords -> rest (glide) coords: per medial segment a rigid YZ transform (the point's nearest segment,
    along-param and signed normal offset are kept; X is untouched). Wing points then rise WING_DIHEDRAL outboard of
    WING_DIH_X0."""
    P = np.atleast_2d(np.asarray(P, float))
    Q = P[:, 1:3]
    best = np.full(len(P), 1e18); kk = np.zeros(len(P), int)
    for k in range(len(_seg)):
        u = np.clip(((Q - BP[k]) @ _seg[k]) / (_len[k] ** 2), 0.0, 1.0)
        d = np.linalg.norm(Q - BP[k] - np.outer(u, _seg[k]), axis=1)
        m = d < best
        best[m] = d[m]; kk[m] = k
    R = Q - BP[kk]
    a = np.einsum("ij,ij->i", R, _dir[kk])                                     # along the segment
    n = _dir[kk, 0] * R[:, 1] - _dir[kk, 1] * R[:, 0]                          # left normal offset
    d2 = BD2[kk]; n2 = np.stack([-d2[:, 1], d2[:, 0]], 1)
    out = P.copy()
    out[:, 1:3] = BP2[kk] + a[:, None] * d2 + n[:, None] * n2
    if wing:
        out[:, 2] += math.tan(math.radians(WING_DIHEDRAL)) * np.maximum(np.abs(out[:, 0]) - WING_DIH_X0, 0.0)
    return out


CV_SHEET = CV.copy()
for p in PARTS:
    p.Vs = p.V.copy()
    p.V = glide_map(p.V, wing=p.name.startswith("wing"))
CV = glide_map(CV)
HV = glide_map(HV)
for s in WING:
    WING[s]["hinge"] = tuple(glide_map(h, wing=True)[0] for h in WING[s]["hinge"])
    WING[s]["bones"] = [glide_map(b, wing=True)[0] for b in WING[s]["bones"]]
    WING[s]["tip"] = glide_map(WING[s]["tip"], wing=True)[0]
report["orientation"] = {
    "rule": "built in sheet coords (the drawn dive), mapped by a piecewise-rigid bend along the medial curve",
    "glide_pitch_deg_chord_head_to_tail": round(math.degrees(math.atan2(_ch[1], _ch[0])), 3),
    "sheet_chord_deg": round(math.degrees(math.atan2(BP[_i_tail, 1] - BP[_i_head, 1], BP[_i_tail, 0] - BP[_i_head, 0])), 3),
    "head_end_tangent_deg": round(math.degrees(math.atan2(BD2[_i_head, 1], BD2[_i_head, 0])), 3),
    "tail_end_tangent_deg": round(math.degrees(math.atan2(BD2[_i_tail - 1, 1], BD2[_i_tail - 1, 0])), 3),
    "bend_keep": BEND_KEEP, "wing_dihedral_deg": WING_DIHEDRAL,
    "sheet_bend_total_deg": round(math.degrees(_th[_i_tail - 1] - _th[_i_head]), 2)}
print("ORIENT", json.dumps(report["orientation"]))

# =========================================================================== 5. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
_shell_lo = float(CV[:, 2].min())
_below = {nm: round(float(V_[:, 2].min()), 4) for nm, V_, F_, R_, p_ in ISL[1:] if V_[:, 2].min() < _shell_lo - 1e-3}
assert not _below, "a part dips below the floor (the belly, z %.4f): %s" % (_shell_lo, _below)
allV = np.vstack([i[1] for i in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, RANGE, VSHEET = [], [], [], {}, []
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    VSHEET.append(CV_SHEET if p is None else p.Vs)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    nv_ += len(V)
VALL = np.vstack(VALL)
VSHEET = np.vstack(VSHEET)                       # the same vertices in sheet coords (weights + sheet-drawn fields)
assert float(VALL[:, 2].min()) >= -1e-9, "floor"
# mirror test (memory 'symmetric rest pose'): every vertex mirrored in X lands on the rest mesh's surface
_bvh_m = BVHTree.FromPolygons(VALL.tolist(), FALL)
_mir = np.array([_bvh_m.find_nearest(Vector((-p_[0], p_[1], p_[2])))[3] for p_ in VALL])
report["mirror_test"] = {"max": round(float(_mir.max()), 5), "p99": round(float(np.percentile(_mir, 99)), 5),
                         "rule": "distance from each vertex's X-mirror to the rest mesh surface (the decimated shell "
                                 "is not vertex-symmetric; its surface is)"}
_imax = int(np.argmax(_mir)); report["mirror_test"]["worst"] = [k for k, (a0, a1) in RANGE.items() if a0 <= _imax < a1][0]
report["mirror_test"]["per_island_max"] = {k: round(float(_mir[a0:a1].max()), 5) for k, (a0, a1) in RANGE.items()}
report["mirror_test"]["tolerance"] = ("one SDF step (%.3f): the kit's marching-tets Kuhn split (0->7 diagonal) is not "
                                        "X-mirror symmetric, so the extracted shell differs by up to a grid step where the "
                                        "surface is sharper than the step (the tail-leaf tip)" % SDF_STEP)
assert _mir.max() < 1.5 * SDF_STEP, report["mirror_test"]
print("MIRROR", json.dumps(report["mirror_test"]))
HV_s = HV - SHIFT
REG = ["body", "highlight", "marking", "beak", "eye", "iris"]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
_empty = [r for j, r in enumerate(REG) if not np.any(rid == j)]
assert not _empty, "empty palette regions (a 0-face region must never borrow a neighbour's field): %s" % _empty


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


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)
_lit = sorted(n for n, v in pal_default["regions"].items() if "emission" in v)
report["glow"] = {"skin": "default", "glowing_regions": _lit, "rule": "no glow (the sheet draws none): zero Glow"}
assert not _lit, _lit

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
PAL.store_regions(me, REG, rid, jitter(VALL, FALL))
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the beak tip (on the midline)
anchor = glide_map(HEAD_C)[0] - SHIFT
landmark = glide_map(_bt)[0] - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (HEAD_C) -> the beak tip on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["face"] = FACE
report["wings"] = {s: {"hinge": [np.round(h - SHIFT, 4).tolist() for h in w["hinge"]],
                       "tip": np.round(w["tip"] - SHIFT, 4).tolist()} for s, w in WING.items()}
report["leaf_cuts"] = {p.name: p.cut for p in PARTS if hasattr(p, "cut")}

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
    key = {"beak": "beak", "eye": "eyes", "iris": "irises", "wing": "wings", "lobe": "tail_lobes"}[p.name.split(".")[0]]
    _part_tris[key] = _part_tris.get(key, 0) + p.tris()
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_cuts": tri_count_F(LF), **_part_tris,
                  "per_part": {p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit): window [%d, %d]. The shell carries the leaf body, head and tail "
                            "with the iso-cut midrib / highlight / belly bands; the two big leaf wings "
                            "(closed pillows with iso-cut veins) are the next biggest shares." % tuple(TRI_BUDGET))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every piece is a closed solid (shell, beak, eye + iris decals, wing / lobe pillows)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "depth_px": round(100 * float(hi_a[1] - lo_a[1]), 1)},
                     "sheet": {"front_view_height_px": 450, "front_view_width_px": 389, "back_view_height_px": 508,
                               "back_view_width_px": 256, "side_view_height_px": 434, "side_view_length_px": 500},
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
low["conquest_source"] = "from scratch: design/reference/leaf_bird-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (all zero: no glow)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's four detail panels
_wing_pts = np.vstack([p.V for p in PARTS if p.name == "wing.L"]) - SHIFT
_tail_pts = np.vstack([p.V for p in PARTS if p.name.startswith("lobe")] + [glide_map(SPINE3[SPY > 1.3])]) - SHIFT


def _mbox(lo, hi):
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], float)
    m = glide_map(c) - SHIFT
    return [m.min(0).tolist(), m.max(0).tolist()]
low["conquest_focus"] = json.dumps({
    "face": _mbox((-0.7, -2.6, 0.0), (0.7, -1.1, 1.25)),
    "wing": [_wing_pts.min(0).tolist(), _wing_pts.max(0).tolist()],
    "markings": _mbox((-0.7, -0.6, 0.0), (0.7, 1.6, 1.9)),
    "tail": [(_tail_pts.min(0) - [0.2, 0.2, 0.2]).tolist(), (_tail_pts.max(0) + [0.2, 0.2, 0.1]).tolist()],
    "views": {"face": [60.0, 5.0], "wing": [35.0, 35.0], "markings": [90.0, 6.0], "tail": [120.0, 10.0]},
    "axis_xy": [0.0, 0.0]})                      # v3: the sheet's dive axes no longer apply; orthos centre on the bbox

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "iso_cuts", "tris", "measure", "facing",
                                                            "open_edges", "regions_area_share", "glow", "face")},
                                default=str))
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
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part",
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


def spine_at(Y):
    return glide_map([0.0, float(Y), float(zc_of_y(Y))])[0]


BONES = [("body", P_(spine_at(-1.0)), P_(spine_at(1.10)), "root"),
         ("head", P_(spine_at(-1.15)), P_(glide_map((0.0, -2.30, 0.45))[0]), "body"),
         ("tail.001", P_(spine_at(1.10)), P_(spine_at(1.60)), "body"),
         ("tail.002", P_(spine_at(1.60)), P_(glide_map(SPINE3[-1])[0]), "tail.001")]
for s, sg in SIDES:
    b0, b1, b2 = WING[s]["bones"]
    BONES += [("wing_%s.001" % s, P_(b0), P_(b1), "body"), ("wing_%s.002" % s, P_(b1), P_(b2), "wing_%s.001" % s)]
CONNECT = {"tail.002", "wing_L.002", "wing_R.002"}
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
    e.use_connect = nm in CONNECT
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 4 influences)
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
Pw = VSHEET[a_:b_]                                # sheet coords: the medial-curve bands
_, s_arc, _ = FP.spine_param(np.stack([np.zeros(len(Pw)), Pw[:, 1], Pw[:, 2]], 1), SPINE3)
Yn = np.interp(s_arc, SP_ARC, SPY)                              # Y of the nearest medial-curve point
w_body_from_head = smoothstep(NECK_BAND[0], NECK_BAND[1], Yn)
w_t1 = smoothstep(TAIL1_BAND[0], TAIL1_BAND[1], Yn)
w_t2 = smoothstep(TAIL2_BAND[0], TAIL2_BAND[1], Yn)
Wt[a_:b_, J["head"]] = 1.0 - w_body_from_head
Wt[a_:b_, J["body"]] = w_body_from_head * (1.0 - w_t1)
Wt[a_:b_, J["tail.001"]] = w_t1 * (1.0 - w_t2)
Wt[a_:b_, J["tail.002"]] = w_t2
for p in PARTS:
    a_, b_ = RANGE[p.name]
    Pp = p.Vs                                     # sheet coords (front-view root line etc.)
    if p.name.startswith("wing"):
        s = p.name.split(".")[1]
        s_lat = np.clip(np.abs(Pp[:, 0]) - x_root(Pp[:, 2]), 0.0, WING_LAT)
        Wv = K.vine_weights(s_lat, WING_LAT, 2, parent_s0=0.0)
        Wt[a_:b_, J["body"]] += Wv[:, 0]
        Wt[a_:b_, J["wing_%s.001" % s]] += Wv[:, 1]
        Wt[a_:b_, J["wing_%s.002" % s]] += Wv[:, 2]
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
                  "rule": "shell: by the Y of the nearest medial-curve point, head | body | tail.001 | tail.002 smoothstep "
                          "bands %s / %s / %s; wings: rigkit.vine_weights by the lateral distance from the root line "
                          "(parent body); beak, eyes, irises "
                          "rigid head; tail lobes rigid tail.002" % (NECK_BAND, TAIL1_BAND, TAIL2_BAND)}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form in the loop phase u (integer harmonics), posed per frame with no action bound (pass 1, floor
#      guard lifting the body if any vertex dips below z 0), keyed from the stored channels (pass 2, frame N+1 = frame 1),
#      measured as the checker plays them (pass 3)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
REST_H = {b.name: np.array(b.head_local) for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
KEY_BONES = DEFORM
HH = report["measure"]["height"]
HOVER = HOVER_FRAC * HH


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def world_rot(rots):
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        av = Vector(AXV[axis]) if isinstance(axis, str) else Vector(axis)
        Rw = Matrix.Rotation(math.radians(deg), 3, av) @ Rw
    return Rw


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ world_rot(rots) @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


# wing hinge axis = the root line; sign fixed so that + raises the wing (up), mirrored L / R
WING_HINGE = {}
for s, sg in SIDES:
    h0, h1 = WING[s]["hinge"]
    hax = unit(h1 - h0)
    nf_ = np.array([0.0, 0.0, 1.0])                # v3: + raises the wing (up: the glide pose's top face)
    if float(np.cross(hax, np.array([sg, 0.0, 0.0])) @ nf_) < 0:
        hax = -hax
    WING_HINGE[s] = tuple(hax)


def wings_pose(P, u, spec, phase0):
    a0, a1, cyc, lag = spec
    for s, sg in SIDES:
        for k in range(2):
            ang = K.vine_wave(u, k, 2, a0, a1, lag, cyc, phase0)
            P["wing_%s.%03d" % (s, k + 1)] = (None, [(WING_HINGE[s], ang)], None)


def tail_pose(P, u, spec, phase0):
    a0, a1, cyc, lag = spec
    for k in range(2):
        P["tail.%03d" % (k + 1)] = (None, [("X", K.vine_wave(u, k, 2, a0, a1, lag, cyc, phase0))], None)


def idle_pose(u):
    """PLACEHOLDER hover-glide: the body bobs (2 cyc + a 3 cyc overtone) with a slow pitch / roll wander, the head nods,
    the wings flex gently about their hinge (2 cyc, root -> tip lag), the tail flicks."""
    P = {}
    bob = HH * (IDLE_BOB[0] * math.sin(TAU * 2 * u) + IDLE_BOB[1] * math.sin(TAU * 3 * u + 0.9))
    P["body"] = ((0.0, 0.0, HOVER + bob), [("X", IDLE_PITCH * math.sin(TAU * u)), ("Y", IDLE_ROLL * math.sin(TAU * u + 1.3))],
                 None)
    P["head"] = (None, [("X", IDLE_HEAD * math.sin(TAU * 2 * u - 0.8))], None)
    wings_pose(P, u, WING_FLEX, 0.0)
    tail_pose(P, u, TAIL_IDLE, 0.4)
    return P, {"lift": HOVER + bob}


def walk_pose(u):
    """PLACEHOLDER glide with a wing beat: 2 beats per loop; the body lifts on each downstroke (bob anti-phase with the
    wing angle), a small pitch wobble, the tail counters."""
    P = {}
    beat = math.sin(TAU * WING_BEAT[2] * u)
    bob = -HH * WALK_BOB * beat
    P["body"] = ((0.0, 0.0, HOVER + bob), [("X", WALK_PITCH * math.sin(TAU * 2 * u + 0.6))], None)
    P["head"] = (None, [("X", -0.6 * WALK_PITCH * math.sin(TAU * 2 * u + 0.6))], None)
    wings_pose(P, u, WING_BEAT, 0.0)
    tail_pose(P, u, TAIL_WALK, 1.6)
    return P, {"lift": HOVER + bob}


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
        if bn == "body":
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
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "wing_tip_L": list(pbs["wing_L.002"].tail), "wing_tip_R": list(pbs["wing_R.002"].tail),
                     "tail_tip": list(pbs["tail.002"].tail), "head_tail": list(pbs["head"].tail),
                     "root": float((rig.matrix_world @ pbs["root"].head).length)})
    rig.animation_data.action = None
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - REST_H["body"][2]
    wl = np.array([r["wing_tip_L"] for r in R_]); wr = np.array([r["wing_tip_R"] for r in R_])
    tt_ = np.array([r["tail_tip"] for r in R_]); hd = np.array([r["head_tail"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "body_lift": [round(float(bz.min()), 5), round(float(bz.max()), 5)],
                    "floor_guard_lift": {"frames_lifted": int(sum(1 for x in GUARD[cn] if x > 0)),
                                         "max": round(float(max(GUARD[cn])), 5)},
                    "wing_tip_L_p2p_xyz": (wl.max(0) - wl.min(0)).round(4).tolist(),
                    "wing_tip_R_p2p_xyz": (wr.max(0) - wr.min(0)).round(4).tolist(),
                    "wing_tip_LR_z_corr": round(float(np.corrcoef(wl[:, 2], wr[:, 2])[0, 1]), 4),
                    "tail_tip_p2p_xyz": (tt_.max(0) - tt_.min(0)).round(4).tolist(),
                    "head_tip_p2p_xyz": (hd.max(0) - hd.min(0)).round(4).tolist()}
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["clip_seconds"] = round(time.time() - t_clip, 1)
rep["hover"] = {"hover_frac_H": HOVER_FRAC, "hover_height_units": round(HOVER, 4), "rest_height_units": HH,
                "rule": "rest pose = the drawn dive with the belly on z 0 (contract feet_origin); both clips key body.location "
                        "z = HOVER_FRAC x H + the bob; floor guard lifts the body if any vertex would dip below z 0"}
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("leaf_bird v2: root > body > head; body > wing_{L,R}.001 > .002; "
                       "body > tail.001 > tail.002")
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER (artist: movement intent + in-game height open). idle = 4 s hover-glide: body "
                               "lifted %.2f H, bob + slow pitch / roll wander, head nod, gentle wing flex about the root "
                               "line, tail flick. walk = 2 s glide with a wing beat (2 beats, the body lifts "
                               "on each downstroke). Floor guard keeps every vertex >= z 0; root still." % HOVER_FRAC)
low["conquest_locomotion"] = "PLACEHOLDER: flyer, in-place glide (movement intent open; the game glides the unit)"
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
              "structure": "armature identity + skinned 'leaf_bird' (one opaque body material); natural scale; "
                           "report-only cell fit %.5f" % k_fit}
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow": report["glow"]},
                "rule": "default skin only (brief)"}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("HOVER", json.dumps(rep["hover"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
