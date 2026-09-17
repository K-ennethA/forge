"""Headless add-on tests for Phase 18(b) — ``fit_to_silhouette``.

ONE Blender launch covers the whole command, on purpose: every extra
``--background`` run is another flash on the artist's machine, so registration,
the single-view fit, the multi-view fit, the knobs and every refusal are tested
together, in one process, in one file.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_silhouette.py

The socket port is **9904** (9876 belongs to a live session, 9879-9903 to the
earlier suites). No service, no Claude CLI, no network beyond loopback, and no
reference pictures on disk that this file did not draw itself.

The fixtures are synthetic and their right answers are arithmetic
-----------------------------------------------------------------
A UV sphere of radius 1 m is 2000 mm across in every direction. Fitted to an
ellipse whose bounding box is 201 x 401 pixels, with ``fit="height"`` (the
reference is scaled so its height matches the mesh's), the answer is known
before the command runs: the mesh must end up ``2000 * 201 / 401`` = **1003 mm**
wide and **2000 mm** tall, and — because a front reference is not entitled to an
opinion about depth — exactly **2000.000 mm** deep, to the last digit that was
there before.

What is actually being proved:

1. the command is registered, is **not** read-only (it edits the artist's mesh,
   so Ctrl+Z has to reach it) and is a legal flow step, proven by running it
   inside a flow rather than by reading a list;
2. **the single-view fit works and is measured**: silhouette IoU against the
   reference before and after, on the same fixed grid against the same frozen
   target, plus the mm dimensions above;
3. **the untouched axis really is untouched** — a front fit reports
   ``displacement_by_axis_mm["Y"] == 0.0`` and the Y coordinates come back
   bit-identical;
4. **front + side together**: two references, three axes, and the consistent
   answer (an ellipsoid) found by iteration — X from the front, Y from the side,
   Z agreed between them;
5. ``strength=0`` is an exact no-op: not one coordinate moves, the mesh is not
   written to at all, and the before/after IoU are the same number — which is
   how you get the measurement without committing to the fit;
6. the knobs do what they say: ``falloff`` holds the interior still, ``symmetry``
   pulls a lopsided reference back onto its own mirror (measured with
   ``verify.symmetry_residual``), ``iterations`` converge, ``top`` is a view;
7. the mask machinery is honest: an alpha reference is tiered ``measured``, a
   flat-background one ``heuristic`` with the plain-background caveat, and a
   stray speck in the corner is warned about rather than silently cropped;
8. bad input — a path with no file, a view nobody has heard of, no views at all,
   ``views`` and the shorthand at once, two views of the same side, a threshold
   out of range, an object that is not a mesh — fails with a sentence, and the
   near misses come back with a suggestion;
9. the budget: an 8 000-vertex sphere, two references, 4 iterations, asserted
   loosely against an upper bound;
10. **character forms** (``as_shape_key``): leaving it out is today's behaviour,
    unchanged; giving it writes the identical displacement into a named shape
    key **bit-identically equal to the destructive fit of the same sphere**,
    leaves the base mesh bit-identical to the sculpt that went in, creates a
    ``Basis`` when there is none, overwrites a key of the same name instead of
    duplicating it, and stacks two forms (teen-wolf and werewolf) on one base
    that each still reproduce their own fit exactly.
"""

import json
import math
import os
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bpy
import numpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9904  # not 9876 (a live session) and not 9879..9903 (every other suite)

#: The fixture sphere: 1 m radius, so every dimension is 2000 mm and every
#: expected answer below is a fraction of that.
SPHERE_RADIUS_M = 1.0
SPHERE_MM = 2000.0

#: A dense-enough sphere for the budget check, and the ceiling it must beat.
BUDGET_SEGMENTS = 128
BUDGET_RINGS = 64
BUDGET_SECONDS = 60.0

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


def _roundtrip(payload, timeout=600.0):
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


def fit(**params):
    return _roundtrip({"type": "fit_to_silhouette", "params": params})


def flow_run(**params):
    return _roundtrip({"type": "flow_run", "params": params})


def ok(reply):
    return reply.get("status") == "success"


def result(reply):
    return reply.get("result") or {}


def message(reply):
    return reply.get("message") or ""


# ---------------------------------------------------------------------------
# fixtures — a sphere, and pictures of ellipses drawn by this file
# ---------------------------------------------------------------------------

def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def make_sphere(name, radius=SPHERE_RADIUS_M, segments=32, rings=16):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings,
                                         radius=radius, location=(0.0, 0.0, 0.0))
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name
    return obj


def coords_of(name):
    """The object's own vertex coordinates as an ``(n, 3)`` array."""
    mesh = bpy.data.objects[name].data
    flat = numpy.empty(len(mesh.vertices) * 3, dtype="f8")
    mesh.vertices.foreach_get("co", flat)
    return flat.reshape(-1, 3)


def ellipse_mask(width, height, semi_x, semi_y, centre=None):
    """A filled ellipse, row 0 at the TOP (the way an image is read)."""
    cx, cy = centre if centre else (width / 2.0, height / 2.0)
    xs = numpy.arange(width, dtype="f8") + 0.5
    ys = numpy.arange(height, dtype="f8") + 0.5
    dx = (xs[None, :] - cx) / float(semi_x)
    dy = (ys[:, None] - cy) / float(semi_y)
    return (dx * dx + dy * dy) <= 1.0


def lopsided_mask(width, height, left_semi, right_semi, semi_y):
    """Two half-ellipses sharing a waist — a reference that is NOT symmetric."""
    left = ellipse_mask(width, height, left_semi, semi_y)
    right = ellipse_mask(width, height, right_semi, semi_y)
    xs = numpy.arange(width, dtype="f8") + 0.5
    return numpy.where(xs[None, :] < width / 2.0, left, right)


def write_mask(path, mask, alpha=True):
    """Save a boolean mask as a PNG: alpha cut-out, or black subject on white.

    Only the values 0.0 and 1.0 are written, which survive Blender's sRGB byte
    conversion exactly — so what comes back off disk is the mask that went in.
    """
    height, width = mask.shape
    image = bpy.data.images.new(os.path.basename(path), width=width,
                                height=height, alpha=True)
    rgba = numpy.zeros((height, width, 4), dtype="f4")
    solid = mask.astype("f4")
    if alpha:
        rgba[..., 0] = solid
        rgba[..., 1] = solid
        rgba[..., 2] = solid
        rgba[..., 3] = solid
    else:
        rgba[..., 0] = 1.0 - solid
        rgba[..., 1] = 1.0 - solid
        rgba[..., 2] = 1.0 - solid
        rgba[..., 3] = 1.0
    # Blender's pixel buffer is bottom-up; the mask is top-down.
    image.pixels.foreach_set(rgba[::-1].reshape(-1))
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


def expected_width_mm(semi_x, semi_y):
    """What ``fit="height"`` must make the mesh, in mm, from the mask's bbox."""
    mask_w = 2.0 * semi_x + 1.0
    mask_h = 2.0 * semi_y + 1.0
    return SPHERE_MM * mask_w / mask_h


# ---------------------------------------------------------------------------
# 1. registration
# ---------------------------------------------------------------------------

