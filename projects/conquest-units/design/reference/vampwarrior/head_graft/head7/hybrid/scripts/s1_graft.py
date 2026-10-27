"""step 1 fast path: the kit's head_graft on the stage-1 'head' fine grid with variant settings -> polygonise -> head crop
in the head's f-frame (glb units).  blender -b -P s1_graft.py -- <solid.npz> <variants.json> <outdir>"""
import sys, os, json, copy, types, time
import numpy as np
KIT = os.environ["BO_KIT"]
sys.path.insert(0, KIT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proxy_finish as PF, proxy_finish_light as PFL, magmoo_sdf as SD
a = sys.argv[sys.argv.index("--") + 1:]
Z = np.load(a[0]); variants = json.load(open(a[1])); out = a[2]
cfg, raw = PF.load_cfg("vampwarrior"); lc = cfg["light"]
meta = json.loads(bytes(Z["meta"]).decode())
fi = [i for i, f in enumerate(meta["fine"]) if f["name"] == "head"][0]
fm = meta["fine"][fi]
Ff = Z["fine_%d" % fi].astype(np.float64); flo = np.asarray(fm["lo"], float); fst = float(fm["step"])
PV, PFc, PUV, PTI = Z["prox_V"], Z["prox_F"], Z["prox_UV"], Z["prox_texi"]
tk = sorted(k for k in Z.files if k.startswith("tex_"))[0]
hg = lc["head_graft"]; s = float(hg["scale"]); mz = float(hg["mid_z"]); ty, tz = hg["place"]
print("FINE head step", fst, "grid", Ff.shape, "min_half_thickness", lc["min_half_thickness"], "band", lc["band_voxels"])
for v in variants:
    t0 = time.time()
    spec = copy.deepcopy(hg)
    spec["k"] = float(v.get("k", spec["k"]))
    if v.get("no_shell"):
        spec.pop("shell_u", None)
    mht = float(v.get("mht", lc["min_half_thickness"]))
    div = int(v.get("refine", 1))
    if div > 1:   # finer synthetic grid over the head only (empty = outside), for a resolution reference
        st = fst / div
        lo = flo.copy(); n = ((np.array(Ff.shape) - 1) * div + 1)
        G = np.full(tuple(n), float(lc["band_voxels"]) * st)
    else:
        st, lo, G = fst, flo, Ff
    Fh = PFL.head_graft(G, lo, st, PV, PFc, PUV, PTI, int(tk[4:]), Z[tk], spec, float(lc["band_voxels"]) * st, mht * st, pad=False)[0]
    Vh, Th = SD.polygonise(types.SimpleNamespace(F=Fh, lo=lo, step=st))
    C = Vh[Th].mean(1)
    keep = (C[:, 2] > 7.13) & (np.abs(C[:, 0]) < 0.47) & (C[:, 1] > -0.75) & (C[:, 1] < 0.03) & (C[:, 2] < 8.06)
    T = Th[keep]; used = np.unique(T); rm = -np.ones(len(Vh), np.int64); rm[used] = np.arange(len(used))
    Vb = Vh[used]; T = rm[T]
    Vf = (Vb - np.array([-s * mz, ty, tz])) / s
    np.savez_compressed(os.path.join(out, v["name"] + "_high.npz"), V=Vf, F=T, Vbody=Vb)
    print("VARIANT", v["name"], json.dumps(v), "tris", len(T), "inflate_u", round(mht * st, 5), "s", round(time.time() - t0, 1))
