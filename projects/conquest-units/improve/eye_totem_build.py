"""Eye Totem -- the MODEL (from-scratch Conquest unit, firefly pattern: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/eye_totem_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-10-10 "NEW UNITS x2: Eye Totem, Ember Eye" (section EYE TOTEM) + the sheet
design/reference/eye_totem-character-sheet.png (intake: design/reference/eye_totem/intake.json).
Contract: improve/templates/new_unit/BUILD_CONTRACT.md. v1 DRAFT (draft speed: one pass, render, report).

UNITS: 'sheet units' -- 1 unit = 100 px of the sheet. FRONT + BACK are true orthos; the SIDE is drawn at a 3/4 slant:
  FRONT view (the unit's left on image right):   X = (x_px - 264.5) / 100 ; z = (786 - y_px) / 100
  BACK view (mirrored into world X):             X = -(x_px - 615) / 100  ; z = (786 - y_px) / 100
  SIDE view (front on image left):               -Y = (x_px - 974) / 100  ; z = (785 - y_px) / 100
Floor z = 0 at the DROPLET TIP (it floats: the sheet's shadow sits well below). X +ve = the unit's LEFT (L).
Every edge below is a 5-row median at the cited z of the view masks (|rgb - background 37,37,36| > 26, closed, filled):
the X edges are the FRONT and the mirrored BACK averaged (the two orthos agree to ~0.03 except the spire's top 0.6,
where the back leans ~0.08 further to the unit's right and is drawn 0.14 taller: tip y 108 vs the front's 122); the
depths are the SIDE view's half-widths (the brief: "measure its depth off the side view"; the side is a 3/4 slant, so
these are upper bounds -- see report deviations).

Pipeline:
  1. BODY = ONE SDF: the SPIRE (an elliptic lathe of the measured profile + seeded gentle edge wobble) smooth-union the
     DISC (an ellipsoid fitted to the front rows) smooth-union the NECK + DROPLET (an elliptic lathe of the measured
     profile), + a faint painterly surface noise (normal map). Marching tets = the HIGH (bake source); collapse
     decimation = the LOW shell; the eye's cut curves are locally densified and pulled back onto the high.
  2. REGIONS on the low shell, every boundary iso-cut: the EYE (the off-white RING, the dark PUPIL, the thin red loop
     drawn in the pupil = PUPIL_MARK; front-projected exactly as the front view draws them, each edge with a seeded
     hand-drawn wobble), the RIM (the faint red glow outline: the band of the surface whose shape normal is within
     RIM_NY of perpendicular to the front axis = the lens EQUATOR, which IS the outline from the front / back).
  3. no separate parts: every piece is the one closed shell (0 open edges).
  4. palette (palettes.py regions -> Col / Glow; rim + pupil_mark glow faintly), matte material (specular 0), Smart UV,
     bake normal + AO from the SDF high.
  5. RIG: root > body > spire.001 > spire.002, body > droplet. Clips PLACEHOLDER (movement not given by the artist):
     idle = slow hover bob (+0..IDLE_LIFT) + slight sway; walk = in-place hover-drift lean. Rest pose: droplet tip on
     the floor (z 0). glb export.
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
import eye_totem_parts as FP  # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, +X = the unit's left, floor z = 0)
UNIT = "eye_totem"
CHAR_ID = "eye_totem"                 # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.02                       # "surface finish": SDF sampling step (the bake high)
SDF_BAND = 0.18                       # BAND >= max smooth-union k (K_TOP 0.12) + 2 steps (contract hazard note)
BODY_TRIS = 9000                      # "body mesh detail": the shell's decimation target (before the eye densify + cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- SPIRE ("tall pointed upper section ... slightly uneven edges"): rows (z, right X edge, left X edge, half-depth).
#   X edges = FRONT (axis 264.5, floor 786) and mirrored BACK (axis 615, floor 786) averaged at each z; half-depth =
#   SIDE half-width (axis 974, floor 785). The tip: front y 122 (z 6.64) / back y 108 (z 6.78) -> 6.71. The two
#   notches the sheet draws (z ~5.0 and ~4.1: the edge steps out then back in) are in the rows. Rows z < 3.3 are the
#   z 3.3 row continued straight down inside the disc (the disc alone matches the flare: z 3.0 front w 0.795, disc 0.771).
SPIRE_ROWS = [
    (6.71, -0.030, -0.030, 0.000), (6.60, -0.152, 0.107, 0.105), (6.50, -0.193, 0.138, 0.140),
    (6.40, -0.223, 0.172, 0.170), (6.30, -0.258, 0.198, 0.185), (6.20, -0.292, 0.212, 0.205),
    (6.10, -0.333, 0.262, 0.235), (6.00, -0.363, 0.292, 0.255), (5.90, -0.397, 0.312, 0.280),
    (5.80, -0.463, 0.338, 0.300), (5.70, -0.483, 0.358, 0.325), (5.60, -0.492, 0.353, 0.340),
    (5.50, -0.502, 0.372, 0.345), (5.40, -0.528, 0.397, 0.355), (5.30, -0.552, 0.417, 0.380),
    (5.20, -0.567, 0.438, 0.385), (5.10, -0.593, 0.458, 0.405), (5.00, -0.597, 0.483, 0.410),
    (4.90, -0.578, 0.502, 0.395), (4.80, -0.568, 0.528, 0.415), (4.70, -0.568, 0.502, 0.410),
    (4.60, -0.573, 0.473, 0.400), (4.50, -0.577, 0.492, 0.405), (4.40, -0.583, 0.497, 0.410),
    (4.30, -0.593, 0.502, 0.405), (4.20, -0.607, 0.528, 0.420), (4.10, -0.627, 0.542, 0.440),
    (4.00, -0.607, 0.518, 0.440), (3.90, -0.567, 0.483, 0.410), (3.80, -0.512, 0.463, 0.390),
    (3.70, -0.512, 0.463, 0.400), (3.60, -0.512, 0.487, 0.405), (3.50, -0.528, 0.497, 0.410),
    (3.40, -0.528, 0.522, 0.420), (3.30, -0.538, 0.528, 0.425), (2.60, -0.538, 0.528, 0.425)]
WOB_SEED = 11                         # "uneven edge pattern" seed (any integer re-rolls it; deterministic)
WOB_AMP = 0.006                       # "edge unevenness": RMS radial wobble on top of the measured profile (u)
WOB_WL = (1.00, 2.20)                 # ... its wavelengths along the height (u): gentle, never ribbed
WOB_M = (1, 3)                        # ... its angular harmonics round the spire (low: never spiky)
K_TOP = 0.12                          # "spire / disc blend": smooth-union k where the spire meets the disc
# ---- DISC / ORB: an ellipsoid fitted to the averaged FRONT / BACK rows: widest half-width 1.123 at z 2.2 (y 566);
#   top half-height from z 2.6 (w 1.047) / 2.8 (w 0.943) -> 1.10; bottom from z 1.4 / 1.3 (w 0.672 / 0.508) -> 0.99;
#   centre X -0.030 (both orthos lean ~0.03 to the unit's right). Depth = the SIDE's widest half-width 0.65 (z 2.2).
DISC_C = (-0.030, 0.0, 2.20)          # "orb centre"
DISC_RX, DISC_RY = 1.123, 0.65        # "orb radius" (front) / "orb thickness" (side: the thick lens)
DISC_RZ = (1.10, 0.99)                # "orb height": top / bottom half-heights
# ---- NECK + DROPLET ("short, droplet-like extension, simple and rounded"): rows as the spire. z >= 0.1 measured
#   (front + mirrored back, side half-depths); z < 0.1 = the droplet ellipse fitted to the measured rows (widest half-width
#   0.273 at z 0.4, half-height 0.42, depth ratio 0.806 = side 0.22 / front 0.273). The neck (half-width 0.172, z 1.0-1.1)
#   continues straight up into the disc (disc bottom z 1.21).
LOWER_ROWS = [
    (0.000, -0.004, -0.004, 0.000), (0.010, -0.063, 0.055, 0.048), (0.030, -0.103, 0.095, 0.080),
    (0.060, -0.140, 0.132, 0.110), (0.10, -0.177, 0.182, 0.150), (0.20, -0.232, 0.232, 0.185),
    (0.30, -0.258, 0.253, 0.205), (0.40, -0.278, 0.268, 0.220), (0.50, -0.278, 0.263, 0.215),
    (0.60, -0.263, 0.253, 0.210), (0.70, -0.247, 0.232, 0.200), (0.80, -0.232, 0.212, 0.185),
    (0.90, -0.207, 0.182, 0.150), (1.00, -0.177, 0.172, 0.145), (1.10, -0.172, 0.172, 0.150),
    (1.60, -0.172, 0.172, 0.150)]
K_BOT = 0.08                          # "neck blend": smooth-union k where the neck meets the disc (front z 1.2 w 0.212)
# ---- PAINTERLY surface ("matte, painterly texture"): faint smooth noise, lives in the normal map
PAINT_SEED = 5
PAINT_AMP = 0.003                     # "painterly grain" (u)
PAINT_WL = (0.10, 0.30)               # its wavelengths (u)
PAINT_N = 24
# ---- EYE (FRONT view, white blob of the ring lum > 150 measured with PIL + scipy hole fill):
#   ring outer: filled bbox x 190-334 / y 499-636 -> centre X -0.030 z 2.185, half 0.725 x 0.690; edge roughness: radial
#   std 1.83 px (0.018 u). Pupil (the ring's hole): bbox x 219-300 / y 514-601 -> centre X -0.059 z 2.291, half 0.410 x
#   0.440 (it sits HIGH in the ring: the ring is 0.14 thick on top, 0.36 at the bottom, as drawn); edge std 1.29 px.
#   Pupil loop (R - G > 20 inside the pupil): bbox x 239-270 / y 529-574 -> centre X -0.100 z 2.345, outer half 0.160 x
#   0.230; drawn line ~2 px (n 280 px / perimeter) -> the curve half-axes 0.150 x 0.220.
EYE_C, EYE_AB = (-0.030, 2.185), (0.725, 0.690)        # "eye ring size"
PUPIL_C, PUPIL_AB = (-0.059, 2.291), (0.410, 0.440)    # "pupil size / placement"
LOOP_C, LOOP_AB = (-0.100, 2.345), (0.150, 0.220)      # "pupil loop" (the thin red line drawn inside the pupil)
LOOP_HALF_W = 0.0125                  # its half line width (u): 0.025 line vs the sheet's ~0.02 (one mesh edge wide)
EYE_ROUGH = (0.004, 0.0, 0.0)         # "hand-drawn roughness": radial wobble RMS of ring / pupil / loop edges (u): the
                                      #   ring OUTLINE only (gate 2026-10-10: pupil + loop clean; 0.008 still read as petals);
                                      #   measured std 1.83 / 1.29 px includes the ovals' ellipticity, 0.018 read as petals
EYE_SEED = 23                         # "roughness pattern" seed
EYE_K = (7, 19)                       # wobble harmonics round the ring / pupil (loop: 2-4: its kidney shape)
EYE_DENSE = ((0.04, 2), (0.04, 2), (0.04, 3))   # densify zone half-width (u) + subdivision rounds: ring / pupil / loop
EYE_AREA_DENSE = 2                    # "eye smoothness": subdivision rounds over the WHOLE eye face (front, ring rho^2 <
                                      #   1 + EYE_AREA_PAD) so flat shading of the gently curved disc face never reads as facets
EYE_AREA_PAD = 0.25
EYE_GRAIN_FADE = (1.10, 1.30)         # "clean eye face": the painterly grain is OFF inside the ring (front rho < 1.10) and
                                      #   fades back in by rho 1.30 (~0.15 u beyond the ring's outer edge)
# ---- RIM glow ("a subtle deep red outer glow / outline"): the band where the SHAPE normal (spire + disc + droplet SDF
#   gradient, the painterly noise excluded so the band edge stays smooth) has |n_y| < RIM_NY = the lens equator. From
#   the front / back that band is the silhouette; RIM_NY 0.40 gives a projected outline ~0.03-0.04 wide on the disc /
#   spire (the sheet's red rim: sil - core 3-6 px).
RIM_NY = 0.40                         # "rim glow width"
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
SPECULAR = 0.0                        # "sheen": Principled specular level (0 = the sheet's matte, painterly body)
# ---- clips (PLACEHOLDER: the artist has not given Eye Totem's movement; brief: idle = slow hover bob (+0..0.3) + a
#   slight sway, walk = hover-drift lean). 24 fps; integer harmonics per loop -> frame N+1 == frame 1. The rest pose
#   stays on the floor (contract feet_origin, droplet tip z 0); the clips lift.
IDLE_N = 96                           # "idle loop": 4 s
IDLE_LIFT = (0.0, 0.30)               # "hover bob": the body rises between these (u) and back, IDLE_BOB_CYC per loop
IDLE_BOB_CYC = 2                      #   (0.5 Hz: slow)
IDLE_PITCH, IDLE_ROLL = 1.2, 1.5      # "slight sway": body wander deg (1 cyc)
SPIRE_IDLE = (0.8, 1.6, 1, 0.6)       # spire sway (root deg, tip deg, cyc, lag): lags the body
DROP_IDLE = (3.0, 2.0)                # droplet pendulum deg (fore-aft, sideways): lags the bob
WALK_N = 48                           # "drift loop": 2 s
WALK_LIFT, WALK_BOB = 0.15, 0.03      # mean lift + bob (2 cyc)
WALK_PITCH, WALK_PITCH_OSC, WALK_ROLL = 8.0, 1.0, 1.5    # "lean into the drift": forward deg, wobble, roll
SPIRE_WALK_TRAIL = (-2.0, -3.0)       # spire trails back (deg per bone)
DROP_WALK_TRAIL = 6.0                 # droplet trails back (deg)

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
          "design/reference/eye_totem-character-sheet.png)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px; front + back true orthos, side a 3/4 slant; floor z = 0 at the "
                   "droplet tip (it floats)",
          "overrides": OVERRIDES}
scene = bpy.context.scene
DIG = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


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

# =========================================================================== 1. body SDF: spire + disc + neck/droplet
t_sdf = time.time()
DC = np.array(DISC_C, float)
SPIRE = FP.Profile(SPIRE_ROWS)
LOWER = FP.Profile(LOWER_ROWS)
WOB = FP.wobble_terms(WOB_SEED, 6, WOB_WL, WOB_M)
PAINT = FP.paint_terms(PAINT_SEED, PAINT_N, PAINT_WL)
SPIRE_TIP_Z = max(r[0] for r in SPIRE_ROWS)


def sd_disc(P):
    return FP.sd_ellipsoid2(P, DC, DISC_RX, DISC_RY, DISC_RZ[0], DISC_RZ[1])


def sd_spire(P):
    """the measured lathe minus the gentle wobble: faded out at the tip (w < 0.25 scales it down: no spikes) and below
    z 3.4 (the disc junction stays the sheet's clean flare)"""
    P = np.atleast_2d(P)
    q, z, th = SPIRE.qz(P)
    d = FP.sd_polygon2(np.stack([q, z], 1), SPIRE.poly)
    w = np.interp(z, SPIRE.z, SPIRE.w)
    fade = smoothstep(3.2, 3.6, z) * np.clip(w / 0.25, 0.0, 1.0)
    return d - WOB_AMP * fade * FP.wobble_field(z, th, WOB)


def sd_lower(P):
    return LOWER.sd(P)


def sd_shape(P):
    """the body without the painterly grain (the region fields + the rig ownership read this)"""
    return SD.smin(SD.smin(sd_disc(P), sd_spire(P), K_TOP), sd_lower(P), K_BOT)


def project_eye_face(V, iters=4, h=0.002):
    """the eye face (front, inside rho EYE_GRAIN_FADE[0] of the ring: no grain there) is the exact smooth disc: Newton-
    project those vertices onto sd_shape's zero set (removes the marching-tet grid ripple, ~0.002 u, that flat shading
    and the normal bake otherwise show as dimples on the white ring). -> (V', moved count, max move)"""
    V = np.array(V, float)
    rho = np.sqrt(((V[:, 0] - EYE_C[0]) / EYE_AB[0]) ** 2 + ((V[:, 2] - EYE_C[1]) / EYE_AB[1]) ** 2)
    m = (V[:, 1] < 0.0) & (rho < EYE_GRAIN_FADE[0])
    P = V[m].copy()
    for _ in range(iters):
        d = sd_shape(P)
        G = np.zeros_like(P)
        for k in range(3):
            e = np.zeros(3); e[k] = h
            G[:, k] = (sd_shape(P + e) - sd_shape(P - e)) / (2 * h)
        G /= np.maximum(np.linalg.norm(G, axis=1, keepdims=True), 1e-12)
        P -= d[:, None] * G
    mv = float(np.linalg.norm(P - V[m], axis=1).max()) if m.any() else 0.0
    V[m] = P
    return V, int(m.sum()), mv


_lo = np.array([min(min(r[1] for r in SPIRE_ROWS + LOWER_ROWS), DC[0] - DISC_RX), -DISC_RY, -0.02])
_hi = np.array([max(DC[0] + DISC_RX, max(r[2] for r in SPIRE_ROWS)), DISC_RY, SPIRE_TIP_Z])
_lo -= 0.06; _hi += 0.06
g = SD.Grid(_lo, _hi, SDF_STEP, SDF_BAND)
g.apply(sd_disc, DC - np.array([DISC_RX, DISC_RY, DISC_RZ[1]]), DC + np.array([DISC_RX, DISC_RY, DISC_RZ[0]]), 0.0)
_sp_lo = np.array([min(r[1] for r in SPIRE_ROWS), -max(r[3] for r in SPIRE_ROWS), min(r[0] for r in SPIRE_ROWS)]) - 0.05
_sp_hi = np.array([max(r[2] for r in SPIRE_ROWS), max(r[3] for r in SPIRE_ROWS), SPIRE_TIP_Z]) + 0.05
g.apply(sd_spire, _sp_lo, _sp_hi, K_TOP)
_lw_lo = np.array([min(r[1] for r in LOWER_ROWS), -max(r[3] for r in LOWER_ROWS), 0.0]) - 0.05
_lw_hi = np.array([max(r[2] for r in LOWER_ROWS), max(r[3] for r in LOWER_ROWS), max(r[0] for r in LOWER_ROWS)]) + 0.05
g.apply(sd_lower, _lw_lo, _lw_hi, K_BOT)
# painterly grain: added to the whole field (only the near-surface values matter; the band clip stays)
_zs = g.lo[2] + g.step * np.arange(g.n[2])
_ys = g.lo[1] + g.step * np.arange(g.n[1])
for ix in range(g.n[0]):
    xx = g.lo[0] + g.step * ix
    Yg, Zg = np.meshgrid(_ys, _zs, indexing="ij")
    Pp = np.stack([np.full(Yg.size, xx), Yg.ravel(), Zg.ravel()], 1)
    near = np.abs(g.F[ix].ravel()) < 3 * g.step
    if near.any():
        sl = g.F[ix].reshape(-1)
        Pn = Pp[near]
        _rho = np.sqrt(((Pn[:, 0] - EYE_C[0]) / EYE_AB[0]) ** 2 + ((Pn[:, 2] - EYE_C[1]) / EYE_AB[1]) ** 2)
        _gm = np.where(Pn[:, 1] < 0.0, smoothstep(EYE_GRAIN_FADE[0], EYE_GRAIN_FADE[1], _rho), 1.0)   # no grain on the eye
        sl[near] = sl[near] - PAINT_AMP * _gm * FP.paint_field(Pn, PAINT)
        g.F[ix] = sl.reshape(g.F[ix].shape)
t_poly = time.time()
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
HV, _n_hp, _mv_hp = project_eye_face(HV)
report["sdf"] = {"step": SDF_STEP, "band": SDF_BAND, "grid": g.n.tolist(), "ops": len(g.ops), "high_tris": len(HF),
                 "high_verts": len(HV), "crumbs": crumbs,
                 "eye_face_projected": {"high_verts": _n_hp, "max_move": round(_mv_hp, 5)}, "seconds_fields": round(t_poly - t_sdf, 1),
                 "seconds": round(time.time() - t_sdf, 1)}
del g
print("SDF", json.dumps(report["sdf"]))

# =========================================================================== 2. low shell: collapse decimation
t = time.time()
tmp = new_obj("dec_src", HV, HF)
dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
dm.ratio = min(1.0, BODY_TRIS / len(HF))
LV, LF = evaluated_arrays(tmp)
bpy.data.objects.remove(tmp, do_unlink=True)
LV, LF, specks = keep_islands(LV, LF, 0.01)
bvh_hi = BVHTree.FromPolygons(HV.tolist(), HF)
dev = np.array([bvh_hi.find_nearest(Vector(p))[3] for p in LV])
bvh_lo = BVHTree.FromPolygons(LV.tolist(), LF)
_hs = np.arange(0, len(HV), max(1, len(HV) // 20000))
dev2 = np.array([bvh_lo.find_nearest(Vector(HV[i]))[3] for i in _hs])
_bb_h = np.ptp(HV, 0); _bb_l = np.ptp(LV, 0)
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "bbox_high": _bb_h.round(4).tolist(), "bbox_low": _bb_l.round(4).tolist(),
                    "bbox_low_over_high": (_bb_l / _bb_h).round(5).tolist(),
                    "seconds": round(time.time() - t, 1)}
assert np.all(_bb_l / _bb_h > 0.99), report["retopo"]          # the hull-fold hazard (BUILD_CONTRACT): gate it
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))

