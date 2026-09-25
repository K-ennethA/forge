"""werewolf_escaped look stage: clean shading + reference-sampled region colour.

Called by build_werewolf_escaped.py (stage 0, before the clips are authored) on the
COPY of the wip blend. Operates on the game mesh only; never touches the rig.

SHADING
  * the inherited custom split normals are dropped (they were carried over from the
    generator mesh and no longer match this retopo's surface),
  * face winding recomputed outward (the mesh is closed: 0 non-manifold edges),
  * the 31 n-gons are triangulated (the glTF exporter cannot build MikkTSpace
    tangents on a face with more than 4 corners - that was the "Could not calculate
    tangents" warning), quads/tris untouched; groups, shape keys and UVs survive
    (bmesh round trip). Two of those n-gons fold over a neighbour triangle and
    their triangulation repeats it; each such zero-thickness pair is removed
    (2 pairs, 2 fold-tip vertices), leaving the mesh manifold and valid,
  * every face smooth, no sharp edges (see CREASE_DEG for the measured sweep).

COLOUR
  Regions come from the rigforge body-part tags (tag_Head / tag_Torso / tag_Arm.* /
  tag_Leg.*), split by the deform weights and by boundaries read off the same
  reference the palette is sampled from (refs/form-a-front.png, -side.png), scaled
  by MM_PER_PX = mesh height / reference figure height:
    Arm.*  : dominant DEF-hand.*       -> skin, else jacket (sleeve)
    Head   : above the hairline        -> hair; below the tee neckline at the
             front -> shirt; below the collar top behind -> jacket; else skin
    Torso/Leg above the jacket hem     -> jacket, except the open-front strip -> shirt
    Torso/Leg below the hem            -> jeans; Leg below the boot top -> boots
  The flat per-region colour is blurred BLUR_RINGS rings across boundaries (vertex
  neighbour averaging, linear space) and written as a point colour attribute "Col",
  read by one Principled material through a Color Attribute node -> glTF COLOR_0.

  Why vertex colour and not a baked texture (both measured 2026-09-24):
  * Godot 4.6 imports COLOR_0 and sets vertex_color_use_as_albedo=true on the
    StandardMaterial3D, values kept linear (vertex_color_is_srgb=false) - probed
    headless on a cube glb; the texture route imports fine too.
  * The texture route needs a clean atlas and this mesh has none: the inherited
    UVMap overlaps on 21.3% of covered texels (hard-mirror copied the UVs);
    rigforge_auto_uv (tag seams + ANGLE_BASED) collapses the closed ends, so hands
    baked jacket colour and boots baked jeans; Smart UV Project on this noisy
    retopo shatters into hundreds of islands (19.7% coverage) whose black gutters
    bleed into every mip level in-engine. Per-vertex colour has no atlas to get
    wrong, carries the soft region blend for free, and ships no image.
  The inherited UVMap is kept only so the exporter can build tangents.
"""
import bpy, bmesh, math, os, json
import numpy as np
from mathutils import Vector

# Fully smooth: no crease angle is applied (180 = no edge qualifies). Sweep on this
# mesh (sharp edges marked): 30 deg 7531, 45 deg 3901, 60 deg 2068, 80 deg 984. On
# renders 30/45 are visibly faceted; close-ups of 60 vs fully smooth show 60 adding
# hard facet shards on the hair fringe and the bunched jeans cuffs (organic
# decimation noise, not design edges). This human mesh has no claws or other hard
# design edges that would earn sharps, so none are marked.
CREASE_DEG = 180.0
BLUR_RINGS = 3
ROUGHNESS = 0.8        # one value: the look is flat colour, not a PBR material study

# --- boundaries read off refs/form-a-front.png / form-a-side.png (pixel rows, y down)
REF_CROWN_PX, REF_SOLE_PX = 32, 968   # top of hair, boot soles (front view)
REF_CHIN_PX = 150
REF_HEM_PX = 432                      # jacket hem at the sides
REF_CREW_PX = 178                     # tee neckline
REF_BOOT_PX = 878                     # jeans hem over the boot top
REF_TEE_HALF_PX = 35                  # half-width of the tee strip between the jacket fronts
REF_COLLAR_BACK_PX = 140              # top of the jacket collar behind the neck (side view)
# hairline height as a fraction of chin->crown, at 0 / 90 / 180 deg round the head
# from the front: front bangs row 85, side (ear top) row 105, nape row 130 (side view)
HAIRLINE = [(0.0, (REF_CHIN_PX - 85) / (REF_CHIN_PX - REF_CROWN_PX)),
            (90.0, (REF_CHIN_PX - 105) / (REF_CHIN_PX - REF_CROWN_PX)),
            (180.0, (REF_CHIN_PX - 130) / (REF_CHIN_PX - REF_CROWN_PX))]

