"""Magmoo v3 translucency evidence at the TACTICAL camera (40 deg round, 55 deg down). In-memory only; never saves.

    blender --background <rigged/magmoo[__skin].blend> --factory-startup --python magmoo_translucency.py -- \
        <out_prefix> [--quick]

Measures how see-through the goo is, and shows what transparency SORTING does:
  floor transmittance T  the floor is an unlit (emission) checker; the unit is rendered over the checker and over its
                         inverse. The unit's own glow, specular and shading are identical in both, so they cancel in
                         |A - B|; what is left is the floor seen THROUGH the goo:  T = mean |A - B| inside the unit's
                         silhouette / mean |A - B| on the open floor (linear light). 0 = opaque, 1 = invisible.
  alpha sweep            goo alpha a in SWEEP (region alphas keep the palette's offsets from its 'goo' alpha; eye +
                         pupil stay opaque): the look tile (lit floor) + T per a  -> <prefix>_alpha_sweep.png
  sorting modes          at the palette alpha: Eevee DITHERED (order independent: the reference, every layer), BLENDED
                         with overlap (per-object sort, triangles in index order, no depth write -- the stand-in for a
                         game engine's plain alpha blend) and BLENDED without overlap (per-object depth prepass -- the
                         stand-in for Godot's depth-prepass alpha): look tiles + T + mean |mode - dithered| inside the
                         silhouette (0-255)  -> <prefix>_sorting_modes.png
--quick: only T at the palette alpha (DITHERED), for a skin variant.
Numbers -> <prefix>_translucency.json.
"""
import bpy, sys, os, math, json
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
PREFIX = argv[0]
QUICK = "--quick" in argv
SWEEP = [0.35, 0.45, 0.55, 0.70, 0.85, 1.0]
RES = 384
scene = bpy.context.scene
rig = next((o for o in scene.objects if o.type == "ARMATURE"), None)
if rig is not None:
    rig.data.pose_position = "REST"
meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
mat = meshes[0].data.materials[0]
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()

scene.render.engine = "BLENDER_EEVEE"
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.render.use_compositing = False
scene.render.use_sequencer = False
scene.eevee.taa_render_samples = 48
scene.render.resolution_x = scene.render.resolution_y = RES
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
world = bpy.data.worlds.new("survey_world"); scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.18, 0.18, 0.19, 1); bg.inputs[1].default_value = 0.6
lrig = bpy.data.objects.new("survey_rig", None); scene.collection.objects.link(lrig)
for name, energy, rot, col in (("s_key", 3.2, (50, 0, 150), (1, 1, 1)), ("s_fill", 1.0, (65, 0, 215), (0.85, 0.9, 1.0)),
                               ("s_rim", 2.0, (60, 0, 10), (1, 1, 1))):
    d = bpy.data.lights.new(name, "SUN"); d.energy = energy; d.color = col
    o = bpy.data.objects.new(name, d); scene.collection.objects.link(o)
    o.rotation_euler = [math.radians(a) for a in rot]; o.parent = lrig
lrig.rotation_euler = (0, 0, math.radians(180))

P = []
for o in meshes:
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co); ev.to_mesh_clear()
    co = co.reshape(-1, 3)
    if float(np.ptp(co, axis=0).max()) > 0.2:
        P.append(co[::5])
