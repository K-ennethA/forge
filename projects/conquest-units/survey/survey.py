"""Conquest creature survey: inventory + consistent stills, headless, never saves.

    blender --background <copy.blend> --factory-startup --python survey.py -- blend <out_prefix> <out.json>
    blender --background --factory-startup --python survey.py -- glb <in.glb> <out_prefix> <out.json> [yaw_deg]

blend mode inspects the OPENED file (always a source-copies/ byte copy) and renders
front (camera on -Y, the Conquest sculpt convention) + three-quarter (40 deg).
glb mode imports the shipped game .glb (read only) into an empty scene, applies the
roster's model_yaw_deg, and renders front + three-quarter + a tactical-zoom thumb
(high angle, 256 px, unit ~100 px tall -- the size docs/BLENDER_RIGGING.md quotes for
gameplay zoom). Lighting copies projects/werewolf/export/game-drop/render_look.py
(key/fill/rim suns + grey world, Standard view transform) so looks compare.
Nothing here calls save; the process exits after writing JSON + PNGs.
"""
import bpy, sys, os, math, json
from mathutils import Vector
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.normpath(os.path.join(HERE, "..", "..", "..", "addon"))
sys.path.insert(0, ADDON)
from forge.tools import verify  # uv_metrics: reuse the werewolf-audit measurement

argv = sys.argv[sys.argv.index("--") + 1:]
MODE = argv[0]
if MODE == "blend":
    SRC, PREFIX, OUT_JSON = bpy.data.filepath, argv[1], argv[2]
    YAW = 0.0
else:
    SRC, PREFIX, OUT_JSON = argv[1], argv[2], argv[3]
    YAW = float(argv[4]) if len(argv) > 4 else 0.0
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=SRC)

# hero wave (2026-09-25): hero originals live one level up, in Documents\ -- override via env.
# SURVEY_TACTICAL=1 adds the glb-mode tactical view to blend mode. Both unset = prior behaviour.
ORIG_DIR = os.environ.get("SURVEY_ORIG_DIR", r"C:\Users\kenne\OneDrive\Documents\CONQUEST")
BLEND_TACTICAL = os.environ.get("SURVEY_TACTICAL") == "1"
scene = bpy.context.scene
dg =bpy.context.evaluated_depsgraph_get()


def shells_and_manifold(me):
    nv, ne = len(me.vertices), len(me.edges)
    if nv == 0:
        return 0, 0, 0
    ev = np.empty(ne * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev)
    a, b = ev[0::2], ev[1::2]
    lab = np.arange(nv)
    for _ in range(200):
        m = np.minimum(lab[a], lab[b])
        new = lab.copy()
        np.minimum.at(new, a, m); np.minimum.at(new, b, m)
        new = new[new]
        if np.array_equal(new, lab):
            break
        lab = new
    shells = int(len(np.unique(lab)))
    le = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("edge_index", le)
    cnt = np.bincount(le, minlength=ne)
    return shells, int((cnt == 1).sum()), int((cnt > 2).sum())


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def smooth_fraction(me):
    if not len(me.polygons):
        return None
    try:
        sm = np.empty(len(me.polygons), dtype=bool); me.polygons.foreach_get("use_smooth", sm)
        return round(float(sm.mean()), 3)
    except Exception:
        return None


def material_info(mat):
    info = {"name": mat.name, "users": mat.users, "use_nodes": mat.use_nodes,
            "viewport_color": [round(c, 3) for c in mat.diffuse_color]}
    if mat.use_nodes and mat.node_tree:
        nodes = mat.node_tree.nodes
        info["node_types"] = sorted({n.bl_idname for n in nodes})
        imgs = []
        for n in nodes:
            if n.bl_idname == "ShaderNodeTexImage" and n.image:
                im = n.image
                path = bpy.path.abspath(im.filepath) if im.filepath else ""
                # relative paths are relative to the ORIGINAL folder, not source-copies/
                orig = (os.path.join(ORIG_DIR, im.filepath[2:]) if im.filepath.startswith("//") else path)
                imgs.append({"image": im.name, "filepath": im.filepath,
                             "packed": im.packed_file is not None,
                             "file_exists": bool(path) and (os.path.exists(path) or os.path.exists(orig)),
                             "size": list(im.size), "linked": any(o.is_linked for o in n.outputs)})
            if n.bl_idname == "ShaderNodeBsdfPrincipled":
                bc = n.inputs["Base Color"]
                info["principled_base_color"] = None if bc.is_linked else [round(c, 3) for c in bc.default_value]
                info["base_color_linked_from"] = bc.links[0].from_node.bl_idname if bc.is_linked else None
                info["roughness"] = None if n.inputs["Roughness"].is_linked else round(n.inputs["Roughness"].default_value, 3)
        info["images"] = imgs
        info["uses_color_attribute"] = any(n.bl_idname in ("ShaderNodeVertexColor", "ShaderNodeAttribute") for n in nodes)
    return info


