"""Duskmaw shadow-base uniformity measurement (read-only; opens blends, never saves).

    blender --background --factory-startup --python duskmaw_base_measure.py -- <out.json> <label>=<blend> [...]

Feedback 2 ("the bottom should almost be uniform like a shadow coming out of the ground"), measured at the rest pose
about the model's XY origin (the contract's bbox centre), in 72 bins of 5 degrees round the vertical axis:
Points = every mesh edge sampled at 9 points (vertices alone leave long decimated floor edges' bins empty).
  floor_ring_radius   per bin, the outermost vertex radius among vertices below FLOOR_Z (where the body meets the
                      floor); reported as mean / min / max and CV = std / mean (0 = a perfect circle).
  floor_contact_bins  % of bins with any vertex below CONTACT_Z (the body reaches the floor all the way round).
  rim_z_std           per bin, the height of the outermost vertex below RIM_ZMAX (the skirt's reach); std over bins
                      (tentacle legs lying on the floor between raised skirt edges = large; one hem = small).
Hands excluded (radius > 7.4 within 3.2 of the side plane above z 3.8 -- the hanging lower arms).
"""
import bpy, sys, json, math
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
FLOOR_Z, CONTACT_Z, RIM_ZMAX, NB = 0.5, 0.05, 6.0, 72
res = {"rule": __doc__.strip().split("\n\n")[1], "blends": {}}
for label, path in (a.split("=", 1) for a in argv[1:]):
    bpy.ops.wm.open_mainfile(filepath=path)
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            o.data.pose_position = "REST"
    bpy.context.view_layer.update()
    ob = max((o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render), key=lambda o: len(o.data.polygons))
    V = np.empty(len(ob.data.vertices) * 3); ob.data.vertices.foreach_get("co", V); V = V.reshape(-1, 3)
    M = np.array(ob.matrix_world); V = V @ M[:3, :3].T + M[:3, 3]
    # dense sampling along every edge (decimated meshes have long floor edges: vertices alone leave empty 5-degree bins)
    E = np.empty(len(ob.data.edges) * 2, dtype=np.int64); ob.data.edges.foreach_get("vertices", E); E = E.reshape(-1, 2)
    V = np.concatenate([V[E[:, 0]] * (1 - t) + V[E[:, 1]] * t for t in np.linspace(0.0, 1.0, 9)])
    r = np.hypot(V[:, 0], V[:, 1])
    hand = (r > 7.4) & (np.abs(V[:, 1]) < 3.2) & (V[:, 2] > 3.8)
    b = ((np.arctan2(V[:, 0], -V[:, 1]) + math.pi) / (2 * math.pi) * NB).astype(int) % NB
    ring, contact, rimz = [], 0, []
    for k in range(NB):
        m = (b == k) & ~hand
        fl = m & (V[:, 2] < FLOOR_Z)
        ring.append(float(r[fl].max()) if fl.any() else 0.0)
        contact += bool((m & (V[:, 2] < CONTACT_Z)).any())
        rm = m & (V[:, 2] < RIM_ZMAX)
        if rm.any():
            i = np.nonzero(rm)[0][np.argmax(r[rm])]
            rimz.append(float(V[i, 2]))
    ring = np.array(ring)
    row = {"file": path, "height": round(float(V[:, 2].max() - V[:, 2].min()), 4),
           "floor_ring_radius": {"mean": round(float(ring.mean()), 4), "min": round(float(ring.min()), 4),
                                 "max": round(float(ring.max()), 4), "cv": round(float(ring.std() / max(ring.mean(), 1e-9)), 4)},
           "floor_contact_bins_pct": round(100.0 * contact / NB, 2),
           "rim_z": {"mean": round(float(np.mean(rimz)), 4), "std": round(float(np.std(rimz)), 4),
                     "min": round(float(np.min(rimz)), 4), "max": round(float(np.max(rimz)), 4)}}
    res["blends"][label] = row
    print("BASE", label, json.dumps(row))
json.dump(res, open(OUT, "w"), indent=1)
print("DONE")
