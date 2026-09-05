"""Headless add-on tests for Phase 4 (RigForge rig and Godot export).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_phase4.py

Like ``headless_rigforge.py`` this needs no geometry service; unlike it, it
needs **Rigify**, which ships with Blender and is enabled in-session by the
add-on itself.  The synthetic tagged biped is imported from the Phase 3 suite
so the two files cannot drift apart, then carried the whole way: tag ->
retopo -> metarig -> generate -> weights -> glTF, with the exported file parsed
back out of the .glb and checked joint by joint.

The two ``--background`` facts that shape this file are the usual ones: no event
loop (the harness drains the server queue from the main thread) and no window
(so every code path under test has to work without a 3D area).
"""

import json
import os
import shutil
import socket as socketlib
import struct
import sys
import tempfile
import threading
import time
import traceback

import bpy
from mathutils import Vector

# --- harness ----------------------------------------------------------------

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))

PORT = 9880  # not 9876 (a live session), 9878 (phase2) or 9879 (rigforge)
SCULPT = "Sculpt"
RETOPO = SCULPT + "_retopo"
ACTION = "wave-loop"

_RESULTS = []


def check(label, condition, detail=""):
    _RESULTS.append((label, bool(condition), detail))
    print("  %s %s%s" % ("PASS" if condition else "FAIL", label,
                         ("  -- " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def note(text):
    print("     %s" % text)


def section(title):
    print("\n== %s ==" % title)


def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def call(command, params=None, timeout=1800.0, expect_error=False):
    """One socket round trip, pumping the main-thread queue while it is in flight."""
    from forge import server as forge_server

    payload = {"type": command, "params": params or {}}
    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(1 << 20)
                    if not chunk:
                        break
                    buffer += chunk
                box["reply"] = json.loads(buffer.split(b"\n")[0].decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=talk, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        if forge_server._server is not None:
            forge_server._server.drain()
        time.sleep(0.005)
    thread.join(timeout=2.0)

    if "error" in box:
        reply = {"status": "error", "message": "harness: %s" % box["error"]}
    else:
        reply = box.get("reply") or {"status": "error",
                                     "message": "no reply within %.0fs" % timeout}
    if expect_error:
        return reply
    if reply.get("status") != "success":
        raise AssertionError("%s failed: %s" % (command, reply.get("message")))
    return reply.get("result") or {}


# --- the tagged biped -------------------------------------------------------

def build_tagged_biped():
    """Phase 3's synthetic sculpt, tagged the way stage 4 expects to find it.

    The builder is imported rather than copied: if the Phase 3 sculpt changes,
    this suite must be looking at the same thing.
    """
    import headless_rigforge as phase3

    obj = phase3.build_sculpt(SCULPT)
    regions = phase3.classify_faces(obj)

    # Ears: the outer caps of the head sphere. They are what makes this a test
    # of chains and secondary motion and not just of a humanoid template.
    ears = {"Ear.L": [], "Ear.R": []}
    head = []
    for index, centre in phase3.face_centres(obj):
        if index not in set(regions["Head"]):
            continue
        if centre.z > 1.72 and abs(centre.x) > 0.10:
            ears["Ear.L" if centre.x > 0 else "Ear.R"].append(index)
        else:
            head.append(index)
    regions["Head"] = head
    regions.update(ears)
    return obj, regions


def bounds_of(obj, tag):
    """World-space bounding box of a tag on an object."""
    from forge.tools import rigforge as rf

    group = obj.vertex_groups.get("tag_" + tag)
    if group is None:
        return None
    matrix = obj.matrix_world
    points = []
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            if entry.group == group.index and entry.weight > 0.0:
                points.append(matrix @ vertex.co)
                break
    if not points:
        return None
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    return low, high


def inside(bounds, point, slack=0.0):
    if bounds is None:
        return False
    low, high = bounds
    for i in range(3):
        if point[i] < low[i] - slack or point[i] > high[i] + slack:
            return False
    return True


# --- glTF ------------------------------------------------------------------

def parse_gltf(path):
    """The JSON document of a .glb (chunk 0) or a .gltf (the file itself)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"glTF":
        return json.loads(data.decode("utf-8"))
    _magic, _version, length = struct.unpack("<III", data[:12])
    offset = 12
    while offset < min(length, len(data)):
        chunk_length, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        chunk = data[offset + 8:offset + 8 + chunk_length]
        if chunk_type == 0x4E4F534A:  # 'JSON'
            return json.loads(chunk.decode("utf-8"))
        offset += 8 + chunk_length
    raise AssertionError("%s has no JSON chunk" % path)


# --- tests ------------------------------------------------------------------

def test_setup(obj, regions):
    section("the tagged sculpt")
    for name, faces in sorted(regions.items()):
        if not faces:
            check("region %s has faces" % name, False)
            continue
        call("rigforge_tag", {"object": obj.name, "tag": name, "faces": faces,
                              "replace": True})
    call("rigforge_manifest", {"object": obj.name, "action": "get", "archetype": "biped",
                               "motion_notes": "ears are floppy and lag behind the head"})
    listing = call("rigforge_list_tags", {"object": obj.name})
    names = sorted(entry["name"] for entry in listing["tags"])
    check("all eight tags are on the sculpt",
          names == ["Arm.L", "Arm.R", "Ear.L", "Ear.R", "Head", "Leg.L", "Leg.R", "Torso"],
          str(names))
    check("every tag has vertices",
          all(entry["vertex_count"] > 0 for entry in listing["tags"]),
          str([(e["name"], e["vertex_count"]) for e in listing["tags"]]))


def test_retopo(obj):
    section("retopo to 5000 faces (the mesh stage 4 rigs)")
    result = call("rigforge_retopo", {"object": obj.name, "target_faces": 5000,
                                      "platform": "mobile", "lods": 2})
    retopo = bpy.data.objects.get(RETOPO)
    if not check("the retopo mesh exists", retopo is not None, str(result["objects"])):
        return None
    check("it is roughly the 5000-face target",
          abs(len(retopo.data.polygons) - 5000) <= 1500,
          "%d faces" % len(retopo.data.polygons))
    tags = [g.name for g in retopo.vertex_groups if g.name.startswith("tag_")]
    check("all eight tags transferred", len(tags) == 8, str(sorted(tags)))
    populated = call("rigforge_list_tags", {"object": RETOPO})
    empty = [e["name"] for e in populated["tags"] if not e["vertex_count"]]
    check("no tag lost all of its geometry in the transfer", not empty, str(empty))
    note("retopo: %d faces, LODs %s" % (len(retopo.data.polygons),
                                        [n for n in result["objects"] if "lod" in n]))
    return retopo


def test_metarig(retopo):
    section("rigforge_metarig - fitted to the tags, not just scaled")
    result = call("rigforge_metarig", {"object": retopo.name, "archetype": "auto"})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    meta = bpy.data.objects.get(result["metarig"])
    if not check("the metarig object exists", meta is not None, result["metarig"]):
        return None, result
    check("it is an armature with bones", meta.type == "ARMATURE" and
          len(meta.data.bones) > 10, "%s, %d bones" % (meta.type, len(meta.data.bones)))
    check("the archetype came from the manifest", result["archetype"] == "biped",
          result["archetype"])
    check("a sculpt with no face or hand tags gets the game-weight template",
          result["preset"] == "basic_human",
          "%s (%s)" % (result["preset"], result["preset_reason"]))
    check("Rigify was enabled by the add-on, not by hand",
          bool((result.get("rigify") or {}).get("module")), str(result.get("rigify")))

    # --- the actual promise: bones land on the tagged landmarks
    bones = {bone.name: (meta.matrix_world @ bone.head_local,
                         meta.matrix_world @ bone.tail_local)
             for bone in meta.data.bones}
    span = max(retopo.dimensions)
    slack = span * 0.02

    head_bounds = bounds_of(retopo, "Head")
    if check("spine.006 (the head bone) exists", "spine.006" in bones):
        head, tail = bones["spine.006"]
        check("the head bone starts inside the Head tag",
              inside(head_bounds, head, slack), "%s vs %s" % (list(head), head_bounds))
        check("and ends inside it too (it did not overshoot the skull)",
              inside(head_bounds, tail, slack), "%s vs %s" % (list(tail), head_bounds))

    torso_bounds = bounds_of(retopo, "Torso")
    if check("spine (the hips) exists", "spine" in bones):
        head, _tail = bones["spine"]
        check("the hip bone starts inside the Torso tag",
              inside(torso_bounds, head, slack), "%s vs %s" % (list(head), torso_bounds))

    for side in ("L", "R"):
        arm_bounds = bounds_of(retopo, "Arm.%s" % side)
        upper = bones.get("upper_arm.%s" % side)
        fore = bones.get("forearm.%s" % side)
        if check("upper_arm.%s and forearm.%s exist" % (side, side),
                 upper is not None and fore is not None):
            check("the shoulder joint is inside the Arm.%s tag" % side,
                  inside(arm_bounds, upper[0], slack),
                  "%s vs %s" % (list(upper[0]), arm_bounds))
            check("the elbow is inside the Arm.%s tag" % side,
                  inside(arm_bounds, upper[1], slack),
                  "%s vs %s" % (list(upper[1]), arm_bounds))
            check("the wrist is inside the Arm.%s tag" % side,
                  inside(arm_bounds, fore[1], slack),
                  "%s vs %s" % (list(fore[1]), arm_bounds))
            check("the arm bends (Rigify needs a pole, a straight limb has none)",
                  (upper[1] - upper[0]).normalized().dot(
                      (fore[1] - fore[0]).normalized()) < 0.9995,
                  "elbow angle is degenerate")

        leg_bounds = bounds_of(retopo, "Leg.%s" % side)
        thigh = bones.get("thigh.%s" % side)
        shin = bones.get("shin.%s" % side)
        if check("thigh.%s and shin.%s exist" % (side, side),
                 thigh is not None and shin is not None):
            check("the hip joint is inside the Leg.%s tag (or just above it)" % side,
                  inside(leg_bounds, thigh[0], slack * 4),
                  "%s vs %s" % (list(thigh[0]), leg_bounds))
            check("the knee is inside the Leg.%s tag" % side,
                  inside(leg_bounds, thigh[1], slack),
                  "%s vs %s" % (list(thigh[1]), leg_bounds))
            check("the ankle is inside the Leg.%s tag" % side,
                  inside(leg_bounds, shin[1], slack),
                  "%s vs %s" % (list(shin[1]), leg_bounds))

    # the metarig must be the size of the sculpt, not Rigify's default human
    heads = [b.head_local for b in meta.data.bones] + [b.tail_local for b in meta.data.bones]
    rig_height = max(p.z for p in heads) - min(p.z for p in heads)
    mesh_height = retopo.dimensions.z
    check("the metarig was scaled to the mesh, not left at Rigify's default",
          abs(rig_height - mesh_height) / mesh_height < 0.25,
          "%.3f vs %.3f" % (rig_height, mesh_height))

    # --- chains
    section("ear chains")
    chains = result.get("chains") or []
    tags = sorted(chain["tag"] for chain in chains)
    check("both ear tags became bone chains", tags == ["Ear.L", "Ear.R"], str(tags))
    for chain in chains:
        check("chain %s has bones in the metarig" % chain["tag"],
              all(name in bones for name in chain["bones"]), str(chain["bones"]))
        check("chain %s is anchored to an existing bone" % chain["tag"],
              chain.get("parent") in bones, str(chain.get("parent")))
        check("chain %s is flagged for Rigify" % chain["tag"],
              chain.get("rigify_type") == "basic.copy_chain", str(chain.get("rigify_type")))
        check("chain %s read the motion notes ('floppy' -> more lag)" % chain["tag"],
              chain.get("follow", 1.0) < 0.6, str(chain.get("follow")))
        ear_bounds = bounds_of(retopo, chain["tag"])
        first = bones[chain["bones"][0]][0]
        last = bones[chain["bones"][-1]][1]
        check("chain %s spans its own tag" % chain["tag"],
              inside(ear_bounds, first, slack) and inside(ear_bounds, last, slack),
              "%s .. %s vs %s" % (list(first), list(last), ear_bounds))

    check("the chain metadata is stashed on the metarig for generate",
          bool(meta.get("forge_spring_chains")), str(meta.get("forge_spring_chains"))[:80])
    check("the tag -> bone mapping is stashed too", bool(meta.get("forge_tag_bones")))
    mapping = result["mapping"]
    check("every tagged region got bones",
          set(mapping) == {"Head", "Torso", "Arm.L", "Arm.R", "Leg.L", "Leg.R",
                           "Ear.L", "Ear.R"},
          str(sorted(mapping)))
    return meta, result


def test_metarig_needs_tags():
    section("rigforge_metarig on an untagged mesh")
    mesh = bpy.data.meshes.new("Bare")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Bare", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    reply = call("rigforge_metarig", {"object": "Bare"}, expect_error=True)
    check("it refuses with a message that says what to do",
          reply.get("status") == "error" and "tag" in (reply.get("message") or "").lower(),
          reply.get("message"))
    bpy.data.objects.remove(obj, do_unlink=True)


def test_generate(meta, retopo):
    section("rigforge_generate_rig - Rigify, weights, per-tag cleanup")
    started = time.monotonic()
    result = call("rigforge_generate_rig", {"metarig": meta.name, "mesh": retopo.name})
    note("generate took %.1fs" % (time.monotonic() - started))
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    rig = bpy.data.objects.get(result["rig"])
    if not check("the rig exists", rig is not None, result["rig"]):
        return None, result
    check("it is an armature", rig.type == "ARMATURE", rig.type)

    names = [bone.name for bone in rig.data.bones]
    deform = [name for name in names if name.startswith("DEF-")]
    check("Rigify generated deform bones", len(deform) >= 20, "%d DEF bones" % len(deform))
    check("and control bones alongside them",
          result["control_bones"] > result["deform_bones"],
          "%d control, %d deform" % (result["control_bones"], result["deform_bones"]))
    check("the ear chains produced deform bones",
          {"DEF-Ear.L", "DEF-Ear.R"} <= set(names),
          str(sorted(n for n in names if "Ear" in n)))

    check("the mesh is parented to the rig", retopo.parent is rig,
          str(retopo.parent.name if retopo.parent else None))
    modifiers = [m for m in retopo.modifiers if m.type == "ARMATURE" and m.object is rig]
    check("with an armature modifier bound to it", bool(modifiers),
          str([(m.type, getattr(m.object, "name", None)) for m in retopo.modifiers]))
    groups = {g.name for g in retopo.vertex_groups}
    check("deform vertex groups were created", len(groups & set(deform)) >= 20,
          "%d of %d" % (len(groups & set(deform)), len(deform)))
    check("the tags survived skinning",
          len([n for n in groups if n.startswith("tag_")]) == 8,
          str(sorted(n for n in groups if n.startswith("tag_"))))
    note("weights: %s" % json.dumps(result.get("weights") or {}))

    section("weight cleanup rules")
    report = result.get("cleanup_report") or {}
    check("a cleanup report came back", bool(report.get("tags")), str(report)[:200])
    per_tag = {entry["tag"]: entry for entry in report.get("tags", [])}
    check("every tag is in the report", len(per_tag) == 8, str(sorted(per_tag)))
    check("the report counts the weights it zeroed",
          isinstance(report.get("weights_zeroed"), int), str(report.get("weights_zeroed")))
    check("influences were limited and normalised",
          "limited" in report and "normalized" in report, str(sorted(report)))
    note("zeroed %d weight(s) across %d tag(s); band %.4f"
         % (report.get("weights_zeroed", 0), len(per_tag), report.get("band", 0.0)))
    for tag in sorted(per_tag):
        entry = per_tag[tag]
        note("  %-7s %4d verts  %4d zeroed  %s"
             % (tag, entry["vertices"], entry["weights_zeroed"],
                ", ".join(entry["bones"][:4])))

    # --- the plan's own example, verified on a vertex: no head weights below the neck
    head_bounds = bounds_of(retopo, "Head")
    torso_group = retopo.vertex_groups.get("tag_Torso")
    head_bones = {name for name in deform if name in ("DEF-spine.006",)}
    head_indices = {retopo.vertex_groups[name].index for name in head_bones
                    if retopo.vertex_groups.get(name) is not None}
    matrix = retopo.matrix_world
    worst = None
    checked = 0
    for vertex in retopo.data.vertices:
        if not any(e.group == torso_group.index and e.weight > 0.0 for e in vertex.groups):
            continue
        point = matrix @ vertex.co
        if point.z > head_bounds[0].z - 0.15:
            continue  # inside the neck band, where blending is the point
        checked += 1
        for element in vertex.groups:
            if element.group in head_indices and element.weight > 0.0:
                worst = (vertex.index, element.weight, point.z)
    check("there are torso vertices well below the neck to test", checked > 20,
          "%d vertices" % checked)
    check("no head-bone weight survives below the neck band", worst is None,
          "vertex %s still has %.4f of DEF-spine.006 at z=%.3f" % worst if worst else "")

    from forge.tools import rigforge_rig as rr

    weights = rr.weight_report(retopo, rig)
    check("every vertex is weighted to something", weights["unweighted_vertices"] == 0,
          "%d unweighted" % weights["unweighted_vertices"])
    check("no vertex has more than 4 influences", weights["max_influences_found"] <= 4,
          "max %d" % weights["max_influences_found"])
    check("every vertex's weights sum to 1", weights["unnormalized_vertices"] == 0,
          "%d un-normalised" % weights["unnormalized_vertices"])
    note("%d deform group(s) used, %d bone(s) carry no weight at all"
         % (weights["deform_groups"], len(weights["unused_bones"])))

    section("secondary motion v1")
    springs = result.get("spring_chains") or []
    check("both chains got a lag rig", len(springs) == 2, str([s["tag"] for s in springs]))
    for entry in springs:
        mixer = rig.pose.bones.get(entry["mixer"])
        check("chain %s has a lag mixer bone" % entry["tag"], mixer is not None,
              entry["mixer"])
        if mixer is not None:
            kinds = [c.type for c in mixer.constraints]
            check("the mixer under-follows the head (that is the lag)",
                  "COPY_TRANSFORMS" in kinds
                  and mixer.constraints[0].influence < 1.0,
                  str([(c.type, round(c.influence, 3)) for c in mixer.constraints]))
        tracked = [name for name in entry["bones"]
                   if any(c.type == "DAMPED_TRACK" for c in rig.pose.bones[name].constraints)]
        check("chain %s's deform bones aim at the mixer" % entry["tag"],
              len(tracked) == len(entry["bones"]), str(tracked))
    check("the lag bones do not deform anything (they never reach Godot)",
          all(not rig.data.bones[entry["mixer"]].use_deform for entry in springs))
    return rig, result


def test_weights_command(retopo, rig):
    section("rigforge_weights report / normalize round trip")
    report = call("rigforge_weights", {"object": retopo.name, "action": "report"})
    data = report["report"]
    check("report names the rig it read", report["rig"] == rig.name, report["rig"])
    check("report counts per-bone influences", bool(data["bones"]),
          str(list(data["bones"].items())[:3]))
    check("report finds nothing wrong after generate",
          data["unweighted_vertices"] == 0 and data["over_influenced_vertices"] == 0
          and data["unnormalized_vertices"] == 0, str(data)[:200])
    check("a report changes nothing", report["changed"] == 0, str(report["changed"]))

    # break the weights, then normalise them back
    group = retopo.vertex_groups[sorted(data["bones"])[0]]
    group.add([0, 1, 2], 0.37, "REPLACE")
    broken = call("rigforge_weights", {"object": retopo.name, "action": "report"})
    check("the deliberate damage shows up in a report",
          broken["report"]["unnormalized_vertices"] > 0,
          str(broken["report"]["unnormalized_vertices"]))
    fixed = call("rigforge_weights", {"object": retopo.name, "action": "normalize"})
    check("normalize reports how much it changed", fixed["changed"] > 0, str(fixed["changed"]))
    after = call("rigforge_weights", {"object": retopo.name, "action": "report"})
    check("and the mesh is clean again",
          after["report"]["unnormalized_vertices"] == 0
          and after["report"]["max_influences_found"] <= 4,
          str(after["report"])[:200])

    cleaned = call("rigforge_weights", {"object": retopo.name, "action": "cleanup"})
    check("cleanup runs on demand and reports per tag",
          len((cleaned["cleanup_report"] or {}).get("tags", [])) == 8,
          str(sorted(e["tag"] for e in cleaned["cleanup_report"]["tags"])))
    check("cleanup leaves the mesh normalised",
          cleaned["report"]["unnormalized_vertices"] == 0
          and cleaned["report"]["unweighted_vertices"] == 0,
          str(cleaned["report"])[:160])


def test_action(rig):
    section("a test action on a control bone (execute_python)")
    control = None
    for candidate in ("upper_arm_fk.L", "upper_arm_ik.L", "hand_ik.L", "torso"):
        if candidate in rig.pose.bones:
            control = candidate
            break
    if control is None:
        control = next((b.name for b in rig.pose.bones
                        if not b.name.startswith(("DEF-", "ORG-", "MCH-"))), None)
    if not check("the generated rig has a control bone to animate", control is not None):
        return None

    code = """
import bpy, math
from mathutils import Matrix
rig = bpy.data.objects[%(rig)r]
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='POSE')
bone = rig.pose.bones[%(bone)r]
bone.rotation_mode = 'QUATERNION'
if rig.animation_data is None:
    rig.animation_data_create()
action = bpy.data.actions.new(%(action)r)
rig.animation_data.action = action
if hasattr(rig.animation_data, 'action_slot') and rig.animation_data.action_slot is None:
    slots = list(getattr(rig.animation_data, 'action_suitable_slots', []))
    if not slots:
        slots = [action.slots.new(id_type='OBJECT', name=rig.name)]
    rig.animation_data.action_slot = slots[0]
for frame, angle in ((1, 0.0), (10, 0.5), (20, 0.0)):
    bpy.context.scene.frame_set(frame)
    bone.rotation_quaternion = Matrix.Rotation(angle, 4, 'X').to_quaternion()
    bone.keyframe_insert('rotation_quaternion', frame=frame)
bpy.ops.object.mode_set(mode='OBJECT')
action.name
""" % {"rig": rig.name, "bone": control, "action": ACTION}
    result = call("execute_python", {"code": code})
    check("the action was keyframed on %s" % control,
          (result.get("result") or "").strip("'\"") == ACTION, str(result))
    action = bpy.data.actions.get(ACTION)
    if check("the action exists in the file", action is not None):
        from forge.tools import rigforge_rig as rr

        check("it animates the control bone we picked",
              control in rr.action_bones(action), str(sorted(rr.action_bones(action))))
        check("it has a real frame range", action.frame_range[1] - action.frame_range[0] > 5,
              str(tuple(action.frame_range)))
    return control


def test_export(rig, retopo, workspace):
    section("rigforge_export_godot")
    before = set(bpy.data.objects.keys())
    path = os.path.join(workspace, "blob.glb")
    started = time.monotonic()
    result = call("rigforge_export_godot", {"rig": rig.name, "path": path,
                                            "actions": "all", "root_motion": False})
    note("export took %.1fs" % (time.monotonic() - started))
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    check("the glTF exists on disk", os.path.isfile(result["path"])
          and os.path.getsize(result["path"]) > 1024,
          "%s (%s bytes)" % (result["path"],
                             os.path.getsize(result["path"])
                             if os.path.exists(result["path"]) else "missing"))
    check("the exporter reported the file it wrote", result["path"] == path, result["path"])
    check("the test action was baked", ACTION in result["actions"], str(result["actions"]))

    doc = parse_gltf(result["path"])
    check("the glTF parses as JSON", isinstance(doc, dict) and "asset" in doc,
          str(list(doc)[:6]))
    check("it has a skin", bool(doc.get("skins")), str(len(doc.get("skins", []))))
    nodes = doc.get("nodes", [])
    joints = [nodes[index].get("name", "") for index in doc["skins"][0]["joints"]]
    check("the skeleton carries the deform bones",
          len([name for name in joints if name.startswith("DEF-")]) >= 20,
          "%d joints" % len(joints))
    for wanted in ("DEF-spine", "DEF-spine.006", "DEF-Ear.L", "DEF-Ear.R"):
        check("joint %s is in the exported skeleton" % wanted, wanted in joints,
              str(sorted(joints)[:8]))
    leaked = sorted(name for name in joints
                    if name.startswith(("ORG-", "MCH-", "WGT-", "VIS-")))
    check("no control bone leaked into the skeleton", not leaked, str(leaked))
    controls = sorted(name for name in joints
                      if not name.startswith("DEF-") and name != "root")
    check("the only non-DEF joint is the root", not controls, str(controls))
    note("%d joint(s): %d DEF, root %s" % (len(joints),
                                           len([n for n in joints if n.startswith("DEF-")]),
                                           "root" in joints))

    animations = [entry.get("name") for entry in doc.get("animations", [])]
    check("the action is in the file under its own name", ACTION in animations,
          str(animations))
    check("nothing else came along for the ride", len(animations) == len(result["actions"]),
          str(animations))
    channels = doc["animations"][animations.index(ACTION)].get("channels", [])
    check("the baked clip animates many bones, not just the one that was posed",
          len(channels) > 30, "%d channels" % len(channels))
    targets = {nodes[channel["target"]["node"]].get("name") for channel in channels}
    check("and the channels target deform bones",
          all(str(name).startswith("DEF-") or name == "root" for name in targets),
          str(sorted(targets)[:6]))

    meshes = [entry.get("name") for entry in doc.get("meshes", [])]
    check("the game mesh is in the file", any(RETOPO in str(name) for name in meshes),
          str(meshes))
    check("the LOD meshes came along with Godot's -lod suffix",
          any(str(name).endswith("-lod1") for name in meshes), str(meshes))

    section("the Godot import helper")
    script = result.get("import_script")
    check("a .gd script was written", script and os.path.isfile(script), str(script))
    if script and os.path.isfile(script):
        text = open(script, "r", encoding="utf-8").read()
        check("it is an EditorScenePostImport script",
              "extends EditorScenePostImport" in text and "_post_import" in text)
        check("it sets the loop flag from the -loop suffix",
              "LOOP_SUFFIX" in text and "Animation.LOOP_LINEAR" in text)
        check("it knows which actions this export contained",
              '"%s"' % ACTION in text, text[:200])
        check("it is listed in the result's files", script in result["files"],
              str(result["files"]))

    section("the export left no litter")
    after = set(bpy.data.objects.keys())
    check("no temporary object survived", after == before,
          str(sorted(after ^ before)))
    check("the temp collection is gone", "FORGE_EXPORT_TEMP" not in bpy.data.collections,
          str(list(bpy.data.collections.keys())))
    check("the rig kept its name", rig.name in bpy.data.objects, rig.name)
    check("the mesh kept its name", retopo.name == RETOPO, retopo.name)
    check("the mesh is still bound to the real rig",
          any(m.type == "ARMATURE" and m.object is rig for m in retopo.modifiers),
          str([(m.type, getattr(m.object, "name", None)) for m in retopo.modifiers]))
    return result


def test_export_root_motion(rig, workspace):
    section("rigforge_export_godot with root motion")
    path = os.path.join(workspace, "blob_root.glb")
    result = call("rigforge_export_godot", {"rig": rig.name, "path": path,
                                            "actions": [ACTION], "root_motion": True,
                                            "lods": False})
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)
    check("it exported", os.path.isfile(path) and os.path.getsize(path) > 1024, path)
    report = result.get("root_motion") or {}
    check("the root-motion pass reports what it did", "applied" in report, str(report))
    if report.get("applied"):
        check("it moved the travel onto the root bone", report.get("bone") == "root",
              str(report))
        doc = parse_gltf(path)
        nodes = doc.get("nodes", [])
        animations = doc.get("animations", [])
        if check("the clip is there", bool(animations), str(animations)):
            targets = {nodes[c["target"]["node"]].get("name")
                       for c in animations[0].get("channels", [])}
            check("the root bone is animated", "root" in targets,
                  str(sorted(targets)[:6]))
    else:
        note("root motion did not apply: %s" % report.get("reason"))
    check("only the named action was exported", result["actions"] == [ACTION],
          str(result["actions"]))
    check("LODs stayed out when asked to", not any("-lod" in name for name in
                                                   result.get("meshes", [])),
          str(result.get("meshes")))


def test_export_errors(rig, workspace):
    section("export refusals are clean")
    reply = call("rigforge_export_godot",
                 {"rig": rig.name, "path": os.path.join(workspace, "nope.glb"),
                  "actions": ["no-such-action"]}, expect_error=True)
    check("an unknown action name is an error that lists the real ones",
          reply.get("status") == "error" and ACTION in (reply.get("message") or ""),
          reply.get("message"))
    check("the failed export left nothing behind",
          "FORGE_EXPORT_TEMP" not in bpy.data.collections
          and not [n for n in bpy.data.objects.keys() if "_forge_export" in n
                   or "_forge_src" in n],
          str([n for n in bpy.data.objects.keys() if "forge" in n]))


def test_other_archetypes(retopo):
    """The quadruped table and the full-human preset, on throwaway copies.

    Run last and on duplicates on purpose: placing a metarig replaces the one
    the rest of this suite generated from.
    """
    section("other archetypes and presets")
    made = []
    for name, params in (("Quad", {"archetype": "quadruped"}),
                         ("Detailed", {"archetype": "biped", "preset": "human"})):
        copy = retopo.copy()
        copy.data = retopo.data.copy()
        copy.name = name
        copy.parent = None
        for modifier in list(copy.modifiers):
            copy.modifiers.remove(modifier)
        bpy.context.scene.collection.objects.link(copy)
        bpy.context.view_layer.update()
        made.append(copy)
        payload = {"object": copy.name}
        payload.update(params)
        result = call("rigforge_metarig", payload)
        meta = bpy.data.objects.get(result["metarig"])
        check("%s: a metarig was built" % name, meta is not None, result["metarig"])
        if meta is None:
            continue
        note("%s: preset %s (%s), %d bones, %d tag(s) mapped"
             % (name, result["preset"], result["preset_reason"], result["bone_count"],
                len(result["mapping"])))
        if name == "Quad":
            check("quadruped uses Rigify's quadruped template",
                  result["preset"] == "quadruped"
                  and "quadruped" in result["metarig_operator"],
                  "%s / %s" % (result["preset"], result["metarig_operator"]))
            bones = {bone.name for bone in meta.data.bones}
            check("it has front and rear legs", {"front_thigh.L", "thigh.L"} <= bones)
            check("the arm tags drove the front legs",
                  "front_thigh.L" in (result["mapping"].get("Arm.L") or []),
                  str(result["mapping"].get("Arm.L")))
            check("the leg tags drove the rear legs",
                  "thigh.L" in (result["mapping"].get("Leg.L") or []),
                  str(result["mapping"].get("Leg.L")))
            check("a body with no Tail tag is a warning, not a failure",
                  any("tail" in w.lower() for w in result.get("warnings") or [])
                  or "Tail" not in result["mapping"],
                  str(result.get("warnings")))
        else:
            check("an explicit preset overrides the automatic choice",
                  result["preset"] == "human" and result["preset_reason"] == "explicit",
                  "%s / %s" % (result["preset"], result["preset_reason"]))
            check("the full human really is the big template",
                  result["bone_count"] > 100, str(result["bone_count"]))
            bones = {bone.name for bone in meta.data.bones}
            check("its face and finger bones came along and were scaled with it",
                  {"jaw", "f_index.01.L"} <= bones)

    for copy in made:
        meta = bpy.data.objects.get(copy.name + "_metarig")
        if meta is not None:
            bpy.data.objects.remove(meta, do_unlink=True)
        bpy.data.objects.remove(copy, do_unlink=True)


def test_panels():
    section("panel wiring")
    import re

    from forge.ui import panels

    for name in ("VIEW3D_PT_forge_rig", "VIEW3D_PT_forge_godot"):
        cls = getattr(bpy.types, name, None)
        if check("%s is registered" % name, cls is not None):
            check("%s hangs off the RigForge panel" % name,
                  cls.bl_parent_id == "VIEW3D_PT_forge_rigforge", cls.bl_parent_id)

    source = open(panels.__file__, "r", encoding="utf-8").read()
    state = bpy.context.scene.forge_rigforge
    unknown = sorted({name for name in re.findall(r'\.prop\(rf,\s*"([a-z_]+)"', source)
                      if not hasattr(state, name)})
    check("every RigForge panel property exists on the scene props", not unknown, str(unknown))
    operators = sorted(set(re.findall(r'\.operator\("(forge\.rf_[a-z_]+)"', source)))
    missing = [name for name in operators
               if not hasattr(bpy.ops.forge, name.split(".")[1])]
    check("every RigForge button maps to a registered operator", not missing, str(missing))
    for name in ("forge.rf_metarig", "forge.rf_generate_rig", "forge.rf_weights",
                 "forge.rf_export_godot"):
        check("the panel offers %s" % name, name in operators, str(operators))


def test_operators(retopo, workspace):
    section("panel operators drive the same code")
    from forge.tools import rigforge as rf

    state = bpy.context.scene.forge_rigforge
    bpy.context.view_layer.objects.active = retopo
    rf.pull_meta(retopo, state)

    state.max_influences = 4
    result = bpy.ops.forge.rf_weights(action="report")
    check("the Weights button finished", "FINISHED" in result, str(result))
    check("and wrote a status line", bool(state.status) and not state.status_is_error,
          state.status)

    state.export_path = os.path.join(workspace, "panel.glb")
    state.export_actions = "all"
    state.root_motion = False
    result = bpy.ops.forge.rf_export_godot()
    check("the Export button finished", "FINISHED" in result, str(result))
    check("and wrote a file", os.path.isfile(state.export_path), state.export_path)
    check("the status line says what happened", "Exported" in state.status, state.status)

    state.export_path = ""
    try:
        failed = bpy.ops.forge.rf_export_godot()
    except RuntimeError:
        failed = {"CANCELLED"}
    check("an empty path is refused into the status line, not a traceback",
          "CANCELLED" in failed and state.status_is_error, state.status)


def test_server_frees_its_port():
    section("server shutdown")
    from forge import server as forge_server

    forge_server.stop_server()
    check("the server reports itself stopped", not forge_server.is_running())
    probe = socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", PORT))
        freed = True
    except OSError as exc:
        freed = False
        note(str(exc))
    finally:
        probe.close()
    check("port %d is free again" % PORT, freed)


# --- entry point ------------------------------------------------------------

def main():
    print("Forge add-on Phase 4 (RigForge rig + Godot export) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_phase4_test_")
    try:
        obj, regions = build_tagged_biped()
        note("sculpt: %d faces, regions %s"
             % (len(obj.data.polygons), {k: len(v) for k, v in sorted(regions.items())}))
        test_setup(obj, regions)
        retopo = test_retopo(obj)
        if retopo is None:
            raise AssertionError("no retopo mesh; the rest of the suite needs one")

        meta, _meta_result = test_metarig(retopo)
        test_metarig_needs_tags()
        if meta is None:
            raise AssertionError("no metarig; the rest of the suite needs one")

        rig, _gen = test_generate(meta, retopo)
        if rig is None:
            raise AssertionError("no rig; the rest of the suite needs one")
        test_weights_command(retopo, rig)
        test_action(rig)
        test_export(rig, retopo, workspace)
        test_export_root_motion(rig, workspace)
        test_export_errors(rig, workspace)
        test_panels()
        test_operators(retopo, workspace)
        test_other_archetypes(retopo)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_server_frees_its_port()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        shutil.rmtree(workspace, ignore_errors=True)

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
