"""Whole-artifact check of a git/werewolf model-swap-contract character .glb.

Usage (any Python with numpy; Blender's bundled one works):
    python verify_game_glb.py <kind.glb> [manifest.json] [--expect-height 2.1]
                              [--expect-images <kind>.images.json]

Asserts: one scene, one skin; NORMAL + TANGENT shipped; bind pose resolves (joint world @ IBM == I, so the
rendered bind pose IS the POSITION data); feet at origin; facing -Z (ankle ->
toe on -Z); morph targets present; every clip starts at t=0; every clip_map
value exists under the name Godot will give it. Prints every number; exits with
the fail count.

Image inventory: Godot extracts every embedded glTF image on import as
<kind>_Image_N.png (N = glTF image index) and the game repo commits those files, so
a rebuild that changes the image count, order or names churns tracked files. The
inventory (glTF images[] in index order: name, mimeType) is always printed; with
--expect-images it must equal the JSON list in that file exactly - count, order and
names. werewolf_escaped.images.json beside this file is that kind's pinned list.

Godot 4.6's glTF importer renames clips (measured 2026-09-24 on a headless
import): a trailing "-loop" is stripped and the clip set to LOOP_LINEAR
("walk-loop" -> "walk"), and "." becomes "_" ("punch.L" -> "punch_L"). A
manifest clip_map value must name the Godot-side clip, so this checks against
godot_name(), not the raw glTF name.
"""
import json, re, struct, sys
import numpy as np

FAILS = []


def godot_name(gltf_name):
    """The AnimationPlayer name Godot 4.6 gives a glTF animation (see docstring)."""
    name = gltf_name
    looping = False
    if name.endswith("-loop"):  # the only suffix measured; other spellings unverified
        name, looping = name[: -len("-loop")], True
    return re.sub(r"[.:,\[/]", "_", name), looping


def check(label, ok, detail=""):
    print("  %s %s %s" % ("PASS" if ok else "FAIL", label, detail))
    if not ok:
        FAILS.append(label)


def load(path):
    b = open(path, "rb").read()
    magic, ver, length = struct.unpack_from("<III", b, 0)
    assert magic == 0x46546C67 and ver == 2 and length == len(b), "not a glb v2"
    off, chunks = 12, {}
    while off < len(b):
        clen, ctype = struct.unpack_from("<II", b, off)
        chunks[ctype] = b[off + 8: off + 8 + clen]
        off += 8 + clen
    return json.loads(chunks[0x4E4F534A]), chunks.get(0x004E4942)


CT = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1)}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def accessor(j, binc, i):
    a = j["accessors"][i]
    bv = j["bufferViews"][a["bufferView"]]
    fmt, size = CT[a["componentType"]]
    n = NC[a["type"]]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", size * n)
    out = np.empty((a["count"], n), dtype=np.float64)
    for k in range(a["count"]):
        out[k] = struct.unpack_from("<%d%s" % (n, fmt), binc, start + k * stride)
    return out


def trs(node):
    t = np.array(node.get("translation", [0, 0, 0]), float)
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    s = np.array(node.get("scale", [1, 1, 1]), float)
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * s
    m[:3, 3] = t
    if "matrix" in node:
        m = np.array(node["matrix"], float).reshape(4, 4).T
    return m


def image_inventory(j):
    """[(name, mimeType)] in glTF index order - the order Godot numbers them by."""
    return [[im.get("name"), im.get("mimeType")] for im in j.get("images", [])]


