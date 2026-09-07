"""Headless add-on tests for Phase 11: drawn base shapes and merge-for-print.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_phase11.py

The socket port is **9894** (9876 is a live session, 9879-9893 the other
suites).  Nothing outside loopback is touched; the geometry service on 8765 is
consulted **read-only** (``/generate``, which builds a solid and returns a mesh)
only to prove that what the sampler measures is something ``forge_lib.soft_body``
will actually build, and its absence is a note, never a failure.

What is being proved:

1. **The drawn door is a measurement, not a trace.**  A curve is sampled into
   the 5-10 control points ``soft_body`` takes, z strictly increasing, every
   radius >= 0, and the silhouette's own extremes — the bulge and the waist —
   survive the reduction that throws the other 190 samples away.
2. **The refusals are the useful ones**: an open curve where a loop is needed,
   an object that is not a curve, two strokes in one object, a point count
   outside what the helper accepts.  Each one names what to do instead.
3. **Merging is one watertight shell** out of overlapping pieces, at a voxel
   size derived from the printer's nozzle, with the originals **hidden and
   still there** — and Ctrl+Z takes the whole thing back.
4. **The component convention** (`<project>` core, `<project>-<component>`
   proposals, one collection) is what the merge resolves and names by, so
   "scrap the collar, then merge what's left" is a delete and a button.
5. The panel button and the `merge-and-check` flow are wired to the same
   command, so the artist's three routes to it agree.
"""

import json
import os
import socket as socketlib
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_DIR = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9894  # not 9876 (live) and not 9879..9893 (the other suites)
SERVICE_URL = "http://127.0.0.1:8765"

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


# ---------------------------------------------------------------------------
# the socket round trip (same shape as every other suite)
# ---------------------------------------------------------------------------

def _roundtrip(payload, timeout=300.0):
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
    return box.get("reply") or {"status": "error", "message": "no reply"}


def send(command, **params):
    return _roundtrip({"type": command, "params": params})


def result_of(reply):
    return reply.get("result") or {}


# ---------------------------------------------------------------------------
# a fake UILayout, so the Model box's draw() runs headless
# ---------------------------------------------------------------------------

class FakeLayout(object):
    def __init__(self, sink=None):
        self._sink = sink if sink is not None else {"labels": [], "operators": [],
                                                    "props": []}
        self.active = True
        self.alert = False
        self.enabled = True
        self.scale_y = 1.0
        self.scale_x = 1.0
        self.alignment = "EXPAND"

    def row(self, align=False):
        return FakeLayout(self._sink)

    def column(self, align=False):
        return FakeLayout(self._sink)

    def box(self):
        return FakeLayout(self._sink)

    def separator(self, factor=1.0):
        return None

    def label(self, text="", icon="NONE", **kwargs):
        self._sink["labels"].append(str(text))
        return None

    def prop(self, data, name, **kwargs):
        getattr(data, name)
        self._sink["props"].append(name)
        return None

    def operator(self, idname, **kwargs):
        self._sink["operators"].append(str(idname))
        return type("Args", (object,), {})()

    @property
    def sink(self):
        return self._sink


def draw_model_panel():
    from forge.ui.panels import VIEW3D_PT_forge_model

    layout = FakeLayout()
    shim = type("PanelShim", (object,), {})()
    shim.layout = layout
    VIEW3D_PT_forge_model.draw(shim, bpy.context)
    return layout


# ---------------------------------------------------------------------------
# scene building
# ---------------------------------------------------------------------------

MM = 0.001

#: A vase drawn in the front view: a base, a bulge, a waist, a flared rim.  The
#: bulge and the waist are the two points the simplification must keep.
VASE_MM = [(15.0, 0.0), (30.0, 12.0), (52.0, 40.0), (30.0, 72.0),
           (26.0, 86.0), (40.0, 110.0)]

#: A closed ear outline, drawn cyclic.
EAR_MM = [(0.0, 0.0), (14.0, 20.0), (10.0, 60.0), (0.0, 72.0),
          (-10.0, 50.0), (-12.0, 16.0)]


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    bpy.context.view_layer.update()


def make_curve(name, points_mm, cyclic=False, kind="BEZIER", plane="XZ",
               flip=False, offset_z_mm=0.0):
    """A curve object drawn the way an artist would draw one, in ``plane``."""
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new(kind)
    count = len(points_mm)
    if kind == "BEZIER":
        spline.bezier_points.add(count - 1)
        seats = spline.bezier_points
    else:
        spline.points.add(count - 1)
        seats = spline.points

    for seat, (across, up) in zip(seats, points_mm):
        if flip:
            across = -across
        up = up + offset_z_mm
        if plane == "XZ":
            location = (across * MM, 0.0, up * MM)
        elif plane == "YZ":
            location = (0.0, across * MM, up * MM)
        else:  # XY — drawn in the top view
            location = (across * MM, up * MM, 0.0)
        if kind == "BEZIER":
            seat.co = location
            seat.handle_left_type = "AUTO"
            seat.handle_right_type = "AUTO"
        else:
            seat.co = (location[0], location[1], location[2], 1.0)
    spline.use_cyclic_u = bool(cyclic)

    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def make_cube(name, size_mm, location_mm, collection=None):
    bpy.ops.mesh.primitive_cube_add(size=size_mm * MM,
                                    location=[v * MM for v in location_mm])
    obj = bpy.context.active_object
    obj.name = name
    if collection is not None:
        for existing in list(obj.users_collection):
            existing.objects.unlink(obj)
        collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def make_sphere(name, radius_mm, location_mm, collection=None):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius_mm * MM,
                                         location=[v * MM for v in location_mm])
    obj = bpy.context.active_object
    obj.name = name
    if collection is not None:
        for existing in list(obj.users_collection):
            existing.objects.unlink(obj)
        collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def new_collection(name):
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    return collection


