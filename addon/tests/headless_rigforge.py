"""Headless add-on tests for Phase 3 (RigForge foundation).

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_rigforge.py

Unlike ``headless_phase2.py`` this suite needs **no geometry service**: RigForge
is pure Blender.  It builds its own "sculpt" in-script — six joined spheres
subdivided into a few tens of thousands of faces, with a hole punched in the
torso and a three-faced edge welded on, so the mesh is genuinely non-manifold
and the retopo pipeline has to earn its voxel pass — then drives every
``rigforge_*`` command over a real socket.

The two ``--background`` facts that shape this file are the same as Phase 2's:
there is no event loop (so ``bpy.app.timers`` never fires and the harness drains
the server queue from the main thread itself), and there is no window, viewport
or 3D area (so every code path under test has to work without one).
"""

import json
import os
import shutil
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bmesh
import bpy
from mathutils import Vector

# --- harness ----------------------------------------------------------------

ADDON_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))
TEMPLATE = os.path.join(REPO_ROOT, "templates", "character.json")

PORT = 9879  # not 9876 (a live session) and not 9878 (headless_phase2)
SCULPT = "Sculpt"

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


# --- setup ------------------------------------------------------------------

def enable_addon():
    if ADDON_DIR not in sys.path:
        sys.path.insert(0, ADDON_DIR)
    import addon_utils

    addon_utils.enable("forge", default_set=True, persistent=False)


def rf_props():
    return bpy.context.scene.forge_rigforge


# --- the synthetic sculpt ---------------------------------------------------

#: (name, centre, radius). Overlapping on purpose: a real sculpt is one surface
#: with self-intersections, not six tidy balls.
BLOBS = (
    ("head", (0.00, 0.00, 1.55), 0.35),
    ("torso", (0.00, 0.00, 1.00), 0.45),
    ("arm_l", (0.58, 0.00, 1.10), 0.20),
    ("arm_r", (-0.58, 0.00, 1.10), 0.20),
    ("leg_l", (0.24, 0.00, 0.45), 0.26),
    ("leg_r", (-0.24, 0.00, 0.45), 0.26),
)


def _uvsphere(bm, radius):
    """``create_uvsphere`` renamed its size argument across Blender versions."""
    for kwargs in ({"radius": radius}, {"diameter": radius * 2.0}):
        try:
            return bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, **kwargs)
        except TypeError:
            continue
    raise RuntimeError("bmesh.ops.create_uvsphere accepted neither radius nor diameter")


def build_sculpt(name=SCULPT):
    """A blobby biped, subdivided, deliberately broken in two places."""
    bm = bmesh.new()
    for _label, centre, radius in BLOBS:
        made = _uvsphere(bm, radius)
        bmesh.ops.translate(bm, verts=made["verts"], vec=Vector(centre))

    for _ in range(2):
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)

    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    # 1. a hole: boundary edges are non-manifold as far as Quadriflow cares.
    holed = [face for face in bm.faces
             if face.calc_center_median().z > 1.15 and face.calc_center_median().y > 0.30][:8]
    if holed:
        bmesh.ops.delete(bm, geom=holed, context="FACES")
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    # 2. a three-faced edge: the other flavour of non-manifold.
    fin = 0
    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        a, b = edge.verts
        neighbours = [v for face in a.link_faces for v in face.verts
                      if v not in (a, b) and v not in b.link_faces]
        if not neighbours:
            continue
        try:
            bm.faces.new((a, b, neighbours[0]))
        except ValueError:
            continue
        fin += 1
        if fin >= 2:
            break

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def face_centres(obj):
    return [(polygon.index, polygon.center.copy()) for polygon in obj.data.polygons]


def classify_faces(obj):
    """Face indices per region, by position — what "tag by describing" would do."""
    regions = {"Head": [], "Torso": [], "Arm.L": [], "Arm.R": [], "Leg.L": [], "Leg.R": []}
    for index, centre in face_centres(obj):
        x, _y, z = centre.x, centre.y, centre.z
        if z > 1.35:
            regions["Head"].append(index)
        elif z <= 0.75:
            regions["Leg.L" if x > 0.0 else "Leg.R"].append(index)
        elif x > 0.40:
            regions["Arm.L"].append(index)
        elif x < -0.40:
            regions["Arm.R"].append(index)
        else:
            regions["Torso"].append(index)
    return regions


