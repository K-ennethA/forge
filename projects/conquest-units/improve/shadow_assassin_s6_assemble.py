# Shadow Assassin build section 6 (adapted from elias_s6_assemble.py, minus the face / eye / mouth / hair bake work): the
# BLADE object (own node; placed straight at its HELD pose -- the sheet's reverse hold beside the right thigh; s7 brings the
# right arm onto it and bakes the hold into the bind pose), assembly (hidden skin removed, gloved-hand interiors decimated,
# centred), materials + palette + the glow gate (S2: only the sigils, subtle, purple), UVs, report, the preview exit, the
# bake (normal + AO from the subdivided MPFB high + parts; normal texels kept on the gloves only), the per-class AO floors,
# the improved save.
t_props = time.time()
# ---- the BLADE at its held pose (build frame): grip centre vs the right shoulder joint (BLADE_GRIP), the blade axis (fist ->
# blade) straight down leaned by BLADE_TILT, the blade plane frontal (convex side out to his right) turned by the yaw
Vbl_, Fbl_, Rbl_, BLM = VP.blade(BLADE)
BLADE_GRIP_P = SHO["R"] + np.array([BLADE_GRIP["out"], BLADE_GRIP["fwd"], -BLADE_GRIP["drop"]])
_zb = unit(np.array([-math.tan(math.radians(BLADE_TILT["out"])), -math.tan(math.radians(BLADE_TILT["fwd"])), -1.0]))
_xb = np.array([-1.0, 0.0, 0.0]); _xb = unit(_xb - _zb * float(_xb @ _zb))
_xb = K._rot(_zb, math.radians(BLADE_TILT["yaw"])) @ _xb
_yb = np.cross(_zb, _xb)
BLADE_R = np.stack([_xb, _yb, _zb], 1)                 # blade local -> build frame rotation
add_part("blade", Vbl_ @ BLADE_R.T + BLADE_GRIP_P, Fbl_, Rbl_, w="rigid:blade", obj="blade")
BLADE_INFO = {"grip_centre": BLADE_GRIP_P.round(4).tolist(), "axis": _zb.round(4).tolist(), "blade_len_m": round(float(BLM["blade_len"]), 4),
              "tip": (BLADE_R @ BLM["tip"] + BLADE_GRIP_P).round(4).tolist(), "grip_r_m": BLADE["grip_r"],
              "overall_len_m": round(float(np.linalg.norm(BLM["tip"] - BLM["pommel"])), 4)}
print("PROPS", json.dumps({"blade": BLADE_INFO, "seconds": round(time.time() - t_props, 1)}))
REG = ["tunic", "tunic_shade", "sleeve", "glove", "trousers", "boot", "boot_cuff", "boot_strap", "boot_sole",
       "facewrap", "hood_void", "hood_inner", "hood", "hood_trim", "cape", "cape_inner", "cloak", "cloak_inner", "cloak_sigil",
       "gold", "gold_dark", "gem_dark", "leather", "leather_dark", "skirt", "skirt_inner", "sash", "sash_inner", "sash_trim",
       "scarf", "scarf_shade"]
REG_B = ["blade", "blade_edge", "blade_sigil", "grip", "guard", "gold"]
# hidden skin removed: the feet inside the boot shells, the shins inside the boot shafts, the forearm skin under the bracers,
# and every body face wholly COVERED -- each vertex's rays along a cone round its own normal (the normal + 4 directions 45 deg
# off it) all meet the hood / face wrap / scarf / cloak / capes / skirt / belt within 0.35 m (the head under the hood + wrap,
# the back under the cloak); the forearms / hands never (they hang free and move)
_cdom = np.array([MB[j] for j in np.argmax(CW, 1)], dtype=object)
_foot = np.array([all(_cdom[i].startswith(("foot_", "ball_")) for i in f) for f in CF])
_underbr = np.zeros(len(CF), bool)
for s in "LR":
    lo_ = s.lower()
    _bra = WRI[s] + (ELB[s] - WRI[s]) * (BRACER["t"][0] + 0.06); _brb = WRI[s] + (ELB[s] - WRI[s]) * (BRACER["t"][1] - 0.06)
    _bax = unit(_brb - _bra); _blen = float(np.linalg.norm(_brb - _bra))
    _underbr |= np.array([all(_cdom[i] in ("lowerarm_" + lo_, "hand_" + lo_) and 0.0 < float((CV[i] - _bra) @ _bax) < _blen
                              for i in f) for f in CF])