def test_registration():
    section("registration and undo classification")
    from forge.tools import registry

    check("fit_to_silhouette is a registered command",
          registry.has_command("fit_to_silhouette"))
    check("it is NOT read-only — it edits the artist's mesh, Ctrl+Z must reach it",
          "fit_to_silhouette" not in registry.READ_ONLY_COMMANDS)
    check("the undo step is named for the command",
          registry.undo_message("fit_to_silhouette") == "Forge: fit_to_silhouette",
          registry.undo_message("fit_to_silhouette"))

    from forge.tools import silhouette

    check("'side' resolves to the right-hand view",
          silhouette.resolve_view("side") == "right")
    check("the front view's plane is XZ and its depth axis is Y",
          silhouette.view_frame("front") == (0, 1.0, 2, 1.0, 1),
          str(silhouette.view_frame("front")))
    check("the side view's plane is YZ and its depth axis is X",
          silhouette.view_frame("side") == (1, 1.0, 2, 1.0, 0),
          str(silhouette.view_frame("side")))
    check("the top view's plane is XY and its depth axis is Z",
          silhouette.view_frame("top") == (0, 1.0, 1, 1.0, 2),
          str(silhouette.view_frame("top")))


# ---------------------------------------------------------------------------
# 2. the single-view fit
# ---------------------------------------------------------------------------

def test_single_view_fit(tmpdir):
    section("one reference, one view — the sphere becomes the ellipse")
    clear_scene()
    make_sphere("Sculpt")
    picture = write_mask(os.path.join(tmpdir, "front_tall.png"),
                         ellipse_mask(512, 512, 100, 200))

    before = coords_of("Sculpt")
    reply = fit(object="Sculpt",
                views=[{"image": picture, "axis": "front"}],
                strength=1.0, iterations=4, smooth=0.4)
    if not check("the fit ran", ok(reply), message(reply)[:400]):
        return
    data = result(reply)
    view = data["views"][0]
    note("IoU %.4f -> %.4f (gain %+.4f); outline error %.1f mm -> %.1f mm"
         % (view["iou_before"]["value"], view["iou_after"]["value"],
            view["iou_gain"], view["outline_error_before_mm"]["mean"],
            view["outline_error_after_mm"]["mean"]))
    note("dimensions %s mm -> %s mm"
         % (data["dimensions_before_mm"], data["dimensions_after_mm"]))
    note("%.3f s for %d vertices" % (data["seconds"], data["vertices"]))

    wanted = expected_width_mm(100, 200)
    width, depth, height = data["dimensions_after_mm"]
    check("the mesh ends up as wide as the reference says (%.0f mm)" % wanted,
          abs(width - wanted) < 0.06 * wanted, "%.1f mm" % width)
    check("and as tall as it started — fit='height' anchors the height",
          abs(height - SPHERE_MM) < 0.05 * SPHERE_MM, "%.1f mm" % height)

    check("the IoU improved", view["iou_gain"] > 0.30,
          "%.4f -> %.4f" % (view["iou_before"]["value"], view["iou_after"]["value"]))
    check("and lands above 0.93 — the outline is the reference's outline",
          view["iou_after"]["value"] >= 0.93, str(view["iou_after"]["value"]))
    check("the shape-only IoU improved too (framing cannot explain it)",
          view["shape_iou_after"]["value"] > view["shape_iou_before"]["value"],
          "%s -> %s" % (view["shape_iou_before"]["value"],
                        view["shape_iou_after"]["value"]))
    check("the mean outline error fell below 3% of the model",
          view["outline_error_after_mm"]["mean"] < 0.03 * SPHERE_MM,
          str(view["outline_error_after_mm"]))
    check("both IoU numbers are tiered 'measured'",
          view["iou_before"]["tier"] == "measured"
          and view["iou_after"]["tier"] == "measured")

    # The untouched axis, the whole point of projecting along one.
    after = coords_of("Sculpt")
    check("a front reference moved NOTHING along Y",
          data["displacement_by_axis_mm"]["Y"] == 0.0,
          str(data["displacement_by_axis_mm"]))
    check("and the Y coordinates come back bit-identical",
          bool(numpy.array_equal(before[:, 1], after[:, 1])),
          "max delta %g" % float(numpy.abs(before[:, 1] - after[:, 1]).max()))
    check("the report names Y as the untouched axis",
          data["untouched_axes"] == ["Y"], str(data["untouched_axes"]))
    check("and X and Z as the constrained ones",
          data["constrained_axes"] == ["X", "Z"], str(data["constrained_axes"]))
    check("the depth extent is unchanged to the last digit",
          data["dimensions_after_mm"][1] == data["dimensions_before_mm"][1],
          "%s vs %s" % (data["dimensions_after_mm"][1],
                        data["dimensions_before_mm"][1]))

    check("most of the mesh actually moved", data["moved_fraction"] > 0.5,
          str(data["moved_fraction"]))
    check("the report says it was applied", data["applied"] is True)
    check("the honesty note about outlines is in every report",
          "OUTLINE" in data["honesty"], data["honesty"][:80])
    check("the method spells out the algorithm rather than naming it",
          "angular bin" in data["method"] and "falloff" in data["method"],
          data["method"][:120])
    check("iteration steps are reported so convergence is visible",
          len(data["iteration_max_mm"]) >= 1
          and data["iteration_max_mm"][0] > data["iteration_max_mm"][-1],
          str(data["iteration_max_mm"]))
    check("seconds are reported", isinstance(data["seconds"], float))


def test_a_wider_reference_widens(tmpdir):
    section("the fit follows the reference, not a preference")
    clear_scene()
    make_sphere("Sculpt")
    # Wider than tall: the mesh has to GROW in X, not shrink.
    picture = write_mask(os.path.join(tmpdir, "front_wide.png"),
                         ellipse_mask(512, 512, 220, 120))
    reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                iterations=4, smooth=0.4)
    if not check("the fit ran", ok(reply), message(reply)[:300]):
        return
    data = result(reply)
    wanted = expected_width_mm(220, 120)
    width = data["dimensions_after_mm"][0]
    note("width %.1f mm (wanted %.1f), IoU %.4f -> %.4f"
         % (width, wanted, data["views"][0]["iou_before"]["value"],
            data["views"][0]["iou_after"]["value"]))
    check("a wide reference makes the mesh wider than it was",
          width > SPHERE_MM * 1.2, "%.1f mm" % width)
    check("and as wide as the reference asks (%.0f mm)" % wanted,
          abs(width - wanted) < 0.08 * wanted, "%.1f mm" % width)
    check("the IoU still improves when the answer is 'grow'",
          data["views"][0]["iou_gain"] > 0.2,
          str(data["views"][0]["iou_gain"]))


# ---------------------------------------------------------------------------
# 3. multi-view
# ---------------------------------------------------------------------------

