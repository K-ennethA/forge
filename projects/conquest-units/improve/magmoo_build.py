"""Magmoo v2 -- the lava GOO serpent: three glossy red-lava slime pieces + floating droplets, split/recombine idle.

    blender --background source-copies/newunit-magmoo.blend --factory-startup --python improve/magmoo_build.py -- \
        [--stop improved]                (geometry + regions + palette only: no rig -- fast look loop)
        [--outroot <dir>]                (write every output under <dir> instead of the project: the determinism re-run)
        [--skins default,obsidian]       (extra palette skins -> rigged/magmoo__<skin>.blend)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Artist (design/review-log.md 2026-09-26, verbatim, binding): "magmoo should look more like this, lets make it more of a
slime vibe, like red lava instead of rock with lava in it, more lava blob monster / also can we make the pieces separate
and then combine into one long serpent piece as its idle animation". Spec = the original concept sketches
design/reference/magmoo-sketch-1.jpg + -2.jpg (they outrank everything v1 invented).

Reading of the sketches (v2):
  HEAD  a rounded teardrop blob, small dot eyes, flame licks trailing off the back of the skull. The artist's own sculpt
        (newunit-magmoo.blend 'Icosphere': a rounded teardrop whose back frays into flame licks with two dorsal flames)
        IS that head -- v1 read it as the main body. v2 uses it as the head, unchanged in shape, + dot eyes.
  MID   the smooth curved bean: an arch rising out of the bottom piece and curving forward to hold the head (the
        sketch's C-curve), frayed tufts at its torn ends, goo drips hanging off its underside.
  TAIL  the sketch's bottom piece: a splash mound with a jagged dripping crown (where the mid plugs in) whose base
        splays into finger splats on the floor, one finger running out long = the serpent's tail, which itself ends
        in a small finger splat (sketch-2's fingered teardrop).
  DROPS the sketch's little circles: blob droplets on their own bones -- hovering beside the joins while combined,
        strung out between the torn ends while split.
Combined (rest / bind pose) = the one long serpent: pieces plug into each other (goo into goo, no gap).

Pipeline:
  1. HEAD = the source mesh in its own sculpt frame mapped to game axes (rounded end -> -Y front, dorsal flames -> +Z).
  2. MID + TAIL + DROPLETS modelled as SDFs (magmoo_sdf.py), polygonised by marching tetrahedra.
  3. ONE FINISH for all: voxel remesh (VOXEL) -> collapse decimation to the same triangle density per area.
  4. assembly: the head pitched to follow the mid's front tangent, its socket on the mid's front end.
  5. regions (iso-contour cuts: colour edges are cut lines): heat graded across the goo -- goo_cool undersides /
     goo body / goo_hot mottled flow patches + ridges / flame tips / white-hot core at every torn end + droplets /
     eye rings with dark pupils. Surfaces stay smooth (no crust, no cracks, no displacement; eyes are glowing eyeball islands with dark pupil dots).
  6. rig: root -> three separable sub-chains parented straight to root (head.0-1 | body.0-4 | tail.0-10) + one bone
     per droplet. Each piece is weighted ONLY to its own chain.
  7. clips (procedural, discrete FK on the rest chain + a rigid carrier transform per piece):
       idle  the SPLIT/RECOMBINE cycle: combined serpent -> the mid pops up out of the crown and the head drifts off
             forward (damped-spring bounce), droplets stream into the gaps, pieces float + bob + the head looks
             around, then they flow back together with a squelch overshoot and settle into the one long serpent.
       walk  in-place lateral slither of the COMBINED serpent (head counter-yawed forward), a soft goo bob.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K        # noqa: E402  (read-only use)
import palettes as PAL    # noqa: E402  (read-only use)
import magmoo_sdf as SD   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; lengths in source units = the head sculpt's own units, natural scale)
UNIT = "magmoo"
TRI_BUDGET = [30000, 50000]           # declared hero-tier window (review-log: "30-50k hero tier stands")
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
VOXEL = 0.03                          # "surface finish": the ONE voxel remesh size every piece goes through
SDF_STEP = 0.03                       # SDF sampling step for the modelled pieces
TRI_DENSITY = 330.0                   # "mesh detail": triangles per square unit of surface, same for every piece
# ---- head = the artist's sculpt (head-local = the source game-framed: front -Y, floor 0, source units)
HEAD_SCALE = 1.0                      # "head size"
HEAD_SOCKET = (0.0, 0.25, 1.05)       # where the mid plugs into the back of the head (head-local)
HEAD_MIDPT = (0.0, -1.30, 0.85)       # head bone joint (head-local)
HEAD_NOSE_Z = 0.75                    # snout axis height (head-local)
HEAD_PITCH_EXTRA_DEG = -8.0           # "head droop": nose-down beyond following the mid's front tangent
EYE_Y = -2.0                          # "eye position" along the head (head-local y; the rounded front is at -3.2)
EYE_UP_DEG = 40.0                     # "eye spacing": each eye's direction, degrees from straight up toward its side
EYE_R, PUPIL_R = 0.19, 0.10           # "eye size" (glowing eyeball) / "pupil size" (dark dot)
EYE_SINK = 0.45                       # eyeball centre sits this x EYE_R under the skin (the rest bulges out)
EYE_LOOK = 0.7                        # pupils turn toward the snout by this much (0 = straight out of the skin)
# ---- mid piece (the curved bean), knots rear (plugged into the crown) -> front (plugged into the head)
MID_PTS = [(0, 0.0, 1.50), (0, -0.20, 2.55), (0, -0.85, 3.35), (0, -1.80, 3.75), (0, -2.75, 3.62), (0, -3.35, 3.30)]
MID_R = [0.50, 0.58, 0.65, 0.67, 0.61, 0.52]                  # "bean thickness" at those knots
MID_PLUG_HEAD = 0.35                  # the mid's front end stops this short of the head socket (its round cap reaches in)
MID_TUFTS = (3, 0.30, 0.34, 0.11)     # "torn-end tufts": per end (count, ring radius, length, root radius)
MID_DRIPS = [(0.42, 0.44, 0.14), (0.60, 0.32, 0.12), (0.78, 0.24, 0.10)]   # "drips": (arc fraction, hang length, bulb r)
# ---- tail piece: splash mound + crown + finger splats + the long tail (+ its tip splat)
MOUND_BASE = ((0.0, 0.10, 0.20), (1.35, 1.40, 0.95))          # "mound size": base ellipsoid (centre, radii)
MOUND_CONE_R = (1.08, 0.64)           # volcano cone radius, base -> crown
CROWN_Z = 1.78                        # "crown height"
CROWN_LICKS = 7                       # "crown points" count (the sketch's zigzag rim)
CROWN_LICK = (0.50, 0.13, 0.62)       # crown point (length, root radius, rim radius)
CRATER_R = 0.44                       # the bowl the mid plugs into
MOUND_FINGERS = [(58, 1.30, 0.33), (-58, 1.30, 0.33), (108, 1.50, 0.31), (-108, 1.50, 0.31),
                 (152, 1.05, 0.27), (-152, 1.05, 0.27)]        # "finger splats": (azimuth deg from +Y back, length, r)
FINGER_TIP_R = 0.14                   # finger ends stay round (drip bulbs), not pointed
TAIL_LEN = 6.6                        # "tail length" (spine arc length from inside the mound)
TAIL_START_Y = 0.55                   # tail spine start (inside the mound)
TAIL_R0, TAIL_RTIP = 0.56, 0.17       # "tail thickness" at the base / at the tip
TAIL_TAPER = 0.9                      # taper curve exponent (1 = linear)
TAIL_FLAT = 0.78                      # tail cross-section height / width (goo slumped on the floor)
TAIL_BEND_DEG = 18.0                  # "rest S-curve": lateral heading swing of the resting tail
TAIL_TIP_FINGERS = [(-34, 0.60), (0, 0.72), (34, 0.60)]       # "tail-tip splat": (heading deg off the tail, length)
# ---- droplets: (join, direction from the join point (walked out until clear), split fraction, split side, radius)
DROPLETS = [("crown", (1.0, -0.35, 0.55), 0.22, 0.10, 0.21), ("crown", (-1.0, 0.15, 0.40), 0.50, -0.14, 0.17),
            ("crown", (0.25, 1.0, 0.70), 0.78, 0.08, 0.13),
            ("head", (1.0, 0.45, -0.25), 0.33, 0.12, 0.16), ("head", (-1.0, 0.55, 0.05), 0.68, -0.10, 0.12)]
DROP_TIP = 1.4                        # "droplet point": teardrop tip length / droplet radius (tips point up)
DROP_CLEAR = 0.12                     # rest droplets hover this far off the goo surface
# ---- palette regions
TIP_SMOOTH_R = 0.42                   # tip field: smoothing reach of the reference copy (units)
RIDGE_T = 0.050                       # "hot ridge": outward displacement off the smoothed copy above this = goo_hot
TIP_T = 0.080                         # "hot tip size": ... above this = flame
UNDER_NZ = -0.45                      # "cool underside": smoothed normal z below this = goo_cool
MOTTLE_CELL = 1.60                    # "flow patch size" of the hot mottling
MOTTLE_T = 0.78                       # "flow patch amount" (field > this = goo_hot; higher = fewer patches)
HEAT_UP = 0.45                        # "hot tops": hot patches surface on upward-facing goo (field += this x normal z)
END_W = 0.75                          # "molten end size": zone radius round each torn (join) end
END_T, END_T2 = 0.30, 0.55            # torn end: flame ring from this facing x closeness, white-hot core inside this
# ---- rig
MID_BONES, TAIL_BONES = 5, 10         # tail bones along the long tail (+ tail.0 = the mound)
# ---- clips
IDLE_FRAMES = 144                     # split/recombine cycle (24 fps -> 6 s)
SPLIT_GAP_MID = 2.40                  # "split height": the mid lifts this far out of the crown (units)
SPLIT_GAP_HEAD = 2.30                 # "split distance": the head drifts this far off the mid's front end (units)
SPLIT_TAIL_DRIFT = 0.30               # the bottom piece slides back this much
SPLIT_TIMING = {"crown": (0.10, 0.60), "head": (0.14, 0.55)}   # (split start, merge start) as cycle fractions
SPLIT_SPRING = (1.0, 0.55)            # "bounce": spring frequency (Hz), damping ratio (0.55 -> 13 % overshoot)
IDLE_HEAD_TILT_DEG = 10.0             # head tilts nose-up while floating free
IDLE_HEAD_LOOK_DEG = 12.0             # ... and looks around
IDLE_BOB = 0.10                       # floating bob while split
IDLE_TAIL_DEG = 14.0                  # "lazy tail tip": heading wave amplitude at the tail tip
IDLE_GLOW_PULSE = 0.40                # torn ends flare: emission strength x (1 + this) while split
WALK_FRAMES = 48                      # slither cycle (24 fps -> 2 s)
WALK_WAVELENGTH = 7.0                 # "wave length" of the lateral undulation (units of arc)
WALK_AMP_BODY_DEG = 10.0              # heading swing through the mid + mound
WALK_AMP_TAIL_DEG = 38.0              # heading swing at the tail tip ("tail whipping wider")
WALK_AMP_HEAD_DEG = 5.0               # heading swing at the head end of the mid
WALK_HEAD_FOLLOW = 0.25               # head heading follows this fraction of the mid (rest = locked forward)
WALK_BOB = 0.08                       # "goo bob": the arch + head rise and settle twice per cycle
WALK_GLOW_PULSE = 0.18

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STOP = argv[argv.index("--stop") + 1] if "--stop" in argv else None
OUTROOT = argv[argv.index("--outroot") + 1] if "--outroot" in argv else ROOT
SKINS = argv[argv.index("--skins") + 1].split(",") if "--skins" in argv else ["default"]
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(OUTROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(OUTROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(OUTROOT, "rigged", UNIT + ".glb")
for d_ in (os.path.dirname(OUT_IMPROVED), os.path.dirname(OUT_RIGGED)):
    os.makedirs(d_, exist_ok=True)
PIECES = ["head", "mid", "tail"]
report = {"unit": UNIT, "version": "v2 goo", "source": bpy.data.filepath, "tier": "hero", "tri_budget": TRI_BUDGET,
          "yaw_fix_deg": 0.0, "overrides": OVERRIDES, "pieces": {}}
scene = bpy.context.scene
TAU = 2 * math.pi
UP = np.array([0.0, 0.0, 1.0])


def sha(*arrs):
    h = hashlib.sha256()
    for a in arrs:
        h.update(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def unit_rows(A):
    A = np.asarray(A, float)
    return A / np.maximum(np.linalg.norm(A, axis=1), 1e-12)[:, None]


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V, float).tolist(), [], [list(map(int, f)) for f in F])
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


def islands(V, F):
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
    return np.array([find(i) for i in range(n)])


def keep_main_island(V, F, min_frac):
    roots = islands(V, F)
    lab, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_lab = cnt >= min_frac * len(V)
    keep_v = keep_lab[inv]
    remap = -np.ones(len(V), dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum())}


def area_of(V, F):
    V = np.asarray(V)
    a = 0.0
    for n in sorted({len(f) for f in F}):
        T = np.asarray([f for f in F if len(f) == n])
        for j in range(1, n - 1):
            a += 0.5 * np.linalg.norm(np.cross(V[T[:, j]] - V[T[:, 0]], V[T[:, j + 1]] - V[T[:, 0]]), axis=1).sum()
    return float(a)


def finish(name, V, F, voxel=None, density=None, min_frac=0.02):
    """THE shared finish: voxel remesh -> collapse decimation to density x area -> main shell."""
    voxel = voxel or VOXEL
    density = density or TRI_DENSITY
    t = time.time()
    tmp = new_obj(name + "_rm", V, F)
    rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = voxel; rm.use_smooth_shade = False
    RV, RF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    RV, RF, specks = keep_main_island(RV, RF, min_frac)
    area = area_of(RV, RF)
    target = int(round(area * density))
    rtris = sum(len(f) - 2 for f in RF)
    tmp = new_obj(name + "_dec", RV, RF)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / rtris)
    LV, LF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF, specks2 = keep_main_island(LV, LF, min_frac)
    return np.asarray(LV), [list(f) for f in LF], {"voxel": voxel, "remesh_tris": rtris, "area": round(area, 4),
                                                   "target_tris": target, "tris": sum(len(f) - 2 for f in LF),
                                                   "specks": [specks, specks2], "seconds": round(time.time() - t, 1)}


def mt_audit(V, T, G_):
    e = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    _, c = np.unique(e, axis=0, return_counts=True)
    out = {"mt_verts": len(V), "mt_tris": len(T), "open_edges": int((c == 1).sum()), "nonmanifold_edges": int((c > 2).sum()),
           "grid": G_.n.tolist(), "nan": int(np.isnan(G_.F).sum())}
    if out["open_edges"]:
        u_, c_ = np.unique(e, axis=0, return_counts=True)
        ov = V[np.unique(u_[c_ == 1])]
        out["open_bbox"] = [ov.min(0).round(3).tolist(), ov.max(0).round(3).tolist()]
        out["grid_box"] = [G_.lo.round(3).tolist(), (G_.lo + G_.step * (G_.n - 1)).round(3).tolist()]
    assert out["open_edges"] == 0 and out["nan"] == 0, out
    return out


def lick_cones(root, ctrl, tip, r0, n=10, rtip=0.014, sharp=1.5):
    pts = SD.bezier(root, ctrl, tip, n)
    u = np.linspace(0.0, 1.0, n)
    rr = rtip + (r0 - rtip) * (1 - u) ** sharp
    return [(pts[i], pts[i + 1], rr[i], rr[i + 1]) for i in range(n - 1)]


def add_cones(G, cones, k, fmap=None):
    for a, b, r1, r2 in cones:
        lo_ = np.minimum(a, b) - max(r1, r2); hi_ = np.maximum(a, b) + max(r1, r2)
        if fmap is None:
            G.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), lo_, hi_, k)
        else:
            G.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(fmap(P), a, b, r1, r2), fmap(lo_[None], inv=True)[0],
                    fmap(hi_[None], inv=True)[0], k)


def bvh_of(V, F):
    return BVHTree.FromPolygons(np.asarray(V).tolist(), F)


def ray_hit(bvh, origin, direction):
    h = bvh.ray_cast(Vector(origin), Vector(direction))[0]
    return None if h is None else np.array(h)


# =========================================================================== 1. HEAD = the artist's sculpt
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
src = bpy.data.objects["Icosphere"]
SV_local, SF = mesh_arrays(src.data)
report["source_mesh"] = {"object": src.name, "verts": len(SV_local), "tris": tri_count(src.data),
                         "object_rotation_deg": [round(math.degrees(a), 3) for a in src.rotation_euler],
                         "materials": [s.material.name if s.material else None for s in src.material_slots],
                         "role_v2": "the HEAD (the sketch's rounded teardrop with flame licks trailing off the back); "
                                    "v1 used it as the main body",
                         "colour_note": "no material on the mesh: goo colours are authored in palettes/magmoo"}
# sculpt frame -> game frame: local (x, y, z) -> (-x, -z, -y): rounded end (+Z) to the front (-Y), dorsal flames up
MAP = np.array([[-1.0, 0, 0], [0, 0, -1.0], [0, -1.0, 0]])
assert abs(np.linalg.det(MAP) - 1.0) < 1e-9
HV0 = SV_local @ MAP.T
lo, hi = HV0.min(0), HV0.max(0)
HV0 = (HV0 - np.array([(lo[0] + hi[0]) / 2, 0.0, lo[2]])) * HEAD_SCALE
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
HEAD_SRC = HV0.copy()
hV, hF, hfin = finish("head", HV0, SF)
report["pieces"]["head"] = {"finish": hfin, "source_tris": report["source_mesh"]["tris"],
                            "head_local_bbox": [hV.min(0).round(4).tolist(), hV.max(0).round(4).tolist()]}
hs = HEAD_SCALE
H_SOCKET = np.array(HEAD_SOCKET) * hs
H_MID = np.array(HEAD_MIDPT) * hs
hb = bvh_of(hV, hF)
H_SNOUT = ray_hit(hb, (0.0, -50.0, HEAD_NOSE_Z * hs), (0, 1.0, 0))
assert H_SNOUT is not None
H_EYES, H_EYE_N = [], []
for sgn in (1.0, -1.0):
    a = math.radians(EYE_UP_DEG)
    dvec = np.array([sgn * math.sin(a), 0.0, math.cos(a)])
    core = np.array([0.0, EYE_Y * hs, HEAD_NOSE_Z * hs])
    p = ray_hit(hb, core + dvec * 20.0, -dvec)
    assert p is not None, "eye ray missed the head"
    H_EYES.append(p); H_EYE_N.append(dvec)
print("HEAD", json.dumps({**hfin, "snout": H_SNOUT.round(4).tolist(), "eyes": [e.round(4).tolist() for e in H_EYES]}))
# eyeballs: their own small islands in the head mesh (fine voxel -> crisp round pupils), glowing ball + dark pupil dot
EYEBALLS = []
for p, dvec in zip(H_EYES, H_EYE_N):
    c = p - dvec * EYE_R * EYE_SINK
    Ge = SD.Grid(c - EYE_R * 1.4, c + EYE_R * 1.4, 0.008, band=0.03)
    Ge.apply(lambda P, c=c: SD.sd_sphere(P, c, EYE_R), c - EYE_R, c + EYE_R, 0.0)
    eV0, eT0 = SD.polygonise(Ge)
    eV, eF, efin = finish("eye", eV0, eT0, voxel=0.008, density=TRI_DENSITY * 3.5)
    look = unit(dvec + EYE_LOOK * np.array([0.0, -1.0, 0.0]))
    EYEBALLS.append({"V": eV, "F": eF, "c": c, "look": look, "finish": efin})
report["pieces"]["head"]["eyeballs"] = [e["finish"] for e in EYEBALLS]

# =========================================================================== 2a. MID piece (SDF bean arch)
t_mid = time.time()
MP = np.array(MID_PTS, float)
mid_poly = SD.resample(SD.catmull(MP, 12), 48)
mid_s = SD.arclen(mid_poly)
mid_r = np.interp(mid_s / mid_s[-1], SD.arclen(MP) / SD.arclen(MP)[-1], MID_R)
mtan = np.gradient(mid_poly, axis=0); mtan /= np.linalg.norm(mtan, axis=1)[:, None]
U2 = unit(mid_poly[1] - mid_poly[0])                 # crown join axis: from the mound up into the mid
U1 = unit(mid_poly[-1] - mid_poly[-2])               # head join axis: from the mid forward into the head
lo_m = mid_poly.min(0) - 1.3; hi_m = mid_poly.max(0) + 1.3
G = SD.Grid(lo_m, hi_m, SDF_STEP, band=0.09)
add_cones(G, [(mid_poly[i], mid_poly[i + 1], mid_r[i], mid_r[i + 1]) for i in range(len(mid_poly) - 1)], 0.04)
# torn-end tufts: a ring of short flame points round each end cap, pointing outward along the axis
n_t, ring_r, t_len, t_r0 = MID_TUFTS
MID_TUFT_ROOTS = []
for end_p, axis_, r_end in ((mid_poly[0], -U2, mid_r[0]), (mid_poly[-1], U1, mid_r[-1])):
    e1 = unit(np.cross(axis_, [1.0, 0, 0]) if abs(axis_[0]) < 0.9 else np.cross(axis_, UP)); e2 = np.cross(axis_, e1)
    for j in range(n_t):
        ang = TAU * (j + 0.25) / n_t
        radial = math.cos(ang) * e1 + math.sin(ang) * e2
        root = end_p + axis_ * (0.55 * r_end) + radial * ring_r * r_end / 0.55 * 0.9
        tip = root + axis_ * t_len + radial * 0.18 * t_len
        add_cones(G, lick_cones(root, (root + tip) / 2 + radial * 0.06, tip, t_r0, n=8, rtip=0.02), 0.06)
        MID_TUFT_ROOTS.append(root)
# drips hanging off the underside of the arch (teardrops: thin neck, round bulb)
MID_DRIP_TIPS = []
for frac, hang, bulb in MID_DRIPS:
    i = int(round(frac * (len(mid_poly) - 1)))
    top = mid_poly[i] - UP * (mid_r[i] * 0.55)
    bot = mid_poly[i] - UP * (mid_r[i] + hang)
    G.apply(lambda P, a=top, b=bot, r2=bulb: SD.sd_round_cone(P, a, b, 0.12, r2), np.minimum(top, bot) - 0.3,
            np.maximum(top, bot) + 0.3, 0.14)
    MID_DRIP_TIPS.append(bot - UP * bulb)
mV0, mT0 = SD.polygonise(G)
mt_mid = mt_audit(mV0, mT0, G)
mV, mF, mfin = finish("mid", mV0, mT0)
report["pieces"]["mid"] = {"finish": mfin, "sdf": {"step": SDF_STEP, **mt_mid, "seconds": round(time.time() - t_mid, 1)},
                           "spine_arc_length": round(float(mid_s[-1]), 4)}
print("MID", json.dumps(mfin))

# =========================================================================== 2b. TAIL piece (SDF splash mound + tail)
t_tl = time.time()
C_JOIN = mid_poly[0].copy()                             # the crown join (the mid's rear end centre, in the crater)


def tail_spine(n=200):
    s = np.linspace(0.0, TAIL_LEN, n)
    ds = s[1] - s[0]
    psi = np.radians(TAIL_BEND_DEG) * np.sin(TAU * s / TAIL_LEN * 0.8 + 0.3) * smoothstep(0.0, 1.5, s)
    d = np.stack([np.sin(psi), np.cos(psi), np.zeros_like(psi)], 1)
    P = np.vstack([[0, 0, 0], np.cumsum(d[:-1] * ds, 0)]) + np.array([C_JOIN[0], TAIL_START_Y, 0.0])
    r = TAIL_RTIP + (TAIL_R0 - TAIL_RTIP) * (1 - s / TAIL_LEN) ** TAIL_TAPER
    return s, P, d, r


ts, tP, tD, tr_ = tail_spine()


def unflat(P, inv=False):
    Q = np.array(P, float, copy=True)
    Q[:, 2] = Q[:, 2] * TAIL_FLAT if inv else Q[:, 2] / TAIL_FLAT
    return Q


spine_u = tP.copy(); spine_u[:, 2] = tr_                # unflattened space: every ring's bottom on the floor
TAIL_SPINE = tP.copy(); TAIL_SPINE[:, 2] = tr_ * TAIL_FLAT
tip_end = TAIL_SPINE[-1]; tip_dir = tD[-1]
reach = max(l for _, l in TAIL_TIP_FINGERS) + 1.0
lo_t = np.minimum(TAIL_SPINE.min(0), [MOUND_BASE[0][0] - 3.2, MOUND_BASE[0][1] - 3.2, 0]) - np.array([reach, 0.5, 0.05])
hi_t = np.maximum(TAIL_SPINE.max(0), [MOUND_BASE[0][0] + 3.2, MOUND_BASE[0][1] + 3.2, CROWN_Z + 0.8]) + np.array([reach, reach, 0.2])
Gt = SD.Grid(lo_t, hi_t, SDF_STEP, band=0.16)
mc_, mr_ = np.array(MOUND_BASE[0]), np.array(MOUND_BASE[1])
Gt.apply(lambda P: SD.sd_ellipsoid(P, mc_, mr_), mc_ - mr_, mc_ + mr_, 0.0)
crown_c = np.array([C_JOIN[0], C_JOIN[1], CROWN_Z])
cone_a = np.array([mc_[0], mc_[1], 0.55])
Gt.apply(lambda P: SD.sd_round_cone(P, cone_a, crown_c, MOUND_CONE_R[0], MOUND_CONE_R[1]), cone_a - 1.2, crown_c + 1.2, 0.36)
# the long tail (+ its tip splat) in the vertically un-flattened space
idx = np.linspace(0, len(ts) - 1, 56).round().astype(int)
add_cones(Gt, [(spine_u[idx[i]], spine_u[idx[i + 1]], tr_[idx[i]], tr_[idx[i + 1]]) for i in range(len(idx) - 1)], 0.05,
          fmap=unflat)
for hd, ln in TAIL_TIP_FINGERS:
    a = math.radians(hd)
    dd = np.array([tip_dir[0] * math.cos(a) - tip_dir[1] * math.sin(a), tip_dir[0] * math.sin(a) + tip_dir[1] * math.cos(a), 0])
    root = spine_u[-1] - tip_dir * 0.1
    tip = root + dd * ln; tip[2] = FINGER_TIP_R * 0.75
    add_cones(Gt, lick_cones(root, (root + tip) / 2, tip, TAIL_RTIP * 0.95, n=7, rtip=FINGER_TIP_R * 0.75, sharp=1.0),
              0.08, fmap=unflat)
# finger splats radiating from the mound base (half-buried round cones, cut flat by the floor)
FINGER_TIPS = []
for az, ln, r0 in MOUND_FINGERS:
    a = math.radians(az)
    dd = np.array([math.sin(a), math.cos(a), 0.0])
    root = mc_ + dd * 0.75 + UP * 0.25
    tip = mc_ + dd * (0.75 + ln) + UP * 0.02
    ctrl = (root + tip) / 2 + UP * 0.02
    add_cones(Gt, lick_cones(root, ctrl, tip, r0, n=8, rtip=FINGER_TIP_R, sharp=1.0), 0.18)
    FINGER_TIPS.append(tip)
# crown: a jagged ring of dripping flame points round the crater rim (the sketch's zigzag top)
CROWN_TIPS = []
lick_len, lick_r, rim_r = CROWN_LICK
for j in range(CROWN_LICKS):
    ang = TAU * (j + 0.5) / CROWN_LICKS
    radial = np.array([math.sin(ang), math.cos(ang), 0.0])
    ln = lick_len * (1.0 + 0.22 * math.sin(3.7 * j + 0.9))
    root = crown_c + radial * rim_r - UP * 0.10
    tip = root + UP * ln + radial * 0.28 * ln
    add_cones(Gt, lick_cones(root, root + UP * 0.6 * ln + radial * 0.05, tip, lick_r, n=8, rtip=0.02), 0.07)
    CROWN_TIPS.append(tip)
# the crater the mid plugs into
crater_c = crown_c + UP * (CRATER_R * 0.55)
Gt.apply(lambda P: SD.sd_sphere(P, crater_c, CRATER_R), crater_c - CRATER_R, crater_c + CRATER_R, 0.08, mode="subtract")
Gt.floor(0.0)
tV0, tT0 = SD.polygonise(Gt)
mt_tail = mt_audit(tV0, tT0, Gt)
lV, lF, lfin = finish("tail", tV0, tT0)
lV[:, 2] = np.maximum(lV[:, 2], 0.0)                   # voxel rounding under the flat floor cut -> back onto the floor
report["pieces"]["tail"] = {"finish": lfin, "sdf": {"step": SDF_STEP, **mt_tail, "seconds": round(time.time() - t_tl, 1)},
                            "tail_spine_length": TAIL_LEN}
print("TAIL", json.dumps(lfin))

# =========================================================================== 3. assembly: head on the mid's front end
MF = mid_poly[-1].copy()                                 # the mid's front end centre
A1 = MF + U1 * MID_PLUG_HEAD                             # the head socket lands here
ax_local = unit(np.array([0.0, H_SNOUT[1], HEAD_NOSE_Z * hs]) - H_SOCKET)     # head axis socket -> snout (YZ plane)
cur = math.atan2(-ax_local[2], -ax_local[1])            # current nose-down angle
tgt = math.atan2(-U1[2], -U1[1]) + math.radians(HEAD_PITCH_EXTRA_DEG)
HEAD_PITCH = tgt - cur
Rh = np.array(Matrix.Rotation(HEAD_PITCH, 3, "X"))


def head_xf(P):
    return (np.asarray(P) - H_SOCKET) @ Rh.T + A1


hV = head_xf(hV)
HEAD_SRC = head_xf(HEAD_SRC)
SNOUT = head_xf(H_SNOUT); HMID = head_xf(H_MID)
EYES = [head_xf(e) for e in H_EYES]; EYE_N = [n_ @ Rh.T for n_ in H_EYE_N]
for eb_ in EYEBALLS:
    eb_["V"] = head_xf(eb_["V"]); eb_["c"] = head_xf(eb_["c"]); eb_["look"] = eb_["look"] @ Rh.T
report["assembly"] = {"head_pitch_deg": round(math.degrees(HEAD_PITCH), 3),
                      "head_axis_nose_down_deg": round(math.degrees(tgt), 3),
                      "mid_front_tangent": U1.round(4).tolist(), "mid_rear_tangent": U2.round(4).tolist()}

# ---- droplets (SDF teardrops, tips up), rest positions walked out along their direction until clear of the goo
PIECE_V = {"head": hV, "mid": mV, "tail": lV}
PIECE_F = {"head": hF, "mid": mF, "tail": lF}
BVH = {k: bvh_of(PIECE_V[k], PIECE_F[k]) for k in PIECES}
JOIN_PT = {"crown": C_JOIN, "head": A1}


def clearance(p):
    return min(BVH[k].find_nearest(Vector(p))[3] for k in PIECES)


DROP_REST, DROP_V, DROP_F, DROP_VID, drop_rep = [], [], [], [], []
for i, (jn, dvec, frac, side, r) in enumerate(DROPLETS):
    d_ = unit(dvec)
    p = JOIN_PT[jn] + d_ * 0.1
    steps = 0
    while clearance(p) < r * 1.1 + DROP_CLEAR and steps < 400:
        p = p + d_ * 0.02; steps += 1
    DROP_REST.append(p)
    Gd = SD.Grid(p - r * 1.6, p + r * (1.6 + DROP_TIP), 0.012, band=0.04)
    Gd.apply(lambda P, c=p, r=r: SD.sd_sphere(P, c, r), p - r, p + r, 0.0)
    tipp = p + UP * r * DROP_TIP
    Gd.apply(lambda P, a=p + UP * r * 0.2, b=tipp, r1=r * 0.72: SD.sd_round_cone(P, a, b, r1, r * 0.08), p - r, tipp + r, r * 0.35)
    dV0, dT0 = SD.polygonise(Gd)
    dV, dF_, dfin = finish("drop%d" % i, dV0, dT0, voxel=0.012)
    nb = sum(len(v) for v in DROP_V)
    DROP_V.append(dV); DROP_F += [[j + nb for j in f] for f in dF_]; DROP_VID.append(np.full(len(dV), i))
    drop_rep.append({"join": jn, "rest": p.round(4).tolist(), "radius": r, "walk_out_steps": steps,
                     "clearance": round(clearance(p), 4), "tris": dfin["tris"]})
DROP_V = np.vstack(DROP_V); DROP_VID = np.concatenate(DROP_VID)
report["droplets"] = drop_rep

# =========================================================================== final centring (contract feet_origin)
ALLV = np.vstack([hV, mV, lV, DROP_V])
lo_a, hi_a = ALLV.min(0), ALLV.max(0)
CENTER = np.array([(lo_a[0] + hi_a[0]) / 2, (lo_a[1] + hi_a[1]) / 2, lo_a[2]])
hV = hV - CENTER; mV = mV - CENTER; lV = lV - CENTER; DROP_V = DROP_V - CENTER; HEAD_SRC = HEAD_SRC - CENTER
SNOUT = SNOUT - CENTER; HMID = HMID - CENTER; EYES = [e - CENTER for e in EYES]
for eb_ in EYEBALLS:
    eb_["V"] = eb_["V"] - CENTER; eb_["c"] = eb_["c"] - CENTER
A1 = A1 - CENTER; MF = MF - CENTER; C_JOIN = C_JOIN - CENTER; mid_poly = mid_poly - CENTER
TAIL_SPINE = TAIL_SPINE - CENTER; DROP_REST = [p - CENTER for p in DROP_REST]
JOIN_PT = {"crown": C_JOIN, "head": A1}
CROWN_TOP = crown_c - CENTER + UP * CROWN_LICK[0]         # droplets string from here (above the crown points) when split
PIECE_V = {"head": hV, "mid": mV, "tail": lV}
BVH = {k: bvh_of(PIECE_V[k], PIECE_F[k]) for k in PIECES}
report["assembly"]["centre_shift"] = CENTER.round(5).tolist()
# torn-end anchors (surface points on each side of each join, along the join axis)
END_ANCHOR = {
    ("crown", "tail"): ray_hit(BVH["tail"], C_JOIN + U2 * 20.0, -U2),      # the crater floor
    ("crown", "mid"): ray_hit(BVH["mid"], C_JOIN - U2 * 20.0, U2),         # the mid's rear cap bottom
    ("head", "mid"): ray_hit(BVH["mid"], MF + U1 * 20.0, -U1),             # the mid's front cap tip
    ("head", "head"): ray_hit(BVH["head"], A1 - U1 * 20.0, U1)}            # the back of the head on the join axis
assert all(v is not None for v in END_ANCHOR.values()), END_ANCHOR
END_DIR = {("crown", "tail"): U2, ("crown", "mid"): -U2, ("head", "mid"): U1, ("head", "head"): -U1}
report["assembly"]["torn_end_anchors"] = {"%s|%s" % k: v.round(4).tolist() for k, v in END_ANCHOR.items()}

# =========================================================================== 5. regions (iso-contour cuts per piece)
REG = ["goo_cool", "goo", "goo_hot", "flame", "core", "eye", "pupil"]
R_ = {n: i for i, n in enumerate(REG)}
CUT_SNAP = 0.15


def vertex_normals(V, F):
    V = np.asarray(V); T = np.asarray([f for f in F if len(f) == 3])
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, T[:, k], n)
    return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


def edges_of(F):
    E = set()
    for f in F:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            E.add((min(a, b), max(a, b)))
    return np.array(sorted(E))


def smooth_copy(V, E, iters, lam=0.5):
    n = len(V)
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    X = np.array(V, float, copy=True)
    one = X.ndim == 1
    if one:
        X = X[:, None]
    for _ in range(iters):
        S = np.zeros_like(X)
        for k in range(X.shape[1]):
            S[:, k] = np.bincount(E[:, 0], X[E[:, 1], k], minlength=n) + np.bincount(E[:, 1], X[E[:, 0], k], minlength=n)
        X = X + lam * (S / np.maximum(deg, 1)[:, None] - X)
    return X[:, 0] if one else X


def mottle(V):
    """deterministic smooth 3D 'lava flow' field in ~[-1, 1]: incommensurate sinusoids, domain-warped."""
    k = TAU / MOTTLE_CELL
    W = V + 0.22 * MOTTLE_CELL * np.stack([np.sin(k * 0.61 * V[:, 1] + 0.7), np.sin(k * 0.57 * V[:, 2] + 1.9),
                                           np.sin(k * 0.53 * V[:, 0] + 2.6)], 1)
    f = (np.sin(k * (0.83 * W[:, 1] + 0.31 * W[:, 2]) + 1.1) + np.sin(k * (0.67 * W[:, 2] - 0.45 * W[:, 0]) + 2.3) +
         np.sin(k * (0.59 * W[:, 0] + 0.71 * W[:, 1] - 0.37 * W[:, 2]) + 0.4)) / 3.0
    return f


PDATA = {}

for pc in PIECES:
    t = time.time()
    V = PIECE_V[pc]; F = PIECE_F[pc]
    E = edges_of(F)
    N = vertex_normals(V, F)
    mean_edge = float(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1).mean())
    tip_iters = int(round((TIP_SMOOTH_R / mean_edge) ** 2))
    Vs = smooth_copy(V, E, tip_iters)
    tipf = np.einsum("ij,ij->i", V - Vs, N)
    nz = smooth_copy(N[:, 2], E, 12)
    mot = smooth_copy(mottle(V), E, 4) + HEAT_UP * nz
    endf = np.full(len(V), -1.0)
    for (jn, side), anchor in END_ANCHOR.items():
        if side != pc:
            continue
        fac = N @ END_DIR[(jn, side)]
        clo = 1.0 - np.linalg.norm(V - anchor, axis=1) / END_W
        endf = np.maximum(endf, np.minimum(fac, clo))
    endf = smooth_copy(endf, E, 3)
    PDATA[pc] = {"V": V, "F": F, "fields": {"tip": tipf, "nz": nz, "mot": mot, "end": endf},
                 "tip_iters": tip_iters, "mean_edge": round(mean_edge, 4)}
    print("FIELDS", pc, json.dumps({"tip_p50_p99_max": [round(float(np.percentile(tipf, q)), 4) for q in (50, 99)] +
                                    [round(float(tipf.max()), 4)], "tip_iters": tip_iters, "sec": round(time.time() - t, 1)}))


def cut_mesh(V, F, FLD, cuts):
    """iso-contour cuts: every colour edge becomes a mesh edge (no per-face sawtooth). -> V2, F2, vertex fields, log"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {k: bm.verts.layers.float.new(k) for k in FLD}
    for v in bm.verts:
        for k, arr in FLD.items():
            v[LAY[k]] = float(arr[v.index])

    def iso_cut(key, tau):
        L = LAY[key]
        eps = 1e-6 * max(1.0, abs(tau))
        side = lambda v: 0 if abs(v[L] - tau) <= eps else (1 if v[L] > tau else -1)
        for e in bm.edges:
            a, b = e.verts
            if side(a) * side(b) < 0:
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0]
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
            if 1 in sides and -1 in sides:
                cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
                if len(cv) == 2:
                    pairs.append(cv)
        for cv in pairs:
            bmesh.ops.connect_verts(bm, verts=cv)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        return {"field": key, "tau": tau, "edge_splits": len(cuts), "face_connects": len(pairs)}
    log = [iso_cut(k, tau) for k, tau in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    V2 = np.array([v.co[:] for v in bm.verts])
    F2 = [[v.index for v in f.verts] for f in bm.faces]
    VF = {k: np.array([v[LAY[k]] for v in bm.verts]) for k in LAY}
    bm.free()
    straddle = {}
    for k, tau in cuts:
        vals = [VF[k][f] for f in F2]
        straddle["%s=%.3f" % (k, tau)] = int(sum(1 for v in vals if v.min() < tau - 1e-5 and v.max() > tau + 1e-5))
    return V2, F2, VF, {"cuts": log, "straddling_faces_after_cut": straddle}


COS_PUPIL = math.sqrt(1.0 - (PUPIL_R / EYE_R) ** 2)


def cut_piece(pc):
    D = PDATA[pc]
    cuts = [("nz", UNDER_NZ), ("mot", MOTTLE_T), ("tip", RIDGE_T), ("tip", TIP_T), ("end", END_T), ("end", END_T2)]
    V2, F2, VF, rep_ = cut_mesh(D["V"], D["F"], D["fields"], cuts)
    FV = {k: np.array([np.mean(VF[k][f]) for f in F2]) for k in VF}
    rid = np.full(len(F2), R_["goo"], dtype=np.int32)
    rid[FV["nz"] < UNDER_NZ] = R_["goo_cool"]
    rid[FV["mot"] > MOTTLE_T] = R_["goo_hot"]
    rid[FV["tip"] > RIDGE_T] = R_["goo_hot"]
    rid[FV["tip"] > TIP_T] = R_["flame"]
    rid[FV["end"] > END_T] = R_["flame"]
    rid[FV["end"] > END_T2] = R_["core"]
    if pc == "head":                                   # + the eyeball islands: glowing ball, dark pupil dot
        eye_rep = []
        for eb_ in EYEBALLS:
            pd = unit_rows(eb_["V"] - eb_["c"]) @ eb_["look"]
            EV2, EF2, EVF, erep = cut_mesh(eb_["V"], eb_["F"], {"pd": pd}, [("pd", COS_PUPIL)])
            erid = np.where(np.array([np.mean(EVF["pd"][f]) for f in EF2]) > COS_PUPIL, R_["pupil"], R_["eye"]).astype(np.int32)
            nb = len(V2)
            V2 = np.vstack([V2, EV2]); F2 = F2 + [[i + nb for i in f] for f in EF2]; rid = np.concatenate([rid, erid])
            eye_rep.append({"tris": len(EF2), "pupil_faces": int((erid == R_["pupil"]).sum()), **erep})
        rep_["eyeballs"] = eye_rep
    return V2, F2, rid, rep_


t_cut = time.time()
PIECEOBJ = {}
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
pal_default = PAL.load(UNIT, "default")
PAL.apply_material(mat, pal_default)


def glow_tiers(pal):
    """emission_scale ranks = the heat grade painted into the Glow colour set (glTF COLOR_1). Gate: the eye is the top
    tier and the goo grades cool < body < hot < flame < core (the torn ends + droplets are the hottest goo)."""
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    rank = sorted(tier, key=lambda n: -tier[n])
    grade = ["goo_cool", "goo", "goo_hot", "flame", "core", "eye"]
    ok = bool(rank) and rank[0] == "eye" and all(tier.get(a, -1) < tier.get(b, -1) for a, b in zip(grade, grade[1:]))
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "rank": rank, "grade": grade, "pass": ok}


