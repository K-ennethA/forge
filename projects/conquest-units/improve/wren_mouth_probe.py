"""Wren v6 MOUTH probe (review-log 2026-09-29 "Wren v6 mouth feedback" + "addendum"): what the artist SEES in the mouth
area, measured on the delivered rig at rest -- the same code on v5.1 and v6, so before / after compare 1:1.

    blender --background rigged/wren.blend --factory-startup --python improve/wren_mouth_probe.py -- <out_prefix>
            [--report <improved/wren.json>]

writes <out_prefix>.json + <out_prefix>_<variant>.png (orthographic FRONT renders of the lower face at PX_MM px / mm, the
survey key / fill / rim lighting of wren_render.py, one fixed window about the eye centres -- the eyes never move -- so a
v5.1 and a v6 render overlay 1:1):
  base        as delivered
  noline      the painted mouth line repainted skin: every dark trace LEFT in the mouth area is a second feature (the
              seam / a crease) -- none should remain
  flatnormal  noline + the baked normal map neutral: the trace that is left comes from the geometry's flat facets alone
  noao        noline + the baked AO lifted to 1

LANDMARKS (midline, front rays on the skin; mm, z up):
  nose_bottom  going down from the nose tip, the end of the nose's underside (hit normals facing down past NOSE_DOWN)
               = the lowest edge of the nose in a front view (the drawn nose mark of the FE / anime references)
  chin         going down from the mouth, the first row whose hit normal is edge-on (|n_y| < CHIN_EDGE: the jaw
               outline of a front view) or whose hit jumps back > 8 mm (under the chin)
  line         the painted 'mouth' region faces: x extent, z centre (at the midline and per column)
  face_w       the skin silhouette width in the front view at the line's height
RATIOS: v_ratio = (nose_bottom - line) / (nose_bottom - chin); w_face = line width / face_w; w_eyes = line width / eye
spacing (yaw-invariant: the FE portrait is three-quarter).
FEATURES (image analysis of each render, inside the mouth window between the nose bottom and the chin): a pixel is a
DARK TRACE (zone: ZONE_NOSE mm under the nose bottom .. ZONE_CHIN mm above the chin) when its luminance sits > TRACE_DL below the median of its column over +-TRACE_WIN mm; traces are labelled as
connected components; the line's own pixels (base vs noline difference) are reported apart from every other trace.
SEAM BAND: per 1 mm column (|x| <= 27 mm), the darkest non-line trace pixel from BAND[0] mm below to BAND[1] mm above the
line's centre IN THAT COLUMN (the painted faces' z; the smirk corner rises): the second mouth feature (the seam's crease) if any -- its x extent, its depth below the line, its contrast.
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import palettes as PAL  # noqa: E402  (read-only use)

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = argv[0]
REP = json.load(open(argv[argv.index("--report") + 1] if "--report" in argv else os.path.join(ROOT, "improved", "wren.json")))
PX_MM = 10.0                 # render scale (px per mm)
WIN = (-40.0, 40.0, -135.0, -45.0)   # window x0, x1 (mm about the midline), z0, z1 (mm about the eye centres' height)
NOSE_DOWN = -0.55            # nose underside: hit normal z below this
CHIN_EDGE = 0.34             # chin outline: |n_y| below this (the surface > 70 deg from the view axis)
TRACE_DL, TRACE_WIN = 0.030, 3.0
MIN_TRACE_PX = 25            # traces smaller than this (px) are texture noise
BAND = (6.0, 4.0)            # the seam band: this far (mm) below / above the line's centre
ZONE_NOSE, ZONE_CHIN = 9.0, 6.0   # the trace zone starts this far (mm) under the nose bottom (clear of the nose's cast
                             #   shadow under the key light: it reaches 7.6 mm down on v5.1, rendered) and ends this far
                             #   above the chin outline (clear of the jaw's terminator); |x| <= 28 mm

scene = bpy.context.scene
for o in scene.objects:
    if o.type == "ARMATURE":
        o.data.pose_position = "REST"
for o in list(scene.objects):
    if o.type in ("CAMERA", "LIGHT"):
        bpy.data.objects.remove(o, do_unlink=True)
bpy.context.view_layer.update()
main = max((o for o in scene.objects if o.type == "MESH"), key=lambda o: len(o.data.polygons))
dg = bpy.context.evaluated_depsgraph_get()
ev = main.evaluated_get(dg); me = ev.to_mesh()
co = np.array([main.matrix_world @ v.co for v in me.vertices])
names = list(main.data["conquest_regions"])
rid = np.empty(len(main.data.polygons), dtype=np.int32); main.data.attributes["region_id"].data.foreach_get("value", rid)
freg = np.array([names[i] for i in rid], dtype=object)
F = [list(p.vertices) for p in me.polygons]
ev.to_mesh_clear()
SKIN_R = {"skin", "skin_shadow", "liner", "lash", "brow", "mouth", "lips"}
Fs = [f for f, r in zip(F, freg) if r in SKIN_R]
BS = BVHTree.FromPolygons(co.tolist(), Fs)
SH = np.array(REP["centre_shift"]); LM = REP["landmarks"]
EL = np.array(LM["eye_L"]) - SH
ER = np.array([-LM["eye_L"][0], LM["eye_L"][1], LM["eye_L"][2]]) - SH
XM = 0.5 * (EL[0] + ER[0]); ZE = EL[2]
EYE_SP = float(EL[0] - ER[0])


def hit(x, z):
    h = BS.ray_cast(Vector((x, -1.0, z)), Vector((0.0, 1.0, 0.0)), 2.0)
    return (None, None) if h[0] is None else (float(h[0][1]), np.array(h[1]))


# ---- midline profile
zs = np.arange(ZE - 0.020, ZE - 0.150, -0.0001)
prof = [hit(XM, float(z)) for z in zs]
ys = np.array([p[0] if p[0] is not None else np.nan for p in prof])
nz = np.array([p[1][2] if p[1] is not None else np.nan for p in prof])
ny = np.array([p[1][1] if p[1] is not None else np.nan for p in prof])
i_tip = int(np.nanargmin(np.where(zs > ZE - 0.080, ys, np.nan)))
i = i_tip
while i + 1 < len(zs) and not (nz[i] < NOSE_DOWN):
    i += 1
while i + 1 < len(zs) and nz[i] < NOSE_DOWN:
    i += 1
Z_NOSE = float(zs[i])
# the painted line
mf = np.nonzero(freg == "mouth")[0]
line = {}
if len(mf):
    mv = co[sorted({v for fi in mf for v in F[fi]})]
    x0, x1 = float(mv[:, 0].min()), float(mv[:, 0].max())
    cols = {}
    for xm_ in np.arange(-24.0, 24.01, 2.0):
        sel = np.abs(mv[:, 0] - (XM + xm_ * 1e-3)) < 0.0012
        if sel.any():
            cols["%+.0f" % xm_] = round(1000 * (float(mv[sel, 2].mean()) - ZE), 2)
    zc_mid = float(mv[np.abs(mv[:, 0] - XM) < 0.002, 2].mean())
    line = {"x_mm": [round(1000 * (x0 - XM), 2), round(1000 * (x1 - XM), 2)], "width_mm": round(1000 * (x1 - x0), 2),
            "z_mid_mm_below_eyes": round(1000 * (ZE - zc_mid), 2), "z_per_col_mm_rel_eyes": cols, "faces": int(len(mf))}
    Z_LINE = zc_mid
else:
    Z_LINE = float(LM["lip_line_z"])
i = int(np.argmin(np.abs(zs - Z_LINE)))
while i + 1 < len(zs):
    if (not np.isnan(ny[i]) and abs(ny[i]) < CHIN_EDGE) or (not np.isnan(ys[i + 1]) and ys[i + 1] - ys[i] > 0.008):
        break
    i += 1
Z_CHIN = float(zs[i])
# face width at the line's height (the skin silhouette of the front view)
xx = np.arange(-0.090, 0.0901, 0.0002)
hx = [x for x in xx if hit(XM + float(x), Z_LINE)[0] is not None]
FACE_W = float(max(hx) - min(hx)) if hx else float("nan")
# the seam: front rays within +-8 mm of the line that pass > 2.5 mm deeper than their +-5 mm neighbourhood = an open crack
crk = []
for x_ in np.arange(-0.028, 0.02801, 0.0005):
    zz = np.arange(Z_LINE - 0.008, Z_LINE + 0.008, 0.00005)
    yy = np.array([(lambda h: h if h is not None else np.nan)(hit(XM + float(x_), float(z_))[0]) for z_ in zz])
    loc = np.array([np.nanmin(yy[max(k - 100, 0):k + 101]) for k in range(len(yy))])
    deep = np.nonzero(yy > loc + 0.0025)[0]
    if len(deep):
        crk.append((round(1000 * float(x_), 1), round(1000 * float(zz[deep].mean() - Z_LINE), 2), int(len(deep))))
geo = {"eye_z": round(ZE, 5), "midline_x": round(XM, 5), "eye_spacing_mm": round(1000 * EYE_SP, 2),
       "nose_tip_mm_below_eyes": round(1000 * (ZE - zs[i_tip]), 2), "nose_bottom_mm_below_eyes": round(1000 * (ZE - Z_NOSE), 2),
       "line_mm_below_eyes": round(1000 * (ZE - Z_LINE), 2), "chin_mm_below_eyes": round(1000 * (ZE - Z_CHIN), 2),
       "nose_to_line_mm": round(1000 * (Z_NOSE - Z_LINE), 2), "line_to_chin_mm": round(1000 * (Z_LINE - Z_CHIN), 2),
       "face_w_at_line_mm": round(1000 * FACE_W, 1), "line": line,
       "v_ratio": round((Z_NOSE - Z_LINE) / (Z_NOSE - Z_CHIN), 3),
       "w_face": round(line.get("width_mm", float("nan")) / (1000 * FACE_W), 3) if line else None,
       "w_eyes": round(line.get("width_mm", float("nan")) / (1000 * EYE_SP), 3) if line else None,
       "open_cracks": {"columns": len(crk), "x_mm_range": [crk[0][0], crk[-1][0]] if crk else None,
                       "rows_total_0.05mm": int(sum(c[2] for c in crk)), "per_column(x, dz_mm, rows)": crk[:: max(1, len(crk) // 24)]}}
print("MPROBE_GEO", json.dumps(geo))

# ---- renders (survey lighting, orthographic front)
scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"; scene.view_settings.look = "None"
scene.render.use_compositing = False; scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 32
except Exception:
    pass
world = bpy.data.worlds.new("probe_world"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
rig = bpy.data.objects.new("probe_rig", None); scene.collection.objects.link(rig)
for nm, en, rot, colr in (("k", 3.2, (50, 0, 150), (1, 1, 1)), ("f", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)), ("r", 2.0, (60, 0, 10), (1, 1, 1))):
    d = bpy.data.lights.new(nm, "SUN"); d.energy = en; d.color = colr
    o = bpy.data.objects.new(nm, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot]; o.parent = rig
rig.rotation_euler = (0, 0, math.radians(180))
cam_d = bpy.data.cameras.new("probe_cam"); cam_d.type = "ORTHO"
cam = bpy.data.objects.new("probe_cam", cam_d); scene.collection.objects.link(cam); scene.camera = cam
W_mm, H_mm = WIN[1] - WIN[0], WIN[3] - WIN[2]
cam_d.ortho_scale = max(W_mm, H_mm) * 1e-3
cam.location = (XM + 0.5e-3 * (WIN[0] + WIN[1]), -2.0, ZE + 0.5e-3 * (WIN[2] + WIN[3]))
cam.rotation_euler = (math.radians(90), 0, 0)
cam_d.clip_start = 0.5; cam_d.clip_end = 4.0
scene.render.resolution_x = int(W_mm * PX_MM); scene.render.resolution_y = int(H_mm * PX_MM)
scene.render.resolution_percentage = 100
for o in scene.objects:
    if o.type == "MESH" and o.name.endswith("_pitchfork"):
        o.hide_render = True
imgs = {im.name: im for im in bpy.data.images}
orig_px = {}
for k in ("wren_normal", "wren_ao"):
    im = imgs[k]; px = np.empty(im.size[0] * im.size[1] * 4, dtype=np.float32); im.pixels.foreach_get(px); orig_px[k] = px
pal = PAL.load(main.get("conquest_unit", "wren"), main.data.get("conquest_skin", "default"))


def set_img(k, val):
    im = imgs[k]
    if val is None:
        im.pixels.foreach_set(orig_px[k])
    else:
        px = orig_px[k].copy().reshape(-1, 4); px[:, :3] = val; im.pixels.foreach_set(px.ravel())
    im.update()


def repaint(noline):
    p = json.loads(json.dumps(pal))
    if noline:
        p["regions"]["mouth"] = dict(p["regions"]["mouth"], rgb=list(p["regions"]["skin"]["rgb"]))
    PAL.paint(main.data, p)


shots = {}
for var in ("base", "noline", "flatnormal", "noao"):
    repaint(var != "base")
    set_img("wren_normal", (0.5, 0.5, 1.0) if var == "flatnormal" else None)
    set_img("wren_ao", (1.0, 1.0, 1.0) if var == "noao" else None)
    scene.render.filepath = "%s_%s.png" % (OUT, var)
    bpy.ops.render.render(write_still=True)
    shots[var] = scene.render.filepath
    print("WROTE", scene.render.filepath)


def lum(p):
    im = bpy.data.images.load(p, check_existing=False)
    a = np.empty(im.size[0] * im.size[1] * 4, dtype=np.float32); im.pixels.foreach_get(a)
    a = a.reshape(im.size[1], im.size[0], 4)[::-1]       # row 0 = top
    bpy.data.images.remove(im)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def mm_to_px(xm_, zm_):
    """(x mm about the midline, z mm about the eyes) -> (col, row)."""
    return (xm_ - WIN[0]) * PX_MM, (WIN[3] - zm_) * PX_MM


Ls = {k: lum(v) for k, v in shots.items()}
H_, W_ = Ls["base"].shape
r_nose = int(mm_to_px(0, 1000 * (Z_NOSE - ZE))[1]) + int(ZONE_NOSE * PX_MM)   # the zone: ZONE_NOSE mm under the nose bottom
r_chin = int(mm_to_px(0, 1000 * (Z_CHIN - ZE))[1]) - int(ZONE_CHIN * PX_MM)   # .. ZONE_CHIN mm above the chin outline
c0, c1 = int(mm_to_px(-28, 0)[0]), int(mm_to_px(28, 0)[0])
line_px = np.abs(Ls["base"] - Ls["noline"]) > 0.04
half = int(TRACE_WIN * PX_MM)


def label(mask):
    lab = np.zeros(mask.shape, np.int32); n = 0
    for r in range(mask.shape[0]):
        for c in range(mask.shape[1]):
            if mask[r, c] and not lab[r, c]:
                n += 1; st = [(r, c)]; lab[r, c] = n
                while st:
                    a, b = st.pop()
                    for da in (-1, 0, 1):
                        for db in (-1, 0, 1):
                            u, v = a + da, b + db
                            if 0 <= u < mask.shape[0] and 0 <= v < mask.shape[1] and mask[u, v] and not lab[u, v]:
                                lab[u, v] = n; st.append((u, v))
    return lab, n


# the line's centre per column (mm about Z_LINE; the smirk corner rises), from the painted faces; level past its ends
if line:
    LINE_X = np.array([float(k) for k in line["z_per_col_mm_rel_eyes"]])
    LINE_DZ = np.array(list(line["z_per_col_mm_rel_eyes"].values())) + 1000 * (ZE - Z_LINE)
    LINE_DZ = LINE_DZ[np.argsort(LINE_X)]; LINE_X = np.sort(LINE_X)
else:
    LINE_X, LINE_DZ = np.array([0.0]), np.array([0.0])
feat = {}
for var, L in Ls.items():
    Z = L[r_nose:r_chin, c0:c1]
    med = np.empty_like(Z)
    for r in range(Z.shape[0]):
        med[r] = np.median(Z[max(r - half, 0):r + half + 1], axis=0)
    dark = (med - Z) > TRACE_DL
    lp = line_px[r_nose:r_chin, c0:c1]
    lab, n = label(dark)
    comps = []
    for k in range(1, n + 1):
        m = lab == k
        if m.sum() < MIN_TRACE_PX:
            continue
        rr, cc = np.nonzero(m)
        on_line = float((m & lp).sum()) / float(m.sum())
        comps.append({"px": int(m.sum()), "x_mm": [round((cc.min() + c0) / PX_MM + WIN[0], 1), round((cc.max() + c0) / PX_MM + WIN[0], 1)],
                      "z_mm_rel_line": [round(WIN[3] - (rr.max() + r_nose) / PX_MM - 1000 * (Z_LINE - ZE), 1),
                                        round(WIN[3] - (rr.min() + r_nose) / PX_MM - 1000 * (Z_LINE - ZE), 1)],
                      "contrast_max": round(float((med - Z)[m].max()), 3), "on_line_frac": round(on_line, 2)})
    comps.sort(key=lambda d: -d["px"])
    other = [c for c in comps if c["on_line_frac"] < 0.5]
    # the SEAM BAND: per 1 mm column, the darkest trace pixel (off the line's own pixels) from BAND[0] mm below to BAND[1]
    # mm above the line's centre -- where a second mouth feature (the seam's crease) would sit
    cm_ = (med - Z) * ~lp
    rows_mm = WIN[3] - (np.arange(Z.shape[0]) + r_nose) / PX_MM - 1000 * (Z_LINE - ZE)      # z of each row, mm about the line
    band_ = []
    for xm_ in np.arange(-27.0, 27.01, 1.0):
        c_ = int(round((xm_ - WIN[0]) * PX_MM)) - c0
        zl_ = float(np.interp(xm_, LINE_X, LINE_DZ))           # the line's own centre in this column (mm about Z_LINE)
        inb_ = (rows_mm - zl_ >= -BAND[0]) & (rows_mm - zl_ <= BAND[1])
        col_ = cm_[inb_, max(c_ - 5, 0):c_ + 5].max(1)
        k_ = int(np.argmax(col_))
        if col_[k_] > TRACE_DL:
            band_.append([xm_, round(float(rows_mm[inb_][k_] - zl_), 1), round(float(col_[k_]), 3)])
    feat[var] = {"traces": len(comps), "line_traces": len(comps) - len(other), "other_traces": len(other),
                 "other_px": int(sum(c["px"] for c in other)), "other_contrast_max": max([c["contrast_max"] for c in other], default=0.0),
                 "seam_band": {"columns_with_trace": len(band_), "x_mm_range": [band_[0][0], band_[-1][0]] if band_ else None,
                               "contrast_max": max([b_[2] for b_ in band_], default=0.0),
                               "dz_mm_below_line_max": max([-b_[1] for b_ in band_], default=0.0),
                               "per_column(x_mm, dz_mm, contrast)": band_},
                 "top": comps[:8]}
# the line's own darkness + its vertical separation from every other trace in the base render
out = {"rule": __doc__.split("LANDMARKS")[0].strip().splitlines()[0], "window_mm": WIN, "px_per_mm": PX_MM,
       "zone_rows_mm_rel_line": [round(WIN[3] - r_nose / PX_MM - 1000 * (Z_LINE - ZE), 1), round(WIN[3] - r_chin / PX_MM - 1000 * (Z_LINE - ZE), 1)],
       "geometry": geo, "features": feat, "renders": shots}
json.dump(out, open(OUT + ".json", "w"), indent=1)
print("MPROBE", json.dumps({"geometry": {k: geo[k] for k in ("nose_bottom_mm_below_eyes", "line_mm_below_eyes", "chin_mm_below_eyes",
                                                              "v_ratio", "w_face", "w_eyes", "face_w_at_line_mm")},
                            "line": line.get("x_mm"), "cracks": geo["open_cracks"]["columns"],
                            "features": {k: dict({kk: v[kk] for kk in ("traces", "line_traces", "other_traces", "other_px", "other_contrast_max")},
                                                  seam_band={kk: v["seam_band"][kk] for kk in ("columns_with_trace", "x_mm_range", "contrast_max",
                                                                                               "dz_mm_below_line_max")})
                                         for k, v in feat.items()}}))
sys.stdout.flush()
os._exit(0)
