"""Shared game-ready glb export for a rigged Conquest unit (import wave, 2026-10-02). One call per unit:

    blender --background --factory-startup --python-exit-code 1 --python improve/export_glb.py -- <unit> \
            [--blend <path>] [--out <path>] [--check-only]     (--check-only: gate an existing glb, no export)
    python improve/export_glb.py --audit rigged/*.glb        (glb JSON-chunk audit only; any Python 3, no bpy)

Opens the unit's rigged .blend (never saves it), selects the armature + every renderable mesh it deforms, binds the
clips the way the self-exporting builds do (rig <- 'idle'; shape keys <- one NLA strip per clip carrying the
action's KEY slot, the supaoctto pattern, so each clip keeps its morph weights) and writes rigged/<unit>.glb.

Export flags = the self-exporting builds' call (duskmaw/firefly/vampwarrior/... _build.py: GLB, selection, +Y up,
no modifier apply, ACTIONS = one glTF animation per clip, skins, all bones) PLUS the colour-set flags named in
survey/color1_probe.md, set explicitly: export_vertex_color='ACTIVE' + export_all_vertex_colors=True -> COLOR_0 = the
active set ('Col', palette albedo), COLOR_1 = 'Glow' (the glow mask the Conquest CUSTOM0 extension reads).
(export_all_vertex_colors already defaults True in Blender 5.0, which is why the builds' calls carry COLOR_1.)

GLOW CARRIER = '_GLOW' (decision 2026-10-02): a float VEC3 copy of Glow.rgb added in memory before export and
written by export_attributes=True without any clamp. Every unit gets it (one read path in the Godot extension).

Gate: every primitive carries COLOR_0, COLOR_1 and _GLOW, and the glb's values are the blend's ('Col' -> COLOR_0,
'Glow' -> COLOR_1 and _GLOW): every distinct value in each glb set lies within COLOUR_TOL of a distinct source value,
and every source value on a non-sliver triangle is in the glb (catches a swapped / missing / re-ordered set).
_GLOW must be EXACT. COLOR_1 stays for compat but is normalized u16 CLIPPED to [0, 1] by the exporter, so glow > 1
is lost there - reported as color1_compat_hdr_values_clamped, not failed. ALPHA_DROPPED = a colour set written VEC3
(material reads no vertex alpha) while the source carries alpha < 1. Exit: 0 = EXACT, 2 = ALPHA_DROPPED only, 1 = FAIL.
Prints one GLB_EXPORT {json}.
Emission-strength keys (geode/mycothrall idle pulse) are NOT exported in ACTIONS mode (rigged/geode_gltf_probe.json):
godot-import-notes item 2 fallback = shader-side pulse.
"""
import json
import os
import struct
import sys

# --- tunables ---------------------------------------------------------------------------------------------------
COLOUR_TOL = 2e-3          # distinct-value match tolerance: the exporter writes normalized u16 (step 1.5e-5)
SLIVER_M2 = 1e-6           # a source colour found ONLY on tris below 1 mm2 may be absent from the glb (exporter drops slivers)
GLOW_SET, ALBEDO_SET = "Glow", "Col"
GLOW_ATTR = "_GLOW"        # float copy of Glow.rgb, exported unclamped (decision 2026-10-02: glow ships as _GLOW)
# unit -> rigged source blend (project-relative) when it is not rigged/<unit>.blend
SOURCES = {
    "eldroot": "rigged/eldroot_standing4.blend",   # standing v4 = current (review-log 2026-09-25 "Eldroot v4 delivered")
}   # duskmaw: rigged/duskmaw.blend IS v4 (duskmaw_build.py docstring); duskmaw_v3.* are kept "before" outputs
EXPORT_KW = dict(export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                 export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                 export_skins=True, export_def_bones=False, export_morph=True, export_morph_animation=True,
                 export_vertex_color="ACTIVE", export_all_vertex_colors=True,
                 export_active_vertex_color_when_no_material=True,
                 export_attributes=True)      # ships the float '_GLOW' attribute (see GLOW_ATTR)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# --- glb reading (no bpy) ---------------------------------------------------------------------------------------