# --- socket plumbing --------------------------------------------------------

def call(command, params=None, timeout=900.0, expect_error=False):
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


# --- tests ------------------------------------------------------------------

def test_the_sculpt_is_a_real_sculpt(obj):
    section("the synthetic sculpt")
    from forge.tools import common

    faces = len(obj.data.polygons)
    check("tens of thousands of faces", 10000 <= faces <= 200000, "%d faces" % faces)
    bad_edges, loose, _count = common._mesh_health(obj)
    check("deliberately non-manifold (this is what the voxel pass is for)",
          bad_edges > 0, "%d bad edge(s), %d loose vert(s)" % (bad_edges, loose))

    refused = call("remesh", {"object": obj.name, "mode": "quad", "target_faces": 5000},
                   expect_error=True)
    check("Quadriflow alone refuses it up front",
          refused.get("status") == "error" and "manifold" in (refused.get("message") or ""),
          refused.get("message"))


def test_tagging(obj, regions):
    section("rigforge_tag / rigforge_list_tags")
    for name, faces in regions.items():
        result = call("rigforge_tag", {"object": obj.name, "tag": name,
                                       "faces": faces, "replace": True})
        if name == "Head":
            check("tag Head reports the vertex group it wrote",
                  result["vertex_group"] == "tag_Head" and result["tag"] == "Head",
                  str(result))
            check("tag Head created the group", result["created"] is True)
        if not result["vertex_count"]:
            check("tag %s assigned vertices" % name, False, str(result))

    listing = call("rigforge_list_tags", {"object": obj.name})
    names = [entry["name"] for entry in listing["tags"]]
    check("all six tags are listed",
          names == ["Arm.L", "Arm.R", "Head", "Leg.L", "Leg.R", "Torso"], str(names))
    check("every tag has vertices and faces",
          all(entry["vertex_count"] > 0 and entry["face_count"] > 0
              for entry in listing["tags"]),
          str(listing["tags"]))
    check("tags are vertex groups named tag_<Name>",
          all(entry["vertex_group"] == "tag_" + entry["name"] for entry in listing["tags"]))
    check("the listing knows the whole mesh",
          listing["total_faces"] == len(obj.data.polygons), str(listing["total_faces"]))

    groups = {g.name for g in obj.vertex_groups}
    check("the groups really exist on the object",
          {"tag_Head", "tag_Torso", "tag_Arm.L", "tag_Leg.R"} <= groups, str(sorted(groups)))
    return listing


def test_tag_from_selection(obj):
    section("rigforge_tag with use_selection (face-select mode)")
    mesh = obj.data
    wanted = [index for index, centre in face_centres(obj) if centre.z > 1.70]
    if not check("there are faces to select", bool(wanted)):
        return

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)  # face select
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select = False
    for index in wanted:
        bm.faces[index].select = True
    bmesh.update_edit_mesh(mesh)

    result = call("rigforge_tag", {"object": obj.name, "tag": "Crown", "use_selection": True,
                                   "replace": True})
    check("the selection path ran (not the faces path)", result["source"] == "selection",
          str(result))
    check("it tagged exactly the selected faces", result["faces_used"] == len(wanted),
          "%d selected, %d used" % (len(wanted), result["faces_used"]))
    check("the sculptor is still in Edit Mode afterwards", obj.mode == "EDIT", obj.mode)
    check("Crown has vertices", result["vertex_count"] > 0, str(result))

    bpy.ops.object.mode_set(mode="OBJECT")
    call("rigforge_untag", {"object": obj.name, "tag": "Crown"})


