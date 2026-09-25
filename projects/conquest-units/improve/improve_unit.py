"""Conquest unit visual-improvement wave: facing, geometry fixes, retopo, UVs, colour regions.

    blender --background <source-copies/X.blend> --factory-startup --python improve_unit.py -- <unit> <out.blend> <out.json>

Opens a BYTE COPY from source-copies/ (never an original), builds a new game-scale mesh,
and writes it with save_as_mainfile to improved/ -- the opened copy is never saved over.
NO animation, no rig: geometry + colour only (artist deferred animation 2026-09-24).

Pipeline per unit (every step deterministic: collapse decimate, seeded RNG, no Quadriflow):
  1. world-space mesh (authored object scale applied -- the ancient tree ships at
     2.509 x 2.238 x 5.325), then a Z rotation so the TRUE front is -Y (Conquest convention).
  2. geometry fixes on the high mesh (numpy Taubin smoothing, weighted to the named defect).
  3. scale to the cell budget (min of height / footprint factor, as prepare_unit.py does),
     origin at the feet, centred in X/Y.
  4. collapse-decimate to the tier budget, uniform (quadric cost already keeps carved detail
     denser; a Decimate vertex group was measured to STOP decimation instead: 481k -> 442k).
  5. flat shading, Smart-UV unwrap AFTER decimation.
  6. colour regions from geometric bands (height / radial / normal / cavity), written per FACE
     to a CORNER colour attribute 'Col' (faceted look) + 'Glow' for emissive regions.
  7. Eldroot only: normal (detail, vs the smooth low) + AO bake from the fixed high mesh.
"""
import bpy, bmesh, sys, os, math, json, random, time
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.kdtree import KDTree

T0 = time.time()
argv = sys.argv[sys.argv.index("--") + 1:]
UNIT, OUT_BLEND, OUT_JSON = argv[0], argv[1], argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))


def srgb(r, g, b):
    """sRGB 0-255 (what the artist sees / what the report quotes) -> linear float."""
    def c(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return np.array([c(r), c(g), c(b)])


# --------------------------------------------------------------------------- unit table
# yaw_fix_deg: Z rotation applied so the true front lands on -Y (see facing_audit.py output
# and the report). max_h / max_fp: Conquest's tools/blender/assets.conf ceilings.
# tris: declared tier budget (crowd <= 5000; boss 20-30k, target 25k).
UNITS = {
    "barkling":   dict(src="tree_grunt",     yaw=0.0,   max_h=1.8, max_fp=1.9, tris=5000,  tier="crowd", budget=[3000, 5000]),
    "petalfang":  dict(src="flower_grunt",   yaw=180.0, max_h=1.8, max_fp=1.9, tris=4500,  tier="crowd", budget=[3000, 5000]),
    "blightcap":  dict(src="shroom_grunt",   yaw=180.0, max_h=1.8, max_fp=1.9, tris=5000,  tier="crowd", budget=[3000, 5000]),
    "mycothrall": dict(src="parasite_grunt", yaw=0.0,   max_h=1.8, max_fp=1.9, tris=4000,  tier="crowd", budget=[3000, 5000]),
    "eldroot":    dict(src="ancient_tree",   yaw=0.0,   max_h=3.4, max_fp=3.8, tris=25000, tier="boss",  budget=[20000, 30000]),
}
U = UNITS[UNIT]
# Petalfang head centre, audited from the +X/+Y closeups + central-column profile of the source:
# head spans y -0.19..0.94 at z 2.25-3.0 (centre y~0.4), lower jaw reaches y 1.22 at z 2.0-2.25.
# After the 180 yaw fix: (0, -0.4, 2.5) in source units.
HEAD_C = np.array([0.0, -0.4, 2.5])
report = {"unit": UNIT, "source": bpy.data.filepath, "yaw_fix_deg": U["yaw"], "tier": U["tier"],
          "tri_budget": U["budget"], "max_height": U["max_h"], "max_footprint": U["max_fp"]}


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    t = np.asarray(t)[..., None] if np.ndim(t) else t
    return a * (1 - t) + b * t


# --------------------------------------------------------------------------- 1. world mesh
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
src = max([o for o in bpy.data.objects if o.type == "MESH"], key=lambda o: len(o.data.vertices))
me_hi = src.data.copy()
me_hi.name = UNIT + "_high"
me_hi.transform(src.matrix_world)
me_hi.transform(Matrix.Rotation(math.radians(U["yaw"]), 4, "Z"))
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
scene = bpy.context.scene
hi = bpy.data.objects.new(UNIT + "_high", me_hi)
scene.collection.objects.link(hi)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
# the high mesh is geometry only: drop stale sculpt UVs / colour layers
while me_hi.uv_layers:
    me_hi.uv_layers.remove(me_hi.uv_layers[0])


def read_mesh(me):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co)
    ev = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev)
    return co.reshape(-1, 3), ev.reshape(-1, 2)


