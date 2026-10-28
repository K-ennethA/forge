"""vampwarrior_rawhead_build.py -- <spec.json> : LOOK-ONLY game head from the raw head7_s56_edit head (no rig, no keys).
f-frame inside (front -y, up +z, glb units) = Blender's import frame of the glb; exported back with +Y up = the glb frame.
spec: {"raw": raw.npz (V, F, FUV glTF v-down, flab), "vis": vis.npz (vis, cand, zc), "out": dir, "margin": rings,
       "lods": [{"name", "tris"}], "albedo": png, "mr": png, "normal": png | null, "glb_prefix": str,
       "smooth_boundary_lid": 0 }
1. keep = considered faces whose 'margin'-ring neighbourhood holds a visible face (the hidden inner wall goes);
2. bisect at z = zc, everything below goes; components without a single visible face go (inner-wall slivers);
3. winding made consistent and outward per component;
4. quadric COLLAPSE decimation (Blender) per LOD, original UVs carried; smooth shading;
5. neck ring = the boundary loop on the cut plane; npz + glb (+ json report) per LOD."""
import sys, os, json, math, numpy as np, bpy, bmesh
from mathutils import Vector
SP = json.load(open(sys.argv[sys.argv.index("--") + 1]))
OUT = SP["out"]; os.makedirs(OUT, exist_ok=True)
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
Z = np.load(SP["raw"]); VS = np.load(SP["vis"])
V, F, FUV = Z["V"], Z["F"].astype(np.int64), Z["FUV"]
vis, cand, zc = VS["vis"], VS["cand"], float(VS["zc"])
# faces straddling the cut can see out BELOW the cap (their centroid is under it): they do not count as visible seeds
vis = vis & (V[F].mean(1)[:, 2] > zc + 0.003)
REP = {"raw_tris": int(len(F)), "raw_visible": int(vis.sum()), "zc": zc}


def grow(mask, n):
    for _ in range(n):
        vm = np.zeros(len(V), bool); vm[F[mask].ravel()] = True; mask = vm[F].any(1)
    return mask


keep = cand & grow(vis, int(SP.get("margin", 3)))
REP["kept_before_cut"] = int(keep.sum())
fi = np.nonzero(keep)[0]
me = bpy.data.meshes.new("rawhead")
used = np.unique(F[fi]); rm = -np.ones(len(V), np.int64); rm[used] = np.arange(len(used))
Fk = rm[F[fi]]
me.vertices.add(len(used)); me.vertices.foreach_set("co", V[used].astype(np.float32).ravel())
me.loops.add(Fk.size); me.loops.foreach_set("vertex_index", Fk.astype(np.int32).ravel())
me.polygons.add(len(Fk)); me.polygons.foreach_set("loop_start", (np.arange(len(Fk)) * 3).astype(np.int32))
me.polygons.foreach_set("loop_total", np.full(len(Fk), 3, np.int32))
me.update()
uv = FUV[fi].reshape(-1, 2).copy(); uv[:, 1] = 1.0 - uv[:, 1]
me.uv_layers.new(name="UVMap").data.foreach_set("uv", uv.astype(np.float32).ravel())
at = me.attributes.new("oid", "INT", "FACE"); at.data.foreach_set("value", fi.astype(np.int32))
at = me.attributes.new("vis", "BOOLEAN", "FACE"); at.data.foreach_set("value", vis[fi])
me.validate()
ob = bpy.data.objects.new("rawhead", me); bpy.context.scene.collection.objects.link(ob)

bm = bmesh.new(); bm.from_mesh(me)
geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(0, 0, zc), plane_no=(0, 0, 1), clear_inner=True)
# components; drop those without a visible face
try:
    visl = bm.faces.layers.bool["vis"]
except Exception:
    visl = bm.faces.layers.int["vis"]
bm.faces.ensure_lookup_table()
seen = set(); comps = []
for f0 in bm.faces:
    if f0.index in seen:
        continue
    st = [f0]; seen.add(f0.index); cf = []
    while st:
        f = st.pop(); cf.append(f)
        for e in f.edges:
            for g in e.link_faces:
                if g.index not in seen:
                    seen.add(g.index); st.append(g)
    comps.append(cf)