_bvh_coat = comb_bvh({"hood", "facewrap", "scarf", "cloak", "cape0", "cape1", "skirt", "skirtunder", "belt", "sash"}, with_body=False)
_cn0 = VP.vertex_normals(CV, CF)
if float(np.mean(np.einsum("ij,ij->i", _cn0, CV - CV.mean(0)))) < 0:
    _cn0 = -_cn0
_cov_v = np.zeros(len(CV), bool)
_armish = np.array([n.startswith(("lowerarm", "hand", "index", "middle", "ring", "pinky", "thumb")) for n in _cdom])
for i in np.nonzero(~_armish)[0]:
    n_ = _cn0[i]
    t1_ = np.cross(n_, [0.0, 0.0, 1.0])
    t1_ = unit(t1_) if np.linalg.norm(t1_) > 1e-6 else unit(np.cross(n_, [1.0, 0.0, 0.0]))
    t2_ = np.cross(n_, t1_)
    o_ = Vector(CV[i] + n_ * 0.0005)
    _cov_v[i] = all(_bvh_coat.ray_cast(o_, Vector(unit(d_)), 0.35)[0] is not None for d_ in (n_, n_ + t1_, n_ - t1_, n_ + t2_, n_ - t2_))
_undercoat = np.array([bool(_cov_v[f].all()) for f in CF])
_keep = ~_foot & (reg != "boot") & ~_underbr & ~_undercoat
_usedv = np.unique(np.concatenate([np.array(f) for f, k in zip(CF, _keep) if k]))
_rm = -np.ones(len(CV), dtype=np.int64); _rm[_usedv] = np.arange(len(_usedv))
report["hidden_skin_removed"] = {"foot_faces": int(_foot.sum()), "boot_shin_faces": int((reg == "boot").sum()),
                                 "under_bracer_faces": int(_underbr.sum()), "covered_faces": int(_undercoat.sum()),
                                 "covered_head_faces": int((_undercoat & (reg == "facewrap")).sum()),
                                 "tris_removed": int(sum(len(f) - 2 for f, k in zip(CF, _keep) if not k))}
CV_all, CF_all, reg_all = CV, CF, reg
CV = CV[_usedv]; CW = CW[_usedv]
CF = [[int(_rm[i]) for i in f] for f, k in zip(CF, _keep) if k]
reg = reg[_keep]
# gloved-hand density (Elias v7.1 / Varden glove pattern): per hand the interior vertices (every incident face a hand face of
# ONE region; the wrist line exact) collapse-decimated to HAND_DECIMATE of that hand's tris; the bake high keeps full hands
HAND_INFO = {"ratio": dict(HAND_DECIMATE) if HAND_DECIMATE else None}
_HCHAIN = ("hand", "index", "middle", "ring", "pinky", "thumb")


def _hand_faces(CV_, CF_, CW_, side):
    dn_ = [MB[j] for j in np.argmax(CW_, 1)]
    ish_ = np.array([n_.split("_")[0] in _HCHAIN and n_.endswith("_" + side.lower()) for n_ in dn_])
    return np.array([bool(ish_[f].all()) for f in CF_])


for _s in "LR":
    HAND_INFO["tris_before_" + _s] = int(tri_count_F([f for f, h in zip(CF, _hand_faces(CV, CF, CW, _s)) if h]))
