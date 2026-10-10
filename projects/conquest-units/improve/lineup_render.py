# Size lineup: every Conquest unit standing in a row on one floor, with a metre ruler at both ends.
# Headless Blender (run via improve/lineup_run.ps1). Reads rigged/<unit>.glb (skips <unit>__<skin> variants), reads the
# Conquest tree READ-ONLY for the delivered scale (.import root_scale x roster .tres model_scale), merges
# design/size-table.json (the artist's file: edit target_height_m), writes renders/lineup/*.png + lineup_table.json.
#
#   blender --background --factory-startup --python lineup_render.py -- [--conquest <tree>] [--only ingame,target,tq]
#
# Each unit is posed the way the game shows it standing (idle clip first frame when the glb has an idle clip, else the
# bind/rest pose), baked to static meshes, scaled, stood on z = 0 facing -Y, and laid out in a row.
import bpy, sys, os, re, json, math, hashlib, time, datetime
import numpy as np
from mathutils import Matrix, Vector

# ---- knobs (artist-facing names in the comments) -------------------------------------------------------------------
GAP_FRAC = 0.25            # gap between units = this x the tallest unit's height
CELL_H, CELL_FP = 1.8, 1.9           # 1x1 cell fit: root_scale = min(CELL_H/h, CELL_FP/fp)   (HANDOFF.md delivery pattern)
BOSS_H, BOSS_FP = 5.6, 3.8           # 2x2 boss fit: min(BOSS_H/h, BOSS_FP/fp)               (import_wave_report.md)
KIND_OVERRIDES = {"shadow_assassin": "humanoid"}   # kind for units the game cannot tell us (not yet in game)
SKIP_UNITS = set()         # unit ids to leave out of the lineup entirely
TICK_M = 0.25              # ruler tick spacing (m) when LABEL_UNITS = "m"
LABEL_UNITS = "ft"         # "ft" (artist 2026-10-09 sizes in feet: labels "6 ft 2 in", ruler ticks every 6 in, a
                           #   label + strong line every foot) or "m". Tables / size-table.json stay in metres.
UNIT_LEN = 0.3048 if LABEL_UNITS == "ft" else 1.0         # one labelled ruler unit (m)
TICK = UNIT_LEN / 2 if LABEL_UNITS == "ft" else TICK_M     # ruler tick spacing (m)
TICKS_PER_UNIT = int(round(UNIT_LEN / TICK))