def test_front_and_side(tmpdir):
    section("front AND side — three axes from two pictures")
    clear_scene()
    make_sphere("Sculpt", segments=48, rings=24)
    front = write_mask(os.path.join(tmpdir, "mv_front.png"),
                       ellipse_mask(512, 512, 100, 200))
    side = write_mask(os.path.join(tmpdir, "mv_side.png"),
                      ellipse_mask(512, 512, 140, 200))

    reply = fit(object="Sculpt",
                views=[{"image": front, "axis": "front"},
                       {"image": side, "axis": "side"}],
                strength=1.0, iterations=6, smooth=0.35)
    if not check("the two-view fit ran", ok(reply), message(reply)[:400]):
        return
    data = result(reply)
    width, depth, height = data["dimensions_after_mm"]
    want_x = expected_width_mm(100, 200)
    want_y = expected_width_mm(140, 200)
    for view in data["views"]:
        note("%s: IoU %.4f -> %.4f, outline error %.1f -> %.1f mm"
             % (view["resolved_axis"], view["iou_before"]["value"],
                view["iou_after"]["value"],
                view["outline_error_before_mm"]["mean"],
                view["outline_error_after_mm"]["mean"]))
    note("dimensions %s mm (wanted X %.0f, Y %.0f, Z %.0f)"
         % (data["dimensions_after_mm"], want_x, want_y, SPHERE_MM))

    check("two views come back in the report", len(data["views"]) == 2)
    check("between them they constrain all three axes",
          data["constrained_axes"] == ["X", "Y", "Z"],
          str(data["constrained_axes"]))
    check("and nothing is left untouched", data["untouched_axes"] == [],
          str(data["untouched_axes"]))
    check("X comes from the front reference (%.0f mm)" % want_x,
          abs(width - want_x) < 0.12 * want_x, "%.1f mm" % width)
    check("Y comes from the side reference (%.0f mm)" % want_y,
          abs(depth - want_y) < 0.12 * want_y, "%.1f mm" % depth)
    check("Z is agreed between them and stays the height it was",
          abs(height - SPHERE_MM) < 0.08 * SPHERE_MM, "%.1f mm" % height)
    for view in data["views"]:
        check("the %s view's IoU improved" % view["resolved_axis"],
              view["iou_gain"] > 0.1, str(view["iou_gain"]))
        check("the %s view's IoU lands above 0.88" % view["resolved_axis"],
              view["iou_after"]["value"] >= 0.88, str(view["iou_after"]["value"]))
    check("each view names the plane it works in",
          {v["plane"] for v in data["views"]} == {"XZ", "YZ"},
          str([v["plane"] for v in data["views"]]))
    check("and the depth axis it refuses to touch",
          {v["depth_axis"] for v in data["views"]} == {"X", "Y"},
          str([v["depth_axis"] for v in data["views"]]))


def test_the_shorthand_is_the_same_call(tmpdir):
    section("front_image / side_image — the sketch's own spelling")
    clear_scene()
    make_sphere("Sculpt")
    front = write_mask(os.path.join(tmpdir, "sugar_front.png"),
                       ellipse_mask(400, 400, 80, 160))
    side = write_mask(os.path.join(tmpdir, "sugar_side.png"),
                      ellipse_mask(400, 400, 110, 160))
    reply = fit(object="Sculpt", front_image=front, side_image=side,
                iterations=4)
    if not check("the shorthand runs", ok(reply), message(reply)[:300]):
        return
    data = result(reply)
    check("it becomes two views", len(data["views"]) == 2)
    check("named front and right",
          sorted(v["resolved_axis"] for v in data["views"]) == ["front", "right"],
          str([v["resolved_axis"] for v in data["views"]]))

    reply = fit(object="Sculpt", front_image=front,
                views=[{"image": front, "axis": "front"}])
    check("giving both spellings at once is refused, not merged",
          not ok(reply) and "not both" in message(reply), message(reply)[:200])


def test_top_view(tmpdir):
    section("top — the third plane")
    clear_scene()
    make_sphere("Sculpt")
    picture = write_mask(os.path.join(tmpdir, "top.png"),
                         ellipse_mask(400, 400, 90, 180))
    before = coords_of("Sculpt")
    reply = fit(object="Sculpt", views=[{"image": picture, "axis": "top"}],
                iterations=4)
    if not check("a top reference runs", ok(reply), message(reply)[:300]):
        return
    data = result(reply)
    after = coords_of("Sculpt")
    check("a top view constrains X and Y",
          data["constrained_axes"] == ["X", "Y"], str(data["constrained_axes"]))
    check("and leaves Z exactly alone",
          data["displacement_by_axis_mm"]["Z"] == 0.0
          and bool(numpy.array_equal(before[:, 2], after[:, 2])),
          str(data["displacement_by_axis_mm"]))


# ---------------------------------------------------------------------------
# 4. the knobs
# ---------------------------------------------------------------------------

def test_strength_zero_is_a_no_op(tmpdir):
    section("strength=0 — measure without committing")
    clear_scene()
    make_sphere("Sculpt")
    picture = write_mask(os.path.join(tmpdir, "zero.png"),
                         ellipse_mask(512, 512, 100, 200))
    before = coords_of("Sculpt")
    reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                strength=0.0, iterations=4)
    if not check("a zero-strength fit still runs", ok(reply), message(reply)[:300]):
        return
    data = result(reply)
    after = coords_of("Sculpt")
    check("not one coordinate moved",
          bool(numpy.array_equal(before, after)),
          "max delta %g" % float(numpy.abs(before - after).max()))
    check("the report says nothing was applied", data["applied"] is False)
    check("and that zero vertices moved", data["moved"] == 0)
    check("the mesh was not written to at all, and says so",
          any("not written to" in n for n in data["notes"]), str(data["notes"]))
    view = data["views"][0]
    check("before and after are the same number, because nothing happened",
          view["iou_before"]["value"] == view["iou_after"]["value"],
          "%s vs %s" % (view["iou_before"]["value"], view["iou_after"]["value"]))
    check("the measurement is still real (a sphere is not an ellipse)",
          0.3 < view["iou_before"]["value"] < 0.75,
          str(view["iou_before"]["value"]))


def test_strength_is_a_lerp(tmpdir):
    section("strength — half the movement, half way there")
    clear_scene()
    picture = write_mask(os.path.join(tmpdir, "lerp.png"),
                         ellipse_mask(512, 512, 100, 200))
    widths = {}
    for strength in (0.25, 1.0):
        clear_scene()
        make_sphere("Sculpt")
        reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                    strength=strength, iterations=1, smooth=0.0)
        if not ok(reply):
            check("strength=%s ran" % strength, False, message(reply)[:200])
            return
        widths[strength] = result(reply)["dimensions_after_mm"][0]
    note("width after one pass: 25%% -> %.1f mm, 100%% -> %.1f mm"
         % (widths[0.25], widths[1.0]))
    check("a quarter-strength pass moves less than a full one",
          widths[0.25] > widths[1.0] + 100.0,
          "%.1f vs %.1f" % (widths[0.25], widths[1.0]))
    check("and it moves in the right direction",
          widths[0.25] < SPHERE_MM - 50.0, "%.1f mm" % widths[0.25])


