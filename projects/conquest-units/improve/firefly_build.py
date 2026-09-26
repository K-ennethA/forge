"""Firefly jet creature -- the MODEL (first fully from-scratch Conquest unit: no source sculpt), one headless run.

    blender --background --factory-startup --python improve/firefly_build.py -- \
        [--preview <out.blend>]          (geometry + regions + palette + materials only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the twin determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Spec: design/review-log.md 2026-09-26 "NEW UNIT: Firefly jet creature" + the three-view sheet
design/reference/firefly-character-sheet.webp. Artist (verbatim): "once thats done begin work on this firefly / the colors
instead of that exact yellow should be more of a firefly glow type color / and the smokey wings should be coming out of the
holes on its side". Two deviations from the sheet, both binding: (1) the amber accents become FIREFLY GLOW (palettes/firefly
default; 'ember' = the sheet's amber, kept as the swap proof / A-B); (2) the smoke wings leave the SIDE-THRUSTER HOLES.

SCOPE: the model only. Movement identity is unanswered (flame always-on vs perch; wings flap vs drift; darts vs cruise;
machine vs bug-fused), so the only clip is a minimal hover-bob PLACEHOLDER named 'idle' and there is NO walk clip
(clip_names fails on the missing walk -- expected until the movement wave).

UNITS: 'sheet units' -- 1 unit = 100 px of the reference sheet's front view (the sheet is orthographic, so every constant
below is a direct sheet measurement / 100). Floor z = 0 at the flame tip (sheet y 845 px); z = (845 - y_px) / 100.
Natural proportions; cell fit is report-only (scale policy 2026-09-25).

Pipeline:
  1. BODY SHELL = one SDF (magmoo_sdf's banded grid + smooth unions): anisotropic torso lathe with chevron band grooves,
     back jet-pack tank, side rails, neck, bug head (mask), firefighter helmet (brim plane cut, tail brim lower at the
     back), goggle cups, respirator snout + ribs + filter canister. Marching tetrahedra at SDF_STEP = the HIGH (bake
     source); collapse decimation to BODY_TRIS = the LOW shell (deterministic).
  2. REGIONS on the low shell: part ownership (nearest SDF part) + the chevron band fields, every boundary cut into the
     mesh along its iso-line (vampito's cutter) so colour edges are clean lines.
  3. PARTS (firefly_parts.py, closed solids): goggle rims (torus) + domed HONEYCOMB lenses (geometry: inner hex cell +
     border ring per cell), helmet straps, four side-thruster cans (rim / glowing inner wall / dark hole floor), the exhaust
     nozzle (glowing abdomen-tip ring + throat), two serrated feather antennae (vane + rachis tube).
  4. SMOKE WINGS: per side-thruster one main sheet + one layered sheet, each with a neck that leaves the hole along the port
     axis, then billows outward/back into a ragged sheet (smoke material: alphaMode BLEND, double-sided, per-region alpha).
  5. FLAME: its own object 'firefly_flame' (the game can hide the node): a lobed teardrop outer + an inner hot core
     (flame material: alphaMode BLEND, single-sided), on its own bone.
  6. palette (palettes.py regions -> Col / Glow + Col alpha), Smart UV, bake normal + AO from the SDF high + parts.
  7. RIG: root (contract) > body > head > antenna.{L,R}.{0,1}; body > abdomen > flame; body > wing.{L,R}.{up,low}.{0,1,2}
     (one chain per port; both sheets of a port ride it). PLACEHOLDER idle: hover bob + slight wing drift. glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, tempfile
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K           # noqa: E402  (read-only use)
import palettes as PAL       # noqa: E402  (read-only use)
import magmoo_sdf as SD      # noqa: E402  (read-only use: the SDF kit is the prior art)
import firefly_parts as FP   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; sheet units = sheet px / 100; front -Y, floor z = 0 at the flame tip)
UNIT = "firefly"
CHAR_ID = "firefly"                   # roster id: not yet in Conquest
TRI_BUDGET = [8000, 22000]            # declared tier: REGULAR unit (see report 'tier_rationale')
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
SDF_STEP = 0.015                      # "surface finish": SDF sampling step of the body shell (the bake high)
SDF_BAND = 0.08
BODY_TRIS = 7600                      # "body mesh detail": the shell's decimation target (before the colour cuts)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
# ---- torso (jet-pack / bug abdomen): half-WIDTH profile (z, r) from the sheet front view; depth = width x TORSO_DEPTH
TORSO_PROFILE = [(2.12, 0.0), (2.17, 0.20), (2.26, 0.34), (2.40, 0.43), (2.60, 0.50), (2.90, 0.56), (3.30, 0.605),
                 (3.60, 0.615), (3.95, 0.60), (4.30, 0.565), (4.60, 0.52), (4.85, 0.44), (5.00, 0.34), (5.08, 0.19),
                 (5.12, 0.0)]
TORSO_DEPTH = 0.90                    # "torso depth": side-view depth / front-view width (sheet side view: the belly
                                      #   bulges to ~0.9 of the front width; v0 0.78 read thin in the sheet overlay)
# "bands": per band (top edge, bottom edge), each (height at the SIDES, chevron drop at the front centre). The sheet has
# two chevrons (V pointing down at the front) and one flat low ring.
BANDS = {"A": ((4.95, 0.30), (4.45, 0.25)), "B": ((4.05, 0.40), (3.60, 0.45)), "C": ((2.75, 0.0), (2.50, 0.0))}
PINCH, PINCH_W = 0.035, 0.028         # "segment grooves": waist pinch at every band edge (fraction of radius, width)
PACK_KNOTS = [(4.88, 0.19), (4.35, 0.25), (3.65, 0.24), (3.05, 0.17), (2.78, 0.07)]   # "jet-pack tank": (z, radius)
                                      #   knots down the back, pointed at the bottom (sheet back view)
PACK_PROUD, PACK_FLAT = 0.085, 0.62   # the tank stands this far off the back; its front-back flattening
PACK_BAND = (3.80, 4.35)              # the glow band across the tank (sheet back view)
# ---- head: bug head + firefighter helmet + goggles + respirator
HEAD_C = (0.0, 0.0, 5.62)             # head centre (sheet: goggles y 300, helmet top y 225)
HEAD_R = (0.40, 0.44, 0.50)           # "head size" (half width / depth / height)
HELMET_SCALE, HELMET_LIFT = 1.075, 0.03   # "helmet": the dome is the head grown by this, lifted by this
BRIM_Z0, BRIM_SLOPE = 5.56, -0.34     # "brim line": z = BRIM_Z0 + BRIM_SLOPE * y (higher at the front, a lower tail brim)
BRIM_T = 0.02                         # "brim lip" half-thickness
BRIM_REACH = (1.13, 1.20, 0.07)       # "brim reach": lip ellipsoid x/y growth over the head + its shift back (the fire-
                                      #   helmet tail: hidden at the front over the goggles, sticking out most at the back)
GOGGLE_AZ, GOGGLE_EL = 36.0, -13.0    # "goggle placement": deg from the front toward each side / below the head centre
GOGGLE_R = 0.19                       # "goggle size": lens radius (sheet: ~38 px lens diameter)
GOGGLE_PROUD = 0.07                   # goggles stand this far off the head
GOGGLE_CUP_R = 0.225                  # rubber housing radius
RIM_TUBE = 0.034                      # goggle rim ring thickness
LENS_DOME, LENS_CELL, LENS_INSET = 0.05, 0.027, 0.74   # "honeycomb": dome height, hex cell size, cell/border ratio
SNOUT_EL = -40.0                      # "respirator": where the snout leaves the face (deg below the head centre)
SNOUT_AXIS = (0.0, -0.72, -0.69)      # snout direction (forward-down)
SNOUT_LEN, SNOUT_R = 0.26, (0.125, 0.092)
SNOUT_RIBS = (0.07, 0.15)             # rib rings along the snout (explicit tori)
SNOUT_RIB_R = 0.02
CANISTER = (0.22, 0.31, 0.108)        # filter canister (start, end along the snout axis, radius)
STRAP_W, STRAP_T, STRAP_SINK = 0.07, 0.018, 0.01   # "straps" width / thickness
STRAP_X = 0.10                        # the two over-the-top straps sit this far off the midline
# ---- antennae (feathers)
ANT_BASE_DIR = (0.32, 0.10, 1.0)      # where each antenna leaves the helmet (direction from the helmet centre)
ANT_TIP_DIR = (0.26, 0.05, 1.0)       # antenna lean
ANT_LEN = 1.42                        # "antenna length" (sheet: tips at y 85 -> z 7.6)
ANT_BEND = 0.07                       # outward bow
ANT_VANE_YAW = 38.0                   # the vane faces this far from the front toward its side (reads front AND side)
ANT_VANE_S0 = 0.20                    # the feather vane starts this far up the antenna
ANT_HALF_W = 0.16                     # "feather width" (half; sheet ~30 px)
ANT_SERR = (4, 0.50)                  # "feather notches": count per edge, depth
ANT_THICK, ANT_STALK_R = 0.012, 0.021
# ---- side thrusters: name -> (z, azimuth deg from the front, r_out, r_in, vertical stretch, proud, hole depth)
PORTS = {"up": (4.50, 100.0, 0.13, 0.088, 1.6, 0.10, 0.08), "low": (3.40, 100.0, 0.205, 0.14, 1.0, 0.17, 0.12)}
RAIL_R = 0.045                        # the side rail joining each side's two thrusters (sheet side view)
# ---- exhaust nozzle: (r, z) profile top pole -> outside -> lip -> throat -> throat floor pole, region per segment
NOZZLE = [(0.0, 2.36), (0.35, 2.36), (0.35, 2.29), (0.368, 2.27), (0.368, 2.235), (0.338, 2.215), (0.330, 2.05),
          (0.352, 1.92), (0.362, 1.86), (0.300, 1.86), (0.262, 1.97), (0.200, 2.10), (0.0, 2.13)]
NOZZLE_REG = ["nozzle", "nozzle", "abdomen_tip", "abdomen_tip", "abdomen_tip", "nozzle", "nozzle", "nozzle",
              "nozzle", "nozzle_throat", "nozzle_throat", "nozzle_throat"]
# ---- smoke wings: per port one main CURTAIN (a second, layered curtain rides beside it). 'top' = the curtain's top edge
# in its swept plane (a outward-back, b up) from the point where the smoke leaves the hole; 'drop' = how far it hangs.
WINGS = {"up": {"sweep": 24.0, "drop": 3.2, "seed": 1.0,
                "top": [(0.22, 0.30), (0.55, 0.46), (0.92, 0.46), (1.16, 0.22), (1.24, -0.40)]},
         "low": {"sweep": 44.0, "drop": 2.05, "seed": 4.0,
                 "top": [(0.22, 0.24), (0.50, 0.34), (0.80, 0.24), (1.00, -0.10)]}}
WING_LAYER = {"scale": 0.84, "sweep_add": 18.0, "drop": 0.86, "offset": 0.07, "seed_add": 7.0}
WING_NS, WING_NT = 15, 7              # "smoke sheet detail": stations along / rows down each curtain
WING_EXIT = 0.12                      # "smoke exit": the smoke pours straight out of the hole this far before it spreads
WING_RAG_END, WING_RAG_EDGE = 0.28, 0.26   # "raggedness" of the hanging bottom edge / the outer end
WING_BILLOW, WING_FOLD, WING_TRAIL = 0.12, 0.07, 0.30   # "billow" (sail) / "folds" / how far the hanging smoke trails back
WING_TUCK = 0.42                      # "leaf taper": the hanging smoke tucks back in toward the body at the bottom
WING_ROOT_W = 0.80                    # the curtain's root height as a fraction of the hole (fits inside it)
# ---- flame (its own object)
FLAME_TOP_Z = 2.06                    # the flame starts inside the nozzle throat
FLAME_R = 0.39                        # "flame width" (sheet: ~78 px at its widest)
FLAME_WIDE_S = 0.28                   # widest at this fraction of the length (a rounded bulb under the nozzle)
FLAME_TAPER = 1.25                    # tip taper (> 1 = concave, a long clean point)
FLAME_LOBES, FLAME_LOBE_A, FLAME_TWIST = 5, 0.16, 1.4   # "flame lobes" round the flame: count, depth, spiral
FLAME_LICKS, FLAME_LICK_A = 4, 0.30   # "flame licks": stacked tongues down each side (count, depth)
FLAME_LICK_S = 0.78                   # the lower outer flame (below this fraction) is the cooler, see-through lick region
CORE_R, CORE_TIP_Z = 0.24, 0.62       # "flame core": width / where it ends (z)
FLAME_SEG, FLAME_ST = 30, 26
# ---- bake
BAKE_RES = (1024, 512)                # normal, AO texture sizes
# ---- rig + placeholder idle
NECK_Z = (5.02, 5.22)                 # head/body weight blend band
WAIST_Z = (2.85, 3.15)                # abdomen/body weight blend band
WING_BONES, ANT_BONES = 3, 2
IDLE_N = 48                           # PLACEHOLDER idle: 2 s loop
HOVER = 0.35                          # hover lift of the placeholder (units; the flame tip clears the floor by this)
BOB = 0.06                            # hover bob amplitude (one bob per loop)
WING_DRIFT = (3.0, 7.0)               # wing drift degrees at the chain root / tip (one wave per loop, travelling out)
WING_LAG = 0.6
ANT_SWAY = 2.5

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
report = {"unit": UNIT, "conquest_character_id": CHAR_ID, "source": "none: first fully from-scratch unit (sheet "
          "design/reference/firefly-character-sheet.webp)", "tier": "regular", "tri_budget": TRI_BUDGET,
          "units": "sheet units: 1 unit = 100 px of the reference sheet (orthographic views); floor z = 0 at the flame tip",
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
_pz = np.array([p[0] for p in TORSO_PROFILE]); _pr = np.array([p[1] for p in TORSO_PROFILE])
_dense = SD.catmull(np.stack([np.zeros_like(_pz), _pz, _pr], 1), 12)          # smooth profile (y = z, z = r)
TZ, TR = _dense[:, 1], np.clip(_dense[:, 2], 0.0, None)
order_ = np.argsort(TZ, kind="stable"); TZ, TR = TZ[order_], TR[order_]


def torso_rx(z):
    return np.interp(z, TZ, TR, left=0.0, right=0.0)


def front_w(P):
    """1 at the front centre (-Y) falling linearly with azimuth to 0 at the sides; 0 on the back half (chevrons)."""
    rho = np.hypot(P[:, 0], P[:, 1])
    c = np.clip(-P[:, 1] / np.maximum(rho, 1e-9), -1.0, 1.0)
    return np.clip(1.0 - np.arccos(c) / (math.pi / 2), 0.0, 1.0)


BAND_EDGES = [(nm + "_top", *BANDS[nm][0]) for nm in "ABC"] + [(nm + "_bot", *BANDS[nm][1]) for nm in "ABC"]


def band_field(P, chev):
    return P[:, 2] + chev * front_w(P)


def sd_torso(P):
    rx = torso_rx(P[:, 2])
    pin = np.zeros(len(P))
    for _, lvl, chev in BAND_EDGES:
        pin += np.exp(-((band_field(P, chev) - lvl) / PINCH_W) ** 2)
    rx = rx * (1.0 - PINCH * np.clip(pin, 0, 1))
    d = np.hypot(P[:, 0], P[:, 1] / TORSO_DEPTH) - rx
    return np.maximum(d, np.maximum(TZ[0] - P[:, 2], P[:, 2] - TZ[-1]))


PACK_PTS = [(0.0, float(torso_rx(np.array([z]))[0]) * TORSO_DEPTH - r * PACK_FLAT + PACK_PROUD, z, r) for z, r in PACK_KNOTS]


def sd_pack(P):
    """a chain of flattened round cones whose back face follows the torso's back curve, PACK_PROUD off it"""
    d = np.full(len(P), 1e9)
    for (x0, y0, z0, r0), (x1, y1, z1, r1) in zip(PACK_PTS[:-1], PACK_PTS[1:]):
        yc = 0.5 * (y0 + y1)
        Q = P.copy(); Q[:, 1] = yc + (P[:, 1] - yc) / PACK_FLAT
        a = (0.0, yc + (y0 - yc) / PACK_FLAT, z0); b = (0.0, yc + (y1 - yc) / PACK_FLAT, z1)
        d = np.minimum(d, SD.sd_round_cone(Q, a, b, r0, r1) * PACK_FLAT)
    return d


