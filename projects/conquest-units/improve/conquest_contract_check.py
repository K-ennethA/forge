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
  flat_shaded   0 smooth faces (faceted look, docs/BLENDER_RIGGING.md "Facet, don't smooth"). A face is smooth when
                use_smooth is set OR any corner normal leaves its face normal by > FLAT_TOL_DEG (authored custom
                normals on a sharp face count too). Scoped exemption (2026-10-03, research H5 hair normals): smooth
                faces are allowed only in the palette regions the mesh declares in its 'conquest_smooth_regions'
                prop (read through the region_id face attribute + 'conquest_regions'), and only hair-family regions
                (names starting with SMOOTH_OK_PREFIX) may be declared; anything else declared = FAIL.
  colour        a colour attribute exists and the material reads it.
Rigged files (an ARMATURE is present; rig wave 2026-09-25) are measured at the REST pose for the
seven checks above (feet-at-origin and cell-fit must survive rigging), plus:
  rig_root      bone 'root' exists, is parentless and non-deforming, its head is at the origin,
                the armature object transform is identity, and in every frame of every clip the
                posed root head stays at the origin (< 1e-6: clips are in place; Conquest glides).
  clip_names    every action is named idle / walk (or idle-loop / walk-loop), and Conquest's
                UnitAnimator._find_clip (exact name, then the part after '|', then 'contains',
                case-insensitive) resolves CLIP_IDLE and CLIP_WALK to two distinct clips.
  clip_loops    per clip: 24 fps, manual frame range + cyclic flag, and the evaluated mesh at the
                first and last frame agree to < 1 mm (seam closed). Clip extents are reported.
  skin          an Armature modifier on the unit mesh targets the rig; every vertex's deform
                weights sum to 1 (+-1e-3); no unweighted vertex; <= 4 influences (glTF joints).
Prints CHECK <json> and 'N checks, M failed'; exits 1 on any failure.
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
SMOOTH_OK_PREFIX = ("hair",)   # flat_shaded exemption cap: only hair-family palette regions may ship smooth/authored normals
FLAT_TOL_DEG = 0.5             # a sharp face's corner normals equal its face normal exactly (measured 0.0 deg, Blender 5.0);
                               #   0.5 deg = far below any visible shading turn, far above float noise
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
rigs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
for r_ in rigs:                       # the seven base checks measure the bind (rest) pose
    r_.data.pose_position = "REST"
bpy.context.view_layer.update()
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
    smooth_exempt, smooth_bad, declared_bad, exempt_rows = 0, 0, [], []
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
        if len(me.loops) and me.has_custom_normals:   # (no custom normals: a sharp face's corners ARE its face normal)
            cn_ = np.empty(len(me.loops) * 3); me.corner_normals.foreach_get("vector", cn_); cn_ = cn_.reshape(-1, 3)
            pn_ = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("normal", pn_); pn_ = pn_.reshape(-1, 3)
            lp_ = np.repeat(np.arange(len(lt)), lt)
            dev_ = np.degrees(np.arctan2(np.linalg.norm(np.cross(cn_, pn_[lp_]), axis=1), np.einsum("ij,ij->i", cn_, pn_[lp_])))
            fdev_ = np.zeros(len(lt)); np.maximum.at(fdev_, lp_, dev_)
            sm = sm | (fdev_ > FLAT_TOL_DEG)
        smooth += int(sm.sum())
        decl_ = [str(n_) for n_ in o.data.get("conquest_smooth_regions", [])]
        names_ = [str(n_) for n_ in o.data.get("conquest_regions", [])]
        declared_bad += [n_ for n_ in decl_ if not n_.startswith(SMOOTH_OK_PREFIX) or n_ not in names_]
        ok_ = np.zeros(len(lt), bool)
        ok_names_ = [n_ for n_ in decl_ if n_.startswith(SMOOTH_OK_PREFIX) and n_ in names_]
        if ok_names_ and "region_id" in me.attributes:
            rid_ = np.empty(len(lt), dtype=np.int64); me.attributes["region_id"].data.foreach_get("value", rid_)
            ok_ = np.isin(rid_, [names_.index(n_) for n_ in ok_names_])
        smooth_exempt += int((sm & ok_).sum()); smooth_bad += int((sm & ~ok_).sum())
        if decl_:
            exempt_rows.append({"mesh": o.name, "declared": decl_, "smooth_in_declared": int((sm & ok_).sum()),
                                "declared_faces": int(ok_.sum())})
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
    check("flat_shaded", smooth_bad == 0 and not declared_bad, smooth_faces=smooth, faces=faces_tot,
          smooth_outside_exemption=smooth_bad, smooth_exempt=smooth_exempt, exemption=exempt_rows or None,
          declared_not_allowed=declared_bad or None, rule="use_smooth or corner normal > %.1f deg off the face normal; "
          "exempt only in declared hair-family regions" % FLAT_TOL_DEG)
    check("colour", colour_ok)