def test_falloff_holds_the_interior(tmpdir):
    section("falloff — move the rim, leave the core")
    clear_scene()
    picture = write_mask(os.path.join(tmpdir, "falloff.png"),
                         ellipse_mask(512, 512, 100, 200))
    interior = {}
    outline = {}
    for falloff in (0.0, 3.0):
        clear_scene()
        make_sphere("Sculpt")
        before = coords_of("Sculpt")
        reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                    falloff=falloff, iterations=2, smooth=0.0)
        if not ok(reply):
            check("falloff=%s ran" % falloff, False, message(reply)[:200])
            return
        after = coords_of("Sculpt")
        # "Interior" is a fact about the PROJECTION: how far a vertex lands from
        # the centre of the front view, not how far it is in 3D.
        projected = numpy.hypot(before[:, 0], before[:, 2])
        core = projected < 0.5 * projected.max()
        moved = numpy.linalg.norm(after - before, axis=1) * 1000.0
        interior[falloff] = float(moved[core].mean())
        outline[falloff] = result(reply)["views"][0]["iou_after"]["value"]
    note("interior vertices moved %.1f mm at falloff 0 and %.1f mm at falloff 3; "
         "outline IoU %.4f vs %.4f"
         % (interior[0.0], interior[3.0], outline[0.0], outline[3.0]))
    check("a high falloff barely moves the interior — the core stays put",
          interior[3.0] < interior[0.0] * 0.35,
          "%.1f vs %.1f mm" % (interior[3.0], interior[0.0]))
    check("and the outline still lands on the reference either way",
          outline[3.0] > 0.9 and outline[0.0] > 0.9,
          "%.4f / %.4f" % (outline[0.0], outline[3.0]))


def test_symmetry(tmpdir):
    section("symmetry — a lopsided photograph must not make a lopsided sculpt")
    from forge.tools import verify

    picture = write_mask(os.path.join(tmpdir, "lopsided.png"),
                         lopsided_mask(512, 512, 90, 170, 200))
    residuals = {}
    for symmetry in (False, True):
        clear_scene()
        make_sphere("Sculpt", segments=32, rings=16)
        reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                    symmetry=symmetry, iterations=4, smooth=0.3)
        if not ok(reply):
            check("symmetry=%s ran" % symmetry, False, message(reply)[:300])
            return
        data = result(reply)
        residual = verify.symmetry_residual(bpy.data.objects["Sculpt"].data, "X")
        residuals[symmetry] = residual["mean_mm"]["value"]
        if symmetry:
            check("the report names the mirror axis", data["symmetry"] == "X",
                  str(data["symmetry"]))
            check("and where the mirror plane is, in mm",
                  data["symmetry_plane_mm"] is not None,
                  str(data["symmetry_plane_mm"]))
        else:
            check("symmetry off is reported as off", data["symmetry"] is None)
    note("mirror residual: off %.2f mm, on %.2f mm"
         % (residuals[False], residuals[True]))
    check("a lopsided reference makes an asymmetric mesh when symmetry is off",
          residuals[False] > 20.0, "%.2f mm" % residuals[False])
    check("and symmetry on pulls it back onto its own mirror",
          residuals[True] < residuals[False] * 0.25,
          "%.2f mm vs %.2f mm" % (residuals[True], residuals[False]))


def test_iterations_converge(tmpdir):
    section("iterations — the passes after the first pay for the smoothing")
    clear_scene()
    picture = write_mask(os.path.join(tmpdir, "converge.png"),
                         ellipse_mask(512, 512, 90, 200))
    scores = {}
    for iterations in (1, 6):
        clear_scene()
        make_sphere("Sculpt")
        reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                    iterations=iterations, smooth=0.8)
        if not ok(reply):
            check("iterations=%d ran" % iterations, False, message(reply)[:200])
            return
        scores[iterations] = result(reply)["views"][0]["iou_after"]["value"]
    note("IoU after 1 pass %.4f, after 6 passes %.4f" % (scores[1], scores[6]))
    check("more passes recover the accuracy heavy smoothing gives away",
          scores[6] >= scores[1], "%.4f vs %.4f" % (scores[6], scores[1]))
    check("and six passes at smooth=0.8 still land above 0.9",
          scores[6] > 0.90, str(scores[6]))


def test_smoothing_never_touches_the_mesh(tmpdir):
    section("smooth smooths the DISPLACEMENT, not the sculpt")
    clear_scene()
    make_sphere("Sculpt", segments=32, rings=16)
    # A deliberate spike: one vertex pulled out along its own normal. If the
    # command smoothed the MESH, this would be gone; it must survive.
    mesh = bpy.data.objects["Sculpt"].data
    spike_index = len(mesh.vertices) // 2
    spike = mesh.vertices[spike_index].co.copy()
    mesh.vertices[spike_index].co = spike * 1.35
    before = coords_of("Sculpt")
    neighbours_before = float(numpy.linalg.norm(before[spike_index])
                              - numpy.median(numpy.linalg.norm(before, axis=1)))

    picture = write_mask(os.path.join(tmpdir, "spike.png"),
                         ellipse_mask(512, 512, 100, 200))
    reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                smooth=1.0, iterations=3)
    if not check("a fully-smoothed fit runs", ok(reply), message(reply)[:300]):
        return
    after = coords_of("Sculpt")
    neighbours_after = float(numpy.linalg.norm(after[spike_index])
                             - numpy.median(numpy.linalg.norm(after, axis=1)))
    note("the spike stood %.1f mm proud before and %.1f mm after"
         % (neighbours_before * 1000.0, neighbours_after * 1000.0))
    check("a sculpted spike survives smooth=1.0 — the mesh is never smoothed",
          neighbours_after > neighbours_before * 0.6,
          "%.4f -> %.4f" % (neighbours_before, neighbours_after))


# ---------------------------------------------------------------------------
# 5. the mask machinery
# ---------------------------------------------------------------------------

def test_mask_sources(tmpdir):
    section("where the silhouette came from, and how much to trust it")
    clear_scene()
    make_sphere("Sculpt")
    mask = ellipse_mask(400, 400, 90, 180)
    cutout = write_mask(os.path.join(tmpdir, "cutout.png"), mask, alpha=True)
    flat = write_mask(os.path.join(tmpdir, "flat.png"), mask, alpha=False)

    data = result(fit(object="Sculpt", views=[{"image": cutout, "axis": "front"}],
                      strength=0.0))
    view = data["views"][0]
    check("an alpha cut-out is read off the alpha channel",
          view["mask"]["source"]["value"] == "alpha",
          str(view["mask"]["source"]))
    check("and is tiered 'measured' — no guessing was involved",
          view["mask"]["source"]["tier"] == "measured")
    check("with high confidence", view["confidence"] == "high",
          view["confidence"])

    data = result(fit(object="Sculpt", views=[{"image": flat, "axis": "front"}],
                      strength=0.0))
    view = data["views"][0]
    check("a flat-background picture is thresholded instead",
          view["mask"]["source"]["value"] == "background threshold",
          str(view["mask"]["source"]))
    check("and is tiered 'heuristic', shouting the assumption it made",
          view["mask"]["source"]["tier"] == "heuristic"
          and "PLAIN BACKGROUND" in view["mask"]["source"]["note"],
          str(view["mask"]["source"])[:200])
    check("the threshold that was actually used is in the report",
          view["mask"]["threshold"] is not None, str(view["mask"]["threshold"]))
    check("so is how uniform the background really was",
          view["mask"]["background_uniformity"] is not None)

    data = result(fit(object="Sculpt",
                      views=[{"image": flat, "axis": "front", "threshold": 0.6}],
                      strength=0.0))
    check("an explicit threshold is honoured and reported",
          data["views"][0]["mask"]["threshold"] == 0.6,
          str(data["views"][0]["mask"]["threshold"]))