def write_co(me, W):
    me.vertices.foreach_set("co", W.ravel())
    me.update()


def vnormals(me):
    nv = np.empty(len(me.vertices) * 3); me.vertex_normals.foreach_get("vector", nv)
    return nv.reshape(-1, 3)


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def neighbour_mean(W, ev, deg):
    s = np.zeros_like(W)
    for k in range(W.shape[1]):
        s[:, k] = np.bincount(ev[:, 0], W[ev[:, 1], k], minlength=len(W)) + np.bincount(ev[:, 1], W[ev[:, 0], k], minlength=len(W))
    return s / np.maximum(deg, 1)[:, None]


def taubin(W, ev, w, iters, lam=0.5, mu=-0.53):
    """Volume-preserving (lambda|mu) smoothing, per-vertex weight w in [0,1]."""
    deg = np.bincount(ev.ravel(), minlength=len(W)).astype(float)
    W = W.copy()
    ww = w[:, None]
    for _ in range(iters):
        W += lam * ww * (neighbour_mean(W, ev, deg) - W)
        W += mu * ww * (neighbour_mean(W, ev, deg) - W)
    return W


def roughness(W, ev, mask):
    """Mean |Laplacian| / mean edge length over mask -- the stair-step / streak metric."""
    deg = np.bincount(ev.ravel(), minlength=len(W)).astype(float)
    lap = np.linalg.norm(neighbour_mean(W, ev, deg) - W, axis=1)
    el = np.linalg.norm(W[ev[:, 0]] - W[ev[:, 1]], axis=1).mean()
    return round(float(lap[mask].mean() / el), 4)


W, EV = read_mesh(me_hi)
report["tris_source"] = tri_count(me_hi)
lo, hi_ = W.min(0), W.max(0)
H0 = hi_[2] - lo[2]
report["source_bbox_after_rotation"] = [lo.round(3).tolist(), hi_.round(3).tolist()]

# --------------------------------------------------------------------------- 2. geometry fixes
fix = {}
h = (W[:, 2] - lo[2]) / H0
NRM = vnormals(me_hi)
if UNIT == "blightcap":
    # cap-rim voxel stair-steps: rim = cap band (h > 0.55) at large radius from the stalk axis.
    cx, cy = (lo[0] + hi_[0]) / 2, (lo[1] + hi_[1]) / 2
    r = np.hypot(W[:, 0] - cx, W[:, 1] - cy)
    rmax = r[h > 0.55].max()
    w_rim = smoothstep(0.55, 0.72, h) * smoothstep(0.62 * rmax, 0.80 * rmax, r)
    rim = w_rim > 0.5
    fix["rim_vertices"] = int(rim.sum())
    fix["rim_roughness_before"] = roughness(W, EV, rim)
    fix["global_roughness_before"] = roughness(W, EV, np.ones(len(W), bool))
    W = taubin(W, EV, np.maximum(w_rim, 0.35), 12)      # light everywhere (voxel steps), full at rim
    W = taubin(W, EV, w_rim, 36)                          # rim only
    fix["rim_roughness_after"] = roughness(W, EV, rim)
    fix["global_roughness_after"] = roughness(W, EV, np.ones(len(W), bool))
    fix["rule"] = "rim weight = smoothstep(h,0.55,0.72) * smoothstep(r,0.62rmax,0.80rmax); Taubin 12 it (weight max(w,0.35)) + 36 it (rim)"
elif UNIT == "mycothrall":
    allm = np.ones(len(W), bool)
    fix["global_roughness_before"] = roughness(W, EV, allm)
    W = taubin(W, EV, np.ones(len(W)), 8)
    fix["global_roughness_after"] = roughness(W, EV, allm)
    fix["rule"] = "voxel stair-steps: Taubin 8 it, whole mesh"
elif UNIT == "eldroot":
    # vertical smear streaks on the head sides: head band, side-facing normals (|n.x| high),
    # which excludes the -Y face (eye hollows, mouth) and the +Y back.
    w_side = smoothstep(0.50, 0.58, h) * smoothstep(0.30, 0.60, np.abs(NRM[:, 0]))
    side = w_side > 0.5
    fix["side_vertices"] = int(side.sum())
    fix["side_roughness_before"] = roughness(W, EV, side)
    W = taubin(W, EV, w_side, 60)
    fix["side_roughness_after"] = roughness(W, EV, side)
    ctrl = (h > 0.6) & (NRM[:, 1] < -0.7)
    fix["face_front_roughness_untouched_check"] = roughness(W, EV, ctrl)
    fix["rule"] = "side weight = smoothstep(h,0.50,0.58) * smoothstep(|n.x|,0.30,0.60); Taubin 60 it"