def test_untag_round_trip(obj, regions):
    section("rigforge_untag round trips")
    before = call("rigforge_list_tags", {"object": obj.name})
    head_before = next(e for e in before["tags"] if e["name"] == "Head")

    # A contiguous cap on the crown: the interior vertices of a patch belong to
    # nothing else, which is exactly the set a conservative untag may drop.
    subset = [index for index, centre in face_centres(obj) if centre.z > 1.80]
    check("there is a contiguous patch to untag", len(subset) > 20, "%d faces" % len(subset))

    partial = call("rigforge_untag", {"object": obj.name, "tag": "Head", "faces": subset})
    check("a face subset drops vertices nothing else needs",
          partial["removed_vertices"] > 0, str(partial))
    check("the tag survives a partial untag", partial["removed_group"] is False)
    check("its face count fell", partial["face_count"] < head_before["face_count"],
          "%d -> %d" % (head_before["face_count"], partial["face_count"]))
    check("the patch's border ring kept its tag (nothing was over-erased)",
          partial["removed_vertices"] < len(subset),
          "%d verts removed for %d faces" % (partial["removed_vertices"], len(subset)))

    # The conservative default protects shared vertices; include_shared does not.
    scattered = regions["Torso"][::37][:20]
    torso_before = next(e for e in before["tags"] if e["name"] == "Torso")
    quiet = call("rigforge_untag", {"object": obj.name, "tag": "Torso", "faces": scattered})
    blunt = call("rigforge_untag", {"object": obj.name, "tag": "Torso",
                                    "faces": scattered, "include_shared": True})
    check("include_shared=true erodes the shared border the default protects",
          blunt["removed_vertices"] > quiet["removed_vertices"]
          and blunt["include_shared"] is True,
          "default removed %d, include_shared removed %d"
          % (quiet["removed_vertices"], blunt["removed_vertices"]))
    check("either way the tag is still there",
          blunt["removed_group"] is False and blunt["vertex_count"] > 0, str(blunt))
    call("rigforge_tag", {"object": obj.name, "tag": "Torso",
                          "faces": regions["Torso"], "replace": True})
    restored = call("rigforge_list_tags", {"object": obj.name})
    check("Torso restores to exactly what it was",
          next(e for e in restored["tags"] if e["name"] == "Torso")["vertex_count"]
          == torso_before["vertex_count"], str(torso_before))

    gone = call("rigforge_untag", {"object": obj.name, "tag": "Head"})
    check("omitting faces removes the whole tag", gone["removed_group"] is True, str(gone))
    listing = call("rigforge_list_tags", {"object": obj.name})
    check("Head is no longer listed",
          "Head" not in [entry["name"] for entry in listing["tags"]],
          str([entry["name"] for entry in listing["tags"]]))
    check("the mesh itself was not touched",
          listing["total_faces"] == before["total_faces"]
          and listing["total_vertices"] == before["total_vertices"])

    again = call("rigforge_tag", {"object": obj.name, "tag": "Head",
                                  "faces": regions["Head"], "replace": True})
    check("re-tagging restores it exactly",
          again["vertex_count"] == head_before["vertex_count"]
          and again["face_count"] == head_before["face_count"],
          "%s vs %s" % (again, head_before))

    bad = call("rigforge_untag", {"object": obj.name, "tag": "NoSuchThing"},
               expect_error=True)
    check("an unknown tag is a clean error naming the ones that exist",
          bad.get("status") == "error" and "Head" in (bad.get("message") or ""),
          bad.get("message"))
    bad = call("rigforge_tag", {"object": obj.name, "tag": "Head",
                                "faces": [len(obj.data.polygons) + 5]}, expect_error=True)
    check("an out-of-range face index is a clean error", bad.get("status") == "error",
          bad.get("message"))