# ---- eye fields (front-projected, gated to the front surface; seeded hand-drawn wobble)
RT_RING = FP.ring_terms(EYE_SEED, 7, EYE_K)
RT_PUP = FP.ring_terms(EYE_SEED + 1, 7, EYE_K)
RT_LOOP = FP.ring_terms(EYE_SEED + 2, 3, (2, 4))


def eye_fields(P):
    front = P[:, 1] < 0.0
    ring = FP.oval_front(P, EYE_C[0], EYE_C[1], EYE_AB[0], EYE_AB[1], EYE_ROUGH[0], RT_RING)
    pup = FP.oval_front(P, PUPIL_C[0], PUPIL_C[1], PUPIL_AB[0], PUPIL_AB[1], EYE_ROUGH[1], RT_PUP)
    loop = FP.oval_band(P, LOOP_C[0], LOOP_C[1], LOOP_AB[0], LOOP_AB[1], LOOP_HALF_W, EYE_ROUGH[2], RT_LOOP)
    return {"ring": np.where(front, ring, -1.0), "pupil": np.where(front, pup, -1.0), "loop": np.where(front, loop, -1.0)}


def eye_curve_dist(P):
    """approx. unsigned distance (u) of each point to the ring / pupil / loop curves (front only; inf behind)"""
    out = []
    for (c, ab) in ((EYE_C, EYE_AB), (PUPIL_C, PUPIL_AB), (LOOP_C, LOOP_AB)):
        d = -FP.oval_band(P, c[0], c[1], ab[0], ab[1], 0.0)
        out.append(np.where(P[:, 1] < 0.0, d, np.inf))
    return out