REGIONS = ["skin", "hair", "jacket", "shirt", "jeans", "boots"]


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _tags_and_dominant(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    n = len(obj.data.vertices)
    tag = [""] * n
    dom = [""] * n
    for v in obj.data.vertices:
        best, bw = "", 0.0
        for g in v.groups:
            name = names[g.group]
            if name.startswith("tag_") and g.weight > 0.5:
                tag[v.index] = name[4:]
            elif name.startswith("DEF-") and g.weight > bw:
                best, bw = name, g.weight
        dom[v.index] = best
    return tag, dom


def classify(obj):
    """Region index per vertex (rest pose, world space) and the boundaries used."""
    me = obj.data
    co = np.array([tuple(obj.matrix_world @ v.co) for v in me.vertices])
    nrm = np.array([tuple((obj.matrix_world.to_3x3() @ v.normal).normalized()) for v in me.vertices])
    tag, dom = _tags_and_dominant(obj)
    tag = np.array(tag, dtype=object); dom = np.array(dom, dtype=object)
    z0, z1 = co[:, 2].min(), co[:, 2].max()
    mm_per_px = (z1 - z0) / (REF_SOLE_PX - REF_CROWN_PX)
    z_at = lambda row: z0 + (REF_SOLE_PX - row) * mm_per_px
    b = {"mesh_height_m": z1 - z0, "m_per_ref_px": mm_per_px,
         "chin_z": z_at(REF_CHIN_PX), "crown_z": z1, "hem_z": z_at(REF_HEM_PX),
         "crew_z": z_at(REF_CREW_PX), "boot_z": z_at(REF_BOOT_PX),
         "tee_half_w": REF_TEE_HALF_PX * mm_per_px}
    head = tag == "Head"
    hc = co[head & (co[:, 2] > b["chin_z"])].mean(axis=0)
    # The hem is re-measured on the mesh itself: the mesh's proportions are not the
    # picture's to the centimetre, and the jacket's lower lip is a real feature -
    # the band of downward-facing vertices (normal z < -0.6) on the body within
    # -8/+3 cm of the reference's estimate. Its median z is the hem.
    body = (tag == "Torso") | np.array([t.startswith("Leg") for t in tag])
    lip = body & (nrm[:, 2] < -0.6) & (co[:, 2] > b["hem_z"] - 0.08) & (co[:, 2] < b["hem_z"] + 0.03)
    b["hem_z_ref"] = b["hem_z"]
    b["hem_lip_vertices"] = int(lip.sum())
    if lip.sum() >= 20:
        b["hem_z"] = float(np.median(co[lip, 2]))
    b["collar_back_z"] = z_at(REF_COLLAR_BACK_PX)
    b["head_centre_xy"] = [float(hc[0]), float(hc[1])]
    lab = np.full(len(co), REGIONS.index("jacket"))
    # front is -Y on the rest rig (toes point -Y)
    for i in range(len(co)):
        t, d, (x, y, z) = tag[i], dom[i], co[i]
        if t == "Head":
            ang = math.degrees(math.atan2(abs(x - hc[0]), -(y - hc[1])))
            frac = float(np.interp(ang, [a for a, _ in HAIRLINE], [f for _, f in HAIRLINE]))
            hair_z = b["chin_z"] + frac * (b["crown_z"] - b["chin_z"])
            if z > hair_z:
                lab[i] = REGIONS.index("hair")
            elif z < b["crew_z"] and ang < 60.0:        # tee neckline, front
                lab[i] = REGIONS.index("shirt")
            elif z < b["collar_back_z"] and ang >= 90.0:  # jacket collar, behind
                lab[i] = REGIONS.index("jacket")
            else:
                lab[i] = REGIONS.index("skin")
        elif t.startswith("Arm"):
            lab[i] = REGIONS.index("skin" if d.startswith("DEF-hand") else "jacket")
        else:  # Torso, Leg.*, untagged
            if t.startswith("Leg") and z < b["boot_z"]:
                lab[i] = REGIONS.index("boots")
            elif z < b["hem_z"]:
                lab[i] = REGIONS.index("jeans")
            elif (abs(x) < b["tee_half_w"] and z < b["crew_z"] and y < hc[1]
                  and nrm[i, 1] < -0.2):
                lab[i] = REGIONS.index("shirt")
            else:
                lab[i] = REGIONS.index("jacket")
    counts = {r: int((lab == k).sum()) for k, r in enumerate(REGIONS)}
    return lab, b, counts


def _neighbours(me):
    nb = [[] for _ in me.vertices]
    for e in me.edges:
        a, c = e.vertices
        nb[a].append(c); nb[c].append(a)
    return nb


def vertex_colours(obj, palette):
    lab, bounds, counts = classify(obj)
    lin = np.array([[srgb_to_linear(c) for c in palette[r]["srgb"]] for r in REGIONS])
    col = lin[lab]
    nb = _neighbours(obj.data)
    for _ in range(BLUR_RINGS):
        col = np.array([(col[i] + col[n].sum(axis=0)) / (1 + len(n)) if n else col[i]
                        for i, n in enumerate(nb)])
    return col, lab, bounds, counts


def fix_shading(obj):
    me = obj.data
    report = {}
    if "custom_normal" in me.attributes:
        me.attributes.remove(me.attributes["custom_normal"])
        report["custom_normals_dropped"] = True
    before = np.array([tuple(p.normal) for p in me.polygons])
    bm = bmesh.new()
    bm.from_mesh(me)
    ngons = [f for f in bm.faces if len(f.verts) > 4]
    report["ngons_triangulated"] = len(ngons)
    if ngons:
        new = set(bmesh.ops.triangulate(bm, faces=ngons)["faces"])
        # Some n-gons fold back over a neighbouring triangle T (measured: 11 of the
        # 31 have an ear equal to an existing face). When the triangulation cuts
        # that ear, the new triangle E repeats T exactly - a zero-thickness double
        # layer: 2 duplicate faces, 2 edges with 4 faces, Mesh.validate() fails.
        # The pair is removed together (context FACES also drops any edge/vertex
        # left without a face), which returns every edge to exactly two faces.
        orig = {}
        for f in bm.faces:
            if f not in new:
                orig[frozenset(v.index for v in f.verts)] = f
        pairs = []
        for f in new:
            t = orig.get(frozenset(v.index for v in f.verts))
            if t is not None:
                pairs += [f, t]
        nv = len(bm.verts)
        if pairs:
            bmesh.ops.delete(bm, geom=pairs, context="FACES")
        report["folded_duplicate_pairs_removed"] = len(pairs) // 2
        report["vertices_removed"] = nv - len(bm.verts)
    report["non_manifold_edges"] = sum(1 for e in bm.edges if not e.is_manifold)
    bm.faces.index_update()
    bm.normal_update()
    pre = {f.index: f.normal.copy() for f in bm.faces}
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.normal_update()
    report["faces_flipped_outward"] = sum(1 for f in bm.faces if f.normal.dot(pre[f.index]) < 0)
    bm.to_mesh(me)
    bm.free()
    me.update()
    report["faces_before"] = len(before); report["faces_after"] = len(me.polygons)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    if "sharp_face" in me.attributes:
        me.attributes.remove(me.attributes["sharp_face"])
    me.set_sharp_from_angle(angle=math.radians(CREASE_DEG))
    sharp = me.attributes.get("sharp_edge")
    report["crease_deg"] = CREASE_DEG
    report["sharp_edges"] = int(sum(d.value for d in sharp.data)) if sharp else 0
    report["edges"] = len(me.edges)
    me.update()
    report["mesh_valid"] = not me.copy().validate()   # validate() returns True if it had to fix
    return report


def build_material(name="werewolf_escaped_look", layer="Col"):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    col = nt.nodes.new("ShaderNodeVertexColor")
    col.layer_name = layer
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = ROUGHNESS
    bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def to_srgb255(lin):
    return [int(round(255 * (c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)))
            for c in lin]


def apply(obj, palette_path):
    palette = json.load(open(palette_path))["regions"]
    shading = fix_shading(obj)
    col, lab, bounds, counts = vertex_colours(obj, palette)
    me = obj.data
    for a in list(me.color_attributes):
        me.color_attributes.remove(a)
    attr = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", np.concatenate([col, np.ones((len(col), 1))], axis=1)
                          .astype(np.float32).ravel())
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = me.color_attributes.active_color_index
    mat = build_material()
    me.materials.clear()
    me.materials.append(mat)
    # region cores after the blur (median per region, sRGB) - equals the palette
    # wherever a region is wider than the blur
    written = {r: to_srgb255(np.median(col[lab == k], axis=0))
               for k, r in enumerate(REGIONS) if (lab == k).any()}
    return {"shading": shading,
            "bounds": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in bounds.items()},
            "region_vertices": counts, "region_median_srgb_written": written,
            "colour_attribute": "Col (POINT, FLOAT_COLOR) -> glTF COLOR_0",
            "material": mat.name}