report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["pass"], report["glow_tiers"]["default"]


def finish_object(name, V, F, rid, shade=None):
    ob = new_obj(name, V, F)
    me = ob.data
    if shade is None:
        FC = np.array([np.mean(V[f], 0) for f in F])
        jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
        shade = 0.95 + 0.10 * jit
    PAL.store_regions(me, REG, rid, shade)
    counts = PAL.paint(me, pal_default)
    me.materials.append(mat)
    me.shade_flat()
    return ob, counts


for pc in PIECES:
    V2, F2, rid, cutrep = cut_piece(pc)
    ob, counts = finish_object(UNIT + "_" + pc, V2, F2, rid)
    PIECEOBJ[pc] = ob
    PDATA[pc]["V2"] = V2; PDATA[pc]["F2"] = F2; PDATA[pc]["rid"] = rid
    report["pieces"][pc].update({"regions_faces": counts, "iso_cuts": cutrep, "tip_field_passes": PDATA[pc]["tip_iters"],
                                 "mean_edge": PDATA[pc]["mean_edge"], "tris_final": tri_count(ob.data),
                                 "min_z": round(float(V2[:, 2].min()), 4),
                                 "bbox": [V2.min(0).round(4).tolist(), V2.max(0).round(4).tolist()]})
drop_ob, drop_counts = finish_object(UNIT + "_droplets", DROP_V, DROP_F, np.full(len(DROP_F), R_["core"], dtype=np.int32),
                                     shade=np.ones(len(DROP_F)))