if HAND_DECIMATE:
    for _s in "LR":
        _hf = _hand_faces(CV, CF, CW, _s)
        _vreg, _bad = {}, set()
        for f, h, r_ in zip(CF, _hf, reg):
            for i in f:
                if not h or _vreg.setdefault(i, r_) != r_:
                    _bad.add(i)
        _gv = sorted(set(i for f, h in zip(CF, _hf) if h for i in f) - _bad)
        _tme = bpy.data.meshes.new("hand_tmp"); _tme.from_pydata(np.asarray(CV).tolist(), [], [list(map(int, f)) for f in CF]); _tme.update()
        _tmp = bpy.data.objects.new("hand_tmp", _tme); scene.collection.objects.link(_tmp)
        _ra = _tmp.data.attributes.new("rid", "INT", "FACE")
        _ra.data.foreach_set("value", np.array([REG.index(r_) for r_ in reg], dtype=np.int32))
        _vg = _tmp.vertex_groups.new(name="g"); _vg.add(_gv, 1.0, "REPLACE")
        _ntri_all = tri_count_F(CF); _ntri_h = HAND_INFO["tris_before_" + _s]
        _dm = _tmp.modifiers.new("dec", "DECIMATE"); _dm.decimate_type = "COLLAPSE"
        _dm.ratio = (_ntri_all - (1.0 - HAND_DECIMATE[_s]) * _ntri_h) / _ntri_all
        _dm.vertex_group = "g"; _dm.use_collapse_triangulate = True
        _dg = bpy.context.evaluated_depsgraph_get()
        _me2 = bpy.data.meshes.new_from_object(_tmp.evaluated_get(_dg))
        _cv2, _cf2 = mesh_arrays(_me2)
        _rid2 = np.empty(len(_me2.polygons), dtype=np.int32); _me2.attributes["rid"].data.foreach_get("value", _rid2)
        bpy.data.objects.remove(_tmp, do_unlink=True); bpy.data.meshes.remove(_me2); bpy.data.meshes.remove(_tme)
        CV = np.asarray(_cv2, float); CF = [list(map(int, f)) for f in _cf2]; reg = np.array([REG[i] for i in _rid2], dtype=object)
        CW = transfer(CV)
    for _s in "LR":
        HAND_INFO["tris_after_" + _s] = int(tri_count_F([f for f, h in zip(CF, _hand_faces(CV, CF, CW, _s)) if h]))
report["hand_decimation"] = HAND_INFO
print("HANDS", json.dumps(HAND_INFO))
ISL = [{"name": "body", "V": CV, "F": CF, "R": list(reg), "w": "body", "obj": "main"}] + PARTS
allV = np.vstack([p["V"] for p in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, 0.0])
report["centre_shift"] = SHIFT.round(6).tolist()
report["min_z_before_shift"] = round(float(lo0[2]), 6)
OBJ = {k_: {"V": [], "F": [], "R": [], "RANGE": {}, "FRANGE": {}, "n": 0} for k_ in ("main", "blade")}
for p in ISL:
    o = OBJ[p["obj"]]
    o["RANGE"][p["name"]] = (o["n"], o["n"] + len(p["V"]))
    o["FRANGE"][p["name"]] = (len(o["F"]), len(o["F"]) + len(p["F"]))
    o["V"].append(p["V"] - SHIFT)
    o["F"] += [[i + o["n"] for i in f] for f in p["F"]]
    o["R"] += list(p["R"])
    o["n"] += len(p["V"])
for o in OBJ.values():
    o["V"] = np.vstack(o["V"])
assert set(OBJ["main"]["R"]) <= set(REG), sorted(set(OBJ["main"]["R"]) - set(REG))
assert set(OBJ["blade"]["R"]) <= set(REG_B), sorted(set(OBJ["blade"]["R"]) - set(REG_B))


