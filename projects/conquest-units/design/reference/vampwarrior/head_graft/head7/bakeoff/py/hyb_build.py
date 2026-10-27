"""hyb_build.py -- <bakeoff dir> <fit.npz> <out_dir> [res] : assemble the hybrid head parts + bake the source look.
Parts (f-frame, head glb units): head (template quads, MPFB UVs packed; skin + mouth-interior materials), eye.L / eye.R
(UV spheres, procedural sclera / iris / pupil / highlight with the edit's paint values, iris on the edit's iris axes),
teeth_upper / teeth_lower / tongue (hm08 helpers carried by the same warp + the local fit displacement), fang.L / fang.R
(tapered, slightly curved cones at the source fangs, rooted inside the upper lip).
Bake: head NORMAL (tangent, smooth reference) + DIFFUSE colour from the source (eyeball shells, fang texels and the eye
cover inside the liner dropped from the bake source), cage `HEAD_CAGE`. Writes <out>/hyb.blend + textures + parts json."""
import sys, os, json, math, numpy as np, bpy, bmesh
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bl_util as U
from mathutils import Vector, Matrix

a = sys.argv[sys.argv.index("--") + 1:]
BD, FIT, OUT = a[0], a[1], a[2]
RES = int(a[3]) if len(a) > 3 else 2048
HEAD_CAGE = float(a[4]) if len(a) > 4 else 0.006
os.makedirs(OUT, exist_ok=True)
J_ = lambda *p: os.path.join(BD, *p)
Z = np.load(FIT)
Zs = np.load(J_("s2", "edit.npz"))
Zm = np.load(J_("s2", "mpfb_base.npz"))
FANG = json.load(open(J_("s3", "src_fangs.json")))
U.clear()
sc = bpy.context.scene
rep = {}


def mesh_obj(name, V, faces, uv=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], [list(map(int, f)) for f in faces])
    me.update()
    ob = bpy.data.objects.new(name, me); sc.collection.objects.link(ob)
    if uv is not None:
        ul = me.uv_layers.new(name="UVMap")
        ul.data.foreach_set("uv", np.asarray(uv, np.float32).ravel())
    return ob


def mat(name, rgb, rough=0.6, img=None, nrm=None):
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; pb = nt.nodes["Principled BSDF"]
    pb.inputs["Base Color"].default_value = (*rgb, 1.0); pb.inputs["Roughness"].default_value = rough
    if img is not None:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = img; nt.links.new(t.outputs[0], pb.inputs["Base Color"])
    if nrm is not None:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = nrm; nrm.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap"); nt.links.new(t.outputs[0], nm.inputs["Color"]); nt.links.new(nm.outputs[0], pb.inputs["Normal"])
    return m


srgb = lambda c: tuple(((np.asarray(c) / 255.0 + 0.055) / 1.055) ** 2.4)

# ---------------------------------------------------------------- head
Q, Fq, UVq = Z["V"].copy(), Z["Fq"], Z["UVq"]
bag = set(Z["bag"].tolist())
pockets = []
for k_, nm_ in enumerate(("l", "r")):
    pk = Z["pocket_" + nm_]; c_ = Z["ball_c"][k_]; R_ = float(Z["ball_r"][k_])
    d_ = Q[pk] - c_; Q[pk] = c_ + 0.9 * R_ * d_ / np.linalg.norm(d_, axis=1)[:, None]
    pockets += pk.tolist()
pockets = set(pockets)
# lid margins onto the ball surface (no visible gap / waterline band)
for k_, nm_ in enumerate(("l", "r")):
    c_ = Z["ball_c"][k_]; R_ = float(Z["ball_r"][k_])
    mg = np.concatenate([Z["eye_up_" + nm_], Z["eye_lo_" + nm_], Z["eye_corners"][k_]])
    d_ = Q[mg] - c_; dn_ = np.linalg.norm(d_, axis=1)
    Q[mg] = c_ + d_ / dn_[:, None] * np.minimum(dn_, R_ + 0.0006)[:, None]
