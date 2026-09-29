# Wren build section 6: the pitchfork object, assembly (hidden skin removed, centred), materials + palette + glow gate,
# UVs (hair in its own strip), report, the preview exit, the bake (normal + AO from the subdivided MPFB high + parts), the
# per-region AO lift, the improved save.
Vf_, Ff_, Rf_, FKL = VP.pitchfork(FORK)
FORK_T = np.array([FORK_REST[0], FORK_REST[1], 0.0])      # rest: upright, butt on the floor
add_part("pitchfork", Vf_ + FORK_T, Ff_, Rf_, w="rigid:pitchfork", obj="fork")
FORK_INFO = {"total_len": round(float(FKL["top"]), 4), "x_height": round(float(FKL["top"]) / (Z_TOP - SOLE_T), 3),
             "grip_at_m": round(FORK["grip_at"] * FORK["len"], 4), "rest_butt": FORK_T.round(4).tolist()}
REG = ["skin", "skin_shadow", "lips", "mouth", "liner", "brow", "eye_sclera", "eye_iris", "eye_pupil", "hair", "hair_shade",
       "hair_tie", "shirt", "sleeve_roll", "vest", "trousers", "trousers_shade", "wrap", "wrap_lace", "boot", "boot_cuff",
       "boot_sole", "strap", "brass", "brass_dark", "rope", "rope_dark", "pouch", "pouch_flap", "cloak", "cloak_worn",
       "cloak_lining", "patch_a", "patch_b", "patch_c", "stitch", "crystal", "cord", "bracer", "bracer_strap"]
REG_F = ["wood", "wood_dark", "grip", "grip_dark", "ferrule"]
# hidden skin removed: the scalp under the cap, the feet inside the boot shells, the shins inside the boot shafts
_cdom = np.array([MB[j] for j in np.argmax(CW, 1)], dtype=object)
_foot = np.array([all(_cdom[i].startswith(("foot_", "ball_")) for i in f) for f in CF])
# the thigh / knee skin fully inside the trouser blouse (between its tuck and its top, with margins; the blouse rides the
# same skin weights, >= 6 mm off it)
_pz0 = max(PUFF_INFO[s]["tuck_z"] for s in "LR") + 0.03
_pz1 = min(PUFF_INFO[s]["top_z"] for s in "LR") - 0.04
_underpuff = np.array([all(_cdom[i].startswith(("thigh_", "calf_")) and _pz0 < CV[i][2] < _pz1 for i in f) for f in CF])
# the left forearm skin fully under the bracer (between its ends, with margins)
_bra = WRI["L"] + (ELB["L"] - WRI["L"]) * (BRACER["t"][0] + 0.06); _brb = WRI["L"] + (ELB["L"] - WRI["L"]) * (BRACER["t"][1] - 0.06)
_bax = unit(_brb - _bra); _blen = float(np.linalg.norm(_brb - _bra))
_underbr = np.array([all(_cdom[i] in ("lowerarm_l", "hand_l") and 0.0 < float((CV[i] - _bra) @ _bax) < _blen for i in f) for f in CF])
_keep = (reg != "hair") & ~_foot & (reg != "boot") & ~_underpuff & ~_underbr
_usedv = np.unique(np.concatenate([np.array(f) for f, k in zip(CF, _keep) if k]))
_rm = -np.ones(len(CV), dtype=np.int64); _rm[_usedv] = np.arange(len(_usedv))
report["hidden_skin_removed"] = {"scalp_faces": int((reg == "hair").sum()), "foot_faces": int(_foot.sum()),
                                 "boot_shin_faces": int((reg == "boot").sum()), "under_blouse_faces": int(_underpuff.sum()), "under_bracer_faces": int(_underbr.sum()),
                                 "tris_removed": int(sum(len(f) - 2 for f, k in zip(CF, _keep) if not k))}