# ---- eye densify: the decimated shell's ~0.07 edges would cut the 0.025 pupil loop to dashes and the ring's
#      hand-drawn wobble to a polygon. Subdivide the faces within EYE_DENSE zone of each curve (ring / pupil 2 rounds,
#      loop 3), then pull every new vertex onto the HIGH (no new silhouette, only finer cut curves).
bm = bmesh.new()
for p in LV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in LF:
    bm.faces.new([bm.verts[i] for i in f])
_nv0 = len(bm.verts)
_dense_log = []
for rnd in range(max(r for _, r in EYE_DENSE)):
    zf = []
    for f in bm.faces:
        Pf = np.array([v.co[:] for v in f.verts])
        dd = eye_curve_dist(Pf)
        ef = eye_fields(Pf)
        hit = False
        for j, ((zw, rr), key) in enumerate(zip(EYE_DENSE, ("ring", "pupil", "loop"))):
            if rnd >= rr:
                continue
            if np.any(dd[j] < zw) or (np.any(ef[key] > 0) and np.any(ef[key] <= 0)):
                hit = True
                break
        if not hit and rnd < EYE_AREA_DENSE and np.any(ef["ring"] > -EYE_AREA_PAD):
            hit = True                                        # the whole eye face (front only: ef is gated)
        if hit:
            zf.append(f)
    ze = list({e for f in zf for e in f.edges})
    bmesh.ops.subdivide_edges(bm, edges=ze, cuts=1, use_grid_fill=True)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    _dense_log.append({"round": rnd + 1, "faces": len(zf), "tris_after": len(bm.faces)})
