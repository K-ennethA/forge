"""Conquest unit contract checker (seed). Read-only: opens a blend, measures, never saves.

    blender --background <unit.blend> --factory-startup --python conquest_contract_check.py -- <out.json>
        [--max-footprint 1.9] [--max-height 1.8] [--tris MIN MAX] [--facing-tol 30]

Checks every render-visible mesh together (the unit as the game would receive it):
  cell_fit      footprint max(w, d) <= max_footprint and height <= max_height (Conquest cell
                is 2.0; ceilings from tools/blender/assets.conf: 1.9 / 1.8 default, the
                4-cell boss 3.8 / 3.4). Object custom props conquest_max_* override the defaults,
                CLI flags override both.
  feet_origin   min z within 0.005 of 0, bbox centre X/Y within 0.01 of the origin,
                object transforms identity (applied).
  facing_-Y     conquest_front_anchor -> conquest_front_landmark (object custom props, set by
                the facing audit from a direction-free identity feature) points within
                facing_tol degrees of -Y. Missing props = FAIL (facing is unverifiable).
  tri_budget    triangle count within conquest_tri_budget [min, max] (the declared tier).
  uv_health     one UV layer; zero-area UV faces <= 0.5 %; flipped UV faces = 0; overlap
                heuristic false (forge verify.uv_metrics, raster 512).
  flat_shaded   0 smooth faces (faceted look, docs/BLENDER_RIGGING.md "Facet, don't smooth").
  colour        a colour attribute exists and the material reads it.
Prints CHECK <json> and 'N checks, M failed'; exits 1 on any failure.
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "addon")))
from forge.tools import verify  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv and not argv[0].startswith("--") else None


def flag(name, n=1, cast=float):
    if name in argv:
        i = argv.index(name)
        vals = [cast(v) for v in argv[i + 1:i + 1 + n]]
        return vals if n > 1 else vals[0]
    return None


meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render]
checks = []


def check(name, ok, **detail):
    checks.append({"check": name, "pass": bool(ok), **detail})


if not meshes:
    check("has_mesh", False)
else:
    main = max(meshes, key=lambda o: len(o.data.polygons))
    props = {k: main[k] for k in main.keys() if k.startswith("conquest_")}
    max_fp = flag("--max-footprint") or props.get("conquest_max_footprint", 1.9)
    max_h = flag("--max-height") or props.get("conquest_max_height", 1.8)
    tris_rng = flag("--tris", 2, int) or list(props.get("conquest_tri_budget", [0, 10 ** 9]))
    tol = flag("--facing-tol") or 30.0

    dg = bpy.context.evaluated_depsgraph_get()
    pts, tris, smooth, zero_uv, flipped_tot, faces_tot = [], 0, 0, 0, 0, 0
    uv_reports, colour_ok, transforms_ok = [], True, True
    for o in meshes:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
        M = np.array(o.matrix_world)
        pts.append(co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3])
        lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
        tris += int((lt - 2).sum())
        sm = np.empty(len(me.polygons), dtype=bool); me.polygons.foreach_get("use_smooth", sm)
        smooth += int(sm.sum())
        faces_tot += len(me.polygons)
        transforms_ok &= bool(np.abs(np.array(o.matrix_world) - np.eye(4)).max() < 1e-6)
        if len(me.uv_layers) >= 1:
            u = verify.uv_metrics(me)
            # zero-area UV faces (fan-triangulated shoelace per polygon)
            uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 2)
            ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
            area = np.zeros(len(me.polygons))
            for j in range(1, lt.max() - 1):
                m = lt > j + 1
                a, b, c = uv[ls[m]], uv[ls[m] + j], uv[ls[m] + j + 1]
                area[m] += 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
            zero_uv += int((np.abs(area) < 1e-9).sum())
            flipped_tot += int(u["flipped_faces"]["value"])
            uv_reports.append({"mesh": o.name, "layers": len(me.uv_layers), "islands": u["islands"]["value"],
                               "flipped": u["flipped_faces"]["value"], "overlap": u["overlap"]["value"],
                               "overlap_note": u["overlap"].get("note"), "coverage": u["coverage"]["value"],
                               "area_distortion_p95": (u["area_distortion"].get("detail") or {}).get("p95")})
        else:
            uv_reports.append({"mesh": o.name, "layers": 0})
        has_col = len(me.color_attributes) > 0
        reads = any(n.bl_idname in ("ShaderNodeVertexColor", "ShaderNodeAttribute") and n.outputs[0].is_linked
                    for s in o.material_slots if s.material and s.material.node_tree for n in s.material.node_tree.nodes)
        colour_ok &= has_col and reads
        ev.to_mesh_clear()
    P = np.vstack(pts)
    lo, hi = P.min(0), P.max(0)
    size = hi - lo
    fp = float(max(size[0], size[1]))
    check("cell_fit", fp <= max_fp + 1e-4 and size[2] <= max_h + 1e-4,
          footprint=round(fp, 4), height=round(float(size[2]), 4), width=round(float(size[0]), 4),
          depth=round(float(size[1]), 4), max_footprint=max_fp, max_height=max_h)
    cxy = (lo[:2] + hi[:2]) / 2
    check("feet_origin", abs(lo[2]) <= 0.005 and np.abs(cxy).max() <= 0.01 and transforms_ok,
          min_z=round(float(lo[2]), 5), centre_xy=cxy.round(5).tolist(), transforms_applied=bool(transforms_ok))
    a, l = props.get("conquest_front_anchor"), props.get("conquest_front_landmark")
    if a is None or l is None:
        check("facing_-Y", False, reason="no conquest_front_anchor/landmark props: facing unverifiable")
    else:
        d = Vector(l) - Vector(a)
        ang = math.degrees(math.atan2(d.x, -d.y))
        check("facing_-Y", abs(ang) <= tol and math.hypot(d.x, d.y) > 1e-6, angle_from_minusY_deg=round(ang, 2),
              tolerance_deg=tol, rule=props.get("conquest_facing_rule"), yaw_fix_deg=props.get("conquest_yaw_fix_deg"))
    declared = flag("--tris", 2, int) is not None or "conquest_tri_budget" in props
    check("tri_budget", declared and tris_rng[0] <= tris <= tris_rng[1], tris=tris, budget=list(tris_rng),
          tier=props.get("conquest_tier"), declared=declared)   # an undeclared tier is a FAIL, not a pass
    uv_ok = all(r.get("layers", 0) >= 1 and not r.get("overlap") for r in uv_reports) and \
        zero_uv / max(faces_tot, 1) <= 0.005 and flipped_tot == 0
    check("uv_health", uv_ok, zero_area_faces=zero_uv, zero_area_pct=round(100.0 * zero_uv / max(faces_tot, 1), 3),
          flipped=flipped_tot, meshes=uv_reports)
    check("flat_shaded", smooth == 0, smooth_faces=smooth, faces=faces_tot)
    check("colour", colour_ok)

failed = [c for c in checks if not c["pass"]]
res = {"file": bpy.data.filepath, "checks": checks, "passed": len(checks) - len(failed), "failed": len(failed)}
print("CHECK", json.dumps(res))
print("%d checks, %d failed" % (len(checks), len(failed)))
if OUT:
    json.dump(res, open(OUT, "w"), indent=1)
sys.stdout.flush()
os._exit(1 if failed else 0)