def sd_capped_cyl(P, a, b, r):
    a = np.asarray(a, float); b = np.asarray(b, float)
    ba = b - a; pa = P - a
    baba = float(ba @ ba); paba = pa @ ba
    x = np.linalg.norm(pa * baba - np.outer(paba, ba), axis=1) - r * baba
    y = np.abs(paba - baba * 0.5) - baba * 0.5
    x2 = x * x; y2 = y * y * baba
    d = np.where(np.maximum(x, y) < 0.0, -np.minimum(x2, y2), np.where(x > 0, x2, 0.0) + np.where(y > 0, y2, 0.0))
    return np.sign(d) * np.sqrt(np.abs(d)) / baba


def sd_torus(P, c, axis, R0, r):
    a = unit(axis)
    Q = P - np.asarray(c, float)
    h = Q @ a
    rad = np.linalg.norm(Q - np.outer(h, a), axis=1)
    return np.hypot(rad - R0, h) - r


HC = np.array(HEAD_C, float)
HR = np.array(HEAD_R, float)
HELM_C = HC + np.array([0.0, 0.0, HELMET_LIFT])
HELM_R = HR * HELMET_SCALE
_bn = math.sqrt(1.0 + BRIM_SLOPE ** 2)


BRIM_C = HELM_C + np.array([0.0, BRIM_REACH[2], 0.0])
BRIM_RR = HR * np.array([BRIM_REACH[0], BRIM_REACH[1], 1.0])