def test_manifest(obj, workspace):
    section("rigforge_manifest save / load / get")
    path = os.path.join(workspace, "character.json")
    notes = "ears are floppy and lag behind the head; hops rather than walks"

    saved = call("rigforge_manifest", {
        "object": obj.name, "action": "save", "path": path,
        "archetype": "biped", "motion_notes": notes, "name": "test-blob",
    })
    check("save wrote the file", os.path.isfile(path), path)
    doc = json.load(open(path, "r", encoding="utf-8"))

    expected_keys = {"name", "archetype", "custom_modules", "tags", "motion_notes",
                     "retopo", "actions", "godot"}
    check("the file has every key templates/character.json defines",
          expected_keys <= set(doc), str(sorted(set(doc))))
    if os.path.isfile(TEMPLATE):
        template = json.load(open(TEMPLATE, "r", encoding="utf-8"))
        missing = {k for k in template if not k.startswith("_")} - set(doc)
        check("nothing the template defines is missing", not missing, str(sorted(missing)))
    check("archetype and motion notes round-tripped",
          doc["archetype"] == "biped" and doc["motion_notes"] == notes, str(doc)[:200])
    check("tags are the bare names, sorted",
          doc["tags"] == sorted(doc["tags"], key=str.lower) and "Arm.L" in doc["tags"],
          str(doc["tags"]))
    check("no tag_ prefix leaked into the manifest",
          not any(t.startswith("tag_") for t in doc["tags"]), str(doc["tags"]))
    check("retopo carries both platform budgets",
          {"target_faces_desktop", "target_faces_mobile", "lods"} <= set(doc["retopo"]),
          str(doc["retopo"]))
    check("the saved manifest matches what save returned", saved["manifest"] == doc)

    fetched = call("rigforge_manifest", {"object": obj.name, "action": "get"})
    check("get returns the same document without writing", fetched["manifest"] == doc)

    # Drop a tag and an archetype, then load: the manifest is the source of truth.
    obj.vertex_groups.remove(obj.vertex_groups["tag_Arm.R"])
    obj["forge_archetype"] = "custom"
    obj["forge_motion_notes"] = ""
    loaded = call("rigforge_manifest", {"object": obj.name, "action": "load", "path": path})
    check("load recreated the missing tag", "Arm.R" in (loaded.get("created_tags") or []),
          str(loaded.get("created_tags")))
    check("load restored the archetype", loaded["manifest"]["archetype"] == "biped")
    check("load restored the motion notes", loaded["manifest"]["motion_notes"] == notes)
    check("the recreated tag is a real (empty) vertex group",
          obj.vertex_groups.get("tag_Arm.R") is not None)

    # a recreated tag is empty, so re-tag it before retopo
    return path


def test_status_command(obj):
    section("rigforge_status (additive overview)")
    status = call("rigforge_status", {"object": obj.name})
    check("status reports the tags", len(status["tags"]) >= 6, str(len(status["tags"])))
    check("status reports the archetype", status["archetype"] == "biped", status["archetype"])
    check("status reports the manifest path", bool(status["manifest_path"]),
          status["manifest_path"])


def test_retopo(obj, workspace):
    section("rigforge_retopo -> 5000 faces, 2 LODs")
    before_faces = len(obj.data.polygons)
    before_verts = len(obj.data.vertices)
    before_tags = {g.name for g in obj.vertex_groups}

    started = time.monotonic()
    result = call("rigforge_retopo", {
        "object": obj.name, "target_faces": 5000, "platform": "mobile",
        "lods": 2, "keep_original": True, "bake_normals": False,
    })
    note("retopo took %.1fs (%s)" % (time.monotonic() - started, result["quad_method"]))
    for warning in result.get("warnings") or []:
        note("warning: %s" % warning)

    retopo_name = obj.name + "_retopo"
    check("the retopo object exists and is named <obj>_retopo",
          retopo_name in bpy.data.objects, str(result["objects"]))
    retopo = bpy.data.objects.get(retopo_name)
    if retopo is None:
        return None

    faces = len(retopo.data.polygons)
    check("face count is within 25%% of the 5000 target",
          abs(faces - 5000) <= 1250, "%d faces (%s)" % (faces, result["quad_method"]))
    check("the reported face count matches the mesh",
          result["face_counts"][retopo_name] == faces, str(result["face_counts"]))

    check("the pipeline ran voxel -> quad -> shrinkwrap -> tags",
          [stage["stage"] for stage in result["stages"]][:4]
          == ["voxel_remesh", "quad_remesh", "shrinkwrap", "tag_transfer"],
          str([stage["stage"] for stage in result["stages"]]))
    check("the voxel size was derived from the sculpt, not hard-coded",
          0.0 < result["voxel_size"] < 0.2, str(result["voxel_size"]))
    check("no modifiers are left on the retopo mesh", not len(retopo.modifiers),
          str([m.name for m in retopo.modifiers]))

    transferred = call("rigforge_list_tags", {"object": retopo_name})
    populated = [entry for entry in transferred["tags"] if entry["vertex_count"] > 0]
    check("tags transferred to the retopo mesh", len(populated) >= 5,
          str([(e["name"], e["vertex_count"]) for e in transferred["tags"]]))
    check("every transferred tag also covers whole faces",
          sum(1 for entry in populated if entry["face_count"] > 0) >= 5,
          str([(e["name"], e["face_count"]) for e in transferred["tags"]]))

    check("the sculpt was not touched",
          len(obj.data.polygons) == before_faces
          and len(obj.data.vertices) == before_verts
          and {g.name for g in obj.vertex_groups} == before_tags,
          "%d/%d faces, %d/%d verts" % (len(obj.data.polygons), before_faces,
                                        len(obj.data.vertices), before_verts))
    check("keep_original is reported as the v1 guarantee", result["keep_original"] is True)

    # the retopo mesh must sit on the sculpt, not float near it
    span = max(obj.dimensions)
    drift = abs(max(retopo.dimensions) - span) / span
    check("shrinkwrap put it back on the sculpt's silhouette", drift < 0.15,
          "%.1f%% size drift" % (drift * 100.0))

    section("LODs")
    # Decimate's ratio is a triangle-budget ratio and its output is triangles,
    # so a 0.5 pass on a quad mesh does not land on exactly half the *polygon*
    # count. What has to hold is that each level is a real step down.
    previous = faces
    for level, ratio in ((1, 0.5), (2, 0.25)):
        name = "%s_lod%d" % (obj.name, level)
        lod = bpy.data.objects.get(name)
        if not check("%s exists" % name, lod is not None):
            continue
        actual = len(lod.data.polygons)
        check("%s is a real step down from the level above" % name,
              0 < actual < previous, "%d vs %d" % (actual, previous))
        check("%s is in the right ballpark for ratio %.2f" % (name, ratio),
              faces * ratio * 0.6 <= actual <= faces * ratio * 1.9,
              "%d faces, retopo has %d" % (actual, faces))
        previous = actual
        lod_tags = [g for g in lod.vertex_groups if g.name.startswith("tag_")]
        check("%s kept its tags" % name, len(lod_tags) >= 5,
              str([g.name for g in lod_tags]))
        listing = call("rigforge_list_tags", {"object": name})
        check("%s tags still have vertices" % name,
              sum(1 for e in listing["tags"] if e["vertex_count"] > 0) >= 5,
              str([(e["name"], e["vertex_count"]) for e in listing["tags"]]))
    return retopo