def hstr(h):
    """height label in the chosen units"""
    if LABEL_UNITS != "ft":
        return "%.2f m" % h
    i = h / 0.0254; f = int(i // 12); r = int(round(i - 12 * f))
    if r == 12:
        f, r = f + 1, 0
    return "%d ft %d in" % (f, r)
PX_PER_M = 140             # ortho render density (capped by MAX_RES_X)
MAX_RES_X = 8000
TQ_YAW_DEG = 35.0          # three-quarter view: each unit turned this much about its own centre
TQ_ELEV_DEG = 14.0         # three-quarter view: camera elevation
TQ_RES_X = 8000
SAMPLES = 32
CONQUEST_CANDIDATES = [os.path.expanduser(r"~\OneDrive\Desktop\git\Conquest"),
                       os.path.expanduser(r"~\Desktop\git\Conquest"), os.path.expanduser(r"~\git\Conquest")]

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(HERE)
RIGGED = os.path.join(P, "rigged")
OUT = os.path.join(P, "renders", "lineup")
TABLE = os.path.join(P, "design", "size-table.json")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
def _arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default
ONLY = set((_arg("--only", "ingame,target,tq")).split(","))
T0 = time.time()
def log(*a):
    print("[lineup %6.1fs]" % (time.time() - T0), *a, flush=True)

# ---- Conquest tree (read-only text parsing) ------------------------------------------------------------------------
def find_conquest():
    c = _arg("--conquest")
    for d in ([c] if c else []) + CONQUEST_CANDIDATES:
        if d and os.path.isdir(os.path.join(d, "game", "characters", "models")):
            return d
    return None

def conquest_info(tree):
    info = {"tree": tree, "branch": None, "units": {}}
    if not tree:
        return info
    try:
        head = open(os.path.join(tree, ".git", "HEAD"), encoding="utf8").read().strip()
        info["branch"] = head.split("refs/heads/")[-1] if "refs/heads/" in head else head[:12]
    except OSError:
        pass
    models = os.path.join(tree, "game", "characters", "models")
    rosters = []
    for rd in [os.path.join(tree, "game", "characters", "roster"), os.path.join(tree, "game", "overworld", "content")]:
        if os.path.isdir(rd):
            rosters += [os.path.join(rd, f) for f in sorted(os.listdir(rd)) if f.endswith(".tres")]
    rtext = {r: open(r, encoding="utf8", errors="replace").read() for r in rosters}
    for biome in sorted(os.listdir(models)):
        bd = os.path.join(models, biome)
        if not os.path.isdir(bd):
            continue
        for f in sorted(os.listdir(bd)):
            m = re.match(r"(.+)_forge\.glb$", f)
            if not m:
                continue
            unit = m.group(1)
            imp = os.path.join(bd, f + ".import")
            rs, apply_rs = 1.0, True
            if os.path.exists(imp):
                t = open(imp, encoding="utf8").read()
                mm = re.search(r"^nodes/root_scale=([0-9.eE+-]+)", t, re.M)
                rs = float(mm.group(1)) if mm else 1.0
                ma = re.search(r"^nodes/apply_root_scale=(\w+)", t, re.M)
                apply_rs = (ma.group(1) == "true") if ma else True
            res_path = "res://game/characters/models/%s/%s" % (biome, f)
            refs = []
            for r, t in rtext.items():
                if res_path not in t:
                    continue
                ms = re.search(r"^model_scale = ([0-9.eE+-]+)", t, re.M)
                fp = re.search(r"^footprint = Vector2i\((\d+), *(\d+)\)", t, re.M)
                kd = re.search(r"^kind = (\d+)", t, re.M)
                dn = re.search(r'^display_name = "([^"]*)"', t, re.M)
                refs.append({"tres": os.path.relpath(r, tree).replace("\\", "/"),
                             "stem": os.path.splitext(os.path.basename(r))[0],
                             "display_name": dn.group(1) if dn else None,
                             "model_scale": float(ms.group(1)) if ms else 1.0,   # CharacterResource default 1.0
                             "footprint": [int(fp.group(1)), int(fp.group(2))] if fp else [1, 1],
                             "human": bool(kd and kd.group(1) == "1"),
                             "placeholder_model": "PLACEHOLDER MODEL" in t})
            # the roster entry that IS this unit: file stem, then display name, then the first non-placeholder
            def rank(x):
                dn = (x["display_name"] or "").split(",")[0].strip().lower()
                return (x["stem"] != unit, dn != unit, x["placeholder_model"], x["tres"])
            refs.sort(key=rank)
            info["units"][unit] = {"glb": os.path.join(bd, f), "res_path": res_path, "root_scale": rs,
                                   "apply_root_scale": apply_rs, "roster": refs}
    return info

def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()[:12]

# ---- scene helpers -------------------------------------------------------------------------------------------------
def reset_scene():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.view_settings.view_transform = "Standard"
    try:
        sc.eevee.taa_render_samples = SAMPLES
    except AttributeError:
        pass
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    sc.render.resolution_percentage = 100
    w = bpy.data.worlds.new("lineup_world"); sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.86, 0.86, 0.87, 1); bg.inputs[1].default_value = 0.9
    for name, energy, rot in [("key", 3.2, (math.radians(50), 0, math.radians(-30))),
                              ("fill", 1.2, (math.radians(65), 0, math.radians(40)))]:
        d = bpy.data.lights.new(name, "SUN"); d.energy = energy
        o = bpy.data.objects.new(name, d); o.rotation_euler = rot; sc.collection.objects.link(o)
    return sc

def flat_mat(name, rgb, lit=False):
    """Annotation material. Unlit (emission) by default so the backdrop and ruler take no unit shadows."""
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree
    if lit:
        b = nt.nodes.get("Principled BSDF")
        b.inputs["Base Color"].default_value = (*rgb, 1); b.inputs["Roughness"].default_value = 0.9
        return m
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    e = nt.nodes.new("ShaderNodeEmission"); e.inputs["Color"].default_value = (*rgb, 1); e.inputs["Strength"].default_value = 1.0
    nt.links.new(e.outputs[0], nt.nodes["Material Output"].inputs["Surface"])
    return m

def coll(name):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name); bpy.context.scene.collection.children.link(c)
    return c

def mesh_world_bbox(objs):
    lo = np.array([1e18] * 3); hi = -lo
    for o in objs:
        me = o.data
        n = len(me.vertices)
        if n == 0:
            continue
        co = np.empty(n * 3, dtype=np.float64); me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        M = np.array(o.matrix_world)
        w = co @ M[:3, :3].T + M[:3, 3]
        lo = np.minimum(lo, w.min(0)); hi = np.maximum(hi, w.max(0))
    return lo, hi