CV_all, CF_all, reg_all = CV, CF, reg                  # (kept for the bake high and the clip gates)
CV = CV[_usedv]; CW = CW[_usedv]
CF = [[int(_rm[i]) for i in f] for f, k in zip(CF, _keep) if k]
reg = reg[_keep]
ISL = [{"name": "body", "V": CV, "F": CF, "R": list(reg), "w": "body", "obj": "main"}] + PARTS
allV = np.vstack([p["V"] for p in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, 0.0])
report["centre_shift"] = SHIFT.round(6).tolist()
report["min_z_before_shift"] = round(float(lo0[2]), 6)
OBJ = {"main": {"V": [], "F": [], "R": [], "RANGE": {}, "FRANGE": {}, "n": 0},
       "fork": {"V": [], "F": [], "R": [], "RANGE": {}, "FRANGE": {}, "n": 0}}
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
assert set(OBJ["fork"]["R"]) <= set(REG_F), sorted(set(OBJ["fork"]["R"]) - set(REG_F))


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
    """glow gate: the ONLY emitting region is the teal crystal (pendant + bracer diamond), subtle, hue teal (+-20 deg of 170)."""
    import colorsys
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    hues = {n: round(colorsys.rgb_to_hsv(*[c / 255.0 for c in v["emission"]])[0] * 360.0, 1)
            for n, v in pal["regions"].items() if "emission" in v}
    ok_grade = set(tier) == {"crystal"} and 0.0 < tier["crystal"] <= 0.35
    ok_hue = all(abs(h - 170.0) <= 20.0 for h in hues.values())
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "grade_pass": ok_grade, "accent_hue_deg": hues, "hue_pass": ok_hue}


MAT_BODY = make_mat(UNIT + "_body")
MAT_FORK = make_mat(UNIT + "_pitchfork")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["grade_pass"] and report["glow_tiers"]["default"]["hue_pass"], report["glow_tiers"]
low = new_obj(UNIT, OBJ["main"]["V"], OBJ["main"]["F"])
low.data.materials.append(MAT_BODY)
rid = np.array([REG.index(r) for r in OBJ["main"]["R"]], dtype=np.int32)
PAL.store_regions(low.data, REG, rid, np.ones(len(rid)))
fko = new_obj(UNIT + "_pitchfork", OBJ["fork"]["V"], OBJ["fork"]["F"])
fko.data.materials.append(MAT_FORK)
rid_f = np.array([REG_F.index(r) for r in OBJ["fork"]["R"]], dtype=np.int32)
PAL.store_regions(fko.data, REG_F, rid_f, np.ones(len(rid_f)))
report["regions_faces"] = repaint([low, fko], pal_default)
me = low.data
fa = np.empty(len(me.polygons)); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == j].sum() / fa.sum()), 4) for j, n in enumerate(REG)}
me["conquest_islands"] = json.dumps({k: list(v) for k, v in OBJ["main"]["RANGE"].items()})
_nose = BV[HEAD_B & (np.abs(BV[:, 0]) < 0.004)]
NOSE = _nose[np.argmin(_nose[:, 1])]
anchor = np.array([0.5 * (EYE["L"]["c"][0] + EYE["R"]["c"][0]), HC[1], EYE["L"]["c"][2]]) - SHIFT
landmark = NOSE - SHIFT
dvec = landmark - anchor
report["facing"] = {"rule": "head centre (between the eyes, at the skull's mid depth) -> nose tip (midline)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
HAIR_REG_IDS = [REG.index(r) for r in ("hair", "hair_shade", "hair_tie")]
_hairf = np.isin(rid, HAIR_REG_IDS)
for ob_ in (low, fko):
    ob_.data.shade_flat()
    bpy.context.view_layer.objects.active = ob_
    for o in scene.objects:
        o.select_set(o is ob_)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.002, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    if ob_ is not low:
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(rotate=True, margin=0.002)
        bpy.ops.object.mode_set(mode="OBJECT")
        continue
    scene.tool_settings.mesh_select_mode = (False, False, True)
    scene.tool_settings.use_uv_select_sync = False
    for sel_hair in (False, True):
        bm_ = bmesh.from_edit_mesh(ob_.data)
        bm_.faces.ensure_lookup_table()
        for f_ in bm_.faces:
            f_.select_set(False)
        for f_ in bm_.faces:
            if bool(_hairf[f_.index]) == sel_hair:
                f_.select_set(True)
        bmesh.update_edit_mesh(ob_.data)
        bpy.ops.uv.select_all(action="SELECT")
        if not sel_hair:
            bpy.ops.uv.pack_islands(rotate=True, margin=0.002)
            continue
        bpy.ops.object.mode_set(mode="OBJECT")
        uvd_ = np.empty(len(ob_.data.loops) * 2); ob_.data.uv_layers.active.data.foreach_get("uv", uvd_); uvd_ = uvd_.reshape(-1, 2)
        lt_ = np.empty(len(ob_.data.polygons), dtype=np.int64); ob_.data.polygons.foreach_get("loop_total", lt_)
        lpf_ = np.repeat(np.arange(len(lt_)), lt_)
        hl_, nl_ = _hairf[lpf_], ~_hairf[lpf_]
        uvd_[nl_, 0] *= (1.0 - HAIR_UV_STRIP - 0.012)
        lo_u, hi_u = uvd_[hl_].min(0), uvd_[hl_].max(0)
        uvd_[hl_] = np.array([1.0 - HAIR_UV_STRIP, 0.0]) + (uvd_[hl_] - lo_u) / np.maximum(hi_u - lo_u, 1e-9) * \
            np.array([HAIR_UV_STRIP - 0.002, 1.0])
        ob_.data.uv_layers.active.data.foreach_set("uv", uvd_.ravel())
        ob_.data.update()
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.uv.select_all(action="SELECT")
        bpy.ops.uv.pack_islands(udim_source="ORIGINAL_AABB", rotate=True, margin=0.002)
    bpy.ops.object.mode_set(mode="OBJECT")
    ob_.data.polygons.foreach_set("select", np.ones(len(ob_.data.polygons), bool))
    uvd_ = np.empty(len(ob_.data.loops) * 2); ob_.data.uv_layers.active.data.foreach_get("uv", uvd_); uvd_ = uvd_.reshape(-1, 2)
    report["uv_hair_strip"] = {"strip_u_from": round(1.0 - HAIR_UV_STRIP, 4), "hair_faces": int(_hairf.sum()),
                               "hair_uv_u_min": round(float(uvd_[_hairf[lpf_], 0].min()), 4),
                               "nonhair_uv_u_max": round(float(uvd_[~_hairf[lpf_], 0].max()), 4)}
tris_main = tri_count_F(OBJ["main"]["F"]); tris_fk = tri_count_F(OBJ["fork"]["F"])
_groups = {}
for p in PARTS:
    g_ = p["name"].split(".")[0]
    _groups[g_] = _groups.get(g_, 0) + tri_count_F(p["F"])
report["tris"] = {"total": tris_main + tris_fk, "main": tris_main, "pitchfork": tris_fk, "body": tri_count_F(CF),
                  "hair_locks": sum(tri_count_F(p["F"]) for p in PARTS if p["name"].startswith("lock")), **_groups}
report["tier_rationale"] = ("HERO role: window [%d, %d]. The MPFB body (face, hands, fingers for the grip) + a real hair mass "
                            "+ a patched two-tone cloak with hood + cowl + boots + the outfit pieces + a detailed pitchfork." % tuple(TRI_BUDGET))
report["open_edges"] = {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}
report["open_edges_rule"] = "every part is a closed solid (listed here only if not); the body is the MPFB skin"
allV2 = np.vstack([OBJ["main"]["V"], OBJ["fork"]["V"]])
lo_a, hi_a = allV2.min(0), allV2.max(0)
Hh = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / Hh, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(Hh, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(Hh * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4)}}
report["parts"] = {"boots": BOOT_INFO, "puff": PUFF_INFO, "skirt": SKIRT_INFO, "rope": ROPE_INFO, "bracer": BRACER_INFO,
                   "pendant": PENDANT_INFO, "cloak": CLOAK_INFO, "pitchfork": FORK_INFO, "locks": LOCK_INFO}
