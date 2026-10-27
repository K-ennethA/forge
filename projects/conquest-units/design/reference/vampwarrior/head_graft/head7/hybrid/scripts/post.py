"""post.py <metrics.json> : eye opening from the front depth maps (recess below a grey-opened lid envelope, px 0.001)
-> adds eye_recess_px / pct to every candidate, prints a table"""
import sys, json, numpy as np
from scipy import ndimage
J = json.load(open(sys.argv[1])); base = sys.argv[1].replace(".json", "")
r = 35
yy, xx = np.mgrid[-r:r + 1, -r:r + 1]; disk = (xx * xx + yy * yy) <= r * r


def eye_px(H):
    y = H[::2, ::2, 1]
    y = np.where(np.isfinite(y), y, np.nanmax(y))
    env = ndimage.grey_opening(y, footprint=disk, mode="nearest")
    rec = y - env
    return rec, int((rec > 0.004).sum())


S = np.load(base + "_source_maps.npz")
src = [eye_px(S[k])[1] for k in ("eye0", "eye1")]
J["source_eye_recess_px"] = src
for c in J["cands"]:
    M = np.load(base + "_%s_maps.npz" % c["name"])
    px = [eye_px(M[k])[1] for k in ("eye0", "eye1")]
    c["eye_recess_px"] = px
    c["eye_recess_pct"] = [round(100.0 * p / max(s, 1), 1) for p, s in zip(px, src)]
json.dump(J, open(sys.argv[1], "w"), indent=1)
print("source eye px", src, "slit", J["source_slit"])
print("%-22s %7s %9s %13s %13s %13s %13s %7s %6s" % ("cand", "tris", "eye%", "face p50/p99", "eyes p50/p99", "mouth p50/99", "jaw p50/p99", "slit", "fang%"))
for c in J["cands"]:
    d = c["drift"]
    f = lambda k: "%.4f/%.4f" % (max(d[k]["s2c_p50"], d[k]["c2s_p50"] or 0), max(d[k]["s2c_p99"], d[k]["c2s_p99"] or 0))
    print("%-22s %7d %9s %13s %13s %13s %13s %7s %6s" % (c["name"], c["tris"], "/".join("%.0f" % e["pct"] for e in c["eye_open"]),
          f("face"), f("eyes"), f("mouth"), f("jaw"), c["slit"]["open_frac"], c["fang_px_pct"]))
