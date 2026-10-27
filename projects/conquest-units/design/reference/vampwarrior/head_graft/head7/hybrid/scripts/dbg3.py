import bpy, numpy as np
for n in ("tongue", "teeth_lower", "teeth_upper", "head"):
    o = bpy.data.objects[n]; P = np.array([v.co for v in o.data.vertices])
    m = (np.abs(P[:, 0]) < 0.05) & (P[:, 2] < -0.11) & (P[:, 2] > -0.17)
    print("DBG", n, "front y (min) in lower-mouth box", P[m, 1].min().round(4) if m.any() else None, "z", P[m, 2].min().round(3) if m.any() else None, P[m, 2].max().round(3) if m.any() else None)
o = bpy.data.objects["head"]
mi = np.zeros(len(o.data.polygons), int); o.data.polygons.foreach_get("material_index", mi)
C = np.array([p.center for p in o.data.polygons])
m = (np.abs(C[:, 0]) < 0.05) & (C[:, 2] < -0.11) & (C[:, 2] > -0.17) & (C[:, 1] < -0.24)
print("DBG head front faces in box by material", np.bincount(mi[m], minlength=3))