P = np.vstack(P)
lo, hi = P.min(0), P.max(0)
R = float(np.linalg.norm(hi - lo)) * 3
fme = bpy.data.meshes.new("s_floor")
fme.from_pydata([(-R, -R, 0), (R, -R, 0), (R, R, 0), (-R, R, 0)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("s_floor", fme); scene.collection.objects.link(floor)
lit = bpy.data.materials.new("s_floor_lit"); lit.use_nodes = True
lit.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.1, 0.11, 1)
lit.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
chk = bpy.data.materials.new("s_floor_checker"); chk.use_nodes = True
nt = chk.node_tree
for n in list(nt.nodes):
    nt.nodes.remove(n)
out = nt.nodes.new("ShaderNodeOutputMaterial"); em = nt.nodes.new("ShaderNodeEmission")
ct = nt.nodes.new("ShaderNodeTexChecker"); ct.inputs["Scale"].default_value = 1.0
tc = nt.nodes.new("ShaderNodeTexCoord")
mp = nt.nodes.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.0 / 0.35, 1.0 / 0.35, 1.0)
nt.links.new(tc.outputs["Object"], mp.inputs["Vector"]); nt.links.new(mp.outputs["Vector"], ct.inputs["Vector"])
nt.links.new(ct.outputs["Color"], em.inputs["Color"]); nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
fme.materials.append(lit)

