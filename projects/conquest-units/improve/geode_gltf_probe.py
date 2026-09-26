"""Does the glTF export carry Geode's glow and its pulse? Opens rigged/geode.blend, exports GLBs to a throwaway path
(never into the project), parses each GLB's JSON chunk and reports what a consumer would receive. Never saves the blend.

    blender --background rigged/geode.blend --factory-startup --python geode_gltf_probe.py -- <tmp.glb> <out.json>

Questions answered from the files themselves, per export_animation_mode (ACTIONS = per-clip, SCENE = one timeline):
  - colour sets: is 'Col' COLOR_0 and 'Glow' COLOR_1 on the primitive (export_all_vertex_colors)?
  - emission: what emissive does the material carry (the Glow -> Emission Color link is a vertex-colour link)?
  - animated emission: does the exporter write the idle's Emission Strength keys (KHR_animation_pointer ->
    KHR_materials_emissive_strength) next to the bone channels?
  - the geometric pulse: is there a 'scale' channel on the core joint?
  - motion v2: does the walk clip export, and does the electricity-arc flicker (shape keys on the mesh's Key slot of
    the same action) arrive as morph-target 'weights' channels (core glTF, no extension needed)?
"""
import bpy, sys, os, json, struct

argv = sys.argv[sys.argv.index("--") + 1:]
GLB, OUT = argv[0], argv[1]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rigkit as K  # noqa: E402
# the idle's glow keys live on the material node-tree slot of the SAME 'idle' action (the Mycothrall pattern); the
# build leaves that slot unassigned, so an export has to bind it -- exactly what a ship-time export would do
idle = bpy.data.actions.get("idle")
rig = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
K.assign_action(rig, idle)
mesh = next(o for o in bpy.context.scene.objects if o.type == "MESH" and o.parent is rig)
nt = mesh.data.materials[0].node_tree
bound = False
try:
    nt.animation_data_create()
    nt.animation_data.action = idle
    slot = next((s for s in idle.slots if s.identifier.startswith("NT")), None)
    if slot is not None:
        nt.animation_data.action_slot = slot
        bound = True
except Exception as exc:  # noqa: BLE001
    bound = repr(exc)
key_bound = False
key = mesh.data.shape_keys
try:
    kslot = next((s for s in idle.slots if s.identifier.startswith("KE")), None)
    if key is not None and kslot is not None:
        key.animation_data_create()
        key.animation_data.action = idle
        key.animation_data.action_slot = kslot
        key_bound = True
except Exception as exc:  # noqa: BLE001
    key_bound = repr(exc)
props = {p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}
want = {"export_format": "GLB", "export_animations": True, "export_skins": True, "use_selection": False,
        "export_all_vertex_colors": True, "export_vertex_color": "ACTIVE", "export_pointer_animation": True,
        "export_apply": False, "export_yup": True}
kw = {k: v for k, v in want.items() if k in props}
res = {"exporter_options_used": kw, "options_missing": sorted(set(want) - props), "glow_slot_bound_for_export": bound,
       "arc_key_slot_bound_for_export": key_bound, "modes": {}}


def export_and_read(mode):
    path = GLB[:-4] + "_" + mode.lower() + ".glb"
    r = bpy.ops.export_scene.gltf(filepath=path, export_animation_mode=mode, **kw)
    data = open(path, "rb").read()
    clen = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20:20 + clen].decode("utf-8"))
    os.remove(path)
    out = {"export_result": sorted(r), "glb_bytes": len(data), "extensionsUsed": js.get("extensionsUsed", [])}
    prims = [p for m in js.get("meshes", []) for p in m.get("primitives", [])]
    out["primitive_attributes"] = [sorted(p["attributes"].keys()) for p in prims]
    out["morph_targets_per_primitive"] = [len(p.get("targets", [])) for p in prims]
    out["materials"] = [{"name": m.get("name"), "emissiveFactor": m.get("emissiveFactor"),
                         "emissiveTexture": "emissiveTexture" in m,
                         "emissive_strength": m.get("extensions", {}).get("KHR_materials_emissive_strength", {}).get("emissiveStrength")}
                        for m in js.get("materials", [])]
    nodes = js.get("nodes", [])
    anims = []
    for a in js.get("animations", []):
        ch = []
        for c in a["channels"]:
            t = c["target"]
            if "node" in t:
                ch.append("%s.%s" % (nodes[t["node"]].get("name"), t["path"]))
            else:
                ch.append("%s %s" % (t.get("path"), t.get("extensions", {}).get("KHR_animation_pointer", {}).get("pointer")))
        anims.append({"name": a.get("name"), "channels": ch})
    out["animations"] = anims
    all_ch = [c for a in anims for c in a["channels"]]
    out["verdict"] = {
        "glow_colour_set_exported_as_COLOR_1": any("COLOR_1" in a for a in out["primitive_attributes"]),
        "animated_emission_exported": any("emissiveStrength" in c for c in all_ch),
        "core_scale_pulse_exported": any(c.startswith("core.scale") for c in all_ch),
        "walk_clip_exported": any("walk" in (a["name"] or "").lower() for a in anims) or mode == "SCENE",
        "arc_flicker_morph_weights_exported": any(c.endswith(".weights") for c in all_ch),
        "material_emissive_is_uniform_factor": bool(out["materials"]) and not out["materials"][0]["emissiveTexture"]
        and out["materials"][0]["emissiveFactor"] is not None}
    return out


for mode in ("ACTIONS", "SCENE"):
    try:
        res["modes"][mode] = export_and_read(mode)
    except Exception as exc:  # noqa: BLE001
        res["modes"][mode] = {"error": repr(exc)}
json.dump(res, open(OUT, "w"), indent=1)
for mode, v in res["modes"].items():
    print("GLTF_PROBE", mode, json.dumps(v.get("verdict", v)), json.dumps(v.get("extensionsUsed")))
sys.stdout.flush()
os._exit(0)