def test_a_speck_is_warned_about(tmpdir):
    section("a stray pixel decides the bounding box — say so, do not crop it")
    clear_scene()
    make_sphere("Sculpt")
    mask = ellipse_mask(400, 400, 80, 160)
    mask[4, 4] = True  # a watermark, a dust spot, a signature
    picture = write_mask(os.path.join(tmpdir, "speck.png"), mask)
    data = result(fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                      strength=0.0))
    check("the outlying pixel is warned about rather than silently trimmed",
          any("outlying pixels" in w for w in data["warnings"]),
          str(data["warnings"])[:300])


def test_shape_keys_are_warned_about(tmpdir):
    section("shape keys — the fit lands on the base mesh, and says so")
    clear_scene()
    obj = make_sphere("Sculpt")
    obj.shape_key_add(name="Basis")
    obj.shape_key_add(name="Squash")
    picture = write_mask(os.path.join(tmpdir, "keys.png"),
                         ellipse_mask(400, 400, 80, 160))
    data = result(fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                      iterations=1))
    check("the shape keys are counted", data["shape_keys"] == 2,
          str(data["shape_keys"]))
    check("and the caveat is a warning, not a footnote",
          any("shape key" in w for w in data["warnings"]),
          str(data["warnings"])[:300])


# ---------------------------------------------------------------------------
# 5b. character forms — as_shape_key
# ---------------------------------------------------------------------------
#
# The claim under test is a strong one and it is tested as a strong one: a
# shape-key fit is not "close to" a destructive fit, it is the SAME float32
# array written somewhere else, so every comparison below is
# ``numpy.array_equal`` rather than a tolerance.
#
# WHY THE CONTROL RUNS ON THE SAME OBJECT, LAST. Two *separate* meshes of
# identical geometry do NOT produce identical fits, and it is worth writing down
# why, because the obvious test (fit a copy destructively, compare) fails for a
# reason that has nothing to do with shape keys: Blender's loop-triangle
# tessellation returns the same SET of triangles in a DIFFERENT ORDER each time
# it is computed (measured here: 960 of 960 rows differ between two pristine
# copies of one sphere, and the sorted sets are equal). The rasteriser hands its
# seeded samples out per triangle in array order, so a re-ordered tessellation
# lands the samples in different places and the fit moves by ~0.3% of the model
# (5 mm on a 2000 mm sphere). The same datablock keeps one cached tessellation,
# so a key fit followed by a destructive fit of the same mesh IS bit-identical —
# and the key fit leaves the base pristine, which is what makes that control
# legal. Measured stable over six trials at two densities before it was relied
# on here.

#: One set of knobs for every fit in this section, so "the same fit" means the
#: same fit and a difference can only come from where it was written.
FORM_PARAMS = {"strength": 1.0, "iterations": 3, "smooth": 0.4}

#: How far apart two fits of two SEPARATE but identical meshes may land, as a
#: fraction of the model, given the tessellation-order noise described above.
CROSS_MESH_NOISE = 0.01


def key_names(name):
    keys = bpy.data.objects[name].data.shape_keys
    return [block.name for block in keys.key_blocks] if keys else []


def key_coords(name, key):
    """A shape key's own coordinates as an ``(n, 3)`` array."""
    block = bpy.data.objects[name].data.shape_keys.key_blocks[key]
    flat = numpy.empty(len(block.data) * 3, dtype="f8")
    block.data.foreach_get("co", flat)
    return flat.reshape(-1, 3)


def evaluated_coords(name):
    """What the viewport actually shows — the mesh after the keys are blended."""
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    obj = bpy.data.objects[name].evaluated_get(deps)
    mesh = obj.to_mesh()
    flat = numpy.empty(len(mesh.vertices) * 3, dtype="f8")
    mesh.vertices.foreach_get("co", flat)
    out = flat.reshape(-1, 3).copy()
    obj.to_mesh_clear()
    return out


def set_key_values(name, value):
    keys = bpy.data.objects[name].data.shape_keys
    for block in keys.key_blocks[1:]:
        block.value = value


def destructive_control(name, picture, **extra):
    """Fit ``name``'s BASE mesh — the control, and always the last fit of it."""
    params = dict(FORM_PARAMS)
    params.update(extra)
    reply = fit(object=name, views=[{"image": picture, "axis": "front"}],
                **params)
    if not ok(reply):
        return None, reply
    return coords_of(name), reply


def test_as_shape_key_absent_is_todays_behaviour(tmpdir):
    section("as_shape_key absent — the base mesh is deformed, exactly as before")
    picture = write_mask(os.path.join(tmpdir, "form_absent.png"),
                         ellipse_mask(512, 512, 100, 200))
    clear_scene()
    make_sphere("Sculpt")
    first, reply = destructive_control("Sculpt", picture)
    if not check("the plain fit still runs", first is not None,
                 message(reply)[:300]):
        return
    data = result(reply)
    check("no shape key is named, because none was asked for",
          data["shape_key"] is None, str(data["shape_key"]))
    check("nothing was overwritten and no basis was invented",
          data["shape_key_overwritten"] is False
          and data["basis_created"] is False,
          "%s / %s" % (data["shape_key_overwritten"], data["basis_created"]))
    check("the mesh still has no shape keys at all",
          bpy.data.objects["Sculpt"].data.shape_keys is None,
          str(key_names("Sculpt")))
    check("the base vertices are the ones that moved",
          data["applied"] is True and data["moved"] > 0
          and abs(data["dimensions_after_mm"][0] - SPHERE_MM) > 100.0,
          "moved %s, width %s mm" % (data["moved"],
                                     data["dimensions_after_mm"][0]))
    check("and the morph caveat is NOT in the honesty block — there is no morph",
          "LINEAR VERTEX INTERPOLATION" not in data["honesty"],
          data["honesty"][-120:])

    # The same fit on a separate copy: near, but NOT bit-identical, and the
    # comment at the top of this section says exactly why. Asserted so the day
    # Blender's tessellation order becomes stable is a day somebody notices.
    clear_scene()
    make_sphere("Sculpt")
    second, _reply = destructive_control("Sculpt", picture)
    delta = (float(numpy.abs(first - second).max()) * 1000.0
             if second is not None else float("nan"))
    note("the same fit on a separate copy of the same sphere lands %.3f mm away "
         "(tessellation order, not the fit)" % delta)
    check("the same fit of a separate copy lands within %.0f%% of the model"
          % (CROSS_MESH_NOISE * 100.0),
          second is not None and delta < CROSS_MESH_NOISE * SPHERE_MM,
          "%.3f mm" % delta)