def sd_helmet(P):
    plane = (BRIM_Z0 + BRIM_SLOPE * P[:, 1] - P[:, 2]) / _bn            # < 0 above the brim line
    dome = np.maximum(SD.sd_ellipsoid(P, HELM_C, HELM_R), plane)
    lip = np.maximum(np.abs(plane) - BRIM_T, SD.sd_ellipsoid(P, BRIM_C, BRIM_RR))   # thin lip slab round the brim line
    return SD.smin(dome, lip, 0.01)


def sd_head(P):
    return SD.sd_ellipsoid(P, HC, HR)


def sd_neck(P):
    return SD.sd_round_cone(P, (0, 0.02, 4.92), (0, 0.0, 5.34), 0.24, 0.20)


# goggles: surface point + axis per side
GOG = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    az, el = math.radians(GOGGLE_AZ), math.radians(GOGGLE_EL)
    d = np.array([sg * math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    S, n = FP.ell_point(HC, HR, d)
    ax = unit(0.5 * d + 0.5 * n)
    GOG[side] = {"surface": S, "axis": ax, "face": S + ax * GOGGLE_PROUD}


def sd_cups(P):
    return np.minimum(*[sd_capped_cyl(P, g["surface"] - g["axis"] * 0.10, g["face"], GOGGLE_CUP_R) for g in GOG.values()])


_sd_d = np.array([0.0, -math.cos(math.radians(SNOUT_EL)), math.sin(math.radians(SNOUT_EL))])
SNOUT_S, _ = FP.ell_point(HC, HR, _sd_d)
SNOUT_A = unit(SNOUT_AXIS)


def sd_snout(P):
    return SD.sd_round_cone(P, SNOUT_S - SNOUT_A * 0.08, SNOUT_S + SNOUT_A * SNOUT_LEN, SNOUT_R[0], SNOUT_R[1])


def snout_r_at(t_):
    return SNOUT_R[0] + (SNOUT_R[1] - SNOUT_R[0]) * (t_ + 0.08) / (SNOUT_LEN + 0.08)


def sd_canister(P):
    return sd_capped_cyl(P, SNOUT_S + SNOUT_A * CANISTER[0], SNOUT_S + SNOUT_A * CANISTER[1], CANISTER[2])


# side thrusters: surface point, outward axis
def torso_surface(z, az_deg, sg):
    az = math.radians(az_deg)
    dx, dy = sg * math.sin(az), -math.cos(az)
    rx = float(torso_rx(np.array([z]))[0])
    t = rx / math.sqrt(dx * dx + (dy / TORSO_DEPTH) ** 2)
    S = np.array([t * dx, t * dy, z])
    dr = (float(torso_rx(np.array([z + 1e-3]))[0]) - float(torso_rx(np.array([z - 1e-3]))[0])) / 2e-3
    n = unit([S[0], S[1] / TORSO_DEPTH ** 2, -dr * rx])
    return S, n


PORT = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    for pn, (z, az, r_out, r_in, st, proud, depth) in PORTS.items():
        S, n = torso_surface(z, az, sg)
        PORT[(side, pn)] = {"surface": S, "axis": n, "mouth": S + n * proud, "floor": S + n * (proud - depth),
                            "r_out": r_out, "r_in": r_in, "stretch": st, "proud": proud, "depth": depth}


def sd_rails(P):
    ds = []
    for side in ("L", "R"):
        a = PORT[(side, "up")]["surface"] + PORT[(side, "up")]["axis"] * 0.01
        b = PORT[(side, "low")]["surface"] + PORT[(side, "low")]["axis"] * 0.01
        ds.append(SD.sd_round_cone(P, a, b, RAIL_R, RAIL_R))
    return np.minimum(*ds)


PART_SDF = {"torso": sd_torso, "pack": sd_pack, "rail": sd_rails, "head": lambda P: np.minimum(sd_head(P), sd_neck(P)),
            "helmet": sd_helmet, "cup": sd_cups, "snout": sd_snout, "canister": sd_canister}
GROUP_REGION = {"torso": "shell", "pack": "pack", "rail": "rail", "head": "mask", "helmet": "helmet",
                "cup": "goggle_cup", "snout": "snout", "canister": "filter"}
GROUPS = list(PART_SDF)
g = SD.Grid((-0.74, -0.78, 2.06), (0.74, 0.64, 6.26), SDF_STEP, SDF_BAND)
g.apply(sd_torso, (-0.64, -0.50, 2.10), (0.64, 0.50, 5.14), 0.0)
g.apply(sd_pack, (-0.27, 0.05, 2.65), (0.27, 0.60, 5.12), 0.05)
g.apply(sd_rails, (-0.72, -0.25, 3.20), (0.72, 0.35, 4.75), 0.03)
g.apply(sd_neck, (-0.26, -0.26, 4.66), (0.26, 0.26, 5.56), 0.06)
g.apply(sd_head, HC - HR, HC + HR, 0.06)
g.apply(sd_helmet, np.minimum(HELM_C - HELM_R, BRIM_C - BRIM_RR), np.maximum(HELM_C + HELM_R, BRIM_C + BRIM_RR), 0.012)
_cup_pts = np.array([p for gg in GOG.values() for p in (gg["surface"] - gg["axis"] * 0.10, gg["face"])])
g.apply(sd_cups, _cup_pts.min(0) - GOGGLE_CUP_R, _cup_pts.max(0) + GOGGLE_CUP_R, 0.035)
_sn_pts = np.array([SNOUT_S - SNOUT_A * 0.08, SNOUT_S + SNOUT_A * CANISTER[1]])
g.apply(sd_snout, _sn_pts.min(0) - 0.16, _sn_pts.max(0) + 0.16, 0.035)
g.apply(sd_canister, _sn_pts.min(0) - 0.16, _sn_pts.max(0) + 0.16, 0.008)
HV, HF = SD.polygonise(g)
HF = [list(map(int, f)) for f in HF]
HV, HF, crumbs = keep_islands(HV, HF, 0.02)
report["sdf"] = {"step": SDF_STEP, "grid": g.n.tolist(), "ops": g.ops, "high_tris": len(HF), "high_verts": len(HV),
                 "crumbs": crumbs, "seconds": round(time.time() - t_sdf, 1)}
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
report["retopo"] = {"method": "SDF marching tetrahedra (step %.3f) -> collapse decimation (deterministic)" % SDF_STEP,
                    "decimated_tris": tri_count_F(LF), "specks": specks,
                    "low_to_high": {"mean": round(float(dev.mean()), 5), "p99": round(float(np.percentile(dev, 99)), 5),
                                    "max": round(float(dev.max()), 5)},
                    "high_to_low_sampled": {"p99": round(float(np.percentile(dev2, 99)), 5), "max": round(float(dev2.max()), 5)},
                    "seconds": round(time.time() - t, 1)}
BAKE_CAGE = max(0.02, round(3.0 * float(np.percentile(dev2, 99)), 4))

# =========================================================================== 3. region fields + iso cuts on the shell
DGR = np.stack([PART_SDF[gname](LV) for gname in GROUPS], 1)


def own_field(D, j):
    return np.delete(D, j, axis=1).min(1) - D[:, j]       # > 0 where group j owns the surface


FIELDS = {"f_" + gname: own_field(DGR, j) for j, gname in enumerate(GROUPS)}
for nm, lvl, chev in BAND_EDGES:
    FIELDS["b_" + nm] = band_field(LV, chev)
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
in_pack = lambda a, b: a[LAY["f_pack"]] > -TOL and b[LAY["f_pack"]] > -TOL
CUTS = [("f_" + gname, 0.0, None) for gname in GROUPS if gname != "torso"]
CUTS += [("b_" + nm, lvl, in_torso) for nm, lvl, _ in BAND_EDGES]
CUTS += [("z", PACK_BAND[0], in_pack), ("z", PACK_BAND[1], in_pack)]
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
for nm in "ABC":
    top = [e for e in BAND_EDGES if e[0] == nm + "_top"][0]
    bot = [e for e in BAND_EDGES if e[0] == nm + "_bot"][0]
    inb = is_torso & (FVAL["b_" + top[0]] < top[1]) & (FVAL["b_" + bot[0]] > bot[1])
    shell_reg[inb] = "band"
is_pack = owner == GROUPS.index("pack")
shell_reg[is_pack & (FVAL["z"] > PACK_BAND[0]) & (FVAL["z"] < PACK_BAND[1])] = "band"
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1), "shell_tris_after_cuts": len(CF)}