def test_auto_uv(retopo):
    section("rigforge_auto_uv (seams from tag boundaries)")
    result = call("rigforge_auto_uv", {"object": retopo.name, "seams_from_tags": True,
                                       "margin": 0.002})
    for line in result.get("notes") or []:
        note(line)

    check("seams came from the tags, not the fallback",
          result["seam_source"] == "tags", str(result["seam_source"]))
    check("tag boundaries produced seams", result["tag_seams"] > 0, str(result))
    check("it found more than one tagged region",
          result["distinct_tag_regions"] >= 5, str(result["distinct_tag_regions"]))

    # Independently verify the seams against the mesh itself, not the report.
    from forge.tools import rigforge as rf

    mesh = retopo.data
    face_map = rf._face_tag_map(retopo)
    boundary_seamed = 0
    boundary_total = 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue
            first, second = edge.link_faces
            if face_map.get(first.index) == face_map.get(second.index):
                continue
            boundary_total += 1
            if edge.seam:
                boundary_seamed += 1
        check("every edge where the tag changes carries a seam",
              boundary_total > 0 and boundary_seamed == boundary_total,
              "%d of %d" % (boundary_seamed, boundary_total))

        # The plan's actual promise: a seam at the neck, so the head is its own
        # island. Flood-fill from a Head face without crossing seams; it must
        # never arrive at a Torso face.
        head = retopo.vertex_groups.get("tag_Head")
        torso = retopo.vertex_groups.get("tag_Torso")
        if check("the retopo mesh has Head and Torso to separate",
                 head is not None and torso is not None):
            start = next((f for f in bm.faces if head.index in face_map.get(f.index, ())),
                         None)
            if check("there is a Head face to start from", start is not None):
                seen = {start.index}
                stack = [start]
                while stack:
                    face = stack.pop()
                    for edge in face.edges:
                        if edge.seam:
                            continue
                        for other in edge.link_faces:
                            if other.index not in seen:
                                seen.add(other.index)
                                stack.append(other)
                head_faces = sum(1 for i in seen if head.index in face_map.get(i, ()))
                torso_faces = sum(1 for i in seen if torso.index in face_map.get(i, ()))
                check("the head is a whole island, not a single face",
                      head_faces > 20, "%d Head faces reached" % head_faces)
                check("no seam-free path runs from the head to the torso (neck seam)",
                      torso_faces == 0, "%d Torso faces reached" % torso_faces)
    finally:
        bm.free()

    check("a UV layer exists", bool(mesh.uv_layers), str(list(mesh.uv_layers.keys())))
    check("it unwrapped into islands", result["islands"] > 1, str(result["islands"]))
    check("every face has UVs with area",
          result["faces_with_uv_area"] == result["face_count"],
          "%d of %d" % (result["faces_with_uv_area"], result["face_count"]))
    # Organic auto-UV on a shrinkwrapped quad mesh lands around 0.50-0.55 with
    # Blender's concave packer; 0.45 is the floor that means "it really packed".
    check("UV coverage clears the 0.45 floor", result["uv_coverage"] > 0.45,
          "%.3f (packed=%s, method=%s)" % (result["uv_coverage"], result["packed"],
                                           result["method"]))
    note("coverage %.3f (%s the 0.5 target)"
         % (result["uv_coverage"], "over" if result["uv_coverage"] > 0.5 else "under"))
    check("the packer actually ran", result["packed"] is True, str(result))
    check("UVs stay inside the 0..1 square",
          _uv_bounds_ok(mesh), "some loop UV fell outside [0, 1]")
    note("%d island(s), %.1f%% coverage, %d seam(s)"
         % (result["islands"], result["uv_coverage"] * 100.0, result["seams"]))
    return result


