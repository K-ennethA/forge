"""Mortis (hero, Conquest character_id 'necromancer') rework: identity made READABLE, one run.

    blender --background source-copies/hero-necromancer.blend --factory-startup --python improve/mortis_rework.py -- \
        [--preview <out.blend>]          (geometry + UV + regions + palette only: no bake, no rig -- fast look loop)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Outputs (the opened source copy is never saved over; save_as_mainfile copy=True):
    improved/mortis.blend + .json        hero-tier mesh, UVs, baked normal/AO, default palette (no rig)
    improved/textures/mortis_{normal,ao}.png
    rigged/mortis.blend + .json          + rig (trunk/head, book float bone, carrying-arms group), PROPOSED 'idle'
                                         (no walk: gait "glide" is unconfirmed; no attacks)

Artist identity (design/review-log.md 2026-09-25, verbatim): "mortis is meant to be a necromancer with a
floating book of the dead, he is carried by the dead and stand on their arms, he is a robed figure, we can
make updates to make all this more clear." Scale policy: natural proportions (cell fit report-only).

Source survey (24 meshes, 298,272 tris, no materials, faces -Y): Sphere = grave slab (960), Sphere.001 =
hooded robe (12,608), Sphere.002 = face mask inside the hood (4,276), Sphere.003-.022 = twenty dead arms with
raised fists (13,648 each = 272,960 = 91.5% of the tris; negative X scale -> winding flipped on import here),
Sphere.023 = a DETACHED object floating in front of the chest (7,468). The survey read Sphere.023 as
"crossed arms"; its top view is an OPEN BOOK (two page spreads, centre gutter, carved glyphs, page-edge
stripes on the sides) -- it is the floating book of the dead. Mortis has no arms in the sculpt.

Pipeline:
  1. parts to world space (transforms applied; det<0 parts get their winding reversed).
  2. HIGH per part: optional Catmull-Clark level + Laplacian-style smooth (removes the voxel-remesh stair
     steps the flat shading exposed; the smoothed high is ALSO the bake source, so the normal map does not
     paint the stairs back). Book tilted toward the viewer so its pages read from the front and tactical.
  3. per-part collapse decimation (deterministic) to a REDISTRIBUTED budget (hands at a sane share,
     face/robe/book get their due), joined, flat, Smart UV + pack.
  4. colour regions (face level) -> palettes.store_regions; paint from palettes/mortis/default.json.
  5. bake normal + AO from the smoothed highs (base pass + one isolated pass per bake group).
  6. rig: root, grave (slab), body/chest/head (robe + face), book (own float bone), dead_arms group (control)
     + one deform bone per carrying arm; rigid part weights, robe z-band blends (<= 2 influences).
  7. PROPOSED idle: hover bob carried by the dead (the carrying arms heave WITH the body), book float on its
     own phase, perimeter arms shifting almost imperceptibly, slab never keyed. Integer harmonics: seam 0.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast
import numpy as np
from mathutils import Matrix, Vector, Euler
from mathutils.kdtree import KDTree
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402  (read-only use)
import palettes as PAL  # noqa: E402  (read-only use)

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun)
UNIT = "mortis"
TRI_TARGETS = {"robe": 13000,        # "robe detail" (folds, hood)
               "head": 6000,         # "face detail"
               "book": 7000,         # "book detail" (pages, glyphs, page edges)
               "slab": 3840}         # "grave slab detail" (960 source, one SIMPLE split so the grave glow rings stay round)
HAND_TRIS = 440                      # "detail per dead arm" (x20)
TRI_BUDGET = [25000, 40000]          # declared hero-tier window (contract tri_budget)
PRE = {"robe": (1, 8, "CATMULL_CLARK"), "head": (1, 4, "CATMULL_CLARK"), "book": (1, 3, "CATMULL_CLARK"),
       "slab": (1, 0, "SIMPLE"), "hand": (0, 3, "CATMULL_CLARK")}
#                                    # per part: (subdivision levels, smooth iterations, type) -- "stair-step cleanup"
SMOOTH_FACTOR = 0.5
BOOK_TILT_DEG = 20.0                 # "book tilt toward the viewer" (pages face the camera)
BOOK_LIFT = 0.0                      # "book float height" offset (sculpt units)
BOOK_FORWARD = 0.0                   # "book distance in front" offset (sculpt units, + = further out)
FIST_FRAC = 0.56                     # dead arm: fist colour above this fraction of the arm's height
Z_CHEST = 6.5                        # robe weight split body -> chest (sculpt z)
Z_NECK = 9.8                         # chest -> head split (sculpt z; the chin is at 9.975)
TRIM_HEM_H = 0.90                    # "hem trim height" above the local hem line
UNDER_TOP_Z = 7.6                    # "robe opening": top of the front opening (sculpt z)
UNDER_HALF_W = (0.12, 1.05)          # "robe opening width": half-width at the top, at the hem
TRIM_EDGE_W = 0.20                   # "opening trim width"
HOOD_SHADOW_D = 0.50                 # "hood opening": robe within this of the face mask is dark hood lining
HOOD_RIM_W = 0.20                    # "hood rim trim width" beyond the opening
GLOW_RING = 0.16                     # "grave glow" ring width around each arm where it leaves the slab
RUNE_CAV = 0.22                      # glyph carvings: page faces more concave than this glow
GUTTER_W = 0.30                      # book gutter half-width excluded from the runes
EYE_DEPTH_FRAC = 0.36                # eye sockets: head faces deeper than this fraction of the max hull depth
IDLE_FRAMES = 120                    # idle loop length in frames (24 fps -> 5 s)
HOVER_BOB = 0.08                     # "hover bob" height (sculpt units; 0.6% of his height)
BODY_SWAY_DEG = 0.35                 # robe sway (roll)
CHEST_BREATH_DEG = 0.9               # chest breath (pitch)
HEAD_DEG = (0.8, 1.6)                # head (pitch, slow look yaw)
BOOK_BOB = 0.14                      # "book float" height
BOOK_TURN_DEG = (2.0, 4.0, 2.5)      # book pitch, yaw, roll drift
ARM_SHIFT_DEG = 1.1                  # "dead arms shifting" (perimeter arms, per-arm phase)
ARM_HEAVE_PERIM = 0.35               # perimeter arms heave this fraction of the bob (carriers heave 1.0)
BAKE_CAGE = 0.08                     # bake cage extrusion (sculpt units; he is 13 units tall)
BAKE_RES = (2048, 1024)              # normal, AO texture sizes
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9   # Conquest hero ceilings -- REPORT ONLY (scale policy 2026-09-25)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(ROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", UNIT + ".blend")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "conquest_character_id": "necromancer", "source": bpy.data.filepath, "tier": "hero",
          "tri_budget": TRI_BUDGET, "yaw_fix_deg": 0.0, "overrides": OVERRIDES}
geo = {}


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    flip = False
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
        flip = np.linalg.det(M[:3, :3]) < 0
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    faces = [lv[a:a + b].tolist() for a, b in zip(ls, lt)]
    if flip:                          # mirrored transform: keep normals outward
        faces = [f[::-1] for f in faces]
    return co, faces


def tri_count_faces(F):
    return int(sum(len(f) - 2 for f in F))


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


scene = bpy.context.scene


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], F)
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


# =========================================================================== 1. source parts
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
SRC = {"Sphere": "slab", "Sphere.001": "robe", "Sphere.002": "head", "Sphere.023": "book"}
HANDS = ["Sphere.%03d" % i for i in range(3, 23)]
for k, on in enumerate(HANDS):
    SRC[on] = "hand.%02d" % k
PART_NAMES = ["slab", "robe", "head", "book"] + ["hand.%02d" % k for k in range(20)]
parts = {}
for on, pn in SRC.items():
    o = bpy.data.objects[on]
    V, F = mesh_arrays(o.data, o.matrix_world)
    parts[pn] = {"V": V, "F": F, "src": on, "tris_src": tri_count(o.data),
                 "det": round(float(np.linalg.det(np.array(o.matrix_world)[:3, :3])), 4)}
report["tris_source"] = int(sum(p["tris_src"] for p in parts.values()))
hand_src = sum(parts[p]["tris_src"] for p in PART_NAMES if p.startswith("hand"))
report["source_shares"] = {"hands": round(hand_src / report["tris_source"], 4),
                           "head": round(parts["head"]["tris_src"] / report["tris_source"], 4),
                           "robe": round(parts["robe"]["tris_src"] / report["tris_source"], 4),
                           "book": round(parts["book"]["tris_src"] / report["tris_source"], 4)}
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)

# =========================================================================== 2. highs (cleanup + book tilt)
pre_rep = {}
for pn in PART_NAMES:
    kind = "hand" if pn.startswith("hand") else pn
    lv_, it_, st_ = PRE[kind]
    V0 = parts[pn]["V"]
    if lv_ or it_:
        ob = new_obj("pre_" + pn, V0, parts[pn]["F"])
        if lv_:
            ms = ob.modifiers.new("sub", "SUBSURF"); ms.levels = ms.render_levels = lv_
            ms.subdivision_type = st_; ms.boundary_smooth = "ALL"
        if it_:
            mm = ob.modifiers.new("sm", "SMOOTH"); mm.factor = SMOOTH_FACTOR; mm.iterations = it_
        V, F = evaluated_arrays(ob)
        me_ = ob.data
        bpy.data.objects.remove(ob, do_unlink=True); bpy.data.meshes.remove(me_)
    else:
        V, F = V0.copy(), parts[pn]["F"]
    pre_rep[pn] = {"subdiv": lv_, "smooth_iter": it_, "tris_high": tri_count_faces(F),
                   "bbox_change": np.round((V.max(0) - V.min(0)) - (V0.max(0) - V0.min(0)), 4).tolist()}
    parts[pn]["V"], parts[pn]["F"] = V, F
geo["high_cleanup"] = {k: v for k, v in pre_rep.items() if not k.startswith("hand.") or k == "hand.00"}
geo["high_cleanup"]["hands_bbox_change_max"] = float(np.max([np.abs(pre_rep[p]["bbox_change"]).max()
                                                             for p in PART_NAMES if p.startswith("hand")]))

# book tilt about its own centre (X axis: + raises the back edge, pages turn toward -Y / the viewer)
BV = parts["book"]["V"]
book_c0 = (BV.min(0) + BV.max(0)) / 2
th = math.radians(BOOK_TILT_DEG)
R_BOOK = np.array([[1, 0, 0], [0, math.cos(th), -math.sin(th)], [0, math.sin(th), math.cos(th)]])
parts["book"]["V"] = (BV - book_c0) @ R_BOOK.T + book_c0 + np.array([0.0, -BOOK_FORWARD, BOOK_LIFT])
BOOK_LOCAL = BV - book_c0           # untilted, centred (book-local frame for regions)

# robe / head / book clearance at rest (book must float free)
def min_dist(A, Bset):
    kd = KDTree(len(Bset))
    for i, p in enumerate(Bset):
        kd.insert(p, i)
    kd.balance()
    return float(min(kd.find(p)[2] for p in A))


geo["book_clearance_rest"] = round(min_dist(parts["book"]["V"][::3], np.vstack([parts["robe"]["V"], parts["head"]["V"]])), 4)

allV = np.vstack([parts[p]["V"] for p in PART_NAMES])
lo, hi = allV.min(0), allV.max(0)
SHIFT = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
for p in PART_NAMES:
    parts[p]["V"] = parts[p]["V"] - SHIFT
book_c0 = book_c0 - SHIFT
size = hi - lo
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / size[2], CELL_MAX_FP / fp)
geo["natural"] = {"height": round(float(size[2]), 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                  "footprint": round(fp, 4), "units": "sculpt units (authored at natural proportions, no refit)",
                  "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(float(size[2] * k_fit), 4),
                                                  "bound_by": "height" if CELL_MAX_H / size[2] <= CELL_MAX_FP / fp else "footprint",
                                                  "ceilings": [CELL_MAX_H, CELL_MAX_FP]},
                  "shipped_glb_height_m": 1.8}

HIGH = {p: new_obj(UNIT + "_high_" + p, parts[p]["V"], parts[p]["F"]) for p in PART_NAMES}

# cavity field per high part (+ = concave furrow, - = convex ridge), improve_unit.py rule
CAVP, CAVV = [], []
for p in PART_NAMES:
    me = HIGH[p].data
    W = parts[p]["V"]
    ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev); ev = ev.reshape(-1, 2)
    nv = np.empty(len(me.vertices) * 3); me.vertex_normals.foreach_get("vector", nv); nv = nv.reshape(-1, 3)
    deg = np.bincount(ev.ravel(), minlength=len(W)).astype(float)

    def nmean(X):
        s_ = np.zeros_like(X)
        for k in range(X.shape[1]):
            s_[:, k] = np.bincount(ev[:, 0], X[ev[:, 1], k], minlength=len(W)) + np.bincount(ev[:, 1], X[ev[:, 0], k], minlength=len(W))
        return s_ / np.maximum(deg, 1)[:, None]
    el = np.linalg.norm(W[ev[:, 0]] - W[ev[:, 1]], axis=1).mean()
    cav = ((nmean(W) - W) * nv).sum(1) / el
    for _ in range(6):
        cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
    cs_ = np.percentile(np.abs(cav), 95) + 1e-9
    CAVP.append(W); CAVV.append(np.clip(cav / cs_, -1, 1))
CAVP = np.vstack(CAVP); CAVV = np.concatenate(CAVV)

# =========================================================================== 3. decimate + join
low_parts = []
for p in PART_NAMES:
    hob = HIGH[p]
    tgt = HAND_TRIS if p.startswith("hand") else TRI_TARGETS[p]
    mod = hob.modifiers.new("dec", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = min(1.0, tgt / tri_count(hob.data))
    mod.use_collapse_triangulate = True
    V, F = evaluated_arrays(hob)
    hob.modifiers.remove(mod)
    low_parts.append((p, V, F))
LV, LF, FPART, off = [], [], [], 0
for pi, (p, V, F) in enumerate(low_parts):
    LV.append(V); LF += [[v + off for v in f] for f in F]; FPART += [pi] * len(F); off += len(V)
LV = np.vstack(LV); FPART = np.array(FPART)
lo2, hi2 = LV.min(0), LV.max(0)
S2 = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
LV = LV - S2
for p in PART_NAMES:
    parts[p]["V"] = parts[p]["V"] - S2
    Vh = np.empty(len(HIGH[p].data.vertices) * 3); HIGH[p].data.vertices.foreach_get("co", Vh)
    HIGH[p].data.vertices.foreach_set("co", (Vh.reshape(-1, 3) - S2).ravel()); HIGH[p].data.update()
CAVP = CAVP - S2
book_c0 = book_c0 - S2
SHIFT = SHIFT + S2
report["origin_shift_sculpt_units"] = SHIFT.round(5).tolist()
low = new_obj(UNIT, LV, LF)
me = low.data
report["tris_final"] = tri_count(me)
tpp = {p: int(sum(len(f) - 2 for f, fp_ in zip(LF, FPART) if fp_ == i)) for i, p in enumerate(PART_NAMES)}
hands_final = sum(v for k, v in tpp.items() if k.startswith("hand"))
report["tris_per_part"] = {**{k: v for k, v in tpp.items() if not k.startswith("hand")}, "hands_total": hands_final,
                           "per_hand": sorted(set(v for k, v in tpp.items() if k.startswith("hand")))}
report["redistribution"] = {
    "hands_share": {"source": report["source_shares"]["hands"], "final": round(hands_final / report["tris_final"], 4)},
    "head_share": {"source": report["source_shares"]["head"], "final": round(tpp["head"] / report["tris_final"], 4)},
    "robe_share": {"source": report["source_shares"]["robe"], "final": round(tpp["robe"] / report["tris_final"], 4)},
    "book_share": {"source": report["source_shares"]["book"], "final": round(tpp["book"] / report["tris_final"], 4)},
    "note": "the shipped glb's shares are measured by improve/mortis_glb_audit.py (improved/mortis_glb_share.json)"}

# =========================================================================== flat + UV
me.shade_flat()
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

# =========================================================================== 4. regions
nf = len(me.polygons)
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
kd = KDTree(len(CAVP))
for i, p in enumerate(CAVP):
    kd.insert(p, i)
kd.balance()
fcav = np.array([np.mean([CAVV[j] for (_, j, _) in kd.find_n(p, 6)]) for p in FC])
PI = {p: i for i, p in enumerate(PART_NAMES)}
is_hand = FPART >= PI["hand.00"]
REG = ["robe", "robe_under", "trim", "robe_shadow", "face", "eye_socket", "eye_glow",
       "book_cover", "book_page", "book_edge", "book_rune", "dead_arm", "dead_fist",
       "slab_stone", "slab_top", "grave_glow"]
R_ = {n: i for i, n in enumerate(REG)}
rid = np.full(nf, R_["robe"], dtype=np.int32)
zs = lambda z: z - SHIFT[2]          # sculpt z -> final z

# ---- dead arms: arm vs fist by per-arm height fraction
ARM = {}
for k in range(20):
    pn = "hand.%02d" % k
    Vh_ = parts[pn]["V"]
    z0, z1 = float(Vh_[:, 2].min()), float(Vh_[:, 2].max())
    low_half = Vh_[Vh_[:, 2] < z0 + 0.45 * (z1 - z0)]
    cxy = low_half[:, :2].mean(0)
    r = float(np.median(np.linalg.norm(low_half[:, :2] - cxy, axis=1)))
    top = Vh_[Vh_[:, 2] > z1 - 0.25 * (z1 - z0)]
    ARM[k] = {"base": [float(cxy[0]), float(cxy[1]), z0], "top": [float(top[:, 0].mean()), float(top[:, 1].mean()), z1],
              "radius": r}
    fm_ = FPART == PI[pn]
    t = (FC[fm_, 2] - z0) / (z1 - z0)
    rid[np.nonzero(fm_)[0]] = np.where(t > FIST_FRAC, R_["dead_fist"], R_["dead_arm"])

# ---- slab: stone sides, grave-earth top, glow ring where each arm leaves the slab
sf = FPART == PI["slab"]
rid[sf] = np.where(FN[sf, 2] > 0.55, R_["slab_top"], R_["slab_stone"])
ring = np.zeros(nf, bool)
for k, a in ARM.items():
    d = np.hypot(FC[:, 0] - a["base"][0], FC[:, 1] - a["base"][1])
    ring |= d < a["radius"] + GLOW_RING
rid[sf & (FN[:, 2] > 0.55) & ring] = R_["grave_glow"]

# ---- head: pale face; eye sockets by depth below the head's convex hull (vineweave rule)
hf = FPART == PI["head"]
rid[hf] = R_["face"]
Hv = parts["head"]["V"]
bmh = bmesh.new()
for p in Hv:
    bmh.verts.new(p)
hull = bmesh.ops.convex_hull(bmh, input=list(bmh.verts))
_inner = {id(g): g for g in hull["geom_interior"] + hull["geom_unused"] if isinstance(g, bmesh.types.BMVert)}
bmesh.ops.delete(bmh, geom=list(_inner.values()), context="VERTS")
bvh_h = BVHTree.FromBMesh(bmh)
hd = np.zeros(nf)
for i in np.nonzero(hf)[0]:
    hd[i] = bvh_h.find_nearest(Vector(FC[i]))[3]
bmh.free()
head_c = Hv.mean(0)
Hz0, Hz1 = float(Hv[:, 2].min()), float(Hv[:, 2].max())
front_mask = hf & (FC[:, 1] < head_c[1]) & (np.abs(FC[:, 0] - head_c[0]) < 0.55) & (FC[:, 2] > Hz0 + 0.45 * (Hz1 - Hz0)) \
    & (FC[:, 2] < Hz0 + 0.80 * (Hz1 - Hz0))
dmax = float(hd[front_mask].max()) if front_mask.any() else 0.0
socket = front_mask & (hd > EYE_DEPTH_FRAC * dmax)
rid[socket] = R_["eye_socket"]
deep = socket & (hd > 0.60 * dmax)
rid[deep] = R_["eye_glow"]
report["eye_rule"] = {"rule": "head faces in the front half, 45-80% of the head height, deeper than EYE_DEPTH_FRAC x max below "
                              "the head's convex hull; glow = deeper than 60% of the max", "max_depth": round(dmax, 4),
                      "socket_faces": int(socket.sum()), "glow_faces": int(deep.sum()),
                      "socket_x_split": [int((socket & (FC[:, 0] < head_c[0])).sum()), int((socket & (FC[:, 0] >= head_c[0])).sum())]}

# ---- book (book-local frame = untilted, centred)
bf = FPART == PI["book"]
FNl = FN @ R_BOOK                    # world normal -> book-local (R^T n, row form)
FCl = (FC - book_c0 - np.array([0.0, -BOOK_FORWARD, BOOK_LIFT])) @ R_BOOK
bz0, bz1 = float(BOOK_LOCAL[:, 2].min()), float(BOOK_LOCAL[:, 2].max())
page = bf & (FNl[:, 2] > 0.45)
cover = bf & ((FNl[:, 2] < -0.35) | ((FNl[:, 2] <= 0.45) & (FCl[:, 2] < bz0 + 0.35 * (bz1 - bz0))))
rid[bf] = R_["book_edge"]
rid[cover] = R_["book_cover"]
rid[page] = R_["book_page"]
rune = page & (fcav > RUNE_CAV) & (np.abs(FCl[:, 0]) > GUTTER_W)
rid[rune] = R_["book_rune"]
report["book_rule"] = {"tilt_deg": BOOK_TILT_DEG, "page_faces": int(page.sum()), "rune_faces": int(rune.sum()),
                       "cover_faces": int((rid[bf] == R_["book_cover"]).sum()), "edge_faces": int((rid[bf] == R_["book_edge"]).sum()),
                       "rule": "book-local normals: up > 0.45 pages, down < -0.35 or low side band = cover, other sides = page "
                               "edges; runes = page faces with cavity > RUNE_CAV outside the gutter"}

# ---- robe: outer / front opening underlayer / trim (hem, opening edges, hood rim) / shadow (hood lining, underside)
rf = FPART == PI["robe"]
RV = parts["robe"]["V"]
rc = RV[RV[:, 2] < zs(6.0), :2].mean(0)
x0 = float(rc[0])
NB = 72
ang_v = np.arctan2(RV[:, 1] - rc[1], RV[:, 0] - rc[0])
bins_v = ((ang_v + math.pi) / (2 * math.pi) * NB).astype(int) % NB
hem = np.full(NB, np.inf)
np.minimum.at(hem, bins_v, RV[:, 2])
hem = np.where(np.isinf(hem), np.nanmin(hem[np.isfinite(hem)]), hem)
hem = np.minimum(hem, np.minimum(np.roll(hem, 1), np.roll(hem, -1)))
ang_f = np.arctan2(FC[:, 1] - rc[1], FC[:, 0] - rc[0])
bins_f = ((ang_f + math.pi) / (2 * math.pi) * NB).astype(int) % NB
hem_z = hem[bins_f]
z_hem_mean = float(hem.mean())
ztop = zs(UNDER_TOP_Z)
u = np.clip((ztop - FC[:, 2]) / max(ztop - z_hem_mean, 1e-6), 0, 1)
half_w = UNDER_HALF_W[0] + (UNDER_HALF_W[1] - UNDER_HALF_W[0]) * u
dx = np.abs(FC[:, 0] - x0)
front = rf & (FN[:, 1] < -0.15) & (FC[:, 1] < rc[1]) & (FC[:, 2] < ztop)
under = front & (dx < half_w)
edge = front & (dx >= half_w) & (dx < half_w + TRIM_EDGE_W)
rid[under] = R_["robe_under"]
rid[edge] = R_["trim"]
hemtrim = rf & (FC[:, 2] - hem_z < TRIM_HEM_H) & (FN[:, 2] > -0.6)
rid[hemtrim] = R_["trim"]
kd_head = KDTree(len(Hv))
for i, p in enumerate(Hv):
    kd_head.insert(p, i)
kd_head.balance()
near_head = np.zeros(nf, bool)
dh = np.full(nf, 1e9)
for i in np.nonzero(rf & (FC[:, 2] > Hz0 - 0.8))[0]:
    dh[i] = kd_head.find(FC[i])[2]
lining = rf & (dh < HOOD_SHADOW_D) & (FN[:, 1] < -0.25)          # front-facing only: the hood crown stays robe
rim = rf & (dh < HOOD_SHADOW_D + HOOD_RIM_W) & ~lining & (FN[:, 1] < 0.0) & (FC[:, 1] < head_c[1] + 0.2)
rid[rim] = R_["trim"]
rid[lining] = R_["robe_shadow"]
rid[rf & (FN[:, 2] < -0.6)] = R_["robe_shadow"]        # hem underside
report["robe_rule"] = {"hem_z_mean": round(z_hem_mean, 4), "opening_x": round(x0, 4), "opening_top_z": round(float(ztop), 4),
                       "under_faces": int(under.sum()), "trim_faces": int((rid == R_["trim"]).sum()),
                       "lining_faces": int(lining.sum()), "rim_faces": int(rim.sum())}

# cavity shade + deterministic per-face value jitter (improve_unit.py rule)
cav_k = 0.40
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["cavity_shade_k"] = cav_k

# facing landmark: head centroid -> eye-socket centroid (direction-free identity feature)
anchor = FC[hf].mean(0)
eyes_all = np.isin(rid, [R_["eye_socket"], R_["eye_glow"]])
landmark = FC[eyes_all].mean(0) if eyes_all.any() else anchor
dvec = landmark - anchor
report["facing"] = {"rule": "face-mask faces centroid -> eye-socket faces centroid", "anchor": anchor.round(4).tolist(),
                    "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2)}
report["geometry"] = geo

# =========================================================================== material
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
PAL.apply_material(mat, pal_default)
me.materials.append(mat)

low["conquest_unit"] = UNIT
low["conquest_character_id"] = "necromancer"
low["conquest_tier"] = "hero"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report[k] for k in ("tris_final", "tris_per_part", "redistribution", "regions_faces",
                                                         "eye_rule", "book_rule", "robe_rule", "facing")}))
    print("GEO", json.dumps(geo))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 5. bake
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
BG_NAMES = ["robe", "head", "book", "slab", "hands"]
BG_HIGH = {g: [HIGH[g]] for g in BG_NAMES if g != "hands"}
BG_HIGH["hands"] = [HIGH[p] for p in PART_NAMES if p.startswith("hand")]
FGRP = np.array(["hands" if PART_NAMES[i].startswith("hand") else PART_NAMES[i] for i in FPART], dtype=object)
report["bake_groups"] = {g: int((FGRP == g).sum()) for g in BG_NAMES}
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
assert (lt_ == 3).all(), "low must be all triangles"
UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for t in UVn[mask_faces]:
        p = t * res
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


def bake_pass(sel_objs, tag):
    for o in scene.objects:
        o.select_set(o in sel_objs or o is low)
    bpy.context.view_layer.objects.active = low
    im_n = bpy.data.images.new("bk_n_" + tag, RN, RN, alpha=False)
    im_n.colorspace_settings.name = "Non-Color"; im_n.generated_color = (0.0, 0.0, 0.0, 1.0)
    im_a = bpy.data.images.new("bk_a_" + tag, RA, RA, alpha=False)
    im_a.colorspace_settings.name = "Non-Color"; im_a.generated_color = (1.0, 0.0, 1.0, 1.0)
    tn.image, ta.image = im_n, im_a
    st = {}
    for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
        nt.nodes.active = node
        scene.cycles.samples = samples
        t = time.time()
        r = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
        st[typ] = {"result": sorted(r), "seconds": round(time.time() - t, 1)}
    pn = np.empty(RN * RN * 4, dtype=np.float32); im_n.pixels.foreach_get(pn)
    pa_ = np.empty(RA * RA * 4, dtype=np.float32); im_a.pixels.foreach_get(pa_)
    bpy.data.images.remove(im_n); bpy.data.images.remove(im_a)
    return pn.reshape(-1, 4), pa_.reshape(-1, 4), st


tb = time.time()
bstats = {}
px, pa, bstats["pass_all"] = bake_pass([o for g in BG_NAMES for o in BG_HIGH[g]], "all")
iso = {}
for g in BG_NAMES:
    fm_ = FGRP == g
    pn_g, pa_g, st_g = bake_pass(BG_HIGH[g], g)
    mn = texels_of(fm_, RN) & (pn_g[:, 2] > 0.25)
    ma = texels_of(fm_, RA) & (np.abs(pa_g[:, 0] - pa_g[:, 1]) < 0.02)
    px[mn] = pn_g[mn]; pa[ma] = pa_g[ma]
    iso[g] = {"faces": int(fm_.sum()), "normal_texels": int(mn.sum()), "ao_texels": int(ma.sum()),
              "seconds": round(st_g["NORMAL"]["seconds"] + st_g["AO"]["seconds"], 1)}
bstats["isolated_passes"] = iso
bstats["rule"] = ("base pass: whole low vs every high group (margins); then each group's texels re-baked against its own "
                  "high only (robe / head / book / slab / 20 arms), AO occluded by the whole scene; low hidden from rays")
tn.image, ta.image = img_n, img_ao
me.shade_flat()
dev = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "normal_baked_pct_of_uv_texels": round(100 * float(cov_n[alltex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((dev[cov_n & alltex_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(dev[cov_n & alltex_n].mean()), 4),
    "ao_baked_pct_of_uv_texels": round(100 * float(cov_a[alltex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & alltex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & alltex_a, 0], 5)), 4)})
per_region = {}
for n_ in REG:
    fm_ = rid == R_[n_]
    if fm_.any():
        tx = texels_of(fm_, RN)
        per_region[n_] = {"texels": int(tx.sum()), "baked_pct": round(100 * float(cov_n[tx].mean()), 2),
                          "normal_dev_mean": round(float(dev[tx & cov_n].mean()), 4) if (tx & cov_n).any() else None}
bstats["per_region_normal"] = per_region
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


bstats["pixel_sha"] = {"normal": sha(px[:, :3]), "ao": sha(pa[:, :1])}
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
for o in list(scene.objects):
    if o is not low:
        m_ = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if m_.users == 0:
            bpy.data.meshes.remove(m_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True

uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV after decimation (66 deg, margin 0.004), whole joined low, repacked", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()),
                "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
bpy.context.preferences.filepaths.save_version = 0
set_tex_paths("//textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))

# =========================================================================== 6. rig
rep = {"unit": UNIT, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
M = K.MeshData(low)
W = M.W
vpart = np.zeros(M.n, dtype=np.int64)
for f, pi in zip(LF, FPART):
    vpart[f] = pi
H_top = float(W[:, 2].max())
robe_v = vpart == PI["robe"]
axis_xy = W[robe_v & (W[:, 2] < zs(Z_NECK))][:, :2].mean(0)
hem_c = float(np.percentile(W[robe_v, 2], 1))
zc, zn = zs(Z_CHEST), zs(Z_NECK)
slab_v = vpart == PI["slab"]
slab_c = W[slab_v].mean(0)
book_v = vpart == PI["book"]
bc = (W[book_v].min(0) + W[book_v].max(0)) / 2

# carriers: arms whose fist lies under the robe (ray straight up from the fist top hits the robe high)
bvh_robe = BVHTree.FromPolygons(parts["robe"]["V"].tolist(), parts["robe"]["F"])
carrier = {}
for k, a in ARM.items():
    hit = bvh_robe.ray_cast(Vector(a["top"]) + Vector((0, 0, -0.05)), Vector((0, 0, 1)))
    carrier[k] = hit[0] is not None
BONES = [("grave", (slab_c[0], slab_c[1], 0.0), (slab_c[0], slab_c[1], float(W[slab_v, 2].max())), "root", True),
         ("body", (axis_xy[0], axis_xy[1], hem_c), (axis_xy[0], axis_xy[1], zc), "root", True),
         ("chest", (axis_xy[0], axis_xy[1], zc), (axis_xy[0], axis_xy[1], zn), "body", True),
         ("head", (axis_xy[0], axis_xy[1], zn), (axis_xy[0], axis_xy[1], H_top), "chest", True),
         ("book", (bc[0], bc[1], bc[2]), (bc[0], bc[1], bc[2] + 1.0), "root", True),
         ("dead_arms", (slab_c[0], slab_c[1], float(W[slab_v, 2].max())), (slab_c[0], slab_c[1], float(W[slab_v, 2].max()) + 1.0), "root", False)]
ARM_B = []
for k, a in ARM.items():
    nm = "dead_arm.%02d" % k
    BONES.append((nm, (a["base"][0], a["base"][1], a["base"][2]), (a["base"][0], a["base"][1], a["top"][2]), "dead_arms", True))
    ARM_B.append(nm)
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.15 * H_top); eb.use_deform = False
for (n, h, t, p, d) in BONES:
    e = arm_data.edit_bones.new(n)
    e.head = Vector(h); e.tail = Vector(t)
    e.parent = arm_data.edit_bones[p]
    e.use_deform = d
    e.use_connect = False
    e.roll = 0.0
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [n for (n, h, t, p, d) in BONES if d]
J = {n: j for j, n in enumerate(DEFORM)}

# ---- weights: rigid parts, robe z-band blends (<= 2 influences)
Wt = np.zeros((M.n, len(DEFORM)))
Wt[slab_v, J["grave"]] = 1.0
Wt[vpart == PI["head"], J["head"]] = 1.0
Wt[book_v, J["book"]] = 1.0
for k in range(20):
    Wt[vpart == PI["hand.%02d" % k], J["dead_arm.%02d" % k]] = 1.0
z = W[:, 2]
s1 = smoothstep(zc - 0.8, zc + 0.8, z)
s2 = smoothstep(zn - 1.0, zn - 0.2, z)
Wt[robe_v, J["body"]] = (1 - s1)[robe_v]
Wt[robe_v, J["chest"]] = np.clip(s1 - s2, 0, 1)[robe_v]
Wt[robe_v, J["head"]] = s2[robe_v]
Wt = np.where(Wt > 1e-4, Wt, 0.0)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
rep["weights"] = {"max_influences": int(infl.max()), "unweighted": int((infl == 0).sum()),
                  "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "rule": ("rigid parts: slab -> grave, face -> head, book -> book, arm k -> dead_arm.k (1.0); robe: z bands "
                           "body -> chest (Z_CHEST +-0.8) -> head (Z_NECK -1.0..-0.2, the hood rides the head)")}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig

# =========================================================================== 7. PROPOSED idle
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
N = IDLE_FRAMES
TAU = 2 * math.pi
GOLD = 2.399963


def idle_pose(t):
    q, loc = {}, {}
    b = HOVER_BOB * 0.5 * math.sin(TAU * t)                      # body: +-A/2 about rest
    loc["body"] = (0.0, b, 0.0)                                   # bone local Y = world +Z (vertical bones)
    q["body"] = Euler((0.0, 0.0, math.radians(BODY_SWAY_DEG * math.sin(TAU * t + 0.9))), "XYZ").to_quaternion()
    q["chest"] = Euler((math.radians(CHEST_BREATH_DEG * math.sin(TAU * t - 0.5)), 0.0, 0.0), "XYZ").to_quaternion()
    q["head"] = Euler((math.radians(-HEAD_DEG[0] * math.sin(TAU * t - 1.1)), math.radians(HEAD_DEG[1] * math.sin(TAU * t + 0.4)),
                       0.0), "XYZ").to_quaternion()
    loc["book"] = (0.0, BOOK_BOB * 0.5 * math.sin(TAU * t + 1.4), 0.0)
    q["book"] = Euler((math.radians(BOOK_TURN_DEG[0] * math.sin(TAU * t + 2.0)),
                       math.radians(BOOK_TURN_DEG[1] * math.sin(TAU * t + 0.3)),
                       math.radians(BOOK_TURN_DEG[2] * math.sin(2 * TAU * t + 0.7))), "XYZ").to_quaternion()
    for k, nm in enumerate(ARM_B):
        if carrier[k]:
            loc[nm] = (0.0, b, 0.0)                               # the carriers lift him: same heave, same phase
            q[nm] = Euler((0.0, 0.0, 0.0), "XYZ").to_quaternion()
        else:
            ph = k * GOLD
            loc[nm] = (0.0, ARM_HEAVE_PERIM * HOVER_BOB * 0.5 * math.sin(TAU * t - 0.35 + 0.3 * math.sin(ph)), 0.0)
            q[nm] = Euler((math.radians(ARM_SHIFT_DEG * math.sin(TAU * t + ph)), 0.0,
                           math.radians(ARM_SHIFT_DEG * math.sin(TAU * t + ph + 1.7))), "XYZ").to_quaternion()
    return q, loc


act = bpy.data.actions.new("idle")
act.use_fake_user = True
K.assign_action(rig, act)
prevq = {}
KEYED_R = ["body", "chest", "head", "book"] + ARM_B
KEYED_L = ["body", "book"] + ARM_B
for f in range(1, N + 2):
    t = ((f - 1) / N) % 1.0
    q, loc = idle_pose(t)
    for n in KEYED_R:
        qq = q[n]
        if n in prevq and prevq[n].dot(qq) < 0:
            qq.negate()
        prevq[n] = qq.copy()
        pose[n].rotation_quaternion = qq
        pose[n].keyframe_insert("rotation_quaternion", frame=f, group=n)
    for n in KEYED_L:
        pose[n].location = loc[n]
        pose[n].keyframe_insert("location", frame=f, group=n)
act.use_frame_range = True
act.frame_start, act.frame_end = 1, N + 1
act.use_cyclic = True


def coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


rest = W.copy()
first = last = None
slab_move = 0.0
min_z = 1e9
book_clear = 1e9
part_amp = {"robe": 0.0, "head": 0.0, "book": 0.0, "carriers": 0.0, "perimeter_arms": 0.0}
car_v = np.isin(vpart, [PI["hand.%02d" % k] for k in range(20) if carrier[k]])
per_v = np.isin(vpart, [PI["hand.%02d" % k] for k in range(20) if not carrier[k]])
not_book = np.nonzero(robe_v | (vpart == PI["head"]))[0]
book_idx = np.nonzero(book_v)[0]
hem_band = np.nonzero(robe_v & (W[:, 2] < hem_c + 1.2))[0]
carry_rel = 0.0
for f in range(1, N + 2):
    scene.frame_set(f)
    C = coords()
    if f == 1:
        first = C
    if f == N + 1:
        last = C
    D = np.linalg.norm(C - rest, axis=1)
    slab_move = max(slab_move, float(D[slab_v].max()))
    min_z = min(min_z, float(C[:, 2].min()))
    part_amp["robe"] = max(part_amp["robe"], float(D[robe_v].max()))
    part_amp["head"] = max(part_amp["head"], float(D[vpart == PI["head"]].max()))
    part_amp["book"] = max(part_amp["book"], float(D[book_v].max()))
    if car_v.any():
        part_amp["carriers"] = max(part_amp["carriers"], float(D[car_v].max()))
        # carrying contact: vertical motion of the carrier fists minus the hem band's (0 = they move as one)
        carry_rel = max(carry_rel, abs(float((C[car_v, 2] - rest[car_v, 2]).mean() - (C[hem_band, 2] - rest[hem_band, 2]).mean())))
    if per_v.any():
        part_amp["perimeter_arms"] = max(part_amp["perimeter_arms"], float(D[per_v].max()))
    if (f - 1) % 4 == 0:
        kd_f = KDTree(len(not_book))
        for i, v in enumerate(not_book):
            kd_f.insert(C[v], i)
        kd_f.balance()
        book_clear = min(book_clear, min(kd_f.find(C[v])[2] for v in book_idx[::2]))
rep["idle"] = {"status": "PROPOSED - the artist judges", "action": "idle", "frames": N + 1, "cycle_frames": N,
               "cycle_s": round(N / K.FPS, 3), "keyed_rotation": KEYED_R, "keyed_location": KEYED_L,
               "never_keyed": ["root", "grave", "dead_arms"],
               "seam_residual_mm": round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 6),
               "slab_motion_max": round(slab_move, 8), "min_z": round(min_z, 4),
               "book_clearance_min": round(book_clear, 4), "book_clearance_rest": geo["book_clearance_rest"],
               "carrier_vs_hem_vertical_mismatch_max": round(carry_rel, 6),
               "amplitude_max_displacement_sculpt_units": {k: round(v, 4) for k, v in part_amp.items()},
               "amplitude_pct_of_height": {k: round(100 * v / H_top, 3) for k, v in part_amp.items()},
               "carriers": [ARM_B[k] for k in range(20) if carrier[k]],
               "perimeter": [ARM_B[k] for k in range(20) if not carrier[k]],
               "motion": {"hover_bob": HOVER_BOB, "body_sway_deg": BODY_SWAY_DEG, "chest_breath_deg": CHEST_BREATH_DEG,
                          "head_deg": HEAD_DEG, "book_bob": BOOK_BOB, "book_turn_deg": BOOK_TURN_DEG,
                          "arm_shift_deg": ARM_SHIFT_DEG, "arm_heave_perimeter": ARM_HEAVE_PERIM, "harmonics": [1, 2]}}
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
K.assign_action(rig, act)
scene.frame_set(1)
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_start, scene.frame_end = 1, N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rig["conquest_rig"] = "mortis v1: trunk/head chain, book float bone, dead_arms group + 20 carrying-arm bones"
low["conquest_clips"] = ["idle"]
low["conquest_clip_status"] = "idle PROPOSED; walk NOT AUTHORED (gait 'glide' unconfirmed); no attacks"
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "seconds")}))
print("IDLE", json.dumps(rep["idle"]))
sys.stdout.flush()
os._exit(0)