# =========================================================================== 4. parts
PARTS = []                      # FP.Part with .bone (rigid) or .chain = (chain name, s_arc per vertex, chain length)
for side in ("L", "R"):
    gg = GOG[side]
    V, F, R = FP.torus(GOGGLE_R + RIM_TUBE * 0.35, RIM_TUBE, 36, 8, gg["face"], gg["axis"], region="goggle_rim")
    PARTS.append(FP.Part("goggle_rim." + side, V, F, R, bone="head"))
    V, F, R = FP.hex_lens(gg["face"] - gg["axis"] * 0.005, gg["axis"], GOGGLE_R + RIM_TUBE * 0.2, LENS_DOME, LENS_CELL,
                          LENS_INSET)
    PARTS.append(FP.Part("lens." + side, V, F, R, bone="head"))
for k, t_ in enumerate(SNOUT_RIBS):
    V, F, R = FP.torus(snout_r_at(t_) + SNOUT_RIB_R * 0.2, SNOUT_RIB_R, 20, 6, SNOUT_S + SNOUT_A * t_, SNOUT_A, region="strap")
    PARTS.append(FP.Part("snout_rib.%d" % k, V, F, R, bone="head"))
# helmet straps: a band above the brim round the front half + two over-the-top straps
_band_dirs = []
for ph in np.linspace(-112.0, 112.0, 41):
    el = math.radians(17.0 - 30.0 * (abs(ph) / 112.0) ** 2)
    _band_dirs.append((math.sin(math.radians(ph)) * math.cos(el), -math.cos(math.radians(ph)) * math.cos(el), math.sin(el)))
V, F, R = FP.strap(HELM_C, HELM_R, _band_dirs, STRAP_W, STRAP_T, STRAP_SINK, closed=False)
PARTS.append(FP.Part("strap.band", V, F, R, bone="head"))
for sg, nm in ((1.0, "L"), (-1.0, "R")):
    dirs = []
    for tt in np.linspace(math.radians(12.0), math.radians(196.0), 30):
        dirs.append((sg * STRAP_X / HELM_R[0], -math.cos(tt), math.sin(tt)))
    V, F, R = FP.strap(HELM_C, HELM_R, dirs, STRAP_W * 0.85, STRAP_T, STRAP_SINK, closed=False)
    PARTS.append(FP.Part("strap.top." + nm, V, F, R, bone="head"))