def test_the_fit_becomes_a_shape_key(tmpdir):
    section("as_shape_key — the fitted form is a morph target, not an edit")
    picture = write_mask(os.path.join(tmpdir, "form_wolf.png"),
                         ellipse_mask(512, 512, 100, 200))
    clear_scene()
    make_sphere("Sculpt")
    pristine = coords_of("Sculpt")
    reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                as_shape_key="werewolf-form", **FORM_PARAMS)
    if not check("the shape-key fit ran", ok(reply), message(reply)[:400]):
        return
    data = result(reply)
    note("key %r, basis created %s, dimensions %s mm"
         % (data["shape_key"], data["basis_created"],
            data["dimensions_after_mm"]))

    check("the report names the key it wrote",
          data["shape_key"] == "werewolf-form", str(data["shape_key"]))
    check("nothing was overwritten the first time",
          data["shape_key_overwritten"] is False,
          str(data["shape_key_overwritten"]))
    check("a Basis was created, because a morph needs the shape it morphs from",
          data["basis_created"] is True, str(data["basis_created"]))
    check("and the mesh now carries exactly Basis + the form",
          key_names("Sculpt") == ["Basis", "werewolf-form"],
          str(key_names("Sculpt")))

    check("the BASE MESH is bit-identical to the sculpt that went in",
          bool(numpy.array_equal(pristine, coords_of("Sculpt"))),
          "max delta %g" % float(numpy.abs(pristine
                                           - coords_of("Sculpt")).max()))
    check("so is the Basis key",
          bool(numpy.array_equal(pristine, key_coords("Sculpt", "Basis"))),
          "max delta %g" % float(numpy.abs(pristine
                                           - key_coords("Sculpt", "Basis")).max()))

    fitted = key_coords("Sculpt", "werewolf-form")
    check("the key holds the fit, not the base",
          not numpy.array_equal(fitted, pristine),
          "max delta %g" % float(numpy.abs(fitted - pristine).max()))

    check("the key is left at value 1, so the artist sees what was just fitted",
          bpy.data.objects["Sculpt"].data.shape_keys
          .key_blocks["werewolf-form"].value == 1.0)
    at_one = evaluated_coords("Sculpt")
    check("and the evaluated mesh at value 1 IS the fitted form",
          float(numpy.abs(at_one - fitted).max()) < 1e-6,
          "max delta %g" % float(numpy.abs(at_one - fitted).max()))
    set_key_values("Sculpt", 0.0)
    at_zero = evaluated_coords("Sculpt")
    check("at value 0 the evaluated mesh is the pristine base, bit-identically",
          bool(numpy.array_equal(at_zero, pristine)),
          "max delta %g" % float(numpy.abs(at_zero - pristine).max()))
    set_key_values("Sculpt", 1.0)

    check("the honesty block says the morph is linear vertex interpolation",
          "LINEAR VERTEX INTERPOLATION" in data["honesty"],
          data["honesty"][-200:])
    check("and names the fix — an eased value curve or a mid-form key",
          "F-curve" in data["honesty"] and "mid-form" in data["honesty"],
          data["honesty"][-200:])
    check("the notes say the base mesh keeps the coordinates it has",
          any("value 0 is the sculpt" in n for n in data["notes"]),
          str(data["notes"])[:300])
    check("and that a Basis had to be created",
          any("had no shape keys" in n for n in data["notes"]),
          str(data["notes"])[:300])
    check("the method says why writing a key is legal at all — topology",
          "topology" in data["method"], data["method"][-200:])

    # The control, last: the base is still pristine, so fitting it destructively
    # now re-runs exactly the fit that made the key, on the same datablock.
    wanted, reply = destructive_control("Sculpt", picture)
    if not check("the destructive control ran on the same base", wanted is not None,
                 message(reply)[:300]):
        return
    check("the key is BIT-IDENTICAL to the destructive fit of the same mesh",
          bool(numpy.array_equal(fitted, wanted)),
          "max delta %g" % float(numpy.abs(fitted - wanted).max()))


def test_refitting_a_form_overwrites_its_key(tmpdir):
    section("the same name twice — iterate the form, never duplicate the key")
    first_pic = write_mask(os.path.join(tmpdir, "form_v1.png"),
                           ellipse_mask(512, 512, 100, 200))
    second_pic = write_mask(os.path.join(tmpdir, "form_v2.png"),
                            ellipse_mask(512, 512, 170, 200))
    clear_scene()
    make_sphere("Sculpt")
    pristine = coords_of("Sculpt")
    fit(object="Sculpt", views=[{"image": first_pic, "axis": "front"}],
        as_shape_key="werewolf-form", **FORM_PARAMS)
    reply = fit(object="Sculpt", views=[{"image": second_pic, "axis": "front"}],
                as_shape_key="werewolf-form", **FORM_PARAMS)
    if not check("refitting the same form runs", ok(reply), message(reply)[:300]):
        return
    data = result(reply)
    check("the report says the key was overwritten",
          data["shape_key_overwritten"] is True,
          str(data["shape_key_overwritten"]))
    check("and the notes say so in words, not just a flag",
          any("overwritten in place" in n for n in data["notes"]),
          str(data["notes"])[:400])
    check("no Basis was created the second time — there already was one",
          data["basis_created"] is False, str(data["basis_created"]))
    check("there is still exactly ONE key of that name",
          key_names("Sculpt") == ["Basis", "werewolf-form"],
          str(key_names("Sculpt")))
    check("and the base mesh is still the sculpt that went in",
          bool(numpy.array_equal(pristine, coords_of("Sculpt"))),
          "max delta %g" % float(numpy.abs(pristine
                                           - coords_of("Sculpt")).max()))
    fitted = key_coords("Sculpt", "werewolf-form")
    wanted, reply = destructive_control("Sculpt", second_pic)
    if not check("the control ran on the same, still-pristine base",
                 wanted is not None, message(reply)[:300]):
        return
    check("the key now holds the SECOND fit, bit-identically",
          bool(numpy.array_equal(fitted, wanted)),
          "max delta %g" % float(numpy.abs(fitted - wanted).max()))