def eval_bbox(mesh_objs):
    dg = bpy.context.evaluated_depsgraph_get()
    lo = np.array([1e18] * 3); hi = -lo
    for o in mesh_objs:
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        n = len(me.vertices)
        if n:
            co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
            M = np.array(ev.matrix_world)
            w = co @ M[:3, :3].T + M[:3, 3]
            lo = np.minimum(lo, w.min(0)); hi = np.maximum(hi, w.max(0))
        ev.to_mesh_clear()
    return lo, hi

def assign_action(idb, act):
    ad = idb.animation_data or idb.animation_data_create()
    for t in list(ad.nla_tracks):
        ad.nla_tracks.remove(t)
    ad.action = act
    try:
        if ad.action_slot is None:
            cand = list(getattr(ad, "action_suitable_slots", [])) or list(act.slots)
            want = ("OB" if isinstance(idb, bpy.types.Object) else "KE") + idb.name
            pick = [s for s in cand if s.identifier == want] or cand
            if pick:
                ad.action_slot = pick[0]
    except AttributeError:
        pass

# ---- one unit: import, measure rest + displayed pose, bake static, delete the rig ----------------------------------
def load_unit(unit, path):
    before_o, before_a = set(bpy.data.objects), set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before_o]
    acts = [a for a in bpy.data.actions if a not in before_a]
    arms = [o for o in new if o.type == "ARMATURE"]
    shapes = {pb.custom_shape for ar in arms for pb in ar.pose.bones if pb.custom_shape}   # importer's bone-display icospheres
    meshes = [o for o in new if o.type == "MESH" and o not in shapes]
    sc = bpy.context.scene
    clips = sorted(a.name for a in acts)
    idle = None
    for a in sorted(acts, key=lambda a: (re.sub(r"\.\d+$", "", a.name).lower() != "idle", a.name)):
        if "idle" in a.name.lower():
            idle = a; break
    # rest / bind pose (what the delivery's root_scale was fitted to)
    for ar in arms:
        ar.data.pose_position = "REST"
    sc.frame_set(0)
    rlo, rhi = eval_bbox(meshes)
    for ar in arms:
        ar.data.pose_position = "POSE"
    pose = "rest"
    for o in new:
        for idb in [o] + ([o.data.shape_keys] if o.type == "MESH" and o.data.shape_keys else []):
            if idb.animation_data:
                idb.animation_data.action = None
                for t in list(idb.animation_data.nla_tracks):
                    idb.animation_data.nla_tracks.remove(t)
    if idle is not None:
        for ar in arms:
            assign_action(ar, idle)
        for o in meshes:
            sk = o.data.shape_keys
            if sk and any(s.target_id_type == "KEY" for s in getattr(idle, "slots", [])):
                assign_action(sk, idle)
        f0 = int(round(idle.frame_range[0]))
        sc.frame_set(f0)
        pose = "%s:%d" % (idle.name, f0)
    else:
        for ar in arms:   # bind pose: clear any pose transforms
            for pb in ar.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
        sc.frame_set(0)
    dg = bpy.context.evaluated_depsgraph_get()
    uc = coll("U_" + unit)
    statics = []
    for i, o in enumerate(meshes):
        if o.hide_render:
            continue
        ev = o.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        me.name = "%s__static_%02d" % (unit, i)
        so = bpy.data.objects.new(me.name, me)
        so.matrix_world = ev.matrix_world.copy()
        uc.objects.link(so)
        statics.append(so)
    # bake the world transform into vertices so the unit root can own the transform
    for so in statics:
        so.data.transform(so.matrix_world); so.matrix_world = Matrix.Identity(4)
    dlo, dhi = mesh_world_bbox(statics)
    for o in new:
        d = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if d is not None and d.users == 0:
            if isinstance(d, bpy.types.Mesh): bpy.data.meshes.remove(d)
            elif isinstance(d, bpy.types.Armature): bpy.data.armatures.remove(d)
    for a in acts:
        bpy.data.actions.remove(a)
    for c in list(bpy.data.collections):   # importer collections left empty
        if c.name.startswith("U_") or c.objects or c.children:
            continue
        bpy.data.collections.remove(c)
    root = bpy.data.objects.new("ROOT_" + unit, None); uc.objects.link(root)
    for so in statics:
        so.parent = root
    for m in {s for so in statics for s in so.data.materials if s}:   # sizing render: glow off (uniform glTF emission)
        if m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED" and "Emission Strength" in n.inputs:
                    n.inputs["Emission Strength"].default_value = 0.0
                if n.type == "EMISSION":
                    n.inputs["Strength"].default_value = 0.0
    tris = sum(sum(len(p.vertices) - 2 for p in so.data.polygons) for so in statics)
    return {"root": root, "statics": statics, "rest_lo": rlo.tolist(), "rest_hi": rhi.tolist(),
            "disp_lo": dlo.tolist(), "disp_hi": dhi.tolist(), "pose": pose, "clips": clips, "tris": tris}