# side-thruster cans
for (side, pn), pt in sorted(PORT.items()):
    pr, dp = pt["proud"], pt["depth"]
    hb = -0.10
    prof = [(0.0, pr - dp), (pt["r_in"], pr - dp), (pt["r_in"], pr - 0.014), (pt["r_in"] + 0.012, pr),
            (pt["r_out"] - 0.012, pr), (pt["r_out"], pr - 0.014), (pt["r_out"], hb), (0.0, hb)]
    regs = ["port_hole", "port_glow", "port_rim", "port_rim", "port_rim", "port_rim", "port_rim"]
    V, F, R = FP.lathe(prof, regs, 28, pt["surface"], pt["axis"], su=pt["stretch"])
    PARTS.append(FP.Part("port.%s.%s" % (side, pn), V, F, R, bone="body"))
# exhaust nozzle
V, F, R = FP.lathe([(r, z - 2.0) for r, z in NOZZLE], NOZZLE_REG, 32, (0, 0, 2.0), (0, 0, 1))
PARTS.append(FP.Part("nozzle", V, F, R, bone="abdomen"))
# feather antennae
ANT = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    bd = np.array([sg * ANT_BASE_DIR[0], ANT_BASE_DIR[1], ANT_BASE_DIR[2]])
    base, bn_ = FP.ell_point(HELM_C, HELM_R, bd)
    base = base - bn_ * 0.03
    yaw = math.radians(ANT_VANE_YAW)
    vn = np.array([sg * math.sin(yaw), -math.cos(yaw), 0.0])
    V, F, R, S_, C_ = FP.feather(base, (sg * ANT_TIP_DIR[0], ANT_TIP_DIR[1], ANT_TIP_DIR[2]), ANT_LEN,
                                 (sg * ANT_BEND, 0.0, 0.0), vn, ANT_VANE_S0, ANT_HALF_W, ANT_SERR[0], ANT_SERR[1],
                                 ANT_THICK, ANT_STALK_R)
    L_ = float(np.linalg.norm(np.diff(C_, axis=0), axis=1).sum())
    p = FP.Part("antenna." + side, V, F, R)
    p.chain = ("antenna." + side, S_ * L_, L_, "head", ANT_BONES)
    PARTS.append(p)
    ANT[side] = {"rachis": C_, "length": L_}
# smoke wings: two sheets per port
WING_INFO, WSPINE = {}, {}
for (side, pn), pt in sorted(PORT.items()):
    sg = 1.0 if side == "L" else -1.0
    wc = WINGS[pn]
    for layer in ("main", "layer"):
        ly = layer == "layer"
        sweep = math.radians(wc["sweep"] + (WING_LAYER["sweep_add"] if ly else 0.0))
        e_a = np.array([sg * math.cos(sweep), math.sin(sweep), 0.0])
        e_b = np.array([0.0, 0.0, 1.0])
        k_ = WING_LAYER["scale"] if ly else 1.0
        n_pl = unit(np.cross(e_a, e_b))
        wroot = WING_ROOT_W * pt["r_in"] * pt["stretch"] * (0.8 if ly else 1.0)
        top = [(0.0, wroot)] + [(a * k_, wroot + (b - wroot) * k_) for a, b in wc["top"]]
        exit_pt = pt["mouth"] + pt["axis"] * WING_EXIT + (n_pl * WING_LAYER["offset"] * sg if ly else 0.0)
        seed = wc["seed"] + (WING_LAYER["seed_add"] if ly else 0.0) + (0 if side == "L" else 13)
        V, F, S_, spine3d, IJ, info = FP.wing_sheet(pt["floor"], exit_pt, e_a, e_b, top, wroot,
                                                    wc["drop"] * (WING_LAYER["drop"] if ly else 1.0), WING_NS, WING_NT,
                                                    WING_RAG_END, WING_RAG_EDGE, WING_BILLOW * sg, WING_FOLD * sg,
                                                    WING_TRAIL, seed, tuck=WING_TUCK)
        chain = "wing.%s.%s" % (side, pn)
        if not ly:
            WSPINE[chain] = spine3d
        # smoke regions: root (denser, where it leaves the hole) / body / lighter wisps / ragged thin edges
        R = []
        for f, (i, j) in zip(F, IJ):
            sm = float(np.mean(S_[f]))
            wisp = math.sin(9.0 * sm + 2.2 * math.pi * (j / WING_NT) + seed) > 0.5
            if sm < info["s_neck"] + 0.06:
                R.append("smoke_root")
            elif (j == WING_NT - 1 and sm > info["s_neck"] + 0.12) or i == info["rows"] - 2:
                R.append("smoke_edge")
            elif wisp:
                R.append("smoke_wisp")
            else:
                R.append("smoke")
        p = FP.Part("%s.%s" % (chain, layer), V, F, R)
        p.chain = (chain, S_ * info["length"], info["length"], "body", WING_BONES)
        p.smoke = True
        PARTS.append(p)
        WING_INFO["%s.%s" % (chain, layer)] = {"top_edge_length": round(info["length"], 4), "neck_len": round(info["neck_len"], 4),
                                                "sweep_deg": round(math.degrees(sweep), 1), "root_half_height": round(wroot, 4),
                                                "drop": round(wc["drop"] * (WING_LAYER["drop"] if ly else 1.0), 3),
                                                "tris": tri_count_F(F)}
# the chain length for weights = its MAIN sheet's length (the layer sheet rides the same chain by its own arc)
CHAIN_LEN = {}
for p in PARTS:
    if getattr(p, "chain", None) and p.name.endswith(".main"):
        CHAIN_LEN[p.chain[0]] = p.chain[2]

# =========================================================================== 5. flame (own object)
fl_prof = []
for k in range(FLAME_ST):
    s = k / (FLAME_ST - 1)
    z = FLAME_TOP_Z * (1.0 - s)
    gshape = (s / FLAME_WIDE_S) ** 0.55 if s <= FLAME_WIDE_S else ((1.0 - s) / (1.0 - FLAME_WIDE_S)) ** FLAME_TAPER
    fl_prof.append((FLAME_R * gshape, z - FLAME_TOP_Z))
fl_reg = ["flame_outer" if (k + 0.5) / (FLAME_ST - 1) < FLAME_LICK_S else "flame_lick" for k in range(FLAME_ST - 1)]
_fs = np.linspace(0, 1, FLAME_ST)


def flame_rmod(k, th):
    """lobes round the flame (slow spiral) x stacked licks down its sides: each lick is a sawtooth in s whose phase
    follows the lobes, so the tongues stagger round the flame instead of forming rings."""
    s = _fs[k]
    lobe = 1.0 + FLAME_LOBE_A * s ** 1.1 * math.cos(FLAME_LOBES * th + FLAME_TWIST * s)
    if s <= FLAME_WIDE_S * 0.6:
        return lobe
    saw = (s * FLAME_LICKS + 0.5 * math.cos(FLAME_LOBES * th + FLAME_TWIST * s)) % 1.0
    return lobe * (1.0 - FLAME_LICK_A * min(1.0, (s - FLAME_WIDE_S * 0.6) / 0.2) * (1.0 - saw) ** 1.6)