def make_mat(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300); vc.name = "col"
    vg = nt.nodes.new("ShaderNodeVertexColor"); vg.layer_name = "Glow"; vg.location = (-600, -300); vg.name = "glow"
    nt.links.new(vg.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    mat.use_backface_culling = True
    return mat


def new_obj(name, V, F):
    me_ = bpy.data.meshes.new(name)
    me_.from_pydata(np.asarray(V).tolist(), [], [list(map(int, f)) for f in F])
    me_.update()
    ob_ = bpy.data.objects.new(name, me_)
    scene.collection.objects.link(ob_)
    return ob_


def repaint(obs, pal):
    out = {}
    for o in obs:
        out[o.name] = PAL.paint(o.data, pal)
        for s_ in o.material_slots:
            PAL.apply_material(s_.material, pal)
    return out


def glow_tiers(pal):
    """glow gate (S2 default): the ONLY emitting regions are the sigils (GLOW_REGIONS), subtle (emission_scale <= cap,
    material emission strength <= 2.0), hue purple (GLOW_HUE window)."""
    import colorsys
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    hues = {n: round(colorsys.rgb_to_hsv(*[c / 255.0 for c in v["emission"]])[0] * 360.0, 1)
            for n, v in pal["regions"].items() if "emission" in v}
    strength = float(pal["material"].get("emission_strength", 1.0))
    ok_grade = set(tier) == set(GLOW_REGIONS) and all(0.0 < t <= GLOW_MAX_SCALE for t in tier.values()) and strength <= 2.0
    ok_hue = all(abs(h - GLOW_HUE[0]) <= GLOW_HUE[1] for h in hues.values())
    peak = max(float(np.max(PAL.srgb_to_linear(pal["regions"][n]["emission"]) * tier[n])) for n in tier)
    return {"emission_scale_tiers": tier, "emission_strength": strength, "grade_pass": ok_grade, "accent_hue_deg": hues,
            "hue_pass": ok_hue, "glow_peak_linear": round(peak, 4), "glow_x_strength_peak": round(peak * strength, 4)}


MAT_BODY = make_mat(UNIT + "_body")
MAT_BLADE = make_mat(UNIT + "_blade")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["grade_pass"] and report["glow_tiers"]["default"]["hue_pass"], report["glow_tiers"]
print("GLOWGATE", json.dumps(report["glow_tiers"]["default"]))
low = new_obj(UNIT, OBJ["main"]["V"], OBJ["main"]["F"])
low.data.materials.append(MAT_BODY)
rid = np.array([REG.index(r) for r in OBJ["main"]["R"]], dtype=np.int32)
PAL.store_regions(low.data, REG, rid, np.ones(len(rid)))
bko = new_obj(UNIT + "_blade", OBJ["blade"]["V"], OBJ["blade"]["F"])
bko.data.materials.append(MAT_BLADE)
PAL.store_regions(bko.data, REG_B, np.array([REG_B.index(r) for r in OBJ["blade"]["R"]], dtype=np.int32), np.ones(len(OBJ["blade"]["R"])))
PROP_OBS = [bko]
report["regions_faces"] = repaint([low] + PROP_OBS, pal_default)
me = low.data
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in OBJ["main"]["RANGE"].items()})
_nose = BV[HEAD_B & (np.abs(BV[:, 0]) < 0.004)]
NOSE = _nose[np.argmin(_nose[:, 1])]
anchor = np.array([0.5 * (EYE["L"]["c"][0] + EYE["R"]["c"][0]), HC[1], EYE["L"]["c"][2]]) - SHIFT
landmark = NOSE - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (between the eye sockets, at the skull's mid depth) -> nose tip (midline), under the wrap",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
_glovef = np.isin(rid, [REG.index(r) for r in SMOOTH_NORMAL_REGIONS])
for ob_ in [bko, low]:
    ob_.data.shade_flat()
    bpy.context.view_layer.objects.active = ob_
    for o in scene.objects:
        o.select_set(o is ob_)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.002, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=0.002)
    bpy.ops.object.mode_set(mode="OBJECT")