report["droplets_object"] = {"tris": tri_count(drop_ob.data), "count": len(DROPLETS), "region": "core"}
report["cut_seconds"] = round(time.time() - t_cut, 1)

# =========================================================================== facing landmark + props, UVs
anchor = (EYES[0] + EYES[1]) / 2
landmark = SNOUT
dvec = landmark - anchor
report["facing"] = {"rule": "midpoint of the two eyes -> the snout tip (the head's rounded front on its axis)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
ALL_OBS = [PIECEOBJ[p] for p in PIECES] + [drop_ob]
for ob in ALL_OBS:
    ob["conquest_unit"] = UNIT
    ob["conquest_tier"] = "hero"
    ob["conquest_tri_budget"] = TRI_BUDGET
    ob["conquest_max_height"] = CELL_MAX_H
    ob["conquest_max_footprint"] = CELL_MAX_FP
    ob["conquest_yaw_fix_deg"] = 0.0
    ob["conquest_front_anchor"] = anchor.tolist()
    ob["conquest_front_landmark"] = landmark.tolist()
    ob["conquest_facing_rule"] = report["facing"]["rule"]
    ob["conquest_source"] = "newunit-magmoo.blend (the head) + fresh SDF mid/tail/droplets (v2 goo)"
    ob["conquest_scale_policy"] = "natural proportions, source units; game scales at import (cell fit report-only)"
    ob["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
    ob["conquest_segment"] = ob.name[len(UNIT) + 1:]
t_uv = time.time()
for ob in ALL_OBS:
    bpy.context.view_layer.objects.active = ob
    for o in scene.objects:
        o.select_set(o is ob)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")
report["uv_seconds"] = round(time.time() - t_uv, 1)


def min_gap(VA, FA, VB, FB):
    bA = BVHTree.FromPolygons(np.asarray(VA).tolist(), FA); bB = BVHTree.FromPolygons(np.asarray(VB).tolist(), FB)
    dA = min(bB.find_nearest(Vector(p))[3] for p in VA)
    dB = min(bA.find_nearest(Vector(p))[3] for p in VB)
    return float(min(dA, dB))


ALLV = np.vstack([np.array([v.co[:] for v in o.data.vertices]) for o in ALL_OBS])
lo_a, hi_a = ALLV.min(0), ALLV.max(0)
report["measure"] = {
    "bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()],
    "length_y": round(float(hi_a[1] - lo_a[1]), 4), "width_x": round(float(hi_a[0] - lo_a[0]), 4),
    "height_z": round(float(hi_a[2] - lo_a[2]), 4),
    "snout_z": round(float(SNOUT[2]), 4), "eye_z": round(float(anchor[2]), 4),
    "head_lowest_z": round(float(PDATA["head"]["V2"][:, 2].min()), 4),
    "mid_arch_top_z": round(float(PDATA["mid"]["V2"][:, 2].max()), 4),
    "mid_spine_length": round(float(mid_s[-1]), 4), "tail_spine_length": TAIL_LEN,
    "units": "source units (the head sculpt's own scale, natural proportions)"}
fp = max(report["measure"]["length_y"], report["measure"]["width_x"])
k_fit = min(CELL_MAX_H / report["measure"]["height_z"], CELL_MAX_FP / fp)
report["measure"]["export_cell_fit_report_only"] = {
    "scale": round(k_fit, 5), "bound_by": "footprint" if CELL_MAX_FP / fp < CELL_MAX_H / report["measure"]["height_z"] else "height",
    "length_m": round(report["measure"]["length_y"] * k_fit, 4), "height_m": round(report["measure"]["height_z"] * k_fit, 4)}
report["measure"]["rest_join_overlap"] = {
    "rule": "combined rest: each join is goo-into-goo -- the pieces interpenetrate (negative = overlap depth along the "
            "join axis between the two torn-end anchors)",
    "crown": round(float((END_ANCHOR[("crown", "mid")] - END_ANCHOR[("crown", "tail")]) @ U2), 4),
    "head": round(float((END_ANCHOR[("head", "head")] - END_ANCHOR[("head", "mid")]) @ U1), 4)}
report["tris"] = {o.name: tri_count(o.data) for o in ALL_OBS}
report["tris"]["total"] = sum(report["tris"].values())
print("MEASURE", json.dumps(report["measure"]), json.dumps(report["tris"]))


def geometry_digest():
    h = hashlib.sha256()
    for o in sorted(ALL_OBS, key=lambda o_: o_.name):
        me = o.data
        co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
        lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
        h.update(np.round(co, 6).astype(np.float32).tobytes()); h.update(lv.tobytes())
        for nm in ("Col", "Glow"):
            cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes[nm].data.foreach_get("color", cd)
            h.update(np.round(cd, 5).tobytes())
        uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


report["digest_geometry_colour_uv"] = geometry_digest()
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
report["seconds_to_improved"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, report["digest_geometry_colour_uv"], round(time.time() - T0, 1))
sys.stdout.flush()
if STOP == "improved":
    os._exit(0)

# =========================================================================== 6. rig
rep = {"unit": UNIT, "version": "v2 goo", "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0


def chain_points(poly, n):
    s = SD.arclen(poly)
    t = np.linspace(0.0, s[-1], n + 1)
    return np.stack([np.interp(t, s, poly[:, k]) for k in range(3)], 1)


MID_J = chain_points(mid_poly, MID_BONES)                 # rear (crown) -> front
T0_ = TAIL_SPINE[0].copy()
TAIL_J = chain_points(TAIL_SPINE, TAIL_BONES)              # tail start (inside the mound) -> tip
# THE CHAIN, nose -> tail tip (a link joins JOINTS[k] -> JOINTS[k+1]; the join link carries no bone)
JOINTS = np.array([SNOUT, HMID, A1] + [MID_J[i] for i in range(MID_BONES, -1, -1)] + list(TAIL_J))
names_chain = ["head.1", "head.0", "join.head"] + ["body.%d" % i for i in range(MID_BONES - 1, -1, -1)] + \
    ["tail.0"] + ["tail.%d" % (i + 1) for i in range(TAIL_BONES)]
assert len(names_chain) == len(JOINTS) - 1, (len(names_chain), len(JOINTS))
assert np.allclose(JOINTS[3], MF) and np.allclose(JOINTS[3 + MID_BONES], C_JOIN)
LINK = {n: k for k, n in enumerate(names_chain)}
S_LINK = SD.arclen(JOINTS)
LINK_MID = 0.5 * (S_LINK[:-1] + S_LINK[1:])
CHAIN_BONES = [n for n in names_chain if not n.startswith("join")]
PIECE_OF = {n: ("head" if n.startswith("head") else "mid" if n.startswith("body") else "tail") for n in CHAIN_BONES}
SEG_OF = {"head": ["head.0", "head.1"], "mid": ["body.%d" % i for i in range(MID_BONES)],
          "tail": ["tail.%d" % i for i in range(TAIL_BONES + 1)]}
DROP_BONES = ["drop.%d" % i for i in range(len(DROPLETS))]
ANCHOR_J = 3 + MID_BONES                                  # the crown join stays put (solve anchor)


def bone_ends(n):
    k = LINK[n]
    a, b = JOINTS[k], JOINTS[k + 1]
    return (b, a) if PIECE_OF[n] in ("head", "mid") else (a, b)   # head + mid bones run from their join outward


arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.5); eb.use_deform = False
PARENT = {"head.0": "root", "head.1": "head.0", "body.0": "root", "tail.0": "root"}
for i in range(1, MID_BONES):
    PARENT["body.%d" % i] = "body.%d" % (i - 1)
for i in range(1, TAIL_BONES + 1):
    PARENT["tail.%d" % i] = "tail.%d" % (i - 1)
for n in DROP_BONES:
    PARENT[n] = "root"
DEFORM = CHAIN_BONES + DROP_BONES
for n in DEFORM:
    e = arm_data.edit_bones.new(n)
    if n.startswith("drop"):
        p = DROP_REST[int(n.split(".")[1])]
        h_, t_ = p, p + UP * 0.3
    else:
        h_, t_ = bone_ends(n)
    e.head = Vector(h_); e.tail = Vector(t_)
    e.align_roll(Vector((0, 0, 1.0)) if abs((Vector(t_) - Vector(h_)).normalized().z) < 0.9 else Vector((0, 1.0, 0)))
    e.use_deform = True
for n in DEFORM:
    e = arm_data.edit_bones[n]
    e.parent = arm_data.edit_bones[PARENT[n]]
    e.use_connect = PARENT[n] != "root" and (e.head - e.parent.tail).length < 1e-6
bpy.ops.object.mode_set(mode="OBJECT")
REST = {b.name: np.array(b.matrix_local) for b in arm_data.bones}
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "connected": b.use_connect, "head": [round(v, 4) for v in b.head_local],
                 "tail": [round(v, 4) for v in b.tail_local], "length": round(b.length, 4)} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["segment_chains"] = {**SEG_OF, "droplets": DROP_BONES}
rep["separability"] = "each piece's sub-chain root (head.0 / body.0 / tail.0) and every droplet bone is parented " \
                      "straight to 'root'; every piece mesh is weighted only to its own sub-chain (verified below)"


def chain_weights(V, E, bones):
    """ordered bones along the piece: vertex -> arc param by nearest point on the bone polyline (extended past both
    ends), 6 graph-smoothing passes, then hat weights between consecutive bone midpoints (<= 2 influences)."""
    pts = np.array([np.array(bone_ends(b)[0]) for b in bones] + [np.array(bone_ends(bones[-1])[1])])
    seg_len = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg_len)])
    best_d = np.full(len(V), np.inf); s = np.zeros(len(V))
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ab = b - a
        tt = (V - a) @ ab / (ab @ ab)
        lo_t = -np.inf if i == 0 else 0.0
        hi_t = np.inf if i == len(pts) - 2 else 1.0
        tt = np.clip(tt, lo_t, hi_t)
        d = np.linalg.norm(V - (a + tt[:, None] * ab), axis=1)
        m = d < best_d
        best_d[m] = d[m]; s[m] = cum[i] + tt[m] * seg_len[i]
    n = len(V)
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    for _ in range(6):
        sm = (np.bincount(E[:, 0], s[E[:, 1]], minlength=n) + np.bincount(E[:, 1], s[E[:, 0]], minlength=n)) / np.maximum(deg, 1)
        s = 0.5 * s + 0.5 * sm
    mids = 0.5 * (cum[:-1] + cum[1:])
    W = np.zeros((n, len(bones)))
    k = np.clip(np.searchsorted(mids, s) - 1, 0, len(bones) - 2)
    u = np.clip((s - mids[k]) / (mids[k + 1] - mids[k]), 0.0, 1.0)
    W[np.arange(n), k] = 1 - u
    W[np.arange(n), k + 1] += u
    return W