Vo, Fo, Ro = FP.lathe(fl_prof, fl_reg, FLAME_SEG, (0, 0, FLAME_TOP_Z), (0, 0, 1), rmod=flame_rmod)
co_prof = []
CORE_TOP = FLAME_TOP_Z - 0.06
for k in range(12):
    s = k / 11
    z = CORE_TOP + (CORE_TIP_Z - CORE_TOP) * s
    gshape = (s / 0.22) ** 0.6 if s <= 0.22 else ((1.0 - s) / 0.78) ** 0.85
    co_prof.append((CORE_R * gshape, z - CORE_TOP))
_cs = np.linspace(0, 1, 12)
Vc, Fc, Rc = FP.lathe(co_prof, ["flame_core"] * 11, FLAME_SEG, (0, 0, CORE_TOP), (0, 0, 1),
                      rmod=lambda k, th: 1.0 + 0.12 * _cs[k] * math.cos(FLAME_LOBES * th + FLAME_TWIST * _cs[k] + 0.6))
FLV = np.vstack([Vc, Vo])                                  # core first: drawn before the translucent outer
FLF = [list(f) for f in Fc] + [[i + len(Vc) for i in f] for f in Fo]
FLR = list(Rc) + list(Ro)
report["flame"] = {"tris": tri_count_F(FLF), "outer_open_edges": FP.open_edges(Fo), "core_open_edges": FP.open_edges(Fc),
                   "top_z": FLAME_TOP_Z, "max_r": FLAME_R, "core_tip_z": CORE_TIP_Z,
                   "rule": "own object 'firefly_flame' (toggle = hide the node), skinned 100% to bone 'flame'"}

# =========================================================================== 6. assemble + centre
ISL = [("shell", CV, CF, list(shell_reg), None)] + [(p.name, p.V, p.F, p.R, p) for p in PARTS]
allV = np.vstack([i[1] for i in ISL] + [FLV])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, lo0[2]])
report["centre_shift"] = SHIFT.round(6).tolist()
VALL, FALL, RNAME, MATI, RANGE = [], [], [], [], {}
nv_ = 0
for name, V, F, R, p in ISL:
    RANGE[name] = (nv_, nv_ + len(V))
    VALL.append(V - SHIFT)
    FALL += [[i + nv_ for i in f] for f in F]
    RNAME += list(R)
    MATI += [1 if (p is not None and getattr(p, "smoke", False)) else 0] * len(F)
    nv_ += len(V)
VALL = np.vstack(VALL)
FLV = FLV - SHIFT
HV_s = HV - SHIFT
REG = [r for r in ["shell", "band", "pack", "rail", "helmet", "strap", "mask", "goggle_cup", "snout", "filter", "goggle_rim",
                   "lens_cell", "lens_grid", "antenna_vane", "antenna_stalk", "port_rim", "port_glow", "port_hole", "nozzle",
                   "abdomen_tip", "nozzle_throat", "smoke_root", "smoke", "smoke_wisp", "smoke_edge"]]
assert set(RNAME) <= set(REG), sorted(set(RNAME) - set(REG))
REG_F = ["flame_outer", "flame_lick", "flame_core"]
rid = np.array([REG.index(r) for r in RNAME], dtype=np.int32)
rid_f = np.array([REG_F.index(r) for r in FLR], dtype=np.int32)


def jitter(V, F):
    FCc = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FCc @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.96 + 0.08 * j


# ---- materials
def make_mat(name, kind):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    if kind != "body":
        nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
        amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
        nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
        mat.surface_render_method = "DITHERED"          # Eevee: order-independent see-through (glTF alphaMode BLEND)
        mat.use_backface_culling = kind == "flame"       # smoke sheets are double-sided, the flame shell is not
        mat.use_transparent_shadow = True
    else:
        mat.use_backface_culling = True                  # opaque closed body: single-sided (glTF doubleSided false)
        nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
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
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal); apply_alpha(s_.material, pal)
    return out


def glow_tiers(pal):
    """firefly-glow gate: emission tiers rise band < lens < port < abdomen tip < throat < flame outer < core, and (default
    skin) every glow accent is a green-tinged yellow: HSV hue in [60, 100] deg (lampyrid light ~560 nm ~ hue 75)."""
    import colorsys
    grade = ["band", "lens_cell", "port_glow", "abdomen_tip", "nozzle_throat", "flame_lick", "flame_outer", "flame_core"]
    tier = {n: float(pal["regions"][n].get("emission_scale", 0.0)) for n in grade}
    hues = {}
    for n, v in pal["regions"].items():
        if "emission" in v:
            h_, s_, v_ = colorsys.rgb_to_hsv(*[c / 255.0 for c in v["rgb"]])
            hues[n] = round(h_ * 360.0, 1)
    ok_grade = all(tier[a] < tier[b] for a, b in zip(grade, grade[1:]))
    ok_hue = all(60.0 <= h <= 100.0 for h in hues.values())
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "grade": grade, "grade_pass": ok_grade,
            "accent_hue_deg": hues, "firefly_hue_window": [60, 100], "hue_pass": ok_hue}


MAT_BODY = make_mat(UNIT + "_body", "body")
MAT_SMOKE = make_mat(UNIT + "_smoke", "smoke")
MAT_FLAME = make_mat(UNIT + "_flame", "flame")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["grade_pass"] and report["glow_tiers"]["default"]["hue_pass"], report["glow_tiers"]

# ---- the main object
low = new_obj(UNIT, VALL, FALL)
me = low.data
me.materials.append(MAT_BODY); me.materials.append(MAT_SMOKE)
me.polygons.foreach_set("material_index", np.array(MATI, dtype=np.int32))
shade = jitter(VALL, FALL)
shade[np.array(MATI) == 1] = 1.0
PAL.store_regions(me, REG, rid, shade)
flame = new_obj(UNIT + "_flame", FLV, FLF)
flame.data.materials.append(MAT_FLAME)
PAL.store_regions(flame.data, REG_F, rid_f, np.ones(len(FLF)))
report["regions_faces"] = repaint([low, flame], pal_default)
report["alpha"] = {"main": paint_alpha(me, pal_default), "flame": paint_alpha(flame.data, pal_default),
                   "material_alpha": pal_default["material"].get("alpha", 1.0)}
me.update(); flame.data.update()
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})

# facing landmark: the head centre -> the respirator canister tip (the snout points forward-down on the midline)
anchor = HC - SHIFT
landmark = SNOUT_S + SNOUT_A * CANISTER[1] - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head (helmet) centre -> respirator filter-canister tip on the midline",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}

# ---- flat + UV (both objects)
for ob_ in (low, flame):
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

tris_main = tri_count_F(FALL)
tris_flame = tri_count_F(FLF)
report["tris"] = {"total": tris_main + tris_flame, "main": tris_main, "flame": tris_flame,
                  "shell": tri_count_F(CF), **{p.name: p.tris() for p in PARTS}}