report["hair"] = HAIR_INFO
for ob_ in (low, fko):
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
low["conquest_source"] = "from scratch on an MPFB2 base: the artist's sheet design/reference/wren-character-sheet.webp"
low["conquest_scale_policy"] = "natural proportions in metres; game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
fko["conquest_toggle"] = "the pitchfork is its own node + bone 'pitchfork' (child of hand_r): hide or swap it"


def box(pts, pad):
    pts = np.asarray(pts)
    return [(pts.min(0) - pad).tolist(), (pts.max(0) + pad).tolist()]


def part_pts(*names):
    return np.vstack([p["V"] for p in PARTS if p["name"].split(".")[0] in names or p["name"] in names]) - SHIFT


FOCUS = {"face": box([EYE["L"]["c"] - SHIFT, EYE["R"]["c"] - SHIFT, np.array([0, Y_LIP, Z_LIP - 0.03]) - SHIFT], 0.045),
         "portrait": box([np.array([-HR[0], Y_CHIN, HC[2] + HR[2] * 0.75]) - SHIFT, np.array([HR[0], Y_CHIN, Z_CHIN - 0.02]) - SHIFT], 0.02),
         "head": box([HC - SHIFT + np.array([0, 0, HR[2] + 0.03]), HC - SHIFT - np.array([HR[0] + 0.03, HR[1] + 0.02, 0.09]),
                      HC - SHIFT + np.array([HR[0] + 0.03, HR[1] + 0.03, 0])], 0.01),
         "cloak": box(part_pts("cloak", "hood", "cowl"), 0.02),
         "patches": box(part_pts("hood", "stitches"), 0.03),
         "necklace": box([PEND_TOP - SHIFT + np.array([0.0, 0.0, -0.035]), PEND_TOP - SHIFT + np.array([0.0, 0.0, 0.06]),
                          CLASP_C - SHIFT], 0.03),
         "bracer": box(part_pts("bracer", "bracergem"), 0.02),
         "boots": box(part_pts("bootfoot", "bootcuff", "sole"), 0.03),
         "torso": box([SHO["L"] - SHIFT, SHO["R"] - SHIFT, np.array([0, 0, Z_SASH - 0.2]) - SHIFT], 0.05),
         "fork_head": box([FORK_T - SHIFT + np.array([0, 0, FORK["collar"][0] * FORK["len"] - 0.05]),
                           FORK_T - SHIFT + np.array([0, 0, FKL["top"] + 0.01])], 0.05),
         "fork_full": box(np.vstack([OBJ["fork"]["V"]]), 0.03),
         "eyes": box([EYE["L"]["c"] - SHIFT + np.array([0.022, 0, 0.018]), EYE["R"]["c"] - SHIFT - np.array([0.022, 0, 0.012])], 0.004)}
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
_hV, _hF = mesh_arrays(_hb_me)
bpy.data.meshes.remove(_hb_me)
_bvh_h = BVHTree.FromPolygons(_hV.tolist(), _hF)
_shrink = np.array([_bvh_h.find_nearest(Vector(p))[3] for p in (CV - SHIFT)])
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
fko.hide_render = True
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
for o in scene.objects:
    o.select_set(o in (HIGHB, HIGHP, low))
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
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
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
bstats.update({"cage_extrusion": BAKE_CAGE, "low_to_high_skin_m": {"p99": round(float(np.percentile(_shrink, 99)), 5),
                                                                   "max": round(float(_shrink.max()), 5)},
               "resolution": {"normal": RN, "ao": RA}, "high_tris": high_tris,
               "high_rule": "MPFB body at subdivision 1 (smooth) + every part",
               "normal_baked_texels_pct": round(100 * float(cov_n.mean()), 2),
               "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n] > 0.05).mean()), 4),
               "ao_baked_texels_pct": round(100 * float(cov_a.mean()), 2),
               "ao_mean": round(float(pa[cov_a, 0].mean()), 4), "ao_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), "wren_normal_%s.npy" % TAG), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), "wren_ao_%s.npy" % TAG), pa[:, :1])
_strip_n = (np.arange(RN * RN) % RN) >= int((1.0 - HAIR_UV_STRIP - 0.006) * RN)
_strip_a = (np.arange(RA * RA) % RA) >= int((1.0 - HAIR_UV_STRIP - 0.006) * RA)
px[_strip_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
# only the SKIN keeps the baked normal detail (the MPFB high's smooth face / hands); every garment / part face gets flat
# normal texels: the parts have no high detail (their high IS the low) and the body under the painted clothes carries the
# naked MPFB anatomy (pectorals, navel, knees) that the shirt / vest / trousers must not show
_skin_ids = [REG.index(r) for r in ("skin", "skin_shadow", "lips", "mouth", "liner", "brow")]
me.calc_loop_triangles()
_ntq = len(me.loop_triangles)
_ltl = np.empty(_ntq * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", _ltl); _ltl = _ltl.reshape(-1, 3)
_ltp = np.empty(_ntq, dtype=np.int64); me.loop_triangles.foreach_get("polygon_index", _ltp)
_uvq = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", _uvq); _uvq = _uvq.reshape(-1, 2)
_ridq = np.array([REG.index(r) for r in OBJ["main"]["R"]])
_skinmask = np.zeros((RN, RN), bool)
for tri_ in np.nonzero(np.isin(_ridq[_ltp], _skin_ids))[0]:
    P3 = _uvq[_ltl[tri_]] * RN - 0.5
    x0, y0 = np.floor(P3.min(0)).astype(int); x1, y1 = np.ceil(P3.max(0)).astype(int)
    x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, RN - 1), min(y1, RN - 1)
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
    _skinmask[gy_[ins_], gx_[ins_]] = True
for _ in range(4):                                   # dilate over the bake margin
    m_ = _skinmask.copy()
    m_[1:] |= _skinmask[:-1]; m_[:-1] |= _skinmask[1:]; m_[:, 1:] |= _skinmask[:, :-1]; m_[:, :-1] |= _skinmask[:, 1:]
    _skinmask = m_
_flatn = ~_skinmask.reshape(-1)
px[_flatn, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
bstats["normal_skin_only"] = {"rule": "normal texels outside the (dilated) skin-region UV triangles set flat",
                              "skin_texels_pct": round(100.0 * float(_skinmask.mean()), 2)}
bstats["seconds"] = round(time.time() - t_bake, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
# AO lift per region class (the raw bake is what the digests / tolerance gate see)
me.calc_loop_triangles()
_nt_ = len(me.loop_triangles)
_lt_l = np.empty(_nt_ * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", _lt_l); _lt_l = _lt_l.reshape(-1, 3)
_lt_p = np.empty(_nt_, dtype=np.int64); me.loop_triangles.foreach_get("polygon_index", _lt_p)
_uvl = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", _uvl); _uvl = _uvl.reshape(-1, 2)
_ridf = np.array([REG.index(r) for r in OBJ["main"]["R"]])
_flr = np.full((RA, RA), AO_FLOOR["default"])
for cls_, regs_ in AO_FLOOR_REGIONS.items():
    ids_ = [REG.index(r) for r in regs_]
    for tri_ in np.nonzero(np.isin(_ridf[_lt_p], ids_))[0]:
        P3 = _uvl[_lt_l[tri_]] * RA - 0.5
        x0, y0 = np.floor(P3.min(0)).astype(int); x1, y1 = np.ceil(P3.max(0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, RA - 1), min(y1, RA - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx_, gy_ = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        a_, b_, c_ = P3
        den_ = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den_) < 1e-12:
            continue
        w0 = ((b_[1] - c_[1]) * (gx_ - c_[0]) + (c_[0] - b_[0]) * (gy_ - c_[1])) / den_
        w1 = ((c_[1] - a_[1]) * (gx_ - c_[0]) + (a_[0] - c_[0]) * (gy_ - c_[1])) / den_
        inside_ = (w0 >= -0.02) & (w1 >= -0.02) & (1 - w0 - w1 >= -0.02)
        _flr[gy_[inside_], gx_[inside_]] = np.maximum(_flr[gy_[inside_], gx_[inside_]], AO_FLOOR[cls_])
for _ in range(6):
    f_ = _flr.copy()
    f_[1:] = np.maximum(f_[1:], _flr[:-1]); f_[:-1] = np.maximum(f_[:-1], _flr[1:])
    f_[:, 1:] = np.maximum(f_[:, 1:], _flr[:, :-1]); f_[:, :-1] = np.maximum(f_[:, :-1], _flr[:, 1:])
    _flr = f_
_ao_raw = pa[:, 0].copy()
pa[:, :3] = (_flr.reshape(-1) + (1.0 - _flr.reshape(-1)) * _ao_raw)[:, None]
_hair_ao_raw = _ao_raw[_strip_a & cov_a]
pa[_strip_a, :3] = 1.0
img_ao.pixels.foreach_set(pa.ravel())
bstats["hair_strip"] = {"rule": "hair faces packed into u > %.2f; normal texels there flat, AO texels white" % (1 - HAIR_UV_STRIP),
                        "raw_ao_in_hair_strip_p05": round(float(np.percentile(_hair_ao_raw, 5)), 4) if len(_hair_ao_raw) else None,
                        "texels_normal": int(_strip_n.sum()), "texels_ao": int(_strip_a.sum())}
bstats["ao_lift"] = {"floors": AO_FLOOR, "raw_p05": round(float(np.percentile(_ao_raw[cov_a], 5)), 4),
                     "lifted_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4), "lifted_mean": round(float(pa[cov_a, 0].mean()), 4)}
DIG["ao_lifted"] = sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))
if not DIGEST_ONLY:
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
report["material"] = {"base_color": "Col x AO bake (multiply)", "normal": "baked normal map",
                      "roughness": pal_default["material"].get("roughness"), "cel_bands": "none (lesson: undone on vampwarrior)",
                      "outline_shells": "none (lesson: undone on vampwarrior)"}
scene.render.engine = "BLENDER_EEVEE"
for ob_ in (HIGHB, HIGHP):
    m_ = ob_.data
    bpy.data.objects.remove(ob_, do_unlink=True); bpy.data.meshes.remove(m_)
fko.hide_render = False
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True
print("BAKE", json.dumps({k: v for k, v in bstats.items() if k != "pixel_sha"}))


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


DIG["geometry_colour_uv"] = geometry_digest([low, fko])
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "the sheet's six palette chips + direct samples off the figures / detail panels"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))