WREP = {}
for pc in PIECES:
    ob = PIECEOBJ[pc]
    V = np.array([v.co[:] for v in ob.data.vertices])
    E = np.array([e.vertices[:] for e in ob.data.edges])
    bones = SEG_OF[pc]
    W = chain_weights(V, E, bones)
    groups = {b: ob.vertex_groups.new(name=b) for b in bones}
    for j, b in enumerate(bones):
        for i in np.nonzero(W[:, j] > 1e-6)[0]:
            groups[b].add([int(i)], float(W[i, j]), "REPLACE")
    ob.parent = rig; ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    WREP[pc] = {"bones": bones, "per_bone_dominant": {b: int((np.argmax(W, 1) == j).sum()) for j, b in enumerate(bones)}}
dgroups = {n: drop_ob.vertex_groups.new(name=n) for n in DROP_BONES}
for i, di in enumerate(DROP_VID):
    dgroups[DROP_BONES[di]].add([i], 1.0, "REPLACE")
drop_ob.parent = rig; drop_ob.matrix_parent_inverse = Matrix.Identity(4)
drop_ob.modifiers.new("Armature", "ARMATURE").object = rig
WREP["droplets"] = {"bones": DROP_BONES, "rule": "each droplet rigid on its own bone (weight 1)"}