if meshes and rigs:
    sys.path.insert(0, HERE)
    import rigkit as K  # noqa: E402
    scene = bpy.context.scene
    rig = rigs[0]
    rig.data.pose_position = "POSE"
    main = max(meshes, key=lambda o: len(o.data.polygons))
    # --- rig_root
    rb = rig.data.bones.get("root")
    rig_ident = bool(np.abs(np.array(rig.matrix_world) - np.eye(4)).max() < 1e-6)
    root_ok = rb is not None and rb.parent is None and not rb.use_deform and \
        Vector(rb.head_local).length < 1e-6 and rig_ident
    acts = list(bpy.data.actions)
    worst_root = 0.0
    clip_rows = []
    for act in acts:
        K.assign_action(rig, act)
        f0, f1 = int(round(act.frame_range[0])), int(round(act.frame_range[1]))
        lo_c, hi_c = np.full(3, 1e9), np.full(3, -1e9)
        coords = {}
        for f in range(f0, f1 + 1):
            scene.frame_set(f)
            if rb is not None:
                worst_root = max(worst_root, (rig.matrix_world @ rig.pose.bones["root"].head).length)
            ev = main.evaluated_get(bpy.context.evaluated_depsgraph_get())
            me = ev.to_mesh()
            co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
            ev.to_mesh_clear()
            lo_c = np.minimum(lo_c, co.min(0)); hi_c = np.maximum(hi_c, co.max(0))
            if f in (f0, f1):
                coords[f] = co
        seam = float(np.linalg.norm(coords[f0] - coords[f1], axis=1).max()) * 1000.0
        clip_rows.append({"clip": act.name, "frames": [f0, f1], "cyclic": bool(getattr(act, "use_cyclic", False)),
                          "manual_range": bool(act.use_frame_range), "seam_mm": round(seam, 4),
                          "extent": {"footprint": round(float(max(hi_c[0] - lo_c[0], hi_c[1] - lo_c[1])), 4),
                                     "height": round(float(hi_c[2] - lo_c[2]), 4), "min_z": round(float(lo_c[2]), 4)}})
    if rig.animation_data is not None:   # (a clip-less rig has no animation data: guard, 2026-10-03 elias v4 lane)
        rig.animation_data.action = None
    check("rig_root", root_ok and worst_root < 1e-6, root_present=rb is not None,
          root_head=[round(v, 6) for v in rb.head_local] if rb else None, armature_identity=rig_ident,
          root_max_offset_over_clips=round(worst_root, 8), bones=len(rig.data.bones))
    # --- clip_names (Conquest UnitAnimator._find_clip, 3 passes)
    names = [a.name for a in acts]

    def find_clip(base):
        want = base.lower()
        for n in names:
            if n.lower() == want:
                return n, "exact"
        for n in names:
            if n.split("|")[-1].lower() == want:
                return n, "after-|"
        for n in names:
            if want in n.lower():
                return n, "contains"
        return None, None
    idle_c, idle_how = find_clip("idle")
    walk_c, walk_how = find_clip("walk")
    bad = [n for n in names if n not in K.ALLOWED_CLIP_NAMES]
    check("clip_names", not bad and idle_c is not None and walk_c is not None and idle_c != walk_c,
          clips=names, not_allowed=bad, idle=[idle_c, idle_how], walk=[walk_c, walk_how])
    # --- clip_loops
    fps = scene.render.fps / scene.render.fps_base
    check("clip_loops", fps == K.FPS and bool(clip_rows) and
          all(r["cyclic"] and r["manual_range"] and r["seam_mm"] < 1.0 for r in clip_rows),
          fps=fps, clips=clip_rows)
    # --- skin
    mods = [m for m in main.modifiers if m.type == "ARMATURE" and m.object is rig]
    deform = {b.name for b in rig.data.bones if b.use_deform}
    gidx = {g.index: g.name for g in main.vertex_groups if g.name in deform}
    sums, infl = [], []
    for v in main.data.vertices:
        ws = [g.weight for g in v.groups if g.group in gidx and g.weight > 0.0]
        sums.append(sum(ws)); infl.append(len(ws))
    sums = np.array(sums); infl = np.array(infl)
    check("skin", bool(mods) and bool(np.all(np.abs(sums - 1.0) < 1e-3)) and int((infl == 0).sum()) == 0 and int(infl.max()) <= 4,
          armature_modifier=bool(mods), weight_sum_min=round(float(sums.min()), 5), weight_sum_max=round(float(sums.max()), 5),
          unweighted=int((infl == 0).sum()), max_influences=int(infl.max()), deform_bones=len(deform))

failed = [c for c in checks if not c["pass"]]
res = {"file": bpy.data.filepath, "checks": checks, "passed": len(checks) - len(failed), "failed": len(failed)}
print("CHECK", json.dumps(res))
print("%d checks, %d failed" % (len(checks), len(failed)))
if OUT:
    json.dump(res, open(OUT, "w"), indent=1)
sys.stdout.flush()
os._exit(1 if failed else 0)
