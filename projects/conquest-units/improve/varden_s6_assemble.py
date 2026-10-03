# Varden build section 6 (adapted from elias_s6_assemble.py): the SWORD and SCABBARD objects (own nodes; rest placement:
# sheathed at his LEFT hip on the sword belt, the hilt leaning forward / out, the scabbard pushed clear of the leg / skirt
# / boot by a measured stand-off search), assembly (hidden skin removed, centred), materials + palette + glow gate (no
# emitters on Varden), UVs (hair + beard in their own strip), report, the preview exit, the bake (normal + AO from the
# subdivided MPFB high + parts; the one-volume hair proxy built PER GROUP -- the scalp hairdo and the beard + mustache),
# the per-region AO lift, the improved save.
t_props = time.time()


def hand_frame(side):
    """the rest hand (build frame): a = index -> pinky knuckle line, e = toward the fingertips, n = the palm normal (toward
    the thumb's side), grip = the fist centre (Wren s8's HANDS rule), kn = the knuckle centre."""
    lo_ = side.lower()
    wr = P("hand_" + lo_)
    kn = np.mean([P("%s_01_%s" % (f, lo_)) for f in ("index", "middle", "ring", "pinky")], 0)
    a_h = unit(P("pinky_01_" + lo_) - P("index_01_" + lo_))
    e_h = unit((P("middle_01_" + lo_) - wr) - float((P("middle_01_" + lo_) - wr) @ a_h) * a_h)
    n_p = unit(np.cross(e_h, a_h))
    if float((P("thumb_02_" + lo_) - wr) @ n_p) < 0:
        n_p = -n_p
    return {"a": a_h, "e": e_h, "n": n_p, "grip": 0.45 * wr + 0.55 * kn + n_p * 0.019, "kn": kn, "wr": wr}


HANDF = {s: hand_frame(s) for s in "LR"}
# ---- the SWORD (own object 'varden_sword', bone 'sword') sheathed in the SCABBARD (own object 'varden_scabbard', bone
# 'scabbard' child of the pelvis) at his LEFT hip. Frame: zf = up the grip (the hilt leaning forward / out from vertical),
# yf = the blade's flat facing out from the hip, xf = the guard span along the body. The guard sits on the sword belt at
# SWORD_HANG azimuth; the stand-off grows (2 mm steps) until every scabbard axis sample clears the leg / skirt / tabard /
# boots / belt by its own half width + 6 mm.
Vsw0_, Fsw_, Rsw_, SWL = VP.longsword(SWORD)
Vsc0_, Fsc_, Rsc_, SCL = VP.scabbard(SCABBARD, SWORD)
_sh = SWORD_HANG
_rd = dir_front(_sh["phi_front"])
_zg = Z_BELT - SWORD_BELT["drop"] - _sh["z_hilt"]
_bvh_clear = comb_bvh({"skirt", "tabard", "skirtstar", "belt", "swordbelt", "bootshaft", "bootcuff", "bootstrap", "bootbuckle",
                       "bootfoot"}, body=(BV, [f for f, n, a in zip(BF, fdomn, is_arm_f) if not a]))
_r0 = float(rp_front(_bvh_clear, _sh["phi_front"], [_zg - 0.02, _zg, _zg + 0.02]).max())
_hw_sc = 0.5 * SWORD["blade_w"][0] + SCABBARD["pad"] + SCABBARD["t"]
_ax = [np.array([0.0, 0.0, z_]) for z_ in np.linspace(SCL["top"], SCL["bottom"], 24)]


def sword_frame(tf_deg, to_deg):
    tf_, to_ = math.radians(tf_deg), math.radians(to_deg)
    zf_ = unit(np.array([0.0, 0.0, 1.0]) * math.cos(tf_) + np.array([0.0, -1.0, 0.0]) * math.sin(tf_) + _rd * math.sin(to_))
    yf_ = unit(_rd - zf_ * float(_rd @ zf_))
    return np.stack([np.cross(yf_, zf_), yf_, zf_], 1)


def sword_clear(R3_, off_):
    G0_ = np.array([0.0, AX_Y, _zg]) + _rd * (_r0 + off_)
    worst_ = 9.0
    for q_ in _ax:
        h_ = _bvh_clear.find_nearest(Vector(R3_ @ q_ + G0_))
        if h_[0] is not None:
            worst_ = min(worst_, float(h_[3]) - (_hw_sc + SWORD_CLEAR))
    return worst_, G0_


# the hang search: per (forward tilt, outward tilt) in SWORD_TILTS the smallest stand-off (2 mm steps) at which every scabbard
# axis sample clears the leg / skirt / boots / belts by the scabbard half width + SWORD_CLEAR; the combination needing the
# smallest stand-off wins (the sword hangs as close to the hip as its tilt allows)
_cands = []
for tf_ in SWORD_TILTS[0]:
    for to_ in SWORD_TILTS[1]:
        R3_ = sword_frame(tf_, to_)
        for k_ in range(50):
            off_ = _sh["off"] + 0.002 * k_
            w_, G0_ = sword_clear(R3_, off_)
            if w_ >= 0.0:
                _cands.append((off_, abs(tf_ - _sh["tilt_fwd"]) + abs(to_ - _sh["tilt_out"]), tf_, to_, w_, G0_, R3_))
                break
if _cands:
    SWORD_OFF, _, SWORD_TILT_F, SWORD_TILT_O, worst_, SWORD_G0, SWORD_R3 = min(_cands, key=lambda c_: (c_[0], c_[1]))
else:
    SWORD_OFF, SWORD_TILT_F, SWORD_TILT_O = None, _sh["tilt_fwd"], _sh["tilt_out"]
    SWORD_R3 = sword_frame(SWORD_TILT_F, SWORD_TILT_O)
    worst_, SWORD_G0 = sword_clear(SWORD_R3, _sh["off"] + 0.1)
add_part("sword", Vsw0_ @ SWORD_R3.T + SWORD_G0, Fsw_, Rsw_, w="rigid:sword", obj="sword")
add_part("scabbard", Vsc0_ @ SWORD_R3.T + SWORD_G0, Fsc_, Rsc_, w="rigid:scabbard", obj="scabbard")
SWORD_INFO = {"total_len_m": round(float(SWL["pommel_top"] + SWORD["blade_len"]), 4), "guard": SWORD_G0.round(4).tolist(),
              "tip": (SWORD_R3 @ SWL["tip"] + SWORD_G0).round(4).tolist(),
              "pommel_z": round(float((SWORD_R3 @ np.array([0.0, 0.0, SWL["pommel_top"]]) + SWORD_G0)[2]), 4),
              "standoff_m": None if SWORD_OFF is None else round(SWORD_OFF, 4), "clear_worst_m": round(worst_, 4),
              "tilt_fwd_out_deg": [SWORD_TILT_F, SWORD_TILT_O],
              "rule": "per tilt pair in SWORD_TILTS the smallest stand-off (2 mm steps) at which every scabbard axis sample "
                      "clears the leg / skirt / boots / belts by the scabbard half width + SWORD_CLEAR; the smallest wins"}
print("PROPS", json.dumps({"sword": SWORD_INFO, "seconds": round(time.time() - t_props, 1)}))
REG = ["skin", "skin_shadow", "lips", "mouth", "liner", "lash", "brow", "face_line", "scar", "scar_shadow", "beard_inner",
       "eye_sclera", "eye_iris", "eye_iris_dark", "eye_pupil", "eye_hilite",
       "hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice",
       "beard", "beard_shade", "beard_root", "beard_tip", "beard_crevice", "beard_grey", "beard_grey_tip",
       "tunic", "tunic_shade", "tunic_trim", "tabard", "tabard_shade", "sleeve", "sleeve_roll", "glove", "trousers",
       "boot", "boot_cuff", "boot_trim", "boot_sole", "strap", "strap_edge",
       "belt", "belt_edge", "brass", "brass_dark", "vambrace", "gem_teal",
       "cloak", "cloak_trim", "cloak_lining", "fur", "fur_shade", "fur_tip", "fur_root", "fur_inner"]
REG_S = ["steel", "steel_dark", "brass", "brass_dark", "grip", "grip_dark", "gem_dark"]
REG_B = ["scabbard", "scabbard_dark", "brass"]
# hidden skin removed: the scalp under the cap, the feet inside the boot shells, the shins inside the boot shafts, the
# forearm skin under both vambraces
_cdom = np.array([MB[j] for j in np.argmax(CW, 1)], dtype=object)
_foot = np.array([all(_cdom[i].startswith(("foot_", "ball_")) for i in f) for f in CF])
_underbr = np.zeros(len(CF), bool)
for s in "LR":
    lo_ = s.lower()
    _bra = WRI[s] + (ELB[s] - WRI[s]) * (VAMBRACE["t"][0] + 0.06); _brb = WRI[s] + (ELB[s] - WRI[s]) * (VAMBRACE["t"][1] - 0.06)
    _bax = unit(_brb - _bra); _blen = float(np.linalg.norm(_brb - _bra))
    _underbr |= np.array([all(_cdom[i] in ("lowerarm_" + lo_, "hand_" + lo_) and 0.0 < float((CV[i] - _bra) @ _bax) < _blen
                              for i in f) for f in CF])
_underpuff = np.zeros(len(CF), bool)
# VARDEN (Elias's rule): body faces wholly UNDER the cloak / fur roll / tunic skirt / tabard / belts / collar / strap --
# every vertex's rays along a cone round its own normal (the normal + 4 directions tilted 45 deg off it) meet one of
# those garments within 0.35 m -- are never seen (the open front, the hands and everything below the hems keep theirs)
_bvh_coat = comb_bvh({"cloak", "furroll", "skirt", "tabard", "belt", "swordbelt", "collar", "strap"}, with_body=False)
_cn0 = VP.vertex_normals(CV, CF)
if float(np.mean(np.einsum("ij,ij->i", _cn0, CV - CV.mean(0)))) < 0:
    _cn0 = -_cn0
_cov_v = np.zeros(len(CV), bool)
for i in np.nonzero(~np.isin(_cdom, ["head", "neck_01"]))[0]:
    n_ = _cn0[i]
    t1_ = np.cross(n_, [0.0, 0.0, 1.0])
    t1_ = unit(t1_) if np.linalg.norm(t1_) > 1e-6 else unit(np.cross(n_, [1.0, 0.0, 0.0]))
    t2_ = np.cross(n_, t1_)
    o_ = Vector(CV[i] + n_ * 0.0005)
    _cov_v[i] = all(_bvh_coat.ray_cast(o_, Vector(unit(d_)), 0.35)[0] is not None
                    for d_ in (n_, n_ + t1_, n_ - t1_, n_ + t2_, n_ - t2_))