def weight_audit(ob):
    deform = {b.name for b in arm_data.bones if b.use_deform}
    gi = {g.index: g.name for g in ob.vertex_groups}
    sums, infl, used = [], [], set()
    for v in ob.data.vertices:
        ws = [(gi[g.group], g.weight) for g in v.groups if gi[g.group] in deform and g.weight > 0]
        sums.append(sum(w for _, w in ws)); infl.append(len(ws)); used |= {n for n, _ in ws}
    sums = np.array(sums); infl = np.array(infl)
    return {"weight_sum_min": round(float(sums.min()), 6), "weight_sum_max": round(float(sums.max()), 6),
            "unweighted": int((infl == 0).sum()), "max_influences": int(infl.max()), "bones_used": sorted(used)}


for pc in PIECES:
    WREP[pc]["audit"] = weight_audit(PIECEOBJ[pc])
    WREP[pc]["only_own_chain"] = set(WREP[pc]["audit"]["bones_used"]) <= set(SEG_OF[pc])
    assert WREP[pc]["only_own_chain"], (pc, WREP[pc]["audit"]["bones_used"])
WREP["droplets"]["audit"] = weight_audit(drop_ob)
rep["weights"] = WREP

# =========================================================================== 7. clips
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
LINKV = np.diff(JOINTS, axis=0)
TAIL_LINKS = [LINK["tail.%d" % i] for i in range(1, TAIL_BONES + 1)]


def rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def raxis(axis, a):
    return np.array(Matrix.Rotation(a, 3, Vector(axis)))


def xf(R=None, t=None, pivot=None):
    """4x4: rotate by R about pivot, then translate by t."""
    M = np.eye(4)
    if R is not None:
        M[:3, :3] = R
        if pivot is not None:
            M[:3, 3] = pivot - R @ pivot
    if t is not None:
        M[:3, 3] += t
    return M


def apply(M, p):
    return M[:3, :3] @ p + M[:3, 3]


def solve_chain(Q):
    P = np.zeros_like(JOINTS)
    P[ANCHOR_J] = JOINTS[ANCHOR_J]
    for k in range(ANCHOR_J, len(LINKV)):
        P[k + 1] = P[k] + Q[k] @ LINKV[k]
    for k in range(ANCHOR_J - 1, -1, -1):
        P[k] = P[k + 1] - Q[k] @ LINKV[k]
    return P


def procrustes_z(P, Q):
    """rigid 2D (about Z) fit of all posed joints back onto their rest: the serpent stays in place."""
    A = P[:, :2]; B = JOINTS[:, :2]
    ca, cb = A.mean(0), B.mean(0)
    H = (A - ca).T @ (B - cb)
    ang = math.atan2(H[0, 1] - H[1, 0], H[0, 0] + H[1, 1])
    R = rz(ang)
    t = np.zeros(3); t[:2] = cb - R[:2, :2] @ ca
    return P @ R.T + t, [R @ q for q in Q], ang


def pose_bones(P, Q, G, drops):
    """armature-space matrices -> pose-bone basis. G: per-piece rigid carrier 4x4; drops: [(pos, scale)]."""
    M = {"root": REST["root"]}
    for n in CHAIN_BONES:
        k = LINK[n]
        head_ = P[k + 1] if PIECE_OF[n] in ("head", "mid") else P[k]
        Mb = np.eye(4); Mb[:3, :3] = Q[k] @ REST[n][:3, :3]; Mb[:3, 3] = head_
        M[n] = G[PIECE_OF[n]] @ Mb
    for n, (p, s) in zip(DROP_BONES, drops):
        Mb = np.eye(4); Mb[:3, :3] = s * REST[n][:3, :3]; Mb[:3, 3] = p
        M[n] = Mb
    out = {}
    for n in DEFORM:
        p = PARENT[n]
        rest_rel = np.linalg.inv(REST[p]) @ REST[n]
        basis = np.linalg.inv(rest_rel) @ np.linalg.inv(M[p]) @ M[n]
        mm = Matrix(basis.tolist())
        loc, q, sc = mm.decompose()
        out[n] = (loc, q, sc)
    return out, M


# ---- idle: the split / recombine cycle
CYC_S = IDLE_FRAMES / K.FPS