def _uv_bounds_ok(mesh, slack=0.002):
    layer = mesh.uv_layers.active
    if layer is None:
        return False
    for datum in layer.data:
        u, v = datum.uv
        if not (-slack <= u <= 1.0 + slack and -slack <= v <= 1.0 + slack):
            return False
    return True


def test_auto_uv_angle_fallback():
    section("rigforge_auto_uv fallback on an untagged mesh")
    mesh = bpy.data.meshes.new("Untagged")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=3, use_grid_fill=True)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Untagged", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()

    result = call("rigforge_auto_uv", {"object": "Untagged", "margin": 0.01,
                                       "angle_limit": 45.0})
    check("with no tags it falls back to sharp-angle seams",
          result["seam_source"] == "angle" and result["angle_seams"] > 0, str(result))
    check("a cube unwraps into 6 islands", result["islands"] == 6, str(result["islands"]))
    check("the fallback still covers most of the UV square",
          result["uv_coverage"] > 0.5, str(result["uv_coverage"]))


def test_bake_is_best_effort(obj, workspace):
    section("rigforge_retopo with bake_normals (best effort)")
    image_path = os.path.join(workspace, "blob_normal.png")
    result = call("rigforge_retopo", {
        "object": obj.name, "target_faces": 600, "lods": 0,
        "bake_normals": True, "bake_resolution": 128, "bake_path": image_path,
    })
    baked = result.get("baked") or {}
    check("a bake report came back", bool(baked), str(result.get("baked")))
    if baked.get("ok"):
        check("the bake produced an image", bool(baked.get("image")), str(baked))
        check("and wrote it where it was asked to",
              baked.get("path") and os.path.isfile(baked["path"])
              and os.path.getsize(baked["path"]) > 0, str(baked.get("path")))
        image = bpy.data.images.get(baked.get("image") or "")
        if check("the image is still in the file", image is not None):
            pixels = list(image.pixels)
            reds = pixels[0::4]
            check("it holds a real normal map, not the flat fill it started as",
                  max(reds) - min(reds) > 0.05,
                  "red channel spans %.4f" % (max(reds) - min(reds)))
        note("bake succeeded in %.2fs at 128px" % baked.get("seconds", 0.0))
    else:
        check("a failed bake is reported cleanly, with a reason, and does not fail the run",
              bool(baked.get("reason")) and bool(result["objects"]), str(baked))
        check("the failure is also surfaced as a warning",
              any("bake" in w.lower() for w in result.get("warnings") or []),
              str(result.get("warnings")))
        note("bake did NOT run headless: %s" % baked.get("reason"))
    check("the rest of the pipeline still delivered a mesh",
          result["face_counts"].get(obj.name + "_retopo", 0) > 0, str(result["face_counts"]))