rep["pocket_moved_behind_ball"] = len(pockets)
head = mesh_obj("head", Q, Fq)
ul = head.data.uv_layers.new(name="UVMap")
uv = UVq.reshape(-1, 2).astype(np.float32)        # obj convention (v up) = Blender
ul.data.foreach_set("uv", uv.ravel())
bag_face = np.array([sum(int(v) in bag for v in f) >= 1 for f in Fq])
pocket_face = np.array([sum(int(v) in pockets for v in f) >= 2 for f in Fq])
bag_face2 = np.array([sum(int(v) in bag for v in f) >= 2 for f in Fq])
# the mouth opening: faces between the upper lip line and the lower lip line (inner lip rolls) are mouth interior
_lu = Q[Z["lip_up_line"]]; _lu = _lu[np.argsort(_lu[:, 0])]; _ll = Q[Z["lip_lo_line"]]; _ll = _ll[np.argsort(_ll[:, 0])]
_C = Q[Fq].mean(1)
_zu = np.interp(_C[:, 0], _lu[:, 0], _lu[:, 2]); _zl = np.interp(_C[:, 0], _ll[:, 0], _ll[:, 2])
opening = (_C[:, 0] > _lu[0, 0] + 0.002) & (_C[:, 0] < _lu[-1, 0] - 0.002) & (_C[:, 2] < _zu - 0.0005) & (_C[:, 2] > _zl + 0.0005)           & (_C[:, 1] > _lu[:, 1].min() - 0.03) & (_C[:, 1] < -0.1)
bag_face2 |= opening
rep["mouth_opening_faces"] = int(opening.sum())
head.data.polygons.foreach_set("use_smooth", np.ones(len(Fq), bool))
# pack the head islands to fill the 0..1 square (the MPFB atlas is the whole body)
bpy.context.view_layer.objects.active = head
for o in bpy.data.objects: o.select_set(o is head)
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(margin=0.003, rotate=False); bpy.ops.object.mode_set(mode="OBJECT")
rep["head"] = {"verts": len(Q), "quads": int(len(Fq)), "bag_faces": int(bag_face.sum())}

# ---------------------------------------------------------------- eyeballs (procedural texture)
ER = Z["ball_r"]; ECn = Z["ball_c"]
axes_edit = {1: np.array([-0.047, -0.988, 0.146]), -1: np.array([-0.044, -0.992, 0.116])}   # edit report, +x / -x eye
EYEP = {"iris_deg": 26.0, "pupil_frac": 0.38, "sclera": [226, 220, 218], "iris_inner": [165, 38, 46],
        "iris_outer": [105, 22, 30], "pupil": [40, 12, 16], "hl": [245, 240, 240], "hl_frac": 0.16}