elif UNIT == "petalfang":
    # snake head: widen (x 1.22) and flatten (z 0.92) the head about its centre -- suggest, not resculpt.
    # head centre from the facing audit (+X closeup): source (0, 0.6, 2.45) -> after 180 yaw (0, -0.6, 2.45)
    hc = HEAD_C
    d = np.linalg.norm((W - hc) * np.array([1.0, 1.0, 1.3]), axis=1)
    w_head = 1.0 - smoothstep(0.55, 0.95, d)
    w_head *= smoothstep(1.85, 2.15, W[:, 2])            # keep the neck
    fix["head_vertices_weighted"] = int((w_head > 0.5).sum())
    W[:, 0] = W[:, 0] + w_head * (W[:, 0] - hc[0]) * 0.40
    W[:, 1] = W[:, 1] + w_head * (W[:, 1] - hc[1]) * 0.25
    W[:, 2] = W[:, 2] + w_head * (W[:, 2] - hc[2]) * 0.15
    fix["rule"] = "head weight = (1-smoothstep(d,0.55,0.95)) * smoothstep(z,1.85,2.15), d = |(p-hc)*(1,1,1.3)|; scale x1.40 y1.25 z1.15 about the audited head centre (0,-0.4,2.5)"
report["geometry_fix"] = fix
write_co(me_hi, W)

# --------------------------------------------------------------------------- 3. cell budget
lo, hi_ = W.min(0), W.max(0)
size = hi_ - lo
fh, ff = U["max_h"] / size[2], U["max_fp"] / max(size[0], size[1])
k = min(fh, ff)
report["scale_factor"] = round(float(k), 6)
report["bound_by"] = "height" if fh <= ff else "footprint"
W = (W - np.array([(lo[0] + hi_[0]) / 2, (lo[1] + hi_[1]) / 2, lo[2]])) * k
write_co(me_hi, W)
H = W[:, 2].max()
h = W[:, 2] / H
NRM = vnormals(me_hi)

# front landmark (anchor -> landmark direction = the identity feature's facing); stored for the checker
deg = np.bincount(EV.ravel(), minlength=len(W)).astype(float)
LAP = np.linalg.norm(neighbour_mean(W, EV, deg) - W, axis=1)
halfw = max(W[:, 0].max(), -W[:, 0].min())
if UNIT in ("barkling", "eldroot"):
    sel = (h > 0.8) & (np.abs(W[:, 0]) < 0.2 * 2 * halfw)
    anchor = W[sel].mean(0)
    wt = LAP[sel] ** 2
    landmark = (W[sel] * wt[:, None]).sum(0) / wt.sum()
    rule = "head (h>0.8, |x|<0.2 width): anchor = centroid, landmark = Laplacian^2-weighted centroid (carved face)"
elif UNIT == "petalfang":
    C0 = np.array([(lo[0] + hi_[0]) / 2, (lo[1] + hi_[1]) / 2, lo[2]])
    HC = (HEAD_C - C0) * k                                  # head centre, game units
    zb = (np.array([1.95, 2.30]) - lo[2]) * k                 # lower-jaw band (source z 1.95-2.30)
    band = (W[:, 2] >= zb[0]) & (W[:, 2] <= zb[1])
    rad = np.hypot(W[:, 0] - HC[0], W[:, 1] - HC[1])
    sel = band & (rad < 1.0 * k) & (np.abs(W[:, 0] - HC[0]) < 0.35 * k)
    anchor = np.array([HC[0], HC[1], W[sel, 2].mean()])
    landmark = W[np.nonzero(sel)[0][int(np.argmax(rad[sel]))]]
    rule = ("lower-jaw band (source z 1.95-2.30, within 1.0 src units of the head axis, |dx|<0.35): "
            "anchor = head axis, landmark = farthest vertex from the axis (the jaw tip)")
elif UNIT == "blightcap":
    # the carved face (closed eyes + mouth) sits on the body just under the cap, found on the
    # improved render sheet; the lean (feet -> crown) is recorded as corroboration.
    rr_ = np.hypot(W[:, 0], W[:, 1])
    rcap_hi = rr_[h > 0.6].max()
    sel = (h > 0.40) & (h < 0.75) & (rr_ < 0.62 * rcap_hi)
    anchor = W[sel].mean(0)
    wt = LAP[sel] ** 2
    landmark = (W[sel] * wt[:, None]).sum(0) / wt.sum()
    feet = W[h < 0.12].mean(0); rimc = W[(h > 0.60) & (h < 0.70)].mean(0); crown = W[h > 0.90].mean(0)
    dl = crown - rimc
    report["blightcap_lean"] = {"feet_xy": feet[:2].round(4).tolist(), "cap_rim_xy": rimc[:2].round(4).tolist(),
                                "crown_xy": crown[:2].round(4).tolist(),
                                "rim_to_crown_angle_from_minusY_deg": round(math.degrees(math.atan2(dl[0], -dl[1])), 1)}
    rule = ("body band under the cap (h 0.40-0.75, r < 0.62 cap radius): anchor = centroid, landmark = "
            "Laplacian^2-weighted centroid (carved eyes + mouth)")