dropped = []
for cf in comps:
    if not any(f[visl] for f in cf):
        dropped.append(len(cf)); bmesh.ops.delete(bm, geom=cf, context="FACES")
REP["components_dropped_no_visible"] = dropped
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
# consistent winding, then outward per component (signed volume about the component centre)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
bm.faces.ensure_lookup_table(); bm.normal_update()
seen = set(); flips = 0; ncomp = 0
for f0 in bm.faces:
    if f0.index in seen:
        continue
    st = [f0]; seen.add(f0.index); cf = []
    while st:
        f = st.pop(); cf.append(f)
        for e in f.edges:
            for g in e.link_faces:
                if g.index not in seen:
                    seen.add(g.index); st.append(g)
    ncomp += 1
    c = sum((f.calc_center_median() for f in cf), Vector()) / len(cf)
    s = sum((f.calc_center_median() - c).dot(f.normal) * f.calc_area() for f in cf)
    if s < 0:
        for f in cf:
            f.normal_flip()
        flips += 1
REP["components"] = ncomp; REP["components_flipped"] = flips
bm.to_mesh(me); bm.free(); me.update()
if SP.get("smooth_zone"):      # Taubin smoothing weighted by a per-raw-vertex mask (vampwarrior_rawhead_zone.py)
    SZ = SP["smooth_zone"]; wr = np.load(SZ["w"])
    Vm = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", Vm); Vm = Vm.reshape(-1, 3)
    look_ = {tuple(k): i for i, k in enumerate(np.round(V * 1e6).astype(np.int64).tolist())}
    wv = np.array([wr[look_[k]] if k in look_ else 0.0 for k in map(tuple, np.round(Vm * 1e6).astype(np.int64).tolist())])
    Ed = np.empty(len(me.edges) * 2, np.int64); me.edges.foreach_get("vertices", Ed); Ed = Ed.reshape(-1, 2)
    me.calc_loop_triangles()
    Tm = np.empty(len(me.loop_triangles) * 3, np.int64); me.loop_triangles.foreach_get("vertices", Tm); Tm = Tm.reshape(-1, 3)
    Es = np.sort(np.concatenate([Tm[:, [0, 1]], Tm[:, [1, 2]], Tm[:, [2, 0]]]), 1); uE, cE = np.unique(Es, axis=0, return_counts=True)
    wv[uE[cE != 2].ravel()] = 0.0
    deg = np.bincount(Ed.ravel(), minlength=len(Vm)).astype(np.float64)
    V0 = Vm.copy()

    def lap(X):
        S = np.zeros_like(X); np.add.at(S, Ed[:, 0], X[Ed[:, 1]]); np.add.at(S, Ed[:, 1], X[Ed[:, 0]])
        return S / np.maximum(deg, 1)[:, None] - X
    for _ in range(int(SZ.get("iters", 15))):
        Vm = Vm + 0.5 * wv[:, None] * lap(Vm)
        if SZ.get("mode", "taubin") == "taubin":
            Vm = Vm - 0.53 * wv[:, None] * lap(Vm)
    me.vertices.foreach_set("co", Vm.astype(np.float32).ravel()); me.update()
    dd = np.linalg.norm(Vm - V0, axis=1)
    REP["smooth_zone"] = {"verts_w": int((wv > 0).sum()), "move_p99": round(float(np.percentile(dd[wv > 0], 99)), 5) if (wv > 0).any() else 0,
                          "move_max": round(float(dd.max()), 5)}
me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool)); me.update()
REP["full_tris"] = len(me.polygons)


def arrays(m):
    m.calc_loop_triangles()
    Vv = np.empty(len(m.vertices) * 3); m.vertices.foreach_get("co", Vv); Vv = Vv.reshape(-1, 3)
    T = np.empty(len(m.loop_triangles) * 3, np.int64); m.loop_triangles.foreach_get("vertices", T); T = T.reshape(-1, 3)
    L = np.empty(len(m.loop_triangles) * 3, np.int64); m.loop_triangles.foreach_get("loops", L); L = L.reshape(-1, 3)
    U = np.empty(len(m.loops) * 2); m.uv_layers[0].data.foreach_get("uv", U); U = U.reshape(-1, 2)
    FU = U[L].copy(); FU[..., 1] = 1.0 - FU[..., 1]
    return Vv, T, FU