def deselect_all():
    view_layer = bpy.context.view_layer
    view_layer.update()
    for obj in list(view_layer.objects):
        if obj is None:
            continue
        try:
            obj.select_set(False)
        except (RuntimeError, ReferenceError):
            pass
    view_layer.objects.active = None


# ---------------------------------------------------------------------------
# 1. profile_from_curve — the drawn silhouette becomes soft_body's points
# ---------------------------------------------------------------------------

def test_profile_sampling():
    section("1. profile_from_curve: a drawn vase becomes control points")
    from forge.tools import curves

    obj = make_curve("VaseProfile", VASE_MM)
    reply = send("profile_from_curve", curve_object="VaseProfile", points=7)
    if not check("it sampled the curve", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    result = result_of(reply)
    points = result.get("points_mm") or []

    check("it returned the 7 control points it was asked for",
          len(points) == 7 and result.get("point_count") == 7, str(len(points)))
    check("every radius is zero or more, which is what a revolve needs",
          all(radius >= 0.0 for radius, _ in points), str(points))
    heights = [height for _, height in points]
    check("z strictly increases, bottom to top",
          all(b > a for a, b in zip(heights, heights[1:])), str(heights))
    check("it read the front (XZ) plane the artist drew on",
          result.get("plane") == "XZ" and result.get("plane_normal") == "Y",
          str(result.get("plane")))
    check("it says which forge_lib helper the points are for",
          result.get("helper") == "forge_lib.soft_body")
    check("it sampled the curve densely rather than reading the control points",
          int(result.get("sample_count") or 0) > 30,
          str(result.get("sample_count")))
    check("nothing was built — the scene still has only the curve in it",
          [o.name for o in bpy.data.objects] == ["VaseProfile"],
          str([o.name for o in bpy.data.objects]))

    # The extremes must survive the reduction. Measure them off the dense
    # samples the sampler itself walked, then look for them in the seven.
    samples, _kind, _notes, _cyclic = curves.spline_samples(obj)
    dense = [(abs(point[0]), point[2]) for point in samples]
    dense_max = max(radius for radius, _ in dense)
    check("the widest point of the vase survived the simplification",
          abs(float(result.get("max_radius_mm")) - dense_max) <= 0.5,
          "sampler %s vs dense %.2f" % (result.get("max_radius_mm"), dense_max))

    waist_zone = [radius for radius, height in dense if 60.0 <= height <= 100.0]
    waist = min(waist_zone) if waist_zone else None
    kept_waist = min((radius for radius, height in points
                      if 60.0 <= height <= 100.0), default=None)
    check("and so did the waist — the other curvature extreme",
          waist is not None and kept_waist is not None
          and abs(kept_waist - waist) <= 2.0,
          "kept %s vs dense %s" % (kept_waist, waist))

    check("the height is the drawn height",
          abs(float(result.get("height_mm")) - 110.0) <= 1.5,
          str(result.get("height_mm")))


def test_profile_options():
    section("2. profile_from_curve: the options that matter")
    make_curve("HighProfile", VASE_MM, offset_z_mm=40.0)

    closed = result_of(send("profile_from_curve", curve_object="HighProfile"))
    check("close_bottom (the default) stands the body on the plate",
          abs(closed["points_mm"][0][1]) < 1e-6, str(closed["points_mm"][0]))
    check("and says how far it moved it",
          abs(float(closed.get("z_offset_mm")) + 40.0) < 1.0,
          str(closed.get("z_offset_mm")))
    check("the default point count is 7, in soft_body's 5-10 band",
          len(closed["points_mm"]) == 7, str(len(closed["points_mm"])))

    kept = result_of(send("profile_from_curve", curve_object="HighProfile",
                          close_bottom=False))
    check("close_bottom: false keeps the heights exactly as drawn",
          abs(kept["points_mm"][0][1] - 40.0) < 1.0, str(kept["points_mm"][0]))

    make_curve("LeftProfile", VASE_MM, flip=True)
    left = result_of(send("profile_from_curve", curve_object="LeftProfile"))
    check("a silhouette drawn on the LEFT of the axis still gives radii >= 0",
          all(radius >= 0.0 for radius, _ in left["points_mm"]),
          str(left["points_mm"]))
    check("and it is the same vase, folded onto the right",
          abs(float(left["max_radius_mm"])
              - float(closed["max_radius_mm"])) < 0.5,
          "%s vs %s" % (left["max_radius_mm"], closed["max_radius_mm"]))

    make_curve("SideProfile", VASE_MM, plane="YZ")
    side = result_of(send("profile_from_curve", curve_object="SideProfile"))
    check("a curve drawn in the side view is read on the YZ plane",
          side.get("plane") == "YZ" and side.get("plane_normal") == "X",
          str(side.get("plane")))

    make_curve("PolyProfile", [(20.0, 0.0), (34.0, 30.0), (28.0, 60.0)],
               kind="POLY")
    poly = result_of(send("profile_from_curve", curve_object="PolyProfile",
                          points=5))
    check("a 3-point poly curve is spread out to the 5 points soft_body needs",
          len(poly["points_mm"]) == 5, str(poly["points_mm"]))
    check("and it says it did that rather than pretending it found 5",
          any("spread evenly" in text for text in poly.get("notes") or []),
          str(poly.get("notes")))
    check("the poly curve's own shape is unchanged by the resampling",
          abs(float(poly["height_mm"]) - 60.0) < 0.5, str(poly["height_mm"]))


def test_profile_refusals():
    section("3. profile_from_curve: the refusals")
    make_cube("NotACurve", 20.0, (0.0, 0.0, 0.0))
    reply = send("profile_from_curve", curve_object="NotACurve")
    check("a mesh is refused, and told what to draw instead",
          reply.get("status") == "error"
          and "not a curve" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    reply = send("profile_from_curve", curve_object="Nope")
    check("a name that is not in the file is refused by name",
          reply.get("status") == "error"
          and "no object called" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    reply = send("profile_from_curve")
    check("no curve named at all is refused with the parameter's name",
          reply.get("status") == "error"
          and "curve_object" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    make_curve("TooMany", VASE_MM)
    reply = send("profile_from_curve", curve_object="TooMany", points=14)
    check("a point count outside soft_body's 5-10 is refused, saying the band",
          reply.get("status") == "error"
          and "between 5 and 10" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    obj = make_curve("TwoStrokes", VASE_MM)
    extra = obj.data.splines.new("POLY")
    extra.points.add(1)
    extra.points[0].co = (0.0, 0.0, 0.0, 1.0)
    extra.points[1].co = (0.01, 0.0, 0.02, 1.0)
    reply = send("profile_from_curve", curve_object="TwoStrokes")
    check("two strokes in one object is refused rather than guessed at",
          reply.get("status") == "error"
          and "separate strokes" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    make_curve("FlatLine", [(10.0, 20.0), (40.0, 20.0), (70.0, 20.0)],
               kind="POLY")
    reply = send("profile_from_curve", curve_object="FlatLine")
    check("a horizontal stroke has no height, and is refused as such",
          reply.get("status") == "error"
          and "no height" in str(reply.get("message")),
          str(reply.get("message"))[:200])


# ---------------------------------------------------------------------------
# 4. outline_from_curve — the drawn loop becomes silhouette_part's outline
# ---------------------------------------------------------------------------

def test_outline_sampling():
    section("4. outline_from_curve: a drawn ear becomes an outline")
    make_curve("EarOutline", EAR_MM, cyclic=True)
    reply = send("outline_from_curve", curve_object="EarOutline", points=10)
    if not check("it sampled the loop", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    result = result_of(reply)
    points = result.get("points_mm") or []

    check("it returned the 10 outline points it was asked for",
          len(points) == 10, str(len(points)))
    check("it says which helper they are for",
          result.get("helper") == "forge_lib.silhouette_part")
    check("the loop is reported closed", result.get("closed_curve") is True)
    check("the simplified outline does not cross itself",
          result.get("self_intersections") == 0,
          str(result.get("self_intersections")))

    ys = [y for _, y in points]
    xs = [x for x, _ in points]
    check("it is recentred with its bottom on y = 0, where the peg goes",
          abs(min(ys)) < 1e-6, str(min(ys)))
    check("and centred on x = 0",
          abs(max(xs) + min(xs)) < 0.01, "%.4f" % (max(xs) + min(xs)))
    check("the width and height are the drawn ones",
          abs(float(result.get("height_mm")) - 72.0) <= 3.0
          and abs(float(result.get("width_mm")) - 26.0) <= 4.0,
          "%s x %s" % (result.get("width_mm"), result.get("height_mm")))

    raw = result_of(send("outline_from_curve", curve_object="EarOutline",
                         points=10, recenter=False))
    raw_xs = [x for x, _ in raw["points_mm"]]
    check("recenter: false leaves the drawn coordinates where they were drawn",
          abs((max(raw_xs) + min(raw_xs))
              - (max(xs) + min(xs))) > 0.5,
          "raw %.2f vs recentred %.2f" % (max(raw_xs) + min(raw_xs),
                                          max(xs) + min(xs)))
    check("and says it did not move them", raw.get("recentered") is False)
    check("the recentring offset is reported so it can be undone",
          abs(float(result.get("offset_mm")[0])) > 0.1,
          str(result.get("offset_mm")))

    default = result_of(send("outline_from_curve", curve_object="EarOutline"))
    check("the default point count is 12, inside silhouette_part's 6-16",
          len(default["points_mm"]) == 12, str(len(default["points_mm"])))


def test_outline_refusals():
    section("5. outline_from_curve: the refusals")
    make_curve("OpenEar", EAR_MM, cyclic=False)
    reply = send("outline_from_curve", curve_object="OpenEar")
    check("an OPEN curve is refused — an outline has to be a loop",
          reply.get("status") == "error"
          and "open" in str(reply.get("message")).lower(),
          str(reply.get("message"))[:200])
    check("and the refusal names the two keys that close it",
          "Alt+C" in str(reply.get("message")), str(reply.get("message"))[:200])

    make_cube("EarNotACurve", 20.0, (0.0, 0.0, 0.0))
    reply = send("outline_from_curve", curve_object="EarNotACurve")
    check("a mesh is refused here too",
          reply.get("status") == "error"
          and "not a curve" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    make_curve("EarLoop", EAR_MM, cyclic=True)
    reply = send("outline_from_curve", curve_object="EarLoop", points=3)
    check("fewer points than silhouette_part takes is refused with the band",
          reply.get("status") == "error"
          and "between 6 and 16" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    # A loop whose two ends were dragged together but never made cyclic is
    # still a closed loop to the artist, and must be to us.
    joined = EAR_MM + [EAR_MM[0]]
    make_curve("JoinedEar", joined, cyclic=False)
    reply = send("outline_from_curve", curve_object="JoinedEar", points=8)
    check("a loop closed by hand (ends meeting) is accepted",
          reply.get("status") == "success", str(reply.get("message"))[:200])


def test_samplers_are_read_only():
    section("6. measuring a drawing is not changing the scene")
    from forge.tools import registry

    check("profile_from_curve is READ_ONLY",
          "profile_from_curve" in registry.READ_ONLY_COMMANDS)
    check("outline_from_curve is READ_ONLY",
          "outline_from_curve" in registry.READ_ONLY_COMMANDS)
    check("so neither eats an undo step",
          registry.push_undo("profile_from_curve") is False
          and registry.push_undo("outline_from_curve") is False)
    check("merge_for_print is NOT read-only — it builds and hides",
          "merge_for_print" not in registry.READ_ONLY_COMMANDS)


def test_the_service_builds_what_was_sampled():
    section("7. what the sampler measured, soft_body actually builds")
    make_curve("BuildableProfile", VASE_MM)
    result = result_of(send("profile_from_curve",
                            curve_object="BuildableProfile", points=7))
    points = result.get("points_mm") or []
    if not points:
        check("there were points to build", False)
        return

    script = (
        "import forge_lib\n\n"
        "PARAMS = {\n"
        '    "scale": {"value": 1.0, "unit": "ratio", "min": 0.5, "max": 2.0},\n'
        "}\n\n"
        "POINTS = %r\n\n"
        "def build(p):\n"
        "    points = [(r * p['scale'], z * p['scale']) for r, z in POINTS]\n"
        "    return forge_lib.soft_body(points)\n" % (points,)
    )
    payload = json.dumps({"script": script, "overrides": {}}).encode("utf-8")
    request = urllib.request.Request(
        SERVICE_URL + "/generate", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        check("the geometry service built the drawn profile", False, detail)
        return
    except (urllib.error.URLError, OSError) as exc:
        note("the geometry service is not running (%s) - skipping the "
             "end-to-end build. The sampler's own checks above still hold." % exc)
        return

    stats = body.get("stats") or {}
    check("soft_body accepted the sampled control points",
          int(stats.get("face_count") or 0) > 0, str(stats)[:200])
    check("and the solid it revolved is watertight",
          stats.get("watertight") is True, str(stats)[:200])
    size = stats.get("bounding_box_mm") or [0, 0, 0]
    check("at the size the artist drew",
          abs(float(size[2]) - 110.0) < 3.0, str(size))


# ---------------------------------------------------------------------------
# 8. the component convention
# ---------------------------------------------------------------------------

def test_component_names():
    section("8. the component naming convention")
    from forge.tools import common

    check("the core is the project's own name",
          common.component_name("gecko-bowl") == "gecko-bowl")
    check("a proposal is <project>-<component>",
          common.component_name("gecko-bowl", "collar") == "gecko-bowl-collar")
    check("a stray dash on the component is not doubled",
          common.component_name("gecko-bowl", "-collar") == "gecko-bowl-collar")
    long_name = common.component_name("g" * 70, "collar")
    check("names stay inside Blender's 63-byte object-name limit",
          len(long_name.encode("utf-8")) <= 63, str(len(long_name)))

    check("a dashed name splits into project and component",
          common.split_component_name("gecko-bowl-collar")
          == ("gecko-bowl", "collar"))
    check("a name with no dash is a core",
          common.split_component_name("bowl") == ("bowl", ""))

    known = ["gecko-bowl", "lamp"]
    check("a known project claims its own core",
          common.project_of("gecko-bowl", known) == "gecko-bowl")
    check("and its proposals",
          common.project_of("gecko-bowl-collar", known) == "gecko-bowl")
    check("including multi-word components",
          common.project_of("gecko-bowl-ear-l", known) == "gecko-bowl")
    check("with no collection to compare against, the last piece is the component",
          common.project_of("thing-collar", []) == "thing")

    check("a set of component names agrees on its own project",
          common.common_project(["core", "core-collar", "core-ear"]) == "core")
    check("even when the project's own name has a dash in it",
          common.common_project(["panel-core", "panel-core-ear"]) == "panel-core")
    check("and names that agree on nothing claim nothing",
          common.common_project(["clash", "other-thing"]) == "")


# ---------------------------------------------------------------------------
# 9. the voxel argument
# ---------------------------------------------------------------------------

def test_voxel_defaults():
    section("9. the voxel size is an argument, not a taste")
    from forge.tools import model

    nozzle, source = model.merge_nozzle_mm()
    check("the nozzle comes from the printer profile", abs(nozzle - 0.4) < 1e-9,
          "%s from %s" % (nozzle, source))
    note("nozzle %s mm from %s" % (nozzle, source))

    voxel, kind, notes, estimate, _nozzle, _source = model.merge_voxel_size(
        20000.0, 40.0, None)
    check("the default is half the nozzle: two voxels per bead",
          abs(voxel - 0.2) < 1e-9 and kind == "nozzle", "%s (%s)" % (voxel, kind))
    check("and the face count is predicted before anything is remeshed",
          estimate == int(20000.0 / (0.2 * 0.2)), str(estimate))

    voxel, kind, notes, estimate, _n, _s = model.merge_voxel_size(
        20000.0, 40.0, 0.35)
    check("an explicit size is honoured", abs(voxel - 0.35) < 1e-9
          and kind == "given", "%s (%s)" % (voxel, kind))

    big = 400000.0  # a 350 mm-ish figure: 10 million faces at 0.2 mm
    voxel, kind, notes, estimate, _n, _s = model.merge_voxel_size(big, 350.0, None)
    check("a huge surface is coarsened rather than freezing Blender",
          kind == "coarsened" and voxel > 0.2, "%s (%s)" % (voxel, kind))
    check("and the coarsened prediction is under the cap",
          estimate <= model.MERGE_FACE_CAP, str(estimate))
    check("the trade is stated in words, not swallowed",
          any("rounded off" in text for text in notes), str(notes))

    voxel, kind, notes, _e, _n, _s = model.merge_voxel_size(500.0, 20.0, 0.005)
    check("a voxel finer than Forge will build is clamped up",
          abs(voxel - model.MERGE_MIN_VOXEL_MM) < 1e-9 and kind == "clamped",
          str(voxel))
    voxel, kind, notes, _e, _n, _s = model.merge_voxel_size(500.0, 12.0, 20.0)
    check("a voxel that would eat the object is clamped down",
          voxel <= 12.0 * model.MERGE_MAX_VOXEL_FRACTION + 1e-9
          and kind == "clamped", str(voxel))


# ---------------------------------------------------------------------------
# 10. merge_for_print
# ---------------------------------------------------------------------------

def test_merge_basics():
    section("10. merge_for_print: many pieces, one sealed shell")
    clear_scene()
    make_cube("core", 20.0, (0.0, 0.0, 0.0))
    make_cube("core-collar", 20.0, (12.0, 0.0, 0.0))
    make_sphere("core-ear", 8.0, (0.0, 0.0, 12.0))
    deselect_all()

    reply = send("merge_for_print",
                 objects=["core", "core-collar", "core-ear"])
    if not check("the merge ran", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    result = result_of(reply)

    merged = bpy.data.objects.get(result.get("object"))
    check("it made one object", merged is not None and merged.type == "MESH",
          str(result.get("object")))
    check("named by the component convention",
          result.get("object") == "core-merged", str(result.get("object")))
    check("with a real surface on it",
          int(result.get("face_count") or 0) > 100
          and int(result.get("vertex_count") or 0) > 100,
          "%s faces" % result.get("face_count"))
    check("and it is watertight — the one thing the slicer needs",
          result.get("watertight") is True, str(result.get("notes")))
    check("all three pieces went in",
          result.get("source_count") == 3
          and [entry["object"] for entry in result["sources"]]
          == ["core", "core-collar", "core-ear"], str(result.get("sources")))
    check("and all three were sealed to begin with",
          result.get("watertight_input_count") == 3,
          str(result.get("watertight_input_count")))
    check("the voxel came from the nozzle, not from taste",
          abs(float(result.get("voxel_size_mm")) - 0.2) < 1e-9
          and result.get("voxel_source") == "nozzle",
          str(result.get("voxel_size_mm")))
    check("it says what to do next",
          result.get("next") == "check_model")

    predicted = int(result.get("predicted_face_count") or 0)
    actual = int(result.get("face_count") or 0)
    ratio = (float(actual) / predicted) if predicted else 0.0
    check("the face-count prediction is close enough to pick a voxel size with",
          0.25 <= ratio <= 4.0, "predicted %d, got %d (%.2fx)"
          % (predicted, actual, ratio))
    note("predicted %d faces, got %d (%.2fx)" % (predicted, actual, ratio))

    for name in ("core", "core-collar", "core-ear"):
        obj = bpy.data.objects.get(name)
        check("%s is still in the file — hidden, never deleted" % name,
              obj is not None and not obj.visible_get(),
              "missing" if obj is None else "still visible")
    check("and the result lists what it hid",
          sorted(result.get("hidden") or [])
          == ["core", "core-collar", "core-ear"], str(result.get("hidden")))
    check("kept_originals is reported honestly",
          result.get("kept_originals") is True)
    check("the merged shell is left selected and active, ready for the check",
          bpy.context.view_layer.objects.active is merged,
          str(getattr(bpy.context.view_layer.objects.active, "name", None)))


def test_merge_undo():
    section("11. merge_for_print: Ctrl+Z takes it back")
    from forge.tools import registry

    clear_scene()
    make_cube("undo-core", 20.0, (0.0, 0.0, 0.0))
    make_cube("undo-core-collar", 20.0, (10.0, 0.0, 0.0))
    deselect_all()

    if not registry.push_undo("merge_for_print"):
        note("undo checkpoints are OFF in this Blender - not testable here")
        check("the guard reported it rather than failing the command",
              registry._UNDO_AVAILABLE is False)
        return

    reply = send("merge_for_print", objects=["undo-core", "undo-core-collar"])
    if not check("the merge ran", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    name = result_of(reply).get("object")
    check("the merged object exists", name in bpy.data.objects, str(name))
    check("the undo step is named for the command",
          registry.undo_message("merge_for_print") == "Forge: merge_for_print")

    try:
        bpy.ops.ed.undo()
    except RuntimeError as exc:
        check("ed.undo is available headless", False, str(exc))
        return
    check("Ctrl+Z takes the merged shell back off the scene",
          name not in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)[:8]))
    original = bpy.data.objects.get("undo-core")
    check("and the pieces are visible again",
          original is not None and original.visible_get(),
          "missing" if original is None else "still hidden")


def test_merge_by_collection():
    section("12. merge_for_print: a project collection, minus what was scrapped")
    clear_scene()
    collection = new_collection("gecko-bowl")
    make_cube("gecko-bowl", 20.0, (0.0, 0.0, 0.0), collection=collection)
    make_cube("gecko-bowl-collar", 16.0, (0.0, 0.0, 10.0), collection=collection)
    scrapped = make_cube("gecko-bowl-ear-l", 10.0, (12.0, 0.0, 10.0),
                         collection=collection)
    make_curve("gecko-bowl-sketch", VASE_MM)
    for existing in list(bpy.data.objects["gecko-bowl-sketch"].users_collection):
        existing.objects.unlink(bpy.data.objects["gecko-bowl-sketch"])
    collection.objects.link(bpy.data.objects["gecko-bowl-sketch"])
    scrapped.hide_set(True)  # the artist said "scrap the left ear"
    deselect_all()

    reply = send("merge_for_print", collection="gecko-bowl")
    if not check("the merge ran off the collection",
                 reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return
    result = result_of(reply)

    check("only the VISIBLE pieces went in",
          [entry["object"] for entry in result["sources"]]
          == ["gecko-bowl", "gecko-bowl-collar"], str(result.get("sources")))
    check("the scrapped one is named as skipped, not silently dropped",
          any("gecko-bowl-ear-l" in text for text in result.get("notes") or []),
          str(result.get("notes")))
    check("the curve in the collection was skipped too",
          any("gecko-bowl-sketch" in text for text in result.get("notes") or []),
          str(result.get("notes")))
    check("the shell is named after the project",
          result.get("object") == "gecko-bowl-merged", str(result.get("object")))
    check("and it lands in the project's own collection",
          "gecko-bowl-merged" in collection.objects,
          str([o.name for o in collection.objects]))
    check("the scrapped ear is untouched — still there, still hidden",
          bpy.data.objects.get("gecko-bowl-ear-l") is not None
          and not bpy.data.objects["gecko-bowl-ear-l"].visible_get())
    check("resolved_by says how the pieces were chosen",
          result.get("resolved_by") == "collection")


def test_merge_options_and_refusals():
    section("13. merge_for_print: the options and the refusals")
    clear_scene()
    make_cube("opt-core", 20.0, (0.0, 0.0, 0.0))
    make_cube("opt-core-tail", 20.0, (10.0, 0.0, 0.0))
    deselect_all()

    result = result_of(send("merge_for_print",
                            objects=["opt-core", "opt-core-tail"],
                            voxel_size_mm=0.6, name="opt-custom"))
    check("an explicit voxel size is used and reported as asked-for",
          abs(float(result.get("voxel_size_mm")) - 0.6) < 1e-9
          and result.get("voxel_source") == "given",
          str(result.get("voxel_size_mm")))
    check("an explicit name is used", result.get("object") == "opt-custom")
    check("a coarser voxel really is a smaller file",
          int(result.get("face_count") or 0) < 200000,
          str(result.get("face_count")))

    clear_scene()
    make_cube("gone-core", 20.0, (0.0, 0.0, 0.0))
    make_cube("gone-core-fin", 20.0, (10.0, 0.0, 0.0))
    deselect_all()
    result = result_of(send("merge_for_print",
                            objects=["gone-core", "gone-core-fin"],
                            keep_originals=False))
    check("keep_originals: false really does delete them",
          "gone-core" not in bpy.data.objects
          and "gone-core-fin" not in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)))
    check("and says so", sorted(result.get("deleted") or [])
          == ["gone-core", "gone-core-fin"], str(result.get("deleted")))

    clear_scene()
    make_cube("clash", 20.0, (0.0, 0.0, 0.0))
    make_cube("clash-two", 20.0, (10.0, 0.0, 0.0))
    deselect_all()
    reply = send("merge_for_print", objects=["clash", "clash-two"], name="clash")
    check("naming the shell after one of its own pieces is refused",
          reply.get("status") == "error"
          and "being merged" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    reply = send("merge_for_print", collection="not-a-collection")
    check("an unknown collection is refused by name",
          reply.get("status") == "error"
          and "no collection called" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    clear_scene()
    deselect_all()
    reply = send("merge_for_print")
    check("nothing named and nothing selected is refused with the three ways in",
          reply.get("status") == "error"
          and "Nothing to merge" in str(reply.get("message")),
          str(reply.get("message"))[:200])

    clear_scene()
    make_cube("sel-core", 20.0, (0.0, 0.0, 0.0))
    make_cube("sel-core-ear", 20.0, (10.0, 0.0, 0.0))
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        obj.select_set(True)
    reply = send("merge_for_print")
    check("with nothing named, the selection is what gets merged",
          reply.get("status") == "success"
          and result_of(reply).get("resolved_by") == "selection",
          str(reply.get("message"))[:200])


# ---------------------------------------------------------------------------
# 14. the panel button and the flow
# ---------------------------------------------------------------------------

def test_panel_button():
    section("14. the Model box's Merge for Print button")
    check("the operator is registered", hasattr(bpy.ops.forge, "model_merge"))

    clear_scene()
    make_cube("panel-core", 20.0, (0.0, 0.0, 0.0))
    make_cube("panel-core-ear", 20.0, (10.0, 0.0, 0.0))
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        obj.select_set(True)

    layout = draw_model_panel()
    check("the Model box draws it",
          "forge.model_merge" in layout.sink["operators"],
          str(layout.sink["operators"]))
    check("with the selection count in the hint under it",
          any("2 selected" in text for text in layout.sink["labels"]),
          str(layout.sink["labels"])[:300])

    status = bpy.ops.forge.model_merge()
    check("pressing it merges the selection", "FINISHED" in status, str(status))
    check("and the merged shell is in the scene, named for the project the two "
          "pieces agree on",
          "panel-core-merged" in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)))

    from forge.tools import model

    props = model.get_props()
    check("the Model box now points at the merged shell",
          props is not None and props.object_name == "panel-core-merged",
          str(getattr(props, "object_name", None)))
    check("and its status line says what to press next",
          "Check imported model" in str(getattr(props, "status", "")),
          str(getattr(props, "status", ""))[:160])

    clear_scene()
    deselect_all()
    layout = draw_model_panel()
    check("with nothing selected the hint says so",
          any("select the pieces" in text for text in layout.sink["labels"]),
          str(layout.sink["labels"])[:300])
    status = bpy.ops.forge.model_merge()
    check("and pressing it is a plain refusal, not a traceback",
          "CANCELLED" in status, str(status))


def test_the_flow():
    section("15. the merge-and-check flow")
    from forge.tools import flows, registry

    check("both of its steps are real Blender commands, so the flow validates",
          registry.has_command("merge_for_print")
          and registry.has_command("check_model"))

    point_pref("forge_flows_dir", os.path.join(REPO_DIR, "flows"))
    try:
        doc = flows.read_flow("merge-and-check")
    except Exception as exc:  # noqa: BLE001
        check("flows/merge-and-check.json loads and validates", False, str(exc))
        return
    check("flows/merge-and-check.json loads and validates", True)
    ops = [step.get("op") for step in doc.get("steps") or []]
    check("it merges, then checks", ops == ["merge_for_print", "check_model"],
          str(ops))
    check("the check is aimed at whatever the merge produced",
          "{{steps.0.result.object}}" in json.dumps(doc["steps"][1]["args"]),
          str(doc["steps"][1]["args"]))
    check("its parameters are the three an artist would change",
          set(doc.get("params") or {})
          == {"collection", "voxel_size_mm", "keep_originals"},
          str(sorted(doc.get("params") or {})))
    check("a blank collection means 'the selection', so the flow is not "
          "tied to one project",
          doc["params"]["collection"]["value"] == "")
    check("and a zero voxel size means 'use the nozzle'",
          doc["params"]["voxel_size_mm"]["value"] == 0)

    listed = {entry.get("name"): entry for entry in flows.list_flows()}
    check("it shows up in the Flows box with no error",
          "merge-and-check" in listed
          and not listed["merge-and-check"].get("error"),
          str(sorted(listed)))

    # The first step alone, through the flow runner: proof the deterministic
    # half really replays. The check step needs the geometry service, which is
    # a separate suite's business.
    clear_scene()
    make_cube("flow-core", 20.0, (0.0, 0.0, 0.0))
    make_cube("flow-core-ear", 20.0, (10.0, 0.0, 0.0))
    for obj in list(bpy.context.view_layer.objects):
        obj.select_set(True)   # blank collection = "merge what I have selected"
    reply = send("flow_run", flow={
        "name": "merge-only",
        "description": "the merge half of merge-and-check",
        "params": doc["params"],
        "steps": [doc["steps"][0],
                  {"kind": "blender", "op": "get_scene_info",
                   "label": "look at what came out", "args": {}}],
    }, params={"collection": ""})
    if reply.get("status") != "success":
        check("the flow runner replays the merge step", False,
              str(reply.get("message"))[:300])
        return
    steps = result_of(reply).get("steps") or []
    check("the flow runner replays the merge step",
          bool(steps) and steps[0].get("ok") is True, str(steps)[:300])
    check("and the shell it made is in the scene",
          "flow-core-merged" in bpy.data.objects,
          str(sorted(o.name for o in bpy.data.objects)))


def test_the_sculpt_flow():
    """The other half of the contract: a base shape handed to the stylus."""
    section("16. the sculpt-ready flow")
    from forge.tools import flows

    point_pref("forge_flows_dir", os.path.join(REPO_DIR, "flows"))
    try:
        doc = flows.read_flow("sculpt-ready")
    except Exception as exc:  # noqa: BLE001
        check("flows/sculpt-ready.json loads and validates", False, str(exc))
        return
    check("flows/sculpt-ready.json loads and validates", True)
    ops = [step.get("op") for step in doc.get("steps") or []]
    check("it remeshes, enters Sculpt Mode, then sets the brush up",
          ops == ["remesh", "set_mode", "sculpt_brush"], str(ops))
    check("a blank object means 'the one they just generated'",
          doc["params"]["object"]["value"] == "")
    check("the remesh grid is 1 mm, said in Blender's own metres",
          doc["params"]["voxel_size"]["value"] == 0.001)

    listed = {entry.get("name"): entry for entry in flows.list_flows()}
    check("it shows up in the Flows box with no error",
          "sculpt-ready" in listed and not listed["sculpt-ready"].get("error"),
          str(sorted(listed)))

    clear_scene()
    obj = make_sphere("base-shape", 30.0, (0.0, 0.0, 0.0))
    before = len(obj.data.polygons)
    bpy.context.view_layer.objects.active = obj
    reply = send("flow_run", name="sculpt-ready")
    if reply.get("status") != "success":
        check("the flow runner replays the whole handoff", False,
              str(reply.get("message"))[:300])
        return
    steps = result_of(reply).get("steps") or []
    check("the flow runner replays the whole handoff",
          len(steps) == 3 and all(step.get("ok") for step in steps),
          str(steps)[:400])
    check("the base shape was remeshed to an even grid",
          len(bpy.data.objects["base-shape"].data.polygons) != before,
          "%d -> %d" % (before, len(bpy.data.objects["base-shape"].data.polygons)))
    check("and it is in Sculpt Mode with the brush in hand",
          bpy.data.objects["base-shape"].mode == "SCULPT",
          bpy.data.objects["base-shape"].mode)
    # Leave the file in Object Mode for whatever runs after this.
    send("set_mode", mode="object", object="base-shape")


# ---------------------------------------------------------------------------
# helpers + entry point
# ---------------------------------------------------------------------------

def point_pref(name, value):
    from forge import prefs as forge_prefs

    forge_prefs.DEFAULTS[name] = value
    setattr(forge_prefs._FALLBACK, name, value)
    entry = None
    try:
        entry = bpy.context.preferences.addons.get(forge_prefs.ADDON_ID)
    except (AttributeError, TypeError):
        entry = None
    if entry is not None and entry.preferences is not None:
        try:
            setattr(entry.preferences, name, value)
        except (AttributeError, TypeError):
            pass


def test_port_is_free_after():
    section("the socket let its port go")
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


def main():
    print("Forge add-on tests: Phase 11 — drawn base shapes and merge-for-print")
    print("  blender %s (background=%s)" % (bpy.app.version_string, bpy.app.background))

    enable_addon()
    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    time.sleep(0.2)

    try:
        clear_scene()
        test_profile_sampling()
        test_profile_options()
        test_profile_refusals()
        clear_scene()
        test_outline_sampling()
        test_outline_refusals()
        test_samplers_are_read_only()
        clear_scene()
        test_the_service_builds_what_was_sampled()
        test_component_names()
        test_voxel_defaults()
        test_merge_basics()
        test_merge_undo()
        test_merge_by_collection()
        test_merge_options_and_refusals()
        test_panel_button()
        test_the_flow()
        test_the_sculpt_flow()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        forge_server.stop_server()
        time.sleep(0.2)
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
