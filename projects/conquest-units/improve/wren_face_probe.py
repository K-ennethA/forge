"""Wren v5 face probe (review-log 2026-09-29 "Wren v5 feedback + FE reference set"): measures the DELIVERED rest mesh
(improved/wren.blend, the main object) with front rays -- the same code on v4 and v5, so before / after compare 1:1.

    blender --background improved/wren.blend --factory-startup --python improve/wren_face_probe.py -- <out_prefix>

writes <out_prefix>.json (numbers) + <out_prefix>.npz (the depth / relief maps the compose step draws).

UNDER-EYE (each eye; columns every 0.5 mm across the lower lid, from the inner to the outer corner):
  crease_mm_5col   the v4 build's metric re-measured on the delivered mesh: 5 columns (eye centre -8 .. +8 mm), from
                   1 mm below the lid edge to 26 mm below the eye centre, max sink behind the profile's front convex hull
  crease_mm_dense  the same sink metric on every column across the whole lower lid (the bag arc runs to the corners)
  flatness_mm      max |deviation| of the skin from a SMOOTH cheek-to-lashline surface: per column a least-squares
                   quadratic y(z) from the lower liner's outer edge (lid edge + LINER_LO) down to CHEEK_MM below the lid
                   edge; a bag, a crease or a lid-margin line is a deviation from it (a smooth convex cheek is not)
  lid_line_mm      the same deviation restricted to the first 3 mm under the liner (the "thin lower-lid line" band)
MOUTH (columns every 0.5 mm, |x| <= MOUTH_HW of the face midline, from MOUTH_UP above the seam to MOUTH_DN below):
  relief_mm        max |deviation| from a smooth no-lip profile: per column a least-squares CUBIC y(z) (the philtrum-to-
                   chin S of the face without lips); proud lips, the seam recess, a lower-lip rim all show as deviation
  upper_proud_mm   per column: the upper lip's max forwardness over the seam point, DETRENDED (the smooth profile's own
                   slope removed) -- the v4 build's upper_fwd includes the face's slope; both are quoted
  seam_recess_mm   per column: how far the seam point sits behind the smooth profile
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
# --report <json>: the build report giving the landmarks, the centre shift, the sealed seam and the v5 bridge zone
# (default improved/wren.json; the v4 mesh is probed with the v5 report: the seal, landmarks and shift are identical)
REP = json.load(open(argv[argv.index("--report") + 1] if "--report" in argv else os.path.join(ROOT, "improved", "wren.json")))
LINER_LO = 0.3            # mm: the lower liner (LINER_W[1]) -- the flatness band starts at its outer edge
CHEEK_MM = 18.0           # mm below the lid edge: the flatness band ends on the cheek
MOUTH_HW, MOUTH_UP, MOUTH_DN = 26.0, 11.0, 12.0
STEP = 0.00025

main = max((o for o in bpy.context.scene.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
me = main.data
n = len(me.vertices)
co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
M = np.array(main.matrix_world)
co = co @ M[:3, :3].T + M[:3, 3]
names = list(me["conquest_regions"])
rid = np.empty(len(me.polygons), dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", rid)
freg = np.array([names[i] for i in rid], dtype=object)
F = [list(p.vertices) for p in me.polygons]
EYE_R = {"eye_sclera", "eye_iris", "eye_iris_dark", "eye_pupil", "eye_hilite"}
SKIN_R = {"skin", "skin_shadow", "liner", "lash", "brow", "mouth", "lips"}
Fs = [f for f, r in zip(F, freg) if r in SKIN_R]
Fs_reg = [r for r in freg if r in SKIN_R]
Fe = [f for f, r in zip(F, freg) if r in EYE_R]
BS = BVHTree.FromPolygons(co.tolist(), Fs)
BE = BVHTree.FromPolygons(co.tolist(), Fe)
SH = np.array(REP["centre_shift"])
LM = REP["landmarks"]
EYEc = {"L": np.array(LM["eye_L"]) - SH}
EYEc["R"] = np.array([-LM["eye_L"][0], LM["eye_L"][1], LM["eye_L"][2]]) - SH
Z_LIP = float(LM["lip_line_z"])


def front(x, z):
    """first front hit: ('skin', y, region) / ('eye', y, None) / (None, nan, None)."""
    hs = BS.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    he = BE.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    if hs[0] is None and he[0] is None:
        return None, np.nan, None
    if he[0] is None or (hs[0] is not None and hs[3] < he[3] - 1e-6):
        return "skin", hs[0][1], Fs_reg[hs[2]]
    return "eye", he[0][1], None


def hull_sink(zs, ys):
    """max sink (m) of a profile behind its FRONT convex hull (forwardness = -y); zs ascending."""
    P = np.stack([zs, -ys], 1)
    h = []
    for p in P:
        while len(h) >= 2 and np.cross(h[-1] - h[-2], p - h[-2]) >= 0:
            h.pop()
        h.append(p)
    h = np.array(h)
    hy = np.interp(P[:, 0], h[:, 0], h[:, 1])
    k = int(np.argmax(hy - P[:, 1]))
    return float((hy - P[:, 1])[k]), float(P[k, 0])


out = {"mesh": bpy.data.filepath, "rule": "see improve/wren_face_probe.py docstring"}
maps = {}
# ------------------------------------------------------------------ under-eye
UE = {}
for s in "LR":
    c = EYEc[s]; sg = 1.0 if s == "L" else -1.0
    # the lid edge per column: scan down from the eye centre until the first skin hit (below the opening)
    cols = np.arange(-14.0, 22.01, 0.5)          # mm, + = toward the outer corner
    rows = np.arange(0.0, -32.0, -STEP * 1000)   # mm below the eye centre
    D = np.full((len(rows), len(cols)), np.nan); REGM = np.full((len(rows), len(cols)), "", dtype=object)
    lid = np.full(len(cols), np.nan)
    for j, dx in enumerate(cols):
        x = c[0] + sg * dx * 1e-3
        seen_eye = False
        for i, dz in enumerate(rows):
            who, y, r = front(float(x), float(c[2] + dz * 1e-3))
            if who == "eye":
                seen_eye = True
            elif who == "skin":
                D[i, j] = y; REGM[i, j] = r
                if seen_eye and np.isnan(lid[j]):
                    lid[j] = dz
    ok = ~np.isnan(lid)
    res = {"columns_mm": [float(cols[ok].min()), float(cols[ok].max())], "per_column": {}}
    cr_d, fl, ll, fl_map = [], [], [], np.full(D.shape, np.nan)
    for j in np.nonzero(ok)[0]:
        # (the liner's own outer edge per column: the last 'liner' / 'lash' row under the lid edge, else lid + LINER_LO)
        i_lid = int(np.argmin(np.abs(rows - lid[j])))
        i_l = i_lid
        while i_l + 1 < len(rows) and REGM[i_l + 1, j] in ("liner", "lash"):
            i_l += 1
        z0 = min(rows[i_l] - STEP * 1000, lid[j] - LINER_LO)
        band = (rows <= z0) & (rows >= lid[j] - CHEEK_MM) & ~np.isnan(D[:, j])
        zz, yy = rows[band], D[band, j]
        if len(zz) < 12:
            continue
        cf = np.polyfit(zz, yy, 2)
        dev = yy - np.polyval(cf, zz)
        fl_map[band, j] = dev
        fl.append(float(np.abs(dev).max()))
        ll.append(float(np.abs(dev[zz >= z0 - 3.0]).max()))
        b2 = (rows <= lid[j] - 1.0) & (rows >= -26.0) & ~np.isnan(D[:, j])
        sk, at = hull_sink(rows[b2][::-1] * 1e-3, D[b2, j][::-1])
        cr_d.append((sk, at, float(cols[j])))
    cr5 = {}
    for dx in (-8.0, -4.0, 0.0, 4.0, 8.0):
        j = int(np.argmin(np.abs(cols - dx)))
        b2 = (rows <= lid[j] - 1.0) & (rows >= -26.0) & ~np.isnan(D[:, j])
        sk, at = hull_sink(rows[b2][::-1] * 1e-3, D[b2, j][::-1])
        cr5["dx%+d" % dx] = round(1000 * sk, 3)
    cr5["max"] = max(cr5.values())
    kd = int(np.argmax([c_[0] for c_ in cr_d]))
    res.update({"lid_edge_below_centre_mm": {"min": round(float(np.nanmin(-lid)), 2), "max": round(float(np.nanmax(-lid)), 2),
                                             "at_dx0": round(float(-lid[int(np.argmin(np.abs(cols)))]), 2)},
                "crease_mm_5col": cr5,
                "crease_mm_dense": {"max": round(1000 * cr_d[kd][0], 3), "at_dx_mm": cr_d[kd][2],
                                    "at_mm_below_centre": round(-1000 * cr_d[kd][1], 1),
                                    "per_column_mm": {"%+.1f" % c_[2]: round(1000 * c_[0], 2) for c_ in cr_d}, "p90": round(1000 * float(np.percentile([c_[0] for c_ in cr_d], 90)), 3),
                                    "columns": len(cr_d)},
                "flatness_mm": {"max": round(1000 * max(fl), 3), "p90": round(1000 * float(np.percentile(fl, 90)), 3),
                                "median": round(1000 * float(np.median(fl)), 3)},
                "lid_line_mm": {"max": round(1000 * max(ll), 3), "p90": round(1000 * float(np.percentile(ll, 90)), 3)},
                "regions_in_band": {r: int((REGM[:, ok] == r).sum()) for r in sorted(set(REGM[:, ok].ravel()) - {""})}})
    UE[s] = res
    maps["ue_%s_depth" % s] = D; maps["ue_%s_dev" % s] = fl_map; maps["ue_%s_cols" % s] = cols; maps["ue_%s_rows" % s] = rows
    maps["ue_%s_lid" % s] = lid
out["undereye"] = UE
out["undereye_rule"] = ("front rays, 0.25 mm rows x 0.5 mm columns; crease = sink behind the column's front convex hull (from 1 mm "
                        "under the lid edge to 26 mm under the eye centre); flatness = |skin - per-column quadratic fit| from "
                        "the liner's outer edge to %.0f mm under the lid edge; lid_line = the same in the first 3 mm" % CHEEK_MM)
# ------------------------------------------------------------------ mouth
xs = np.arange(-MOUTH_HW, MOUTH_HW + 0.01, 0.5)
zs = np.arange(MOUTH_UP, -MOUTH_DN - 0.01, -STEP * 1000)
xm = float(np.median([EYEc["L"][0], EYEc["R"][0]]))       # the face midline in the delivered (centre-shifted) frame
D = np.full((len(zs), len(xs)), np.nan); REGM = np.full(D.shape, "", dtype=object)
for j, x in enumerate(xs):
    for i, dz in enumerate(zs):
        who, y, r = front(xm + x * 1e-3, Z_LIP + dz * 1e-3)
        if who == "skin":
            D[i, j] = y; REGM[i, j] = r
dev = np.full(D.shape, np.nan)
cols_out = {}
up_d, rec_d = [], []
for j, x in enumerate(xs):
    okz = ~np.isnan(D[:, j])
    cf = np.polyfit(zs[okz], D[okz, j], 3)
    dev[okz, j] = D[okz, j] - np.polyval(cf, zs[okz])
    near = okz & (np.abs(zs) <= 1.5)
    i_s = int(np.nanargmax(np.where(near, D[:, j], -9.0)))
    upm = okz & (zs > zs[i_s]) & (zs < zs[i_s] + 8.0)
    raw_up = float(D[i_s, j] - np.nanmin(D[upm, j]))
    det_up = float(dev[i_s, j] - np.nanmin(dev[upm, j]))
    up_d.append(det_up); rec_d.append(float(dev[i_s, j]))
    if abs(x) in (0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0) and x >= 0:
        cols_out["x%+d" % x] = {"seam_dz_mm": round(float(zs[i_s]), 2), "upper_fwd_raw_mm": round(1000 * raw_up, 3),
                                "upper_proud_detrended_mm": round(1000 * det_up, 3), "seam_recess_mm": round(1000 * float(dev[i_s, j]), 3),
                                "relief_max_mm": round(1000 * float(np.nanmax(np.abs(dev[:, j]))), 3)}
ad = np.abs(dev)
# the flattened zone (the v5 bridge lens about the sealed seam) when the report carries it
SEAM_R = (REP.get("lip_seal") or {}).get("seam")
BR = ((REP.get("v5_round") or {}).get("mouth") or {}).get("bridge_mm")
zone = np.ones(ad.shape, bool)
if SEAM_R and BR:
    zcs = (np.interp(xs * 1e-3, SEAM_R["xs"], SEAM_R["zc"]) - Z_LIP) * 1e3          # the seam, mm about the lip line
    tt = np.clip((np.abs(xs) - BR[2]) / (BR[3] - BR[2]), 0.0, 1.0)
    up_, dn_ = BR[0] + (BR[4] - BR[0]) * tt, BR[1] + (BR[5] - BR[1]) * tt
    zone = (zs[:, None] <= zcs[None, :] + up_[None, :] - BR[8]) & (zs[:, None] >= zcs[None, :] - dn_[None, :] + BR[8]) & \
        (np.abs(xs)[None, :] <= BR[3])
adz = np.where(zone, ad, np.nan)
out["mouth"] = {"relief_in_zone_mm": {"max": round(1000 * float(np.nanmax(adz)), 3), "p99": round(1000 * float(np.nanpercentile(adz, 99)), 3),
                                      "zone": "the v5 bridge lens (full weight) about the sealed seam, within the probe window"
                                      if SEAM_R and BR else "the whole probe window (no bridge in the report)"},
                "relief_mm": {"max": round(1000 * float(np.nanmax(ad)), 3), "p99": round(1000 * float(np.nanpercentile(ad, 99)), 3),
                              "p95": round(1000 * float(np.nanpercentile(ad, 95)), 3),
                              "max_at_x_dz_mm": [float(xs[np.unravel_index(np.nanargmax(ad), ad.shape)[1]]),
                                                 float(zs[np.unravel_index(np.nanargmax(ad), ad.shape)[0]])],
                              "past_line_ends_max(|x|>16.5)": round(1000 * float(np.nanmax(ad[:, np.abs(xs) > 16.5])), 3),
                              "lower_lip_band_max(dz -3..-10)": round(1000 * float(np.nanmax(ad[(zs <= -3) & (zs >= -10)])), 3),
                              "upper_lip_band_max(dz +1..+8)": round(1000 * float(np.nanmax(ad[(zs >= 1) & (zs <= 8)])), 3)},
                "upper_proud_detrended_mm_max": round(1000 * max(up_d), 3),
                "seam_recess_mm_max": round(1000 * max(rec_d), 3),
                "columns": cols_out,
                "rule": "front rays over |x| <= %.0f mm, %.0f mm above .. %.0f mm below the lip line; relief = |skin - per-column "
                        "least-squares cubic| (the no-lip S profile)" % (MOUTH_HW, MOUTH_UP, MOUTH_DN),
                "regions": {r: int((REGM == r).sum()) for r in sorted(set(REGM.ravel()) - {""})}}
maps["m_depth"] = D; maps["m_dev"] = dev; maps["m_xs"] = xs; maps["m_zs"] = zs; maps["m_zone"] = zone.astype(np.int8)
maps["m_mouthpaint"] = (REGM == "mouth").astype(np.int8)
json.dump(out, open(OUT + ".json", "w"), indent=1)
np.savez_compressed(OUT + ".npz", **{k: np.asarray(v, float) for k, v in maps.items()})
print("PROBE", json.dumps({"undereye": {s: {k: UE[s][k] for k in ("crease_mm_5col", "crease_mm_dense", "flatness_mm", "lid_line_mm")}
                                        for s in "LR"}, "mouth": {k: out["mouth"][k] for k in ("relief_in_zone_mm", "relief_mm",
                                                                                                "upper_proud_detrended_mm_max",
                                                                                                "seam_recess_mm_max")}}))
sys.stdout.flush()
os._exit(0)