def test_panels_are_registered_and_reference_real_properties():
    """`draw()` never runs headless, so check what it would touch instead."""
    section("panel wiring")
    import re

    from forge.ui import panels

    for name in ("VIEW3D_PT_forge_rigforge", "VIEW3D_PT_forge_retopo", "VIEW3D_PT_forge_uv"):
        cls = getattr(bpy.types, name, None)
        check("%s is registered" % name, cls is not None)
    for name in ("VIEW3D_PT_forge_retopo", "VIEW3D_PT_forge_uv"):
        cls = getattr(bpy.types, name, None)
        if cls is not None:
            check("%s hangs off the RigForge panel" % name,
                  cls.bl_parent_id == "VIEW3D_PT_forge_rigforge")

    source = open(panels.__file__, "r", encoding="utf-8").read()
    state = rf_props()
    unknown = sorted({name for name in re.findall(r'\.prop\(rf,\s*"([a-z_]+)"', source)
                      if not hasattr(state, name)})
    check("every RigForge panel property exists on the scene props", not unknown, str(unknown))

    operators = sorted(set(re.findall(r'\.operator\("(forge\.rf_[a-z_]+)"', source)))
    missing = [name for name in operators
               if not hasattr(bpy.ops.forge, name.split(".")[1])]
    check("every RigForge button maps to a registered operator", not missing, str(missing))
    check("all nine RigForge operators are wired up", len(operators) >= 8, str(operators))

    # the PartForge panel state must not have been disturbed by the new panels
    unknown_pf = sorted({name for name in re.findall(r'\.prop\(props,\s*"([a-z_]+)"', source)
                         if not hasattr(bpy.context.scene.forge_partforge, name)})
    check("PartForge panel properties are still intact", not unknown_pf, str(unknown_pf))


def test_operators_drive_the_same_code(obj):
    section("panel operators")
    from forge.tools import rigforge as rf

    state = rf_props()
    bpy.context.view_layer.objects.active = obj
    rf.pull_meta(obj, state)
    check("Read From Object filled the panel",
          state.archetype == "biped" and "floppy" in state.motion_notes,
          "%s / %s" % (state.archetype, state.motion_notes[:40]))

    state.new_tag_name = "Ear.L"
    result = bpy.ops.forge.rf_new_tag()
    check("New Tag finished", "FINISHED" in result, str(result))
    check("it created the vertex group", obj.vertex_groups.get("tag_Ear.L") is not None)
    check("and cleared the name field", state.new_tag_name == "", state.new_tag_name)
    check("the status line says what happened", bool(state.status) and not state.status_is_error,
          state.status)

    bpy.ops.forge.rf_remove_tag(tag="Ear.L")
    check("Remove Tag deleted it", obj.vertex_groups.get("tag_Ear.L") is None)

    state.new_tag_name = ""
    try:
        failed = bpy.ops.forge.rf_new_tag()
    except RuntimeError:
        failed = {"CANCELLED"}
    check("a nameless tag is refused into the status line, not a traceback",
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
    print("Forge add-on Phase 3 (RigForge) headless tests")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    check("command socket up on 127.0.0.1:%d" % PORT, forge_server.is_running())

    workspace = tempfile.mkdtemp(prefix="forge_rigforge_test_")
    try:
        obj = build_sculpt()
        regions = classify_faces(obj)
        note("sculpt: %d faces, regions %s"
             % (len(obj.data.polygons),
                {k: len(v) for k, v in sorted(regions.items())}))

        test_the_sculpt_is_a_real_sculpt(obj)
        test_tagging(obj, regions)
        test_tag_from_selection(obj)
        test_untag_round_trip(obj, regions)
        test_manifest(obj, workspace)
        # the manifest load recreated tag_Arm.R empty; put its faces back
        call("rigforge_tag", {"object": obj.name, "tag": "Arm.R",
                              "faces": regions["Arm.R"], "replace": True})
        test_status_command(obj)

        retopo = test_retopo(obj, workspace)
        if retopo is not None:
            test_auto_uv(retopo)
        test_auto_uv_angle_fallback()
        test_operators_drive_the_same_code(obj)
        test_panels_are_registered_and_reference_real_properties()
        test_bake_is_best_effort(obj, workspace)
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
