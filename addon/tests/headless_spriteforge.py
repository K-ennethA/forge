"""Headless add-on tests for ``sprite_cutout`` — a picture becomes a card.

ONE Blender launch covers the whole command, on purpose: every extra
``--background`` run is another flash on the artist's machine, so registration,
the synthetic sprite, the real fixture, determinism, the glTF round trip and
every refusal are tested together, in one process, in one file.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_spriteforge.py

The socket port is **9915** (9876 belongs to a live session, 9878-9914 to the
earlier suites). No geometry service, no Claude CLI, no network beyond loopback.

The two fixtures
----------------
**The cross** is built in this file, out of ``bpy`` image pixels, and is the
reason every number below is arithmetic rather than a recording: a 32 x 32 white
plate with a black cross on it, the vertical bar 4 px wide over rows 10-21 and
the horizontal bar 4 px tall over columns 10-21.  That is **80 subject pixels**
in a **12 x 12** bounding box with **exactly 12 corners**, so the traced outline,
the triangle count, the world size, the UVs and the cap's area are all things
this file can compute and compare, not things it can only observe.

**The harpy** is ``fixtures/harpy.png``, the pixel-art sprite the phase was asked
for: a 1254 x 1254 RGBA picture of a harpy on a flat white plate.  It arrived as
``5.webp``; Blender opened it and saved it as PNG, and the two decode to the same
pixels **byte for byte** (``max|webp − png| = 0``, measured before it was
committed), so the conversion is a container change and nothing else.  Its alpha
channel is 1.0 everywhere — an opaque RGBA sprite — which is exactly why the
command has a border-colour route at all.

What is actually being proved
-----------------------------
1. the command is registered on the socket, is **not** read-only (it builds
   objects, so Ctrl+Z has to reach it), and is a legal flow step;
2. **the extraction**, on a picture whose every pixel this file placed: the
   background colour, the island count, the 48 traced boundary segments, the 12
   corners they simplify to, and the fact that those 12 corners are the cross's
   own corners to the last digit;
3. **the card**: 24 vertices, 28 faces, 40 triangles, front cap facing −Y, back
   cap facing +Y, a rim of quads, 1000 x 30 x 1000 mm standing on z = 0, and a
   cap whose area equals the 80 source pixels **exactly**;
4. **the UVs** are the source image's own pixels — each vertex checked against
   ``u = x_px / 32``, ``v = 1 − y_px / 32``, computed here;
5. **the texture and the material**: a generated cutout whose RGB is the source's
   and whose alpha is the extraction mask, ``Closest`` interpolation, ``CLIP``
   extension, and the alpha cut as a ``Math: GREATER_THAN`` node — which is the
   only thing on Blender 5.0 that makes the glTF say ``alphaMode: MASK``;
6. **the awkward shapes**: an enclosed pocket of background stays card, two
   islands stay two islands (and ``islands="largest"`` keeps one), a speck is
   dropped by measured area, and two pixels touching at a corner trace as one
   loop **and are split back into two simple rings** — a pinched loop is not a
   polygon, and the triangulator does not say so, it just stops;
7. **determinism**: the same picture twice in one process is byte-identical, and
   the harpy's mesh digest is pinned to a constant so a change across processes
   fails loudly instead of flickering;
8. **the harpy**, every number pinned as measured;
9. **the glTF round trip**: the .glb's own JSON says MASK and NEAREST, its index
   count is three times the triangle count this command reported, and importing
   it back through ``import_generated`` returns the same triangles and the same
   millimetres;
10. **refusals are sentences**, not tracebacks;
11. **THE RENDER GATE** — the card is put in front of an orthographic camera and
    rendered in EEVEE, and the picture is asked the questions the counts cannot
    answer: does the drawn area match the silhouette's own area, does every
    point deep inside the sprite draw, is every point outside it clipped away,
    and is the colour at each sample one the texture actually has there.

    That last section exists because of a defect that got all the way past a
    green suite (2026-09-21): a pinched contour made Blender's ear clipper give
    up after 38.8% of the cap, and the card rendered as a floating outline while
    **every number in the report stayed correct** — island area, triangle count,
    UV bounds, glTF index count, even the outline's own enclosed area, because
    that was measured off the outline rather than off the face. A count of
    triangles cannot tell you whether the triangles cover anything. The tool now
    measures its own ``cap_coverage`` and refuses under 0.999, and this suite
    renders the thing and looks.
"""

import hashlib
import json
import math
import os
import socket as socketlib
import struct
import sys
import tempfile
import threading
import time
import traceback

import bpy
import mathutils
import numpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
HARPY = os.path.join(FIXTURES_DIR, "harpy.png")

PORT = 9915  # not 9876 (a live session) and not 9878..9914 (every other suite)

# --- the synthetic cross, spelled once so the assertions can be arithmetic ---
CROSS_SIZE = 32       # px square
CROSS_ARM = 6         # half the length of a bar, in px
CROSS_HALF = 2        # half the width of a bar, in px
CROSS_LO = CROSS_SIZE // 2 - CROSS_ARM      # 10
CROSS_HI = CROSS_SIZE // 2 + CROSS_ARM - 1  # 21
CROSS_PIXELS = (2 * CROSS_ARM) * (2 * CROSS_HALF) * 2 - (2 * CROSS_HALF) ** 2  # 80
CROSS_SPAN = CROSS_HI - CROSS_LO + 1        # 12

#: SHA-256 of the harpy card - vertex coordinates rounded to 9 decimals, polygon
#: vertex indices and every UV, via :func:`mesh_digest`.  A determinism gate, not
#: a tolerance: the extraction is integer pixel arithmetic and the triangulation
#: is Blender's own ear clipper on a fixed point list, so two runs that disagree
#: mean something in the chain stopped being deterministic.  Measured identical
#: over two fresh ``--background --factory-startup`` processes on Blender 5.0.1.
#:
#: Re-pinned 2026-09-21 when the traced loop began being split at its pinch
#: corners: the geometry legitimately moved (6 060 -> 6 134 ring points, and a
#: cap that covers 100% of the silhouette instead of 38.8%), so this digest
#: necessarily moved with it.
HARPY_DIGEST = "94c55652bf8f164c61b7d082b43adc601a442d9d25d52b8a6f5ceeb9cd0e1710"

#: Everything the harpy measures, in one place, because a pinned number with no
#: derivation beside it is a number nobody dares change.  Each one is what the
#: command reported on the committed fixture at ``height_m = 1.8``.
HARPY_HEIGHT_M = 1.8
HARPY_EXPECTED = {
    "image_size": [1254, 1254],
    # 254/255: the plate is one byte below white, and Blender hands pixels back
    # byte-exact rather than linearised.
    "background_colour": 0.996078,
    "border_uniformity": 0.946,
    "islands_found": 467,      # the sprite, plus 466 specks of codec ringing
    "islands_kept": 1,
    "largest_px": 671469,
    "largest_dropped_px": 78,  # 8 600x smaller than the sprite: the gap the
                               # 0.001 default sits in
    "enclosed_px": 2734,
    "points_traced": 19782,
    # The traced loop visits 105 corners twice (the sprite is joined
    # diagonally there), so it is split into 106 simple rings before anything
    # is triangulated — see spriteforge.split_pinches.
    "pinch_points": 105,
    "rings": 106,
    "points_simplified": 6134,
    "vertices": 12268,         # 2 x 6134: a front ring and a back ring
    "faces": 17176,
    "triangles": 23310,
    "slivers": 802,
    "cap_area_mm2": 1636486.258,
    "sprite_px": [1244, 1153],
    "dimensions_mm": [1942.064, 54.0, 1800.0],
    "mm_per_pixel": 1.561145,
}

_RESULTS = []
_WORKDIR = None


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