def spring_step(tau):
    """unit step response of a damped spring (zero start velocity), tau in seconds; 0 before the step."""
    if tau <= 0:
        return 0.0
    f, z = SPLIT_SPRING
    wn = TAU * f / math.sqrt(1 - z * z)
    wd = wn * math.sqrt(1 - z * z)
    return 1.0 - math.exp(-z * wn * tau) * (math.cos(wd * tau) + z / math.sqrt(1 - z * z) * math.sin(wd * tau))


def envelope(jn, t):
    t0, t2 = SPLIT_TIMING[jn]
    e = spring_step((t - t0) * CYC_S) * (1.0 - spring_step((t - t2) * CYC_S))
    return e * (1.0 - float(smoothstep(0.92, 1.0, t)))   # the tail of the settle is faded out: frame 1 == last frame


def carriers(t, e_c, e_h):
    wc, wh = max(0.0, min(1.0, e_c)), max(0.0, min(1.0, e_h))
    G_tail = xf(t=np.array([0.0, SPLIT_TAIL_DRIFT * e_c, 0.0]))
    d_mid = SPLIT_GAP_MID * e_c * U2 + UP * IDLE_BOB * wc * math.sin(TAU * 3 * t)
    G_mid = xf(t=d_mid)
    # head: rides the mid, drifts off along the head join axis, tilts nose-up and looks around while free
    tilt = math.radians(IDLE_HEAD_TILT_DEG) * wh
    look = math.radians(IDLE_HEAD_LOOK_DEG) * wh * math.sin(TAU * 2 * t)
    R = rz(look) @ raxis((1.0, 0, 0), -tilt)
    d_head = d_mid + SPLIT_GAP_HEAD * e_h * U1 + UP * IDLE_BOB * wh * math.sin(TAU * 3 * t + 1.3)
    G_head = xf(R=R, t=d_head, pivot=A1)
    return {"head": G_head, "mid": G_mid, "tail": G_tail}


def droplet_positions(t, G, e):
    out = []
    for i, (jn, _, frac, side, r) in enumerate(DROPLETS):
        pa, pb = ("tail", "mid") if jn == "crown" else ("mid", "head")
        a = apply(G[pa], CROWN_TOP if jn == "crown" else END_ANCHOR[(jn, pa)])
        b = apply(G[pb], END_ANCHOR[(jn, pb)])
        ax_ = unit(b - a) if np.linalg.norm(b - a) > 1e-6 else (U2 if jn == "crown" else U1)
        lat = unit(np.cross(ax_, UP)) if abs(ax_[2]) < 0.95 else np.array([1.0, 0, 0])
        on_line = a + frac * (b - a) + lat * side
        rest_follow = apply(G[pb], DROP_REST[i])       # hovering beside the join, carried by the outer piece
        w = float(smoothstep(0.0, 1.0, e[jn]))
        bob = UP * 0.06 * math.sin(TAU * 4 * t + 1.7 * i)
        p = (1 - w) * rest_follow + w * on_line + bob
        s = 1.0 + 0.08 * math.sin(TAU * 3 * t + 2.1 * i)
        out.append((p, s))
    return out


def idle_pose(t):
    Q = [np.eye(3) for _ in LINKV]
    for j, k in enumerate(TAIL_LINKS):
        f = (j + 1) / len(TAIL_LINKS)
        Q[k] = rz(math.radians(IDLE_TAIL_DEG) * f ** 2.0 * math.sin(TAU * 2 * t - 1.4 * f * math.pi))
    P = solve_chain(Q)
    e = {jn: envelope(jn, t) for jn in SPLIT_TIMING}
    G = carriers(t, e["crown"], e["head"])
    drops = droplet_positions(t, G, e)
    glow = 1.0 + IDLE_GLOW_PULSE * max(0.0, max(e.values()))
    return P, Q, G, drops, glow, 0.0, e


# ---- walk: in-place slither of the combined serpent
S_C = S_LINK[ANCHOR_J]


def walk_amp(s):
    s_mf = S_LINK[3]
    if s < s_mf:
        return math.radians(WALK_AMP_HEAD_DEG)
    if s <= S_C + 1.0:
        f = (s - s_mf) / (S_C + 1.0 - s_mf)
        return math.radians(WALK_AMP_HEAD_DEG + (WALK_AMP_BODY_DEG - WALK_AMP_HEAD_DEG) * f)
    f = (s - S_C - 1.0) / (S_LINK[-1] - S_C - 1.0)
    return math.radians(WALK_AMP_BODY_DEG + (WALK_AMP_TAIL_DEG - WALK_AMP_BODY_DEG) * f ** 1.3)


def walk_pose(t):
    Q = [rz(walk_amp(LINK_MID[k]) * math.sin(TAU * (LINK_MID[k] / WALK_WAVELENGTH - t))) for k in range(len(LINKV))]
    mid_mean = np.mean([math.atan2(Q[LINK[b]][1, 0], Q[LINK[b]][0, 0]) for b in SEG_OF["mid"]])
    for b in SEG_OF["head"] + ["join.head"]:
        Q[LINK[b]] = rz(WALK_HEAD_FOLLOW * mid_mean)
    P = solve_chain(Q)
    P, Q, ang = procrustes_z(P, Q)
    bob = UP * WALK_BOB * (0.5 - 0.5 * math.cos(TAU * 2 * t))
    G = {"head": xf(t=bob), "mid": xf(t=bob), "tail": np.eye(4)}
    # droplets ride their join (rigidly with the chain: rest offset rotated with the local heading)
    drops = []
    for i, (jn, *_r) in enumerate(DROPLETS):
        j = ANCHOR_J if jn == "crown" else 2
        k = j if jn == "crown" else 2
        p = P[j] + Q[k] @ (DROP_REST[i] - JOINTS[j]) + bob + UP * 0.05 * math.sin(TAU * 2 * t + 1.7 * i)
        drops.append((p, 1.0))
    beat = 0.5 - 0.5 * math.cos(TAU * t)
    return P, Q, G, drops, 1.0 + WALK_GLOW_PULSE * beat, ang, {}


sock = bsdf.inputs["Emission Strength"]
base_es = sock.default_value


def author(name, frames, pose_fn):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    glow, fits, envs = [], [], []
    for f in range(1, frames + 2):
        t = ((f - 1) / frames) % 1.0
        P, Q, G, drops, g, ang, e = pose_fn(t)
        glow.append(g); fits.append(ang); envs.append(e)
        B, _ = pose_bones(P, Q, G, drops)
        for n in DEFORM:
            loc, q, sc = B[n]
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = pose[n]
            pb.location = loc; pb.rotation_quaternion = q
            pb.scale = sc if n.startswith("drop") else (1.0, 1.0, 1.0)
            pb.keyframe_insert("location", frame=f, group=n)
            pb.keyframe_insert("rotation_quaternion", frame=f, group=n)
            if n.startswith("drop"):
                pb.keyframe_insert("scale", frame=f, group=n)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, frames + 1
    act.use_cyclic = True
    out = {"procrustes_yaw_deg_range": [round(math.degrees(min(fits)), 4), round(math.degrees(max(fits)), 4)]}
    try:
        K.assign_action(nt, act)
        for f, g in enumerate(glow, start=1):
            sock.default_value = base_es * g
            sock.keyframe_insert("default_value", frame=f)
        out["glow"] = {"socket": "Principled BSDF.Emission Strength", "base": base_es,
                       "range": [round(base_es * min(glow), 4), round(base_es * max(glow), 4)]}
    except Exception as exc:  # noqa: BLE001
        out["glow"] = {"error": repr(exc)}
    nt.animation_data.action = None
    sock.default_value = base_es
    out["slots"] = [s_.identifier for s_ in act.slots]
    return act, out, envs


def eval_all():
    dg = bpy.context.evaluated_depsgraph_get()
    res = {}
    for ob in ALL_OBS:
        ev = ob.evaluated_get(dg)
        m_ = ev.to_mesh()
        co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
        ev.to_mesh_clear()
        res[ob.name] = co.reshape(-1, 3)
    return res


PF_T = {p: [list(q.vertices) for q in PIECEOBJ[p].data.polygons] for p in PIECES}
JOIN_PAIRS = {"crown": ("tail", "mid"), "head": ("mid", "head")}
MEAS_PT = {"crown": C_JOIN, "head": MF}                   # the shared chain joint of each join (rest)


