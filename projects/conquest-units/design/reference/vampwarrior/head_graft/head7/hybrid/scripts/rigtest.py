"""rigtest.py <in.npz> <out_prefix> [eye_balls_part 0|1] : quick shape-key style deformation tests in the f-frame.
JAW: rotate 14 deg about a hinge (0, 0.08, -0.02) with weights split exactly at the lip line (z -0.109) and faded out over
the neck; BLINK: the skin above each eye's aperture centre rotated about the eyeball centre to close the aperture (weight 1
at the aperture, 0 at the brow); separate eyeball parts (part > 0) never move. Writes <out>_jaw.npz, <out>_blink.npz and
<out>_rig.json (edge stretch in the mouth / eye boxes: p99 and max of new/old length; triangles flipped)."""
import sys, json, numpy as np
a = sys.argv[1:]
Z = np.load(a[0]); out = a[1]
V, F = Z["V"].astype(float), Z["F"]
part = Z["part"] if "part" in Z.files else np.zeros(len(F), int)
vpart = np.zeros(len(V), int)
for k in range(3):
    vpart[F[:, k]] = np.maximum(vpart[F[:, k]], part)
skin = vpart == 0


def ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t)


def rotx(P, piv, ang):
    c, s = np.cos(ang), np.sin(ang)
    d = P - piv
    return piv + np.stack([d[:, 0], c * d[:, 1] - s * d[:, 2], s * d[:, 1] + c * d[:, 2]], 1)


E = np.unique(np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1), axis=0)


def stats(V2, box):
    Cm = 0.5 * (V[E[:, 0]] + V[E[:, 1]])
    m = np.all((Cm >= box[0]) & (Cm <= box[1]), 1)
    l0 = np.linalg.norm(V[E[m, 0]] - V[E[m, 1]], axis=1); l1 = np.linalg.norm(V2[E[m, 0]] - V2[E[m, 1]], axis=1)
    r = l1 / np.maximum(l0, 1e-9)
    n0 = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]); n1 = np.cross(V2[F[:, 1]] - V2[F[:, 0]], V2[F[:, 2]] - V2[F[:, 0]])
    Cf = V[F].mean(1); mf = np.all((Cf >= box[0]) & (Cf <= box[1]), 1)
    flip = int(((n0 * n1).sum(1) < 0)[mf].sum())
    return {"edges": int(m.sum()), "stretch_p99": round(float(np.percentile(r, 99)), 2), "stretch_max": round(float(r.max()), 2),
            "edges_over_2x": int((r > 2).sum()), "tris_flipped": flip}


rep = {}
# jaw
piv = np.array([0.0, 0.08, -0.02])
split = Z["jawsplit"].astype(float) if "jawsplit" in Z.files else ss(-0.1075, -0.1105, V[:, 2])
w = split * ss(0.12, 0.02, V[:, 1]) * ss(-0.40, -0.30, V[:, 2])
w[~skin] = 0
Vj = V.copy()
Vr = rotx(V, piv, np.radians(14.0))
Vj = V + w[:, None] * (Vr - V)
rep["jaw"] = stats(Vj, (np.array([-0.12, -0.4, -0.2]), np.array([0.12, -0.1, -0.05])))
np.savez_compressed(out + "_jaw.npz", V=Vj, F=F, **({"FUV": Z["FUV"]} if "FUV" in Z.files else {}))
# blink
EC = [np.array([0.1258, -0.1364, 0.0926]), np.array([-0.1161, -0.1343, 0.0905])]
Vb = V.copy(); wb_all = np.zeros(len(V))
for c in EC:
    zc = 0.092
    dx = np.abs(V[:, 0] - c[0])
    wb = ss(0.075, 0.05, dx) * ss(zc - 0.002, zc + 0.006, V[:, 2]) * ss(zc + 0.06, zc + 0.025, V[:, 2]) * (V[:, 1] < c[1] + 0.02)
    wb[~skin] = 0
    ang = np.radians(30.0)             # the upper lid down over the eye
    Vr = rotx(V, c, ang)
    Vb = Vb + wb[:, None] * (Vr - V)
    wb_all = np.maximum(wb_all, wb)
rep["blink"] = stats(Vb, (np.array([-0.2, -0.3, 0.03]), np.array([0.2, -0.08, 0.16])))
rep["blink"]["verts_moved"] = int((wb_all > 0.01).sum())
np.savez_compressed(out + "_blink.npz", V=Vb, F=F, **({"FUV": Z["FUV"]} if "FUV" in Z.files else {}))
json.dump(rep, open(out + "_rig.json", "w"), indent=1)
print("RIG", out, json.dumps(rep))