def read_glb(path):
    d = open(path, "rb").read()
    jl = struct.unpack_from("<I", d, 12)[0]
    return d, json.loads(d[20:20 + jl]), 20 + jl + 8


def accessor_values(d, js, bin_off, ai):
    a = js["accessors"][ai]
    bv = js["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    f = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[a["componentType"]]
    sz = struct.calcsize(f)
    stride = bv.get("byteStride", n * sz)
    off = bin_off + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    sc = {"H": 65535.0, "B": 255.0}.get(f, 1.0) if a.get("normalized") else 1.0
    return [tuple(x / sc for x in struct.unpack_from("<" + f * n, d, off + i * stride)) for i in range(a["count"])]


def audit(path):
    d, js, _ = read_glb(path)
    prims, tris = [], 0
    for m in js.get("meshes", []):
        for p in m["primitives"]:
            t = js["accessors"][p["indices"]]["count"] // 3 if "indices" in p else 0
            tris += t
            prims.append({"mesh": m.get("name"), "tris": t, "attributes": sorted(p["attributes"]),
                          "morph_targets": len(p.get("targets", []))})
    return {"glb": os.path.basename(path), "bytes": len(d), "tris": tris, "primitives": prims,
            "COLOR_0": all("COLOR_0" in p["attributes"] for p in prims),
            "COLOR_1": all("COLOR_1" in p["attributes"] for p in prims),
            "_GLOW": all("_GLOW" in p["attributes"] for p in prims),
            "animations": [a.get("name") for a in js.get("animations", [])],
            "skins": len(js.get("skins", [])), "joints": [len(s["joints"]) for s in js.get("skins", [])],
            "extensionsUsed": js.get("extensionsUsed", [])}


if __name__ == "__main__" and "--audit" in sys.argv:
    import glob
    for pat in sys.argv[sys.argv.index("--audit") + 1:]:
        for p in sorted(glob.glob(pat)):
            print("GLB_AUDIT " + json.dumps(audit(p)))
    sys.exit(0)

# --- export (bpy) -----------------------------------------------------------------------------------------------
import bpy  # noqa: E402
import numpy as np  # noqa: E402


def distinct(rows):
    """distinct RGBA rows; a VEC3 colour set (the exporter drops a constant-1 alpha) is compared as alpha 1."""
    a = np.asarray(rows, dtype=np.float64)
    if a.ndim == 1:          # flat foreach_get buffer
        a = a.reshape(-1, 4)
    if a.shape[1] == 3:
        a = np.hstack([a, np.ones((len(a), 1))])
    return np.unique(np.round(a, 4), axis=0)


def covered(a, b):
    """count of rows of a with no row of b within COLOUR_TOL (max-abs)."""
    if len(b) == 0:
        return len(a)
    return int(sum(1 for r in a if np.abs(b - r).max(axis=1).min() > COLOUR_TOL))


def deformed_by(o, rig):
    p = o.parent
    while p is not None:
        if p is rig:
            return True
        p = p.parent
    return any(m.type == "ARMATURE" and m.object is rig for m in o.modifiers)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    unit = argv[0]
    blend = os.path.join(ROOT, SOURCES.get(unit, "rigged/%s.blend" % unit))
    out = os.path.join(ROOT, "rigged", unit + ".glb")
    if "--blend" in argv:
        blend = os.path.abspath(argv[argv.index("--blend") + 1])
    if "--out" in argv:
        out = os.path.abspath(argv[argv.index("--out") + 1])
    check_only = "--check-only" in argv
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    rigs = [o for o in scene.objects if o.type == "ARMATURE"]
    assert len(rigs) == 1, "expected one armature, got %s" % [o.name for o in rigs]
    rig = rigs[0]
    meshes = [o for o in scene.objects if o.type == "MESH" and not o.hide_render and deformed_by(o, rig)]
    assert meshes, "no mesh deformed by " + rig.name
    for o in scene.objects:
        o.select_set(o is rig or o in meshes)
    bpy.context.view_layer.objects.active = rig

    sys.path.insert(0, HERE)
    import rigkit as K
    acts = {a.name: a for a in bpy.data.actions}
    K.assign_action(rig, acts.get("idle") or next(iter(acts.values())))
    for o in meshes:   # morph weights per clip: one NLA strip per action carrying its KEY slot
        key = o.data.shape_keys
        if key is None:
            continue
        kad = key.animation_data or key.animation_data_create()
        kad.action = None
        for a in acts.values():
            ks = next((s for s in a.slots if s.target_id_type == "KEY"), None)
            if ks is not None:
                tr = kad.nla_tracks.new(); tr.name = a.name
                st = tr.strips.new(a.name, int(a.frame_range[0]), a)
                st.action_slot = ks
    # _GLOW: a FLOAT_VECTOR copy of Glow.rgb on Glow's domain, added in memory only (the blend is never saved).
    # export_attributes writes '_'-prefixed generic attributes as float accessors -- no [0, 1] clip, unlike the
    # u16n COLOR_1 (measured: geode _GLOW max G 1.7, 7/7 distinct values). Glow's alpha is always 1 (palettes.paint).
    underscore = {}
    for o in meshes:
        me = o.data
        gl = me.color_attributes.get(GLOW_SET)
        if not check_only and gl is not None and GLOW_ATTR not in me.attributes:
            raw = np.empty(len(gl.data) * 4); gl.data.foreach_get("color", raw)
            ga = me.attributes.new(GLOW_ATTR, "FLOAT_VECTOR", gl.domain)
            ga.data.foreach_set("vector", raw.reshape(-1, 4)[:, :3].ravel())
        underscore[o.name] = sorted(a.name for a in me.attributes if a.name.startswith("_"))
    missing = []
    if check_only:
        assert os.path.exists(out), out
    else:
        props = {p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}
        kw = {k: v for k, v in EXPORT_KW.items() if k in props}
        missing = sorted(set(EXPORT_KW) - props)
        bpy.ops.export_scene.gltf(filepath=out, **kw)

    rep = audit(out)
    rep.update({"unit": unit, "source_blend": os.path.relpath(blend, ROOT), "rig": rig.name, "check_only": check_only,
                "meshes": [o.name for o in meshes], "options_missing": missing,
                "clips_in_blend": sorted(acts), "underscore_attributes": underscore, "colour_gate": {}})
    d, js, bo = read_glb(out)
    structure_ok = rep["COLOR_0"] and rep["COLOR_1"] and rep[GLOW_ATTR]
    hdr_lost = alpha_lost = 0
    for o in meshes:
        me = o.data
        prims = [p for m in js["meshes"] if m.get("name") == me.name for p in m["primitives"]]
        # The exporter drops sliver triangles (petalfang 4980 -> 4978 tris; the one colour it lost sits on a
        # 3.4e-7 m2 tri). So the check is asymmetric: every glb value must exist in the FULL source (catches a
        # swapped / wrong set), and every source value carried by a triangle of area > SLIVER_M2 must exist in the
        # glb (a value living only on sub-mm2 slivers is reported, not failed).
        me.calc_loop_triangles()
        nt = len(me.loop_triangles)
        lt = np.empty(nt * 3, dtype=np.int64); me.loop_triangles.foreach_get("loops", lt)
        ar = np.empty(nt); me.loop_triangles.foreach_get("area", ar)
        lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
        big = lt.reshape(-1, 3)[ar > SLIVER_M2].ravel()
        tri_idx = {"CORNER": (lt, big), "POINT": (lv[lt], lv[big])}
        g = {}
        for aname, cset in ((ALBEDO_SET, "COLOR_0"), (GLOW_SET, "COLOR_1"), (GLOW_SET, GLOW_ATTR)):
            attr = me.color_attributes.get(aname)
            if attr is None or not prims:
                g[cset] = "source attribute %r or glb mesh %r missing" % (aname, me.name)
                structure_ok = False
                continue
            raw = np.empty(len(attr.data) * 4); attr.data.foreach_get("color", raw)
            raw = raw.reshape(-1, 4)
            src = distinct(raw[tri_idx[attr.domain][0]])        # every triangle corner
            src_big = distinct(raw[tri_idx[attr.domain][1]])    # corners of non-sliver triangles
            acc = [js["accessors"][p["attributes"][cset]] for p in prims if cset in p["attributes"]]
            glb = distinct([v for p in prims if cset in p["attributes"]
                            for v in accessor_values(d, js, bo, p["attributes"][cset])])
            # RGB and alpha judged apart: a VEC3 set (the exporter's choice when the material reads no vertex
            # alpha) drops the source alpha -- reported as alpha_dropped, not a colour mismatch. The exporter
            # clips normalized-u16 sets to [0, 1] (io_scene_gltf2 primitive_attributes.py __gather_attribute
            # np.clip): clamp_only = glb matches clip(source) exactly while some source value lies outside [0, 1].
            has_vec3 = any(a["type"] == "VEC3" for a in acc)
            c = 3 if has_vec3 else 4
            s_all, s_big, g_ = (np.unique(x[:, :c], axis=0) for x in (src, src_big, glb))
            clip = lambda x: np.unique(np.clip(x, 0, 1), axis=0)  # noqa: E731
            exact = covered(g_, s_all) == 0 and covered(s_big, g_) == 0
            clamp_only = (cset == "COLOR_1" and not exact and covered(g_, clip(s_all)) == 0
                          and covered(clip(s_big), g_) == 0)   # _GLOW must be EXACT; only compat COLOR_1 may clamp
            hdr = src[(src[:, :3] > 1 + 1e-6).any(axis=1) | (src[:, :3] < -1e-6).any(axis=1)]
            alpha_dropped = bool(has_vec3 and cset != GLOW_ATTR and (src[:, 3] < 1 - 1e-6).any())
            g[cset] = {"from": aname, "encoding": sorted({"%s/%s%s" % (a["type"], a["componentType"],
                                                                       "n" if a.get("normalized") else "") for a in acc}),
                       "distinct_src": len(src), "distinct_glb": len(glb), "exact": exact,
                       "glb_max": round(float(glb[:, :3].max()), 4),
                       "clamp_only": clamp_only, "alpha_dropped": alpha_dropped,
                       "src_values_only_on_slivers": covered(s_all, s_big),
                       "src_alpha_values": sorted({round(float(x), 3) for x in src[:, 3]})[:8],
                       "src_values_outside_0_1": len(hdr), "src_max": round(float(src[:, :3].max()), 4),
                       "src_hdr_values": [[round(float(x), 4) for x in r[:3]] for r in hdr[:8]],
                       "tri_corners_outside_0_1": int((raw[tri_idx[attr.domain][0]][:, :3] > 1 + 1e-6).any(axis=1).sum())}
            alpha_lost += int(alpha_dropped)
            if not exact:
                if clamp_only:
                    hdr_lost += len(hdr)
                else:
                    structure_ok = False
        rep["colour_gate"][o.name] = g
    chans = {}
    for a in js.get("animations", []):
        kinds = {}
        for c in a["channels"]:
            kinds[c["target"].get("path")] = kinds.get(c["target"].get("path"), 0) + 1
        chans[a.get("name")] = kinds
    rep["animation_channels"] = chans
    rep["pass_structure"] = bool(structure_ok)
    rep["color1_compat_hdr_values_clamped"] = hdr_lost   # informational: the glow carrier is _GLOW (gated EXACT)
    rep["alpha_dropped_sets"] = alpha_lost
    flags = ["ALPHA_DROPPED"] * bool(alpha_lost)
    rep["verdict"] = "FAIL" if not structure_ok else ("+".join(flags) or "EXACT")
    print("GLB_EXPORT " + json.dumps(rep))
    sys.stdout.flush()
    os._exit(1 if not structure_ok else (2 if flags else 0))


if __name__ == "__main__":
    main()
