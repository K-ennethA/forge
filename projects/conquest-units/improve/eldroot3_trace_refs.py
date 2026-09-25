"""Eldroot v3 trace: register the artist's annotated crops (design/refs/eldroot-v3-*.png) onto the v2 render they
were cut from (renders/eldroot-standing2/standing_front.png: coarse-to-fine scale/offset search on grey levels, red
strokes masked out), then back-project every red stroke pixel through the exact v2 sheet camera onto a world plane
(skirt y = -0.90, knee collars y = -0.22, groin circle y = -0.70). Deterministic extraction of the drawn targets
(never eyeballed coordinates); eldroot_stand3.py reads the result.

    blender --background rigged/eldroot_standing2.blend --factory-startup --python eldroot3_trace_refs.py --
            design/refs/eldroot-v3-trace.json
"""
import bpy, numpy as np, math, json, sys
from mathutils import Vector
PRJ = r"C:\Users\kenne\OneDrive\Desktop\git\forge\projects\conquest-units"


def load(path):
    im = bpy.data.images.load(path)
    w, h = im.size
    px = np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1, :, :3].copy()
    bpy.data.images.remove(im)
    return px


STRIDE = [3]


def register(ref, ren, s_rng, x_rng, y_rng):
    red = (ref[:, :, 0] > 0.6) & (ref[:, :, 1] < 0.35) & (ref[:, :, 2] < 0.35)
    g_ref = ref.mean(2); g_ren = ren.mean(2)
    h, w = g_ref.shape
    ii, jj = np.mgrid[0:h:STRIDE[0], 0:w:STRIDE[0]]
    keep = ~red[ii, jj]
    # dilate red exclusion
    ii, jj = ii[keep], jj[keep]
    vals = g_ref[ii, jj]
    best = (1e18, None)
    for s in s_rng:
        for x0 in x_rng:
            xs = np.round(x0 + jj / s).astype(int)
            okx = (xs >= 0) & (xs < g_ren.shape[1])
            for y0 in y_rng:
                ys = np.round(y0 + ii / s).astype(int)
                ok = okx & (ys >= 0) & (ys < g_ren.shape[0])
                if ok.mean() < 0.97:
                    continue
                d = g_ren[ys[ok], xs[ok]] - vals[ok]
                e = float((d * d).mean())
                if e < best[0]:
                    best = (e, (s, x0, y0))
    return best, red


def refine(ref, ren, guess, red):
    s0, x00, y00 = guess
    return register(ref, ren, np.arange(s0 - 0.006, s0 + 0.0061, 0.002), np.arange(x00 - 4, x00 + 4.1, 1), np.arange(y00 - 4, y00 + 4.1, 1))


out = {}
ren_front = load(PRJ + r"\renders\eldroot-standing2\standing_front.png")
for nm in ("eldroot-v3-skirt-silhouette.png", "eldroot-v3-groin-circled.png", "eldroot-v3-knee-collars.png"):
    ref = load(PRJ + r"\design\refs\\" + nm)
    STRIDE[0] = 9
    b, red = register(ref, ren_front, np.arange(0.55, 2.6, 0.05), np.arange(0, 800, 16), np.arange(0, 900, 16))
    STRIDE[0] = 4
    b1, _ = register(ref, ren_front, np.arange(b[1][0] - 0.05, b[1][0] + 0.051, 0.01), np.arange(b[1][1] - 16, b[1][1] + 17, 4), np.arange(b[1][2] - 16, b[1][2] + 17, 4))
    STRIDE[0] = 2
    b2, _ = refine(ref, ren_front, b1[1], red)
    s, x0, y0 = b2[1]
    ys, xs = np.nonzero(red)
    out[nm] = {"size": list(ref.shape[:2]), "scale_ref_per_render_px": round(float(s), 4), "offset_render_px": [float(x0), float(y0)],
               "rms_gray": round(math.sqrt(b2[0]), 4), "red_px": int(red.sum()),
               "red_render_xy": np.stack([x0 + xs / s, y0 + ys / s], 1).round(2).tolist()}
    print(nm, "scale", s, "offset", x0, y0, "rms", math.sqrt(b2[0]), "coarse", b[1])

# exact v2 sheet camera for standing_front (eldroot2_render_sheet.py: bbox of the rest mesh, aim(c, r, 0, 5, 1.0))
stand = bpy.data.objects["eldroot"]; rig = bpy.data.objects["eldroot_rig"]
if rig.animation_data:
    rig.animation_data.action = None
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
m = stand.evaluated_get(dg).to_mesh()
co = np.array([v.co[:] for v in m.vertices])
lo, hi = Vector(co.min(0)), Vector(co.max(0))
c, r = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
half_fov = math.atan(18.0 / 50.0)
dist = r / math.sin(half_fov) * 1.0
e = math.radians(5.0)
d = Vector((0.0, -math.cos(e), math.sin(e)))
cam_loc = c + d * dist
q = (c - cam_loc).to_track_quat("-Z", "Y")
Rm = q.to_matrix()
RES = 1024


def pix_ray(px, py):
    # sensor 36 mm fit AUTO on a square frame: half extent = tan(half_fov) at unit depth
    t = math.tan(half_fov)
    xn = (px + 0.5) / RES * 2 - 1; yn = 1 - (py + 0.5) / RES * 2
    v = Rm @ Vector((xn * t, yn * t, -1.0))
    return cam_loc, v.normalized()


def to_plane_y(px, py, yp):
    o, v = pix_ray(px, py)
    k = (yp - o.y) / v.y
    p = o + v * k
    return [round(p.x, 4), round(p.z, 4)]


def project(p):
    v = Rm.transposed() @ (Vector(p) - cam_loc)
    t = math.tan(half_fov)
    return [((v.x / -v.z) / t + 1) / 2 * RES, (1 - (v.y / -v.z) / t) / 2 * RES]


out["camera"] = {"loc": list(cam_loc), "centre": list(c), "radius": r}
out["checks_projected_px"] = {"trunk_base_centre_front": project((0, -0.5, 2.7314)), "hip_L": project((0.45, 0.0854, 3.0608)),
                              "knee_L": project((0.5417, 0.0854, 1.4706)), "top": project((0, 0, hi.z))}
for nm, yp in (("eldroot-v3-skirt-silhouette.png", -0.90), ("eldroot-v3-knee-collars.png", -0.22), ("eldroot-v3-groin-circled.png", -0.70)):
    pts = out[nm]["red_render_xy"]
    out[nm]["world_xz_on_plane_y%.2f" % yp] = [to_plane_y(px, py, yp) for px, py in pts]
    del out[nm]["red_render_xy"]
json.dump(out, open(sys.argv[sys.argv.index("--") + 1], "w"))
print("checks", out["checks_projected_px"])


def summarise(nm, key):
    P = np.array(out[nm][key])
    xs = np.round(P[:, 0] / 0.02) * 0.02
    print(nm, key, "x range %.3f..%.3f  z range %.3f..%.3f" % (P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max()))
    for xb in np.unique(xs):
        zz = P[xs == xb, 1]
        print("  x %+.2f  z %s" % (xb, " ".join("%.3f" % v for v in np.unique(np.round(zz, 2)))))
summarise("eldroot-v3-skirt-silhouette.png", "world_xz_on_plane_y-0.90")
summarise("eldroot-v3-knee-collars.png", "world_xz_on_plane_y-0.22")
summarise("eldroot-v3-groin-circled.png", "world_xz_on_plane_y-0.70")
