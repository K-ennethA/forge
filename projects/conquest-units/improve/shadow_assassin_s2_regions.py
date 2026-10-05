# Shadow Assassin build section 2 (the iso-cut machinery of elias_s2_regions.py, no face work): painted regions on the body
# -- the TUNIC on the torso (collar at the neck base, down to the belt line), SLEEVES on the arms, GLOVES from just above
# the wrists, TROUSERS below the belt line, the boot tops (the skin inside the boot shafts is harvested in s6), and the
# FACE WRAP tone on the whole head + neck (S5: the face is never seen; the wrap shell (s3) and the hood (s4) cover it).

Z_BELT = Z_WAIST + BELT["dz"]
FIELDS = {}
X, Y, Z = BV[:, 0], BV[:, 1], BV[:, 2]
SHIN = {}
GLOVE_UP = 0.022                      # the glove cuff ends this far up the forearm from the wrist joint (m; under the bracer)
for s, sg in (("L", 1.0), ("R", -1.0)):
    fa = unit(WRI[s] - ELB[s])
    FIELDS["glove_" + s] = (BV - WRI[s]) @ fa + GLOVE_UP                    # > 0 = the glove
    FIELDS["legm_" + s] = LEG_B[s].astype(float)
    FIELDS["armm_" + s] = ARM_B[s].astype(float)
    sa = ANKLE[s] - KNEE[s]
    Ls = float(np.linalg.norm(sa)); ua = sa / Ls
    fr = unit(np.array([0.0, -1.0, 0.0]) - ua * float(ua @ np.array([0.0, -1.0, 0.0])))
    lat = np.cross(ua, fr)
    lat = lat if lat[0] > 0 else -lat
    tt = ((BV - KNEE[s]) @ ua) / Ls
    SHIN[s] = {"ua": ua, "fr": fr, "lat": lat, "len": Ls}
    FIELDS["boottop_" + s] = tt - BOOT_TOP
FIELDS["collar"] = Z - (NECK0[2] - 0.012 + 0.25 * np.maximum(Y - NECK0[1], 0.0))   # > 0 = the neck / head (wrap tone)
FIELDS["headm"] = (HEAD_B | dom_in(["neck_01"])).astype(float)
FIELDS["belt"] = Z - Z_BELT
FIELDS["z"] = Z.copy()

bm = bmesh.new()
for p in BV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in BF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None, snap=CUT_SNAP):
    """(Elias / Wren s2) split edges + connect faces along field key = tau (gate(a, b): which edges may be cut)."""
    L = LAY[key]
    eps = 1e-6 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < snap:
                a[L] = tau
            elif tt > 1 - snap:
                b[L] = tau
    cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
    pairs = []
    for f in bm.faces:
        vs = list(f.verts)
        sides = [side(v) for v in vs]
        if not (1 in sides and -1 in sides):
            continue
        cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
        if len(cv) == 2:
            pairs.append(cv)
    for cv in pairs:
        try:
            bmesh.ops.connect_verts(bm, verts=cv)
        except Exception:
            pass
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": len(cuts), "face_connects": len(pairs)}


def g_and(*conds):
    return lambda a, b: all(c(a) and c(b) for c in conds)


def gv(key, lo=-1e9, hi=1e9):
    L = LAY[key]
    return lambda v: lo <= v[L] <= hi


CUTS = []
for s in "LR":
    CUTS.append(("glove_" + s, 0.0, g_and(gv("armm_" + s, 0.5))))
    CUTS.append(("boottop_" + s, 0.0, g_and(gv("legm_" + s, 0.5))))
CUTS.append(("collar", 0.0, g_and(gv("z", NECK0[2] - 0.10, HEADJ[2] + 0.04), gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5))))
CUTS.append(("belt", 0.0, g_and(gv("armm_L", -1, 0.5), gv("armm_R", -1, 0.5), gv("headm", -1, 0.5))))
t_ = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
_ndeg = len(bm.faces)
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
for _ in range(3):
    _sl = {}
    for f in bm.faces:
        if f.calc_area() < SLIVER_AREA:
            e_ = min(f.edges, key=lambda e: e.calc_length())
            _sl[e_.index if e_.is_valid else id(e_)] = e_
    bm.edges.index_update()
    if not _sl:
        break
    _seen, _es = set(), []
    for e_ in _sl.values():
        if e_.is_valid and not (set(e_.verts) & _seen):
            _es.append(e_); _seen |= set(e_.verts)
    bmesh.ops.collapse(bm, edges=_es, uvs=False)
_ndeg -= len(bm.faces)
bm.verts.index_update(); bm.faces.index_update()
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FV = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
bm.free()
nF = len(CF)
reg = np.array(["tunic"] * nF, dtype=object)
_armL, _armR = FV["armm_L"] > 0.5, FV["armm_R"] > 0.5
_arm = _armL | _armR
_legL, _legR = FV["legm_L"] > 0.5, FV["legm_R"] > 0.5
_leg = _legL | _legR
_head = FV["headm"] > 0.5
_torso = ~_head & ~_arm & ~_leg
reg[_arm] = "sleeve"
reg[(_armL & (FV["glove_L"] > 0)) | (_armR & (FV["glove_R"] > 0))] = "glove"
reg[(_torso & (FV["belt"] < 0)) | _leg] = "trousers"
for s, lm_ in (("L", _legL), ("R", _legR)):
    reg[lm_ & (FV["boottop_" + s] > 0)] = "boot"          # under the solid boot (harvested in s6)
reg[(_head | (~_arm & (FV["collar"] > 0)))] = "facewrap"
report["iso_cuts"] = {"cuts": len(cut_log), "edge_splits": int(sum(c["edge_splits"] for c in cut_log)),
                      "degenerate_faces_dissolved": int(_ndeg), "seconds": round(time.time() - t_, 1), "body_tris_after_cuts": nF}
print("CUTS", json.dumps(report["iso_cuts"]))
CW = transfer(CV)
BVH_CUT = BVHTree.FromPolygons(CV.tolist(), CF)
report["regions_body_faces"] = {r: int((reg == r).sum()) for r in sorted(set(reg))}
print("BODYREG", json.dumps(report["regions_body_faces"]))