bm.verts.ensure_lookup_table()
for v in bm.verts[_nv0:]:
    v.co = bvh_hi.find_nearest(v.co)[0]
bm.verts.index_update()
LV = np.array([v.co[:] for v in bm.verts])
LV, _n_lp, _mv_lp = project_eye_face(LV)
LF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["retopo"]["eye_densify"] = {"zones": EYE_DENSE, "area_rounds": EYE_AREA_DENSE, "rounds": _dense_log, "eye_face_projected": {"low_verts": _n_lp, "max_move": round(_mv_lp, 5)}, "new_verts": len(LV) - _nv0,
                                   "tris_after": tri_count_F(LF)}
print("RETOPO", json.dumps(report["retopo"]))

# =========================================================================== 3. region fields + iso cuts on the shell


def shape_normals(P, h=0.004):
    """unit gradient of the SHAPE SDF (no painterly grain) by central differences"""
    G = np.zeros_like(P)
    for k in range(3):
        e = np.zeros(3); e[k] = h
        G[:, k] = (sd_shape(P + e) - sd_shape(P - e)) / (2 * h)
    return G / np.maximum(np.linalg.norm(G, axis=1, keepdims=True), 1e-12)


NRM = shape_normals(LV)
FIELDS = eye_fields(LV)
FIELDS["rim"] = RIM_NY - np.abs(NRM[:, 1])
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
in_pupil = lambda a, b: a[LAY["pupil"]] > -TOL and b[LAY["pupil"]] > -TOL
off_eye = lambda a, b: a[LAY["ring"]] < TOL and b[LAY["ring"]] < TOL
CUTS = [("ring", 0.0, None), ("pupil", 0.0, None), ("loop", 0.0, in_pupil), ("rim", 0.0, off_eye)]
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
shell_reg = np.full(len(CF), "body", dtype=object)
shell_reg[FVAL["rim"] > 0.0] = "rim"
shell_reg[FVAL["ring"] > 0.0] = "ring"
shell_reg[(FVAL["ring"] > 0.0) & (FVAL["pupil"] > 0.0)] = "pupil"
shell_reg[(FVAL["ring"] > 0.0) & (FVAL["pupil"] > 0.0) & (FVAL["loop"] > 0.0)] = "pupil_mark"
REG = ["body", "rim", "ring", "pupil", "pupil_mark"]
# every region is non-empty and owns ONLY faces its own field claims (no region borrows another's field; a field that
# is never positive can never paint: the soot_puff 'dust' lesson)
OWN = {"rim": "rim", "ring": "ring", "pupil": "pupil", "pupil_mark": "loop"}
for r_ in REG:
    assert np.any(shell_reg == r_), "region %s is empty" % r_
