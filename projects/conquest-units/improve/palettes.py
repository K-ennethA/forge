"""Palette-skin machinery for Conquest unit builds (shared; any unit can adopt it).

A unit build that adopts this pattern does two things:
  1. computes its colour REGIONS once from geometry (a per-face region index + a per-face
     shade multiplier for cavity darkening / value jitter) and stores them on the mesh with
     store_regions() -- face attributes 'region_id' (INT) and 'region_shade' (FLOAT) plus the
     mesh custom prop 'conquest_regions' (the region names, in index order);
  2. paints the 'Col' / 'Glow' corner colour attributes from a PALETTE FILE with paint().

Because geometry and palette are separate, a skin variant is a pure palette swap: repaint the
stored regions from another palette file, no geometry recomputed. Palette files live in

    projects/conquest-units/palettes/<unit>/default.json      (every region, the approved look)
    projects/conquest-units/palettes/<unit>/<skin>.json       (a variant: "extends": "default",
                                                               overrides only what it changes)

Palette file schema (RGB is sRGB 0-255, what the artist sees and the report quotes):
    {"unit": "vineweave", "skin": "emberroot", "extends": "default", "description": "...",
     "regions": {"vine": {"rgb": [120, 38, 24], "emission": [255, 110, 40], "emission_scale": 0.4,
                          "note": "..."}, ...},
     "material": {"roughness": 0.8, "emission_strength": 1.5}}
A region with "emission" writes emission * emission_scale (default 1.0) into 'Glow' (the material
reads Glow as Emission Color) and is not cavity-shaded, so glowing parts stay clean.

Two ways to use it:
  - inside a build script: `import palettes as PAL` then PAL.load / PAL.store_regions / PAL.paint
    (vineweave_build.py does this and emits <unit>__<skin>.blend per requested skin);
  - as a standalone repaint of an already-built blend (any unit that stored its regions):
        blender --background <unit.blend> --factory-startup --python palettes.py -- \
            --unit <unit> --skin <skin> --out <unit__skin.blend>
    It repaints every mesh carrying 'region_id', applies the palette's material block, and
    saves a COPY; the opened blend is never written.
"""
import json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PALETTE_ROOT = os.path.normpath(os.path.join(HERE, "..", "palettes"))


def srgb_to_linear(rgb):
    v = np.asarray(rgb, float) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def palette_path(unit, skin="default"):
    return os.path.join(PALETTE_ROOT, unit, skin + ".json")


def list_skins(unit):
    d = os.path.join(PALETTE_ROOT, unit)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def load(unit, skin="default"):
    """Resolve a palette (following 'extends'), validate it, return a plain dict:
    {"unit", "skin", "files": [...], "regions": {name: {...}}, "material": {...}}."""
    p = palette_path(unit, skin)
    if not os.path.exists(p):
        raise FileNotFoundError("no palette %s (have: %s)" % (p, list_skins(unit)))
    raw = json.load(open(p, encoding="utf-8"))
    files = [p]
    if raw.get("extends"):
        base = load(unit, raw["extends"])
        unknown = sorted(set(raw.get("regions", {})) - set(base["regions"]))
        if unknown:
            raise ValueError("palette %s overrides regions its base does not define: %s" % (p, unknown))
        regions = {k: dict(v) for k, v in base["regions"].items()}
        for k, v in raw.get("regions", {}).items():
            regions[k] = dict(v) if v.get("replace") else {**regions[k], **v}
        material = {**base["material"], **raw.get("material", {})}
        files = base["files"] + files
    else:
        regions = {k: dict(v) for k, v in raw["regions"].items()}
        material = dict(raw.get("material", {}))
    for k, v in regions.items():
        for key in ("rgb", "emission"):
            if key in v:
                c = v[key]
                if len(c) != 3 or any((not isinstance(x, int)) or x < 0 or x > 255 for x in c):
                    raise ValueError("palette %s region %s: %s must be 3 ints 0-255, got %r" % (p, k, key, c))
        if "rgb" not in v:
            raise ValueError("palette %s region %s has no rgb" % (p, k))
    return {"unit": unit, "skin": raw.get("skin", skin), "description": raw.get("description", ""),
            "files": files, "regions": regions, "material": material}


def table(pal):
    """Report-ready rows: region -> rgb (+ emission)."""
    out = {}
    for k, v in pal["regions"].items():
        row = {"rgb": list(v["rgb"])}
        if "emission" in v:
            row["emission"] = list(v["emission"]); row["emission_scale"] = v.get("emission_scale", 1.0)
        out[k] = row
    return out