else:
    anchor = W.mean(0)
    dxy = np.hypot(W[:, 0] - anchor[0], W[:, 1] - anchor[1])
    landmark = W[int(np.argmax(dxy))]
    rule = "vertex centroid -> farthest XY vertex (the head/maw protrusion)"
HCn = HC.copy() if UNIT == "petalfang" else anchor.copy()
dvec = landmark - anchor
Rz = np.array(Matrix.Rotation(math.radians(-U["yaw"]), 3, "Z"))
d_src = Rz @ dvec
report["facing"] = {
    "rule": rule,
    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
    "source_angle_from_minusY_deg": round(math.degrees(math.atan2(d_src[0], -d_src[1])), 1),
    "final_angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 1),
}

# cavity field on the high mesh: + = concave (pit/furrow), - = convex (ridge); smoothed 6x
el = np.linalg.norm(W[EV[:, 0]] - W[EV[:, 1]], axis=1).mean()
CAV = ((neighbour_mean(W, EV, deg) - W) * NRM).sum(1) / el
for _ in range(6):
    CAV = neighbour_mean(CAV[:, None], EV, deg)[:, 0] * 0.5 + CAV * 0.5
cs = np.percentile(np.abs(CAV), 95) + 1e-9
CAV = np.clip(CAV / cs, -1, 1)

# --------------------------------------------------------------------------- 4. decimate
tris_hi = tri_count(me_hi)
mod = hi.modifiers.new("decimate", "DECIMATE")
mod.decimate_type = "COLLAPSE"
mod.ratio = min(1.0, U["tris"] / tris_hi)
mod.use_collapse_triangulate = True
dg = bpy.context.evaluated_depsgraph_get()
me_lo = bpy.data.meshes.new_from_object(hi.evaluated_get(dg))
me_lo.name = UNIT + "_mesh"
hi.modifiers.remove(mod)
low = bpy.data.objects.new(UNIT, me_lo)
scene.collection.objects.link(low)
low.vertex_groups.clear()

# --------------------------------------------------------------------------- petalfang thorns (after decimation)
n_body_faces = len(me_lo.polygons)
if UNIT == "petalfang":
    # port of Conquest prepare_unit.add_thorns (seed 1337, 40 spikes, same scoring/separation),
    # added AFTER decimation so the collapse cannot eat the spikes.
    rng = random.Random(1337)
    bm = bmesh.new(); bm.from_mesh(me_lo); bm.faces.ensure_lookup_table()
    fc = np.array([f.calc_center_median() for f in bm.faces]); fn = np.array([f.normal[:] for f in bm.faces])
    com = np.array([v.co[:] for v in bm.verts]).mean(0)
    off = fc - com
    dd = np.linalg.norm(off, axis=1); rr = np.hypot(off[:, 0], off[:, 1])
    s = 0.5 * dd / dd.max() + 0.5 * rr / rr.max()
    pool = np.nonzero((s >= s.max() * 0.5) & (np.linalg.norm(fc - HCn, axis=1) > 1.6 * k))[0]   # keep thorns off the head
    wts = s[pool] ** 2
    cw = np.cumsum(wts).tolist()
    ext = np.array([v.co[:] for v in bm.verts]); maxdim = (ext.max(0) - ext.min(0)).max()
    chosen = []; min_sep = maxdim * 0.05; att = 0
    while len(chosen) < 40 and att < 16000:
        att += 1
        i = rng.choices(pool.tolist(), cum_weights=cw, k=1)[0]
        c = fc[i]
        if all(np.linalg.norm(c - pc) >= min_sep for pc, _ in chosen):
            chosen.append((c, fn[i]))
        elif att % 1600 == 0:
            min_sep *= 0.7
    base_len = maxdim * 0.038
    thorn_verts_start = len(bm.verts)
    for c, nrm in chosen:
        L = base_len * rng.uniform(0.75, 1.35)
        rad = L * rng.uniform(0.20, 0.30)
        q = Vector(nrm).to_track_quat("Z", "Y")
        q = q @ Quaternion((0, 0, 1), rng.uniform(0, math.tau))
        ax = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0)).normalized()
        q = q @ Quaternion(ax, rng.uniform(0.0, 0.30))
        loc = Vector(c) + (q @ Vector((0, 0, 1))) * (L * 0.25)
        M = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True, segments=6, radius1=rad, radius2=0.0, depth=L, matrix=M)
    bm.to_mesh(me_lo); bm.free()
    report["thorns"] = {"placed": len(chosen), "len_base": round(base_len, 4)}