for r_, f_ in OWN.items():
    assert np.all(FVAL[f_][shell_reg == r_] > 0.0), "region %s has faces outside its field %s" % (r_, f_)
    assert np.any(FIELDS[f_] > 0.0), "field %s never positive" % f_
assert not np.any((shell_reg == "rim") & (FVAL["ring"] > 0.0)), "rim on the eye"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}
print("CUTS", json.dumps(report["iso_cuts"]))

# =========================================================================== 4. assemble + centre (one closed shell)
ISL = [("shell", CV, CF, list(shell_reg), None)]
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
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
assert abs(float(VALL[:, 2].min())) < 1e-9, "rest pose: the droplet tip is the floor (min z 0)"


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.97 + 0.06 * j


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True                      # opaque closed shell: single-sided (glTF doubleSided false)
    bsdf.inputs["Specular IOR Level"].default_value = SPECULAR   # "matte" (sheet COLOR / MATERIAL panel)
    return mat


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


GLOW_REGIONS = {"rim", "pupil_mark"}   # the sheet's red glow outline (+ the same red line drawn in the pupil)
GLOW_CAP = 2.0                         # emissive palette ceiling (phone cap, import notes s7)


def glow_check(pal):
    lit = sorted(n for n, v in pal["regions"].items() if "emission" in v)
    peak = max([max(c / 255.0 for c in pal["regions"][n]["emission"]) * pal["regions"][n].get("emission_scale", 1.0)
                for n in lit] or [0.0])
    return {"skin": pal["skin"], "glowing_regions": lit, "peak_srgb_x_scale": round(peak, 4),
            "peak_x_strength": round(peak * pal["material"].get("emission_strength", 1.0), 4), "cap": GLOW_CAP,
            "ok": set(lit) == GLOW_REGIONS and peak * pal["material"].get("emission_strength", 1.0) <= GLOW_CAP}