def loops(Vv, T):
    E = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    u, c = np.unique(E, axis=0, return_counts=True)
    b = u[c == 1]; nm = int((c > 2).sum())
    adj = {}
    for x, y in b:
        adj.setdefault(int(x), []).append(int(y)); adj.setdefault(int(y), []).append(int(x))
    seen = set(); out = []
    for s in adj:
        if s in seen:
            continue
        lp = [s]; seen.add(s); cur = s
        while True:
            nx = [n for n in adj[cur] if n not in seen]
            if not nx:
                break
            cur = nx[0]; seen.add(cur); lp.append(cur)
        out.append(lp)
    return out, nm


def loop_report(Vv, T):
    lps, nm = loops(Vv, T)
    info = []
    for lp in lps:
        P = Vv[lp]
        info.append({"n": len(lp), "z_min": round(float(P[:, 2].min()), 4), "z_max": round(float(P[:, 2].max()), 4),
                     "centre": P.mean(0).round(4).tolist()})
    info.sort(key=lambda d: -d["n"])
    return lps, info, nm


Vf, Tf, FUf = arrays(me)
lps, info, nm = loop_report(Vf, Tf)
REP["full_open_loops"] = len(info); REP["full_nonmanifold_edges"] = nm; REP["full_loops_top"] = info[:12]
np.savez_compressed(os.path.join(OUT, "cull_full.npz"), V=Vf, F=Tf, FUV=FUf)

# material (the raw glb's: base colour, metallic-roughness (G rough, B metal), optional normal map)
mat = bpy.data.materials.new("vampwarrior_head"); mat.use_nodes = True
nt = mat.node_tree; pb = nt.nodes["Principled BSDF"]
ti = nt.nodes.new("ShaderNodeTexImage"); ti.image = bpy.data.images.load(SP["albedo"]); ti.image.name = "vampwarrior_head_albedo"
nt.links.new(ti.outputs[0], pb.inputs["Base Color"])
pb.inputs["Roughness"].default_value = float(SP.get("roughness", 0.6)); pb.inputs["Metallic"].default_value = 0.0
if SP.get("mr"):
    tm = nt.nodes.new("ShaderNodeTexImage"); tm.image = bpy.data.images.load(SP["mr"]); tm.image.colorspace_settings.name = "Non-Color"
    tm.image.name = "vampwarrior_head_mr"
    sp = nt.nodes.new("ShaderNodeSeparateColor"); nt.links.new(tm.outputs[0], sp.inputs[0])
    nt.links.new(sp.outputs["Green"], pb.inputs["Roughness"]); nt.links.new(sp.outputs["Blue"], pb.inputs["Metallic"])
if SP.get("normal"):
    tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = bpy.data.images.load(SP["normal"]); tn.image.colorspace_settings.name = "Non-Color"
    tn.image.name = "vampwarrior_head_normal"
    nmn = nt.nodes.new("ShaderNodeNormalMap"); nt.links.new(tn.outputs[0], nmn.inputs["Color"]); nt.links.new(nmn.outputs[0], pb.inputs["Normal"])
mat.use_backface_culling = False
me.materials.append(mat)