def test_two_forms_coexist_on_one_base(tmpdir):
    section("human -> teen-wolf -> werewolf: two forms, one mesh, one topology")
    teen_pic = write_mask(os.path.join(tmpdir, "form_teen.png"),
                          ellipse_mask(512, 512, 130, 200))
    wolf_pic = write_mask(os.path.join(tmpdir, "form_full.png"),
                          ellipse_mask(512, 512, 190, 200))

    # The control has to be the LAST fit of a mesh (it moves the base), so the
    # two-form scene is built twice and each copy is controlled for one of its
    # forms. Same scenario, one claim each.
    forms = {}
    for controlled in ("teen-wolf", "werewolf"):
        clear_scene()
        make_sphere("Sculpt")
        pristine = coords_of("Sculpt")
        reply = fit(object="Sculpt", views=[{"image": teen_pic, "axis": "front"}],
                    as_shape_key="teen-wolf", **FORM_PARAMS)
        if not check("the first form was fitted", ok(reply), message(reply)[:300]):
            return
        reply = fit(object="Sculpt", views=[{"image": wolf_pic, "axis": "front"}],
                    as_shape_key="werewolf", **FORM_PARAMS)
        if not check("the second form was fitted onto the same base", ok(reply),
                     message(reply)[:300]):
            return
        data = result(reply)

        check("both forms sit on one mesh, in the order they were fitted",
              key_names("Sculpt") == ["Basis", "teen-wolf", "werewolf"],
              str(key_names("Sculpt")))
        check("the second fit reports its own key and no overwrite",
              data["shape_key"] == "werewolf"
              and data["shape_key_overwritten"] is False,
              str(data["shape_key"]))
        check("the base mesh survived both fits bit-identically",
              bool(numpy.array_equal(pristine, coords_of("Sculpt"))),
              "max delta %g" % float(numpy.abs(pristine
                                               - coords_of("Sculpt")).max()))
        set_key_values("Sculpt", 0.0)
        check("and with every form at 0 the viewport shows the human, exactly",
              bool(numpy.array_equal(evaluated_coords("Sculpt"), pristine)),
              "max delta %g" % float(numpy.abs(evaluated_coords("Sculpt")
                                               - pristine).max()))
        set_key_values("Sculpt", 1.0)
        check("stacking two forms above 0 is WARNED about, because keys add up",
              any("ADDITIVE" in w for w in data["warnings"]),
              str(data["warnings"])[:300])

        forms[controlled] = {name: key_coords("Sculpt", name).copy()
                             for name in ("teen-wolf", "werewolf")}
        check("the two forms are different shapes, not the same fit twice",
              not numpy.array_equal(forms[controlled]["teen-wolf"],
                                    forms[controlled]["werewolf"]))

        picture = teen_pic if controlled == "teen-wolf" else wolf_pic
        wanted, reply = destructive_control("Sculpt", picture)
        if not check("the %s control ran on the untouched base" % controlled,
                     wanted is not None, message(reply)[:300]):
            return
        check("%s still reproduces ITS OWN fit exactly, with the other form "
              "sitting beside it" % controlled,
              bool(numpy.array_equal(forms[controlled][controlled], wanted)),
              "max delta %g" % float(numpy.abs(
                  forms[controlled][controlled] - wanted).max()))


def test_a_form_on_a_mesh_that_already_has_keys(tmpdir):
    section("an existing Basis is used, not replaced")
    clear_scene()
    obj = make_sphere("Sculpt")
    obj.shape_key_add(name="Basis", from_mix=False)
    obj.shape_key_add(name="Squash", from_mix=False)
    pristine = coords_of("Sculpt")
    picture = write_mask(os.path.join(tmpdir, "form_existing.png"),
                         ellipse_mask(512, 512, 100, 200))
    reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                as_shape_key="werewolf-form", **FORM_PARAMS)
    if not check("the fit ran on a mesh that already had keys", ok(reply),
                 message(reply)[:300]):
        return
    data = result(reply)
    check("no second Basis was invented", data["basis_created"] is False,
          str(data["basis_created"]))
    check("the existing keys are left where they were",
          key_names("Sculpt") == ["Basis", "Squash", "werewolf-form"],
          str(key_names("Sculpt")))
    check("the old 'the fit landed on the base mesh' warning is NOT raised — "
          "it would be false here",
          not any("written to the base mesh" in w for w in data["warnings"]),
          str(data["warnings"])[:300])
    check("and the base mesh really is untouched",
          bool(numpy.array_equal(pristine, coords_of("Sculpt"))),
          "max delta %g" % float(numpy.abs(pristine
                                           - coords_of("Sculpt")).max()))


def test_strength_scales_the_key_not_the_base(tmpdir):
    section("strength — half the form, still none of the base")
    picture = write_mask(os.path.join(tmpdir, "form_strength.png"),
                         ellipse_mask(512, 512, 100, 200))
    travel = {}
    for strength in (0.25, 1.0):
        clear_scene()
        make_sphere("Sculpt")
        pristine = coords_of("Sculpt")
        reply = fit(object="Sculpt", views=[{"image": picture, "axis": "front"}],
                    as_shape_key="werewolf-form", strength=strength,
                    iterations=1, smooth=0.0)
        if not check("strength=%s ran as a shape key" % strength, ok(reply),
                     message(reply)[:300]):
            return
        fitted = key_coords("Sculpt", "werewolf-form")
        travel[strength] = float(numpy.linalg.norm(fitted - pristine,
                                                   axis=1).max() * 1000.0)
        check("strength=%s left the base mesh bit-identical" % strength,
              bool(numpy.array_equal(pristine, coords_of("Sculpt"))),
              "max delta %g" % float(numpy.abs(pristine
                                               - coords_of("Sculpt")).max()))
    note("the key's worst displacement: 25%% -> %.1f mm, 100%% -> %.1f mm"
         % (travel[0.25], travel[1.0]))
    check("strength scales the KEY's displacement",
          travel[0.25] < travel[1.0] * 0.5,
          "%.1f vs %.1f mm" % (travel[0.25], travel[1.0]))
    check("and a quarter-strength form still moved something",
          travel[0.25] > 1.0, "%.3f mm" % travel[0.25])


def test_bad_shape_key_names(tmpdir):
    section("as_shape_key refusals")
    clear_scene()
    make_sphere("Sculpt")
    picture = write_mask(os.path.join(tmpdir, "form_bad.png"),
                         ellipse_mask(256, 256, 60, 110))
    views = [{"image": picture, "axis": "front"}]

    reply = fit(object="Sculpt", views=views, as_shape_key="   ")
    check("a blank key name is refused, and says what the parameter is for",
          not ok(reply) and "as_shape_key" in message(reply),
          message(reply)[:200])

    reply = fit(object="Sculpt", views=views, as_shape_key=7)
    check("a key name that is not a string is refused",
          not ok(reply) and "non-empty string" in message(reply),
          message(reply)[:200])

    reply = fit(object="Sculpt", views=views, as_shape_key="Basis")
    check("naming the basis is refused, with the reason",
          not ok(reply) and "cannot be 'Basis'" in message(reply),
          message(reply)[:250])
    check("and the refusal offers both real choices",
          "werewolf-form" in message(reply)
          and "leave 'as_shape_key' out" in message(reply),
          message(reply)[:300])
    check("nothing was written on the way to any of those refusals",
          bpy.data.objects["Sculpt"].data.shape_keys is None,
          str(key_names("Sculpt")))


# ---------------------------------------------------------------------------
# 6. refusals
# ---------------------------------------------------------------------------