MAT_BODY = make_mat(UNIT + "_body")
pal_default = PAL.load(UNIT, "default")
assert set(pal_default["regions"]) == set(REG), (sorted(pal_default["regions"]), REG)   # palette <-> regions 1:1
report["glow"] = glow_check(pal_default)
report["glow"]["rule"] = ("rim = |n_y| < %.2f of the shape normal (the lens equator: the outline from the front / back); "
                          "faint tier under the %.1f cap" % (RIM_NY, GLOW_CAP))
assert report["glow"]["ok"], report["glow"]

low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY)
shade = jitter(VALL, FALL)
shade[np.isin(rid, [REG.index("ring"), REG.index("pupil"), REG.index("pupil_mark")])] = 1.0   # the eye stays clean
PAL.store_regions(me, REG, rid, shade)
report["regions_faces"] = repaint([low], pal_default)
me.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the disc centre -> the eye's front point (the disc's front surface at the eye centre)
_ys_front = np.linspace(-DISC_RY - 0.3, 0.0, 4001)
_pp = np.stack([np.full_like(_ys_front, EYE_C[0]), _ys_front, np.full_like(_ys_front, EYE_C[1])], 1)
_yf = float(_ys_front[np.argmax(sd_shape(_pp) <= 0.0)])
anchor = DC - SHIFT
landmark = np.array([DC[0], _yf, DC[2]]) - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "disc centre -> the eye's front point (the eye faces -Y)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
report["eye"] = {"ring": {"centre_xz": list(EYE_C), "half_wh": list(EYE_AB), "rough_rms": EYE_ROUGH[0]},
                 "pupil": {"centre_xz": list(PUPIL_C), "half_wh": list(PUPIL_AB), "rough_rms": EYE_ROUGH[1]},
                 "loop": {"centre_xz": list(LOOP_C), "half_wh": list(LOOP_AB), "half_line_w": LOOP_HALF_W},
                 "rule": "front-projected ovals iso-cut into the disc's front surface (y < 0); the back is plain body"}

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
_reg_tris = {n: int((rid == j).sum()) for j, n in enumerate(REG)}
# per-part tris by SDF ownership (which of the three pieces is nearest at each face centre)
_FC = np.array([np.mean(VALL[f], 0) for f in FALL]) + SHIFT
_own = np.argmin(np.stack([sd_spire(_FC), sd_disc(_FC), sd_lower(_FC)], 1), 1)
report["tris"] = {"total": tris_main, "shell": tri_count_F(CF), "shell_before_eye_densify": report["retopo"]["decimated_tris"],
                  "per_region": _reg_tris,
                  "per_part": {"spire": int((_own == 0).sum()), "disc (+ eye)": int((_own == 1).sum()),
                               "neck + droplet": int((_own == 2).sum())},
                  "per_part_rule": "one SDF shell; each face counted to the piece whose SDF is lowest at its centre"}
report["tier_rationale"] = ("REGULAR role: window [%d, %d]. One shell carries everything: ~%d decimated tris keep the "
                            "spire's measured notches + wobble in the LOW silhouette; the eye's densified cut curves "
                            "(ring, pupil, the thin pupil loop) and the rim cuts add the rest." % (TRI_BUDGET[0], TRI_BUDGET[1], BODY_TRIS))