def action_info(act):
    nfc = 0
    try:
        nfc = len(act.fcurves)
    except Exception:
        pass
    try:
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    nfc += len(bag.fcurves)
    except Exception:
        pass
    return {"name": act.name, "frame_range": [round(f, 1) for f in act.frame_range],
            "fcurves": nfc, "users": act.users, "fake_user": act.use_fake_user}


report = {"mode": MODE, "source": SRC, "yaw_deg": YAW, "objects": [], "unit_scale": scene.unit_settings.scale_length,
          "scenes": [s.name for s in bpy.data.scenes]}
view_layer_objs = set(o.name for o in bpy.context.view_layer.objects)
render_meshes = []
for o in bpy.data.objects:
    ent = {"name": o.name, "type": o.type, "parent": o.parent.name if o.parent else None,
           "in_view_layer": o.name in view_layer_objs, "hide_render": o.hide_render,
           "hide_viewport": o.hide_get() if o.name in view_layer_objs else None,
           "dimensions": [round(d, 3) for d in o.dimensions],
           "scale": [round(s, 3) for s in o.scale],
           "modifiers": [{"type": m.type, "name": m.name,
                          **({"levels": m.levels, "render_levels": m.render_levels, "sculpt_levels": getattr(m, "sculpt_levels", None)} if m.type == "MULTIRES" else {}),
                          **({"object": m.object.name if m.object else None} if m.type == "ARMATURE" else {}),
                          **({"ratio": round(m.ratio, 4)} if m.type == "DECIMATE" else {})} for m in o.modifiers]}
    if o.type == "MESH":
        me = o.data
        shells, boundary, nonman = shells_and_manifold(me)
        ent.update({"verts": len(me.vertices), "faces": len(me.polygons), "tris_base": tri_count(me),
                    "loose_shells": shells, "boundary_edges": boundary, "nonmanifold_edges_gt2": nonman,
                    "smooth_face_fraction": smooth_fraction(me),
                    "uv_layers": [l.name for l in me.uv_layers],
                    "color_attributes": [f"{c.name}:{c.domain}:{c.data_type}" for c in me.color_attributes],
                    "materials": [s.material.name if s.material else None for s in o.material_slots],
                    "vertex_groups": len(o.vertex_groups),
                    "shape_keys": [k.name for k in me.shape_keys.key_blocks] if me.shape_keys else []})
        try:
            ev = o.evaluated_get(dg)
            eme = ev.to_mesh()
            ent["tris_evaluated"] = tri_count(eme)
            ev.to_mesh_clear()
        except Exception as e:
            ent["tris_evaluated"] = f"error {e}"
        if me.uv_layers:
            try:
                uv = verify.uv_metrics(me)
                ent["uv"] = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in uv.items()}
                ent["uv"]["overlap_note"] = uv["overlap"].get("note")
                ent["uv"]["distortion_detail"] = uv["area_distortion"].get("detail")
            except Exception as e:
                ent["uv"] = f"error {e}"
        if o.name in view_layer_objs and not o.hide_render and o.visible_get():
            render_meshes.append(o)
    if o.type == "ARMATURE":
        ent["bones"] = len(o.data.bones)
        ent["bone_names"] = [b.name for b in o.data.bones][:60]
        ad = o.animation_data
        ent["active_action"] = ad.action.name if ad and ad.action else None
        ent["nla_tracks"] = [t.name for t in ad.nla_tracks] if ad else []
    report["objects"].append(ent)

report["materials"] = [material_info(m) for m in bpy.data.materials]
report["images"] = [{"name": i.name, "filepath": i.filepath, "packed": i.packed_file is not None, "size": list(i.size)} for i in bpy.data.images]
report["actions"] = [action_info(a) for a in bpy.data.actions]
report["armatures"] = [a.name for a in bpy.data.armatures]