report["tier_rationale"] = ("REGULAR role (a line unit, not a boss or hero): window [%d, %d], the same ceiling class as "
                            "supaoctto's regular tier. The sheet's hard-surface detail -- honeycomb lenses, straps, four "
                            "rimmed thrusters, serrated feathers, eight smoke sheets, a lobed flame -- needs ~2x crowd "
                            "density to read, so the build lands mid-window." % tuple(TRI_BUDGET))
open_e = {}
for name, V, F, R, p in ISL:
    open_e[name] = FP.open_edges(F)
report["open_edges"] = {"closed_parts": {k: v for k, v in open_e.items() if v == 0 and not k.startswith("wing")},
                        "open_parts": {k: v for k, v in open_e.items() if v > 0},
                        "rule": "every solid is closed; open = the single-surface smoke sheets (double-sided material) "
                                "and the lens domes (rim buried under the goggle ring)"}
lo_a = np.vstack([VALL, FLV]).min(0); hi_a = np.vstack([VALL, FLV]).max(0)
H = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(H, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "sheet_px_equivalent": {"height_px": round(100 * H, 1), "wingspan_px": round(100 * float(hi_a[0] - lo_a[0]), 1)},
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint"}}
for ob_ in (low, flame):
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
low["conquest_source"] = "from scratch: design/reference/firefly-character-sheet.webp (no sculpt)"
low["conquest_scale_policy"] = "natural proportions in sheet units (1 = 100 sheet px); game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
low["conquest_alpha_channel"] = ("Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor; smoke "
                                 "material alphaMode BLEND double-sided, flame material alphaMode BLEND single-sided, body opaque")
flame["conquest_toggle"] = "the exhaust flame is its own node: hide it to turn the flame off (artist question open: always-on?)"
# focus boxes for the close-up renders (world, after the centre shift)
_head_pts = np.vstack([VALL[RANGE["goggle_rim.L"][0]:RANGE["goggle_rim.L"][1]], VALL[RANGE["goggle_rim.R"][0]:RANGE["goggle_rim.R"][1]],
                       (HC - SHIFT - HR * 1.1)[None], (HC - SHIFT + HR * 1.1)[None],
                       (SNOUT_S + SNOUT_A * CANISTER[1] - SHIFT)[None]])
_port_pts = np.vstack([PORT[("L", "up")]["mouth"] - SHIFT, PORT[("L", "low")]["mouth"] - SHIFT,
                       PORT[("L", "up")]["surface"] - SHIFT + np.array([0.9, 0.5, 0.5]),
                       PORT[("L", "low")]["surface"] - SHIFT + np.array([0.9, 0.5, -0.6])])
_flame_pts = np.vstack([FLV, np.array([[0.4, 0.4, 2.40], [-0.4, -0.4, 2.40]]) - SHIFT])
low["conquest_focus"] = json.dumps({"head": [_head_pts.min(0).tolist(), _head_pts.max(0).tolist()],
                                    "port": [_port_pts.min(0).tolist(), _port_pts.max(0).tolist()],
                                    "flame": [_flame_pts.min(0).tolist(), _flame_pts.max(0).tolist()],
                                    "axis_xy": [float(-SHIFT[0]), float(-SHIFT[1])]})

if PREVIEW:
    for o in list(scene.objects):
        if o not in (low, flame):
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("sdf", "retopo", "tris", "measure", "facing", "open_edges",
                                                            "regions_area_share", "glow_tiers")}))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 7. bake normal + AO (high = SDF shell + parts)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
PH_V, PH_F = [HV_s], [list(f) for f in HF]
o_ = len(HV_s)
for name, V, F, R, p in ISL[1:]:
    if getattr(p, "smoke", False):
        continue                                     # smoke sheets never occlude the body (they are see-through)
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
BAKE_NODES = {}
for mat_ in (MAT_BODY, MAT_SMOKE):
    nt_ = mat_.node_tree
    tn = nt_.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt_.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    BAKE_NODES[mat_.name] = (tn, ta)
flame.hide_render = True
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
for typ, idx, samples in (("NORMAL", 0, 1), ("AO", 1, 16)):
    for mat_ in (MAT_BODY, MAT_SMOKE):
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
body_faces = np.array(MATI) == 0
alltex_n = texels_of(body_faces, RN)
alltex_a = texels_of(body_faces, RA)
bstats.update({
    "cage_extrusion": BAKE_CAGE, "cage_rule": "3 x the sampled high->low p99 distance (floor 0.02)",
    "resolution": {"normal": RN, "ao": RA},
    "high_tris": len(PH_F), "high_rule": "SDF shell (marching tets) + every closed part; smoke sheets excluded (see-through)",
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
np.save(os.path.join(tempfile.gettempdir(), "firefly_normal_twin.npy" if DIGEST_ONLY else "firefly_normal_main.npy"), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), "firefly_ao_twin.npy" if DIGEST_ONLY else "firefly_ao_main.npy"), pa[:, :1])
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
# wire the textures into the BODY material only (the smoke keeps flat colour + alpha)
nt = MAT_BODY.node_tree
tn, ta = BAKE_NODES[MAT_BODY.name]
for n_ in BAKE_NODES[MAT_SMOKE.name]:
    MAT_SMOKE.node_tree.nodes.remove(n_)
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
flame.hide_render = False
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


DIG["geometry_colour_uv"] = geometry_digest([low, flame])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "authored from the sheet's palette swatches; accents shifted to firefly glow (artist)"}
report["wings"] = WING_INFO
report["ports"] = {"%s.%s" % k: {"surface": (v["surface"] - SHIFT).round(4).tolist(), "axis": v["axis"].round(4).tolist(),
                                 "r_out": v["r_out"], "r_in": v["r_in"], "stretch": v["stretch"], "proud": v["proud"]}
                   for k, v in sorted(PORT.items())}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 8. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
S0 = SHIFT


def P_(p):
    return np.asarray(p, float) - S0


BONES = [("body", P_((0, 0, 3.90)), P_((0, 0, 4.90)), "root"),
         ("abdomen", P_((0, 0, 3.00)), P_((0, 0, 2.20)), "body"),
         ("head", P_((0, 0, 5.12)), P_(HC + np.array([0, 0, 0.55])), "body"),
         ("flame", P_((0, 0, FLAME_TOP_Z)), P_((0, 0, 0.0)), "abdomen")]
CHAIN_PTS = {}
for side in ("L", "R"):
    C_ = ANT[side]["rachis"]
    idx = np.linspace(0, len(C_) - 1, ANT_BONES + 1).round().astype(int)
    CHAIN_PTS["antenna." + side] = [P_(C_[i]) for i in idx]
for chain, sp in sorted(WSPINE.items()):
    al = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(sp, axis=0), axis=1))])
    tt = np.linspace(0, al[-1], WING_BONES + 1)
    CHAIN_PTS[chain] = [P_([np.interp(x, al, sp[:, k]) for k in range(3)]) for x in tt]
for chain, pts in sorted(CHAIN_PTS.items()):
    parent = "head" if chain.startswith("antenna") else "body"
    for k in range(len(pts) - 1):
        BONES.append(("%s.%d" % (chain, k), pts[k], pts[k + 1], parent if k == 0 else "%s.%d" % (chain, k - 1)))
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