open_e = {name: FP.open_edges(F) for name, V, F, R, p in ISL}
report["open_edges"] = {"closed_parts": sorted(k for k, v in open_e.items() if v == 0),
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "one closed SDF shell (spire + disc + droplet); the eye + rim are iso-cut into it (no decals)"}
lo_a, hi_a = VALL.min(0), VALL.max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "width_px": round(100 * float(hi_a[0] - lo_a[0]), 1),
                                             "sheet": {"front": "H 6.65 W 2.24", "back": "H 6.77 W 2.24",
                                                       "side (3/4)": "H 6.68 D 1.29"}},
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
low["conquest_source"] = "from scratch: design/reference/eye_totem-character-sheet.png (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color (rim + pupil_mark glow faintly)"
# focus boxes for the close-ups (world, after the centre shift) = the sheet's detail panels
low["conquest_focus"] = json.dumps({
    "eye_detail": [(np.array([-0.85, -0.9, 1.40]) - SHIFT).tolist(), (np.array([0.80, -0.3, 2.95]) - SHIFT).tolist()],
    "silhouette_detail": [(np.array([-0.65, -0.5, 4.55]) - SHIFT).tolist(), (np.array([0.55, 0.5, 6.75]) - SHIFT).tolist()],
    "lower_extension": [(np.array([-0.55, -0.45, -0.02]) - SHIFT).tolist(), (np.array([0.55, 0.3, 1.70]) - SHIFT).tolist()],
    "rim_material": [(np.array([-1.20, -0.7, 1.70]) - SHIFT).tolist(), (np.array([-0.35, 0.3, 2.95]) - SHIFT).tolist()],
    "views": {"eye_detail": [0.0, 3.0], "silhouette_detail": [0.0, 4.0], "lower_extension": [0.0, 2.0],
              "rim_material": [-35.0, 5.0]},
    "axis_xy": [float(0.0 - SHIFT[0]), float(0.0 - SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow", "centre_shift")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake normal + AO (high = the SDF shell)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
HIGH = new_obj(UNIT + "_high", HV_s, HF)
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
    "high_tris": len(HF), "high_rule": "the SDF shell (marching tets: spire + disc + droplet + painterly grain)",
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
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k != "pixel_sha"}))
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
# wire the textures into the body material
nt = MAT_BODY.node_tree
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
                     "provenance": "pixel-sampled from the sheet's five palette chips (intake swatches) + drawn-surface "
                                   "samples (see the palette notes); the charcoal-gray chip is unused (not drawn on the body)"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def P_(p):
    return np.asarray(p, float) - SHIFT


SPIRE_BASE_Z = 3.15                   # where the spire leaves the disc (front: the flare ends at z ~3.2)
SPIRE_MID_Z = 4.90
NECK_TOP_Z = 1.25                     # where the neck leaves the disc (disc bottom z 1.21)
_sc = lambda z: float(np.interp(z, SPIRE.z, SPIRE.c))
BONES = [("body", P_(DC), P_(DC + np.array([0.0, 0.0, 0.8])), "root"),
         ("spire.001", P_((_sc(SPIRE_BASE_Z), 0.0, SPIRE_BASE_Z)), P_((_sc(SPIRE_MID_Z), 0.0, SPIRE_MID_Z)), "body"),
         ("spire.002", P_((_sc(SPIRE_MID_Z), 0.0, SPIRE_MID_Z)), P_((_sc(SPIRE_TIP_Z - 0.01), 0.0, SPIRE_TIP_Z)), "spire.001"),
         ("droplet", P_((0.0, 0.0, NECK_TOP_Z)), P_((0.0, 0.0, 0.0)), "body")]
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
    e.use_connect = nm == "spire.002"
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights (<= 3 influences): spire ownership = smoothstep over (disc distance - spire distance) at the rest shell
#      (the fillet blends), split along the height by rigkit.vine_weights from the spire base; droplet ownership the
#      same against the neck + droplet SDF
OWN_W = 0.10                          # half-width of the ownership blend across a fillet
SPIRE_PARENT_S0 = -0.20               # the body -> spire.001 blend starts this far below the spire base (along z)
Wt = np.zeros((len(VALL), len(DEFORM)))
Pw = VALL + SHIFT
d_disc_ = sd_disc(Pw)
own_s = smoothstep(-OWN_W, OWN_W, d_disc_ - sd_spire(Pw))
own_d = smoothstep(-OWN_W, OWN_W, d_disc_ - sd_lower(Pw))
Ls = SPIRE_TIP_Z - SPIRE_BASE_Z
Wv = K.vine_weights(np.clip(Pw[:, 2] - SPIRE_BASE_Z, SPIRE_PARENT_S0 - 1.0, Ls), Ls, 2, parent_s0=SPIRE_PARENT_S0)
Wt[:, J["body"]] = 1.0 - own_s * (1.0 - Wv[:, 0]) - own_d
Wt[:, J["spire.001"]] = own_s * Wv[:, 1]
Wt[:, J["spire.002"]] = own_s * Wv[:, 2]
Wt[:, J["droplet"]] = own_d
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
                  "rule": "disc + eye 1.0 body; the spire owned by smoothstep(+-%.2f) of (disc distance - spire distance), "
                          "split along z by rigkit.vine_weights from z %.2f (body -> spire.001 blend from %.2f below); "
                          "neck + droplet the same against the lower SDF -> droplet" % (OWN_W, SPIRE_BASE_Z, -SPIRE_PARENT_S0)}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
am = low.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- clips: closed-form periodic curves keyed every frame (location, quaternion, scale on every deform bone in BOTH
#      clips); integer cycles per loop -> frame N+1 == frame 1 exactly
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}
AXV = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
TAU = 2.0 * math.pi
HH = report["measure"]["height"]


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


def local_quat(bone, rots):
    """world-axis rotations (in order, about the bone head, relative to the parent's posed frame) -> basis quaternion"""
    Rw = Matrix.Identity(3)
    for axis, deg in rots:
        Rw = Matrix.Rotation(math.radians(deg), 3, Vector(AXV[axis])) @ Rw
    R0 = Matrix(REST[bone].tolist())
    q = (R0.transposed() @ Rw @ R0).to_quaternion()
    q.normalize()
    if q.w < 0:
        q.negate()
    return q


CLIPS = {"idle": IDLE_N, "walk": WALK_N}