# glb: the importer adds a bone-shape icosphere for armatures; keep meshes that are real
if MODE == "glb":
    render_meshes = [o for o in scene.objects if o.type == "MESH" and not o.name.startswith("Icosphere")]
    # apply roster yaw by rotating a parent empty around world Z
    piv = bpy.data.objects.new("yaw", None); scene.collection.objects.link(piv)
    for o in scene.objects:
        if o.parent is None and o is not piv:
            o.parent = piv
    piv.rotation_euler = (0, 0, math.radians(YAW))
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()

lo = Vector((1e9, 1e9, 1e9)); hi = -lo
tot_render_tris = 0
for o in render_meshes:
    ev = o.evaluated_get(dg)
    for c in ev.bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    try:
        eme = ev.to_mesh(); tot_render_tris += tri_count(eme); ev.to_mesh_clear()
    except Exception:
        pass
report["render_meshes"] = [o.name for o in render_meshes]
report["render_tris_total"] = tot_render_tris
report["bbox_min"] = [round(v, 3) for v in lo]; report["bbox_max"] = [round(v, 3) for v in hi]
size = hi - lo
report["bbox_size_wdh"] = [round(size.x, 3), round(size.y, 3), round(size.z, 3)]

# ---- render setup (in memory only) ----
for o in scene.objects:
    if o.type in ("LIGHT", "CAMERA"):
        o.hide_render = True
scene.render.engine = "BLENDER_EEVEE"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
try:
    scene.eevee.taa_render_samples = 32
except Exception:
    pass
world = bpy.data.worlds.new("survey_world"); scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.19, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6

rig = bpy.data.objects.new("survey_rig", None); scene.collection.objects.link(rig)


def light(name, energy, rot_deg, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = color
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot_deg]
    o.parent = rig
    return o


light("s_key", 3.2, (50, 0, 150)); light("s_fill", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)); light("s_rim", 2.0, (60, 0, 10))
# render_look's "front" is +Y; Conquest assets face -Y, so the light rig turns 180
rig.rotation_euler = (0, 0, math.radians(180))

centre = (lo + hi) / 2
radius = max(size.length / 2, 1e-3)
fm_me = bpy.data.meshes.new("s_floor"); R = radius * 6
fm_me.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fm_me); scene.collection.objects.link(floor)
floor.location = (centre.x, centre.y, lo.z)
fm = bpy.data.materials.new("s_floor"); fm.use_nodes = True
fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
fm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
fm_me.materials.append(fm)

cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam_d.clip_start = radius * 0.01; cam_d.clip_end = radius * 100
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam)
scene.camera = cam
half_fov = math.atan(18.0 / 50.0)
# Conquest glbs round-trip the sculpt's -Y face back to Blender -Y (glTF +Z), so both modes
# look from -Y; the roster yaw is applied above for glbs, as the game does.
front_sign = -1.0


def aim(angle_deg, elev_deg, fill=1.08):
    a, e = math.radians(angle_deg), math.radians(elev_deg)
    dist = radius / math.sin(half_fov) * fill
    d = Vector((math.sin(a) * math.cos(e), front_sign * math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = centre + d * dist
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()


outs = []
views = [("front", 0.0, 5.0, 1024, 1.0), ("threequarter", 40.0, 15.0, 1024, 1.0)]
if MODE == "glb" or BLEND_TACTICAL:
    # tactical zoom: 55 deg down, three-quarter; unit ~100 px tall in a 256 frame
    views.append(("tactical", 40.0, 55.0, 256, 1.6))
if os.environ.get("SURVEY_SIDE") == "1":
    # facing evidence: profile from +X (90 deg), 512 px -- a nose/visor/foot direction reads here
    views.append(("side", 90.0, 5.0, 512, 1.0))
for tag, ang, elev, res, fill in views:
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    aim(ang, elev, fill)
    scene.render.filepath = "%s_%s.png" % (PREFIX, tag)
    bpy.ops.render.render(write_still=True)
    outs.append(scene.render.filepath)
    print("WROTE", scene.render.filepath)
report["renders"] = outs

with open(OUT_JSON, "w") as f:
    json.dump(report, f, indent=1)
print("SURVEY_JSON", OUT_JSON)