# ---- weights (<= 4 influences), main mesh
Wt = np.zeros((len(VALL), len(DEFORM)))
a_, b_ = RANGE["shell"]
zz = VALL[a_:b_, 2] + S0[2]
wh = smoothstep(NECK_Z[0], NECK_Z[1], zz)
wa = smoothstep(WAIST_Z[1], WAIST_Z[0], zz)
Wt[a_:b_, J["head"]] = wh
Wt[a_:b_, J["abdomen"]] = wa
Wt[a_:b_, J["body"]] = np.clip(1.0 - wh - wa, 0, None)
for p in PARTS:
    a_, b_ = RANGE[p.name]
    if getattr(p, "chain", None):
        chain, s_arc, _, parent, nb = p.chain
        L_ = CHAIN_LEN.get(chain, ANT.get(chain.split(".")[-1], {}).get("length", p.chain[2]))
        Wv = K.vine_weights(np.clip(s_arc, 0, L_), L_, nb)
        Wt[a_:b_, J[parent]] += Wv[:, 0]
        for k in range(nb):
            Wt[a_:b_, J["%s.%d" % (chain, k)]] += Wv[:, k + 1]
    else:
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
fvg = flame.vertex_groups.new(name="flame")
fvg.add(list(range(len(flame.data.vertices))), 1.0, "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "flame_object": "all %d vertices 1.0 on 'flame'" % len(flame.data.vertices),
                  "rule": "shell: head above NECK_Z blend, abdomen below WAIST_Z blend, body between; goggles/lenses/straps "
                          "head; thruster cans body; nozzle abdomen; antennae + smoke sheets: rigkit.vine_weights arc-length "
                          "hat weights along their chain (root blends from the parent: the smoke root is pinned in its hole)"}
for ob_ in (low, flame):
    ob_.parent = rig
    ob_.matrix_parent_inverse = Matrix.Identity(4)
    am = ob_.modifiers.new("Armature", "ARMATURE"); am.object = rig

# ---- PLACEHOLDER idle: hover bob + slight wing drift + antenna sway (closed-form, integer cycles -> exact seam)
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"
REST = {b.name: np.array(b.matrix_local)[:3, :3] for b in arm_data.bones}


def local_loc(bone, dw):
    return Vector(REST[bone].T @ np.asarray(dw, float))


act = bpy.data.actions.new("idle")
act.use_fake_user = True
K.assign_action(rig, act)
key_rows = []
wing_chains = sorted(c for c in CHAIN_PTS if c.startswith("wing"))
for f in range(IDLE_N + 1):
    u = (f % IDLE_N) / IDLE_N
    pose = {"body": ((0.0, 0.0, HOVER + BOB * math.sin(2 * math.pi * u)), None)}
    for ci, chain in enumerate(wing_chains):
        for k in range(WING_BONES):
            ang = K.vine_wave(u, k, WING_BONES, WING_DRIFT[0], WING_DRIFT[1], WING_LAG, 1, phase=0.4 * ci)
            pose["%s.%d" % (chain, k)] = (None, ang)
    for side in ("L", "R"):
        for k in range(ANT_BONES):
            pose["antenna.%s.%d" % (side, k)] = (None, ANT_SWAY * math.sin(2 * math.pi * u + 0.8 * k + (0 if side == "L" else 1.3)))
    for bn, (loc, ang) in pose.items():
        pb = rig.pose.bones[bn]
        if loc is not None:
            pb.location = local_loc(bn, loc)
            pb.keyframe_insert("location", frame=f + 1)
        q = Quaternion((1.0, 0.0, 0.0), math.radians(ang)) if ang is not None else Quaternion()
        pb.rotation_quaternion = q
        pb.keyframe_insert("rotation_quaternion", frame=f + 1)
        key_rows.append(list(pb.location) + list(pb.rotation_quaternion))
for fc in K.action_fcurves(act):
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
act.use_frame_range = True
act.frame_start, act.frame_end = 1, IDLE_N + 1
act.use_cyclic = True
act["placeholder"] = "PLACEHOLDER idle (hover bob + slight wing drift): movement identity awaits the artist's answers"
act["wingbeats"] = 1
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
first = last = ffirst = flast = None
for f in range(1, IDLE_N + 2):
    scene.frame_set(f)
    C = eval_coords(low); Cf = eval_coords(flame)
    samples.append(C[::7]); samples.append(Cf[::5])
    if f == 1:
        first, ffirst = C, Cf
    if f == IDLE_N + 1:
        last, flast = C, Cf
    minz = min(minz, float(C[:, 2].min()), float(Cf[:, 2].min()))
    root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = {"idle": {"status": "PLACEHOLDER (movement wave pending the artist's answers)", "frames": [1, IDLE_N + 1],
                         "seconds": IDLE_N / K.FPS, "cyclic": True,
                         "seam_main_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
                         "seam_flame_mm": round(float(np.linalg.norm(ffirst - flast, axis=1).max()) * 1000, 6),
                         "min_z": round(minz, 4), "hover": HOVER, "bob": BOB, "wing_drift_deg": list(WING_DRIFT),
                         "root_offset_max": round(root_off, 8)},
                "walk": "NOT BUILT: locomotion identity (darts vs cruise, wings flap vs drift, flame always-on vs perch, "
                        "machine vs bug-fused) awaits the artist; clip_names fails on the missing walk by design"}
rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = ("firefly v1 (model lane): root > body > head > antenna.{L,R}.{0,1}; body > abdomen > flame; "
                       "body > wing.{L,R}.{up,low}.{0,1,2} (one chain per side thruster; both smoke sheets ride it)")
low["conquest_clips"] = ["idle"]
low["conquest_clip_status"] = "idle = PLACEHOLDER hover bob + wing drift; NO walk yet (movement wave awaits the artist)"
low["conquest_locomotion"] = "hover (flyer): rest pose on the floor per contract (flame tip z 0); the idle lifts HOVER"
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
    o.select_set(o in (rig, low, flame))
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
              "structure": "armature identity + skinned 'firefly' (body opaque + smoke BLEND primitives) + skinned "
                           "'firefly_flame' (own node, BLEND); natural scale; report-only cell fit %.5f" % k_fit}
geo0 = geometry_digest([low, flame])
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"]}}
pal_e = PAL.load(UNIT, "ember")
gt_e = glow_tiers(pal_e)
assert gt_e["grade_pass"], gt_e
counts_e = repaint([low, flame], pal_e)
out_e = OUT_RIGGED[:-6] + "__ember.blend"
bpy.ops.wm.save_as_mainfile(filepath=out_e, copy=True, compress=True, relative_remap=False)
rep["skins"]["ember"] = {"file": out_e, "palette": PAL.table(pal_e), "palette_files": pal_e["files"], "glow_tiers": gt_e,
                         "region_faces": counts_e, "geometry_colour_uv_digest": geometry_digest([low, flame])}
repaint([low, flame], pal_default)
rep["skins"]["repaint_proof"] = {"rule": "a skin is a pure palette swap: same regions/faces/vertices; default restored",
                                 "default_digest_before": geo0, "default_digest_after_restore": geometry_digest([low, flame])}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "digest", "seconds")}))
print("CLIP", json.dumps(rep["clips"]["idle"]))
print("GLB", json.dumps(rep["glb"]["carries"]))
sys.stdout.flush()
os._exit(0)