def measure(act, frames, gap_every=6):
    K.assign_action(rig, act)
    first = last = None
    minz = 1e9
    root_off = 0.0
    ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
    head_yaw, tail_tip, open_ = [], [], {j: [] for j in JOIN_PAIRS}
    gaps = {j: [] for j in JOIN_PAIRS}
    for f in range(1, frames + 2):
        scene.frame_set(f)
        C = eval_all()
        if f == 1:
            first = C
        if f == frames + 1:
            last = C
        allc = np.vstack(list(C.values()))
        minz = min(minz, float(allc[:, 2].min()))
        ext_lo = np.minimum(ext_lo, allc.min(0)); ext_hi = np.maximum(ext_hi, allc.max(0))
        root_off = max(root_off, (rig.matrix_world @ pose["root"].head).length)
        hd = np.array(pose["head.1"].tail) - np.array(pose["head.1"].head)
        head_yaw.append(math.degrees(math.atan2(hd[0], -hd[1])))
        tail_tip.append(np.array(pose["tail.%d" % TAIL_BONES].tail))
        # join opening: the join point as carried by each side (bone-space rigid carry of the rest join point)
        for jn, (pa, pb) in JOIN_PAIRS.items():
            ba = "tail.0" if pa == "tail" else "body.%d" % (MID_BONES - 1)
            bb = "body.0" if pb == "mid" else "head.0"
            Jp = MEAS_PT[jn]
            carried = []
            for bn in (ba, bb):
                Mp = np.array(rig.matrix_world) @ np.array(pose[bn].matrix)
                carried.append(Mp[:3, :3] @ (np.linalg.inv(REST[bn])[:3, :3] @ (Jp - REST[bn][:3, 3])) + Mp[:3, 3])
            open_[jn].append(float(np.linalg.norm(carried[1] - carried[0])))
        if (f - 1) % gap_every == 0:
            for jn, (pa, pb) in JOIN_PAIRS.items():
                A = C[PIECEOBJ[pa].name]; B_ = C[PIECEOBJ[pb].name]
                gaps[jn].append((f, min_gap(A, PF_T[pa], B_, PF_T[pb])))
    seams = {n: round(float(np.linalg.norm(first[n] - last[n], axis=1).max()) * 1000, 6) for n in first}
    tail_tip = np.array(tail_tip)
    out = {"frames": frames + 1, "cycle_frames": frames, "cycle_s": round(frames / K.FPS, 4),
           "seam_first_last_max_per_object_mm": seams,
           "min_z": round(minz, 6), "root_offset_max": round(root_off, 9),
           "head_yaw_deg_range": [round(min(head_yaw), 3), round(max(head_yaw), 3)],
           "tail_tip_x_range": [round(float(tail_tip[:, 0].min()), 4), round(float(tail_tip[:, 0].max()), 4)],
           "join_opening_units_max": {j: round(max(v), 4) for j, v in open_.items()},
           "join_opening_frame_of_max": {j: int(np.argmax(v)) + 1 for j, v in open_.items()},
           "join_opening_first_last": {j: [round(v[0], 6), round(v[-1], 6)] for j, v in open_.items()},
           "mesh_gap_every_%dth_frame" % gap_every: {j: [[f_, round(g_, 4)] for f_, g_ in v] for j, v in gaps.items()},
           "mesh_gap_max": {j: round(max(g_ for _, g_ in v), 4) for j, v in gaps.items()},
           "mesh_gap_frame_of_max": {j: max(v, key=lambda x: x[1])[0] for j, v in gaps.items()},
           "extent": {"width": round(float(ext_hi[0] - ext_lo[0]), 4), "length": round(float(ext_hi[1] - ext_lo[1]), 4),
                      "height": round(float(ext_hi[2] - ext_lo[2]), 4)}}
    return out, open_


t_anim = time.time()
act_idle, idle_x, idle_env = author("idle", IDLE_FRAMES, idle_pose)
act_walk, walk_x, _ = author("walk", WALK_FRAMES, walk_pose)
rig.animation_data.action = None
m_idle, idle_open = measure(act_idle, IDLE_FRAMES, gap_every=4)
m_walk, _ = measure(act_walk, WALK_FRAMES, gap_every=6)


def timing(jn):
    ev = [e[jn] for e in idle_env]
    fr = np.arange(1, len(ev) + 1)
    above = [f for f, v in zip(fr, ev) if v > 0.05]
    pk = int(fr[int(np.argmax(ev))])
    t0, t2 = SPLIT_TIMING[jn]
    settle = [f for f, v in zip(fr, ev) if f > 1 + t2 * IDLE_FRAMES and abs(v) > 0.02]
    return {"split_start_frame": 1 + int(round(t0 * IDLE_FRAMES)), "open_past_5pct_frames": [int(above[0]), int(above[-1])],
            "peak_frame": pk, "peak_envelope": round(max(ev), 4), "merge_start_frame": 1 + int(round(t2 * IDLE_FRAMES)),
            "squelch_overshoot_envelope": round(min(ev), 4), "settled_within_2pct_from_frame": int(settle[-1]) + 1 if settle else None}


m_idle["split_cycle"] = {jn: timing(jn) for jn in SPLIT_TIMING}
both = [min(max(0, e["crown"]), max(0, e["head"])) for e in idle_env]
m_idle["split_peak_frame"] = int(np.argmax(both)) + 1
m_idle["rest_extent"] = {"width": report["measure"]["width_x"], "length": report["measure"]["length_y"],
                         "height": report["measure"]["height_z"]}
m_idle["motion"] = {"split_gap_mid": SPLIT_GAP_MID, "split_gap_head": SPLIT_GAP_HEAD, "tail_drift": SPLIT_TAIL_DRIFT,
                    "timing": SPLIT_TIMING, "spring_hz_damping": SPLIT_SPRING, "head_tilt_deg": IDLE_HEAD_TILT_DEG,
                    "head_look_deg": IDLE_HEAD_LOOK_DEG, "bob": IDLE_BOB, "tail_tip_deg": IDLE_TAIL_DEG,
                    "glow_pulse": IDLE_GLOW_PULSE, **idle_x}
wave_speed = WALK_WAVELENGTH / (WALK_FRAMES / K.FPS)
m_walk["implied_forward_speed"] = {"wave_speed_units_per_s": round(wave_speed, 4),
                                   "m_per_s_at_report_only_cell_fit": round(wave_speed * report["measure"]["export_cell_fit_report_only"]["scale"], 4)}
m_walk["motion"] = {"wavelength": WALK_WAVELENGTH, "amp_head_deg": WALK_AMP_HEAD_DEG, "amp_body_deg": WALK_AMP_BODY_DEG,
                    "amp_tail_tip_deg": WALK_AMP_TAIL_DEG, "head_follow": WALK_HEAD_FOLLOW, "goo_bob": WALK_BOB,
                    "glow_pulse": WALK_GLOW_PULSE, "state": "combined serpent (joins closed)",
                    "wave_direction": "head -> tail (posterior travelling wave = forward propulsion toward -Y)", **walk_x}
rep["idle"] = {"status": "PROPOSED v2 (artist 2026-09-26: pieces separate then combine into one long serpent)", **m_idle}
rep["walk"] = {"status": "PROPOSED v2 (in-place slither, combined)", **m_walk}
rep["anim_seconds"] = round(time.time() - t_anim, 1)
K.assign_action(rig, None)
scene.frame_set(1)
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_start, scene.frame_end = 1, IDLE_FRAMES + 1
rig["conquest_rig"] = "magmoo v2 chain: root + head.0-1 | body.0-%d | tail.0-%d | drop.0-%d (three separable pieces + droplets)" % (
    MID_BONES - 1, TAIL_BONES, len(DROPLETS) - 1)
rig["conquest_split_peak_frame"] = m_idle["split_peak_frame"]
for ob in ALL_OBS:
    ob["conquest_clips"] = ["idle", "walk"]
    ob["conquest_clip_status"] = "idle = split/recombine cycle, walk = combined slither (both PROPOSED); no attack/hit/death"


def full_digest():
    h = hashlib.sha256(geometry_digest().encode())
    for ob in sorted(ALL_OBS, key=lambda o_: o_.name):
        for v in ob.data.vertices:
            for g in v.groups:
                h.update(np.array([v.index, g.group, round(g.weight, 6)], np.float64).tobytes())
    for b in arm_data.bones:
        h.update(np.round(np.array(b.matrix_local), 6).astype(np.float32).tobytes())
    for act in (act_idle, act_walk):
        for fc in K.action_fcurves(act):
            h.update(fc.data_path.encode()); h.update(bytes([fc.array_index]))
            h.update(np.round(np.array([k.co[:] for k in fc.keyframe_points]), 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


rep["digest_full"] = full_digest()
rep["digest_geometry_colour_uv"] = report["digest_geometry_colour_uv"]
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True)

# =========================================================================== glb (identity scale, natural units)
for o in scene.objects:
    o.select_set(o is rig or o in ALL_OBS)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, act_idle)
t = time.time()
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False)
rig.animation_data.action = None
rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "sha256_16": hashlib.sha256(open(OUT_GLB, "rb").read()).hexdigest()[:16],
              "seconds": round(time.time() - t, 1),
              "structure": "armature identity + 4 skinned mesh children (head/mid/tail/droplets), natural scale; "
                           "report-only cell fit %.5f" % k_fit}


# =========================================================================== skins (palette swap, geometry untouched)
def region_sample(pal_obs):
    out = {}
    for nm in ("Col", "Glow"):
        acc = {}
        for ob in pal_obs:
            me = ob.data
            names, rid, _ = PAL.read_regions(me)
            ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
            cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes[nm].data.foreach_get("color", cd)
            cd = cd.reshape(-1, 4)[ls, :3]
            for r in np.unique(rid):
                acc.setdefault(names[r], []).append(cd[rid == r])
        out[nm] = {k: [round(float(x), 4) for x in np.vstack(v).mean(0)] for k, v in acc.items()}
    return out


def region_counts():
    tot = {}
    for ob in ALL_OBS:
        names, rid, _ = PAL.read_regions(ob.data)
        for r, c in zip(*np.unique(rid, return_counts=True)):
            tot[names[r]] = tot.get(names[r], 0) + int(c)
    return tot


def vpos_sha():
    return hashlib.sha256(b"".join(np.round(np.array([v.co[:] for v in o.data.vertices]), 6).astype(np.float32).tobytes()
                                   for o in ALL_OBS)).hexdigest()[:16]


geo0 = geometry_digest()
vpos0 = vpos_sha()
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"],
                            "region_faces": region_counts(), "region_mean_linear": region_sample(ALL_OBS),
                            "vertex_positions_sha": vpos0}}
for skin in SKINS:
    if skin == "default":
        continue
    pal = PAL.load(UNIT, skin)
    gt = glow_tiers(pal)
    assert gt["pass"], (skin, gt)
    for ob in ALL_OBS:
        PAL.paint(ob.data, pal)
    PAL.apply_material(mat, pal)
    out = OUT_RIGGED[:-6] + "__" + skin + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    rep["skins"][skin] = {"file": out, "palette": PAL.table(pal), "palette_files": pal["files"], "glow_tiers": gt,
                          "region_faces": region_counts(), "region_mean_linear": region_sample(ALL_OBS),
                          "geometry_colour_uv_digest": geometry_digest(), "vertex_positions_sha": vpos_sha()}
for ob in ALL_OBS:
    PAL.paint(ob.data, pal_default)
PAL.apply_material(mat, pal_default)
rep["skins"]["repaint_proof"] = {
    "rule": "a skin is a pure palette swap: region face counts identical, vertex positions identical, colours differ only "
            "by the palette table; the default digest is restored after the swap",
    "default_digest_before": geo0, "default_digest_after_restore": geometry_digest()}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({"bones": rep["bone_count"], "digest_full": rep["digest_full"], "seconds": rep["seconds"]}))
print("IDLE", json.dumps(rep["idle"]))
print("WALK", json.dumps(rep["walk"]))
sys.stdout.flush()
os._exit(0)