tris_main = tri_count_F(OBJ["main"]["F"]); tris_bl = tri_count_F(OBJ["blade"]["F"])
_groups = {}
for p in PARTS:
    g_ = p["name"].split(".")[0]
    _groups[g_] = _groups.get(g_, 0) + tri_count_F(p["F"])
report["tris"] = {"total": tris_main + tris_bl, "main": tris_main, "blade": tris_bl, "body": tri_count_F(CF), **_groups}
report["tier_rationale"] = ("HERO role: window [%d, %d]. The MPFB body (covered skin harvested) + hood + face wrap + capes + cloak + "
                            "skirt + boots + the outfit pieces; the blade as its own node." % tuple(TRI_BUDGET))
report["open_edges"] = {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}
allV2 = np.vstack([OBJ["main"]["V"], OBJ["blade"]["V"]])
lo_a, hi_a = allV2.min(0), allV2.max(0)
Hh = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / Hh, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(Hh, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(Hh * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4)}}
report["parts"] = {"boots": BOOT_INFO, "knees": KNEE_INFO, "facewrap": FACEWRAP_INFO, "scarf": SCARF_INFO, "straps": STRAP_INFO,
                   "skirt": SKIRT_INFO, "bracers": BRACER_INFO, "hood": HOOD_INFO, "cloak": CLOAK_INFO, "capes": CAPE_INFO,
                   "blade": BLADE_INFO, "pendant": PENDANT_C.round(4).tolist(), "brooch": BROOCH_C.round(4).tolist()}