# --------------------------------------------------------------------------- 5. flat shading + UVs
me_lo.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.object.mode_set(mode="OBJECT")

# feet exactly at origin after decimation (collapse can lift the lowest tip a hair)
Wl, EVl = read_mesh(me_lo)
shift = np.array([(Wl[:, 0].min() + Wl[:, 0].max()) / 2, (Wl[:, 1].min() + Wl[:, 1].max()) / 2, Wl[:, 2].min()])
Wl -= shift
write_co(me_lo, Wl)
W -= shift
write_co(me_hi, W)
anchor -= shift; landmark -= shift; HCn -= shift
# re-clamp: thorns / head shaping can push the footprint past the ceiling after the first scale
ext_ = Wl.max(0) - Wl.min(0)
s2 = min(1.0, U["max_fp"] / max(ext_[0], ext_[1]), U["max_h"] / ext_[2])
if s2 < 1.0:
    Wl *= s2; W *= s2; anchor *= s2; landmark *= s2; HCn *= s2; k *= s2
    write_co(me_lo, Wl); write_co(me_hi, W)
report["reclamp_factor"] = round(float(s2), 6)

# --------------------------------------------------------------------------- 6. colour regions
nf = len(me_lo.polygons)
FC = np.empty(nf * 3); me_lo.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
FN = np.empty(nf * 3); me_lo.polygon_normals.foreach_get("vector", FN); FN = FN.reshape(-1, 3)
Hl = Wl[:, 2].max()
fh_ = FC[:, 2] / Hl
kd = KDTree(len(W))
for i, p in enumerate(W):
    kd.insert(p, i)
kd.balance()
fcav = np.array([np.mean([CAV[j] for (_, j, _) in kd.find_n(p, 6)]) for p in FC])
is_thorn = np.zeros(nf, bool)
if UNIT == "petalfang":
    is_thorn[n_body_faces:] = True
fx, fy, fz = FC[:, 0], FC[:, 1], FC[:, 2]
halfw_l = max(Wl[:, 0].max(), -Wl[:, 0].min())
fr = np.hypot(fx, fy) / max(np.hypot(Wl[:, 0], Wl[:, 1]).max(), 1e-9)
col = np.zeros((nf, 3)); glow = np.zeros((nf, 3))
regions = {}
PAL = {}


def region(name, mask, rgb, soft=None):
    """Assign a region colour. soft (0..1 per face) blends instead of replacing."""
    global col
    c = srgb(*rgb)
    PAL[name] = list(rgb)
    if soft is None:
        col[mask] = c
        regions[name] = int(mask.sum())
    else:
        t = np.where(mask, soft, 0.0)
        col = col * (1 - t[:, None]) + c * t[:, None]
        regions[name] = int((t > 0.5).sum())


cav_k = 0.40
if UNIT == "barkling":
    region("trunk_bark", np.ones(nf, bool), (126, 88, 56))
    region("branch_bark", np.ones(nf, bool), (142, 102, 64), smoothstep(0.18, 0.30, np.abs(fx) / halfw_l) * smoothstep(0.40, 0.50, fh_))
    region("root_legs", np.ones(nf, bool), (84, 58, 40), 1 - smoothstep(0.12, 0.22, fh_))
    region("leaf_buds", np.ones(nf, bool), (112, 142, 62), smoothstep(0.72, 0.86, np.abs(fx) / halfw_l))
    face = (fh_ > 0.74) & (np.abs(fx) < 0.24 * halfw_l)
    region("heartwood_face", face, (190, 150, 100), smoothstep(0.25, 0.6, -FN[:, 1]))
    region("crown_moss", np.ones(nf, bool), (96, 126, 58), smoothstep(0.93, 0.97, fh_) * smoothstep(0.3, 0.6, FN[:, 2]))
    region("carved_features", face & (fcav > 0.25), (46, 30, 20), smoothstep(0.25, 0.55, fcav))
    cav_k = 0.45
