"""vampwarrior_rawhead_holes.py -- <raw.npz> <vis.npz> <out.json> <cand.npz> [cand2.npz ...] : visible-hole test.
Orthographic ray grids from 62 directions (Fibonacci sphere, px 0.004) over the whole head plus 9 face close-up directions
(px 0.001, yaw / pitch -25..25). Reference = the raw faces not entirely below the neck cut; both reference and candidate
get the same neck CAP (vis.npz), i.e. nothing is judged through the neck opening. Per pixel: a 'hole' = the raw is hit and
the candidate is missed, or the candidate's hit is > TOL farther than the raw's (sees through to something behind).
Raw hits inside the 0.004 band above the cut (raw faces straddling the cut plane) are not judged. Reported per view: raw-hit px, hole px, and 'core' hole px (hole px whose 4 neighbours are holes too = a hole >= 3 px wide;
single-pixel silhouette / chord differences do not survive this)."""
import sys, json, math, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); VS = np.load(a[1]); OUTP = a[2]; CANDS = a[3:]
TOL = 0.006
capV, capF = VS["capV"], VS["capF"]


def tree(V, F):
    V2 = np.vstack([V, capV]); F2 = np.vstack([F, capF + len(V)])
    return BVHTree.FromPolygons(V2.tolist(), F2.tolist())


ref = tree(Z["V"], Z["F"][VS["cand"]])
c0 = np.array([0.0, 0.0, -0.02]); R = 0.56


def views():
    out = []
    n = 62; i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n); th = np.pi * (1 + 5 ** 0.5) * i
    for d in np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1):
        out.append(("s%02d" % len(out), d, c0, R, 0.004))
    for yaw in (-25, 0, 25):
        for pit in (-25, 0, 25):
            y, p = math.radians(yaw), math.radians(pit)
            d = np.array([math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)])
            out.append(("f%+d%+d" % (yaw, pit), d, np.array([0.0, -0.15, -0.02]), 0.26, 0.001))
    return out


def grid(t, d, c, r, px):
    u = np.cross(d, [0, 0, 1.0] if abs(d[2]) < 0.9 else [1.0, 0, 0]); u /= np.linalg.norm(u); w = np.cross(d, u)
    g = np.arange(-r, r, px); n = len(g)
    D = np.full((n, n), np.inf); Hz = np.full((n, n), np.inf); dv = Vector((-d).tolist())
    for ii, s in enumerate(g):
        for jj, tt in enumerate(g):
            o = c + d * 1.5 + u * s + w * tt
            h = t.ray_cast(Vector(o.tolist()), dv, 4.0)
            if h[0] is not None:
                D[ii, jj] = h[3]; Hz[ii, jj] = h[0][2]
    return D, Hz


VW = views()
REF = [grid(ref, d, c, r, px) for (_, d, c, r, px) in VW]
ZCUT = float(VS["zc"])
res = {"tol": TOL, "cands": {}}
for cp in CANDS:
    C = np.load(cp); t = tree(C["V"], C["F"])
    per = {}; tot = 0; core_tot = 0
    for (nm, d, c, r, px), (Dr, Hr) in zip(VW, REF):
        Dc, _ = grid(t, d, c, r, px)
        hole = np.isfinite(Dr) & (Hr > ZCUT + 0.004) & ((~np.isfinite(Dc)) | (Dc - Dr > TOL))
        core = hole.copy()
        core[1:-1, 1:-1] &= hole[:-2, 1:-1] & hole[2:, 1:-1] & hole[1:-1, :-2] & hole[1:-1, 2:]
        core[0, :] = core[-1, :] = core[:, 0] = core[:, -1] = False
        per[nm] = {"raw_px": int(np.isfinite(Dr).sum()), "hole_px": int(hole.sum()), "core_px": int(core.sum())}
        tot += int(hole.sum()); core_tot += int(core.sum())
        if core.any():
            ii, jj = np.nonzero(core); per[nm]["core_at_ij"] = [int(ii.mean()), int(jj.mean())]
    res["cands"][cp] = {"hole_px_total": tot, "core_px_total": core_tot,
                        "worst": sorted(per.items(), key=lambda kv: -kv[1]["core_px"])[:6]}
    print("HOLES", cp, tot, core_tot, json.dumps(res["cands"][cp]["worst"][:3]), flush=True)
json.dump(res, open(OUTP, "w"), indent=1)