def main(path, manifest_path=None, expect_height=None, expect_images=None):
    j, binc = load(path)
    nodes = j["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i

    def world(i):
        m = trs(nodes[i])
        while i in parent:
            i = parent[i]
            m = trs(nodes[i]) @ m
        return m

    print("== structure")
    check("one scene", len(j.get("scenes", [])) == 1, "scenes=%d" % len(j.get("scenes", [])))
    roots = j["scenes"][0]["nodes"]
    for r in roots:
        n = nodes[r]
        print("   root node %d %r translation=%s rotation=%s scale=%s" % (
            r, n.get("name"), n.get("translation", [0, 0, 0]), n.get("rotation", [0, 0, 0, 1]),
            n.get("scale", [1, 1, 1])))
    check("one skin", len(j.get("skins", [])) == 1)
    skin = j["skins"][0]
    joints = skin["joints"]
    names = {nodes[i].get("name"): i for i in joints}
    print("   joints=%d meshes=%d" % (len(joints), len(j.get("meshes", []))))

    print("== image inventory (Godot extracts these as <kind>_Image_N.png, N = index)")
    inv = image_inventory(j)
    for k, (name, mime) in enumerate(inv):
        print("   [%d] %r %s" % (k, name, mime))
    print("   images=%d textures=%d materials=%d" % (len(inv), len(j.get("textures", [])),
                                                 len(j.get("materials", []))))
    img_names = [n for n, _ in inv]
    check("image names unique", len(set(img_names)) == len(img_names), str(img_names))
    if expect_images is not None:
        want = json.load(open(expect_images))
        want = [list(w) if isinstance(w, (list, tuple)) else [w, None] for w in want]
        got = [[n, m if want and k < len(want) and want[k][1] is not None else None]
               for k, (n, m) in enumerate(inv)]
        check("image inventory matches %s (count, order, names)" % expect_images,
              got == want, "got %s want %s" % (got, want))

    print("== bind pose")
    ibm = accessor(j, binc, skin["inverseBindMatrices"])
    worst = 0.0
    for k, ji in enumerate(joints):
        M = ibm[k].reshape(4, 4).T
        worst = max(worst, float(np.abs(world(ji) @ M - np.eye(4)).max()))
    check("joint world @ IBM == identity (so rendered bind pose = POSITION)", worst < 1e-4,
          "worst element error %.2e" % worst)

    print("== geometry (glTF: +Y up, metres)")
    mesh_nodes = [i for i, n in enumerate(nodes) if "mesh" in n]
    lo = np.full(3, 1e9); hi = np.full(3, -1e9)
    tris = 0
    targets = []
    attrs = []
    for i in mesh_nodes:
        m = j["meshes"][nodes[i]["mesh"]]
        for p in m["primitives"]:
            pos = accessor(j, binc, p["attributes"]["POSITION"])
            lo = np.minimum(lo, pos.min(0)); hi = np.maximum(hi, pos.max(0))
            tris += j["accessors"][p["indices"]]["count"] // 3
            targets.append(len(p.get("targets", [])))
            print("   primitive attrs:", sorted(p["attributes"]))
            attrs.append(set(p["attributes"]))
            if "COLOR_0" in p["attributes"]:
                col = accessor(j, binc, p["attributes"]["COLOR_0"])
                ca = j["accessors"][p["attributes"]["COLOR_0"]]
                if ca.get("normalized"):
                    col = col / {5121: 255.0, 5123: 65535.0}[ca["componentType"]]
                print("   COLOR_0 %s linear min=%s max=%s" % (ca["type"], np.round(col.min(0), 4),
                                                           np.round(col.max(0), 4)))
        tn = m.get("extras", {}).get("targetNames")
        print("   mesh %r tris=%d morph targets=%s names=%s" % (m.get("name"), tris, targets, tn))
    height = hi[1] - lo[1]
    print("   bounds min=(%.4f, %.4f, %.4f) max=(%.4f, %.4f, %.4f)" % (*lo, *hi))
    print("   height (Y extent) = %.4f m; footprint X %.4f m, Z %.4f m" % (height, hi[0] - lo[0], hi[2] - lo[2]))
    check("feet at origin: min Y within 5 mm of 0", abs(lo[1]) < 0.005, "minY=%.4f m" % lo[1])
    cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
    check("centred on origin in X/Z within 5 cm", abs(cx) < 0.05 and abs(cz) < 0.05,
          "bbox centre x=%.4f z=%.4f" % (cx, cz))
    check("morph targets present", all(t > 0 for t in targets), str(targets))
    check("NORMAL + TANGENT on every primitive (smooth shading and a stable tangent basis ship)",
          all({"NORMAL", "TANGENT"} <= a for a in attrs), str([sorted(a) for a in attrs]))

    print("== facing (ankle -> toe tip, joints in world space at bind)")
    fz = []
    for side in ("L", "R"):
        foot = world(names["DEF-foot.%s" % side])[:3, 3]
        toe = world(names["DEF-toe.%s" % side])[:3, 3]
        d = toe - foot
        fz.append(d[2])
        print("   %s: DEF-foot=(%.4f, %.4f, %.4f) DEF-toe=(%.4f, %.4f, %.4f) dir.z=%+.4f" % (side, *foot, *toe, d[2]))
    check("toes point -Z (Godot forward)", all(z < 0 for z in fz), "dz=%s" % [round(z, 4) for z in fz])
    lx = world(names["DEF-upper_arm.L"])[0, 3]; rx = world(names["DEF-upper_arm.R"])[0, 3]
    check("character's left on -X (consistent with facing -Z)", lx < 0 < rx,
          "upper_arm.L x=%+.4f upper_arm.R x=%+.4f" % (lx, rx))

    print("== animations")
    anims = {a["name"]: a for a in j.get("animations", [])}
    godot = {}
    for name in anims:
        gname, looping = godot_name(name)
        godot[gname] = name
        print("   glTF %-10s -> Godot %-8s loop=%s" % (name, gname, looping))
    check("Godot names are unique", len(godot) == len(anims))
    for name, a in anims.items():
        ts = [accessor(j, binc, s["input"]) for s in a["samplers"]]
        tmin = min(float(t.min()) for t in ts); tmax = max(float(t.max()) for t in ts)
        paths = {}
        for c in a["channels"]:
            paths[c["target"]["path"]] = paths.get(c["target"]["path"], 0) + 1
        print("   %-10s t=[%.4f, %.4f] s  channels=%d %s" % (name, tmin, tmax, len(a["channels"]), paths))
        check("%s starts at t=0" % name, abs(tmin) < 1e-6)
    if manifest_path:
        man = json.load(open(manifest_path))
        print("== manifest", json.dumps(man))
        check("manifest scale is a positive number", isinstance(man.get("scale"), (int, float)) and man["scale"] > 0)
        for game, ours in man.get("clip_map", {}).items():
            check("clip_map %s -> %s is a Godot clip (glTF %r)" % (game, ours, godot.get(ours)),
                  ours in godot)
        check("idle is shipped (the fallback for every missing clip)",
              "idle" in godot or "idle" in man.get("clip_map", {}))
        s = man.get("scale", 1.0)
        print("   in-game bind height = %.4f m x %.4f = %.4f m" % (height, s, height * s))
    if expect_height:
        print("   expected ~%.3f m for this kind; bind height x scale is %.1f%% of it"
              % (expect_height, 100.0 * height * (man.get("scale", 1.0) if manifest_path else 1.0)
                 / expect_height))
    print("\n%d failed" % len(FAILS))
    return len(FAILS)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    expect = None
    images = None
    if "--expect-height" in args:
        i = args.index("--expect-height")
        expect = float(args[i + 1])
        del args[i:i + 2]
    if "--expect-images" in args:
        i = args.index("--expect-images")
        images = args[i + 1]
        del args[i:i + 2]
    sys.exit(main(args[0], args[1] if len(args) > 1 else None, expect, images))