elif UNIT == "petalfang":
    headc = HCn
    RH = 0.72 * k                      # audited head radius ~0.6 source units x1.2 (enlarge), in game units
    dh = np.linalg.norm(FC - headc, axis=1)
    region("calyx_body", np.ones(nf, bool), (70, 104, 50))
    region("tendril_vine", np.ones(nf, bool), (96, 140, 62), smoothstep(0.22, 0.40, fr))
    region("petal_tips", np.ones(nf, bool), (226, 98, 60), smoothstep(0.58, 0.88, fh_) * smoothstep(0.32, 0.42, fr))
    neck = (np.abs(fx) < 0.45 * RH) & (np.abs(fy - headc[1]) < 1.2 * RH) & (fz > 0.25 * Hl) & (dh > 1.0 * RH)
    region("serpent_neck", neck, (196, 186, 104), 0.85 * np.ones(nf))
    head_t = 1 - smoothstep(1.1 * RH, 1.5 * RH, dh)
    region("serpent_head", np.ones(nf, bool), (226, 208, 122), head_t)
    region("serpent_dorsal", np.ones(nf, bool), (92, 70, 44), head_t * smoothstep(0.45, 0.80, FN[:, 2]))
    # eyes: on the upper head sides -- the head face (above the head centre) most aligned with
    # (+-sin70 cos20, -cos70 cos20, sin20); a more forward direction lands on the jaw tip instead.
    eyes = np.zeros(nf, bool)
    hm = np.nonzero((dh < 1.5 * RH) & (fz > headc[2]))[0]
    for sx in (-1, 1):
        dvec_e = np.array([sx * math.sin(math.radians(70)) * math.cos(math.radians(20)), -math.cos(math.radians(70)) * math.cos(math.radians(20)), math.sin(math.radians(20))])
        proj = (FC[hm] - headc) @ dvec_e
        tip = FC[hm[int(np.argmax(proj))]]
        eyes |= (np.linalg.norm(FC - tip, axis=1) < 0.22 * RH) & (dh < 1.5 * RH)
    region("eyes", eyes, (22, 16, 12))
    report["head_radius_game_units"] = round(float(RH), 4)
    mouth = (dh < 1.4 * RH) & (fcav > 0.25) & (dh < 1.1 * RH) & (fy < headc[1] - 0.3 * RH) & (fz > headc[2] - 0.6 * RH) & (fz < headc[2] + 0.4 * RH)
    region("mouth", mouth, (150, 22, 42))
    region("thorns", is_thorn, (236, 226, 196))
    cav_k = 0.35
elif UNIT == "blightcap":
    cap_lo = 0.58
    region("stalk_body", np.ones(nf, bool), (216, 202, 168))
    region("root_legs", np.ones(nf, bool), (126, 98, 70), 1 - smoothstep(0.10, 0.18, fh_))
    region("arm_tips", np.ones(nf, bool), (170, 150, 118), smoothstep(0.62, 0.80, np.abs(fx) / halfw_l) * (fh_ < cap_lo))
    rf = np.hypot(fx, fy)
    rcap = rf[fh_ > 0.6].max()
    under = (fh_ > 0.55) & (FN[:, 2] < -0.25) & (rf > 0.50 * rcap)
    top = ((fh_ > 0.72) | ((fh_ > cap_lo) & (rf > 0.78 * rcap))) & ~under
    th = np.arctan2(fy, fx)
    gill = 0.82 + 0.18 * (np.sin(th * 44) > 0)
    region("cap_top", top, (172, 72, 50))
    region("cap_crown", top, (138, 54, 40), smoothstep(0.86, 0.99, fh_))
    region("gills", under, (122, 88, 74))
    col[under] *= gill[under][:, None]
    # spots: Vogel spiral in the cap's XY disk (golden angle), deterministic
    R = np.hypot(fx[top], fy[top]).max()
    ga = math.pi * (3 - math.sqrt(5))
    pts = np.array([[math.sqrt((i + 0.5) / 18) * 0.86 * R * math.cos(i * ga + 0.4), math.sqrt((i + 0.5) / 18) * 0.86 * R * math.sin(i * ga + 0.4)] for i in range(18)])
    dsp = np.min(np.linalg.norm(FC[:, None, :2] - pts[None], axis=2), axis=1)
    spots = top & (dsp < 0.085 * R) & (FN[:, 2] > 0.15)
    region("spots", spots, (236, 224, 190))
    feat = ~top & ~under & (fh_ > 0.40) & (fh_ < 0.74) & (fy < 0) & (fcav > 0.25)
    region("face_features", feat, (58, 40, 34), smoothstep(0.25, 0.5, fcav))
    cav_k = 0.30
elif UNIT == "mycothrall":
    region("body", np.ones(nf, bool), (152, 48, 122))
    region("underside", np.ones(nf, bool), (92, 26, 78), 1 - smoothstep(-0.2, 0.25, FN[:, 2]))
    ymin = Wl[:, 1].min()
    head = fy < ymin + 0.24 * (Wl[:, 1].max() - ymin)
    region("maw_head", head, (255, 130, 152), smoothstep(0.0, 1.0, (ymin + 0.24 * (Wl[:, 1].max() - ymin) - fy) / 0.12))
    region("maw_inner", head & (fcav > 0.3), (170, 16, 48))
    bc = np.array([0.0, ymin + 0.24 * (Wl[:, 1].max() - ymin)])
    th = np.arctan2(fx - bc[0], fy - bc[1])
    rn = np.hypot(fx - bc[0], fy - bc[1]) / (Wl[:, 1].max() - ymin)
    ph = (th * 11 / (2 * math.pi) + 0.18 * np.sin(rn * 9.0)) % 1.0
    threads = (np.abs(ph - 0.5) < 0.11) & (FN[:, 2] > 0.1) & ~head & (fh_ > 0.15)
    region("threads", threads, (206, 255, 74))
    glow[threads] = srgb(206, 255, 74) * 0.35
    PAL["threads_emission_x0.35"] = [206, 255, 74]
    cav_k = 0.30