_undercoat = np.array([bool(_cov_v[f].all()) for f in CF]) & (reg != "hair")
# v2: ENCLOSED skin (the harvest that pays for the face round's cuts -- the sealed mouth's interior sleeve and the eye-
# socket skin behind the eyeballs): a head face goes only if EVERY one of its vertices is enclosed -- rays from the vertex
# (lifted 0.4 mm off the skin) toward every viewing direction of the front / tactical hemisphere (ENCLOSED_DIRS) all hit
# the body or an eyeball within 8 cm. The head carries no facial rig, so the enclosure holds in every clip.
_bvh_enc = comb_bvh({"eye"})
_cn = VP.vertex_normals(CV, CF)
if float(np.mean(np.einsum("ij,ij->i", _cn, CV - CV.mean(0)))) < 0:
    _cn = -_cn
_cand = (_cdom == "head") & ((np.linalg.norm(CV - np.array([0.0, Y_LIP, Z_SLIT]), axis=1) < 0.030) |
                             (np.linalg.norm(CV - EYE["L"]["c"], axis=1) < EYE["L"]["r"] + 0.006) |
                             (np.linalg.norm(CV - EYE["R"]["c"], axis=1) < EYE["R"]["r"] + 0.006))
_dirs = [unit(d) for d in ENCLOSED_DIRS]
_enc_v = np.zeros(len(CV), bool)
for i in (np.nonzero(_cand)[0] if len(_dirs) else []):          # (ENCLOSED_DIRS = () turns the harvest off)
    o_ = Vector(CV[i] + _cn[i] * 0.0004)
    _enc_v[i] = all(_bvh_enc.ray_cast(o_, Vector(d), 0.08)[0] is not None for d in _dirs)
_enclosed = np.array([bool(_enc_v[f].all()) for f in CF]) & (reg != "hair")