def pose_at(clip, f):
    N = CLIPS[clip]
    u = f / N
    P = {}
    if clip == "idle":
        ph_b = TAU * IDLE_BOB_CYC * u
        w_ = 0.5 * (1.0 - math.cos(ph_b))                      # 0 at the bob bottom .. 1 at the top
        z = IDLE_LIFT[0] + (IDLE_LIFT[1] - IDLE_LIFT[0]) * w_
        pitch = IDLE_PITCH * math.sin(TAU * u + 0.5) * w_      # the wander fades out at the bottom (rest = the floor)
        roll = IDLE_ROLL * math.sin(TAU * u + 1.9) * w_
        P["body"] = ((0.0, 0.0, z), [("X", pitch), ("Y", roll)], None)
        for k in range(2):
            rx = K.vine_wave(u, k, 2, SPIRE_IDLE[0], SPIRE_IDLE[1], SPIRE_IDLE[3], SPIRE_IDLE[2], -0.6)
            ry = K.vine_wave(u, k, 2, SPIRE_IDLE[0], SPIRE_IDLE[1], SPIRE_IDLE[3], SPIRE_IDLE[2], 1.3)
            P["spire.%03d" % (k + 1)] = (None, [("X", rx), ("Y", ry)], None)
        # droplet pendulum: lags the bob (fore-aft at the bob rate) + a slow sideways swing
        P["droplet"] = (None, [("X", DROP_IDLE[0] * math.sin(ph_b - math.pi / 2)),
                               ("Y", DROP_IDLE[1] * math.sin(TAU * u + 0.4))], None)
        info = {"lift": z, "pitch": pitch, "roll": roll}
    else:
        z = WALK_LIFT + WALK_BOB * math.sin(TAU * 2 * u)
        pitch = WALK_PITCH + WALK_PITCH_OSC * math.sin(TAU * 2 * u - 1.0)
        roll = WALK_ROLL * math.sin(TAU * u + 0.6)
        P["body"] = ((0.0, 0.0, z), [("X", pitch), ("Y", roll)], None)
        for k in range(2):
            P["spire.%03d" % (k + 1)] = (None, [("X", SPIRE_WALK_TRAIL[k] + 0.6 * math.sin(TAU * 2 * u - 1.6 - 0.5 * k)),
                                                ("Y", 0.8 * math.sin(TAU * u + 1.0 - 0.5 * k))], None)
        P["droplet"] = (None, [("X", DROP_WALK_TRAIL + 1.5 * math.sin(TAU * 2 * u - 2.2)),
                               ("Y", 1.5 * math.sin(TAU * u + 1.6))], None)
        info = {"lift": z, "pitch": pitch, "roll": roll}
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
        by = pbs["body"].matrix.col[1].xyz
        rows.append({"minz": float(C[:, 2].min()), "body_z": float(pbs["body"].head.z),
                     "pitch": math.degrees(math.atan2(-by.y, by.z)), "roll": math.degrees(math.atan2(by.x, by.z)),
                     "root": float((rig.matrix_world @ pbs["root"].head).length),
                     "spire_tip": list(pbs["spire.002"].tail), "drop_tip": list(pbs["droplet"].tail)})
    R_ = rows[:-1]
    g_ = lambda k: np.array([r[k] for r in R_])
    bz = g_("body_z") - arm_data.bones["body"].head_local.z
    st = np.array([r["spire_tip"] for r in R_]); dt = np.array([r["drop_tip"] for r in R_])
    clip_rep[cn] = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 3), "cyclic": True,
                    "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                    "root_offset_max": round(max(r["root"] for r in rows), 8),
                    "clearance_min_z": round(float(g_("minz").min()), 5),
                    "body_lift": [round(float(bz.min()), 4), round(float(bz.max()), 4)],
                    "pitch_deg": [round(float(g_("pitch").min()), 3), round(float(g_("pitch").max()), 3)],
                    "roll_deg": [round(float(g_("roll").min()), 3), round(float(g_("roll").max()), 3)],
                    "spire_tip_p2p_xyz": (st.max(0) - st.min(0)).round(4).tolist(),
                    "droplet_tip_p2p_xyz": (dt.max(0) - dt.min(0)).round(4).tolist()}
    # nothing dips below the floor in any clip frame (it floats: the rest pose touches z 0, the clips only lift)
    assert clip_rep[cn]["clearance_min_z"] >= -1e-4, ("below the floor", cn, clip_rep[cn]["clearance_min_z"])
    print("CLIP", cn, json.dumps(clip_rep[cn]))
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["hover"] = {"rest_height_units": HH, "idle_lift_units": list(IDLE_LIFT), "walk_lift_units": WALK_LIFT,
                "rule": "rest pose: droplet tip on the floor (contract feet_origin, z 0); idle keys body.location z = "
                        "IDLE_LIFT[0] .. [1] (slow bob), walk WALK_LIFT + bob; the contract root never moves. The sheet's "
                        "floor shadow sits well under the tip: the in-game hover height is the artist's open question"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "eye_totem v1: root > body > spire.001 > spire.002, body > droplet"
low["conquest_clips"] = ["idle", "walk"]
low["conquest_clip_status"] = ("PLACEHOLDER clips (the artist has not given Eye Totem's movement or hover height; open "
                               "question): idle = 4 s slow hover bob (body +%.2f..%.2f u, 0.5 Hz) + a slight sway (spire "
                               "lag, droplet pendulum); walk = 2 s in-place hover-drift (%.0f deg forward lean, spire + "
                               "droplet trailing)" % (IDLE_LIFT[0], IDLE_LIFT[1], WALK_PITCH))
low["conquest_locomotion"] = "hover (floats): rest pose droplet tip z 0 per contract; the clips lift the body"
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

# =========================================================================== 7. glb
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
              "structure": "armature identity + skinned 'eye_totem' (one opaque body primitive); natural scale; "
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