elif UNIT == "eldroot":
    region("old_bark", np.ones(nf, bool), (94, 78, 64))
    region("root_feet", np.ones(nf, bool), (66, 52, 44), 1 - smoothstep(0.22, 0.34, fh_))
    region("moss", np.ones(nf, bool), (92, 118, 60), smoothstep(0.50, 0.75, FN[:, 2]) * smoothstep(0.15, 0.30, fh_) * (fh_ < 0.93) * (fcav < 0.2) * ((fh_ < 0.55) | (fy > 0)))
    region("deadwood_crown", np.ones(nf, bool), (152, 140, 118), smoothstep(0.92, 0.97, fh_))
    # eye hollows: depth rule on the HIGH mesh (front envelope), transferred to faces
    hv = W[:, 2] / H
    headm = (hv > 0.70) & (hv < 0.90)
    Ph = W[headm]
    xb = np.linspace(Ph[:, 0].min(), Ph[:, 0].max(), 41); zb = np.linspace(Ph[:, 2].min(), Ph[:, 2].max(), 21)
    ix = np.clip(np.digitize(Ph[:, 0], xb) - 1, 0, 39); iz = np.clip(np.digitize(Ph[:, 2], zb) - 1, 0, 19)
    gmin = np.full((20, 40), np.inf)
    np.minimum.at(gmin, (iz, ix), Ph[:, 1])
    yref = np.percentile(gmin[np.isfinite(gmin)], 15)
    depth = 0.045 * H
    fix_ = np.clip(np.digitize(fx, xb) - 1, 0, 39); fiz = np.clip(np.digitize(fz, zb) - 1, 0, 19)
    in_band = (fz / Hl > 0.72) & (fz / Hl < 0.88)
    binmin = gmin[fiz, fix_]
    hollow_bin = in_band & np.isfinite(binmin) & (binmin > yref + depth) & (np.abs(fx) > 0.06 * halfw_l) & (np.abs(fx) < 0.29 * halfw_l)
    eyes = hollow_bin & (fy < binmin + 0.10 * H)
    region("hollow_dark", (fcav > 0.35) & (fh_ > 0.45), (30, 22, 18), smoothstep(0.35, 0.7, fcav))
    region("eye_glow", eyes, (40, 60, 20))
    glow[eyes] = srgb(170, 255, 70)
    PAL["eye_glow_emission"] = [170, 255, 70]
    report["eye_rule"] = {"yref": round(float(yref), 4), "depth": round(float(depth), 4), "faces": int(eyes.sum())}
    cav_k = 0.45

# cavity shading (furrows darker, ridges slightly lighter) + deterministic per-face value jitter
shade = 1.0 - cav_k * np.clip(fcav, 0, 1) + 0.08 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= 0.96 + 0.08 * jit
emissive = glow.sum(1) > 0
col[~emissive] *= shade[~emissive, None]
col = np.clip(col, 0, 1)

for nm in ("Col", "Glow"):
    if nm in me_lo.color_attributes:
        me_lo.color_attributes.remove(me_lo.color_attributes[nm])
ca = me_lo.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
ga_ = me_lo.color_attributes.new("Glow", "FLOAT_COLOR", "CORNER")
lt = np.empty(nf, dtype=np.int64); me_lo.polygons.foreach_get("loop_total", lt)
col4 = np.repeat(np.hstack([col, np.ones((nf, 1))]), lt, axis=0)
glow4 = np.repeat(np.hstack([glow, np.ones((nf, 1))]), lt, axis=0)
ca.data.foreach_set("color", col4.ravel())
ga_.data.foreach_set("color", glow4.ravel())
me_lo.color_attributes.active_color = ca
me_lo.color_attributes.render_color_index = me_lo.color_attributes.find("Col")
report["regions_faces"] = regions
report["palette_srgb"] = PAL
report["cavity_shade_k"] = cav_k

# --------------------------------------------------------------------------- material
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
rough = {"mycothrall": 0.22, "petalfang": 0.55, "blightcap": 0.6}.get(UNIT, 0.85)
bsdf.inputs["Roughness"].default_value = rough
if UNIT == "mycothrall":
    bsdf.inputs["Coat Weight"].default_value = 0.8
    bsdf.inputs["Coat Roughness"].default_value = 0.12
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
bsdf.inputs["Emission Strength"].default_value = 1.4 if UNIT == "eldroot" else 1.5
report["material"] = {"roughness": rough, "coat": 0.8 if UNIT == "mycothrall" else 0.0,
                      "emission_strength": bsdf.inputs["Emission Strength"].default_value}
