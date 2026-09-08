"""Headless add-on tests for the GEOMETRIC GATE — ``verify_design`` and ``turntable``.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_verify.py

The socket port is **9900** (9876 belongs to a live session, 9879-9891 to the
earlier suites).  Nothing else is needed: no geometry service, no Claude CLI, no
network beyond loopback.  Every fixture is constructed here, which is the whole
design of this suite — a verifier is only worth having if its numbers can be
checked against cases whose answers are known in advance.

What is actually being proved:

1. ``verify_design`` and ``turntable`` are protocol commands, and READ-ONLY
   ones: measuring the work must never eat the artist's undo step;
2. **every claim in the report carries a credibility tier**, and only the two
   legal ones. This is the property the whole build turns on — a report that
   presented a thresholded silhouette with the same confidence as a face count
   would be worse than one that omitted it, so the tiering is asserted
   structurally, on every leaf, not spot-checked;
3. the **symmetry residual orders correctly**: an untouched mirror scores ~0, a
   mesh with one side pushed out scores materially higher, and neither is ever
   reported as a fault;
4. **UVs measured against known answers**: a freshly unwrapped cube has islands
   and no flips; a mesh with no UV layer at all is reported as *missing* (which
   is a finding for a game asset and not one for anything else), and a
   deliberately flipped face is counted;
5. **the poly budget** gates against the number that was passed in, and says how
   far over;
6. **silhouette IoU against a synthetic reference**: a cube's own front render,
   used AS the reference for that cube, must score ~1.0; the same reference
   against a sphere must score materially lower (a circle inscribed in a square
   is pi/4 = 0.785, which is the arithmetic this test pins), and against a thin
   bar, lower again;
7. **edge loops** are found from an armature when there is one and from the
   RigForge tag boundaries when there is not — the second path being the one
   that is easy to implement in a way that finds nothing at all;
8. **the turntable is a real contact sheet**: PNG magic, an IHDR whose
   dimensions are exactly ``columns x resolution`` by ``rows x resolution``,
   every tile carrying pixels, and the frames cleaned up afterwards;
9. **the scene comes back exactly as it was** — no leftover camera, the render
   engine, resolution, film transparency, colour management and Workbench
   shading all restored, every ``hide_render`` flag as it was found — after a
   turntable, after a silhouette comparison, and after a FAILED one;
10. bad input (an impossible view count, a folder as a path, a reference that
    does not exist, an unknown profile) fails with a sentence rather than a
    traceback.
"""

import json
import os
import socket as socketlib
import struct
import sys
import tempfile
import threading
import time
import traceback

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9900  # not 9876 (live session) and not 9879..9891 (every other suite)

#: Small on purpose: every pixel assertion reads the image back in Python.
SMALL = 128
TILE = 64

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


def _roundtrip(payload, timeout=240.0):
    """One socket command, pumping the main-thread queue until it replies."""
    from forge import server as forge_server

    box = {}

    def talk():
        try:
            conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=timeout)
            with conn:
                conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = conn.recv(65536)
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
        time.sleep(0.01)
    thread.join(timeout=2.0)

    if "error" in box:
        return {"status": "error", "message": "harness: %s" % box["error"]}
    return box.get("reply") or {"status": "error",
                                "message": "no reply within %.0fs" % timeout}


def verify(**params):
    return _roundtrip({"type": "verify_design", "params": params})


def turntable(**params):
    return _roundtrip({"type": "turntable", "params": params})


def preview(**params):
    return _roundtrip({"type": "render_preview", "params": params})


def command(name, **params):
    return _roundtrip({"type": name, "params": params})


# ---------------------------------------------------------------------------
# fixtures — every one with a known answer
# ---------------------------------------------------------------------------

def clear_scene():
    for obj in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass


def make_cube(name, size=0.1, location=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def make_sphere(name, radius=0.05, segments=32, rings=16, location=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=segments,
                                         ring_count=rings, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def make_limb(name, radius=0.03, depth=0.4, cuts=20):
    """A cylinder with rings along it — the shape 'edge loops at a joint' means."""
    import bmesh as _bmesh

    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, vertices=16)
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.mode_set(mode="EDIT")
    bm = _bmesh.from_edit_mesh(obj.data)
    lengthwise = [e for e in bm.edges
                  if abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6]
    _bmesh.ops.subdivide_edges(bm, edges=lengthwise, cuts=cuts,
                               use_grid_fill=False)
    _bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def attach_armature(mesh_obj, name="rig"):
    """A two-bone chain with its joint at the mesh's waist."""
    bpy.ops.object.armature_add(location=(0.0, 0.0, -0.2))
    arm = bpy.context.active_object
    arm.name = name
    bpy.ops.object.mode_set(mode="EDIT")
    bones = arm.data.edit_bones
    root = bones[0]
    root.head = (0.0, 0.0, -0.2)
    root.tail = (0.0, 0.0, 0.0)
    upper = bones.new("upper")
    upper.head = (0.0, 0.0, 0.0)
    upper.tail = (0.0, 0.0, 0.2)
    upper.parent = root
    bpy.ops.object.mode_set(mode="OBJECT")
    modifier = mesh_obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm
    bpy.context.view_layer.objects.active = mesh_obj
    return arm


def unwrap(obj):
    """Give an object a real UV layer through Blender's own unwrapper."""
    bpy.context.view_layer.objects.active = obj
    for other in bpy.data.objects:
        other.select_set(other is obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def strip_uvs(obj):
    while obj.data.uv_layers:
        obj.data.uv_layers.remove(obj.data.uv_layers[0])


# ---------------------------------------------------------------------------
# reading a PNG back
# ---------------------------------------------------------------------------

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(path):
    """``(width, height)`` straight out of the IHDR chunk, or ``None``."""
    with open(path, "rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or not head.startswith(PNG_MAGIC) or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def tile_variances(path, columns, rows, tile):
    """Per-tile pixel spread, so 'every tile has something in it' is testable."""
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError:
        return None
    try:
        import numpy

        width, height = int(image.size[0]), int(image.size[1])
        channels = int(image.channels) or 4
        flat = numpy.empty(width * height * channels, dtype="f")
        image.pixels.foreach_get(flat)
        values = flat.reshape(height, width, channels)[:, :, :3].mean(axis=2)
        out = []
        for row in range(rows):
            for column in range(columns):
                top = row * tile
                left = column * tile
                out.append(float(values[top:top + tile, left:left + tile].std()))
        return out
    finally:
        try:
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass


# ---------------------------------------------------------------------------
# credibility tiers — walked structurally, not spot-checked
# ---------------------------------------------------------------------------

LEGAL_TIERS = {"measured", "heuristic"}


def walk_claims(node, path="result"):
    """Every ``{"value": ..., "tier": ...}`` leaf in the report, with its path."""
    found = []
    if isinstance(node, dict):
        if "value" in node and "tier" in node:
            found.append((path, node))
        for key, value in node.items():
            found.extend(walk_claims(value, "%s.%s" % (path, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(walk_claims(value, "%s[%d]" % (path, index)))
    return found


# ---------------------------------------------------------------------------
# scene state — the borrow-and-return contract
# ---------------------------------------------------------------------------

_SHADING_FIELDS = (
    "light", "color_type", "single_color", "background_type", "background_color",
    "show_shadows", "show_specular_highlight", "show_cavity", "cavity_type",
    "show_object_outline", "show_xray",
)


def scene_state():
    scene = bpy.context.scene
    render = scene.render
    view_settings = getattr(scene, "view_settings", None)

    def frozen(owner, names):
        out = {}
        for name in names:
            try:
                value = getattr(owner, name)
            except (AttributeError, TypeError):
                continue
            if not isinstance(value, (str, bytes)):
                try:
                    value = tuple(round(float(v), 6) for v in value)
                except TypeError:
                    pass
            out[name] = value
        return out

    return {
        "camera": scene.camera.name if scene.camera else None,
        "cameras": sorted(c.name for c in bpy.data.cameras),
        "camera_objects": sorted(o.name for o in bpy.data.objects
                                 if o.type == "CAMERA"),
        "images": sorted(i.name for i in bpy.data.images),
        "objects": sorted(o.name for o in bpy.data.objects),
        "render": frozen(render, ("engine", "filepath", "resolution_x",
                                  "resolution_y", "resolution_percentage",
                                  "film_transparent", "use_overwrite",
                                  "use_file_extension", "use_border")),
        "image_settings": frozen(render.image_settings,
                                 ("file_format", "color_mode", "color_depth")),
        "display_aa": getattr(scene.display, "render_aa", None),
        "shading": frozen(scene.display.shading, _SHADING_FIELDS),
        "view_settings": frozen(view_settings, ("view_transform", "look",
                                                "exposure", "gamma"))
        if view_settings else {},
        "hide_render": {o.name: o.hide_render for o in bpy.data.objects},
        "selected": sorted(o.name for o in bpy.data.objects if o.select_get()),
        "active": (bpy.context.view_layer.objects.active.name
                   if bpy.context.view_layer.objects.active else None),
    }


def state_diff(before, after):
    return sorted(key for key in before if before[key] != after.get(key))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import registry

    check("verify_design is a protocol command", registry.has_command("verify_design"))
    check("turntable is a protocol command", registry.has_command("turntable"))
    for name in ("verify_design", "turntable"):
        check("%s is READ-ONLY — measuring never costs an undo step" % name,
              name in registry.READ_ONLY_COMMANDS)
        check("and push_undo refuses to push one for %s" % name,
              registry.push_undo(name) is False)
    check("every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "load_mesh", "mesh_diagnose", "render_preview",
               "capture_viewport", "rigforge_tag", "check_model", "flow_run")),
          "%d commands registered" % len(registry.command_names()))


def test_every_claim_carries_a_tier():
    section("credibility tiering (the property the build turns on)")
    clear_scene()
    obj = make_sphere("tiered")
    unwrap(obj)
    reply = verify(object="tiered", **{"for": "game"})
    if not check("verify_design succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:600]):
        return
    result = reply["result"]

    claims = walk_claims(result)
    check("the report is made of tiered claims, not bare numbers",
          len(claims) >= 12, "%d claims found" % len(claims))
    bad = [(path, entry.get("tier")) for path, entry in claims
           if entry.get("tier") not in LEGAL_TIERS]
    check("every single claim carries a legal tier", not bad, str(bad[:5]))

    tiers = {entry["tier"] for _path, entry in claims}
    check("both tiers are actually used — the distinction is real",
          tiers == LEGAL_TIERS, str(sorted(tiers)))

    check("the report defines what the tiers mean",
          set(result.get("tiers") or {}) == LEGAL_TIERS,
          str(result.get("tiers")))
    check("every axis carries a tier too",
          all("tier" in axis for axis in (result.get("axes") or {}).values()),
          str([k for k, v in (result.get("axes") or {}).items() if "tier" not in v]))
    check("the verdict lines are stamped with the tier they came from",
          all(line.startswith("[") for line in result.get("verdict") or []),
          str(result.get("verdict"))[:300])

    heuristics = [path for path, entry in claims if entry["tier"] == "heuristic"]
    check("the judgement calls are the ones marked heuristic",
          any("overlap" in path for path in heuristics)
          or any("near_symmetric" in path for path in heuristics),
          str(heuristics[:6]))


def test_symmetry_orders_correctly():
    section("symmetry residual — reported, never judged")
    clear_scene()
    symmetric = make_sphere("symmetric")
    bpy.ops.object.duplicate()
    asymmetric = bpy.context.active_object
    asymmetric.name = "asymmetric"
    # Pushed far enough to be unambiguous: the point of the fixture is the
    # ORDERING, and a nudge that lands either side of the near-symmetric
    # convention would be testing the threshold rather than the measurement.
    for vertex in asymmetric.data.vertices:
        if vertex.co.x > 0.02:
            vertex.co.x += 0.05
    asymmetric.data.update()

    residuals = {}
    for name in ("symmetric", "asymmetric"):
        reply = verify(object=name)
        if not check("verify_design ran on %s" % name,
                     reply.get("status") == "success",
                     str(reply.get("message"))[:400]):
            return
        axis = reply["result"]["axes"]["symmetry"]
        residuals[name] = axis
        check("%s: symmetry is REPORTED, not gated" % name,
              axis["status"] == "reported" and axis.get("judged") is False,
              str(axis["status"]))

    low = residuals["symmetric"]["detail"]["mean_mm"]["value"]
    high = residuals["asymmetric"]["detail"]["mean_mm"]["value"]
    check("an untouched mirror scores ~0 mm", low < 0.01, str(low))
    check("pushing one side out scores materially higher", high > low + 1.0,
          "%s vs %s mm" % (low, high))
    check("the symmetric one is called near-symmetric",
          residuals["symmetric"]["detail"]["near_symmetric"]["value"] is True)
    check("the asymmetric one is not",
          residuals["asymmetric"]["detail"]["near_symmetric"]["value"] is False,
          str(residuals["asymmetric"]["detail"]["fraction_of_size"]))
    for name in ("symmetric", "asymmetric"):
        result = verify(object=name)["result"]
        check("%s: symmetry never blocks 'done' — it is not a gated axis" % name,
              "symmetry" not in result["gated_axes"]
              and "symmetry" not in (result.get("attention") or []),
              "gated=%s attention=%s" % (result["gated_axes"],
                                         result.get("attention")))
        check("%s: and its verdict line is marked report-only" % name,
              any(line.startswith("[report only]") for line in result["verdict"])
              or not any("Mirror residual" in line for line in result["verdict"]),
              str(result["verdict"]))

    # the near-symmetric CALL is a convention and says so; the distance is not
    check("the near-symmetric verdict is tiered as heuristic",
          residuals["symmetric"]["detail"]["near_symmetric"]["tier"] == "heuristic")
    check("the millimetre distance itself is measured",
          residuals["symmetric"]["detail"]["mean_mm"]["tier"] == "measured")


def test_uvs_measured_and_missing():
    section("UV metrics — with UVs, without, and with a flipped face")
    clear_scene()
    unwrapped = make_cube("unwrapped")
    unwrap(unwrapped)
    bare = make_cube("bare", location=(0.5, 0.0, 0.0))
    strip_uvs(bare)

    reply = verify(object="unwrapped", **{"for": "game"})
    if not check("verify_design ran on the unwrapped cube",
                 reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    uv = reply["result"]["axes"]["uv"]
    check("a mesh with UVs reports them present", uv["present"]["value"] is True)
    detail = uv["detail"]
    check("islands are counted and there is at least one",
          isinstance(detail["islands"]["value"], int)
          and detail["islands"]["value"] >= 1,
          str(detail["islands"]))
    check("a fresh smart-project has no flipped faces",
          detail["flipped_faces"]["value"] == 0, str(detail["flipped_faces"]))
    check("nothing lands outside the 0..1 square",
          detail["out_of_bounds_loops"]["value"] == 0,
          str(detail["out_of_bounds_loops"]))
    check("the overlap estimate is honest about being an estimate",
          detail["overlap"]["tier"] == "heuristic"
          and "raster" in (detail["overlap"].get("note") or ""),
          str(detail["overlap"])[:200])
    check("area distortion comes back as a ratio around 1",
          detail["area_distortion"]["value"] is not None,
          str(detail["area_distortion"]))

    reply = verify(object="bare", **{"for": "game"})
    uv = reply["result"]["axes"]["uv"]
    check("a game asset with NO UVs is flagged for attention",
          uv["status"] == "attention" and uv["present"]["value"] is False,
          str(uv))
    check("and the report names the tool that would fix it",
          "rigforge_auto_uv" in uv["summary"], uv["summary"])

    reply = verify(object="bare", **{"for": "print"})
    uv = reply["result"]["axes"]["uv"]
    check("the same mesh is NOT flagged when it is going to a printer",
          uv["status"] == "not_applicable", str(uv["status"]))

    # a deliberately flipped island: reverse one face's UV winding
    mesh = unwrapped.data
    layer = mesh.uv_layers.active
    polygon = mesh.polygons[0]
    loops = list(range(polygon.loop_start, polygon.loop_start + polygon.loop_total))
    original = [tuple(layer.data[i].uv) for i in loops]
    for slot, index in enumerate(loops):
        layer.data[index].uv = original[len(loops) - 1 - slot]
    mesh.update()
    reply = verify(object="unwrapped", **{"for": "game"})
    detail = reply["result"]["axes"]["uv"]["detail"]
    check("a face wound against the majority is counted as flipped",
          detail["flipped_faces"]["value"] >= 1, str(detail["flipped_faces"]))
    check("and a flipped face is enough to want attention",
          reply["result"]["axes"]["uv"]["status"] == "attention",
          reply["result"]["axes"]["uv"]["summary"])


def test_poly_budget():
    section("poly budget")
    clear_scene()
    obj = make_sphere("budgeted", segments=32, rings=16)
    faces = len(obj.data.polygons)
    note("the fixture has %d faces" % faces)

    reply = verify(object="budgeted", poly_budget=faces * 4)
    axis = reply["result"]["axes"]["poly_budget"]
    check("inside the budget passes", axis["status"] == "pass", axis["summary"])
    check("and it says how much of the budget was used",
          "%" in axis["summary"], axis["summary"])

    reply = verify(object="budgeted", poly_budget=max(1, faces // 4))
    axis = reply["result"]["axes"]["poly_budget"]
    check("over the budget wants attention", axis["status"] == "attention",
          axis["summary"])
    check("and it says how far over, as a multiple",
          "x over" in axis["summary"], axis["summary"])
    check("the ratio is a measured claim",
          axis["ratio"]["tier"] == "measured" and axis["ratio"]["value"] > 3.5,
          str(axis["ratio"]))
    check("poly_budget is in the gated list for 'game'",
          "poly_budget" in (verify(object="budgeted",
                                   **{"for": "game"})["result"]["gated_axes"]))

    reply = verify(object="budgeted", **{"for": "print"})
    axis = reply["result"]["axes"]["poly_budget"]
    check("a print has no polygon budget by default",
          axis["status"] == "not_applicable", axis["summary"])
    check("but the face count is still measured and reported",
          axis["faces"]["value"] == faces, str(axis["faces"]))


def test_profiles_gate_different_axes():
    section("profiles")
    clear_scene()
    obj = make_sphere("profiled")
    strip_uvs(obj)

    game = verify(object="profiled", **{"for": "game"})["result"]
    printing = verify(object="profiled", **{"for": "print"})["result"]
    anything = verify(object="profiled")["result"]

    check("game gates loops, UVs and the budget",
          {"uv", "loops", "poly_budget"} <= set(game["gated_axes"]),
          str(game["gated_axes"]))
    check("print does not gate UVs or loops",
          not ({"uv", "loops"} & set(printing["gated_axes"])),
          str(printing["gated_axes"]))
    check("print points at the tool that really answers print readiness",
          any("partforge_check" in n for n in printing["notes"]),
          str(printing["notes"]))
    check("print does NOT grow a second opinion about wall thickness",
          not any("wall thickness" in json.dumps(axis).lower()
                  for key, axis in printing["axes"].items()
                  if key != "defects"),
          "a duplicate print check would be worse than none")
    check("'any' is the default profile", anything["for"] == "any")
    check("and it gates only the universal axes",
          set(anything["gated_axes"]) == {"defects", "silhouette"},
          str(anything["gated_axes"]))
    check("an unknown profile is refused by name",
          verify(object="profiled", **{"for": "sculpture"}).get("status") == "error")


def test_silhouette_against_a_synthetic_reference(tmpdir):
    section("silhouette IoU against a synthetic reference")
    clear_scene()
    make_cube("cube")
    make_sphere("sphere", location=(1.0, 0.0, 0.0))
    bar = make_cube("bar", location=(2.0, 0.0, 0.0))
    bar.scale = (0.15, 1.0, 3.0)
    bpy.context.view_layer.update()

    reference = os.path.join(tmpdir, "cube-front.png")
    reply = preview(objects=["cube"], view="front", resolution=256, path=reference)
    if not check("rendered a cube to use AS the reference",
                 reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    reference = reply["result"]["path"]

    scores = {}
    for name in ("cube", "sphere", "bar"):
        reply = verify(object=name, reference_image=reference)
        if not check("verify_design ran on %s with a reference" % name,
                     reply.get("status") == "success",
                     str(reply.get("message"))[:500]):
            return
        axis = reply["result"]["axes"]["silhouette"]
        scores[name] = axis["detail"]["iou"]["value"]
        note("%s: IoU %s" % (name, scores[name]))

    check("a cube against its own render scores ~1.0", scores["cube"] > 0.97,
          str(scores["cube"]))
    check("a sphere against a cube scores materially lower",
          scores["sphere"] < scores["cube"] - 0.1,
          "%s vs %s" % (scores["sphere"], scores["cube"]))
    check("and it lands near pi/4 = 0.785, which is the arithmetic for a "
          "circle inscribed in a square",
          0.70 < scores["sphere"] < 0.86, str(scores["sphere"]))
    check("a thin bar scores lower still", scores["bar"] < scores["sphere"] - 0.3,
          "%s vs %s" % (scores["bar"], scores["sphere"]))

    cube = verify(object="cube", reference_image=reference)["result"]
    axis = cube["axes"]["silhouette"]
    check("the matching silhouette passes the gate", axis["status"] == "pass",
          axis["summary"])
    check("a mismatched one wants attention",
          verify(object="bar",
                 reference_image=reference)["result"]["axes"]["silhouette"]["status"]
          == "attention")
    check("the aspect delta is reported separately from the shape score",
          axis["detail"]["aspect_delta"]["value"] is not None,
          str(axis["detail"]["aspect_delta"]))
    check("the centroid delta too", axis["detail"]["centroid_delta"]["value"]
          is not None)
    check("the report says HOW the reference mask was obtained",
          axis["detail"]["reference_mask"]["value"] in
          ("alpha", "background threshold"),
          str(axis["detail"]["reference_mask"]))
    check("an opaque reference is honest that it was thresholded, not exact",
          axis["detail"]["reference_mask"]["tier"] == "heuristic"
          and "PLAIN BACKGROUND" in axis["detail"]["reference_mask"]["note"],
          str(axis["detail"]["reference_mask"])[:200])
    check("the IoU number itself is measured",
          axis["detail"]["iou"]["tier"] == "measured")
    check("the method is spelled out in the result",
          "cropped" in axis["detail"]["method"], axis["detail"]["method"][:120])
    check("background uniformity comes back as a number, not a claim of trust",
          axis["detail"]["background_uniformity"]["value"] is not None)

    check("no reference means the axis says so rather than passing silently",
          verify(object="cube")["result"]["axes"]["silhouette"]["status"]
          == "not_applicable")
    check("and it names the parameter that would measure it",
          "reference_image" in
          verify(object="cube")["result"]["axes"]["silhouette"]["summary"])

    reply = verify(object="cube",
                   reference_image=os.path.join(tmpdir, "not-here.png"))
    check("a reference that does not exist fails with a sentence",
          reply.get("status") == "error"
          and "No image at" in str(reply.get("message")),
          str(reply.get("message"))[:200])


def test_silhouette_confidence_on_a_busy_background(tmpdir):
    section("silhouette confidence — the honesty caveat, measured")
    clear_scene()
    make_cube("cube")

    # A reference with NOISE in it: the border is not one flat colour, so the
    # threshold cannot be trusted and the report has to say so rather than
    # quoting an IoU as though it were a fact.
    import numpy

    size = 96
    rng = numpy.random.default_rng(7)
    noisy = rng.random((size, size, 4)).astype("f")
    noisy[:, :, 3] = 1.0
    image = bpy.data.images.new("busy", width=size, height=size, alpha=True)
    image.pixels.foreach_set(noisy.reshape(-1))
    busy = os.path.join(tmpdir, "busy.png")
    image.filepath_raw = busy
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)

    reply = verify(object="cube", reference_image=busy)
    if reply.get("status") != "success":
        # An all-noise reference can legitimately have no separable subject at
        # all; refusing with a sentence naming the fix is the correct answer.
        check("a reference with no separable subject is refused with a sentence",
              "plain" in str(reply.get("message")).lower(),
              str(reply.get("message"))[:300])
        return
    axis = reply["result"]["axes"]["silhouette"]
    check("a busy background drops the confidence rather than being ignored",
          axis["confidence"] == "low", str(axis["confidence"]))
    check("and it says WHY, in a sentence",
          bool(axis["detail"]["confidence_reasons"]),
          str(axis["detail"]["confidence_reasons"]))
    check("a low-confidence silhouette is tiered heuristic, not measured",
          axis["tier"] == "heuristic", axis["tier"])
    check("the summary shouts the caveat rather than burying it",
          "LOW CONFIDENCE" in axis["summary"], axis["summary"])
    check("the reason reaches the top-level notes too",
          any("silhouette confidence" in n for n in reply["result"]["notes"]),
          str(reply["result"]["notes"]))


def test_loops_from_an_armature_and_from_tags():
    section("edge loops in deformation zones")
    clear_scene()
    limb = make_limb("limb", cuts=20)
    attach_armature(limb)

    reply = verify(object="limb", **{"for": "game"})
    if not check("verify_design ran on a rigged limb",
                 reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    axis = reply["result"]["axes"]["loops"]
    check("the joints came off the armature", axis["detail"]["source"] == "rig",
          str(axis["detail"]["source"]))
    check("a well-subdivided limb carries enough loops at the joint",
          axis["status"] == "pass", axis["summary"])
    check("loop counts are tiered heuristic, never measured",
          all(zone["loops"]["tier"] == "heuristic"
              for zone in axis["detail"]["zones"]),
          str(axis["detail"]["zones"])[:200])
    check("the method is spelled out rather than implied",
          "approximate" in axis["detail"]["method"],
          axis["detail"]["method"][:140])

    clear_scene()
    bare = make_limb("bare", cuts=0)
    attach_armature(bare)
    reply = verify(object="bare", **{"for": "game"})
    axis = reply["result"]["axes"]["loops"]
    check("a cylinder with no rings at the joint wants attention",
          axis["status"] == "attention", axis["summary"])
    check("and it names the joint and its count", "upper" in axis["summary"],
          axis["summary"])

    # No armature at all: the tag boundary IS the deformation zone, and reading
    # only edges that CROSS between tags finds nothing, because Forge's own
    # tagging shares the seam ring between both tags.
    clear_scene()
    tagged = make_limb("tagged", cuts=0)
    lower = [p.index for p in tagged.data.polygons if p.center.z < 0]
    upper = [p.index for p in tagged.data.polygons if p.center.z >= 0]
    command("rigforge_tag", object="tagged", tag="Lower", faces=lower)
    command("rigforge_tag", object="tagged", tag="Upper", faces=upper)

    reply = verify(object="tagged", **{"for": "game"})
    axis = reply["result"]["axes"]["loops"]
    check("with no armature, the tag boundaries are read instead",
          axis["status"] in ("pass", "attention")
          and axis["detail"]["source"] == "tag boundaries",
          str(axis.get("detail", {}).get("source")))
    check("and the zone is named after the two tags it joins",
          any("Lower" in zone["joint"] and "Upper" in zone["joint"]
              for zone in axis["detail"]["zones"]),
          str([z["joint"] for z in axis["detail"]["zones"]]))

    clear_scene()
    make_sphere("naked")
    reply = verify(object="naked", **{"for": "game"})
    axis = reply["result"]["axes"]["loops"]
    check("no armature and no tags is n/a, not a failure",
          axis["status"] == "not_applicable", axis["summary"])
    check("and it names both things that would make it measurable",
          "rigforge_tag" in axis["summary"] and "armature" in axis["summary"],
          axis["summary"])


def test_defects_are_composed_not_reimplemented():
    section("defects — mesh_diagnose composed in")
    clear_scene()
    # A cube, not a sphere: a UV sphere's poles really are several times denser
    # than its equator, and mesh_diagnose is right to say so — which makes a
    # sphere a poor fixture for "nothing is wrong with this".
    make_cube("clean")
    reply = verify(object="clean")
    axis = reply["result"]["axes"]["defects"]
    check("the defect axis says where it came from",
          axis.get("source") == "mesh_diagnose", str(axis.get("source")))
    check("a clean mesh passes", axis["status"] == "pass", axis["summary"])
    check("the whole mesh_diagnose result is carried, not summarised away",
          {"self_intersections", "topology", "density", "scale"}
          <= set(axis["detail"]), str(sorted(axis["detail"]))[:200])

    direct = command("mesh_diagnose", object="clean")
    check("and it is the SAME numbers mesh_diagnose gives on its own",
          direct["result"]["face_count"] == axis["detail"]["face_count"],
          "%s vs %s" % (direct["result"]["face_count"],
                        axis["detail"]["face_count"]))

    # a mesh that clips itself: two boxes sharing one object, overlapping
    clear_scene()
    a = make_cube("broken", size=0.1)
    b = make_cube("second", size=0.1, location=(0.04, 0.0, 0.0))
    bpy.context.view_layer.objects.active = a
    a.select_set(True)
    b.select_set(True)
    bpy.ops.object.join()
    reply = verify(object="broken")
    axis = reply["result"]["axes"]["defects"]
    check("a self-intersecting mesh wants attention",
          axis["status"] == "attention", axis["summary"])
    check("and it blocks the gate", reply["result"]["gate"] == "attention",
          str(reply["result"]["attention"]))


def test_turntable_contact_sheet(tmpdir):
    section("turntable — the standardised judging rig")
    clear_scene()
    make_sphere("spun")

    path = os.path.join(tmpdir, "sheet.png")
    reply = turntable(object="spun", views=9, resolution=TILE, path=path)
    if not check("turntable succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:600]):
        return
    result = reply["result"]

    check("it wrote the file it named", os.path.exists(result["path"]),
          result["path"])
    with open(result["path"], "rb") as handle:
        magic = handle.read(8)
    check("with PNG magic bytes", magic == PNG_MAGIC, repr(magic))

    columns, rows = result["columns"], result["rows"]
    check("9 views laid out 3 x 3", (columns, rows) == (3, 3),
          "%dx%d" % (columns, rows))
    expected = (columns * TILE, rows * TILE)
    check("the IHDR says exactly columns x tile by rows x tile",
          png_size(result["path"]) == expected,
          "%s, wanted %s" % (png_size(result["path"]), expected))
    check("and the result reports the same size",
          tuple(result["sheet_size"]) == expected, str(result["sheet_size"]))

    variances = tile_variances(result["path"], columns, rows, TILE)
    check("every tile has an object in it, not just background",
          variances is not None and all(v > 0.005 for v in variances),
          str([round(v, 4) for v in (variances or [])]))

    check("the angles are evenly spaced around Z",
          len(result["angles_deg"]) == 9
          and abs(result["angles_deg"][1] - 40.0) < 0.01,
          str(result["angles_deg"]))
    check("the reading order is stated, not left to be guessed",
          "left to right" in result["reading_order"], result["reading_order"])
    check("the framing is fixed across every tile",
          result["fixed_framing"] is True)
    check("and the result says so in words, because it is the whole point",
          any("framed identically" in n for n in result["notes"]),
          str(result["notes"]))

    check("the individual frames are cleaned up by default",
          not any(name.startswith("sheet-frame-") for name in os.listdir(tmpdir)),
          str(sorted(os.listdir(tmpdir))))
    check("and nothing was kept in the result either", result["frames"] == [])

    kept = os.path.join(tmpdir, "kept.png")
    reply = turntable(object="spun", views=4, resolution=TILE, path=kept,
                      keep_frames=True)
    check("keep_frames leaves the tiles on disk",
          reply.get("status") == "success"
          and len(reply["result"]["frames"]) == 4
          and all(os.path.exists(f) for f in reply["result"]["frames"]),
          str(reply.get("message"))[:200])


def test_turntable_defaults_are_the_protocol_numbers(tmpdir):
    section("turntable defaults")
    clear_scene()
    make_cube("defaulted", size=0.05)
    from forge.tools import verify as verify_module

    check("the default view count is the measured 24",
          verify_module.TURNTABLE_VIEWS == 24)
    check("the default tile is the measured 256 px",
          verify_module.TURNTABLE_RESOLUTION == 256)

    # `dir` instead of `path`: the sheet still gets a real name
    reply = turntable(object="defaulted", views=4, resolution=TILE,
                      **{"dir": tmpdir})
    check("a 'dir' is enough — it names the file itself",
          reply.get("status") == "success"
          and os.path.dirname(reply["result"]["path"]) == os.path.abspath(tmpdir),
          str(reply.get("message"))[:300])
    check("and the generated name says what it is",
          "turntable-4x%d" % TILE in os.path.basename(reply["result"]["path"]),
          reply["result"]["path"])


def test_state_is_restored(tmpdir):
    section("borrow and return")
    clear_scene()
    make_sphere("subject")
    hidden = make_cube("hidden-by-the-artist", location=(1.0, 0.0, 0.0))
    hidden.hide_render = True

    reference = os.path.join(tmpdir, "ref.png")
    preview(objects=["subject"], view="front", resolution=SMALL, path=reference)

    before = scene_state()
    reply = turntable(object="subject", views=4, resolution=TILE,
                      path=os.path.join(tmpdir, "state.png"))
    check("the turntable succeeded", reply.get("status") == "success",
          str(reply.get("message"))[:300])
    after = scene_state()
    check("nothing in the scene changed after a turntable",
          not state_diff(before, after), str(state_diff(before, after)))

    before = scene_state()
    reply = verify(object="subject", reference_image=reference)
    check("verify_design with a silhouette render succeeded",
          reply.get("status") == "success", str(reply.get("message"))[:300])
    after = scene_state()
    check("nothing changed after a silhouette comparison either",
          not state_diff(before, after), str(state_diff(before, after)))
    check("the artist's own hidden object is still hidden",
          bpy.data.objects["hidden-by-the-artist"].hide_render is True)
    check("no leftover camera datablock",
          not any("Forge Verify" in c.name for c in bpy.data.cameras),
          str([c.name for c in bpy.data.cameras]))
    check("no leftover image datablock",
          not any("Forge Turntable" in i.name for i in bpy.data.images),
          str([i.name for i in bpy.data.images]))
    check("the silhouette render is not left next to the artist's picture",
          not any(n.startswith("forge-silhouette-") for n in os.listdir(tmpdir)),
          str(sorted(os.listdir(tmpdir))))

    before = scene_state()
    reply = turntable(object="subject", views=4, resolution=TILE,
                      path=os.path.join(tmpdir, "nested", "deep", "fail.png"))
    note("a nested path is created, not refused: %s" % reply.get("status"))
    after = scene_state()
    check("a run that had to make folders still restores everything",
          not state_diff(before, after), str(state_diff(before, after)))


def test_failure_also_restores(tmpdir):
    section("a failure restores too")
    clear_scene()
    make_sphere("subject")
    before = scene_state()
    reply = turntable(object="nothing-called-this", views=4, resolution=TILE,
                      path=os.path.join(tmpdir, "never.png"))
    check("an unknown object fails with a sentence",
          reply.get("status") == "error"
          and "nothing-called-this" in str(reply.get("message")),
          str(reply.get("message"))[:200])
    after = scene_state()
    check("and the scene is exactly as it was",
          not state_diff(before, after), str(state_diff(before, after)))


def test_bad_input(tmpdir):
    section("bad input says what is accepted")
    clear_scene()
    make_sphere("subject")

    cases = [
        ("no path and no dir at all", turntable(object="subject"), "path"),
        ("a folder as the path",
         turntable(object="subject", path=tmpdir), "folder"),
        ("too few views",
         turntable(object="subject", views=1, path=os.path.join(tmpdir, "a.png")),
         "views"),
        ("too many views",
         turntable(object="subject", views=500,
                   path=os.path.join(tmpdir, "a.png")), "views"),
        ("an impossible tile size",
         turntable(object="subject", resolution=4,
                   path=os.path.join(tmpdir, "a.png")), "resolution"),
        ("an impossible elevation",
         turntable(object="subject", elevation=400.0,
                   path=os.path.join(tmpdir, "a.png")), "elevation"),
        ("an unknown profile", verify(object="subject", **{"for": "sculpture"}),
         "for"),
        ("an unknown symmetry axis",
         verify(object="subject", symmetry_axis="Q"), "symmetry_axis"),
        ("examples out of range", verify(object="subject", examples=0),
         "examples"),
        ("a reference that is not a string",
         verify(object="subject", reference_image=17), "reference_image"),
        ("an object that does not exist", verify(object="ghost"), "ghost"),
    ]
    for label, reply, word in cases:
        message = str(reply.get("message") or "")
        check("%s is refused" % label, reply.get("status") == "error",
              message[:200])
        check("  ...and the message names %r" % word, word in message,
              message[:200])
        check("  ...with a sentence, not a traceback" ,
              "Traceback" not in message, message[:200])


def test_an_empty_scene_says_so(tmpdir):
    section("an empty scene")
    clear_scene()
    reply = verify()
    check("verify_design with nothing to measure says so",
          reply.get("status") == "error"
          and "active object" in str(reply.get("message")),
          str(reply.get("message"))[:200])
    reply = turntable(path=os.path.join(tmpdir, "empty.png"))
    check("so does a turntable of nothing",
          reply.get("status") == "error"
          and "nothing visible" in str(reply.get("message")).lower(),
          str(reply.get("message"))[:200])


def test_port_is_free_after():
    section("teardown")
    from forge import server as forge_server

    forge_server.stop_server()
    time.sleep(0.2)
    try:
        probe = socketlib.socket()
        probe.settimeout(2.0)
        probe.bind(("127.0.0.1", PORT))
        probe.close()
        free = True
    except OSError as exc:
        free = False
        note(str(exc))
    check("port %d is free again" % PORT, free)


# ---------------------------------------------------------------------------

def main():
    print("Forge headless tests - the geometric gate (verify_design + turntable)")
    enable_addon()
    tmpdir = tempfile.mkdtemp(prefix="forge_verify_")
    note("scratch in %s" % tmpdir)

    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    note("startup Cube, Light and Camera removed: every fixture here is built "
         "on purpose, with a known answer")

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)

    try:
        test_registration()
        test_every_claim_carries_a_tier()
        test_symmetry_orders_correctly()
        test_uvs_measured_and_missing()
        test_poly_budget()
        test_profiles_gate_different_axes()
        test_silhouette_against_a_synthetic_reference(tmpdir)
        test_silhouette_confidence_on_a_busy_background(tmpdir)
        test_loops_from_an_armature_and_from_tags()
        test_defects_are_composed_not_reimplemented()
        test_turntable_contact_sheet(tmpdir)
        test_turntable_defaults_are_the_protocol_numbers(tmpdir)
        test_state_is_restored(tmpdir)
        test_failure_also_restores(tmpdir)
        test_bad_input(tmpdir)
        test_an_empty_scene_says_so(tmpdir)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for root, _dirs, files in os.walk(tmpdir, topdown=False):
            for name in files:
                try:
                    os.remove(os.path.join(root, name))
                except OSError:
                    pass
            try:
                os.rmdir(root)
            except OSError:
                pass

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
