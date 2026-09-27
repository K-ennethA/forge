"""Vampwarrior v4.2 hairline check: the built strand top edge / hair outline vs the artist's traced lines, measured in
the head_front image at idle:1 (the still the annotation was drawn on). Headless, in memory, writes one json.

    blender --background rigged/vampwarrior.blend --factory-startup --python vampwarrior_hairline_check.py -- <out.json>

Traced lines: renders/vampwarrior/vampwarrior_v42_hairline_trace.json (vampwarrior_hairline_trace.py). Camera: the
render script's head_front (focus box 'head', yaw 0, elevation 4, fill 1.0, 50 mm, 1024 px). Every pixel is classified
by the part its camera ray hits first (the mesh's conquest_islands vertex ranges). At each traced point the scan runs
along the line's outward normal (+-45 px, 0.5 px steps) and takes the nearest inside -> outside transition:
  orange: inside = a front strand (lock.curtain.*)      -> the strand's visible top edge
  green:  inside = anything (hair / head)                -> the hair outline
deviation > 0 = the built edge lies OUTSIDE (above) the traced line; px -> mm at the head centre's depth.
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K  # noqa: E402  (read-only use)

OUT = sys.argv[sys.argv.index("--") + 1]
TR_ = json.load(open(os.path.join(ROOT, "renders", "vampwarrior", "vampwarrior_v42_hairline_trace.json")))
scene = bpy.context.scene
rig = [o for o in scene.objects if o.type == "ARMATURE"][0]
K.assign_action(rig, bpy.data.actions["idle"]); rig.data.pose_position = "POSE"; scene.frame_set(1)
main = bpy.data.objects["vampwarrior"]
FOCUS = json.loads(main["conquest_focus"])
b0, b1 = Vector(FOCUS["head"][0]), Vector(FOCUS["head"][1])
cam_d = bpy.data.cameras.new("hl_cam"); cam_d.lens = 50
cam = bpy.data.objects.new("hl_cam", cam_d); scene.collection.objects.link(cam); scene.camera = cam
c = (b0 + b1) / 2
rad = max((b1 - b0).length / 2, 1e-3)
e = math.radians(4.0)
cam.location = c + Vector((0.0, -math.cos(e), math.sin(e))) * (rad / math.sin(math.atan(18.0 / 50.0)))
cam.rotation_euler = (c - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.render.resolution_x = scene.render.resolution_y = 1024
scene.render.resolution_percentage = 100
bpy.context.view_layer.update()
TRc, BR, BL, TL = [cam.matrix_world @ v for v in cam_d.view_frame(scene=scene)]
dg = bpy.context.evaluated_depsgraph_get()
ISL = json.loads(main.data["conquest_islands"])
starts = sorted((a, b, k) for k, (a, b) in ISL.items())
IMP = json.load(open(os.path.join(ROOT, "improved", "vampwarrior.json")))
HC = np.array(IMP["landmarks"]["head_centre"]) - np.array(IMP["centre_shift"])


def ray(px, py):
    u = (px + 0.5) / 1024.0
    v = 1.0 - (py + 0.5) / 1024.0
    p = BL + (BR - BL) * u + (TL - BL) * v
    return cam.location.copy(), (p - cam.location).normalized()


o_, d0 = ray(511.5, 511.5)
_, d1 = ray(611.5, 511.5)
MMPX = 1000.0 * (Vector(HC.tolist()) - cam.location).length * (d1 - d0).length / 100.0
cache = {}


def first_part(px, py):
    key = (round(px * 2), round(py * 2))
    if key not in cache:
        o, dv = ray(px, py)
        r = scene.ray_cast(dg, o, dv, distance=5.0)
        if not r[0]:
            cache[key] = None
        elif r[4].name != main.name:
            cache[key] = r[4].name
        else:
            v0 = main.data.polygons[r[3]].vertices[0]
            cache[key] = next((k for a, b, k in starts if a <= v0 < b), "?")
    return cache[key]


def scan(pts, inside):
    P = np.array(pts)
    rows = []
    for i in range(len(P)):
        a, b = P[max(i - 2, 0)], P[min(i + 2, len(P) - 1)]
        t = (b - a) / np.linalg.norm(b - a)
        n = np.array([t[1], -t[0]])
        if np.dot(n, P[i] - np.array(TR_["centre_px"])) < 0:
            n = -n
        best, prev = None, None
        for s in np.arange(-45.0, 45.01, 0.5):
            q = P[i] + n * s
            ins = inside(first_part(q[0], q[1]))
            if prev and not ins and (best is None or abs(s - 0.25) < abs(best)):
                best = s - 0.25
            prev = ins
        rows.append({"px": [round(float(P[i][0]), 1), round(float(P[i][1]), 1)],
                     "dev_mm": None if best is None else round(float(best) * MMPX, 2)})
    return rows


def summary(rows, sel=None):
    d = np.array([r["dev_mm"] for i, r in enumerate(rows) if r["dev_mm"] is not None and (sel is None or sel(i, len(rows)))])
    return {"points": len(rows), "measured": int(len(d)), "mean_abs_mm": round(float(np.abs(d).mean()), 2),
            "p90_abs_mm": round(float(np.percentile(np.abs(d), 90)), 2), "max_abs_mm": round(float(np.abs(d).max()), 2),
            "mean_signed_mm": round(float(d.mean()), 2)}


res = {"camera": "head_front @ idle:1", "mm_per_px_at_head_centre": round(MMPX, 4),
       "orange_vs_strand_top": scan(TR_["orange"], lambda p: bool(p) and p.startswith("lock.curtain")),
       "green_vs_hair_outline": scan(TR_["green"], lambda p: p is not None)}
res["orange_summary"] = summary(res["orange_vs_strand_top"])
res["green_summary"] = summary(res["green_vs_hair_outline"])
# the green's last 6 points per side are the freehand stroke ends beside the temples (the halves differ most there)
res["green_summary_upper_arc"] = summary(res["green_vs_hair_outline"], lambda i, n: 6 <= i < n - 6)
res["green_summary_stroke_ends"] = summary(res["green_vs_hair_outline"], lambda i, n: i < 6 or i >= n - 6)
cols = {}
for px in range(380, 660, 4):
    cols[px] = [next((py for py in range(150, 420) if first_part(px, py)), None),
                next((py for py in range(150, 420) if (first_part(px, py) or "").startswith("lock.curtain")), None)]
res["outline_top_and_strand_top_per_column_px"] = cols
json.dump(res, open(OUT, "w"), indent=1)
print("HAIRLINE", json.dumps({k: v for k, v in res.items() if "summary" in k}))
sys.stdout.flush()
os._exit(0)
