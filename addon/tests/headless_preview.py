"""Headless add-on tests for ``render_preview`` — the assistant's eyes.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_preview.py

The socket port is **9891** (9876 belongs to a live session, 9879-9890 to the
earlier suites).  Nothing else is needed: no geometry service, no Claude CLI, no
network beyond loopback.  The "generated part" is built here as a millimetre
mesh and pushed in through ``load_mesh``, which is the real PartForge path, so
the suite carries its own fixtures and leaves nothing behind.

What is actually being proved:

1. ``render_preview`` is a protocol command, and a READ-ONLY one — looking at
   the scene must never eat the artist's undo step;
2. it writes a real PNG: magic bytes, an IHDR that says the resolution that was
   asked for, and a file big enough to be a picture rather than a header;
3. the framing is right — a lone cube parked 5 m from the origin comes out
   centred and filling the frame, not a speck in a corner and not an empty
   image.  Measured on the pixels, through Blender's own image loader;
4. all four views render, and they are four DIFFERENT pictures;
5. naming ``objects`` renders those objects and nothing else;
6. the scene comes back exactly as it was: no leftover camera, no leftover
   light, the render engine, output path, resolution, colour management and
   Workbench shading all restored, and every object's ``hide_render`` flag as it
   was found — including the ones the artist had already hidden;
7. the interactive-session guard, as far as ``--background`` allows: the command
   renders through ``bpy.ops.render.render``, never ``render.opengl``, and never
   reads or writes a VIEW_3D space's own shading, so a live session's viewport
   is untouched;
8. bad input (no path, unknown view, an impossible resolution, an object that
   does not exist, an empty scene) fails with a sentence rather than a
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

PORT = 9891  # not 9876 (live session) and not 9879..9890 (every other suite)

#: Small on purpose: every pixel test reads the image back in Python, and a
#: 256 px square is plenty to answer "is there a shape in the middle of this".
SMALL = 256

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


def _roundtrip(payload, timeout=180.0):
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


def render_preview(**params):
    return _roundtrip({"type": "render_preview", "params": params})


def load_mesh(**params):
    return _roundtrip({"type": "load_mesh", "params": params})


# ---------------------------------------------------------------------------
# fixtures — meshes in millimetres, the way the geometry service sends them
# ---------------------------------------------------------------------------

def box_mesh(size_mm, center_mm=(0.0, 0.0, 0.0)):
    """Axis-aligned cube of ``size_mm``, in the millimetre coordinates
    ``load_mesh`` expects."""
    half = size_mm / 2.0
    cx, cy, cz = center_mm
    vertices = [
        [cx - half, cy - half, cz - half], [cx + half, cy - half, cz - half],
        [cx + half, cy + half, cz - half], [cx - half, cy + half, cz - half],
        [cx - half, cy - half, cz + half], [cx + half, cy - half, cz + half],
        [cx + half, cy + half, cz + half], [cx - half, cy + half, cz + half],
    ]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
             [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return vertices, faces


def bowl_mesh(diameter_mm=120.0, height_mm=60.0, segments=48):
    """A cup-shaped part: the sort of thing PartForge generates and the sort of
    thing whose looks are worth a second opinion."""
    import math as _math

    radius = diameter_mm / 2.0
    inner = radius - 4.0
    vertices = []
    faces = []
    for ring, (r, z) in enumerate(((radius, 0.0), (radius, height_mm),
                                   (inner, height_mm), (inner, 4.0))):
        for i in range(segments):
            angle = 2.0 * _math.pi * i / segments
            vertices.append([r * _math.cos(angle), r * _math.sin(angle), z])
    for ring in range(3):
        base = ring * segments
        for i in range(segments):
            j = (i + 1) % segments
            faces.append([base + i, base + j, base + segments + j, base + segments + i])
    # floor: fan from a centre vertex at the inside bottom, and the outside base
    inner_bottom = len(vertices)
    vertices.append([0.0, 0.0, 4.0])
    outer_bottom = len(vertices)
    vertices.append([0.0, 0.0, 0.0])
    for i in range(segments):
        j = (i + 1) % segments
        faces.append([3 * segments + j, 3 * segments + i, inner_bottom])
        faces.append([i, j, outer_bottom])
    return vertices, faces


# ---------------------------------------------------------------------------
# reading a PNG back — header without a library, pixels through Blender
# ---------------------------------------------------------------------------

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(path):
    """``(width, height)`` straight out of the IHDR chunk, or ``None``."""
    with open(path, "rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or not head.startswith(PNG_MAGIC) or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def pixel_stats(path):
    """Mean, spread and where the subject sits, read through bpy's own loader.

    Returns a dict, or ``None`` if the file will not open.  The image datablock
    is always removed again: a preview that quietly filled ``bpy.data.images``
    would break the state test two functions down.
    """
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError:
        return None
    try:
        width, height = int(image.size[0]), int(image.size[1])
        if width <= 0 or height <= 0:
            return None
        channels = int(image.channels) or 4
        try:
            import numpy

            flat = numpy.empty(width * height * channels, dtype="f")
            image.pixels.foreach_get(flat)
            values = flat.reshape(height, width, channels)[:, :, :3]
            luma = values.mean(axis=2)
            mean = float(luma.mean())
            spread = float(luma.std())
            corner = float(luma[0, 0])
            subject = numpy.abs(luma - corner) > 0.02
            covered = float(subject.mean())
            if covered > 0:
                rows, cols = numpy.nonzero(subject)
                centre = (float(cols.mean()) / width, float(rows.mean()) / height)
            else:
                centre = (0.5, 0.5)
        except ImportError:  # pragma: no cover - Blender ships numpy
            raw = list(image.pixels)
            luma = [sum(raw[i:i + 3]) / 3.0
                    for i in range(0, len(raw), channels)]
            mean = sum(luma) / len(luma)
            spread = (sum((v - mean) ** 2 for v in luma) / len(luma)) ** 0.5
            corner = luma[0]
            hits = [i for i, v in enumerate(luma) if abs(v - corner) > 0.02]
            covered = len(hits) / float(len(luma))
            if hits:
                centre = (sum(i % width for i in hits) / len(hits) / width,
                          sum(i // width for i in hits) / len(hits) / height)
            else:
                centre = (0.5, 0.5)
        return {"width": width, "height": height, "mean": mean, "spread": spread,
                "covered": covered, "centre": centre}
    finally:
        try:
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass


# ---------------------------------------------------------------------------
# scene state — the whole point of the try/finally in the command
# ---------------------------------------------------------------------------

_SHADING_FIELDS = (
    "light", "color_type", "single_color", "background_type", "background_color",
    "show_shadows", "show_specular_highlight", "show_cavity", "cavity_type",
    "show_object_outline", "show_xray",
)


def scene_state():
    scene = bpy.context.scene
    render = scene.render
    shading = scene.display.shading
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
        "camera_objects": sorted(o.name for o in bpy.data.objects if o.type == "CAMERA"),
        "lights": sorted(lamp.name for lamp in bpy.data.lights),
        "light_objects": sorted(o.name for o in bpy.data.objects if o.type == "LIGHT"),
        "objects": sorted(o.name for o in bpy.data.objects),
        "render": frozen(render, ("engine", "filepath", "resolution_x", "resolution_y",
                                  "resolution_percentage", "film_transparent",
                                  "use_overwrite", "use_file_extension", "use_border")),
        "image_settings": frozen(render.image_settings,
                                 ("file_format", "color_mode", "color_depth")),
        "display_aa": getattr(scene.display, "render_aa", None),
        "shading": frozen(shading, _SHADING_FIELDS),
        "view_settings": frozen(view_settings, ("view_transform", "look", "exposure",
                                                "gamma")) if view_settings else {},
        "hide_render": {o.name: o.hide_render for o in bpy.data.objects},
        "hide_viewport": {o.name: o.hide_viewport for o in bpy.data.objects},
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

    check("render_preview is a protocol command", registry.has_command("render_preview"))
    check("it is READ-ONLY, so looking never costs an undo step",
          "render_preview" in registry.READ_ONLY_COMMANDS)
    check("and push_undo refuses to push one for it",
          registry.push_undo("render_preview") is False)
    check("every older command is untouched",
          all(registry.has_command(name) for name in
              ("ping", "load_mesh", "load_meshes", "load_reference", "export_stl",
               "partforge_open", "flow_list", "flow_run", "check_model",
               "import_generated", "rigforge_tag")),
          "%d commands registered" % len(registry.command_names()))


def test_a_generated_part(tmpdir):
    section("a preview of a generated part")
    vertices, faces = bowl_mesh()
    reply = load_mesh(name="bowl", vertices=vertices, faces=faces, replace=True)
    if not check("the part loaded into the scene", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return None

    path = os.path.join(tmpdir, "bowl.png")
    reply = render_preview(path=path, resolution=SMALL)
    if not check("render_preview succeeded", reply.get("status") == "success",
                 str(reply.get("message"))[:600]):
        return None
    result = reply["result"]

    check("the file it named exists", os.path.exists(path), path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    check("and it is a picture, not a stub (> 2 kB)", size > 2048, "%d bytes" % size)
    with open(path, "rb") as handle:
        magic = handle.read(8)
    check("with PNG magic bytes", magic == PNG_MAGIC, repr(magic))
    check("and an IHDR that says %d x %d" % (SMALL, SMALL),
          png_size(path) == (SMALL, SMALL), str(png_size(path)))

    check("the contract's four fields come back",
          {"path", "objects", "resolution", "view"} <= set(result), str(sorted(result)))
    check("path is the absolute file that was written",
          os.path.normcase(result.get("path", "")) == os.path.normcase(path),
          str(result.get("path")))
    check("objects names the part it framed", result.get("objects") == ["bowl"],
          str(result.get("objects")))
    check("resolution echoes what was asked for", result.get("resolution") == SMALL,
          str(result.get("resolution")))
    check("view defaults to iso", result.get("view") == "iso", str(result.get("view")))
    check("shading defaults to solid Workbench",
          result.get("shading") == "solid"
          and result.get("engine") == "BLENDER_WORKBENCH",
          "%s / %s" % (result.get("shading"), result.get("engine")))
    check("it reports the size it framed, in millimetres",
          abs(result.get("bounds_mm", {}).get("size", [0])[0] - 120.0) < 0.5,
          str(result.get("bounds_mm")))

    stats = pixel_stats(path)
    if check("the render can be read back as pixels", stats is not None):
        check("there is actually a shape in it (the image is not flat)",
              stats["spread"] > 0.01, "spread %.4f" % stats["spread"])
    note("bowl preview: %s" % path)
    return path


def test_framing_far_from_origin(tmpdir):
    section("framing — a lone cube 5 m from the origin")
    vertices, faces = box_mesh(40.0, center_mm=(5000.0, -4000.0, 3000.0))
    reply = load_mesh(name="FarCube", vertices=vertices, faces=faces, replace=True)
    if not check("the far cube loaded", reply.get("status") == "success",
                 str(reply.get("message"))[:300]):
        return

    path = os.path.join(tmpdir, "farcube.png")
    reply = render_preview(path=path, objects=["FarCube"], resolution=SMALL)
    if not check("it rendered", reply.get("status") == "success",
                 str(reply.get("message"))[:600]):
        return
    result = reply["result"]
    check("only the cube was framed", result.get("objects") == ["FarCube"],
          str(result.get("objects")))
    check("and the framing is not the whole scene",
          result.get("framed_all_visible") is False,
          str(result.get("framed_all_visible")))

    stats = pixel_stats(path)
    if not check("the render reads back", stats is not None):
        return
    check("the image is NOT empty — the camera found the cube",
          stats["spread"] > 0.01, "spread %.4f" % stats["spread"])
    check("the cube fills a real part of the frame (5-95%%)",
          0.05 < stats["covered"] < 0.95, "covered %.1f%%" % (stats["covered"] * 100.0))
    x, y = stats["centre"]
    check("and it is centred, not shoved in a corner",
          abs(x - 0.5) < 0.12 and abs(y - 0.5) < 0.12,
          "centre %.3f, %.3f" % (x, y))
    check("the reported bounds are the cube's own, 5 m out",
          abs(result.get("bounds_mm", {}).get("min", [0])[0] - 4980.0) < 1.0,
          str(result.get("bounds_mm", {}).get("min")))
    note("a cube parked at (5000, -4000, 3000) mm still comes out centred: "
         "the camera is fitted to the bounds, not parked at the origin")


def test_every_view(tmpdir):
    section("all four views")
    signatures = {}
    for view in ("iso", "front", "side", "top"):
        path = os.path.join(tmpdir, "view_%s.png" % view)
        reply = render_preview(path=path, objects=["bowl"], view=view, resolution=SMALL)
        if not check("%s rendered" % view, reply.get("status") == "success",
                     str(reply.get("message"))[:400]):
            continue
        check("%s echoes its own view back" % view,
              reply["result"].get("view") == view, str(reply["result"].get("view")))
        check("%s wrote a real PNG" % view,
              png_size(path) == (SMALL, SMALL) and os.path.getsize(path) > 2048,
              "%s / %s bytes" % (png_size(path), os.path.getsize(path)))
        stats = pixel_stats(path)
        if stats is not None:
            check("%s is not an empty frame" % view, stats["spread"] > 0.01,
                  "spread %.4f" % stats["spread"])
        with open(path, "rb") as handle:
            signatures[view] = handle.read()
    check("the four views are four different pictures",
          len({bytes(v) for v in signatures.values()}) == len(signatures),
          "%d unique of %d" % (len({bytes(v) for v in signatures.values()}),
                               len(signatures)))
    note("front/side/top match load_reference's three exactly, so a front render "
         "and a front reference can be held up against each other")


def test_objects_subset(tmpdir):
    section("naming objects renders those objects only")
    path_one = os.path.join(tmpdir, "subset_one.png")
    path_both = os.path.join(tmpdir, "subset_both.png")
    one = render_preview(path=path_one, objects=["bowl"], view="front", resolution=SMALL)
    both = render_preview(path=path_both, objects=["bowl", "FarCube"], view="front",
                          resolution=SMALL)
    if not check("both renders succeeded",
                 one.get("status") == "success" and both.get("status") == "success",
                 "%s / %s" % (one.get("message"), both.get("message"))):
        return
    single = one["result"]["bounds_mm"]["size"]
    pair = both["result"]["bounds_mm"]["size"]
    check("one object frames one object's bounds", max(single) < 200.0, str(single))
    check("two objects frame both, so the far cube really was included",
          max(pair) > 3000.0, str(pair))
    check("the two renders are different pictures",
          open(path_one, "rb").read() != open(path_both, "rb").read())
    check("a name that does not exist is refused by name",
          "No object named" in str(
              render_preview(path=os.path.join(tmpdir, "nope.png"),
                             objects=["Gargoyle"]).get("message", "")))


def test_state_is_restored(tmpdir):
    section("the scene comes back exactly as it was")
    scene = bpy.context.scene

    # Deliberately unusual settings: restoring to the factory defaults would
    # pass a weaker test than this one.
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.filepath = "//artists_own_render_path"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 50
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "JPEG"
    scene.display.shading.show_cavity = False
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.background_type = "THEME"

    # An object the artist had already excluded from renders: it must still be
    # excluded afterwards, and must not have been "helpfully" turned back on.
    vertices, faces = box_mesh(20.0, center_mm=(0.0, 200.0, 0.0))
    load_mesh(name="AlreadyHidden", vertices=vertices, faces=faces, replace=True)
    hidden = bpy.data.objects.get("AlreadyHidden")
    if hidden is not None:
        hidden.hide_render = True

    before = scene_state()
    reply = render_preview(path=os.path.join(tmpdir, "state.png"), resolution=SMALL)
    if not check("the render ran", reply.get("status") == "success",
                 str(reply.get("message"))[:400]):
        return
    after = scene_state()

    differences = state_diff(before, after)
    check("nothing about the scene changed", not differences, str(differences))
    check("no preview camera was left behind",
          not any("Forge Preview" in name for name in after["camera_objects"])
          and not any("Forge Preview" in name for name in after["cameras"]),
          str(after["camera_objects"] + after["cameras"]))
    check("no preview light was left behind",
          not any("Forge Preview" in name for name in after["light_objects"])
          and not any("Forge Preview" in name for name in after["lights"]),
          str(after["light_objects"] + after["lights"]))
    check("the camera count is identical",
          len(before["camera_objects"]) == len(after["camera_objects"]),
          "%d -> %d" % (len(before["camera_objects"]), len(after["camera_objects"])))
    check("the render engine is the artist's again",
          after["render"].get("engine") == "BLENDER_EEVEE",
          str(after["render"].get("engine")))
    check("their output path survived",
          after["render"].get("filepath") == "//artists_own_render_path",
          str(after["render"].get("filepath")))
    check("their resolution survived (1920 x 1080 at 50%)",
          (after["render"].get("resolution_x"), after["render"].get("resolution_y"),
           after["render"].get("resolution_percentage")) == (1920, 1080, 50),
          str(after["render"]))
    check("their file format survived",
          after["image_settings"].get("file_format") == "JPEG",
          str(after["image_settings"].get("file_format")))
    check("their transparent film survived",
          after["render"].get("film_transparent") is True,
          str(after["render"].get("film_transparent")))
    check("the Workbench shading they had set survived",
          after["shading"].get("show_cavity") is False
          and after["shading"].get("color_type") == "MATERIAL"
          and after["shading"].get("background_type") == "THEME",
          str(after["shading"]))
    check("colour management survived",
          before["view_settings"] == after["view_settings"],
          "%s -> %s" % (before["view_settings"], after["view_settings"]))
    check("an object the artist had hidden from renders is still hidden",
          after["hide_render"].get("AlreadyHidden") is True,
          str(after["hide_render"].get("AlreadyHidden")))
    check("and the visible ones are still visible",
          after["hide_render"].get("bowl") is False,
          str(after["hide_render"].get("bowl")))

    if hidden is not None:
        bpy.data.objects.remove(hidden, do_unlink=True)


def test_failure_also_restores(tmpdir):
    section("a render that fails still puts everything back")
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.filepath = "//still_the_artists_path"
    before = scene_state()
    # A path under a file (not a folder) — the write cannot succeed.
    victim = os.path.join(tmpdir, "bowl.png")
    reply = render_preview(path=os.path.join(victim, "inside_a_file.png"),
                           resolution=SMALL)
    check("it failed, with a message and no traceback",
          reply.get("status") == "error" and "Traceback" not in str(reply.get("message")),
          str(reply.get("message"))[:300])
    after = scene_state()
    check("and the scene is untouched anyway", not state_diff(before, after),
          str(state_diff(before, after)))
    check("with no camera stranded in it",
          not any("Forge Preview" in name for name in after["camera_objects"]),
          str(after["camera_objects"]))


def test_material_shading(tmpdir):
    section("material shading")
    path = os.path.join(tmpdir, "material.png")
    reply = render_preview(path=path, objects=["bowl"], shading="material",
                           resolution=SMALL)
    if not check("it rendered (EEVEE, or Workbench with a note)",
                 reply.get("status") == "success", str(reply.get("message"))[:500]):
        return
    result = reply["result"]
    engine = result.get("engine")
    check("the engine is EEVEE or the documented Workbench fallback",
          engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"),
          str(engine))
    if engine == "BLENDER_WORKBENCH":
        check("and the fallback is said out loud, not hidden",
              any("Workbench" in str(n) for n in result.get("notes", [])),
              str(result.get("notes")))
    else:
        check("a scene with no lights got a temporary one",
              any("sun light" in str(n) for n in result.get("notes", [])),
              str(result.get("notes")))
    check("it still wrote a real PNG", png_size(path) == (SMALL, SMALL),
          str(png_size(path)))
    check("and no light was left in the scene",
          not any(o.name.startswith("Forge Preview") for o in bpy.data.objects),
          str([o.name for o in bpy.data.objects if o.name.startswith("Forge Preview")]))


def test_defaults_to_all_visible(tmpdir):
    section("no objects given = every visible mesh")
    hidden = bpy.data.objects.get("FarCube")
    was_hidden = None
    if hidden is not None:
        was_hidden = hidden.hide_viewport
        hidden.hide_viewport = True
    try:
        reply = render_preview(path=os.path.join(tmpdir, "all.png"), resolution=SMALL)
        if not check("it rendered", reply.get("status") == "success",
                     str(reply.get("message"))[:400]):
            return
        result = reply["result"]
        check("it says it framed everything visible",
              result.get("framed_all_visible") is True,
              str(result.get("framed_all_visible")))
        check("the hidden object was left out",
              "FarCube" not in (result.get("objects") or []),
              str(result.get("objects")))
        check("the visible part was included",
              "bowl" in (result.get("objects") or []), str(result.get("objects")))
    finally:
        if hidden is not None and was_hidden is not None:
            hidden.hide_viewport = was_hidden


def test_bad_input(tmpdir):
    section("bad input gets a sentence, not a traceback")
    cases = (
        ("no path at all", {}, "path"),
        ("an unknown view", {"path": os.path.join(tmpdir, "x.png"), "view": "corner"},
         "must be one of"),
        ("a resolution below the floor",
         {"path": os.path.join(tmpdir, "x.png"), "resolution": 4}, ">="),
        ("a resolution above the ceiling",
         {"path": os.path.join(tmpdir, "x.png"), "resolution": 9000}, "<="),
        ("an unknown shading",
         {"path": os.path.join(tmpdir, "x.png"), "shading": "cartoon"}, "must be one of"),
        ("objects that is not a list",
         {"path": os.path.join(tmpdir, "x.png"), "objects": 7}, "list of object names"),
        ("an object that does not exist",
         {"path": os.path.join(tmpdir, "x.png"), "objects": ["Nessie"]},
         "No object named"),
        ("a folder where the file should be", {"path": tmpdir}, "folder"),
    )
    for label, params, fragment in cases:
        reply = render_preview(**params)
        message = str(reply.get("message") or "")
        check("%s is refused" % label, reply.get("status") == "error", str(reply)[:200])
        check("  ... saying why (%r)" % fragment, fragment in message, message[:250])
        check("  ... without a traceback", "Traceback" not in message, message[:250])


def test_extension_is_forced(tmpdir):
    section("the output is always a .png")
    stem = os.path.join(tmpdir, "no_extension")
    reply = render_preview(path=stem, objects=["bowl"], resolution=SMALL)
    if not check("a path with no extension is accepted",
                 reply.get("status") == "success", str(reply.get("message"))[:300]):
        return
    written = reply["result"].get("path", "")
    check("and comes back as .png", written.lower().endswith(".png"), written)
    check("which is where the file actually is", os.path.exists(written), written)
    check("and it really is a PNG", png_size(written) is not None, str(png_size(written)))


def test_empty_scene_says_so(tmpdir):
    section("an empty scene is a sentence, not a black square")
    saved = [(o, o.hide_viewport) for o in bpy.data.objects if o.type == "MESH"]
    for obj, _ in saved:
        obj.hide_viewport = True
    try:
        reply = render_preview(path=os.path.join(tmpdir, "empty.png"))
        message = str(reply.get("message") or "")
        check("it refuses rather than rendering nothing",
              reply.get("status") == "error", str(reply)[:200])
        check("and says what to do about it",
              "nothing visible" in message.lower() and "objects" in message,
              message[:300])
    finally:
        for obj, was in saved:
            obj.hide_viewport = was


def find_view_3d():
    """The first VIEW_3D space, or None.

    ``--background`` still builds one off-screen window from the startup file,
    so the viewport-untouched claim below is a real measurement here rather than
    a source-code argument.
    """
    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", []) or []:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        return space
    return None


def test_interactive_guard(tmpdir):
    section("the interactive-session guard: the artist's viewport is untouched")
    import inspect

    from forge.tools import common

    source = inspect.getsource(common.cmd_render_preview)
    helpers = "".join(
        inspect.getsource(getattr(common, name))
        for name in ("_preview_targets", "_preview_bounds", "_preview_frame",
                     "_preview_configure_workbench", "_preview_engine",
                     "_preview_snapshot", "_preview_restore")
    )
    body = source + helpers

    check("it renders with bpy.ops.render.render, not render.opengl",
          "bpy.ops.render.render(" in body and "render.opengl" not in body)
    check("it never reads or writes a 3D view's own shading (space_data)",
          "space_data" not in body and "spaces.active" not in body
          and "SpaceView3D" not in body)
    check("it never walks the window manager's screens or areas",
          "screen.areas" not in body and "window_manager" not in body
          and "temp_override" not in body)
    check("it configures scene.display.shading — a scene property, not the "
          "artist's viewport", "scene.display.shading" in body
          or "scene.display" in body)
    check("and the whole body is under a try/finally that restores",
          "finally:" in source and "_preview_restore(restore)" in source)

    space = find_view_3d()
    if not check("there is a VIEW_3D to guard (background builds one too)",
                 space is not None):
        return

    # Set the viewport to something the render would love to stomp on: material
    # colours, no cavity, a THEME background, a perspective view.
    shading = space.shading
    shading.type = "SOLID"
    shading.color_type = "MATERIAL"
    shading.show_cavity = False
    shading.background_type = "THEME"
    region = space.region_3d
    if region is not None:
        region.view_perspective = "PERSP"

    before = {
        "type": shading.type,
        "color_type": shading.color_type,
        "light": shading.light,
        "studio_light": shading.studio_light,
        "show_cavity": shading.show_cavity,
        "background_type": shading.background_type,
        "single_color": tuple(round(v, 6) for v in shading.single_color),
        "use_local_camera": space.use_local_camera,
        "camera": space.camera.name if space.camera else None,
        "perspective": region.view_perspective if region is not None else None,
        "view_matrix": (tuple(round(v, 5) for row in region.view_matrix for v in row)
                        if region is not None else None),
    }

    reply = render_preview(path=os.path.join(tmpdir, "guard.png"),
                           objects=["bowl"], resolution=SMALL)
    check("a preview rendered while that viewport was open",
          reply.get("status") == "success", str(reply.get("message"))[:300])

    after = {
        "type": shading.type,
        "color_type": shading.color_type,
        "light": shading.light,
        "studio_light": shading.studio_light,
        "show_cavity": shading.show_cavity,
        "background_type": shading.background_type,
        "single_color": tuple(round(v, 6) for v in shading.single_color),
        "use_local_camera": space.use_local_camera,
        "camera": space.camera.name if space.camera else None,
        "perspective": region.view_perspective if region is not None else None,
        "view_matrix": (tuple(round(v, 5) for row in region.view_matrix for v in row)
                        if region is not None else None),
    }
    changed = sorted(key for key in before if before[key] != after[key])
    check("the viewport's own shading is exactly as it was", not changed, str(changed))
    check("  ... still MATERIAL colours, cavity still off",
          after["color_type"] == "MATERIAL" and after["show_cavity"] is False,
          "%s / %s" % (after["color_type"], after["show_cavity"]))
    check("  ... and the artist is still looking through their own view, not "
          "the preview camera",
          after["perspective"] == before["perspective"]
          and after["view_matrix"] == before["view_matrix"])
    note("scene.display.shading (what the render uses) and space.shading (what "
         "the artist looks through) are different properties; only the first is "
         "borrowed, and it is put back")


def test_port_is_free_after():
    section("the socket port is released")
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
    print("Forge headless tests - render_preview (the assistant's eyes)")
    enable_addon()
    tmpdir = tempfile.mkdtemp(prefix="forge_preview_")
    note("renders in %s" % tmpdir)

    # The startup file's Cube and Light would quietly answer two of the
    # questions below for us: the Cube by being framed alongside the part, the
    # Light by making "did it add one?" unanswerable. The Camera STAYS — it is
    # what proves scene.camera is handed back to the artist afterwards.
    for name in ("Cube", "Light"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    note("startup Cube and Light removed; the startup Camera stays on purpose")

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)

    try:
        test_registration()
        test_a_generated_part(tmpdir)
        test_framing_far_from_origin(tmpdir)
        test_every_view(tmpdir)
        test_objects_subset(tmpdir)
        test_defaults_to_all_visible(tmpdir)
        test_state_is_restored(tmpdir)
        test_failure_also_restores(tmpdir)
        test_material_shading(tmpdir)
        test_extension_is_forced(tmpdir)
        test_bad_input(tmpdir)
        test_empty_scene_says_so(tmpdir)
        test_interactive_guard(tmpdir)
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

    failed = [label for label, ok, _ in _RESULTS if not ok]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