def test_bad_input(tmpdir):
    section("bad input fails with a sentence")
    clear_scene()
    make_sphere("Sculpt")
    good = write_mask(os.path.join(tmpdir, "good.png"),
                      ellipse_mask(256, 256, 60, 110))

    reply = fit(object="Sculpt",
                views=[{"image": os.path.join(tmpdir, "nope.png"),
                        "axis": "front"}])
    check("an image that is not there fails with a sentence naming the path",
          not ok(reply) and "No image at" in message(reply), message(reply)[:200])

    reply = fit(object="Sculpt", views=[{"image": good, "axis": "fromt"}])
    check("a misspelled view is refused",
          not ok(reply) and "no 'fromt' view" in message(reply),
          message(reply)[:200])
    check("and comes back with the near miss, not a list to read",
          "Did you mean 'front'" in message(reply), message(reply)[:200])

    reply = fit(object="Sculpt", views=[{"image": good}])
    check("a view with no axis is refused with the vocabulary",
          not ok(reply) and "axis" in message(reply), message(reply)[:200])

    reply = fit(object="Sculpt", views=[{"axis": "front"}])
    check("a view with no image is refused",
          not ok(reply) and "missing 'image'" in message(reply),
          message(reply)[:200])

    reply = fit(object="Sculpt")
    check("no references at all is refused, and names both spellings",
          not ok(reply) and "front_image" in message(reply),
          message(reply)[:250])

    reply = fit(object="Sculpt", views=[])
    check("an empty views list is refused",
          not ok(reply) and "non-empty" in message(reply), message(reply)[:200])

    reply = fit(object="Sculpt", views=[{"image": good, "axis": "front"},
                                        {"image": good, "axis": "front"}])
    check("two views of the same side is refused, not silently merged",
          not ok(reply) and "One picture per side" in message(reply),
          message(reply)[:250])

    reply = fit(object="Sculpt",
                views=[{"image": good, "axis": "front", "threshold": 4.0}])
    check("a threshold outside 0-1 is refused",
          not ok(reply) and "threshold" in message(reply), message(reply)[:200])

    reply = fit(object="Sculpt", views=[{"image": good, "axis": "front"}],
                strength=5.0)
    check("a strength outside 0-1 is refused",
          not ok(reply) and "strength" in message(reply), message(reply)[:200])

    reply = fit(object="Nosuch", views=[{"image": good, "axis": "front"}])
    check("an object that is not there is refused with a suggestion",
          not ok(reply) and "no object called 'nosuch'" in message(reply).lower(),
          message(reply)[:200])
    check("and the suggestion names what IS in the file",
          "Sculpt" in message(reply), message(reply)[:200])

    bpy.ops.object.camera_add()
    bpy.context.view_layer.objects.active.name = "NotAMesh"
    reply = fit(object="NotAMesh", views=[{"image": good, "axis": "front"}])
    check("a camera is refused, and told what the command needs",
          not ok(reply) and "needs a MESH" in message(reply), message(reply)[:200])

    blank = write_mask(os.path.join(tmpdir, "blank.png"),
                       numpy.zeros((64, 64), dtype=bool))
    reply = fit(object="Sculpt", views=[{"image": blank, "axis": "front"}])
    check("a picture with no subject in it is refused with the fix",
          not ok(reply) and "plain, contrasting background" in message(reply),
          message(reply)[:250])


# ---------------------------------------------------------------------------
# 7. flows
# ---------------------------------------------------------------------------

def test_it_is_a_legal_flow_step(tmpdir):
    section("a flow can run it — proven by running one")
    clear_scene()
    make_sphere("Sculpt")
    picture = write_mask(os.path.join(tmpdir, "flow.png"),
                         ellipse_mask(400, 400, 80, 160))
    reply = flow_run(flow={
        "name": "fit-test",
        "description": "ping, then fit the sculpt to a silhouette",
        "params": {"picture": {"value": picture, "description": "the reference"}},
        "steps": [
            {"kind": "blender", "op": "ping", "args": {}},
            {"kind": "blender", "op": "fit_to_silhouette",
             "args": {"object": "Sculpt",
                      "views": [{"image": "{{picture}}", "axis": "front"}],
                      "iterations": 2}},
        ],
    })
    if not check("the flow ran", ok(reply), message(reply)[:400]):
        return
    steps = result(reply).get("steps") or []
    check("both steps ran", len(steps) == 2, str(len(steps)))
    last = steps[-1] if steps else {}
    check("the second step really is the fit",
          last.get("op") == "fit_to_silhouette", str(last)[:200])
    check("and it succeeded inside the flow",
          last.get("ok") is True, str(last)[:300])
    check("the flow's summary names the object it fitted",
          "Sculpt" in str(last.get("brief") or ""), str(last)[:200])


# ---------------------------------------------------------------------------
# 8. budget
# ---------------------------------------------------------------------------

def test_budget(tmpdir):
    section("the budget — a dense sphere, two references, four passes")
    clear_scene()
    make_sphere("Sculpt", segments=BUDGET_SEGMENTS, rings=BUDGET_RINGS)
    front = write_mask(os.path.join(tmpdir, "budget_front.png"),
                       ellipse_mask(512, 512, 110, 200))
    side = write_mask(os.path.join(tmpdir, "budget_side.png"),
                      ellipse_mask(512, 512, 150, 200))
    started = time.monotonic()
    reply = fit(object="Sculpt",
                views=[{"image": front, "axis": "front"},
                       {"image": side, "axis": "side"}],
                iterations=4)
    elapsed = time.monotonic() - started
    if not check("the dense fit ran", ok(reply), message(reply)[:300]):
        return
    data = result(reply)
    note("%d vertices, %d faces, 2 views, 4 passes in %.2f s (command reported "
         "%.2f s)" % (data["vertices"], data["faces"], elapsed, data["seconds"]))
    check("a %d-vertex two-view fit finishes inside %.0f s"
          % (data["vertices"], BUDGET_SECONDS),
          elapsed < BUDGET_SECONDS, "%.2f s" % elapsed)
    for view in data["views"]:
        check("the %s view still improves on a dense mesh" % view["resolved_axis"],
              view["iou_gain"] > 0.1, str(view["iou_gain"]))


def test_port_is_free_after():
    section("shutdown")
    from forge import server as forge_server

    forge_server.stop_server()
    time.sleep(0.2)
    try:
        conn = socketlib.create_connection(("127.0.0.1", PORT), timeout=0.5)
        conn.close()
        check("the socket closed with the server", False, "still accepting")
    except OSError:
        check("the socket closed with the server", True)


def main():
    print("Forge headless tests — Phase 18(b) fit_to_silhouette")
    enable_addon()
    tmpdir = tempfile.mkdtemp(prefix="forge_silhouette_")
    note("reference pictures in %s" % tmpdir)

    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)

    try:
        test_registration()
        test_single_view_fit(tmpdir)
        test_a_wider_reference_widens(tmpdir)
        test_front_and_side(tmpdir)
        test_the_shorthand_is_the_same_call(tmpdir)
        test_top_view(tmpdir)
        test_strength_zero_is_a_no_op(tmpdir)
        test_strength_is_a_lerp(tmpdir)
        test_falloff_holds_the_interior(tmpdir)
        test_symmetry(tmpdir)
        test_iterations_converge(tmpdir)
        test_smoothing_never_touches_the_mesh(tmpdir)
        test_mask_sources(tmpdir)
        test_a_speck_is_warned_about(tmpdir)
        test_shape_keys_are_warned_about(tmpdir)
        test_as_shape_key_absent_is_todays_behaviour(tmpdir)
        test_the_fit_becomes_a_shape_key(tmpdir)
        test_refitting_a_form_overwrites_its_key(tmpdir)
        test_two_forms_coexist_on_one_base(tmpdir)
        test_a_form_on_a_mesh_that_already_has_keys(tmpdir)
        test_strength_scales_the_key_not_the_base(tmpdir)
        test_bad_shape_key_names(tmpdir)
        test_bad_input(tmpdir)
        test_it_is_a_legal_flow_step(tmpdir)
        test_budget(tmpdir)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    failed = [label for label, ok_, _ in _RESULTS if not ok_]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