for ob_ in [low] + PROP_OBS:
    ob_["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "hero"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = 0.0
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = "from scratch on an MPFB2 base: design/reference/shadow_assassin/shadow_assassin_sheet.webp (review-log 2026-10-05)"
low["conquest_scale_policy"] = "natural proportions in metres; game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1; float copy _GLOW) -> Emission Color; Col -> Base Color"
bko["conquest_toggle"] = "the blade is its own node + bone 'blade' (child of hand_r): hide or swap it"


def box(pts, pad):
    pts = np.asarray(pts)
    return [(pts.min(0) - pad).tolist(), (pts.max(0) + pad).tolist()]


def part_pts(*names):
    return np.vstack([p["V"] for p in PARTS if p["name"].split(".")[0] in names or p["name"] in names]) - SHIFT


FOCUS = {"head": box(part_pts("hood", "facewrap"), 0.01),
         "blade": box(np.vstack([OBJ["blade"]["V"], (WRI["R"] - SHIFT)[None]]), 0.03),
         "hold": box([BLADE_GRIP_P - SHIFT], 0.13),
         "belt": box(part_pts("buckle", "pouch", "beltring", "sashchevron"), 0.03),
         "chest": box(part_pts("brooch", "pendant", "scarf"), 0.04),
         "back_sigil": box(part_pts("cloaksigil", "cloakornament"), 0.05),
         "boots": box(part_pts("bootfoot", "bootcuff", "kneeguard"), 0.03)}
low["conquest_focus"] = json.dumps(FOCUS)
print("TRIS", json.dumps(report["tris"]))
print("MEASURE", json.dumps(report["measure"]), "HIDDEN", json.dumps(report["hidden_skin_removed"]))
if PREVIEW:
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    json.dump(report, open(PREVIEW[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("PREVIEW_SAVED", PREVIEW, round(time.time() - T0, 1))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== bake normal + AO (high = subdivided MPFB body + parts)
try:
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
t_bake = time.time()
HIGHB = new_obj(UNIT + "_highbody", CV_all - SHIFT, CF_all)
HIGHB.data.shade_smooth()
sm_ = HIGHB.modifiers.new("subd", "SUBSURF"); sm_.levels = 1; sm_.render_levels = 1
HV_, HF_ = [], []
_o = 0
for p in PARTS:
    if p["obj"] != "main":
        continue
    HV_.append(p["V"] - SHIFT); HF_ += [[i + _o for i in f] for f in p["F"]]; _o += len(p["V"])
HIGHP = new_obj(UNIT + "_highparts", np.vstack(HV_), HF_)
dg = bpy.context.evaluated_depsgraph_get()
_hb_me = bpy.data.meshes.new_from_object(HIGHB.evaluated_get(dg))
high_tris = sum(len(p_.vertices) - 2 for p_ in _hb_me.polygons) + tri_count_F(HF_)
bpy.data.meshes.remove(_hb_me)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.cycles.seed = 0
BAKE_CAGE = 0.012
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
nt = MAT_BODY.node_tree
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
bko.hide_render = True
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
for o in scene.objects:
    o.select_set(o in (HIGHB, HIGHP, low))
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, AO_SAMPLES)):
    nt.nodes.active = node
    scene.cycles.samples = samples
    t_ = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, max_ray_distance=BAKE_CAGE * 2.5,
                             margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t_, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
me.shade_flat()
cov_n = px[:, 2] > 0.25
cov_a = np.abs(pa[:, 0] - pa[:, 1]) < 0.02
bstats.update({"cage_extrusion": BAKE_CAGE, "resolution": {"normal": RN, "ao": RA}, "high_tris": high_tris,
               "high_rule": "MPFB body (all faces, before the harvest) at subdivision 1 (smooth) + every main part",
               "normal_baked_texels_pct": round(100 * float(cov_n.mean()), 2), "ao_baked_texels_pct": round(100 * float(cov_a.mean()), 2),
               "ao_mean": round(float(pa[cov_a, 0].mean()), 4), "ao_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0)
pa[~cov_a, :3] = 1.0
bstats["pixel_sha_raw"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                           "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
me.calc_loop_triangles()
_ntq = len(me.loop_triangles)
_ltl = np.empty(_ntq * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", _ltl); _ltl = _ltl.reshape(-1, 3)
_ltp = np.empty(_ntq, dtype=np.int64); me.loop_triangles.foreach_get("polygon_index", _ltp)
_uvq = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", _uvq); _uvq = _uvq.reshape(-1, 2)


def raster(mask_tris, res, fill, value=True, use_max=False):
    """paint fill[gy, gx] for every texel inside the UV triangles listed (Elias's rasteriser)."""
    for tri_ in mask_tris:
        P3 = _uvq[_ltl[tri_]] * res - 0.5
        x0, y0 = np.floor(P3.min(0)).astype(int); x1, y1 = np.ceil(P3.max(0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, res - 1), min(y1, res - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx_, gy_ = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        a_, b_, c_ = P3
        den_ = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den_) < 1e-12:
            continue
        w0 = ((b_[1] - c_[1]) * (gx_ - c_[0]) + (c_[0] - b_[0]) * (gy_ - c_[1])) / den_
        w1 = ((c_[1] - a_[1]) * (gx_ - c_[0]) + (a_[0] - c_[0]) * (gy_ - c_[1])) / den_
        ins_ = (w0 >= -0.02) & (w1 >= -0.02) & (1 - w0 - w1 >= -0.02)
        if use_max:
            fill[gy_[ins_], gx_[ins_]] = np.maximum(fill[gy_[ins_], gx_[ins_]], value)
        else:
            fill[gy_[ins_], gx_[ins_]] = value


# normal texels: kept only on the SMOOTH_NORMAL_REGIONS (the gloves: the MPFB hands' smooth high); every garment face flat
# (their high IS the low; the body under the painted tunic / trousers carries anatomy the cloth must not show)
_ridq = np.array([REG.index(r) for r in OBJ["main"]["R"]])
_smask = np.zeros((RN, RN), bool)
raster(np.nonzero(np.isin(_ridq[_ltp], [REG.index(r) for r in SMOOTH_NORMAL_REGIONS]))[0], RN, _smask)
for _ in range(4):
    m_ = _smask.copy()
    m_[1:] |= _smask[:-1]; m_[:-1] |= _smask[1:]; m_[:, 1:] |= _smask[:, :-1]; m_[:, :-1] |= _smask[:, 1:]
    _smask = m_
px[~_smask.reshape(-1), :3] = (0.5, 0.5, 1.0)
img_n.pixels.foreach_set(px.ravel())
bstats["normal_kept"] = {"regions": list(SMOOTH_NORMAL_REGIONS), "texels_pct": round(100.0 * float(_smask.mean()), 2)}
# AO floors per region class (no black grime on the near-black palette)
_flr = np.full((RA, RA), AO_FLOOR["default"])
for cls_, regs_ in AO_FLOOR_REGIONS.items():
    raster(np.nonzero(np.isin(_ridq[_ltp], [REG.index(r) for r in regs_]))[0], RA, _flr, AO_FLOOR[cls_], use_max=True)
for _ in range(6):
    f_ = _flr.copy()
    f_[1:] = np.maximum(f_[1:], _flr[:-1]); f_[:-1] = np.maximum(f_[:-1], _flr[1:])
    f_[:, 1:] = np.maximum(f_[:, 1:], _flr[:, :-1]); f_[:, :-1] = np.maximum(f_[:, :-1], _flr[:, 1:])
    _flr = f_
_ao_raw = pa[:, 0].copy()
pa[:, :3] = (_flr.reshape(-1) + (1.0 - _flr.reshape(-1)) * _ao_raw)[:, None]
img_ao.pixels.foreach_set(pa.ravel())
bstats["ao_lift"] = {"floors": AO_FLOOR, "raw_p05": round(float(np.percentile(_ao_raw[cov_a], 5)), 4),
                     "lifted_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4), "lifted_mean": round(float(pa[cov_a, 0].mean()), 4)}
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
bstats["seconds"] = round(time.time() - t_bake, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
os.makedirs(TEX_DIR, exist_ok=True)
for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
    img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()
bsdf = nt.nodes["Principled BSDF"]
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(nt.nodes["col"].outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
report["material"] = {"base_color": "Col x AO bake (multiply)", "normal": "baked normal map (glove texels; flat elsewhere)",
                      "roughness": pal_default["material"].get("roughness"), "cel_bands": "none", "outline_shells": "none"}
scene.render.engine = "BLENDER_EEVEE"
for ob_ in (HIGHB, HIGHP):
    m_ = ob_.data
    bpy.data.objects.remove(ob_, do_unlink=True); bpy.data.meshes.remove(m_)
bko.hide_render = False
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True
print("BAKE", json.dumps({k: v for k, v in bstats.items() if not k.startswith("pixel_sha")}))


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, UNIT + "_normal.png"), (img_ao, UNIT + "_ao.png")):
        img.filepath = rel_prefix + nm


def geometry_digest(obs):
    h = hashlib.sha256()
    for o in obs:
        me_ = o.data
        co = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", co)
        lv = np.empty(len(me_.loops), dtype=np.int64); me_.loops.foreach_get("vertex_index", lv)
        h.update(np.round(co, 6).astype(np.float32).tobytes()); h.update(lv.tobytes())
        for nm in ("Col", "Glow"):
            cd = np.empty(len(me_.loops) * 4, dtype=np.float32); me_.color_attributes[nm].data.foreach_get("color", cd)
            h.update(np.round(cd, 5).tobytes())
        uv = np.empty(len(me_.loops) * 2); me_.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


DIG["geometry_colour_uv"] = geometry_digest([low] + PROP_OBS)
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "every region pixel-sampled from the saved sheet (improve/shadow_assassin_palette.py; notes per region)"}
bpy.context.preferences.filepaths.save_version = 0
set_tex_paths("//textures/")
os.makedirs(os.path.dirname(OUT_IMPROVED), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))