# --- socket harness ---------------------------------------------------------

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

    worker = threading.Thread(target=talk, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive() and time.monotonic() < deadline:
        if forge_server._server is not None:
            forge_server._server.drain()
        time.sleep(0.005)
    worker.join(timeout=2.0)
    if "error" in box:
        return {"status": "error", "message": "harness: %s" % box["error"]}
    return box.get("reply") or {"status": "error",
                                "message": "no reply within %.0fs" % timeout}


def call(command, **params):
    """A command over the socket. Returns the reply dict, success or not."""
    return _roundtrip({"type": command, "params": params})


def result(reply):
    if reply.get("status") != "success":
        raise AssertionError("%s" % reply.get("message"))
    return reply.get("result") or {}


def cut(**params):
    """``sprite_cutout``, over the socket, unwrapped."""
    return result(call("sprite_cutout", **params))


# --- helpers ----------------------------------------------------------------

def workdir():
    global _WORKDIR
    if _WORKDIR is None:
        _WORKDIR = tempfile.mkdtemp(prefix="forge-spriteforge-")
    return _WORKDIR


def write_png(path, rgba):
    """Save an ``(h, w, 4)`` float array as a PNG, top-down like a human reads.

    Only 0.0 and 1.0 are used by the fixtures below, which survive Blender's
    8-bit round trip exactly, so what comes back off disk is what went in.
    """
    height, width = rgba.shape[0], rgba.shape[1]
    image = bpy.data.images.new(os.path.basename(path), width=width,
                                height=height, alpha=True)
    image.pixels.foreach_set(rgba[::-1].astype("f4").reshape(-1))
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


def plate(size=CROSS_SIZE):
    """A white, fully opaque plate."""
    rgba = numpy.ones((size, size, 4), dtype="f4")
    return rgba


def cross_mask(size=CROSS_SIZE, arm=CROSS_ARM, half=CROSS_HALF):
    mask = numpy.zeros((size, size), dtype=bool)
    mid = size // 2
    mask[mid - arm:mid + arm, mid - half:mid + half] = True
    mask[mid - half:mid + half, mid - arm:mid + arm] = True
    return mask


def paint(rgba, mask, colour=(0.0, 0.0, 0.0)):
    for channel in range(3):
        rgba[:, :, channel] = numpy.where(mask, colour[channel],
                                          rgba[:, :, channel])
    return rgba


def cross_png():
    path = os.path.join(workdir(), "cross.png")
    if not os.path.isfile(path):
        write_png(path, paint(plate(), cross_mask()))
    return path


def cross_corners_px():
    """The 12 corners of the cross, in pixel CORNER coordinates, computed here.

    A pixel ``(row, col)`` occupies ``[col, col+1] x [row, row+1]``, so the
    vertical bar spans x = 14..18 and y = 10..22 and the horizontal bar spans
    x = 10..22 and y = 14..18.
    """
    mid = CROSS_SIZE // 2
    x0, x1 = mid - CROSS_HALF, mid + CROSS_HALF          # 14, 18
    y0, y1 = mid - CROSS_ARM, mid + CROSS_ARM            # 10, 22
    a0, a1 = mid - CROSS_ARM, mid + CROSS_ARM            # 10, 22 (x of the arms)
    b0, b1 = mid - CROSS_HALF, mid + CROSS_HALF          # 14, 18 (y of the arms)
    return {
        (x0, y0), (x1, y0), (x1, b0), (a1, b0), (a1, b1), (x1, b1),
        (x1, y1), (x0, y1), (x0, b1), (a0, b1), (a0, b0), (x0, b0),
    }


def mesh_digest(obj):
    """SHA-256 over coordinates, polygons and UVs — the determinism gate."""
    digest = hashlib.sha256()
    mesh = obj.data
    coords = numpy.empty(len(mesh.vertices) * 3, dtype="f8")
    mesh.vertices.foreach_get("co", coords)
    digest.update(numpy.round(coords, 9).tobytes())
    for polygon in mesh.polygons:
        digest.update(bytes(str(tuple(polygon.vertices)), "utf-8"))
    if mesh.uv_layers:
        uv = numpy.empty(len(mesh.loops) * 2, dtype="f8")
        mesh.uv_layers[0].data.foreach_get("uv", uv)
        digest.update(numpy.round(uv, 9).tobytes())
    return digest.hexdigest()


def vertex_array(obj):
    coords = numpy.empty(len(obj.data.vertices) * 3, dtype="f8")
    obj.data.vertices.foreach_get("co", coords)
    return coords.reshape(-1, 3)


def uv_array(obj):
    uv = numpy.empty(len(obj.data.loops) * 2, dtype="f8")
    obj.data.uv_layers[0].data.foreach_get("uv", uv)
    return uv.reshape(-1, 2)


def image_pixels(name):
    image = bpy.data.images[name]
    width, height = int(image.size[0]), int(image.size[1])
    flat = numpy.empty(width * height * int(image.channels), dtype="f4")
    image.pixels.foreach_get(flat)
    return flat.reshape(height, width, int(image.channels))[::-1].astype("f8")


def extraction_mask(path):
    """The tool's own silhouette for a picture, recomputed here.

    The render gate below needs to know where the sprite IS before it can ask
    whether the sprite RENDERS, and the honest source for that is the same
    extraction the card was built from: the claim under test is "the tool says
    671 469 pixels of sprite — does that much of it actually draw?".
    """
    from forge.tools import spriteforge, verify

    width, height, channels, pixels = verify._image_pixels(path)
    background, _report, _alpha = spriteforge._background_mask(
        pixels, channels, spriteforge.BACKGROUND_TOLERANCE)
    solid, labels, count, areas, _boxes, _enclosed = spriteforge.extract_islands(
        background, [])
    order = sorted(range(1, count + 1), key=lambda i: (-int(areas[i]), i))
    largest = int(areas[order[0]])
    floor = max(1, int(math.ceil(spriteforge.MIN_ISLAND_FRACTION * largest)))
    keep = [i for i in order if i == order[0] or int(areas[i]) >= floor]
    return numpy.isin(labels, numpy.array(keep, dtype="i4")), pixels


def render_engine():
    """Whichever name EEVEE has on this build."""
    scene = bpy.context.scene
    available = set(scene.bl_rna.properties["render"].fixed_type.properties
                    ["engine"].enum_items.keys())
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if name in available:
            return name
    return None  # pragma: no cover - every build ships EEVEE


def render_front(obj, resolution=360):
    """Render the card head-on in EEVEE and hand back its pixels and its ruler.

    Orthographic and framed on the card's own bounding box, so the mapping from
    a rendered pixel to a world position is exact arithmetic rather than a
    projection to be inverted: the frame is ``span`` metres across, centred on
    the card, and nothing perspective can drift.

    ``film_transparent`` is on, which makes the rendered ALPHA the answer to the
    only question that matters here — did the card draw anything at this point?
    The view transform is ``Standard``, because AgX would grade the colours this
    check compares against the texture.
    """
    scene = bpy.context.scene
    engine = render_engine()
    if engine is None:  # pragma: no cover
        return None, None
    scene.render.engine = engine

    bound = [tuple(corner) for corner in obj.bound_box]
    xs = [obj.matrix_world @ mathutils.Vector(c) for c in bound]
    min_x = min(v.x for v in xs)
    max_x = max(v.x for v in xs)
    min_z = min(v.z for v in xs)
    max_z = max(v.z for v in xs)
    centre = ((min_x + max_x) * 0.5, (min_z + max_z) * 0.5)
    span = max(max_x - min_x, max_z - min_z) * 1.1   # a 5% margin either side

    camera_data = bpy.data.cameras.new("SpriteGateCam")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = span
    camera = bpy.data.objects.new("SpriteGateCam", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (centre[0], -2.0, centre[1])
    camera.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    scene.camera = camera

    if scene.world is None:
        scene.world = bpy.data.worlds.new("SpriteGateWorld")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs[1].default_value = 1.0
    scene.render.film_transparent = True
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    try:
        scene.view_settings.view_transform = "Standard"
    except TypeError:  # pragma: no cover - older colour pipelines
        pass

    path = os.path.join(workdir(), "gate_front.png")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

    image = bpy.data.images.load(path, check_existing=False)
    flat = numpy.empty(resolution * resolution * 4, dtype="f4")
    image.pixels.foreach_get(flat)
    pixels = flat.reshape(resolution, resolution, 4)[::-1].astype("f8")
    bpy.data.images.remove(image)
    bpy.data.objects.remove(camera, do_unlink=True)

    def world_to_render(x, z):
        """(column, row) of the rendered pixel a world point lands in."""
        step = span / float(resolution)
        col = (x - centre[0]) / step + resolution * 0.5 - 0.5
        row = resolution * 0.5 - 0.5 - (z - centre[1]) / step
        return int(round(col)), int(round(row))

    return pixels, {"world_to_render": world_to_render, "span": span,
                    "step": span / float(resolution), "path": path}


def parse_gltf(path):
    """The JSON document of a .glb (chunk 0)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"glTF":
        return json.loads(data.decode("utf-8"))
    _magic, _version, length = struct.unpack("<III", data[:12])
    offset = 12
    while offset < min(length, len(data)):
        chunk_length, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        if chunk_type == 0x4E4F534A:  # 'JSON'
            return json.loads(
                data[offset + 8:offset + 8 + chunk_length].decode("utf-8"))
        offset += 8 + chunk_length
    raise AssertionError("%s has no JSON chunk" % path)


def wipe(prefix=None):
    """Remove the objects, meshes, materials and images a test made."""
    for obj in list(bpy.data.objects):
        if prefix is None or obj.name.startswith(prefix):
            bpy.data.objects.remove(obj, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


# ---------------------------------------------------------------------------
# 1. registration
# ---------------------------------------------------------------------------

def test_registration():
    section("registration")
    from forge.tools import flows, registry

    check("sprite_cutout is a registered command",
          registry.has_command("sprite_cutout"),
          str(registry.command_names())[:200])
    check("...and it is NOT read-only, because it builds an object Ctrl+Z has "
          "to reach", "sprite_cutout" not in registry.READ_ONLY_COMMANDS)
    reply = call("ping")
    check("the socket answers", reply.get("status") == "success",
          str(reply)[:200])
    step = flows._validate_step({"kind": "blender", "op": "sprite_cutout"}, 0)
    check("and it is a legal flow step, so a saved job can end with it",
          step is not None, str(step))


# ---------------------------------------------------------------------------
# 2 + 3 + 4. the synthetic cross: extraction, card, UVs
# ---------------------------------------------------------------------------

def test_the_picture_is_what_this_file_thinks_it_is():
    section("the synthetic cross — the fixture itself")
    path = cross_png()
    mask = cross_mask()
    check("the cross is %d subject pixels" % CROSS_PIXELS,
          int(mask.sum()) == CROSS_PIXELS, str(int(mask.sum())))
    rows, cols = numpy.nonzero(mask)
    check("in a %d x %d bounding box at (%d, %d)"
          % (CROSS_SPAN, CROSS_SPAN, CROSS_LO, CROSS_LO),
          (int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max()))
          == (CROSS_LO, CROSS_LO, CROSS_HI, CROSS_HI),
          str((int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max()))))
    check("the PNG on disk exists", os.path.isfile(path), path)
    note("fixture: %s" % path)
    return path


def test_extraction_on_the_cross():
    section("the cross — extraction")
    report = cut(image=cross_png(), name="Cross", height_m=1.0)
    background = report["background"]
    check("with no usable alpha the background is the border's own colour, and "
          "it says so as a heuristic",
          background["source"]["value"] == "border colour"
          and background["source"]["tier"] == "heuristic",
          str(background["source"]))
    check("the plate's colour was measured, not assumed: pure white",
          background["colour"] == [1.0, 1.0, 1.0], str(background["colour"]))
    check("the whole border ring is that one colour",
          background["border_uniformity"] == 1.0,
          str(background["border_uniformity"]))
    check("the threshold sits in a valley: halving and doubling it does not "
          "move the mask at all on a plate this clean",
          background["margin"]["swing"] == 0.0, str(background["margin"]))

    islands = report["islands"]
    check("one island found, one kept",
          (islands["found"], islands["kept"], islands["dropped"]) == (1, 1, 0),
          str(islands))
    check("and it is the %d pixels this file painted" % CROSS_PIXELS,
          islands["largest_px"] == CROSS_PIXELS, str(islands["largest_px"]))
    check("nothing is walled in, because a cross has no holes",
          islands["enclosed_background_px"] == 0,
          str(islands["enclosed_background_px"]))

    contour = report["contour"]
    # How many pixel sides the mask actually exposes — counted here, off the
    # same mask, rather than taken from the report it is checking.
    mask = cross_mask()
    padded = numpy.pad(mask, 1)
    edges = int((mask & ~padded[0:-2, 1:-1]).sum() + (mask & ~padded[2:, 1:-1]).sum()
                + (mask & ~padded[1:-1, 0:-2]).sum() + (mask & ~padded[1:-1, 2:]).sum())
    check("the trace found exactly as many boundary segments as the mask has "
          "exposed pixel sides (%d)" % edges,
          contour["points_traced"] == edges,
          "%s traced, %s sides" % (contour["points_traced"], edges))
    check("one closed loop", contour["loops"] == 1, str(contour["loops"]))
    check("and they simplify to the cross's 12 corners",
          contour["points_simplified"] == 12,
          str(contour["points_simplified"]))
    area = contour["area_mm2"]["value"]
    check("the outline encloses exactly the area of the pixels it was traced "
          "from — the simplification cost nothing here",
          area["ratio"] == 1.0, str(area))
    return report


def test_the_card_on_the_cross(report):
    section("the cross — the card")
    obj = bpy.data.objects["Cross"]
    geometry = report["geometry"]["value"]
    check("24 vertices: 12 corners on the front ring, 12 on the back",
          geometry["vertices"] == 24 and len(obj.data.vertices) == 24,
          str(geometry["vertices"]))
    check("28 faces: 16 cap triangles and 12 rim quads",
          (geometry["faces"], geometry["cap_triangles"], geometry["side_quads"])
          == (28, 16, 12), str(geometry))
    check("40 triangles once the quads are split",
          geometry["triangles"] == 16 + 2 * 12, str(geometry["triangles"]))
    check("the 4 zero-area slivers Blender's ear clipper emits on this outline "
          "were dropped rather than shipped",
          geometry["slivers_dropped"] == 4, str(geometry["slivers_dropped"]))
    check("the face covers its whole outline — 555555.556 mm2 of cross, all of "
          "it triangulated",
          geometry["cap_coverage"] == 1.0
          and geometry["cap_area_mm2"] == report["contour"]["area_mm2"]["value"]["outline"],
          "%s at coverage %s" % (geometry["cap_area_mm2"],
                                 geometry["cap_coverage"]))
    check("one traced loop, one simple ring, no pinches to split",
          (report["contour"]["loops"], report["contour"]["rings"],
           report["contour"]["pinch_points"]) == (1, 1, 0),
          str(report["contour"]))

    normals = numpy.empty(len(obj.data.polygons) * 3, dtype="f8")
    obj.data.polygons.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    centres = numpy.empty(len(obj.data.polygons) * 3, dtype="f8")
    obj.data.polygons.foreach_get("center", centres)
    centres = centres.reshape(-1, 3)
    front = normals[:, 1] < -0.999
    back = normals[:, 1] > 0.999
    rim = numpy.abs(normals[:, 1]) < 1e-6
    check("the front cap faces -Y, the way the sprite was drawn",
          report["geometry"]["value"]["front_normal"] == [0.0, -1.0, 0.0]
          and int(front.sum()) == 8, str(int(front.sum())))
    check("the back cap faces +Y", int(back.sum()) == 8, str(int(back.sum())))
    check("every rim face is horizontal — no cap leaked into the rim",
          int(rim.sum()) == 12, str(int(rim.sum())))
    check("no face points anywhere else",
          int(front.sum()) + int(back.sum()) + int(rim.sum())
          == len(obj.data.polygons))
    check("the front cap sits at -thickness/2 and the back at +thickness/2",
          abs(centres[front][:, 1].mean() + 0.015) < 1e-9
          and abs(centres[back][:, 1].mean() - 0.015) < 1e-9,
          "%s / %s" % (centres[front][:, 1].mean(), centres[back][:, 1].mean()))

    scale = report["scale"]
    check("a 1 m sprite is 1000 mm tall, 1000 mm wide (the cross is square) and "
          "30 mm thick (3%% of its height)",
          scale["dimensions_mm"] == [1000.0, 30.0, 1000.0],
          str(scale["dimensions_mm"]))
    check("it stands ON z = 0, not through it",
          scale["stands_on_mm"] == 0.0, str(scale["stands_on_mm"]))
    check("one source pixel is %g mm at this height" % (1000.0 / CROSS_SPAN),
          abs(scale["mm_per_pixel"] - 1000.0 / CROSS_SPAN) < 1e-4,
          str(scale["mm_per_pixel"]))
    check("the sprite's own bounding box is the ruler, not the 32 px frame",
          scale["sprite_px"] == [CROSS_SPAN, CROSS_SPAN], str(scale["sprite_px"]))

    # The 12 corners, computed here from the pixel grid, in world metres.
    centre_x = 0.5 * (CROSS_LO + CROSS_HI + 1)
    baseline = float(CROSS_HI + 1)
    step = 1.0 / CROSS_SPAN
    expected = sorted((round((x - centre_x) * step, 9), round((baseline - y) * step, 9))
                      for x, y in cross_corners_px())
    verts = vertex_array(obj)
    # A mesh stores coordinates as float32, so the comparison is to the micron
    # rather than to the last decimal: 1/12 m is 0.166666672 there and
    # 0.166666667 here, a 5 nm difference that is the storage, not the maths.
    built = sorted(set((round(v[0], 6), round(v[2], 6)) for v in verts))
    worst = (max(max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                 for a, b in zip(built, expected))
             if len(built) == len(expected) else None)
    check("and the card's corners ARE the cross's corners, to the micron "
          "(float32 is what a mesh stores)",
          len(built) == len(expected) and worst is not None and worst < 1e-6,
          "built %s\nexpected %s" % (built[:4], expected[:4]))
    check("every vertex sits on one of the two card faces, nothing between",
          set(numpy.round(verts[:, 1], 9).tolist()) == {-0.015, 0.015},
          str(sorted(set(numpy.round(verts[:, 1], 9).tolist()))))


def test_uvs_on_the_cross(report):
    section("the cross — UVs are the source image's own pixels")
    obj = bpy.data.objects["Cross"]
    uv = uv_array(obj)
    check("there is one UV layer, named the way Blender names them",
          report["uv"]["layer"] == "UVMap" and len(obj.data.uv_layers) == 1,
          str([layer.name for layer in obj.data.uv_layers]))
    lo, hi = float(CROSS_LO) / CROSS_SIZE, float(CROSS_HI + 1) / CROSS_SIZE
    check("the UVs span exactly the sprite's pixels in the frame: "
          "%g..%g in u and %g..%g in v" % (lo, hi, 1.0 - hi, 1.0 - lo),
          report["uv"]["bounds"] == [round(lo, 5), round(1.0 - hi, 5),
                                     round(hi, 5), round(1.0 - lo, 5)],
          str(report["uv"]["bounds"]))

    # Every loop, not a sample: the UV must be its own vertex's pixel position.
    verts = vertex_array(obj)
    loops = numpy.empty(len(obj.data.loops), dtype="i4")
    obj.data.loops.foreach_get("vertex_index", loops)
    centre_x = 0.5 * (CROSS_LO + CROSS_HI + 1)
    baseline = float(CROSS_HI + 1)
    step = 1.0 / CROSS_SPAN
    px = verts[loops][:, 0] / step + centre_x
    py = baseline - verts[loops][:, 2] / step
    want = numpy.stack([px / CROSS_SIZE, 1.0 - py / CROSS_SIZE], axis=1)
    worst = float(numpy.abs(uv - want).max())
    check("every one of the %d loops carries u = x_px/32, v = 1 - y_px/32 "
          "(worst error %.3g)" % (len(uv), worst), worst < 1e-6, str(worst))
    check("the back's UVs are the front's, which is what makes the back read "
          "mirrored rather than blank",
          report["uv"]["back"] == "mirror" and report["uv"]["back_faces"] == 0,
          str(report["uv"]))


def test_texture_and_material_on_the_cross(report):
    section("the cross — texture and material")
    texture = report["texture"]
    check("the picture has no alpha to clip, so a cutout texture was generated "
          "from the extraction mask",
          texture["source"]["value"] == "generated cutout"
          and texture["source"]["tier"] == "measured", str(texture["source"]))
    check("it is the same size as the source", texture["size"] == [32, 32],
          str(texture["size"]))
    check("and it is packed, so the card survives a .blend save",
          texture["packed"] is True, str(texture["packed"]))

    pixels = image_pixels(texture["image"])
    mask = cross_mask()
    check("its alpha is EXACTLY the extraction mask: 1 on the %d sprite pixels, "
          "0 on the other %d" % (CROSS_PIXELS, 32 * 32 - CROSS_PIXELS),
          numpy.array_equal(pixels[:, :, 3] > 0.5, mask),
          "%d opaque" % int((pixels[:, :, 3] > 0.5).sum()))
    source = paint(plate(), mask)
    check("and its colours are the source's, untouched",
          float(numpy.abs(pixels[:, :, :3] - source[:, :, :3]).max()) < 1e-6,
          str(float(numpy.abs(pixels[:, :, :3] - source[:, :, :3]).max())))

    material = report["material"]
    check("pixel art is sampled with Closest, never smoothed",
          material["interpolation"] == "Closest", str(material["interpolation"]))
    check("and clipped at the frame rather than wrapped",
          material["extension"] == "CLIP", str(material["extension"]))
    clip = material["alpha"]["node"]["value"]
    check("the alpha cut is a Math GREATER_THAN node at 0.5 — the node is what "
          "the glTF exporter reads on 5.0, the material setting is not",
          clip == {"type": "MATH", "operation": "GREATER_THAN", "cutoff": 0.5},
          str(clip))
    check("the material's own render method is set beside it, and reported as "
          "it READS BACK (5.0 turns CLIP into HASHED and says nothing)",
          material["alpha"].get("surface_render_method") == "DITHERED"
          and material["alpha"].get("blend_method") in ("CLIP", "HASHED"),
          str(material["alpha"]))
    check("a flat card that catches a specular highlight stops reading as a "
          "drawing, so roughness is 1 and specular 0",
          material["shading"]["Roughness"] == 1.0
          and material["shading"]["Specular IOR Level"] == 0.0,
          str(material["shading"]))

    obj = bpy.data.objects["Cross"]
    check("the card wears exactly one material, the one just described",
          [m.name for m in obj.data.materials] == [material["name"]],
          str([m.name for m in obj.data.materials]))
    tree = obj.data.materials[0].node_tree
    linked = {(link.from_node.type, link.to_socket.name) for link in tree.links}
    check("the image drives Base Color, and the clip node drives Alpha",
          ("TEX_IMAGE", "Base Color") in linked and ("MATH", "Alpha") in linked,
          str(sorted(linked)))


# ---------------------------------------------------------------------------
# 5. the awkward shapes
# ---------------------------------------------------------------------------

def test_an_enclosed_pocket_stays_card():
    section("a hole in the sprite is not a hole in the card")
    mask = cross_mask()
    holed = mask.copy()
    holed[15:17, 15:17] = False       # 4 px of plate, walled in by the cross
    path = write_png(os.path.join(workdir(), "holed.png"),
                     paint(plate(), holed))
    report = cut(image=path, name="Holed")
    check("the walled-in pocket is counted and kept as card",
          report["islands"]["enclosed_background_px"] == 4,
          str(report["islands"]["enclosed_background_px"]))
    check("so the outline is still ONE loop, not two",
          report["contour"]["loops"] == 1, str(report["contour"]["loops"]))
    check("and the card is the same 12-corner cross it was without the hole",
          report["contour"]["points_simplified"] == 12,
          str(report["contour"]["points_simplified"]))
    check("the texture still shows the pocket as the artist drew it — white, "
          "and opaque",
          float(image_pixels(report["texture"]["image"])[15, 15, 3]) == 1.0
          and float(image_pixels(report["texture"]["image"])[15, 15, 0]) == 1.0,
          str(image_pixels(report["texture"]["image"])[15, 15]))
    wipe("Holed")


def test_two_islands_and_the_largest_one():
    section("two islands")
    mask = numpy.zeros((CROSS_SIZE, CROSS_SIZE), dtype=bool)
    mask[4:12, 4:12] = True        # 64 px
    mask[20:24, 20:24] = True      # 16 px
    path = write_png(os.path.join(workdir(), "two.png"), paint(plate(), mask))

    report = cut(image=path, name="Two")
    check("both islands are found and both are kept",
          (report["islands"]["found"], report["islands"]["kept"]) == (2, 2),
          str(report["islands"]))
    check("as two loops and two squares' worth of corners",
          (report["contour"]["loops"], report["contour"]["points_simplified"])
          == (2, 8), str(report["contour"]))
    check("one object, two boxes: 16 vertices and 8 + 8 rim quads",
          report["geometry"]["value"]["vertices"] == 16
          and report["geometry"]["value"]["side_quads"] == 8,
          str(report["geometry"]["value"]))
    check("the card spans both islands — 20 px wide, not 8",
          report["scale"]["sprite_px"] == [20, 20],
          str(report["scale"]["sprite_px"]))

    report = cut(image=path, name="TwoLargest", islands="largest")
    check("islands='largest' keeps one, by measured area",
          (report["islands"]["found"], report["islands"]["kept"]) == (2, 1)
          and report["islands"]["largest_px"] == 64, str(report["islands"]))
    check("and the card is that island alone: 8 px, 4 corners",
          report["scale"]["sprite_px"] == [8, 8]
          and report["contour"]["points_simplified"] == 4,
          str(report["scale"]["sprite_px"]))
    wipe("Two")


def test_a_speck_is_dropped_by_measured_area():
    section("speckle")
    mask = numpy.zeros((CROSS_SIZE, CROSS_SIZE), dtype=bool)
    mask[4:12, 4:12] = True     # 64 px of sprite
    mask[28, 28] = True         # one pixel of dirt
    path = write_png(os.path.join(workdir(), "speck.png"), paint(plate(), mask))
    report = cut(image=path, name="Speck", min_island_fraction=0.1)
    check("both are found, one is dropped as speckle",
          (report["islands"]["found"], report["islands"]["kept"],
           report["islands"]["dropped"]) == (2, 1, 1), str(report["islands"]))
    check("the cut was 10%% of the largest island — 7 px — and the speck is 1",
          report["islands"]["min_island_px"] == 7
          and report["islands"]["largest_dropped_px"] == 1,
          str(report["islands"]))
    check("...and the report names the number it dropped, not just the count",
          any("1 island(s) under 7 px" in n for n in report["notes"]),
          str(report["notes"]))
    report = cut(image=path, name="SpeckKept", min_island_fraction=0.0)
    check("min_island_fraction=0 keeps every speck, as documented",
          report["islands"]["kept"] == 2, str(report["islands"]))
    wipe("Speck")


def test_a_diagonal_pinch_traces_as_one_loop():
    section("two pixels touching at a corner")
    mask = numpy.zeros((CROSS_SIZE, CROSS_SIZE), dtype=bool)
    mask[10:14, 10:14] = True
    mask[14:18, 14:18] = True   # touches the first only at the corner (14, 14)
    path = write_png(os.path.join(workdir(), "pinch.png"), paint(plate(), mask))
    report = cut(image=path, name="Pinch")
    check("8-connectivity makes the two blobs ONE island",
          report["islands"]["found"] == 1, str(report["islands"]))
    check("and the walk crosses the pinch, so it traces as one loop",
          report["contour"]["loops"] == 1, str(report["contour"]))
    check("but that loop is NOT a polygon: it visits the pinch corner twice, "
          "so it is split there into two simple rings",
          (report["contour"]["pinch_points"], report["contour"]["rings"])
          == (1, 2), str(report["contour"]))
    check("8 points in all — a square's four corners, twice",
          report["contour"]["points_simplified"] == 8,
          str(report["contour"]["points_simplified"]))
    check("the card covers both squares and nothing between them",
          report["contour"]["area_mm2"]["value"]["ratio"] == 1.0,
          str(report["contour"]["area_mm2"]["value"]))
    check("and the face really covers that outline — the check that a pinched "
          "ring silently fails",
          report["geometry"]["value"]["cap_coverage"] == 1.0,
          str(report["geometry"]["value"]["cap_coverage"]))
    wipe("Pinch")


def test_alpha_is_believed_when_it_says_something():
    section("a picture with a real alpha channel")
    mask = cross_mask()
    rgba = numpy.zeros((CROSS_SIZE, CROSS_SIZE, 4), dtype="f4")
    rgba[:, :, 0] = 1.0                    # red everywhere, including the plate
    rgba[:, :, 3] = mask.astype("f4")      # but only the cross is opaque
    path = write_png(os.path.join(workdir(), "alpha.png"), rgba)
    report = cut(image=path, name="Alpha")
    check("the alpha channel is the answer, and it is a MEASURED one",
          report["background"]["source"]["value"] == "alpha channel"
          and report["background"]["source"]["tier"] == "measured",
          str(report["background"]["source"]))
    check("cut at 0.5, the same number the material clips at",
          report["background"]["cutoff"] == 0.5,
          str(report["background"]["cutoff"]))
    check("the silhouette is the same cross the colour route found",
          (report["islands"]["largest_px"], report["contour"]["points_simplified"])
          == (CROSS_PIXELS, 12), str(report["contour"]))
    check("and no texture was generated: the file already had what the card "
          "needs",
          report["texture"]["source"]["value"] == "source file",
          str(report["texture"]["source"]))
    check("the red plate is NOT in the silhouette — alpha outranks colour",
          report["scale"]["sprite_px"] == [CROSS_SPAN, CROSS_SPAN],
          str(report["scale"]["sprite_px"]))
    wipe("Alpha")


def test_the_optional_extras():
    section("bevel, flat back, relief")
    path = cross_png()
    report = cut(image=path, name="Bevelled", bevel=0.25, bevel_segments=1)
    check("a bevel of a quarter of the thickness is 7.5 mm on a 30 mm card",
          report["scale"]["bevel_mm"] == 7.5, str(report["scale"]["bevel_mm"]))
    check("and it really added geometry",
          report["geometry"]["value"]["bevel_faces"] > 0
          and report["geometry"]["value"]["vertices"] > 24,
          str(report["geometry"]["value"]))
    check("the card is still 1000 x 30 x 1000 mm: a bevel eats into the card, "
          "it does not grow it",
          report["scale"]["dimensions_mm"][0] == 1000.0
          and abs(report["scale"]["dimensions_mm"][1] - 30.0) < 0.001,
          str(report["scale"]["dimensions_mm"]))
    uv = uv_array(bpy.data.objects["Bevelled"])
    check("and the bevel's new loops got UVs from the same projection, so "
          "nothing landed outside the picture",
          float(uv.min()) >= 0.0 and float(uv.max()) <= 1.0,
          "%s..%s" % (uv.min(), uv.max()))

    report = cut(image=path, name="FlatBack", back="flat_color")
    check("a flat back is a second material on the back cap's faces alone",
          report["uv"]["back_faces"] == 8
          and report["material"]["back_material"] == "FlatBack Back",
          str(report["uv"]["back_faces"]))
    check("its colour was measured from the sprite's own pixels (black cross)",
          report["uv"]["back_color"] == [0.0, 0.0, 0.0],
          str(report["uv"]["back_color"]))
    obj = bpy.data.objects["FlatBack"]
    indices = numpy.empty(len(obj.data.polygons), dtype="i4")
    obj.data.polygons.foreach_get("material_index", indices)
    check("and exactly those 8 polygons point at slot 1",
          int((indices == 1).sum()) == 8, str(int((indices == 1).sum())))

    report = cut(image=path, name="Relief", relief=0.25)
    check("relief builds a normal map and wires it in",
          report["material"]["relief"]["image"] == "Relief Relief",
          str(report["material"]["relief"]))
    relief = image_pixels("Relief Relief")
    # The datablock is 8-bit, so "0.5" is 128/255 = 0.501961 when it is read
    # back: the tolerance below is one quantisation step, not a fudge.
    step = 1.0 / 255.0
    check("it is a tangent-space map: flat where the picture is flat "
          "(0.5, 0.5, 1) and tilted where the luminance steps",
          abs(float(relief[0, 0, 0]) - 0.5) <= step
          and abs(float(relief[0, 0, 1]) - 0.5) <= step
          and abs(float(relief[0, 0, 2]) - 1.0) <= step
          and float(relief[:, :, 0].max()) > 0.6
          and float(relief[:, :, 0].min()) < 0.4,
          str([float(v) for v in relief[0, 0]]
              + [float(relief[:, :, 0].min()), float(relief[:, :, 0].max())]))
    check("and the report says out loud that it is fabricated, not depth",
          "FABRICATED" in report["honesty"], report["honesty"][-120:])
    wipe("Bevelled")
    wipe("FlatBack")
    wipe("Relief")


# ---------------------------------------------------------------------------
# 6. determinism
# ---------------------------------------------------------------------------

def test_two_runs_are_byte_identical():
    section("determinism — the same picture twice")
    path = cross_png()
    first = cut(image=path, name="DetA")
    second = cut(image=path, name="DetB")
    check("the two reports agree on every count",
          first["geometry"]["value"] == second["geometry"]["value"]
          and first["contour"] == second["contour"], str(first["contour"]))
    digest_a = mesh_digest(bpy.data.objects["DetA"])
    digest_b = mesh_digest(bpy.data.objects["DetB"])
    check("and the meshes are byte-identical: same coordinates, same polygons, "
          "same UVs", digest_a == digest_b, "%s / %s" % (digest_a, digest_b))
    note("cross digest: %s" % digest_a)
    wipe("Det")


def test_building_over_an_existing_card():
    section("building the same sprite again replaces it in place")
    path = cross_png()
    first = cut(image=path, name="Again")
    obj = bpy.data.objects["Again"]
    obj["artist_note"] = "keep me"
    pointer = obj.as_pointer()
    second = cut(image=path, name="Again", height_m=2.0)
    check("there is still exactly one object of that name",
          len([o for o in bpy.data.objects if o.name == "Again"]) == 1)
    check("it is the SAME object, not a replacement",
          bpy.data.objects["Again"].as_pointer() == pointer,
          "%s / %s" % (bpy.data.objects["Again"].as_pointer(), pointer))
    check("so a custom property the artist put on it survived",
          bpy.data.objects["Again"].get("artist_note") == "keep me")
    check("the report says it replaced the mesh rather than creating one",
          second["built"]["replaced"] is True and first["built"]["replaced"] is False,
          str(second["built"]))
    check("and the new height is the one asked for",
          second["scale"]["dimensions_mm"][2] == 2000.0,
          str(second["scale"]["dimensions_mm"]))
    wipe("Again")


# ---------------------------------------------------------------------------
# 7. the harpy
# ---------------------------------------------------------------------------

def test_the_harpy_fixture():
    section("the harpy fixture — every number as measured")
    if not check("the fixture is committed beside this suite", os.path.isfile(HARPY),
                 HARPY):
        return None
    started = time.monotonic()
    report = cut(image=HARPY, name="Harpy", height_m=HARPY_HEIGHT_M)
    note("sprite_cutout took %.2f s on a %d x %d picture"
         % (time.monotonic() - started, *report["image_size"]))
    want = HARPY_EXPECTED

    check("the picture is %s and RGBA" % want["image_size"],
          report["image_size"] == want["image_size"]
          and report["image_channels"] == 4, str(report["image_size"]))
    background = report["background"]
    check("its alpha says nothing (opaque everywhere), so the border colour is "
          "the route — and it is one byte below white, as measured",
          background["source"]["value"] == "border colour"
          and background["colour"] == [want["background_colour"]] * 3,
          str(background["colour"]))
    check("%.1f%% of the border ring is that one colour"
          % (want["border_uniformity"] * 100),
          background["border_uniformity"] == want["border_uniformity"],
          str(background["border_uniformity"]))
    check("and the threshold is the derived 0.04, with its margin measured "
          "rather than asserted",
          background["tolerance"] == 0.04
          and background["margin"]["swing"] < 0.02, str(background["margin"]))

    islands = report["islands"]
    check("%d islands found — the sprite and %d specks of codec ringing"
          % (want["islands_found"], want["islands_found"] - 1),
          islands["found"] == want["islands_found"], str(islands["found"]))
    check("one kept: %d px, against a largest speck of %d px"
          % (want["largest_px"], want["largest_dropped_px"]),
          (islands["kept"], islands["largest_px"], islands["largest_dropped_px"])
          == (want["islands_kept"], want["largest_px"], want["largest_dropped_px"]),
          str(islands))
    check("the default cut (a thousandth of the largest island) lands between "
          "them: %d px" % (want["largest_px"] // 1000 + 1),
          islands["min_island_px"] == int(math.ceil(want["largest_px"] * 0.001)),
          str(islands["min_island_px"]))
    check("%d pixels of plate are walled in by the sprite and were kept as card"
          % want["enclosed_px"],
          islands["enclosed_background_px"] == want["enclosed_px"],
          str(islands["enclosed_background_px"]))

    contour = report["contour"]
    check("the outline traces as %d boundary segments in one loop"
          % want["points_traced"],
          (contour["points_traced"], contour["loops"])
          == (want["points_traced"], 1), str(contour))
    check("and simplifies to %d points at half a source pixel (%.0f%% off)"
          % (want["points_simplified"],
             100.0 * (1 - want["points_simplified"] / want["points_traced"])),
          contour["points_simplified"] == want["points_simplified"],
          str(contour["points_simplified"]))
    check("half a pixel is 0.78 mm at this scale, and the simplified outline "
          "still encloses the pixels' own area",
          contour["area_mm2"]["value"]["ratio"] == 1.0
          and abs(contour["simplify_mm"] - 0.78) < 0.01, str(contour))

    geometry = report["geometry"]["value"]
    check("the card is %d vertices, %d faces, %d triangles"
          % (want["vertices"], want["faces"], want["triangles"]),
          (geometry["vertices"], geometry["faces"], geometry["triangles"])
          == (want["vertices"], want["faces"], want["triangles"]), str(geometry))
    check("with %d ear-clipper slivers dropped" % want["slivers"],
          geometry["slivers_dropped"] == want["slivers"],
          str(geometry["slivers_dropped"]))
    check("the outline touches itself at %d corners, so it was split into %d "
          "simple rings — a pinched ring cannot be triangulated"
          % (want["pinch_points"], want["rings"]),
          (contour["loops"], contour["pinch_points"], contour["rings"])
          == (1, want["pinch_points"], want["rings"]), str(contour))
    check("AND THE FACE COVERS ITS OUTLINE: %.1f mm2 of cap against %.1f mm2 "
          "of silhouette, coverage 1.0 — the invariant that catches a "
          "triangulator giving up part way"
          % (geometry["cap_area_mm2"],
             contour["area_mm2"]["value"]["outline"]),
          geometry["cap_coverage"] == 1.0
          and geometry["cap_area_mm2"] == want["cap_area_mm2"]
          and abs(geometry["cap_area_mm2"]
                  - contour["area_mm2"]["value"]["outline"]) < 1.0,
          "%s at coverage %s" % (geometry["cap_area_mm2"],
                                 geometry["cap_coverage"]))
    check("and its front cap faces -Y",
          geometry["front_normal"] == [0.0, -1.0, 0.0],
          str(geometry["front_normal"]))

    scale = report["scale"]
    check("the sprite measures %s px inside a %s px frame"
          % (want["sprite_px"], want["image_size"]),
          scale["sprite_px"] == want["sprite_px"], str(scale["sprite_px"]))
    check("at height_m=%.1f that is %s mm, pixel aspect preserved"
          % (HARPY_HEIGHT_M, want["dimensions_mm"]),
          scale["dimensions_mm"] == want["dimensions_mm"],
          str(scale["dimensions_mm"]))
    check("one source pixel is %.6f mm" % want["mm_per_pixel"],
          scale["mm_per_pixel"] == want["mm_per_pixel"],
          str(scale["mm_per_pixel"]))
    check("the card is 3%% of its height thick: 54 mm on a 1.8 m harpy",
          scale["thickness_mm"] == 54.0, str(scale["thickness_mm"]))

    check("its texture is a generated cutout the size of the source",
          report["texture"]["source"]["value"] == "generated cutout"
          and report["texture"]["size"] == want["image_size"],
          str(report["texture"]))
    check("sampled Closest and clipped at 0.5 by the node the exporter reads",
          report["material"]["interpolation"] == "Closest"
          and report["material"]["alpha"]["node"]["value"]["cutoff"] == 0.5,
          str(report["material"]["alpha"]["node"]["value"]))

    digest = mesh_digest(bpy.data.objects["Harpy"])
    note("harpy digest: %s" % digest)
    check("and the whole card hashes to the pinned digest — coordinates, "
          "polygons and UVs, byte for byte, in every process",
          digest == HARPY_DIGEST, "%s, expected %s" % (digest, HARPY_DIGEST))

    second = cut(image=HARPY, name="Harpy2", height_m=HARPY_HEIGHT_M)
    check("a second run in this process agrees on every count",
          second["geometry"]["value"] == geometry
          and second["contour"] == contour)
    check("...and hashes the same",
          mesh_digest(bpy.data.objects["Harpy2"]) == digest)
    bpy.data.objects.remove(bpy.data.objects["Harpy2"], do_unlink=True)
    print("\n--- the harpy report, in full ---")
    print(json.dumps(report, indent=1, sort_keys=True))
    print("--- end of report ---\n")
    return report


# ---------------------------------------------------------------------------
# 7b. THE RENDER GATE — does the card actually draw?
# ---------------------------------------------------------------------------

def sample_points(mask, margin=8, grid=13):
    """Points deep inside the sprite, and points deep outside it.

    Taken off a fixed grid over the mask's bounding box and kept only when the
    whole ``margin``-pixel neighbourhood agrees, so half a pixel of rounding in
    the render mapping cannot move a sample across the silhouette and make this
    gate flicker. Deterministic: same mask, same points, same order.
    """
    rows, cols = numpy.nonzero(mask)
    left, right = int(cols.min()), int(cols.max())
    top, bottom = int(rows.min()), int(rows.max())
    inside, outside = [], []
    for row_step in range(1, grid):
        for col_step in range(1, grid):
            x = left + (right - left) * col_step // grid
            y = top + (bottom - top) * row_step // grid
            window = mask[max(y - margin, 0):y + margin + 1,
                          max(x - margin, 0):x + margin + 1]
            if window.all():
                inside.append((x, y))
            elif not window.any():
                outside.append((x, y))
    return inside, outside


def test_the_card_actually_renders(report):
    """The gate the counting missed: put the card in front of a camera.

    Every number in the old report was right — island area, triangle count, UV
    bounds, glTF indices — while the card rendered as a floating outline,
    because a count of triangles cannot tell you whether those triangles COVER
    anything. This renders the card head-on and asks the picture.
    """
    section("the render gate — the card in front of a camera")
    if report is None:
        check("the harpy built, so it can be rendered", False, "no report")
        return
    obj = bpy.data.objects["Harpy"]
    mask, source = extraction_mask(HARPY)
    pixels, ruler = render_front(obj)
    if not check("EEVEE rendered the card headless", pixels is not None,
                 "no EEVEE engine on this build"):
        return
    note("%d x %d render, %.4f m across, %.4f m per pixel"
         % (pixels.shape[1], pixels.shape[0], ruler["span"], ruler["step"]))

    alpha = pixels[:, :, 3]
    drawn = float((alpha > 0.5).mean()) * ruler["span"] ** 2
    expected = report["contour"]["area_mm2"]["value"]["outline"] / 1e6
    check("the rendered card covers the area the report claims for it: "
          "%.4f m2 drawn against %.4f m2 claimed (%.1f%%)"
          % (drawn, expected, 100.0 * drawn / expected),
          abs(drawn - expected) / expected < 0.04,
          "%.4f vs %.4f" % (drawn, expected))

    scale = report["scale"]["mm_per_pixel"] / 1000.0
    left, top, right, bottom = report["scale"]["sprite_bbox_px"]
    centre_x = 0.5 * (left + right + 1)
    baseline = float(bottom + 1)

    def world_of(px, py):
        return ((px + 0.5 - centre_x) * scale, (baseline - (py + 0.5)) * scale)

    inside, outside = sample_points(mask)
    check("there are enough sample points deep inside and deep outside the "
          "sprite to ask about", len(inside) >= 8 and len(outside) >= 4,
          "%d inside, %d outside" % (len(inside), len(outside)))

    # One render pixel covers several source pixels, so the colour comparison
    # is against the texel WINDOW that render pixel spans rather than a single
    # texel: on pixel art two neighbouring texels can be a feather apart.
    footprint = max(1, int(round(ruler["step"] / scale)))
    opaque = []
    ratios = []
    wrong_colour = []
    for px, py in inside:
        col, row = ruler["world_to_render"](*world_of(px, py))
        sampled = pixels[row, col]
        opaque.append((px, py, float(sampled[3]),
                       tuple(round(float(v), 3) for v in sampled[:3])))
        window = source[max(py - footprint, 0):py + footprint + 1,
                        max(px - footprint, 0):px + footprint + 1, :3].mean(axis=2)
        low, high = float(window.min()), float(window.max())
        rendered = float(sampled[:3].mean())
        if not (low - 0.03 <= rendered <= high + 0.03):
            wrong_colour.append((px, py, round(rendered, 3),
                                 round(low, 3), round(high, 3)))
        middle = float(numpy.median(window))
        if middle > 0.05:
            ratios.append(rendered / middle)
    worst = min(entry[2] for entry in opaque)
    check("every one of the %d points deep inside the sprite DRAWS "
          "(worst alpha %.3f)" % (len(opaque), worst), worst > 0.9,
          str([e for e in opaque if e[2] <= 0.9][:6]))
    for entry in opaque[:4]:
        note("  px(%4d,%4d) alpha %.3f rgb %s" % entry)

    clear = []
    for px, py in outside:
        col, row = ruler["world_to_render"](*world_of(px, py))
        clear.append((px, py, float(pixels[row, col, 3])))
    loudest = max(entry[2] for entry in clear)
    check("and every one of the %d points outside it is CLIPPED AWAY "
          "(loudest alpha %.3f)" % (len(clear), loudest), loudest < 0.1,
          str([e for e in clear if e[2] >= 0.1][:6]))

    # The card is flat and lit by a uniform white world under the Standard view
    # transform, so what comes out of the renderer at a point on its face is the
    # colour the texture has there — the check is that every sample lands inside
    # the range of the texels its own render pixel covers. A UV that pointed
    # somewhere else in the sheet would fetch a colour from somewhere else.
    check("the face shows the TEXTURE at the right place: all %d samples land "
          "inside the colour range of the %d x %d texel window their render "
          "pixel spans" % (len(opaque), 2 * footprint + 1, 2 * footprint + 1),
          not wrong_colour, str(wrong_colour[:6]))
    median = sorted(ratios)[len(ratios) // 2]
    note("rendered over source luminance across the samples: median %.4f "
         "(1.0 means the renderer handed back the artist's own colours)"
         % median)
    check("...and that constant really is 1: a flat card under a white world "
          "renders its own albedo", abs(median - 1.0) < 0.1, str(median))
    note("render written to %s" % ruler["path"])


# ---------------------------------------------------------------------------
# 8. the glTF round trip
# ---------------------------------------------------------------------------

def test_the_glb_round_trip(report):
    section("glTF export and re-import — what Godot would actually get")
    if report is None:
        check("the harpy built, so it can be exported", False, "no report")
        return
    from forge.tools import common

    obj = bpy.data.objects["Harpy"]
    path = os.path.join(workdir(), "harpy.glb")
    with common.selection([obj], obj):
        status = bpy.ops.export_scene.gltf(**common.op_kwargs(
            bpy.ops.export_scene.gltf, {
                "filepath": path,
                "export_format": "GLB",
                "use_selection": True,
                "export_materials": "EXPORT",
                "export_image_format": "AUTO",
                "export_normals": True,
                "export_texcoords": True,
                "export_yup": True,
                "check_existing": False,
            }))
    if not check("the card exported", "FINISHED" in status and os.path.isfile(path),
                 str(status)):
        return
    note("%s, %d bytes" % (os.path.basename(path), os.path.getsize(path)))

    doc = parse_gltf(path)
    materials = doc.get("materials") or []
    check("the glTF material is alphaMode MASK — an alpha-scissored sprite in "
          "Godot, not a sorted transparent one",
          len(materials) == 1 and materials[0].get("alphaMode") == "MASK",
          json.dumps(materials)[:300])
    check("...at the glTF default cutoff of 0.5 (omitted means 0.5)",
          materials[0].get("alphaCutoff", 0.5) == 0.5,
          str(materials[0].get("alphaCutoff")))
    samplers = doc.get("samplers") or []
    check("and the sampler is NEAREST (9728), so the pixels stay pixels on the "
          "other side of the file",
          len(samplers) == 1 and samplers[0].get("magFilter") == 9728,
          str(samplers))
    check("with CLAMP_TO_EDGE (33071) on both axes, so no sprite wraps onto "
          "itself", samplers[0].get("wrapS") == 33071
          and samplers[0].get("wrapT") == 33071, str(samplers))
    images = doc.get("images") or []
    check("the generated cutout travelled with it, embedded as PNG",
          len(images) == 1 and images[0].get("mimeType") == "image/png",
          str([{k: v for k, v in i.items() if k != "uri"} for i in images]))

    primitive = doc["meshes"][0]["primitives"][0]
    indices = doc["accessors"][primitive["indices"]]["count"]
    check("the file carries three indices per triangle this command reported "
          "(%d)" % report["geometry"]["value"]["triangles"],
          indices == 3 * report["geometry"]["value"]["triangles"],
          "%d indices" % indices)

    reply = call("import_generated", path=path, name="HarpyBack", repair=False)
    if not check("and it imports back through the add-on's own glTF door",
                 reply.get("status") == "success", str(reply.get("message"))[:300]):
        return
    back = result(reply)
    check("un-repaired, so the numbers below are the FILE's, not a rebuild's",
          back.get("repaired") is False, str(back.get("repaired")))
    check("the same triangle count comes back: %d"
          % report["geometry"]["value"]["triangles"],
          back.get("face_count") == report["geometry"]["value"]["triangles"],
          str(back.get("face_count")))
    check("and the same millimetres: %s" % report["scale"]["dimensions_mm"],
          [round(v, 3) for v in back.get("dimensions_mm") or []]
          == report["scale"]["dimensions_mm"],
          str(back.get("dimensions_mm")))
    imported = bpy.data.objects["HarpyBack"]
    check("with a material and a UV layer on it",
          len(imported.data.materials) == 1 and len(imported.data.uv_layers) == 1,
          str([m.name for m in imported.data.materials]))
    check("and the vertex count grew only the way glTF always grows it — the "
          "exporter splits a vertex per unique normal/UV, it never loses one",
          back.get("vertex_count") >= report["geometry"]["value"]["vertices"],
          "%s vs %s" % (back.get("vertex_count"),
                        report["geometry"]["value"]["vertices"]))
    wipe("HarpyBack")


# ---------------------------------------------------------------------------
# 9. refusals
# ---------------------------------------------------------------------------

def test_refusals():
    section("refusals are sentences")
    cases = [
        ("no image at all", {}, "image"),
        ("a path to nothing",
         {"image": os.path.join(workdir(), "nope.png")}, "No image at"),
        ("a folder instead of a picture", {"image": workdir()}, "No image at"),
        ("a negative height", {"image": cross_png(), "height_m": -1.0}, ">="),
        ("a thickness of zero",
         {"image": cross_png(), "thickness_fraction": 0.0}, ">="),
        ("a bevel past half the thickness",
         {"image": cross_png(), "bevel": 0.9}, "<="),
        ("an islands mode nobody has",
         {"image": cross_png(), "islands": "biggest"}, "'islands' must be"),
        ("a back mode nobody has",
         {"image": cross_png(), "back": "painted"}, "'back' must be"),
        ("a back colour that is not a colour",
         {"image": cross_png(), "back": "flat_color", "back_color": [1, 2]},
         "back_color"),
        ("a collection that is not a name",
         {"image": cross_png(), "collection": 7}, "collection"),
    ]
    for label, params, fragment in cases:
        reply = call("sprite_cutout", **params)
        message = str(reply.get("message") or "")
        check("%s is refused with a sentence naming the problem" % label,
              reply.get("status") == "error" and fragment in message,
              message[:200] or "(succeeded)")
        check("...and the refusal is a message, not a traceback",
              "Traceback" not in message, message[:160])

    blank = write_png(os.path.join(workdir(), "blank.png"), plate())
    reply = call("sprite_cutout", image=blank, name="Blank")
    message = str(reply.get("message") or "")
    check("an empty plate is refused, with the fix in the same sentence",
          reply.get("status") == "error" and "no sprite to cut out" in message
          and "threshold" in message, message[:240])
    check("and nothing was built for it", "Blank" not in bpy.data.objects)


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
    print("Forge headless tests — sprite_cutout (spriteforge)")
    enable_addon()

    for name in ("Cube", "Light", "Camera"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)

    from forge import server as forge_server

    forge_server.start_server(host="127.0.0.1", port=PORT)
    note("socket on 127.0.0.1:%d" % PORT)

    try:
        test_registration()
        test_the_picture_is_what_this_file_thinks_it_is()
        report = test_extraction_on_the_cross()
        test_the_card_on_the_cross(report)
        test_uvs_on_the_cross(report)
        test_texture_and_material_on_the_cross(report)
        wipe("Cross")
        test_an_enclosed_pocket_stays_card()
        test_two_islands_and_the_largest_one()
        test_a_speck_is_dropped_by_measured_area()
        test_a_diagonal_pinch_traces_as_one_loop()
        test_alpha_is_believed_when_it_says_something()
        test_the_optional_extras()
        test_two_runs_are_byte_identical()
        test_building_over_an_existing_card()
        harpy = test_the_harpy_fixture()
        test_the_card_actually_renders(harpy)
        test_the_glb_round_trip(harpy)
        test_refusals()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("harness ran to completion", False, "unhandled exception")
    finally:
        try:
            test_port_is_free_after()
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    failed = [label for label, ok_, _ in _RESULTS if not ok_]
    print("\n%d checks, %d failed" % (len(_RESULTS), len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    print("RESULT: %s" % ("OK" if not failed else "FAILURES"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
