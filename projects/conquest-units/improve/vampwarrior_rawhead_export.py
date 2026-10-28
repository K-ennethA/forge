"""vampwarrior_rawhead_export.py -- <spec.json> : head npz (f-frame V, F, FUV glTF v-down, neck) -> game glb (glb frame, +Y up).
spec: {"items": [{"npz", "glb", "name"}], "albedo": png, "roughness": 0.6, "report": json, "zc": neck cut z (f-frame)}
One mesh, one material (albedo only, roughness constant, metallic 0, double-sided like the raw), smooth shading,
no rig / keys. The report records tris, verts, open loops and the neck ring (positions in the glb frame)."""
import sys, os, json, numpy as np, bpy
SP = json.load(open(sys.argv[sys.argv.index("--") + 1]))
REP = {}
mat = bpy.data.materials.new("vampwarrior_head"); mat.use_nodes = True
nt = mat.node_tree; pb = nt.nodes["Principled BSDF"]
pb.inputs["Roughness"].default_value = float(SP.get("roughness", 0.6)); pb.inputs["Metallic"].default_value = 0.0
ti = nt.nodes.new("ShaderNodeTexImage"); ti.image = bpy.data.images.load(SP["albedo"]); ti.image.name = "vampwarrior_head_albedo"
nt.links.new(ti.outputs[0], pb.inputs["Base Color"])
mat.use_backface_culling = False
for it in SP["items"]:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    Z = np.load(it["npz"]); V, F, FUV = Z["V"].copy(), Z["F"].astype(np.int64), Z["FUV"]
    # neck ring = the longest open loop lying on the cut plane (within 0.003); its vertices are snapped onto the plane
    neck = None
    if "zc" in SP:
        Eb = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1)
        ub, cb = np.unique(Eb, axis=0, return_counts=True); adj = {}
        for x_, y_ in ub[cb == 1]:
            adj.setdefault(int(x_), []).append(int(y_)); adj.setdefault(int(y_), []).append(int(x_))
        seen = set()
        for s0 in adj:
            if s0 in seen:
                continue
            lp = [s0]; seen.add(s0); cur = s0
            while True:
                nx = [q for q in adj[cur] if q not in seen]
                if not nx:
                    break
                cur = nx[0]; seen.add(cur); lp.append(cur)
            if len(lp) > 20 and np.abs(V[lp, 2] - SP["zc"]).max() < 0.003 and (neck is None or len(lp) > len(neck)):
                neck = lp
        if neck is not None:
            V[neck, 2] = SP["zc"]
    me = bpy.data.meshes.new(it["name"])
    me.vertices.add(len(V)); me.vertices.foreach_set("co", V.astype(np.float32).ravel())
    me.loops.add(F.size); me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
    me.polygons.add(len(F)); me.polygons.foreach_set("loop_start", (np.arange(len(F)) * 3).astype(np.int32))
    me.polygons.foreach_set("loop_total", np.full(len(F), 3, np.int32))
    me.update()
    uv = FUV.reshape(-1, 2).copy(); uv[:, 1] = 1.0 - uv[:, 1]
    me.uv_layers.new(name="UVMap").data.foreach_set("uv", uv.astype(np.float32).ravel())
    me.validate()
    me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool)); me.update()
    me.materials.append(mat)
    ob = bpy.data.objects.new(it["name"], me); bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob; ob.select_set(True)
    E = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1)
    u, c = np.unique(E, axis=0, return_counts=True)
    r = {"tris": int(len(F)), "verts": int(len(V)), "open_edges": int((c == 1).sum()), "nonmanifold_edges": int((c > 2).sum())}
    if neck is not None:
        P = V[neck]; G = np.stack([P[:, 0], P[:, 2], -P[:, 1]], 1)
        r["neck_ring"] = {"n": int(len(P)), "plane": "glb y = %.4f (horizontal cut)" % float(P[:, 2].mean()),
                          "centre_glb": G.mean(0).round(5).tolist(),
                          "extent_glb_x": [round(float(G[:, 0].min()), 4), round(float(G[:, 0].max()), 4)],
                          "extent_glb_z": [round(float(G[:, 2].min()), 4), round(float(G[:, 2].max()), 4)],
                          "npz_vertex_indices": [int(i) for i in neck], "ordered": True, "positions_glb": G.round(6).tolist()}
    bpy.ops.export_scene.gltf(filepath=it["glb"], export_format="GLB", use_selection=True, export_yup=True, export_apply=True,
                              export_normals=True, export_tangents=False, export_materials="EXPORT", export_image_format="AUTO",
                              export_animations=False, export_skins=False, export_morph=False)
    r["glb"] = it["glb"]; r["glb_bytes"] = os.path.getsize(it["glb"])
    REP[it["name"]] = r
    print("EXPORT", it["name"], json.dumps({k: v for k, v in r.items() if k != "neck_ring"}), flush=True)
json.dump(REP, open(SP["report"], "w"), indent=1)