ET = 1024
# texture in the ball's LOCAL frame: the iris centre at local -y (u 0.25... computed below), equirect lat-long
uu, vv = np.meshgrid((np.arange(ET) + 0.5) / ET, (np.arange(ET // 2) + 0.5) / (ET // 2))
phi = uu * 2 * np.pi; th = vv * np.pi                   # th 0 = local +z pole
D = np.stack([np.sin(th) * np.cos(phi), np.sin(th) * np.sin(phi), np.cos(th)], -1)
iris_ax = np.array([0.0, -1.0, 0.0])
ang = np.degrees(np.arccos(np.clip(D @ iris_ax, -1, 1)))
ia = EYEP["iris_deg"]; pa = ia * EYEP["pupil_frac"]
img = np.zeros(D.shape[:2] + (3,))
img[:] = EYEP["sclera"]
t = np.clip(ang / ia, 0, 1)[..., None]
iris = (np.asarray(EYEP["iris_inner"]) * (1 - t ** 1.5) + np.asarray(EYEP["iris_outer"]) * t ** 1.5)
img = np.where((ang <= ia)[..., None], iris, img)
edge = (ang > ia - 1.2) & (ang <= ia + 0.8)
img[edge] = img[edge] * 0.55
img[ang < pa] = EYEP["pupil"]
hdir = np.array([0.32, -0.9, 0.28]); hdir /= np.linalg.norm(hdir)
img[np.degrees(np.arccos(np.clip(D @ hdir, -1, 1))) < ia * EYEP["hl_frac"]] = EYEP["hl"]
# a soft shadow band at the top (lid occlusion read)
img = img * (1 - 0.18 * np.clip((D[..., 2] - 0.15) / 0.5, 0, 1))[..., None]
eimg = bpy.data.images.new("eye_albedo", ET, ET // 2, alpha=False)
px = np.ones((ET // 2, ET, 4)); px[..., :3] = (np.clip(img, 0, 255) / 255.0) ** 2.2   # stored linear-ish -> sRGB image
eimg.colorspace_settings.name = "sRGB"
px[..., :3] = np.clip(img, 0, 255) / 255.0
eimg.pixels.foreach_set(px[::-1].ravel().astype(np.float32))   # image rows bottom-up; v = 1 - th/pi
eimg.filepath_raw = os.path.join(OUT, "hybrid_eye_albedo.png"); eimg.file_format = "PNG"; eimg.save()
eye_mat = mat("eye", (1, 1, 1), 0.25, eimg)


def rot_to(a_, b_):
    a_ = a_ / np.linalg.norm(a_); b_ = b_ / np.linalg.norm(b_)
    v = np.cross(a_, b_); c = float(a_ @ b_)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


eyes_obj = []
for k, (c, R) in enumerate(zip(ECn, ER)):
    side = 1 if c[0] > 0 else -1
    nu, nvr = 48, 24
    Vb, Fb, UVb = [], [], []
    for i in range(nvr + 1):
        for j in range(nu + 1):
            th_ = np.pi * i / nvr; ph_ = 2 * np.pi * j / nu
            Vb.append([np.sin(th_) * np.cos(ph_), np.sin(th_) * np.sin(ph_), np.cos(th_)])
    for i in range(nvr):
        for j in range(nu):
            a0 = i * (nu + 1) + j; a1 = a0 + 1; b0 = a0 + nu + 1; b1 = b0 + 1
            Fb.append([a0, b0, b1, a1])
    Vb = np.array(Vb)
    Rm = rot_to(iris_ax, axes_edit[side])
    Vw = (Vb @ Rm.T) * R + c
    ob = mesh_obj("eye.%s" % ("L" if side > 0 else "R"), Vw, Fb)
    ul_ = ob.data.uv_layers.new(name="UVMap")
    uvs = []
    for f in Fb:
        for vi in f:
            i, j = divmod(vi, nu + 1)
            uvs.append([j / nu, 1 - i / nvr])
    ul_.data.foreach_set("uv", np.array(uvs, np.float32).ravel())
    ob.data.polygons.foreach_set("use_smooth", np.ones(len(Fb), bool))
    ob.data.materials.append(eye_mat)
    ob["ball_centre"] = list(map(float, c)); ob["ball_radius"] = float(R)
    eyes_obj.append(ob)
rep["eyes"] = [{"centre": np.round(c, 5).tolist(), "R": round(float(R), 5)} for c, R in zip(ECn, ER)]

# ---------------------------------------------------------------- teeth / tongue (hm08 helpers through the same warp)
Xs, W = Z["tps_X"], Z["tps_W"]; hc, hcs, scl = Z["aff_hc"], Z["aff_hcs"], Z["aff_scl"]
n = len(Xs)
aff = lambda P: (P - hc) * scl + hcs
tps = lambda P: np.linalg.norm(P[:, None] - Xs[None], axis=-1) @ W[:n] + np.hstack([np.ones((len(P), 1)), P]) @ W[n:]
Vh, Fh = Zm["V"], Zm["F"]
P0 = Z["P0"]
Ppure = tps(aff(P0))                        # head verts through the pure warp
Dfit = Q - Ppure                            # the local fit displacement (pins + projection)
from mathutils.kdtree import KDTree
kd = KDTree(len(Ppure))
for i, p in enumerate(Ppure):
    kd.insert(p.tolist(), i)
kd.balance()
mouth_mat = mat("mouth_interior", srgb([78, 26, 32]), 0.7)
teeth_mat = mat("teeth", srgb([232, 226, 214]), 0.35)
tongue_mat = mat("tongue", srgb([150, 62, 70]), 0.55)
parts = {}
for nm, key, mt in (("teeth_upper", "helper-upper-teeth", teeth_mat), ("teeth_lower", "helper-lower-teeth", teeth_mat),
                    ("tongue", "helper-tongue", tongue_mat)):
    fs = np.load(J_("s3", "hm_%s_faces.npy" % key))
    Fk = Fh[fs]; uk = np.unique(Fk); rmk = -np.ones(len(Vh), np.int64); rmk[uk] = np.arange(len(uk))
    Pk = np.stack([Vh[uk, 0], -Vh[uk, 2], Vh[uk, 1]], 1)
    Pw = tps(aff(Pk))
    for i in range(len(Pw)):
        nb = kd.find_n(Pw[i].tolist(), 8)
        ws = np.array([1.0 / (d_ + 1e-4) for _, _, d_ in nb]); ids = [j for _, j, _ in nb]
        Pw[i] = Pw[i] + (Dfit[ids] * ws[:, None]).sum(0) / ws.sum()
    LLz = float(Q[Z["lip_up_line"], 2].mean()); LLy = float(Q[Z["lip_up_line"], 1].mean())
    if nm.startswith("teeth"):
        Pw[:, 2] = LLz + 0.45 * (Pw[:, 2] - LLz) + (0.0015 if nm == "teeth_upper" else -0.0015)
        Pw[:, 0] *= 0.85
        Pw[:, 2] = LLz + 0.6 * (Pw[:, 2] - LLz) + (0.004 if nm == "teeth_upper" else -0.012)
        Pw[:, 1] += max(0.0, (LLy + 0.022) - Pw[:, 1].min())
    else:
        Pw[:, 1] += max(0.0, (LLy + 0.040) - Pw[:, 1].min())
        Pw[:, 2] -= 0.012
    ob = mesh_obj(nm, Pw, rmk[Fk])
    ob.data.polygons.foreach_set("use_smooth", np.ones(len(Fk), bool)); ob.data.materials.append(mt)
    parts[nm] = ob
    rep[nm] = {"verts": int(len(Pw)), "quads": int(len(Fk)), "bbox": [Pw.min(0).round(4).tolist(), Pw.max(0).round(4).tolist()]}

# ---------------------------------------------------------------- fangs: small tapered curved cones at the source fangs
lip_up = Z["lip_up_line"]
fang_mat = mat("fang", srgb([240, 236, 226]), 0.3)
for s_, nm in (("l", "fang.L"), ("r", "fang.R")):
    fg = FANG[s_]
    xc = 0.5 * (fg["x"][0] + fg["x"][1]); width = min(0.011, 0.75 * (fg["x"][1] - fg["x"][0]))
    ztip = fg["z"][0]
    # the lip line z / y at this x
    LL = Q[lip_up]; o = np.argsort(LL[:, 0]); zl = float(np.interp(xc, LL[o, 0], LL[o, 2])); yl = float(np.interp(xc, LL[o, 0], LL[o, 1]))
    yfront = fg["y_front"]
    root_z = zl + 0.010; nseg, nrad = 8, 10
    Vf, Ff = [], []
    for i in range(nseg + 1):
        t = i / nseg                                  # 0 root -> 1 tip
        z = root_z + (ztip - root_z) * t
        r = 0.5 * width * (1 - t ** 1.6) + 0.0004
        yc = yfront + 0.5 * width + 0.0025 * (1 - t) - 0.002 * t * t      # slight forward curve at the tip
        if t < 0.35:
            yc += 0.008 * (0.35 - t) / 0.35            # the root tucks back inside the lip
        for j in range(nrad):
            p = 2 * np.pi * j / nrad
            Vf.append([xc + r * np.cos(p), yc + r * 0.8 * np.sin(p), z])
    Vf.append([xc, yfront + 0.5 * width - 0.002, ztip - 0.0015])          # the point
    tip = len(Vf) - 1
    for i in range(nseg):
        for j in range(nrad):
            a0 = i * nrad + j; a1 = i * nrad + (j + 1) % nrad
            Ff.append([a0, a1, a1 + nrad, a0 + nrad])
    for j in range(nrad):
        Ff.append([nseg * nrad + j, nseg * nrad + (j + 1) % nrad, tip])
    Ff.append(list(range(nrad))[::-1])
    ob = mesh_obj(nm, Vf, Ff)
    bm = bmesh.new(); bm.from_mesh(ob.data); bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:]); bm.to_mesh(ob.data); bm.free()
    ob.data.polygons.foreach_set("use_smooth", np.ones(len(ob.data.polygons), bool)); ob.data.materials.append(fang_mat)
    parts[nm] = ob
    rep[nm] = {"x": round(xc, 4), "width": round(width, 4), "root_z": round(root_z, 4), "tip_z": round(ztip, 4)}

from mathutils.bvhtree import BVHTree as _B
skin_faces = [list(map(int, f)) for f, b_, p_ in zip(Fq, bag_face, pocket_face) if not b_ and not p_]
tskin = _B.FromPolygons(Q.tolist(), skin_faces)
def push_inside(P, margin=0.003):
    n_out = 0
    for i in range(len(P)):
        h = tskin.find_nearest(Vector(P[i]))
        if h[0] is None:
            continue
        d_ = (Vector(P[i]) - h[0]).dot(h[1])
        if d_ > -margin and (Vector(P[i]) - h[0]).length < 0.03:
            P[i] = np.array(h[0] - h[1] * margin); n_out += 1
    return n_out
outs = {}
for nm in ("teeth_upper", "teeth_lower", "tongue"):
    ob = parts[nm]; P = np.array([v.co for v in ob.data.vertices])
    outs[nm] = push_inside(P)
    ob.data.vertices.foreach_set("co", P.astype(np.float32).ravel()); ob.data.update()
bagi = np.array(sorted(bag)); Pb = Q[bagi].copy(); outs["bag"] = push_inside(Pb, 0.002); Q[bagi] = Pb
head.data.vertices.foreach_set("co", Q.astype(np.float32).ravel()); head.data.update()
rep["pushed_inside"] = outs
# ---------------------------------------------------------------- bake the source look onto the head
sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 1
ecov = np.load(J_("s2", "edit_eyecover.npy"))
FS = Zs["F"]; flab = Zs["flab"]
Cs = Zs["V"][FS].mean(1)
uvs_ = Zs["FUV"].mean(1); tex = Zs["tex"]; th_, tw_ = tex.shape[:2]
col = tex[np.clip((uvs_[:, 1] * th_).astype(int), 0, th_ - 1), np.clip((uvs_[:, 0] * tw_).astype(int), 0, tw_ - 1)].astype(float)
fang_src = (np.abs(Cs[:, 0]) < 0.07) & (Cs[:, 2] > -0.14) & (Cs[:, 2] < -0.105) & (Cs[:, 1] < -0.24) & \
           (col.mean(1) > 185) & (col.max(1) - col.min(1) < 40) & (np.abs(np.abs(Cs[:, 0]) - 0.0475) < 0.012)
SMo = np.load(J_("s3", "src_mouth.npz"))
sl_ = SMo["slit"][np.argsort(SMo["slit"][:, 0])]; ll_ = SMo["lolip"][np.argsort(SMo["lolip"][:, 0])]
zt = np.interp(Cs[:, 0], sl_[:, 0], sl_[:, 2]); zb = np.interp(Cs[:, 0], ll_[:, 0], ll_[:, 2])
plate = (Cs[:, 0] > sl_[0, 0] + 0.004) & (Cs[:, 0] < sl_[-1, 0] - 0.004) & (Cs[:, 2] < zt - 0.002) & (Cs[:, 2] > zb + 0.002) & (Cs[:, 1] < -0.2)
def _lab(rgb):
    c_ = rgb / 255.0; c_ = np.where(c_ <= 0.04045, c_ / 12.92, ((c_ + 0.055) / 1.055) ** 2.4)
    Mx = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c_ @ Mx.T / np.array([0.95047, 1.0, 1.08883]); f_ = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f_[:, 1] - 16, 500 * (f_[:, 0] - f_[:, 1]), 200 * (f_[:, 1] - f_[:, 2])], 1)
labc = _lab(col)
eyebox = np.zeros(len(Cs), bool)
for c_ in Z["ball_c"]:
    eyebox |= (np.abs(Cs[:, 0] - c_[0]) < 0.085) & (np.abs(Cs[:, 2] - c_[2]) < 0.05) & (Cs[:, 1] < -0.12)
irisbits = eyebox & ~ecov & (labc[:, 1] > 18)
keep = (flab == 0) & ~fang_src & ~ecov & ~plate & ~irisbits
rep["dropped_iris_bits_on_lids"] = int(irisbits.sum())
rep["dropped_mouth_plate"] = int(plate.sum())
src = mesh_obj("src", Zs["V"], FS[keep])
su = src.data.uv_layers.new(name="UV"); uvk = Zs["FUV"][keep].reshape(-1, 2).copy(); uvk[:, 1] = 1 - uvk[:, 1]
su.data.foreach_set("uv", uvk.astype(np.float32).ravel())
# orient every source face like the hybrid head's surface next to it (the generated mesh has mixed winding: a back-face
# hit bakes an inverted normal)
from mathutils.bvhtree import BVHTree as _BVH
dg = bpy.context.evaluated_depsgraph_get()
th_ = _BVH.FromObject(head, dg)
hn = None
flip = []
for p_ in src.data.polygons:
    h = th_.find_nearest(p_.center)
    if h[0] is not None and p_.normal.dot(h[1]) < 0:
        flip.append(p_.index)
bm = bmesh.new(); bm.from_mesh(src.data); bm.faces.ensure_lookup_table()
bmesh.ops.reverse_faces(bm, faces=[bm.faces[i_] for i_ in flip]); bm.to_mesh(src.data); bm.free()
rep["src_faces_flipped_to_match_head"] = len(flip)
src.data.polygons.foreach_set("use_smooth", np.ones(int(keep.sum()), bool))
texp = J_("s2", "edit_tex.png")
src.data.materials.append(mat("src_m", (1, 1, 1), 0.6, bpy.data.images.load(texp)))
rep["bake_source"] = {"faces": int(keep.sum()), "dropped_fang": int(fang_src.sum()), "dropped_eyecover": int(ecov.sum()),
                      "dropped_eyeball_shells": int((flab > 0).sum())}
skin_img = bpy.data.images.new("hybrid_albedo", RES, RES, alpha=False)
nrm_img = bpy.data.images.new("hybrid_normal", RES, RES, alpha=False); nrm_img.colorspace_settings.name = "Non-Color"
skin_mat = mat("skin", (1, 1, 1), 0.55, skin_img, nrm_img)
head.data.materials.append(skin_mat); head.data.materials.append(mouth_mat)
conj_mat = mat("eye_pocket", srgb([150, 92, 96]), 0.5)
head.data.materials.append(conj_mat)
head.data.polygons.foreach_set("material_index", np.where(pocket_face, 2, bag_face2.astype(int)).astype(np.int32))
nt = skin_mat.node_tree
nodes = {n_.image.name: n_ for n_ in nt.nodes if n_.type == "TEX_IMAGE"}
for typ, img_, nm in (("NORMAL", nrm_img, "normal"), ("DIFFUSE", skin_img, "albedo")):
    for n_ in nt.nodes: n_.select = False
    nodes[img_.name].select = True; nt.nodes.active = nodes[img_.name]
    for o in bpy.data.objects: o.select_set(o in (head, src))
    bpy.context.view_layer.objects.active = head
    if typ == "DIFFUSE":
        sc.render.bake.use_pass_direct = False; sc.render.bake.use_pass_indirect = False; sc.render.bake.use_pass_color = True
    r = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=HEAD_CAGE, max_ray_distance=2.5 * HEAD_CAGE,
                            margin=12, use_clear=True)
    if typ == "NORMAL":                       # cap the tangent tilt (~22 deg): wrong-hit blobs near lids / lips calm down
        px_n = np.array(img_.pixels[:]).reshape(RES, RES, 4)
        nn = px_n[..., :3] * 2 - 1
        xy = nn[..., :2]; L_ = np.linalg.norm(xy, axis=-1); cap = 0.38
        f_ = np.where(L_ > cap, cap / np.maximum(L_, 1e-6), 1.0)
        xy = xy * f_[..., None]; z_ = np.sqrt(np.clip(1 - (xy ** 2).sum(-1), 0, 1))
        valid = px_n[..., :3].sum(-1) > 0.05
        nn = np.where(valid[..., None], np.concatenate([xy, z_[..., None]], -1), np.array([0.0, 0.0, 1.0]))
        px_n[..., :3] = nn * 0.5 + 0.5
        rep["normal_texels_capped"] = int(((L_ > cap) & valid).sum())
        img_.pixels.foreach_set(px_n.astype(np.float32).ravel())
    img_.filepath_raw = os.path.join(OUT, "hybrid_%s.png" % nm); img_.file_format = "PNG"; img_.save()
    rep["bake_" + nm] = list(r)
bpy.data.objects.remove(src, do_unlink=True)
rep["head_cage_u"] = HEAD_CAGE
# coverage: albedo texels left at the clear colour inside the head's UV islands
px_ = np.array(skin_img.pixels[:]).reshape(RES, RES, 4)[..., :3]
rep["albedo_black_frac"] = round(float((px_.sum(-1) < 0.02).mean()), 4)
for ob in list(sc.collection.objects):
    pass
me_ = head.data
uvh = np.empty(len(me_.loops) * 2); me_.uv_layers[0].data.foreach_get("uv", uvh)
lt = np.empty(len(me_.polygons), np.int64); me_.polygons.foreach_get("loop_total", lt)
mi_ = np.empty(len(me_.polygons), np.int64); me_.polygons.foreach_get("material_index", mi_)
np.savez_compressed(os.path.join(OUT, "hyb_uv.npz"), uv=uvh.reshape(-1, 2), lt=lt, mi=mi_)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "hyb.blend"))
json.dump(rep, open(os.path.join(OUT, "hyb_build.json"), "w"), indent=1)
print("BUILD", json.dumps(rep))