def mouth_hidden(V, F):
    """v5: per face in the mouth zone (MOUTH_HIDDEN[:3]; up to MOUTH_HIDDEN[4] behind the lips' front): whether it faces
    BACKWARD (the rims folded flat against the bridge: culled, never drawn) and how far its NEAREST vertex lies behind the
    front skin (m; front rays against the zone's forward-facing faces only -- a face with any vertex on the front stays).
    -> in_zone, behind, back."""
    fc_ = np.array([V[f].mean(0) for f in F])
    dz_ = fc_[:, 2] - np.interp(fc_[:, 0], SEAM["xs"], SEAM["zc"])
    inz_ = (np.abs(fc_[:, 0]) < MOUTH_HIDDEN[0]) & (dz_ < MOUTH_HIDDEN[1]) & (dz_ > -MOUTH_HIDDEN[2]) & \
        (fc_[:, 1] < Y_LIP + MOUTH_HIDDEN[4])
    fn_ = np.array([np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]]) for f in F])
    bvh0_ = BVHTree.FromPolygons(V.tolist(), F)
    first_ = []
    for fi_ in np.nonzero(inz_)[0]:
        h_ = bvh0_.ray_cast(Vector((float(fc_[fi_, 0]), -1.0, float(fc_[fi_, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
        if h_[0] is not None and h_[2] == fi_:
            first_.append(fi_)
    sg_ = -1.0 if float(np.median(fn_[first_, 1])) > 0 else 1.0   # (outward winding: the first-hit faces face -y)
    back_ = inz_ & ((sg_ * fn_[:, 1]) > 0.1 * np.linalg.norm(fn_, axis=1))
    fwd_ = [f for f, b in zip(F, back_) if not b]
    bvh_ = BVHTree.FromPolygons(V.tolist(), fwd_)
    vi_ = np.unique(np.concatenate([np.array(F[i]) for i in np.nonzero(inz_)[0]]))
    vb_ = np.zeros(len(V))
    for i in vi_:
        h_ = bvh_.ray_cast(Vector((float(V[i, 0]), -1.0, float(V[i, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
        vb_[i] = max(float(V[i, 1]) - h_[0][1], 0.0) if h_[0] is not None else 0.0
    beh_ = np.array([float(vb_[f].min()) if z_ else 0.0 for f, z_ in zip(F, inz_)])
    return inz_, beh_, back_


# v5 MOUTH HIDDEN-LAYER HARVEST: in the flat mouth zone the rims folded against the bridge (backward-facing, culled) and
# the layers tucked up to MOUTH_HIDDEN[5] behind the front skin are never seen; left in, smart-project folded their steep
# fold faces into the flat skin's UV islands and their baked corrections (40-70 deg) bled into the flat faces' texels:
# dark texel blotches above the drawn line (rendered; measured on the decoded map)
_mouth_harvest = np.zeros(len(CF), bool)
if MOUTH_HIDDEN is not None and SEAM is not None:
    _mz, _mb, _mk = mouth_hidden(CV, CF)
    _mouth_harvest = _mz & (_mk | ((_mb > MOUTH_HIDDEN[3]) & (_mb <= MOUTH_HIDDEN[5]))) & (reg != "hair")
_keep = (reg != "hair") & ~_foot & (reg != "boot") & ~_underpuff & ~_underbr & ~_enclosed & ~_mouth_harvest & ~HAIR_INTERIOR_MASK & ~_undercoat
_usedv = np.unique(np.concatenate([np.array(f) for f, k in zip(CF, _keep) if k]))
_rm = -np.ones(len(CV), dtype=np.int64); _rm[_usedv] = np.arange(len(_usedv))
report["hidden_skin_removed"] = {"scalp_faces": int((reg == "hair").sum()), "mouth_interior_v7": int(HAIR_INTERIOR_MASK.sum()),
                                 "foot_faces": int(_foot.sum()),
                                 "boot_shin_faces": int((reg == "boot").sum()), "under_cloak_fur_skirt_faces": int(_undercoat.sum()), "under_vambrace_faces": int(_underbr.sum()),
                                 "enclosed_face_faces_v2": int(_enclosed.sum()), "enclosed_rule": "every vertex's rays toward %d front / tactical directions hit body or eyeball within 8 cm" % len(_dirs),
                                 "mouth_hidden_layer_faces_v5": int((_mouth_harvest & ~_enclosed).sum()),
                                 "mouth_hidden_rule": "mouth-zone faces facing backward or %.2f-%.1f mm behind the front skin" % (
                                     1000 * MOUTH_HIDDEN[3], 1000 * MOUTH_HIDDEN[5]) if MOUTH_HIDDEN is not None else None,
                                 "tris_removed": int(sum(len(f) - 2 for f, k in zip(CF, _keep) if not k))}
CV_all, CF_all, reg_all = CV, CF, reg                  # (kept for the bake high and the clip gates)
CV = CV[_usedv]; CW = CW[_usedv]
CF = [[int(_rm[i]) for i in f] for f, k in zip(CF, _keep) if k]
reg = reg[_keep]
# VARDEN GLOVE DECIMATION (budget): the MPFB hands carry ~6.5k faces (measured: 'glove' 6512 of the body's faces) and as
# dark gloves their finger detail is silhouette only -- the INTERIOR glove vertices (every face round them painted glove,
# so every region boundary is kept exactly) are collapse-decimated to GLOVE_DECIMATE; weights re-taken by transfer()
GLOVE_INFO = {"ratio": GLOVE_DECIMATE, "faces_before": int((reg == "glove").sum())}
if GLOVE_DECIMATE is not None and GLOVE_DECIMATE < 1.0:
    _gf = np.array([r_ == "glove" for r_ in reg])
    _nong = set(i for f, g in zip(CF, _gf) if not g for i in f)
    _gv = sorted(set(i for f, g in zip(CF, _gf) if g for i in f) - _nong)
    _tme = bpy.data.meshes.new("glove_tmp"); _tme.from_pydata(np.asarray(CV).tolist(), [], [list(map(int, f)) for f in CF]); _tme.update()
    _tmp = bpy.data.objects.new("glove_tmp", _tme); scene.collection.objects.link(_tmp)
    _ra = _tmp.data.attributes.new("rid", "INT", "FACE")
    _ra.data.foreach_set("value", np.array([REG.index(r_) if r_ in REG else 0 for r_ in reg], dtype=np.int32))
    _vg = _tmp.vertex_groups.new(name="g"); _vg.add(_gv, 1.0, "REPLACE")
    _ntri_all = tri_count_F(CF); _ntri_g = tri_count_F([f for f, g in zip(CF, _gf) if g])
    _dm = _tmp.modifiers.new("dec", "DECIMATE"); _dm.decimate_type = "COLLAPSE"
    _dm.ratio = (_ntri_all - (1.0 - GLOVE_DECIMATE) * _ntri_g) / _ntri_all   # (the modifier's ratio is of the WHOLE mesh)
    _dm.vertex_group = "g"; _dm.use_collapse_triangulate = True
    _dg = bpy.context.evaluated_depsgraph_get()
    _me2 = bpy.data.meshes.new_from_object(_tmp.evaluated_get(_dg))
    _cv2, _cf2 = mesh_arrays(_me2)
    _rid2 = np.empty(len(_me2.polygons), dtype=np.int32); _me2.attributes["rid"].data.foreach_get("value", _rid2)
    bpy.data.objects.remove(_tmp, do_unlink=True); bpy.data.meshes.remove(_me2)
    CV = _cv2; CF = _cf2; reg = np.array([REG[i] for i in _rid2], dtype=object)
    CW = transfer(CV)
    GLOVE_INFO["faces_after"] = int((reg == "glove").sum())
report["glove_decimation"] = GLOVE_INFO
print("GLOVES", json.dumps(GLOVE_INFO))
ISL = [{"name": "body", "V": CV, "F": CF, "R": list(reg), "w": "body", "obj": "main"}] + PARTS
allV = np.vstack([p["V"] for p in ISL])
lo0, hi0 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo0[0] + hi0[0]) / 2, (lo0[1] + hi0[1]) / 2, 0.0])
report["centre_shift"] = SHIFT.round(6).tolist()
report["min_z_before_shift"] = round(float(lo0[2]), 6)
OBJ = {k_: {"V": [], "F": [], "R": [], "RANGE": {}, "FRANGE": {}, "n": 0} for k_ in ("main", "sword", "scabbard")}
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
assert set(OBJ["sword"]["R"]) <= set(REG_S), sorted(set(OBJ["sword"]["R"]) - set(REG_S))
assert set(OBJ["scabbard"]["R"]) <= set(REG_B), sorted(set(OBJ["scabbard"]["R"]) - set(REG_B))


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
    """glow gate: Varden carries NO emitting region (a soldier, no magic: the sheet shows no glow); the steel-teal
    pendant gem is plain paint. (The _GLOW attribute still ships -- all zeros -- for the one Godot read path.)"""
    import colorsys
    tier = {n: float(v.get("emission_scale", 1.0)) for n, v in pal["regions"].items() if "emission" in v}
    hues = {n: round(colorsys.rgb_to_hsv(*[c / 255.0 for c in v["emission"]])[0] * 360.0, 1)
            for n, v in pal["regions"].items() if "emission" in v}
    ok_grade = len(tier) == 0
    ok_hue = True
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "grade_pass": ok_grade, "accent_hue_deg": hues, "hue_pass": ok_hue}


MAT_BODY = make_mat(UNIT + "_body")
MAT_SWORD = make_mat(UNIT + "_sword")
MAT_SCAB = make_mat(UNIT + "_scabbard")
pal_default = PAL.load(UNIT, "default")
report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["grade_pass"] and report["glow_tiers"]["default"]["hue_pass"], report["glow_tiers"]
low = new_obj(UNIT, OBJ["main"]["V"], OBJ["main"]["F"])
low.data.materials.append(MAT_BODY)
rid = np.array([REG.index(r) for r in OBJ["main"]["R"]], dtype=np.int32)
PAL.store_regions(low.data, REG, rid, np.ones(len(rid)))
fko = new_obj(UNIT + "_sword", OBJ["sword"]["V"], OBJ["sword"]["F"])          # (variable kept from Wren: the prop object)
fko.data.materials.append(MAT_SWORD)
PAL.store_regions(fko.data, REG_S, np.array([REG_S.index(r) for r in OBJ["sword"]["R"]], dtype=np.int32), np.ones(len(OBJ["sword"]["R"])))
bko = new_obj(UNIT + "_scabbard", OBJ["scabbard"]["V"], OBJ["scabbard"]["F"])
bko.data.materials.append(MAT_SCAB)
PAL.store_regions(bko.data, REG_B, np.array([REG_B.index(r) for r in OBJ["scabbard"]["R"]], dtype=np.int32), np.ones(len(OBJ["scabbard"]["R"])))
PROP_OBS = [fko, bko]
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
report["facing"] = {"rule": "head centre (between the eyes, at the skull's mid depth) -> nose tip (midline)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
HAIR_REGS = ("hair", "hair_shade", "hair_root", "hair_ring", "hair_tip", "hair_inner", "hair_crevice",
             "beard", "beard_shade", "beard_root", "beard_tip", "beard_crevice", "beard_grey", "beard_grey_tip")
HAIR_REG_IDS = [REG.index(r) for r in HAIR_REGS]
low["conquest_hair_uv_strip"] = float(1.0 - HAIR_UV_STRIP)
_hairf = np.isin(rid, HAIR_REG_IDS)
# v2 FACE ZONE: the body's skin faces (skin / shadow shapes / lips / mouth / liner / brow) whose every vertex is head- or
# neck-dominant -- the face, ears and the bare neck down to the collar (a faceted neck under a smooth face read as a seam
# at the jaw); the torso / arms / legs stay v1
FACE_SKIN = ["skin", "skin_shadow", "lips", "mouth", "liner", "brow", "lash", "face_line", "scar", "scar_shadow", "beard_inner"]
_hdom = np.array([MB[j] in ("head", "neck_01") for j in np.argmax(CW, 1)])
_facez = np.zeros(len(rid), bool)
_facez[:len(CF)] = np.array([bool(_hdom[f].all()) for f in CF]) & np.isin(np.array(reg), FACE_SKIN)
_pa3 = np.empty(len(me.polygons)); me.polygons.foreach_get("area", _pa3)
_pn3 = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("normal", _pn3); _pn3 = _pn3.reshape(-1, 3)
_eF = {}
for fi_ in np.nonzero(_facez)[0]:
    f_ = OBJ["main"]["F"][fi_]
    for k_ in range(len(f_)):
        _eF.setdefault((min(f_[k_], f_[(k_ + 1) % len(f_)]), max(f_[k_], f_[(k_ + 1) % len(f_)])), []).append(fi_)
_dih = np.degrees(np.arccos(np.clip([float(_pn3[a_] @ _pn3[b_]) for a_, b_ in (v_ for v_ in _eF.values() if len(v_) == 2)], -1, 1)))
report["face_zone"] = {"rule": "body skin faces (%s) with every vertex head- or neck_01-dominant" % ", ".join(FACE_SKIN),
                       "faces": int(_facez.sum()), "tris": tri_count_F([OBJ["main"]["F"][i] for i in np.nonzero(_facez)[0]]),
                       "area_cm2": round(1e4 * float(_pa3[_facez].sum()), 1),
                       "dihedral_deg_p50_p90": [round(float(np.percentile(_dih, 50)), 2), round(float(np.percentile(_dih, 90)), 2)],
                       "tris_per_cm2": round(tri_count_F([OBJ["main"]["F"][i] for i in np.nonzero(_facez)[0]]) / (1e4 * float(_pa3[_facez].sum())), 2)}


def uv_share():
    uvd = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uvd); uvd = uvd.reshape(-1, 2)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    a = np.zeros(len(lt))
    for k in range(len(lt)):
        q = uvd[ls[k]:ls[k] + lt[k]]
        a[k] = 0.5 * abs(float(np.dot(q[:, 0], np.roll(q[:, 1], -1)) - np.dot(q[:, 1], np.roll(q[:, 0], -1))))
    return uvd, lt, ls, a
for ob_ in [fko, bko, low]:                     # (the props first: the low's loop ends in its own report lines)
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
    if FACE_UV_SCALE != 1.0:
        # v2: the face zone re-unwrapped as its own islands and scaled to FACE_UV_SCALE x the rest's texel density (the
        # non-hair pack below keeps relative island sizes)
        bpy.ops.object.mode_set(mode="OBJECT")
        _uv0, _lt0, _ls0, _ua0 = uv_share()
        _dens0 = float(_ua0[_facez].sum() / _pa3[_facez].sum()) / float(_ua0[~_facez & ~_hairf].sum() / _pa3[~_facez & ~_hairf].sum())
        bpy.ops.object.mode_set(mode="EDIT")
        bm_ = bmesh.from_edit_mesh(ob_.data); bm_.faces.ensure_lookup_table()
        for f_ in bm_.faces:
            f_.select_set(bool(_facez[f_.index]))
        bmesh.update_edit_mesh(ob_.data)
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.002, area_weight=0.0,
                                 correct_aspect=True, scale_to_bounds=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        _uv1, _lt1, _ls1, _ua1 = uv_share()
        _db = float(_ua1[~_facez & ~_hairf].sum() / _pa3[~_facez & ~_hairf].sum())
        _df = float(_ua1[_facez].sum() / _pa3[_facez].sum())
        _k = math.sqrt(FACE_UV_SCALE ** 2 * _db / _df)
        _fl = np.repeat(_facez, _lt1)
        _uv1[_fl] = _uv1[_fl].min(0) + (_uv1[_fl] - _uv1[_fl].min(0)) * _k
        ob_.data.uv_layers.active.data.foreach_set("uv", _uv1.ravel()); ob_.data.update()
        report["face_uv"] = {"linear_texel_density_vs_rest": {"v1_rule": round(math.sqrt(_dens0), 3), "v2": FACE_UV_SCALE}}
        bpy.ops.object.mode_set(mode="EDIT")
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
    _uvF, _ltF, _lsF, _uaF = uv_share()
    report.setdefault("face_uv", {}).update({"uv_share_pct": round(100 * float(_uaF[_facez].sum()), 3),
                                             "texels_per_mm_face": round(BAKE_RES[0] * math.sqrt(float(_uaF[_facez].sum() / _pa3[_facez].sum())) / 1000, 3),
                                             "texels_per_mm_rest": round(BAKE_RES[0] * math.sqrt(float(_uaF[~_facez & ~_hairf].sum() / _pa3[~_facez & ~_hairf].sum())) / 1000, 3)})
    print("FACE", json.dumps({"zone": report["face_zone"], "uv": report["face_uv"]}))
    report["uv_hair_strip"] = {"strip_u_from": round(1.0 - HAIR_UV_STRIP, 4), "hair_faces": int(_hairf.sum()),
                               "hair_uv_u_min": round(float(uvd_[_hairf[lpf_], 0].min()), 4),
                               "nonhair_uv_u_max": round(float(uvd_[~_hairf[lpf_], 0].max()), 4)}
# VARDEN v2: UV WINDING FIX -- smart-project can leave a near-degenerate sliver wound against the rest (the uv_health
# gate's 'flipped' = the minority sign of the signed UV areas; v2 measured 1 face); such a face's UVs are mirrored about
# their own centroid (sub-texel faces: no visible change), counted in the report
def _uv_fix_winding(me_):
    uvd_ = np.empty(len(me_.loops) * 2); me_.uv_layers.active.data.foreach_get("uv", uvd_); uvd_ = uvd_.reshape(-1, 2)
    lt_ = np.empty(len(me_.polygons), dtype=np.int64); me_.polygons.foreach_get("loop_total", lt_)
    ls_ = np.concatenate([[0], np.cumsum(lt_)[:-1]])
    sa_ = np.array([0.5 * float(np.dot(q_[:, 0], np.roll(q_[:, 1], -1)) - np.dot(q_[:, 1], np.roll(q_[:, 0], -1)))
                    for q_ in (uvd_[a_:a_ + n_] for a_, n_ in zip(ls_, lt_))])
    maj_ = 1.0 if (sa_ > 0).sum() >= (sa_ < 0).sum() else -1.0
    bad_ = np.nonzero(sa_ * maj_ < 0)[0]
    for f_ in bad_:
        q_ = uvd_[ls_[f_]:ls_[f_] + lt_[f_]]
        q_[:, 0] = 2.0 * q_[:, 0].mean() - q_[:, 0]
        uvd_[ls_[f_]:ls_[f_] + lt_[f_]] = q_
    me_.uv_layers.active.data.foreach_set("uv", uvd_.ravel()); me_.update()
    return int(len(bad_))


report["uv_winding_fixed"] = {o_.name: _uv_fix_winding(o_.data) for o_ in [low] + PROP_OBS}
print("UVWIND", json.dumps(report["uv_winding_fixed"]))
tris_main = tri_count_F(OBJ["main"]["F"]); tris_st = tri_count_F(OBJ["sword"]["F"]); tris_bk = tri_count_F(OBJ["scabbard"]["F"])
_groups = {}
for p in PARTS:
    g_ = p["name"].split(".")[0]
    _groups[g_] = _groups.get(g_, 0) + tri_count_F(p["F"])
_lk = [p for p in PARTS if p["name"].startswith("lock.")]
report["tris"] = {"total": tris_main + tris_st + tris_bk, "main": tris_main, "sword": tris_st, "scabbard": tris_bk, "body": tri_count_F(CF),
                  "scalp_locks": sum(tri_count_F(p["F"]) for p in _lk if p["name"].split(".")[1] not in BEARD_KINDS),
                  "beard_mustache_locks": sum(tri_count_F(p["F"]) for p in _lk if p["name"].split(".")[1] in BEARD_KINDS), **_groups}
report["tier_rationale"] = ("HERO role: window [%d, %d]. The MPFB body (face, hands) + scalp ribbon locks + beard / mustache "
                            "locks + cloak + FUR MANTLE (roll + tufts) + boots + the outfit pieces; sword + scabbard as own nodes." % tuple(TRI_BUDGET))
report["open_edges"] = {p["name"]: VP.open_edges(p["F"]) for p in PARTS if VP.open_edges(p["F"])}
report["open_edges_rule"] = "every part is a closed solid (listed here only if not); the body is the MPFB skin"
allV2 = np.vstack([OBJ["main"]["V"], OBJ["sword"]["V"], OBJ["scabbard"]["V"]])
lo_a, hi_a = allV2.min(0), allV2.max(0)
Hh = float(hi_a[2] - lo_a[2]); fp = float(max(hi_a[0] - lo_a[0], hi_a[1] - lo_a[1]))
k_fit = min(CELL_MAX_H / Hh, CELL_MAX_FP / fp)
report["measure"] = {"bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()], "height": round(Hh, 4),
                     "width_x": round(float(hi_a[0] - lo_a[0]), 4), "depth_y": round(float(hi_a[1] - lo_a[1]), 4),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(Hh * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4)}}
report["parts"] = {"boots": BOOT_INFO, "skirt": SKIRT_INFO, "stars": STAR_INFO, "vambraces": VAMB_INFO, "cloak": CLOAK_INFO,
                   "sword": SWORD_INFO, "locks": LOCK_INFO}
report["hair"] = HAIR_INFO
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
low["conquest_source"] = "from scratch on an MPFB2 base: design/reference/varden/varden_sheet.webp (review-log 2026-10-02)"
low["conquest_scale_policy"] = "natural proportions in metres; game scales at import (cell fit report-only)"
low["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
fko["conquest_toggle"] = "the sword is its own node + bone 'sword' (sheathed: child of 'scabbard'; the grip transform to hand_r is recorded)"
bko["conquest_toggle"] = "the scabbard is its own node + bone 'scabbard' (child of the pelvis): hide or swap it"


def box(pts, pad):
    pts = np.asarray(pts)
    return [(pts.min(0) - pad).tolist(), (pts.max(0) + pad).tolist()]


def part_pts(*names):
    return np.vstack([p["V"] for p in PARTS if p["name"].split(".")[0] in names or p["name"] in names]) - SHIFT


_crest_pts = part_pts("crest")
FOCUS = {"face": box([EYE["L"]["c"] - SHIFT, EYE["R"]["c"] - SHIFT, np.array([0, Y_LIP, Z_LIP - 0.03]) - SHIFT], 0.045),
         "portrait": box([np.array([-HR[0], Y_CHIN, HC[2] + HR[2] * 0.75]) - SHIFT, np.array([HR[0], Y_CHIN, Z_CHIN - 0.02]) - SHIFT], 0.02),
         "head": box([HC - SHIFT + np.array([0, 0, HR[2] + 0.03]), HC - SHIFT - np.array([HR[0] + 0.03, HR[1] + 0.02, 0.09]),
                      HC - SHIFT + np.array([HR[0] + 0.03, HR[1] + 0.03, 0])], 0.01),
         "cloak": box(part_pts("cloak"), 0.02),
         "fur": box(part_pts("furroll", "fur"), 0.02),
         "brooch": box(part_pts("brooch", "pendant"), 0.035),
         "vambrace": box(part_pts("vambrace"), 0.02),
         "boots": box(part_pts("bootfoot", "bootcuff", "sole"), 0.03),
         "belt": box(part_pts("buckle", "strapbuckle", "beltstud"), 0.03),
         "emblem": box(_crest_pts, 0.03),
         "beard": box(np.vstack([p["V"] for p in PARTS if p["name"].split(".")[0] == "lock" and p["name"].split(".")[1] in BEARD_KINDS]) - SHIFT, 0.015),
         "torso": box([SHO["L"] - SHIFT, SHO["R"] - SHIFT, np.array([0, 0, Z_BELT - 0.2]) - SHIFT], 0.05),
         "sword": box(np.vstack([OBJ["sword"]["V"][np.argsort(OBJ["sword"]["V"][:, 2])[-400:]]]), 0.03),
         "sword_full": box(np.vstack([OBJ["sword"]["V"], OBJ["scabbard"]["V"]]), 0.03),
         "eyes": box([EYE["L"]["c"] - SHIFT + np.array([0.022, 0, 0.018]), EYE["R"]["c"] - SHIFT - np.array([0.022, 0, 0.012])], 0.004),
         "brows": box([EYE["L"]["c"] - SHIFT + np.array([0.026, 0, 0.030]), EYE["R"]["c"] - SHIFT - np.array([0.026, 0, 0.006])], 0.004),
         "mouth": box([np.array([-0.026, Y_LIP, Z_SLIT - 0.016]) - SHIFT, np.array([0.026, Y_LIP + 0.012, Z_SLIT + 0.014]) - SHIFT], 0.002)}
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
_high_keep = np.ones(len(CF_all), bool)            # (v5: body faces the bake high keeps)
HIGH_MOUTH_INFO = None
if MOUTH_HIDDEN is not None and SEAM is not None:
    # v5: the bake high leaves out the mouth zone's HIDDEN sheets too -- every face behind the front skin (the rims, the
    # mouth interior down to MOUTH_HIDDEN[4] behind the lips) or facing backward. Subdivided with them, the fold at each rim
    # pulled the flat front sheet round into the seam and the baked normals there tilted away from the light; a bake ray
    # slipping through the sealed seam read the interior's normals (a jagged crease past the drawn line, rendered). Left
    # out, each rim is a boundary the subdivision keeps in place and a ray through the seam finds nothing (the texel falls
    # back to the facet normal): the flat skin bakes flat.
    _inm, _mbh, _mbk = mouth_hidden(CV_all, CF_all)
    _hid = _inm & (_mbk | (_mbh > MOUTH_HIDDEN[3]))
    _high_keep &= ~_hid
    HIGH_MOUTH_INFO = {"zone_m": MOUTH_HIDDEN[:3], "hidden_tol_m": MOUTH_HIDDEN[3], "depth_behind_lips_m": MOUTH_HIDDEN[4],
                       "faces_in_zone": int(_inm.sum()), "hidden_faces_left_out": int(_hid.sum())}
HIGH_EYE_INFO = None
if EYE_RIM_HIGH_OUT is not None:
    # v5: the bake high also leaves out the lower lids' steep RIM faces (the lid edge's drop to the flat under-eye skin).
    # Subdivided with them, the corner between the rim and the flat skin rounded over the first 1-3 mm under the liner and
    # the flat faces there baked 15-35 deg tilted normals (decoded off the map): a faint line under each lower lid. Left
    # out, the flat skin's edge is a boundary the subdivision keeps: it bakes flat right up to the liner.
    _fcr = np.array([CV_all[f].mean(0) for f in CF_all])
    _fnr = np.array([np.cross(CV_all[f[1]] - CV_all[f[0]], CV_all[f[2]] - CV_all[f[0]]) for f in CF_all])
    _fnr /= np.maximum(np.linalg.norm(_fnr, axis=1, keepdims=True), 1e-18)
    _rim = np.zeros(len(CF_all), bool)
    for s in "LR":
        _rad, _ang = eye_polar(_fcr, s)
        _db = _rad - aperture(_ang, s)
        _da = np.abs(((_ang - 270.0) + 180.0) % 360.0 - 180.0)
        _rim |= (_db > -EYE_RIM_HIGH_OUT[0] * 1e-3) & (_db < EYE_RIM_HIGH_OUT[1] * 1e-3) & (_da < EYE_RIM_HIGH_OUT[2]) & \
            (_fcr[:, 1] < EYE[s]["c"][1]) & (np.abs(_fnr[:, 1]) < math.cos(math.radians(EYE_RIM_HIGH_OUT[3])))
    _high_keep &= ~_rim
    HIGH_EYE_INFO = {"rule": "lower-lid faces from %.1f mm inside to %.1f mm outside the lid edge, within %.0f deg of straight "
                             "down, tilted more than %.0f deg from the view axis" % tuple(EYE_RIM_HIGH_OUT),
                     "rim_faces_left_out": int(_rim.sum())}
HIGHB = new_obj(UNIT + "_highbody", CV_all - SHIFT, [f for f, k_ in zip(CF_all, _high_keep) if k_])
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
fko.hide_render = True; bko.hide_render = True
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
if FACE_NORMAL_REF == "flat":
    # v2: the face zone bakes against its FLAT facets (tangent frames of the flat faces): the map carries the smooth high's
    # normal per facet, so the flat-shaded face renders smooth; every other face bakes against the smooth low (v1)
    _sm = np.ones(len(me.polygons), bool); _sm[_facez] = False
    me.polygons.foreach_set("use_smooth", _sm); me.update()
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
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
bstats.update({"cage_extrusion": BAKE_CAGE, "low_to_high_skin_m": {"p99": round(float(np.percentile(_shrink, 99)), 5),
                                                                   "max": round(float(_shrink.max()), 5)},
               "resolution": {"normal": RN, "ao": RA}, "high_tris": high_tris,
               "high_rule": "MPFB body at subdivision 1 (smooth) + every part", "high_mouth_hidden_out": HIGH_MOUTH_INFO, "high_lower_lid_rim_out": HIGH_EYE_INFO,
               "normal_baked_texels_pct": round(100 * float(cov_n.mean()), 2),
               "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n] > 0.05).mean()), 4),
               "ao_baked_texels_pct": round(100 * float(cov_a.mean()), 2),
               "ao_mean": round(float(pa[cov_a, 0].mean()), 4), "ao_p05": round(float(np.percentile(pa[cov_a, 0], 5)), 4)})
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), "varden_normal_%s.npy" % TAG), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), "varden_ao_%s.npy" % TAG), pa[:, :1])
_strip_n = (np.arange(RN * RN) % RN) >= int((1.0 - HAIR_UV_STRIP - 0.006) * RN)
_strip_a = (np.arange(RA * RA) % RA) >= int((1.0 - HAIR_UV_STRIP - 0.006) * RA)
px[_strip_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
# only the SKIN keeps the baked normal detail (the MPFB high's smooth face / hands); every garment / part face gets flat
# normal texels: the parts have no high detail (their high IS the low) and the body under the painted clothes carries the
# naked MPFB anatomy (pectorals, navel, knees) that the shirt / vest / trousers must not show
_skin_ids = [REG.index(r) for r in FACE_SKIN]
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
# ---- v3 SMOOTH-PROXY hair normal bake (the anime one-volume trick, done inside the flat-shaded contract like the v2 face):
# (1) the PROXY: a sphere grid about the hairdo's centre whose radius per direction is the hair's outer hull (rays from
#     outside against the hair faces), smoothed as an upper envelope (r <- max(hull, neighbour mean), HAIR_PROXY[2] passes)
#     and padded: a smooth egg enclosing the whole hairdo;
# (2) every hair vertex takes the proxy's normal at its nearest proxy point (barycentric on the proxy's vertex normals:
#     the data-transfer "nearest face interpolated" rule); a HIGH copy of the hair faces carries those as custom normals;
# (3) Cycles bakes the high's shading normal onto the low's FLAT hair facets (tangent frames of the flat faces, like the v2
#     face zone): the hair strip of the normal map now turns every flat facet toward the proxy's smooth normal, so the
#     hairdo lights as one soft volume while every face stays flat-shaded.
HAIR_BAKE = {"enabled": HAIR_PROXY is not None}
if HAIR_PROXY is not None:
    t_hb = time.time()
    _Vl = OBJ["main"]["V"]; _Fl = OBJ["main"]["F"]
    _hfi = np.nonzero(_hairf)[0]
    _hvi = np.unique(np.concatenate([np.array(_Fl[i]) for i in _hfi]))
    _hmap = -np.ones(len(_Vl), dtype=np.int64); _hmap[_hvi] = np.arange(len(_hvi))
    _HV = _Vl[_hvi]
    _HF = [[int(_hmap[i]) for i in _Fl[k]] for k in _hfi]
    # (Elias's) one proxy egg PER GROUP -- the scalp hairdo (cap + scalp locks) and the beard + mustache -- so the beard never
    # shades as the bottom of one giant egg hung from the crown. Group of a hair face = whether its part is a beard lock.
    _beardf = np.zeros(len(_Fl), bool)
    for n_, (fa_, fb_) in OBJ["main"]["FRANGE"].items():
        if (n_.startswith("lock.") and n_.split(".")[1] in BEARD_KINDS) or n_ == "beardcore":
            _beardf[fa_:fb_] = True
    _vgrp = np.zeros(len(_HV), int)
    for k_, f_ in zip(_hfi, _HF):
        if _beardf[k_]:
            _vgrp[f_] = 1
    _nlon, _nlat, _iters, _pad = HAIR_PROXY
    _lats = np.radians(np.linspace(-90.0, 90.0, _nlat + 2)[1:-1]); _lons = 2 * np.pi * np.arange(_nlon) / _nlon
    _dirs = [np.array([0.0, 0.0, -1.0])] + [np.array([math.cos(la) * math.sin(lo), -math.cos(la) * math.cos(lo), math.sin(la)])
                                            for la in _lats for lo in _lons] + [np.array([0.0, 0.0, 1.0])]
    _dirs = np.array(_dirs)
    _nd = len(_dirs)
    _vid = lambda j, i: 1 + j * _nlon + (i % _nlon)
    _nbr = [[] for _ in range(_nd)]
    for j in range(_nlat):
        for i in range(_nlon):
            k = _vid(j, i)
            _nbr[k] += [_vid(j, i - 1), _vid(j, i + 1)]
            _nbr[k].append(_vid(j - 1, i) if j > 0 else 0)
            _nbr[k].append(_vid(j + 1, i) if j < _nlat - 1 else _nd - 1)
    _nbr[0] = [_vid(0, i) for i in range(_nlon)]; _nbr[_nd - 1] = [_vid(_nlat - 1, i) for i in range(_nlon)]
    _PF = [[0, _vid(0, i + 1), _vid(0, i)] for i in range(_nlon)]
    for j in range(_nlat - 1):
        for i in range(_nlon):
            _PF.append([_vid(j, i), _vid(j, i + 1), _vid(j + 1, i + 1), _vid(j + 1, i)])
    _PF += [[_nd - 1, _vid(_nlat - 1, i), _vid(_nlat - 1, i + 1)] for i in range(_nlon)]
    _PT = [[f[0], f[k], f[k + 1]] for f in _PF for k in range(1, len(f) - 1)]

    def build_proxy(HVg, HFg):
        bvh_ho = BVHTree.FromPolygons(HVg.tolist(), HFg)
        cen_ = 0.5 * (HVg.min(0) + HVg.max(0))
        hull_ = np.full(_nd, np.nan)
        for i, d_ in enumerate(_dirs):
            h_ = bvh_ho.ray_cast(Vector(cen_ + d_ * 0.5), Vector(-d_), 0.5)
            if h_[0] is not None:
                hull_[i] = 0.5 - h_[3]
        vd_ = (HVg - cen_) / np.maximum(np.linalg.norm(HVg - cen_, axis=1), 1e-12)[:, None]
        for g_, r_ in zip(np.argmax(vd_ @ _dirs.T, axis=1), np.linalg.norm(HVg - cen_, axis=1)):
            hull_[g_] = r_ if np.isnan(hull_[g_]) else max(hull_[g_], r_)
        has_ = ~np.isnan(hull_)
        r_ = np.where(has_, hull_, np.nanmean(hull_))
        for _ in range(_iters):
            rs_ = np.array([r_[n_].mean() for n_ in _nbr])
            r_ = np.where(has_, np.maximum(hull_, rs_), rs_)
        PV_ = cen_ + _dirs * (r_ + _pad)[:, None]
        PN_ = VP.vertex_normals(PV_, _PF)
        if float(np.mean(np.einsum("ij,ij->i", PN_, PV_ - cen_))) < 0:
            PN_ = -PN_
        return {"cen": cen_, "PV": PV_, "PN": PN_, "bvh": BVHTree.FromPolygons(PV_.tolist(), _PT), "has": int(has_.sum()),
                "r": (float(r_.min()) + _pad, float(r_.max()) + _pad)}

    PROXIES = []
    for g_ in (0, 1):
        sel_ = np.nonzero(_vgrp == g_)[0]
        if len(sel_) == 0:
            PROXIES.append(None); continue
        fsel_ = [f_ for k_, f_ in zip(_hfi, _HF) if int(_beardf[k_]) == g_]
        mp_ = -np.ones(len(_HV), dtype=np.int64); mp_[sel_] = np.arange(len(sel_))
        PROXIES.append(build_proxy(_HV[sel_], [[int(mp_[i]) for i in f_] for f_ in fsel_]))
    _cen, _PV, _PN, _bvh_px = PROXIES[0]["cen"], PROXIES[0]["PV"], PROXIES[0]["PN"], PROXIES[0]["bvh"]   # (the scalp's: the
    #                                                                     enclosure proof below is quoted for the scalp)

    def proxy_normal(P_, grp=None):
        out_ = np.zeros((len(P_), 3))
        for i, p_ in enumerate(P_):
            if grp is not None:
                px_ = PROXIES[int(grp[i])]
            else:                                           # (a free query: the nearer proxy)
                px_ = min((x_ for x_ in PROXIES if x_ is not None), key=lambda x_: x_["bvh"].find_nearest(Vector(p_))[3])
            q_, _, ti_, _ = px_["bvh"].find_nearest(Vector(p_))
            a_, b_, c_ = (px_["PV"][j] for j in _PT[ti_])
            v0, v1, v2 = b_ - a_, c_ - a_, np.array(q_) - a_
            d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
            den_ = max(d00 * d11 - d01 * d01, 1e-20)
            bv_ = (d11 * d20 - d01 * d21) / den_; bw_ = (d00 * d21 - d01 * d20) / den_
            bb_ = np.clip([1.0 - bv_ - bw_, bv_, bw_], 0.0, 1.0); bb_ /= bb_.sum()
            out_[i] = unit(bb_[0] * px_["PN"][_PT[ti_][0]] + bb_[1] * px_["PN"][_PT[ti_][1]] + bb_[2] * px_["PN"][_PT[ti_][2]])
        return out_

    _HN = proxy_normal(_HV, _vgrp)
    if HAIR_LOCK_NORMAL_MIX:
        # (Wren v4) the shading normal leans HAIR_LOCK_NORMAL_MIX toward each lock's OWN smooth normal (per group centre)
        _own = VP.vertex_normals(_HV, _HF)
        for g_ in (0, 1):
            m_ = _vgrp == g_
            if m_.any() and float(np.mean(np.einsum("ij,ij->i", _own[m_], _HV[m_] - PROXIES[g_]["cen"]))) < 0:
                _own[m_] = -_own[m_]
        _HN = _HN * (1.0 - HAIR_LOCK_NORMAL_MIX) + _own * HAIR_LOCK_NORMAL_MIX
        _HN = _HN / np.maximum(np.linalg.norm(_HN, axis=1), 1e-12)[:, None]
    HAIRH = new_obj(UNIT + "_hairhigh", _HV, _HF)
    HAIRH.data.shade_smooth()
    HAIRH.data.normals_split_custom_set_from_vertices([tuple(n_) for n_ in _HN])
    HAIRH.data.update()
    img_hn = bpy.data.images.new(UNIT + "_hairnormal", RN, RN, alpha=False)
    img_hn.colorspace_settings.name = "Non-Color"; img_hn.generated_color = (0.0, 0.0, 0.0, 1.0)
    th_ = nt.nodes.new("ShaderNodeTexImage"); th_.image = img_hn; th_.location = (-900, -900)
    nt.nodes.active = th_
    me.shade_flat()                                     # the hair (every face) bakes against its FLAT facets
    for o in scene.objects:
        o.select_set(o in (HAIRH, low))
    bpy.context.view_layer.objects.active = low
    scene.cycles.samples = 1
    _rh = bpy.ops.object.bake(type="NORMAL", use_selected_to_active=True, cage_extrusion=HAIR_BAKE_CAGE[0],
                              max_ray_distance=HAIR_BAKE_CAGE[1], margin=16, use_clear=False)
    phn = np.empty(RN * RN * 4, dtype=np.float32); img_hn.pixels.foreach_get(phn); phn = phn.reshape(-1, 4)
    _covh = (phn[:, 2] > 0.25) & _strip_n
    DIG["bake_hair_normal"] = sha(np.clip(np.rint(phn[:, :3] * 255.0), 0, 255).astype(np.uint8))
    np.save(os.path.join(tempfile.gettempdir(), "varden_hairnormal_%s.npy" % TAG), phn[:, :3])
    px[_covh, :3] = phn[_covh, :3]
    img_n.pixels.foreach_set(px.ravel())
    # proof 1: the strip is no longer flat -- the tangent-space tilt of the covered hair texels
    _nt3 = 2.0 * phn[_covh, :3] - 1.0
    _tilt = np.degrees(np.arccos(np.clip(_nt3[:, 2] / np.maximum(np.linalg.norm(_nt3, axis=1), 1e-9), -1, 1)))
    # proof 2: decode the baked texel at every hair triangle's centroid with that flat triangle's own tangent frame
    # (T, B from the UV gradients, N = the facet normal; B = sign x N x T) and compare with the proxy normal there; and
    # the facet-to-proxy angle the map corrects (what the flat facets alone would show)
    me.calc_loop_triangles()
    _nq = len(me.loop_triangles)
    _ll = np.empty(_nq * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", _ll); _ll = _ll.reshape(-1, 3)
    _lp = np.empty(_nq, dtype=np.int64); me.loop_triangles.foreach_get("polygon_index", _lp)
    _lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", _lv)
    _uvh = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", _uvh); _uvh = _uvh.reshape(-1, 2)
    _dec, _fac, _adj = [], [], []
    for t_i in np.nonzero(_hairf[_lp])[0]:
        P3_ = _Vl[_lv[_ll[t_i]]]; U3_ = _uvh[_ll[t_i]]
        e1, e2 = P3_[1] - P3_[0], P3_[2] - P3_[0]; d1, d2 = U3_[1] - U3_[0], U3_[2] - U3_[0]
        Nf_ = np.cross(e1, e2)
        det_ = d1[0] * d2[1] - d2[0] * d1[1]
        if np.linalg.norm(Nf_) < 1e-12 or abs(det_) < 1e-14:
            continue
        Nf_ = unit(Nf_)
        Tt_ = (e1 * d2[1] - e2 * d1[1]) / det_; Bt_ = (e2 * d1[0] - e1 * d2[0]) / det_
        Tt_ = unit(Tt_ - Nf_ * float(Nf_ @ Tt_)); Bt_ = (1.0 if float(np.cross(Nf_, Tt_) @ Bt_) >= 0 else -1.0) * np.cross(Nf_, Tt_)
        uc_ = U3_.mean(0)
        ix_, iy_ = min(RN - 1, int(uc_[0] * RN)), min(RN - 1, int(uc_[1] * RN))
        if not _covh[iy_ * RN + ix_]:
            continue
        tn_ = 2.0 * px[iy_ * RN + ix_, :3] - 1.0
        nw_ = unit(Tt_ * tn_[0] + Bt_ * tn_[1] + Nf_ * tn_[2])
        np_ = proxy_normal(P3_.mean(0)[None], [int(_beardf[_lp[t_i]])])[0]
        _dec.append((math.degrees(math.acos(float(np.clip(nw_ @ np_, -1, 1)))), float(Nf_ @ np_)))
        _fac.append(math.degrees(math.acos(float(np.clip(Nf_ @ np_, -1, 1)))))
    # proof 3: across every edge shared by two hair faces, the facet normals' angle vs the proxy normals' angle at the two
    # face centroids (the dihedral a flat render shows vs the one the baked shading shows)
    _hfc = {k: _Vl[_Fl[k]].mean(0) for k in _hfi}
    _pnc = dict(zip(_hfi, proxy_normal(np.array([_hfc[k] for k in _hfi]), _beardf[_hfi].astype(int))))
    _fnn = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("normal", _fnn); _fnn = _fnn.reshape(-1, 3)
    _eh = {}
    for k in _hfi:
        f_ = _Fl[k]
        for j in range(len(f_)):
            _eh.setdefault((min(f_[j], f_[(j + 1) % len(f_)]), max(f_[j], f_[(j + 1) % len(f_)])), []).append(k)
    _dih_f, _dih_p = [], []
    for e_, ks_ in _eh.items():
        if len(ks_) == 2:
            a_, b_ = ks_
            _dih_f.append(math.degrees(math.acos(float(np.clip(_fnn[a_] @ _fnn[b_], -1, 1)))))
            _dih_p.append(math.degrees(math.acos(float(np.clip(_pnc[a_] @ _pnc[b_], -1, 1)))))
    nt.nodes.remove(th_)
    bpy.data.images.remove(img_hn)
    _hm = HAIRH.data
    bpy.data.objects.remove(HAIRH, do_unlink=True); bpy.data.meshes.remove(_hm)
    pct = lambda a_, q_: round(float(np.percentile(a_, q_)), 2) if len(a_) else None
    HAIR_BAKE.update({
        "result": sorted(_rh), "seconds": round(time.time() - t_hb, 1),
        "proxy": {"grid_lon_lat": [_nlon, _nlat], "smoothing_passes": _iters, "pad_m": _pad, "dirs": _nd,
                  "groups": {nm_: (None if px_ is None else {"centre": px_["cen"].round(4).tolist(), "hull_dirs_hit": px_["has"],
                                                             "radius_m_min_max": [round(px_["r"][0], 4), round(px_["r"][1], 4)]})
                             for nm_, px_ in zip(("scalp", "beard_mustache"), PROXIES)}},
        "cage_extrusion_m": HAIR_BAKE_CAGE[0], "max_ray_m": HAIR_BAKE_CAGE[1],
        "hair_verts": int(len(_HV)), "hair_faces": int(len(_HF)),
        "strip_texels_covered": int(_covh.sum()),
        "strip_texels_not_flat_pct(dev>0.05)": round(100.0 * float((np.linalg.norm(phn[_covh, :3] - np.array([0.5, 0.5, 1.0]), axis=1) > 0.05).mean()), 1),
        "tangent_tilt_deg_p10_p50_p90": [pct(_tilt, 10), pct(_tilt, 50), pct(_tilt, 90)],
        "decode_vs_proxy_deg_p50_p90_p99": {
            "representable(facet.proxy>0.2)": [pct([d_ for d_, c_ in _dec if c_ > 0.2], q_) for q_ in (50, 90, 99)],
            "all": [pct([d_ for d_, c_ in _dec], q_) for q_ in (50, 90, 99)],
            "note": "a tangent-space map can only turn a facet within its own hemisphere: underside facets (facing the "
                    "scalp, painted hair_shade) cannot take a proxy normal > 90 deg away"},
        "decode_samples": len(_dec), "representable_pct": round(100.0 * float(np.mean([c_ > 0.2 for _, c_ in _dec])), 1) if _dec else None,
        "facet_vs_proxy_deg_p50_p90": [pct(_fac, 50), pct(_fac, 90)],
        "neighbour_angle_deg_p50_p90": {"flat_facets": [pct(_dih_f, 50), pct(_dih_f, 90)], "baked_shading": [pct(_dih_p, 50), pct(_dih_p, 90)]},
        "v2": "the strip was flat (0.5, 0.5, 1): every facet lit by its own face normal"})
    # enclosure proof: a hair vertex is OUTSIDE the proxy when it lies farther from the centre than the proxy surface along
    # the same ray (the ray from outside toward the centre meets the proxy at radius 0.5 - hit distance)
    def _proxy_r(d_):
        h_ = _bvh_px.ray_cast(Vector(_cen + d_ * 0.5), Vector(-d_), 0.5)
        return 0.5 - h_[3] if h_[0] is not None else 0.0
    HAIR_BAKE["proxy"]["hair_verts_outside"] = int(sum(1 for p_ in _HV[_vgrp == 0] if float(np.linalg.norm(p_ - _cen)) > _proxy_r(unit(p_ - _cen)) + 1e-6))
    print("HAIRBAKE", json.dumps(HAIR_BAKE))
# ---- v6 SMOOTH MOUTH PROXY (review-log 2026-09-29 "Wren v6 mouth feedback": nose to chin reads as smooth uninterrupted
# skin, the drawn line the only feature). The mouth zone's normal texels are WRITTEN from a smooth proxy surface instead of
# taken from the ray bake: the subdivided high's FRONT heightfield y(x, z), sampled by front rays every MOUTH_PROXY_GRID m
# (valid: a front-facing hit within 2 mm of the local front -- not a ray through the seam's crack -- below the nose bottom
# - MOUTH_PROXY[5]), Gaussian-smoothed (normalised convolution over the valid samples); its normal (dS/dx, -1, dS/dz) at
# every texel's own surface point, encoded in that flat facet's tangent frame (T, B from the UV gradients, N = the facet
# normal, B = sign x N x T: the frame the hair proof decodes with), blended with the ray-baked normal by W (1 over the zone
# core, 0 past its MOUTH_PROXY[4] mm fade). The seam's rim sheets and crack, the bridge zone's edge kinks, the folded rims'
# slivers and the flat facets all shade as ONE smooth surface, with no ray to miss. Only the normal map changes: the mesh,
# the seam and the seal are untouched. (Tried and dropped, rendered: moving the high's own vertices to the smoothed
# heightfield, and a bake-only proxy sheet in front of the high -- both left the texels of the low's steep sliver faces
# baking wild normals (a bright blob and dark dots under the line) and the sheet's holes printed its outline.)
MOUTH_PROXY_INFO = None
if MOUTH_PROXY is not None and SEAM is not None:
    t_mp = time.time()
    _sg, _X0, _U0, _D0, _FD, _NC = [v_ * 1e-3 for v_ in MOUTH_PROXY]
    _GS = 0.0005
    _xs0 = float(SHIFT[0])                                  # build x = mesh x + SHIFT[0] (the low / high are centre-shifted)
    _zm = float(np.interp(0.0, SEAM["xs"], SEAM["zc"]))
    _pad = _FD + 3.0 * _sg
    _gx = np.arange(-(_X0 + _pad), _X0 + _pad + 1e-9, _GS)
    _gz = np.arange(_zm - (_D0 + _pad), _zm + _U0 + _pad + 1e-9, _GS)
    _H, _S, _val, _wsum, _ = front_heightfield_smooth(_hV + SHIFT, _hF, _gx, _gz, _sg, Z_NOSE_BOTTOM - _NC)   # (build frame)
    # v6.1: the lip forms ride ON the smoothed surface -- smooth the heightfield WITHOUT them (+ the analytic forward
    # field), then put them back analytically, so they shade as the soft volumes they are (not blurred away by sigma)
    _LF = lip_field(_gx[None, :], _gz[:, None])
    _Hb = np.where(np.isnan(_H), np.nan, _H + _LF)
    _k6 = np.exp(-0.5 * (np.arange(-int(3 * _sg / _GS), int(3 * _sg / _GS) + 1) * _GS / _sg) ** 2)
    _blur6 = lambda A: np.apply_along_axis(lambda c: np.convolve(c, _k6, mode="same"), 0,
                                           np.apply_along_axis(lambda r: np.convolve(r, _k6, mode="same"), 1, A))
    _S = _blur6(np.where(_val, _Hb, 0.0)) / np.maximum(_wsum, 1e-9) - _LF
    _Hn = np.where(np.isnan(_H), 9.0, _H)
    _dSz, _dSx = np.gradient(_S, _GS, _GS)

    def _grid_at(A, x_, z_):
        return grid_bilinear(A, _gx, _gz, x_, z_)

    def _wzone(x_, z_):
        dz_ = z_ - np.interp(x_, SEAM["xs"], SEAM["zc"])
        w_ = (1.0 - smoothstep(_X0, _X0 + _FD, np.abs(x_))) * (1.0 - smoothstep(_U0, _U0 + _FD, dz_)) * \
            (1.0 - smoothstep(_D0, _D0 + _FD, -dz_))
        return w_ * (_grid_at(_wsum, x_, z_) > 0.5 * float(_wsum.max())) * (z_ < Z_NOSE_BOTTOM - _NC)
    me.calc_loop_triangles()
    _nq6 = len(me.loop_triangles)
    _ll6 = np.empty(_nq6 * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", _ll6); _ll6 = _ll6.reshape(-1, 3)
    _lp6 = np.empty(_nq6, dtype=np.int64); me.loop_triangles.foreach_get("polygon_index", _lp6)
    _lv6 = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", _lv6)
    _uv6 = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", _uv6); _uv6 = _uv6.reshape(-1, 2)
    _pn6 = np.empty(len(me.polygons) * 3); me.polygons.foreach_get("normal", _pn6); _pn6 = _pn6.reshape(-1, 3)
    _Vl6 = OBJ["main"]["V"]
    _pc6 = _Vl6[_lv6[_ll6]].mean(1)                        # triangle centroids (mesh frame)
    _dzc6 = _pc6[:, 2] - np.interp(_pc6[:, 0] + _xs0, SEAM["xs"], SEAM["zc"])
    _cand6 = np.nonzero(_facez[_lp6] & (np.abs(_pc6[:, 0] + _xs0) < _X0 + _FD + 0.003) & (_dzc6 < _U0 + _FD + 0.003) &
                        (_dzc6 > -(_D0 + _FD + 0.003)))[0]
    # only the VISIBLE zone triangles (the first front-ray hit at the centroid) own texels: a hidden rim / fold packed in
    # the same UV island wrote the proxy in its own (backward) frame, and the visible faces too small to own a texel centre
    # decoded it from there (a bright blob under the line, rendered); their texels are filled by the margin pass instead
    _bl6 = BVHTree.FromPolygons(_Vl6.tolist(), OBJ["main"]["F"])
    _vis6 = []
    for t_ in _cand6:
        h_ = _bl6.ray_cast(Vector((float(_pc6[t_, 0]), -1.0, float(_pc6[t_, 2]))), Vector((0.0, 1.0, 0.0)), 2.0)
        if h_[0] is not None and (h_[2] == _lp6[t_] or float(_pc6[t_, 1]) - h_[0][1] < 5e-5) and _pn6[_lp6[t_], 1] < 0.0:
            _vis6.append(t_)
    _vis6 = np.array(_vis6, dtype=np.int64)
    # texels inside any OTHER triangle of the main object near the zone's UV patch are protected (another island's own)
    _done6 = np.zeros(RN * RN, bool)
    _prot6 = np.zeros(RN * RN, bool)
    _zuv = _uv6[_ll6[_cand6]].reshape(-1, 2)
    _u0, _u1 = _zuv.min(0) - 4.0 / RN, _zuv.max(0) + 4.0 / RN
    _tuv = _uv6[_ll6]
    _near6 = np.nonzero((_tuv[:, :, 0].max(1) >= _u0[0]) & (_tuv[:, :, 0].min(1) <= _u1[0]) &
                        (_tuv[:, :, 1].max(1) >= _u0[1]) & (_tuv[:, :, 1].min(1) <= _u1[1]))[0]
    _isc6 = np.zeros(_nq6, bool); _isc6[_cand6] = True
    for t_ in _near6[~_isc6[_near6]]:
        Q3 = _uv6[_ll6[t_]] * RN - 0.5
        x0, y0 = np.floor(Q3.min(0)).astype(int); x1, y1 = np.ceil(Q3.max(0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, RN - 1), min(y1, RN - 1)
        gx_, gy_ = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        a_, b_, c_ = Q3
        den_ = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
        if abs(den_) < 1e-12:
            continue
        w0 = ((b_[1] - c_[1]) * (gx_ - c_[0]) + (c_[0] - b_[0]) * (gy_ - c_[1])) / den_
        w1 = ((c_[1] - a_[1]) * (gx_ - c_[0]) + (a_[0] - c_[0]) * (gy_ - c_[1])) / den_
        _prot6[(gy_ * RN + gx_)[(w0 >= -1e-6) & (w1 >= -1e-6) & (1 - w0 - w1 >= -1e-6)]] = True
    _done6 |= _prot6
    _px6 = px[:, :3].copy()
    _core_dev, _nt6 = [], 0
    for _pass in (0, 1):                                    # 0: the texels inside each triangle; 1: its bake margin (2 px)
        for t_ in _vis6:
            P3_ = _Vl6[_lv6[_ll6[t_]]]; U3_ = _uv6[_ll6[t_]]
            e1, e2 = P3_[1] - P3_[0], P3_[2] - P3_[0]; d1, d2 = U3_[1] - U3_[0], U3_[2] - U3_[0]
            det_ = d1[0] * d2[1] - d2[0] * d1[1]
            Nf_ = _pn6[_lp6[t_]]
            if abs(det_) < 1e-14 or np.linalg.norm(Nf_) < 0.5:
                continue
            Tt_ = (e1 * d2[1] - e2 * d1[1]) / det_; Bt_ = (e2 * d1[0] - e1 * d2[0]) / det_
            Tt_ = unit(Tt_ - Nf_ * float(Nf_ @ Tt_)); Bt_ = (1.0 if float(np.cross(Nf_, Tt_) @ Bt_) >= 0 else -1.0) * np.cross(Nf_, Tt_)
            Q3 = U3_ * RN - 0.5
            x0, y0 = np.floor(Q3.min(0)).astype(int) - 2; x1, y1 = np.ceil(Q3.max(0)).astype(int) + 2
            x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, RN - 1), min(y1, RN - 1)
            gx_, gy_ = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
            a_, b_, c_ = Q3
            den_ = (b_[1] - c_[1]) * (a_[0] - c_[0]) + (c_[0] - b_[0]) * (a_[1] - c_[1])
            if abs(den_) < 1e-12:
                continue
            w0 = ((b_[1] - c_[1]) * (gx_ - c_[0]) + (c_[0] - b_[0]) * (gy_ - c_[1])) / den_
            w1 = ((c_[1] - a_[1]) * (gx_ - c_[0]) + (a_[0] - c_[0]) * (gy_ - c_[1])) / den_
            w2 = 1.0 - w0 - w1
            if _pass == 0:
                sel_ = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            else:                                           # within 2 px of the triangle (its altitudes in texels)
                ed_ = [np.linalg.norm(Q3[(k + 1) % 3] - Q3[(k + 2) % 3]) for k in range(3)]
                tol_ = [2.0 * ed_[k] / max(abs(den_), 1e-9) for k in range(3)]   # 2 px as a barycentric margin per vertex
                sel_ = (w0 >= -tol_[0]) & (w1 >= -tol_[1]) & (w2 >= -tol_[2])
            ti_ = (gy_ * RN + gx_)[sel_]
            keep_ = ~_done6[ti_]
            if not keep_.any():
                continue
            ti_ = ti_[keep_]
            W3 = np.stack([w0[sel_][keep_], w1[sel_][keep_], w2[sel_][keep_]], 1)
            Pw_ = W3 @ P3_                                   # the texel's surface point (mesh frame)
            xb_, zb_ = Pw_[:, 0] + _xs0, Pw_[:, 2]
            wz_ = _wzone(xb_, zb_)
            npx_ = np.stack([_grid_at(_dSx, xb_, zb_), -np.ones(len(xb_)), _grid_at(_dSz, xb_, zb_)], 1)
            npx_ /= np.linalg.norm(npx_, axis=1, keepdims=True)
            tb_ = 2.0 * px[ti_, :3] - 1.0
            nb_ = tb_[:, :1] * Tt_ + tb_[:, 1:2] * Bt_ + tb_[:, 2:3] * Nf_
            nb_ /= np.maximum(np.linalg.norm(nb_, axis=1, keepdims=True), 1e-9)
            nw_ = wz_[:, None] * npx_ + (1.0 - wz_[:, None]) * nb_
            nw_ /= np.maximum(np.linalg.norm(nw_, axis=1, keepdims=True), 1e-9)
            _px6[ti_] = 0.5 + 0.5 * np.stack([nw_ @ Tt_, nw_ @ Bt_, nw_ @ Nf_], 1)
            _done6[ti_] = True
            if _pass == 0:
                _nt6 += 1
                cf_ = wz_ > 0.999
                if cf_.any():                                # proof: the facet-to-proxy angle the map corrects here
                    _core_dev += np.degrees(np.arccos(np.clip(npx_[cf_] @ Nf_, -1, 1))).tolist()
    _done6 &= ~_prot6
    _chg6 = _done6 & (np.abs(_px6 - px[:, :3]).max(1) > 0)
    px[:, :3] = np.clip(_px6, 0.0, 1.0)
    img_n.pixels.foreach_set(px.ravel())
    # the proof on the written map: decode every zone triangle's centroid texel with its own frame vs the proxy normal
    _dec6 = []
    for t_ in _vis6:
        P3_ = _Vl6[_lv6[_ll6[t_]]]; U3_ = _uv6[_ll6[t_]]; Nf_ = _pn6[_lp6[t_]]
        e1, e2 = P3_[1] - P3_[0], P3_[2] - P3_[0]; d1, d2 = U3_[1] - U3_[0], U3_[2] - U3_[0]
        det_ = d1[0] * d2[1] - d2[0] * d1[1]
        if abs(det_) < 1e-14:
            continue
        Tt_ = (e1 * d2[1] - e2 * d1[1]) / det_; Bt_ = (e2 * d1[0] - e1 * d2[0]) / det_
        Tt_ = unit(Tt_ - Nf_ * float(Nf_ @ Tt_)); Bt_ = (1.0 if float(np.cross(Nf_, Tt_) @ Bt_) >= 0 else -1.0) * np.cross(Nf_, Tt_)
        uc_ = U3_.mean(0); pc_ = P3_.mean(0)
        if _wzone(np.array([pc_[0] + _xs0]), np.array([pc_[2]]))[0] < 0.999:
            continue
        tn_ = 2.0 * px[min(RN - 1, int(uc_[1] * RN)) * RN + min(RN - 1, int(uc_[0] * RN)), :3] - 1.0
        nd_ = unit(Tt_ * tn_[0] + Bt_ * tn_[1] + Nf_ * tn_[2])
        npc_ = unit(np.array([float(_grid_at(_dSx, np.array([pc_[0] + _xs0]), np.array([pc_[2]]))[0]), -1.0,
                              float(_grid_at(_dSz, np.array([pc_[0] + _xs0]), np.array([pc_[2]]))[0])]))
        _dec6.append(math.degrees(math.acos(float(np.clip(nd_ @ npc_, -1, 1)))))
    _cz6 = _wzone(np.tile(_gx, len(_gz)), np.repeat(_gz, len(_gx))).reshape(len(_gz), len(_gx)) > 0.999
    DIG["mouth_proxy_core"] = sha(np.clip(np.rint(np.where(_cz6, _S, 0.0) * 1e6), -2 ** 31, 2 ** 31 - 1).astype(np.int64))
    MOUTH_PROXY_INFO = {"sigma_mm": MOUTH_PROXY[0], "zone_mm": {"half_width": MOUTH_PROXY[1], "above_seam": MOUTH_PROXY[2],
                                                                 "below_seam": MOUTH_PROXY[3], "fade": MOUTH_PROXY[4]},
                        "nose_clear_mm": MOUTH_PROXY[5], "grid": [len(_gz), len(_gx)], "grid_mm": _GS * 1000,
                        "valid_samples": int(_val.sum()),
                        "invalid_in_core(seam crack / deep hits)": int((_cz6 & ~_val).sum()),
                        "smooth_vs_high_mm_in_core": {"max": round(1000 * float(np.abs(_S - _Hn)[_cz6 & _val].max()), 3),
                                                      "p99": round(1000 * float(np.percentile(np.abs(_S - _Hn)[_cz6 & _val], 99)), 3)},
                        "zone_triangles": {"candidates": int(len(_cand6)), "visible": int(len(_vis6)), "with_texel_centres": int(_nt6)},
                        "protected_texels(other islands)": int(_prot6.sum()),
                        "texels_written": int(_done6.sum()), "texels_changed": int(_chg6.sum()),
                        "facet_vs_proxy_deg_core_p50_p90_max": [round(float(np.percentile(_core_dev, q_)), 2) for q_ in (50, 90)] +
                                                                [round(float(np.max(_core_dev)), 2)] if _core_dev else None,
                        "decode_vs_proxy_deg_core_p50_p99_max": [round(float(np.percentile(_dec6, q_)), 3) for q_ in (50, 99)] +
                                                                 [round(float(np.max(_dec6)), 3)] if _dec6 else None,
                        "seconds": round(time.time() - t_mp, 1)}
    print("MOUTHPROXY", json.dumps(MOUTH_PROXY_INFO))
bstats["mouth_proxy_v6"] = MOUTH_PROXY_INFO
bstats["hair_proxy_bake"] = HAIR_BAKE
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
AO_LIFT_INFO = None
if AO_FACE_FLOOR is not None:
    # v5 "AO fully floored" (review-log 2026-09-29 "Wren v5 feedback + FE reference set"): the flat under-eye skin and the
    # flat mouth zone carry NO baked occlusion tone (the FE face: flat skin + the drawn lines). Per skin face a lift weight
    # (1 inside, feathered to 0 over AO_FACE_FLOOR[1] mm at the zone border) -> floor = skin floor .. AO_FACE_FLOOR[0].
    _fcb = np.array([CV[f].mean(0) for f in CF])
    _fl = AO_FACE_FLOOR[1] * 1e-3
    _we = np.zeros(len(CF))
    _e0, _e1, _ea = AO_FACE_FLOOR[2]
    for s in "LR":
        _rad, _ang = eye_polar(_fcb, s)
        _db = _rad - aperture(_ang, s)                       # m below / outside the lid edge along the polar ray
        _da = np.abs(((_ang - 270.0) + 180.0) % 360.0 - 180.0)
        _degf = math.degrees(_fl / max(float(aperture(270.0, s)) + 0.008, 1e-3))   # the feather as an angle at ~8 mm under the lid
        _w = smoothstep(_e0 * 1e-3 - 1e-9, _e0 * 1e-3 + 1e-9, _db) * (1.0 - smoothstep(_e1 * 1e-3 - _fl, _e1 * 1e-3, _db)) * \
            (1.0 - smoothstep(_ea - _degf, _ea, _da)) * (_fcb[:, 1] < EYE[s]["c"][1])
        _we = np.maximum(_we, _w)
    _wm = np.zeros(len(CF))
    if SEAM is not None:
        _X, _U, _D = AO_FACE_FLOOR[3]
        _dzf = _fcb[:, 2] - np.interp(_fcb[:, 0], SEAM["xs"], SEAM["zc"])
        _wm = (1.0 - smoothstep(_X - _fl, _X, np.abs(_fcb[:, 0]))) * (1.0 - smoothstep(_U - _fl, _U, _dzf)) * \
            (1.0 - smoothstep(_D - _fl, _D, -_dzf)) * (_fcb[:, 1] < Y_LIP + 0.012)
    _skinf = np.isin(np.array(reg), ["skin", "skin_shadow", "lips"])
    _wf = np.where(_skinf, np.maximum(_we, _wm), 0.0)
    _flf = AO_FLOOR["skin"] + (AO_FACE_FLOOR[0] - AO_FLOOR["skin"]) * _wf
    _lf_tris = np.nonzero((_lt_p < len(CF)) & np.concatenate([_wf > 0, np.zeros(len(_ridf) - len(CF), bool)])[_lt_p])[0]
    for tri_ in _lf_tris:
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
        _flr[gy_[inside_], gx_[inside_]] = np.maximum(_flr[gy_[inside_], gx_[inside_]], _flf[_lt_p[tri_]])
    AO_LIFT_INFO = {"floor": AO_FACE_FLOOR[0], "feather_mm": AO_FACE_FLOOR[1], "undereye_faces": int((_skinf & (_we > 0)).sum()),
                    "undereye_faces_full": int((_skinf & (_we > 0.999)).sum()), "mouth_faces": int((_skinf & (_wm > 0)).sum()),
                    "mouth_faces_full": int((_skinf & (_wm > 0.999)).sum())}
elif AO_FACE_LIFT is not None:
    # v4: skin faces in the mouth band + under the eyes get the AO_FACE_LIFT floor (see the constant)
    _fcb = np.array([CV[f].mean(0) for f in CF])
    _lift = np.zeros(len(CF), bool)
    if SEAM is not None:
        _lift |= (np.abs(_fcb[:, 2] - np.interp(_fcb[:, 0], SEAM["xs"], SEAM["zc"])) < AO_FACE_LIFT[1]) & \
            (np.abs(_fcb[:, 0]) < float(np.abs(SEAM["xs"]).max()) + 0.003) & (_fcb[:, 1] < Y_LIP + 0.012)
    _n_mouth = int(_lift.sum())
    for s in "LR":
        _rad, _ang = eye_polar(_fcb, s)
        _apd = float(aperture(270.0, s))
        _lift |= (_rad > _apd + AO_FACE_LIFT[2] * 1e-3) & (_rad < _apd + AO_FACE_LIFT[3] * 1e-3) & \
            (np.abs(_ang - 270.0) < EYE_BAG_FLAT[3]) & (_fcb[:, 1] < EYE[s]["c"][1])
    _lift &= np.isin(np.array(reg), ["skin", "skin_shadow", "lips"])
    _lf_tris = np.nonzero((_lt_p < len(CF)) & np.concatenate([_lift, np.zeros(len(_ridf) - len(CF), bool)])[_lt_p])[0]
    for tri_ in _lf_tris:
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
        _flr[gy_[inside_], gx_[inside_]] = np.maximum(_flr[gy_[inside_], gx_[inside_]], AO_FACE_LIFT[0])
    AO_LIFT_INFO = {"floor": AO_FACE_LIFT[0], "mouth_band_faces": _n_mouth, "faces": int(_lift.sum())}
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
bstats["ao_lift"] = {"floors": AO_FLOOR, "v4_face_lift": AO_LIFT_INFO, "raw_p05": round(float(np.percentile(_ao_raw[cov_a], 5)), 4),
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
fko.hide_render = False; bko.hide_render = False
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


DIG["geometry_colour_uv"] = geometry_digest([low] + PROP_OBS)
report["digest_geometry_colour_uv"] = DIG["geometry_colour_uv"]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "the sheet's six palette chips + direct samples off the figures / detail panels"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))