me_lo.materials.append(mat)

# --------------------------------------------------------------------------- 7. eldroot bake
if UNIT == "eldroot":
    tex_dir = os.path.join(os.path.dirname(OUT_BLEND), "textures"); os.makedirs(tex_dir, exist_ok=True)
    try:
        import addon_utils
        addon_utils.enable("cycles", default_set=False, persistent=False)
    except Exception:
        pass
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.use_denoising = False
    bake = scene.render.bake
    ext = float(max(Wl.max(0) - Wl.min(0))) * 0.02
    bake.use_selected_to_active = True
    bake.cage_extrusion = ext
    bake.max_ray_distance = ext * 2.0
    bake.margin = 16
    bake.use_clear = False   # images are pre-filled with a sentinel so un-baked texels are measurable
    hi.hide_render = False
    img_n = bpy.data.images.new("eldroot_normal", 2048, 2048, alpha=False)
    img_n.colorspace_settings.name = "Non-Color"
    img_n.generated_color = (0.0, 0.0, 0.0, 1.0)            # sentinel: a baked normal always has z >= 0.5
    img_ao = bpy.data.images.new("eldroot_ao", 1024, 1024, alpha=False)
    img_ao.colorspace_settings.name = "Non-Color"
    img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)           # sentinel magenta: a baked AO texel is grey (r == g)
    tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
    ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
    # the bake writes the ACTIVE image node; the low is smooth-shaded during the NORMAL bake so the
    # map carries only detail relative to the interpolated surface, then flat shading comes back.
    me_lo.shade_smooth()
    for o in scene.objects:
        o.select_set(o in (hi, low))
    bpy.context.view_layer.objects.active = low
    stats = {}
    for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
        nt.nodes.active = node
        scene.cycles.samples = samples
        t = time.time()
        r = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=ext, margin=16, use_clear=False)
        stats[typ] = {"result": sorted(r), "seconds": round(time.time() - t, 1)}
    me_lo.shade_flat()
    img_n.file_format = "PNG"; img_ao.file_format = "PNG"
    px = np.empty(2048 * 2048 * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
    dev = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
    pa = np.empty(1024 * 1024 * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
    cov_n = px[:, 2] > 0.25
    cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
    stats["normal_covered_texels"] = round(float(cov_n.mean()), 4)
    stats["normal_detail_fraction_dev_gt_0.05"] = round(float((dev[cov_n] > 0.05).mean()), 4)
    stats["normal_dev_mean"] = round(float(dev[cov_n].mean()), 4)
    stats["ao_covered_texels"] = round(float(cov_a.mean()), 4)
    stats["ao_mean"] = round(float(pa[cov_a, 0].mean()), 4)
    stats["ao_p05"] = round(float(np.percentile(pa[cov_a, 0], 5)), 4)
    # un-baked texels (sentinel) -> neutral, so no sentinel colour can bleed in at island edges
    px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
    pa[~cov_a, :3] = stats["ao_mean"]; img_ao.pixels.foreach_set(pa.ravel())
    for img, nm in ((img_n, "eldroot_normal.png"), (img_ao, "eldroot_ao.png")):
        img.filepath_raw = os.path.join(tex_dir, nm); img.save(); img.pack(); img.filepath = "//textures/" + nm
    stats["cage_extrusion"] = round(ext, 4)
    report["bake"] = stats
    # wire: base = Col x AO ; normal = NormalMap(normal tex)
    mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
    mul.inputs["Factor"].default_value = 1.0
    ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
    oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
    nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
    nt.links.new(oc, bsdf.inputs["Base Color"])
    nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
    nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    scene.render.engine = "BLENDER_EEVEE"
else:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])

# --------------------------------------------------------------------------- finish
bpy.data.objects.remove(hi, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
Wl, _ = read_mesh(me_lo)
low["conquest_unit"] = UNIT
low["conquest_tier"] = U["tier"]
low["conquest_tri_budget"] = U["budget"]
low["conquest_max_height"] = U["max_h"]
low["conquest_max_footprint"] = U["max_fp"]
low["conquest_yaw_fix_deg"] = U["yaw"]
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = rule
low["conquest_source"] = os.path.basename(bpy.data.filepath)
report["tris_final"] = tri_count(me_lo)
import hashlib
_cd = np.empty(len(me_lo.loops) * 4, dtype=np.float32); me_lo.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(Wl, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
report["final_bbox"] = [Wl.min(0).round(4).tolist(), Wl.max(0).round(4).tolist()]
report["seconds"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0   # no .blend1 backups
bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
json.dump(report, open(OUT_JSON, "w"), indent=1)
print("IMPROVE_DONE", json.dumps(report))