# ---- layout + annotation ---------------------------------------------------------------------------------------------
def clear_annot():
    c = bpy.data.collections.get("ANNOT")
    if c:
        for o in list(c.objects):
            d = o.data; bpy.data.objects.remove(o, do_unlink=True)
            if d is not None and d.users == 0:
                (bpy.data.curves if isinstance(d, bpy.types.Curve) else bpy.data.meshes).remove(d)
    return coll("ANNOT")

def box(c, name, lo, hi, mat):
    me = bpy.data.meshes.new(name)
    x0, y0, z0 = lo; x1, y1, z1 = hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me.from_pydata(v, [], f); me.materials.append(mat)
    o = bpy.data.objects.new(name, me); c.objects.link(o); return o

def text(c, body, loc, size, mat, align="CENTER", yaw=0.0):
    cu = bpy.data.curves.new("t", "FONT"); cu.body = body; cu.size = size
    cu.align_x = align; cu.align_y = "TOP"; cu.materials.append(mat)
    o = bpy.data.objects.new("txt_" + body[:12], cu)
    o.location = loc; o.rotation_euler = (math.radians(90), 0, yaw); c.objects.link(o); return o

def layout(units, order, heights_key, scale_key, yaw_deg, mats, label_fmt):
    """Place each unit; returns the annotated extents. units[u] carries disp_lo/hi and the scale for this render."""
    tallest = max(units[u][heights_key] for u in order)
    gap = GAP_FRAC * tallest
    c = clear_annot()
    x = 0.0; half_depth = 0.0; placed = []
    yaw = math.radians(yaw_deg)
    for u in order:
        d = units[u]; s = d[scale_key]
        lo, hi = np.array(d["disp_lo"]), np.array(d["disp_hi"])
        ctr = (lo + hi) / 2
        # footprint of the turned unit in x (for spacing)
        ex, ey = (hi - lo)[0] * s / 2, (hi - lo)[1] * s / 2
        wx = abs(math.cos(yaw)) * ex + abs(math.sin(yaw)) * ey
        wy = abs(math.sin(yaw)) * ex + abs(math.cos(yaw)) * ey
        cx = x + wx
        M = (Matrix.Translation((cx, 0, 0)) @ Matrix.Rotation(yaw, 4, "Z") @ Matrix.Scale(s, 4)
             @ Matrix.Translation((-ctr[0], -ctr[1], -lo[2])))
        d["root"].matrix_world = M
        placed.append((u, cx, wx, d[heights_key]))
        half_depth = max(half_depth, wy)
        x = cx + wx + gap
    x_end = x - gap
    rh = math.ceil((tallest + 1e-6) / UNIT_LEN) * UNIT_LEN + TICK    # ruler top: the next whole unit above the tallest + a tick
    front_y = -half_depth - 0.15
    back_y = half_depth + 0.4
    # labels under the floor: name + height, sized to the slot (own width + gap)
    lab_size = 0.2 * max(1.0, tallest / 4.0)
    for i, (u, cx, wx, h) in enumerate(placed):
        name = units[u].get("label") or u
        avail = 2 * wx + gap * 0.9
        # the height label splits at " (" onto its own lines ("6 ft 2 in" / "(game 5 ft 11 in)"): ft/in labels are long,
        # and one line ran into its neighbours (target render, 2026-10-09); the slot fits the LONGEST line
        lines = label_fmt(u, h).replace(" (", "\n(").split("\n")
        longest = max([len(name)] + [len(s_) for s_ in lines])
        sz = max(0.08, min(lab_size, avail / (0.62 * max(longest, 6))))
        text(c, name, (cx, front_y, -0.12), sz, mats["ink"])
        for li, s_ in enumerate(lines):
            text(c, s_, (cx, front_y, -0.12 - 1.25 * sz * (li + 1)), sz * 0.95, mats["ink2"])
    # rulers at both ends, standing in the row's plane (y = 0) so the perspective view reads them true
    rx = [-gap * 0.55, x_end + gap * 0.55]
    tick_w = max(0.06, 0.03 * tallest)
    for j, r in enumerate(rx):
        box(c, "ruler_bar", (r - tick_w * 0.15, -0.02, 0), (r + tick_w * 0.15, 0.02, rh), mats["ink"])
        n = int(math.floor(rh / TICK + 1e-6))
        for k in range(n + 1):
            z = k * TICK
            major = k % TICKS_PER_UNIT == 0
            half = major or (LABEL_UNITS != "ft" and (2 * k) % TICKS_PER_UNIT == 0)
            ln = tick_w * (1.6 if major else 1.0 if half else 0.6)
            sgn = -1 if j == 0 else 1     # ticks point outward
            xa, xb = (r, r + sgn * ln) if sgn > 0 else (r + sgn * ln, r)
            box(c, "tick", (xa, -0.02, z - 0.008 * max(1, tallest / 2)), (xb, 0.02, z + 0.008 * max(1, tallest / 2)), mats["ink"])
            if half and k > 0:
                lbl = ("%d %s" % (k // TICKS_PER_UNIT, LABEL_UNITS)) if major else ("%.1f" % z)
                tsz = tick_w * (1.5 if major else 1.15)
                text(c, lbl, (r + sgn * (ln + tick_w * 0.4), 0.0, z + tsz * 0.4), tsz, mats["ink"],
                     align="LEFT" if sgn > 0 else "RIGHT")
    # floor line, ground grid (1 m), backdrop height lines (0.25 m faint, 1 m strong)
    x0, x1 = rx[0] - 2.2 * tick_w - 0.6, rx[1] + 2.2 * tick_w + 0.6
    box(c, "floor_line", (x0, front_y - 0.03, -0.03), (x1, back_y, 0.0), mats["floor"])
    gl = 0.012 * max(1, tallest / 2)
    for gx in range(int(math.floor(x0)), int(math.ceil(x1)) + 1):
        box(c, "grid_x", (gx - gl, front_y - 0.03, 0.0), (gx + gl, back_y, 0.002), mats["grid"])
    gy = math.floor(front_y)
    while gy <= back_y:
        box(c, "grid_y", (x0, gy - gl, 0.0), (x1, gy + gl, 0.002), mats["grid"]); gy += 1.0
    box(c, "backdrop", (x0, back_y, 0.0), (x1, back_y + 0.05, rh + 0.6), mats["back"])
    k = 1
    while k * TICK <= rh + 1e-6:
        z = k * TICK; major = k % TICKS_PER_UNIT == 0
        box(c, "hline", (x0, back_y - 0.01, z - gl * (1.0 if major else 0.6)), (x1, back_y, z + gl * (1.0 if major else 0.6)),
            mats["grid"] if major else mats["grid_faint"])
        k += 1
    label_bottom = -0.12 - 3.9 * lab_size     # name + up to two height lines
    return {"x0": x0, "x1": x1, "z0": label_bottom - 0.15, "z1": rh + 0.35, "front_y": front_y, "back_y": back_y,
            "tallest": tallest, "gap": gap, "ruler_top": rh}

def render_ortho(ext, path):
    sc = bpy.context.scene
    W = ext["x1"] - ext["x0"]; H = ext["z1"] - ext["z0"]
    ppm = min(PX_PER_M, MAX_RES_X / W)
    sc.render.resolution_x = int(round(W * ppm)); sc.render.resolution_y = int(round(H * ppm))
    cd = bpy.data.cameras.new("ortho"); cd.type = "ORTHO"; cd.ortho_scale = max(W, H); cd.clip_end = 1000
    cam = bpy.data.objects.new("cam_ortho", cd); sc.collection.objects.link(cam)
    cam.location = ((ext["x0"] + ext["x1"]) / 2, -100, (ext["z0"] + ext["z1"]) / 2)
    cam.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = cam
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    log("wrote", path, sc.render.resolution_x, "x", sc.render.resolution_y)

def render_tq(ext, path):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("tq"); cd.lens = 50; cd.sensor_width = 36; cd.sensor_fit = "HORIZONTAL"; cd.clip_end = 5000
    cam = bpy.data.objects.new("cam_tq", cd); sc.collection.objects.link(cam)
    el = math.radians(TQ_ELEV_DEG)
    cxm = (ext["x0"] + ext["x1"]) / 2; czm = (ext["z0"] + ext["z1"]) / 2
    rot = Matrix.Rotation(math.radians(90) - el, 3, "X")
    fwd = rot @ Vector((0, 0, -1))
    pts = [Vector((x, y, z)) for x in (ext["x0"], ext["x1"]) for y in (ext["front_y"], ext["back_y"])
           for z in (ext["z0"], ext["z1"])]
    tanh = 18.0 / 50.0
    def proj(D):
        pos = Vector((cxm, 0, czm)) - fwd * D
        R = rot.transposed()
        uv = []
        for q in pts:
            c = R @ (q - pos); uv.append((c.x / -c.z, c.y / -c.z))
        return pos, uv
    lo_d, hi_d = 1.0, 2000.0
    for _ in range(60):
        D = (lo_d + hi_d) / 2
        _, uv = proj(D)
        span = max(u for u, _ in uv) - min(u for u, _ in uv)
        if span > 2 * tanh * 0.97: lo_d = D
        else: hi_d = D
    pos, uv = proj(hi_d)
    us = [u for u, _ in uv]; vs = [v for _, v in uv]
    uspan, vspan = max(us) - min(us), max(vs) - min(vs)
    sc.render.resolution_x = TQ_RES_X
    sc.render.resolution_y = max(400, int(round(TQ_RES_X * (vspan / (2 * tanh)) * 1.03)))
    cd.shift_x = ((max(us) + min(us)) / 2) / (2 * tanh)
    cd.shift_y = ((max(vs) + min(vs)) / 2) / (2 * tanh)
    cam.location = pos; cam.rotation_euler = rot.to_euler()
    sc.camera = cam; sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    log("wrote", path, sc.render.resolution_x, "x", sc.render.resolution_y)

# ---- size table merge ------------------------------------------------------------------------------------------------
AUTO_NOTE = re.compile(r"^(in game|not in game)\b")
def merge_table(rows_new):
    old = {"rows": []}
    if os.path.exists(TABLE):
        try:
            old = json.load(open(TABLE, encoding="utf8"))
        except Exception as e:
            raise SystemExit("size-table.json is not valid JSON (fix it by hand; nothing was overwritten): %s" % e)
    rows = old.get("rows", old if isinstance(old, list) else [])
    by = {r["unit"]: r for r in rows}
    out, edited = [], []
    for r in rows:   # existing rows first, in the artist's order
        u = r["unit"]
        if u in rows_new:
            n = rows_new[u]
            prev_ing, prev_tgt = r.get("ingame_height_m"), r.get("target_height_m")
            untouched = (prev_tgt is None or prev_ing is None or abs(float(prev_tgt) - float(prev_ing)) < 5e-4)
            r["ingame_height_m"] = n["ingame_height_m"]
            if untouched:
                r["target_height_m"] = n["ingame_height_m"]
            else:
                edited.append(u)
            r.setdefault("kind", n["kind"])
            if not r.get("note") or AUTO_NOTE.match(r["note"]):
                r["note"] = n["note"]
        out.append(r)
    for u, n in rows_new.items():   # new units appended
        if u not in by:
            out.append(dict(n))
    doc = {"_readme": ["One row per Conquest unit. EDIT target_height_m (metres, standing height as the game shows it);",
                       "rerun improve/lineup_run.ps1 and renders/lineup/lineup_target.png shows the row at your sizes.",
                       "ingame_height_m is MEASURED every run (delivered glb x .import root_scale x roster model_scale);",
                       "a target equal to ingame_height_m follows the measurement, any other value is kept as yours.",
                       "Row order = the roster-order render. kind and note are yours to edit too (notes starting",
                       "'in game' / 'not in game' are auto-refreshed). The renderer never deletes a row."],
           "rows": out}
    os.makedirs(os.path.dirname(TABLE), exist_ok=True)
    with open(TABLE, "w", encoding="utf8") as fh:
        fh.write("{\n  \"_readme\": [\n")
        fh.write(",\n".join("    " + json.dumps(s) for s in doc["_readme"]))
        fh.write("\n  ],\n  \"rows\": [\n")
        keys = ["unit", "kind", "ingame_height_m", "target_height_m", "note"]
        fh.write(",\n".join("    " + json.dumps({k: r.get(k) for k in keys} | {k: v for k, v in r.items() if k not in keys})
                            for r in out))
        fh.write("\n  ]\n}\n")
    return out, edited

# ---- main ----------------------------------------------------------------------------------------------------------
def main():
    os.makedirs(OUT, exist_ok=True)
    all_glbs = sorted(f for f in os.listdir(RIGGED) if f.lower().endswith(".glb"))
    units_found = [f[:-4] for f in all_glbs if "__" not in f and not f[:-4].endswith("_probe")
                   and f[:-4] not in SKIP_UNITS]
    if _arg("--units"):   # dev: a subset
        units_found = [u for u in units_found if u in _arg("--units").split(",")]
    log("rigged/*.glb = %d, units (skins/probes/skips excluded) = %d" % (len(all_glbs), len(units_found)))
    tree = find_conquest()
    cq = conquest_info(tree)
    log("conquest tree:", tree, "branch:", cq["branch"], "forge glbs in game:", len(cq["units"]))
    reset_scene()
    units, failed = {}, {}
    for u in units_found:
        path = os.path.join(RIGGED, u + ".glb")
        for attempt in range(2):
            try:
                t = time.time()
                d = load_unit(u, path)
                d["sha1"] = sha1(path)
                d["import_s"] = round(time.time() - t, 1)
                units[u] = d
                break
            except Exception as e:
                log("import failed", u, attempt, repr(e))
                if attempt == 0:
                    time.sleep(5)
                else:
                    failed[u] = repr(e)
        if u in units:
            d = units[u]
            log("%-16s pose=%-10s rest_h=%.4f disp_h=%.4f tris=%d (%.1fs)" % (
                u, d["pose"], d["rest_hi"][2] - d["rest_lo"][2], d["disp_hi"][2] - d["disp_lo"][2], d["tris"], d["import_s"]))
    # scales
    rows_new = {}
    for u, d in units.items():
        rlo, rhi = np.array(d["rest_lo"]), np.array(d["rest_hi"])
        dlo, dhi = np.array(d["disp_lo"]), np.array(d["disp_hi"])
        rh, rfp = float(rhi[2] - rlo[2]), float(max(rhi[0] - rlo[0], rhi[1] - rlo[1]))
        g = cq["units"].get(u)
        ref = g["roster"][0] if g and g["roster"] else None
        boss = bool(ref and max(ref["footprint"]) >= 2)
        fh, ff = (BOSS_H, BOSS_FP) if boss else (CELL_H, CELL_FP)
        formula = min(fh / rh, ff / rfp)
        limit = "height" if fh / rh <= ff / rfp else "footprint"
        if g:
            root_s = g["root_scale"] if g["apply_root_scale"] else 1.0
            mscale = ref["model_scale"] if ref else 1.0
            scale = root_s * mscale
            src = "measured"
            game_sha = sha1(g["glb"])
        else:
            root_s, mscale, scale, src, game_sha = None, None, formula, "formula", None
        kind = "boss" if boss else ("humanoid" if (ref and ref["human"]) else KIND_OVERRIDES.get(u, "creature"))
        dh = float(dhi[2] - dlo[2])
        d.update({"rest_h": rh, "rest_fp": rfp, "disp_h": dh, "formula_scale": formula, "formula_limit": limit,
                  "root_scale": root_s, "model_scale": mscale, "ingame_scale": scale, "scale_source": src,
                  "ingame_h": dh * scale, "ingame_fp": rfp * scale, "kind": kind, "game": g, "roster_ref": ref,
                  "game_glb_identical": (game_sha == d["sha1"]) if game_sha else None})
        if g:
            note = "in game: root_scale %g x model_scale %g (%s); formula %.5f (%s-limited)" % (
                root_s, mscale, ref["tres"] if ref else "no roster .tres", formula, limit)
            if game_sha != d["sha1"]:
                note += "; WARNING delivered glb differs from rigged glb"
        else:
            note = "not in game: formula min(%.1f/h, %.1f/fp) = %.5f (%s-limited)" % (fh, ff, formula, limit)
        rows_new[u] = {"unit": u, "kind": kind, "ingame_height_m": round(d["ingame_h"], 3),
                       "target_height_m": round(d["ingame_h"], 3), "note": note}
    rows, edited = merge_table(rows_new)
    tgt = {r["unit"]: r for r in rows}
    for u, d in units.items():
        th = tgt[u].get("target_height_m")
        th = float(th) if th not in (None, "") else d["ingame_h"]
        d["target_h"] = th
        d["target_scale"] = th / d["disp_h"]
    roster_order = [r["unit"] for r in rows if r["unit"] in units]
    by_ing = sorted(units, key=lambda u: (units[u]["ingame_h"], u))
    by_tgt = sorted(units, key=lambda u: (units[u]["target_h"], u))
    mats = {"ink": flat_mat("ink", (0.05, 0.05, 0.06)), "ink2": flat_mat("ink2", (0.18, 0.2, 0.32)),
            "floor": flat_mat("floor", (0.62, 0.62, 0.63), lit=True), "grid": flat_mat("grid", (0.42, 0.43, 0.47)),
            "grid_faint": flat_mat("grid_faint", (0.66, 0.67, 0.69)), "back": flat_mat("back", (0.80, 0.80, 0.79))}
    fmt_ing = lambda u, h: hstr(h) + ("" if units[u]["scale_source"] == "measured" else " (formula)")
    fmt_tgt = lambda u, h: hstr(h) + ("" if abs(h - units[u]["ingame_h"]) < 5e-4 else " (game %s)" % hstr(units[u]["ingame_h"]))
    renders = {}
    if "ingame" in ONLY:
        for order, tag in [(by_ing, "lineup_ingame"), (roster_order, "lineup_ingame_roster")]:
            ext = layout(units, order, "ingame_h", "ingame_scale", 0.0, mats, fmt_ing)
            render_ortho(ext, os.path.join(OUT, tag + ".png")); renders[tag] = ext
    if "target" in ONLY:
        for order, tag in [(by_tgt, "lineup_target"), (roster_order, "lineup_target_roster")]:
            ext = layout(units, order, "target_h", "target_scale", 0.0, mats, fmt_tgt)
            render_ortho(ext, os.path.join(OUT, tag + ".png")); renders[tag] = ext
    if "tq" in ONLY:
        ext = layout(units, by_ing, "ingame_h", "ingame_scale", TQ_YAW_DEG, mats, fmt_ing)
        render_tq(ext, os.path.join(OUT, "lineup_ingame_tq.png")); renders["lineup_ingame_tq"] = ext
    r4 = lambda v: round(float(v), 4)
    table = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
             "conquest_tree": tree, "conquest_branch": cq["branch"],
             "rigged_glbs": len(all_glbs), "units_discovered": len(units_found), "units_rendered": len(units),
             "failed": failed, "targets_edited_by_artist": edited,
             "fit_rule": {"cell": "min(%.1f/h, %.1f/fp)" % (CELL_H, CELL_FP), "boss_2x2": "min(%.1f/h, %.1f/fp)" % (BOSS_H, BOSS_FP),
                          "h": "rest-pose bbox height", "fp": "max(x, y) rest-pose bbox extent"},
             "order_by_ingame_height": by_ing, "order_roster": roster_order,
             "renders": {k: {"tallest_m": r4(v["tallest"]), "gap_m": r4(v["gap"])} for k, v in renders.items()},
             "units": {}}
    for u in roster_order:
        d = units[u]
        table["units"][u] = {
            "kind": d["kind"], "glb_sha1": d["sha1"], "tris": d["tris"], "clips": d["clips"], "pose_shown": d["pose"],
            "natural_rest_bbox_units": {"min": [r4(v) for v in d["rest_lo"]], "max": [r4(v) for v in d["rest_hi"]],
                                        "height": r4(d["rest_h"]), "footprint": r4(d["rest_fp"])},
            "natural_shown_height_units": r4(d["disp_h"]),
            "scale_source": d["scale_source"] if d["scale_source"] == "measured" else "not in game: formula",
            "root_scale_import": d["root_scale"], "model_scale_roster": d["model_scale"],
            "roster_tres": d["roster_ref"]["tres"] if d["roster_ref"] else None,
            "roster_all": [x["tres"] for x in d["game"]["roster"]] if d["game"] else [],
            "game_glb_identical": d["game_glb_identical"],
            "formula_scale": round(d["formula_scale"], 5), "formula_limit": d["formula_limit"],
            "ingame_scale": round(d["ingame_scale"], 5),
            "ingame_height_m": r4(d["ingame_h"]), "ingame_footprint_m": r4(d["ingame_fp"]),
            "target_height_m": r4(d["target_h"]), "target_scale": round(d["target_scale"], 5)}
    with open(os.path.join(OUT, "lineup_table.json"), "w", encoding="utf8") as fh:
        json.dump(table, fh, indent=1)
    log("TABLE")
    for u in by_ing:
        t = table["units"][u]
        log("%-16s %-9s nat_h=%9.4f shown_h=%9.4f scale=%.5f (%s) formula=%.5f ingame_h=%.3f fp=%.3f target=%.3f pose=%s" % (
            u, t["kind"], t["natural_rest_bbox_units"]["height"], t["natural_shown_height_units"], t["ingame_scale"],
            "measured" if t["scale_source"] == "measured" else "formula", t["formula_scale"], t["ingame_height_m"],
            t["ingame_footprint_m"], t["target_height_m"], t["pose_shown"]))
    log("DONE units=%d failed=%d" % (len(units), len(failed)))

main()