# --------------------------------------------------------------------------- mesh side (bpy)
def store_regions(me, names, face_region, face_shade):
    """Persist the geometry-derived region map on the mesh (survives into rigged copies)."""
    face_region = np.asarray(face_region, dtype=np.int32)
    face_shade = np.asarray(face_shade, dtype=np.float32)
    assert len(face_region) == len(me.polygons) == len(face_shade)
    for nm in ("region_id", "region_shade"):
        if nm in me.attributes:
            me.attributes.remove(me.attributes[nm])
    me.attributes.new("region_id", "INT", "FACE").data.foreach_set("value", face_region)
    me.attributes.new("region_shade", "FLOAT", "FACE").data.foreach_set("value", face_shade)
    me["conquest_regions"] = list(names)


def read_regions(me):
    names = list(me["conquest_regions"])
    n = len(me.polygons)
    rid = np.empty(n, dtype=np.int32); me.attributes["region_id"].data.foreach_get("value", rid)
    sh = np.empty(n, dtype=np.float32); me.attributes["region_shade"].data.foreach_get("value", sh)
    return names, rid, sh


def face_colours(names, rid, shade, pal):
    """-> (col linear Nx3, glow linear Nx3). Missing region in the palette = hard error."""
    missing = [n for n in names if n not in pal["regions"]]
    if missing:
        raise KeyError("palette %s/%s lacks regions %s" % (pal["unit"], pal["skin"], missing))
    base = np.array([srgb_to_linear(pal["regions"][n]["rgb"]) for n in names])
    emis = np.array([srgb_to_linear(pal["regions"][n]["emission"]) * pal["regions"][n].get("emission_scale", 1.0)
                     if "emission" in pal["regions"][n] else np.zeros(3) for n in names])
    col = base[rid]
    glow = emis[rid]
    lit = glow.sum(1) <= 0
    col[lit] *= np.asarray(shade)[lit, None]
    return np.clip(col, 0, 1), glow


def paint(me, pal):
    """(Re)write 'Col' + 'Glow' CORNER colours from the stored regions and a resolved palette."""
    names, rid, sh = read_regions(me)
    col, glow = face_colours(names, rid, sh, pal)
    nf = len(me.polygons)
    for nm in ("Col", "Glow"):
        if nm in me.color_attributes:
            me.color_attributes.remove(me.color_attributes[nm])
    ca = me.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    ga = me.color_attributes.new("Glow", "FLOAT_COLOR", "CORNER")
    lt = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ca.data.foreach_set("color", np.repeat(np.hstack([col, np.ones((nf, 1))]), lt, axis=0).ravel())
    ga.data.foreach_set("color", np.repeat(np.hstack([glow, np.ones((nf, 1))]), lt, axis=0).ravel())
    me.color_attributes.active_color = ca
    me.color_attributes.render_color_index = me.color_attributes.find("Col")
    me["conquest_skin"] = pal["skin"]
    counts = np.bincount(rid, minlength=len(names))
    return {n: int(c) for n, c in zip(names, counts)}


def apply_material(mat, pal):
    """Palette 'material' block -> Principled BSDF inputs (roughness, emission strength)."""
    if mat is None or not mat.node_tree:
        return
    bsdf = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is None:
        return
    m = pal["material"]
    if "roughness" in m:
        bsdf.inputs["Roughness"].default_value = m["roughness"]
    if "emission_strength" in m:
        bsdf.inputs["Emission Strength"].default_value = m["emission_strength"]


def repaint_scene(unit, skin):
    import bpy
    pal = load(unit, skin)
    done = {}
    for o in bpy.context.scene.objects:
        if o.type == "MESH" and "region_id" in o.data.attributes:
            done[o.name] = paint(o.data, pal)
            for s in o.material_slots:
                apply_material(s.material, pal)
    return pal, done


if __name__ == "__main__" and "--" in sys.argv:
    import bpy
    a = sys.argv[sys.argv.index("--") + 1:]
    kv = {a[i]: a[i + 1] for i in range(0, len(a) - 1, 2)}
    pal, done = repaint_scene(kv["--unit"], kv["--skin"])
    if not done:
        print("PALETTE_ERROR no mesh carries region_id: this unit has not adopted the palette pattern")
        sys.stdout.flush(); os._exit(1)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=kv["--out"], copy=True, compress=True)
    print("PALETTE_DONE", json.dumps({"skin": pal["skin"], "files": pal["files"], "meshes": done}))
    sys.stdout.flush(); os._exit(0)
