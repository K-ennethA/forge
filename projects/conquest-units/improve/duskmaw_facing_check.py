"""Read-only facing check of the SHIPPED Duskmaw glb + a glTF axis probe. Never writes into Conquest.

    blender --background --factory-startup --python duskmaw_facing_check.py -- <monster.glb> <scratch_dir> <out.json>

1. Imports monster.glb; the mouth = faces using material 'summoner_mouth' (recolor_tips_and_mouth.py
   paints the mouth on the -Y side of the sculpt: `c.y < cy`). Reports the mouth centroid direction
   in the imported (Blender) frame, and after the roster's model_yaw_deg (180).
2. glTF axis probe: exports a cube with a nose at Blender -Y (same exporter flags as Conquest's
   prepare_unit.py: export_yup=True) to scratch, reads the glb JSON chunk, reports which glTF
   axis the nose landed on. glTF / Godot treat +Z as the model front (Godot MODEL_FRONT).
"""
import bpy, sys, json, math, struct, os
import numpy as np

a = sys.argv[sys.argv.index("--") + 1:]
GLB, SCR, OUT = a[0], a[1], a[2]
rep = {}
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
ob = bpy.data.objects["Monster"]
me = ob.data
M = np.array(ob.matrix_world)
co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
mi = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("material_index", mi)
fc = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("center", fc); fc = fc.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
names = [s.material.name if s.material else "" for s in ob.material_slots]
mouth_idx = [i for i, n in enumerate(names) if n.startswith("summoner_mouth")]
mm = np.isin(mi, mouth_idx)
c_all = co.mean(0); c_m = fc[mm].mean(0)
d = c_m - c_all
rep["mouth_faces"] = int(mm.sum())
rep["mouth_centroid"] = c_m.round(4).tolist(); rep["body_centroid"] = c_all.round(4).tolist()
rep["mouth_dir_angle_from_minusY_deg_imported"] = round(math.degrees(math.atan2(d[0], -d[1])), 1)
# roster yaw 180 about Blender Z (== Godot Y)
dr = np.array([-d[0], -d[1]])
rep["mouth_dir_angle_from_minusY_deg_after_roster_yaw_180"] = round(math.degrees(math.atan2(dr[0], -dr[1])), 1)
# spikes/limb extremes ('mouth illusion' parts): outermost faces per side in the lower half
# 2. glTF axis probe
bpy.ops.wm.read_factory_settings(use_empty=True)
bme = bpy.data.meshes.new("probe")
bme.from_pydata([(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0), (0, -1.0, 0.05)], [], [(0, 1, 3), (1, 2, 3), (2, 0, 3), (0, 2, 1)])
o = bpy.data.objects.new("probe", bme); bpy.context.scene.collection.objects.link(o)
o.select_set(True); bpy.context.view_layer.objects.active = o
p = os.path.join(SCR, "axis_probe.glb")
bpy.ops.export_scene.gltf(filepath=p, export_format="GLB", use_selection=True, export_yup=True, export_apply=True)
raw = open(p, "rb").read()
jl = struct.unpack("<I", raw[12:16])[0]
gj = json.loads(raw[20:20 + jl])
acc = gj["accessors"][gj["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
rep["axis_probe"] = {"blender_nose": [0, -1.0, 0.05], "gltf_POSITION_min": acc["min"], "gltf_POSITION_max": acc["max"],
                     "reading": "nose lands on glTF +Z" if acc["max"][2] > 0.9 else ("glTF -Z" if acc["min"][2] < -0.9 else "other")}
json.dump(rep, open(OUT, "w"), indent=1)
print("DUSKMAW", json.dumps(rep))