cam_d = bpy.data.cameras.new("s_cam"); cam_d.lens = 50
cam = bpy.data.objects.new("s_cam", cam_d); scene.collection.objects.link(cam); scene.camera = cam
half = math.atan(18.0 / 50.0)
a, e = math.radians(40.0), math.radians(55.0)
dn = np.array([math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)])
right = np.cross([0, 0, 1.0], dn); right /= np.linalg.norm(right); up = np.cross(dn, right)
pr, pu = P @ right, P @ up
c = right * (pr.min() + pr.max()) / 2 + up * (pu.min() + pu.max()) / 2 + dn * float(np.mean(P @ dn))
rad = max(pr.max() - pr.min(), pu.max() - pu.min()) / 2
dist = rad / math.tan(half) * 1.25 + float((P @ dn).max() - np.mean(P @ dn))
cam.location = Vector(c + dn * dist)
cam.rotation_euler = (Vector(c) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam_d.clip_start = dist * 0.01; cam_d.clip_end = dist * 20

# region alphas as painted (from the Col attribute) + their regions: the sweep shifts every non-eye region by the
# same offset the goo alpha moves
REG = {}
for o in meshes:
    me = o.data
    names = list(me["conquest_regions"])
    rid = np.empty(len(me.polygons), dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", rid)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
    cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", cd)
    cd = cd.reshape(-1, 4)
    REG[o.name] = (names, rid, lt, cd.copy())
PAL_ALPHA = {}
for names, rid, lt, cd in REG.values():
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    for r in np.unique(rid):
        PAL_ALPHA[names[r]] = round(float(cd[ls[rid == r][0], 3]), 4)
GOO_A = PAL_ALPHA.get("goo", 1.0)


def set_goo_alpha(a_goo):
    for o in meshes:
        names, rid, lt, cd = REG[o.name]
        cd2 = cd.copy()
        fa = np.array([1.0 if names[r] in ("eye", "pupil") else min(1.0, max(0.0, PAL_ALPHA[names[r]] + a_goo - GOO_A))
                       for r in range(len(names))], np.float32)
        cd2[:, 3] = np.repeat(fa[rid], lt)
        o.data.color_attributes["Col"].data.foreach_set("color", cd2.ravel())
        o.data.update()


def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def render(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(RES, RES, 4)[:, :, :3]
    bpy.data.images.remove(img)
    return px


def floor_mode(kind):
    fme.materials[0] = lit if kind == "lit" else chk
    if kind in ("A", "B"):
        ct.inputs["Color1"].default_value = (1, 1, 1, 1) if kind == "A" else (0, 0, 0, 1)
        ct.inputs["Color2"].default_value = (0, 0, 0, 1) if kind == "A" else (1, 1, 1, 1)


# the silhouette mask: every unit mesh flat white emission over a black emission floor
white = bpy.data.materials.new("s_white"); white.use_nodes = True
wn = white.node_tree
for n in list(wn.nodes):
    wn.nodes.remove(n)
wo = wn.nodes.new("ShaderNodeOutputMaterial"); we = wn.nodes.new("ShaderNodeEmission")
wn.links.new(we.outputs["Emission"], wo.inputs["Surface"])
saved = {o.name: [s.material for s in o.material_slots] for o in meshes}
for o in meshes:
    for s in o.material_slots:
        s.material = white
floor_mode("A"); ct.inputs["Color1"].default_value = (0, 0, 0, 1); ct.inputs["Color2"].default_value = (0, 0, 0, 1)
bg.inputs[1].default_value = 0.0
tmp = PREFIX + "_tmp.png"
MASK = render(tmp).mean(2) > 0.5
bg.inputs[1].default_value = 0.6
for o in meshes:
    for s, m_ in zip(o.material_slots, saved[o.name]):
        s.material = m_
OPEN = ~MASK


def transmittance():
    floor_mode("A"); A = srgb_to_lin(render(tmp))
    floor_mode("B"); B = srgb_to_lin(render(tmp))
    D = np.abs(A - B).mean(2)
    ref = float(np.median(D[OPEN]))
    return float(D[MASK].mean() / ref), float(np.percentile(D[MASK] / ref, 90))


res = {"camera": "tactical: 40 deg round, 55 deg down, fit 1.25", "resolution": RES, "silhouette_px": int(MASK.sum()),
       "palette_region_alpha": PAL_ALPHA, "palette_goo_alpha": GOO_A,
       "rule": "T = mean |A-B| inside the silhouette / median |A-B| on open floor (unlit checker A vs inverted B, "
               "linear light): 0 opaque, 1 invisible"}
mat.surface_render_method = "DITHERED"
if QUICK:
    t_, t90 = transmittance()
    res["dithered_at_palette_alpha"] = {"T_mean": round(t_, 4), "T_p90": round(t90, 4)}
else:
    tiles, sweep = [], []
    for a_ in SWEEP:
        set_goo_alpha(a_)
        t_, t90 = transmittance()
        floor_mode("lit"); tiles.append(render(tmp))
        sweep.append({"goo_alpha": a_, "T_mean": round(t_, 4), "T_p90": round(t90, 4)})
        print("SWEEP", sweep[-1])
    res["alpha_sweep"] = sweep
    rows = [np.concatenate(tiles[i * 3:(i + 1) * 3], axis=1) for i in (1, 0)]
    sheet = np.concatenate(rows, axis=0)
    set_goo_alpha(GOO_A)
    modes, mt = [], []
    ref_img = None
    for label, method, overlap in (("dithered", "DITHERED", True), ("blended_overlap", "BLENDED", True),
                                   ("blended_prepass", "BLENDED", False)):
        mat.surface_render_method = method
        mat.use_transparency_overlap = overlap
        t_, t90 = transmittance()
        floor_mode("lit"); img = render(tmp)
        if ref_img is None:
            ref_img = img
        diff = float(np.abs(img - ref_img)[MASK].mean() * 255.0)
        modes.append({"mode": label, "T_mean": round(t_, 4), "T_p90": round(t90, 4), "mean_abs_diff_vs_dithered_0_255": round(diff, 3)})
        mt.append(img)
        print("MODE", modes[-1])
    res["sorting_modes"] = modes
    mat.surface_render_method = "DITHERED"; mat.use_transparency_overlap = True
    for arr, name in ((sheet, "alpha_sweep"), (np.concatenate(mt, axis=1), "sorting_modes")):
        h, w = arr.shape[:2]
        im = bpy.data.images.new(name, w, h, alpha=True)
        im.pixels.foreach_set(np.dstack([arr, np.ones((h, w, 1), np.float32)]).ravel())
        im.filepath_raw = "%s_%s.png" % (PREFIX, name); im.file_format = "PNG"; im.save()
        bpy.data.images.remove(im)
        print("WROTE", "%s_%s.png" % (PREFIX, name))
if os.path.exists(tmp):
    os.remove(tmp)
json.dump(res, open(PREFIX + "_translucency.json", "w"), indent=1)
print("TRANSLUCENCY", json.dumps(res))
sys.stdout.flush()
os._exit(0)