for lod in SP["lods"]:
    ob2 = ob.copy(); ob2.data = me.copy(); ob2.name = lod["name"]; bpy.context.scene.collection.objects.link(ob2)
    if lod["tris"] < len(me.polygons):
        md = ob2.modifiers.new("d", "DECIMATE"); md.decimate_type = "COLLAPSE"
        md.ratio = lod["tris"] / len(me.polygons); md.use_collapse_triangulate = True
        if SP.get("seam_lock", 0) > 0:          # UV-seam vertices (more than one UV) + open-boundary vertices weighted
            m2_ = ob2.data; vi_ = np.empty(len(m2_.loops), np.int64); m2_.loops.foreach_get("vertex_index", vi_)
            uv_ = np.empty(len(m2_.loops) * 2); m2_.uv_layers[0].data.foreach_get("uv", uv_)
            key_ = np.unique(np.c_[vi_, np.round(uv_.reshape(-1, 2) * 8192).astype(np.int64)], axis=0)
            seam_ = np.bincount(key_[:, 0], minlength=len(m2_.vertices)) > 1
            vg = ob2.vertex_groups.new(name="seam")
            vg.add([int(i) for i in np.nonzero(seam_)[0]], 1.0, "REPLACE")
            md.vertex_group = "seam"; md.vertex_group_factor = float(SP["seam_lock"])
            REP.setdefault("seam_verts", {})[lod["name"]] = int(seam_.sum())
        bpy.context.view_layer.objects.active = ob2
        for o in bpy.context.scene.objects:
            o.select_set(o is ob2)
        bpy.ops.object.modifier_apply(modifier="d")
    m2 = ob2.data
    m2.polygons.foreach_set("use_smooth", np.ones(len(m2.polygons), bool)); m2.update()
    for a_ in [a_.name for a_ in m2.attributes if a_.name in ("oid", "vis")]:
        m2.attributes.remove(m2.attributes[a_])
    Vl, Tl, FUl = arrays(m2)
    lpl, infol, nml = loop_report(Vl, Tl)
    neck = None
    for lp in lpl:
        P = Vl[lp]
        if np.abs(P[:, 2] - zc).max() < 1e-4 and len(lp) > 20:
            if neck is None or len(lp) > len(neck):
                neck = lp
    r = {"tris": int(len(Tl)), "verts": int(len(Vl)), "open_loops": len(infol), "nonmanifold_edges": nml, "loops_top": infol[:12]}
    if neck is not None:
        Pn = Vl[neck]
        glbp = np.stack([Pn[:, 0], Pn[:, 2], -Pn[:, 1]], 1)          # f-frame -> glb (+Y up)
        r["neck_ring"] = {"n": len(neck), "blender_vertex_indices": [int(i) for i in neck],
                          "plane_glb_y": zc, "centre_glb": glbp.mean(0).round(5).tolist(),
                          "radius_minmax": [round(float(np.linalg.norm(Pn[:, :2] - Pn[:, :2].mean(0), axis=1).min()), 4),
                                            round(float(np.linalg.norm(Pn[:, :2] - Pn[:, :2].mean(0), axis=1).max()), 4)],
                          "positions_glb": glbp.round(6).tolist()}
    np.savez_compressed(os.path.join(OUT, lod["name"] + ".npz"), V=Vl, F=Tl, FUV=FUl,
                        neck=np.array(neck if neck is not None else [], np.int64))
    for o in bpy.context.scene.objects:
        o.select_set(o is ob2)
    bpy.context.view_layer.objects.active = ob2
    gp = os.path.join(OUT, SP.get("glb_prefix", "vampwarrior_head_") + lod["name"] + ".glb")
    if not SP.get("export_glb", True):
        REP[lod["name"]] = r
        print("LOD", lod["name"], json.dumps({k: v for k, v in r.items() if k not in ("neck_ring",)}), flush=True)
        continue
    bpy.ops.export_scene.gltf(filepath=gp, export_format="GLB", use_selection=True, export_yup=True, export_apply=True,
                              export_normals=True, export_tangents=bool(SP.get("normal")), export_materials="EXPORT",
                              export_image_format="AUTO", export_animations=False, export_skins=False, export_morph=False)
    r["glb"] = gp; r["glb_bytes"] = os.path.getsize(gp)
    REP[lod["name"]] = r
    print("LOD", lod["name"], json.dumps({k: v for k, v in r.items() if k not in ("neck_ring",)}), flush=True)
    if neck is not None:
        print("NECK", lod["name"], r["neck_ring"]["n"], r["neck_ring"]["centre_glb"], r["neck_ring"]["radius_minmax"], flush=True)
json.dump(REP, open(os.path.join(OUT, "build_report.json"), "w"), indent=1)
print("BUILD", json.dumps({k: v for k, v in REP.items() if not isinstance(v, dict)}), flush=True)
