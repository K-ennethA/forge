"""Headless add-on tests for the Phase 17 mechanism demos.

ONE Blender launch covers all three commands, on purpose: every extra
``--background`` run is another flash on the artist's machine, so
``animate_object``, ``set_material_emission`` and ``render_animation`` are
tested together, in one process, in one file.

Run it inside Blender, never windowed::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python addon\\tests\\headless_mechanism.py

The socket port is **9903** (9876 belongs to a live session, 9879-9902 to the
earlier suites).  No service, no Claude CLI, no network beyond loopback.

What is actually being proved:

1. the three commands are registered, and exactly one of them
   (``render_animation``) is read-only — a keyframe and a material are the
   artist's work and must stay on the undo stack;
2. ``animate_object`` round-trips: the F-curves exist, they hold the values
   that were asked for at the frames that were asked for, the interpolation
   landed on the points this call made, ``clear`` really empties, a quaternion
   object is moved to XYZ euler and says so, and a near-miss object name comes
   back with a suggestion rather than a traceback;
3. ``set_material_emission`` creates a working emission material where there
   was none, sets strength and colour on the node itself, keyframes both when a
   frame is given, and holds the value CONSTANT between keys — an LED is off
   and then on, it does not fade;
4. ``render_animation`` writes a REAL .mp4: ISO base-media magic bytes, a
   ``mvhd`` duration that matches the frames and fps asked for, and the exact
   path that was requested (no ``0001-0048`` suffix beside it);
5. it puts every borrowed setting back — engine, output path, resolution, file
   format, ffmpeg container/codec, fps, frame range, frame position, colour
   management, Workbench shading, ``scene.camera``, every object's
   ``hide_render`` — and leaves no camera and no light behind;
6. the end-to-end demo: a flame pressed 1.8 mm over 12 frames, an LED keyed on
   at the latch frame, and a film of the two happening together;
7. the budget: a 48-frame 640 px render finishes in tens of seconds, asserted
   loosely against an upper bound;
8. bad input (no path, a backwards frame range, a clip nobody would wait for,
   an unknown engine, an object with nowhere to put a material, a key that sets
   nothing, both location units at once) fails with a sentence.
"""

import json
import os
import socket as socketlib
import sys
import tempfile
import threading
import time
import traceback

import bpy

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.normpath(os.path.join(TESTS_DIR, os.pardir))
REPO_ROOT = os.path.normpath(os.path.join(ADDON_DIR, os.pardir))

PORT = 9903

#: The budget from the contract: a 48-frame 640 px EEVEE render is tens of
#: seconds.  The bound is deliberately loose — this asserts "not a coffee
#: break", not a benchmark, and a cold GPU driver is allowed a slow first frame.
BUDGET_FRAMES = 48
BUDGET_RESOLUTION = 640
BUDGET_SECONDS = 180.0

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


def animate_object(**params):
    return _roundtrip({"type": "animate_object", "params": params})


def set_material_emission(**params):
    return _roundtrip({"type": "set_material_emission", "params": params})


def render_animation(**params):
    return _roundtrip({"type": "render_animation", "params": params})


def load_mesh(**params):
    return _roundtrip({"type": "load_mesh", "params": params})


def ok(reply):
    return reply.get("status") == "success"


def result(reply):
    return reply.get("result") or {}


def message(reply):
    return reply.get("message") or ""


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def box_mesh(size_mm, center_mm=(0.0, 0.0, 0.0)):
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


def make_box(name, size_mm=20.0, center_mm=(0.0, 0.0, 0.0)):
    vertices, faces = box_mesh(size_mm, center_mm)
    reply = load_mesh(name=name, vertices=vertices, faces=faces, replace=True)
    if not ok(reply):
        raise RuntimeError("fixture %r failed: %s" % (name, message(reply)))
    return bpy.data.objects[name]


# ---------------------------------------------------------------------------
# reading an .mp4 back — the ISO base-media boxes, without a library
# ---------------------------------------------------------------------------

def _boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = pos + 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            body = pos + 16
        elif size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            return
        yield kind, body, pos + size
        pos += size


def mp4_info(path):
    """``{"brand", "duration_s", "timescale"}`` or ``None`` if it is not an mp4."""
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 12 or data[4:8] != b"ftyp":
        return None
    info = {"brand": data[8:12].decode("latin-1", "replace"), "duration_s": None,
            "timescale": None, "bytes": len(data)}
    for kind, body, end in _boxes(data, 0, len(data)):
        if kind != b"moov":
            continue
        for inner, ibody, iend in _boxes(data, body, end):
            if inner != b"mvhd":
                continue
            version = data[ibody]
            cursor = ibody + 4
            if version == 1:
                cursor += 16
                timescale = int.from_bytes(data[cursor:cursor + 4], "big")
                duration = int.from_bytes(data[cursor + 4:cursor + 12], "big")
            else:
                cursor += 8
                timescale = int.from_bytes(data[cursor:cursor + 4], "big")
                duration = int.from_bytes(data[cursor + 4:cursor + 8], "big")
            if timescale:
                info["timescale"] = timescale
                info["duration_s"] = duration / float(timescale)
    return info


# ---------------------------------------------------------------------------
# scene state — the whole point of the try/finally in the command
# ---------------------------------------------------------------------------

_SHADING_FIELDS = (
    "light", "color_type", "single_color", "background_type", "background_color",
    "show_shadows", "show_specular_highlight", "show_cavity", "cavity_type",
    "show_object_outline", "show_xray",
)

_FFMPEG_FIELDS = ("format", "codec", "constant_rate_factor", "ffmpeg_preset",
                  "gopsize", "audio_codec")


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
            if not isinstance(value, (str, bytes, int, float, bool)):
                try:
                    value = tuple(round(float(v), 6) for v in value)
                except TypeError:
                    value = repr(value)
            out[name] = value
        return out

    state = {
        "scene": frozen(scene, ("frame_start", "frame_end", "frame_current",
                                "frame_step")),
        "render": frozen(render, ("engine", "filepath", "resolution_x",
                                  "resolution_y", "resolution_percentage",
                                  "film_transparent", "use_overwrite",
                                  "use_file_extension", "use_stamp", "use_border",
                                  "fps", "fps_base")),
        "image": frozen(render.image_settings, ("media_type", "file_format",
                                                "color_mode", "color_depth")),
        "shading": frozen(scene.display.shading, _SHADING_FIELDS),
        "display": frozen(scene.display, ("render_aa",)),
        "camera": scene.camera.name if scene.camera else None,
        "objects": sorted(o.name for o in bpy.data.objects),
        "cameras": sorted(c.name for c in bpy.data.cameras),
        "lights": sorted(lamp.name for lamp in bpy.data.lights),
        "hide_render": {o.name: bool(o.hide_render) for o in bpy.data.objects},
    }
    ffmpeg = getattr(render, "ffmpeg", None)
    if ffmpeg is not None:
        state["ffmpeg"] = frozen(ffmpeg, _FFMPEG_FIELDS)
    if view_settings is not None:
        state["view"] = frozen(view_settings, ("view_transform", "look",
                                               "exposure", "gamma"))
    return state


def diff_state(before, after):
    out = []
    for group in sorted(set(before) | set(after)):
        first, second = before.get(group), after.get(group)
        if isinstance(first, dict) and isinstance(second, dict):
            for key in sorted(set(first) | set(second)):
                if first.get(key) != second.get(key):
                    out.append("%s.%s %r -> %r" % (group, key, first.get(key),
                                                   second.get(key)))
        elif first != second:
            out.append("%s %r -> %r" % (group, first, second))
    return out


# ---------------------------------------------------------------------------
# fcurve helpers — Blender 5.0 keeps them in slotted channelbags
# ---------------------------------------------------------------------------

def curves_of(action):
    from forge.tools import mechanism

    return mechanism.action_fcurves(action)


def curve_for(action, data_path, index):
    for curve in curves_of(action):
        if curve.data_path == data_path and curve.array_index == index:
            return curve
    return None


# ---------------------------------------------------------------------------
# 1. registration
# ---------------------------------------------------------------------------

def test_registration():
    section("registration and undo classification")
    from forge.tools import registry

    names = registry.command_names()
    for name in ("animate_object", "set_material_emission", "render_animation"):
        check("%s is a protocol command" % name, name in names)

    check("render_animation is read-only (no undo checkpoint)",
          "render_animation" in registry.READ_ONLY_COMMANDS)
    check("animate_object is NOT read-only — keys are the artist's work",
          "animate_object" not in registry.READ_ONLY_COMMANDS)
    check("set_material_emission is NOT read-only — a material is undoable work",
          "set_material_emission" not in registry.READ_ONLY_COMMANDS)


# ---------------------------------------------------------------------------
# 2. animate_object
# ---------------------------------------------------------------------------

def test_animate_round_trip():
    section("animate_object — the keys land where they were asked for")
    obj = make_box("Flame", 20.0)

    reply = animate_object(
        object="Flame",
        keys=[
            {"frame": 1, "location_mm": [0.0, 0.0, 0.0]},
            {"frame": 8, "location_mm": [0.0, 0.0, -1.8]},
            {"frame": 12, "location_mm": [0.0, 0.0, -1.5]},
        ],
        interpolation="LINEAR",
    )
    if not check("animate_object succeeded", ok(reply), message(reply)):
        return
    data = result(reply)
    check("keys_set counts every channel written", data.get("keys_set") == 3,
          data.get("keys_set"))
    check("frame_range is the span asked for", data.get("frame_range") == [1, 12],
          data.get("frame_range"))
    check("frames counts inclusively", data.get("frames") == 12, data.get("frames"))
    check("the channel is reported", data.get("channels") == ["location"],
          data.get("channels"))
    check("interpolation echoed", data.get("interpolation") == "LINEAR")
    check("an action was created", data.get("created_action") is True)
    check("the units are spelled out in the result",
          "millimetres" in (data.get("units") or ""))

    action = obj.animation_data.action if obj.animation_data else None
    if not check("the object has an action holding the keys", action is not None):
        return
    curve = curve_for(action, "location", 2)
    if not check("there is a location Z F-curve", curve is not None):
        return
    check("three keyframe points on Z", len(curve.keyframe_points) == 3,
          len(curve.keyframe_points))
    check("frame 1 is at rest", abs(curve.evaluate(1) - 0.0) < 1e-6, curve.evaluate(1))
    check("frame 8 is 1.8 mm down (0.0018 m)",
          abs(curve.evaluate(8) - (-0.0018)) < 1e-7, curve.evaluate(8))
    check("frame 12 is the latched 1.5 mm",
          abs(curve.evaluate(12) - (-0.0015)) < 1e-7, curve.evaluate(12))
    check("mm went in as mm and came out as metres",
          abs(curve.evaluate(8) * 1000.0 + 1.8) < 1e-4)
    check("every made point took the interpolation",
          all(point.interpolation == "LINEAR" for point in curve.keyframe_points))
    check("interpolated_points counts what was set",
          data.get("interpolated_points", 0) >= 3, data.get("interpolated_points"))
    note("action %r, %d F-curves" % (action.name, data.get("fcurves")))


def test_metres_and_degrees():
    section("animate_object — metres, degrees, scale, and the unit guard")
    make_box("Lever", 16.0, center_mm=(40.0, 0.0, 0.0))

    reply = animate_object(
        object="Lever",
        keys=[
            {"frame": 1, "location": [0.0, 0.0, 0.0], "rotation_euler_deg": [0, 0, 0],
             "scale": 1.0},
            {"frame": 10, "location": [0.0, 0.0, 0.25],
             "rotation_euler_deg": [0.0, 0.0, 90.0], "scale": [1.0, 1.0, 2.0]},
        ],
    )
    if not check("metres/degrees/scale in one call", ok(reply), message(reply)):
        return
    data = result(reply)
    check("all three channels reported",
          data.get("channels") == ["location", "rotation_euler", "scale"],
          data.get("channels"))
    check("keys_set counts channels, not entries", data.get("keys_set") == 6,
          data.get("keys_set"))

    obj = bpy.data.objects["Lever"]
    action = obj.animation_data.action
    loc = curve_for(action, "location", 2)
    rot = curve_for(action, "rotation_euler", 2)
    scale = curve_for(action, "scale", 2)
    check("location metres go straight in", loc is not None and abs(loc.evaluate(10) - 0.25) < 1e-6)
    check("90 degrees became pi/2 radians",
          rot is not None and abs(rot.evaluate(10) - 1.5707963) < 1e-4,
          rot.evaluate(10) if rot else None)
    check("a scalar scale means all three axes",
          scale is not None and abs(scale.evaluate(1) - 1.0) < 1e-6)
    check("scale Z reaches 2", scale is not None and abs(scale.evaluate(10) - 2.0) < 1e-6)
    check("BEZIER is the default interpolation", data.get("interpolation") == "BEZIER")


def test_quaternion_object_is_moved_to_euler():
    section("animate_object — a quaternion object is switched to XYZ and says so")
    obj = make_box("Spinner", 12.0, center_mm=(-40.0, 0.0, 0.0))
    obj.rotation_mode = "QUATERNION"

    reply = animate_object(object="Spinner",
                           keys=[{"frame": 1, "rotation_euler_deg": [0, 0, 0]},
                                 {"frame": 6, "rotation_euler_deg": [0, 0, 45]}])
    if not check("keying a quaternion object works", ok(reply), message(reply)):
        return
    data = result(reply)
    check("the object is now XYZ euler", obj.rotation_mode == "XYZ", obj.rotation_mode)
    check("the change is reported", data.get("rotation_mode_changed") == "QUATERNION",
          data.get("rotation_mode_changed"))
    check("and warned about in words",
          any("XYZ euler" in w for w in data.get("warnings") or []),
          data.get("warnings"))


def test_clear_is_idempotent():
    section("animate_object — clear makes a demo re-runnable")
    first = result(animate_object(
        object="Flame",
        keys=[{"frame": 1, "location_mm": [0, 0, 0]},
              {"frame": 5, "location_mm": [0, 0, -2.0]}],
        clear=True))
    check("clear reported what it removed", first.get("cleared_fcurves", 0) > 0,
          first.get("cleared_fcurves"))
    obj = bpy.data.objects["Flame"]
    curve = curve_for(obj.animation_data.action, "location", 2)
    check("only the new keys survive", curve is not None and len(curve.keyframe_points) == 2,
          len(curve.keyframe_points) if curve else None)
    check("and they are the new values",
          curve is not None and abs(curve.evaluate(5) + 0.002) < 1e-7,
          curve.evaluate(5) if curve else None)
    check("the second run did not create a second action",
          first.get("created_action") is False)


def test_animate_bad_input():
    section("animate_object — bad input is a sentence")
    reply = animate_object(object="Flam", keys=[{"frame": 1, "location_mm": [0, 0, 0]}])
    check("a near-miss name is refused", not ok(reply))
    check("...with a suggestion", "Did you mean" in message(reply), message(reply))
    check("...naming the real object", "'Flame'" in message(reply), message(reply))

    for label, params, needle in (
        ("no keys at all", {"object": "Flame"}, "'keys' must be"),
        ("empty keys", {"object": "Flame", "keys": []}, "'keys' must be"),
        ("a key with no frame", {"object": "Flame", "keys": [{"location_mm": [0, 0, 0]}]},
         "missing 'frame'"),
        ("a key that sets nothing", {"object": "Flame", "keys": [{"frame": 2}]},
         "sets nothing"),
        ("both location units", {"object": "Flame",
                                 "keys": [{"frame": 2, "location": [0, 0, 0],
                                           "location_mm": [0, 0, 1]}]},
         "same channel"),
        ("a two-component vector", {"object": "Flame",
                                    "keys": [{"frame": 2, "location_mm": [0, 0]}]},
         "three numbers"),
        ("an unknown interpolation", {"object": "Flame",
                                      "keys": [{"frame": 2, "location_mm": [0, 0, 0]}],
                                      "interpolation": "SPRINGY"},
         "must be one of"),
    ):
        reply = animate_object(**params)
        check("refused: %s" % label, not ok(reply))
        check("...saying why (%s)" % label, needle in message(reply), message(reply))

    obj = bpy.data.objects["Flame"]
    before = len(curves_of(obj.animation_data.action))
    animate_object(object="Flame",
                   keys=[{"frame": 20, "location_mm": [0, 0, -1]},
                         {"frame": 21, "location_mm": [0, 0]}])
    check("a typo in the LAST key leaves the first one unapplied",
          len(curves_of(obj.animation_data.action)) == before
          and curve_for(obj.animation_data.action, "location", 2).evaluate(20) != -0.001)


# ---------------------------------------------------------------------------
# 3. set_material_emission
# ---------------------------------------------------------------------------

def test_emission_material_is_created():
    section("set_material_emission — an object with no material gets one")
    obj = make_box("Bulb", 8.0, center_mm=(0.0, 40.0, 0.0))
    check("the fixture starts with no material", obj.active_material is None)

    reply = set_material_emission(object="Bulb", strength=4.0, color=[1.0, 0.7, 0.3])
    if not check("set_material_emission succeeded", ok(reply), message(reply)):
        return
    data = result(reply)
    check("it says it made one", data.get("created_material") is True)
    check("named Forge Glow", (data.get("material") or "").startswith("Forge Glow"),
          data.get("material"))
    check("the node is an emission shader", data.get("node_type") == "EMISSION",
          data.get("node_type"))
    check("strength echoed", abs((data.get("strength") or 0) - 4.0) < 1e-6)
    check("colour echoed with alpha", data.get("color") == [1.0, 0.7, 0.3, 1.0],
          data.get("color"))
    check("nothing was keyed without a frame", data.get("keyed") == [])
    check("the honest note about Workbench is there",
          any("Workbench" in n for n in data.get("notes") or []), data.get("notes"))

    material = bpy.data.materials.get(data.get("material"))
    if not check("the material exists in the file", material is not None):
        return
    # `is` is wrong on bpy structs: RNA hands out a fresh Python wrapper per
    # access, so two wrappers for the same datablock are never the same object.
    check("it is on the object", obj.active_material == material)
    node = material.node_tree.nodes.get(data.get("node"))
    if not check("the node exists", node is not None):
        return
    check("strength is really 4 on the node",
          abs(node.inputs["Strength"].default_value - 4.0) < 1e-6,
          node.inputs["Strength"].default_value)
    check("colour is really warm on the node",
          abs(node.inputs["Color"].default_value[1] - 0.7) < 1e-6,
          tuple(node.inputs["Color"].default_value))
    wiring = [(link.from_node.name, link.from_socket.name, link.to_node.name,
               link.to_socket.name) for link in material.node_tree.links]
    surface = None
    for link in material.node_tree.links:
        if link.to_node.type == "OUTPUT_MATERIAL":
            surface = link.from_node
    check("the emission is wired to the material output", surface == node, wiring)


def test_emission_keyframes_snap():
    section("set_material_emission — keyed off, then on, with no fade")
    off = set_material_emission(object="Bulb", strength=0.0, frame=1)
    on = set_material_emission(object="Bulb", strength=6.0, color=[1.0, 0.9, 0.6],
                               frame=8)
    if not check("both keys were accepted", ok(off) and ok(on),
                 message(off) or message(on)):
        return
    off_data, on_data = result(off), result(on)
    check("the off key reports what it keyed", off_data.get("keyed") == ["strength"],
          off_data.get("keyed"))
    check("the on key keyed strength AND colour",
          on_data.get("keyed") == ["strength", "color"], on_data.get("keyed"))
    check("CONSTANT is the default for a light",
          on_data.get("interpolation") == "CONSTANT", on_data.get("interpolation"))
    check("the frame is echoed", on_data.get("frame") == 8)
    check("an action holds the keys", bool(on_data.get("action")))

    material = bpy.data.materials.get(on_data.get("material"))
    tree = material.node_tree
    action = tree.animation_data.action
    node = tree.nodes.get(on_data.get("node"))
    path = node.inputs["Strength"].path_from_id("default_value")
    curve = curve_for(action, path, 0)
    if not check("there is a strength F-curve on the node tree", curve is not None,
                 [c.data_path for c in curves_of(action)]):
        return
    check("two keyframe points", len(curve.keyframe_points) == 2,
          len(curve.keyframe_points))
    check("dark at frame 1", abs(curve.evaluate(1)) < 1e-6, curve.evaluate(1))
    check("lit at frame 8", abs(curve.evaluate(8) - 6.0) < 1e-6, curve.evaluate(8))
    check("STILL DARK at frame 7 — an LED does not fade up",
          abs(curve.evaluate(7)) < 1e-6, curve.evaluate(7))
    check("still lit after the last key", abs(curve.evaluate(30) - 6.0) < 1e-6)
    check("the points are CONSTANT",
          all(p.interpolation == "CONSTANT" for p in curve.keyframe_points))

    colour_path = node.inputs["Color"].path_from_id("default_value")
    green = curve_for(action, colour_path, 1)
    check("the colour was keyed too", green is not None
          and abs(green.evaluate(8) - 0.9) < 1e-6,
          green.evaluate(8) if green else None)


def test_emission_reuses_an_existing_material():
    section("set_material_emission — an existing Principled keeps its look")
    obj = make_box("Shell", 24.0, center_mm=(0.0, -40.0, 0.0))
    material = bpy.data.materials.new("Shell Plastic")
    material.use_nodes = True
    obj.data.materials.append(material)

    reply = set_material_emission(object="Shell", strength=2.0)
    if not check("emission on a Principled material works", ok(reply), message(reply)):
        return
    data = result(reply)
    check("no new material was made", data.get("created_material") is False)
    check("it used the material that was there", data.get("material") == "Shell Plastic",
          data.get("material"))
    check("through the Principled's own emission inputs",
          data.get("node_type") == "BSDF_PRINCIPLED", data.get("node_type"))
    check("and says so", any("keeps the look" in n for n in data.get("notes") or []),
          data.get("notes"))
    node = material.node_tree.nodes.get(data.get("node"))
    check("Emission Strength really is 2",
          abs(node.inputs["Emission Strength"].default_value - 2.0) < 1e-6)


def test_emission_bad_input():
    section("set_material_emission — bad input is a sentence")
    empty = bpy.data.objects.new("Marker", None)
    bpy.context.scene.collection.objects.link(empty)

    for label, params, needle in (
        ("an object that cannot hold a material",
         {"object": "Marker", "strength": 1.0}, "cannot hold a material"),
        ("a missing strength", {"object": "Bulb"}, "strength"),
        ("a negative strength", {"object": "Bulb", "strength": -1.0}, ">="),
        ("a two-component colour", {"object": "Bulb", "strength": 1.0, "color": [1, 0]},
         "[r, g, b]"),
        ("a name nobody has", {"object": "Bulbb", "strength": 1.0}, "Did you mean"),
    ):
        reply = set_material_emission(**params)
        check("refused: %s" % label, not ok(reply))
        check("...saying why (%s)" % label, needle in message(reply), message(reply))

    bpy.data.objects.remove(empty, do_unlink=True)


# ---------------------------------------------------------------------------
# 4/5. render_animation
# ---------------------------------------------------------------------------

def test_render_animation_writes_a_film(tmpdir):
    section("render_animation — a real .mp4, and the scene handed back intact")
    path = os.path.join(tmpdir, "press.mp4")
    before = scene_state()

    started = time.monotonic()
    reply = render_animation(path=path, frame_start=1, frame_end=12, fps=24,
                             resolution=320, engine="workbench", objects=["Flame"])
    elapsed = time.monotonic() - started
    if not check("render_animation succeeded", ok(reply), message(reply)):
        return
    data = result(reply)
    note("12 frames at 320 px on %s in %.1f s" % (data.get("engine"), elapsed))

    check("the file is where it was asked for", data.get("path") == path, data.get("path"))
    check("the file exists", os.path.exists(path))
    check("no frame-range file beside it",
          not any(name.startswith("press0") for name in os.listdir(tmpdir)),
          os.listdir(tmpdir))
    info = mp4_info(path) if os.path.exists(path) else None
    if check("it is an ISO base-media file (ftyp magic)", info is not None):
        check("with an mp4 brand", "mp4" in info["brand"] or "iso" in info["brand"],
              info["brand"])
        check("and a non-zero duration", (info["duration_s"] or 0) > 0.0,
              info["duration_s"])
        check("that matches 12 frames at 24 fps (0.5 s)",
              abs((info["duration_s"] or 0) - 0.5) < 0.25, info["duration_s"])
        check("and it is bigger than a header", info["bytes"] > 1000, info["bytes"])
    check("frames counted inclusively", data.get("frames") == 12, data.get("frames"))
    check("duration reported", abs((data.get("duration_s") or 0) - 0.5) < 1e-6)
    check("container is mp4", data.get("container") == "MPEG4", data.get("container"))
    check("codec is H.264", data.get("codec") == "H264", data.get("codec"))
    check("size reported", (data.get("size_bytes") or 0) > 1000)
    check("the camera measured several frames of the clip",
          len(data.get("framed_over_frames") or []) > 1,
          data.get("framed_over_frames"))
    check("the honesty line is in the result",
          "not a physics simulation" in (data.get("honesty") or ""),
          data.get("honesty"))
    check("Workbench warns that an emission will not show",
          any("Workbench" in n for n in data.get("notes") or []), data.get("notes"))

    after = scene_state()
    differences = diff_state(before, after)
    check("every borrowed setting was put back", not differences, differences)
    check("no camera left behind",
          not any(o.name.startswith("Forge Demo") for o in bpy.data.objects))
    check("no camera datablock left behind",
          not any(c.name.startswith("Forge Demo") for c in bpy.data.cameras))
    check("no light datablock left behind",
          not any(lamp.name.startswith("Forge Demo") for lamp in bpy.data.lights))


def test_moving_subject_stays_in_frame(tmpdir):
    section("render_animation — the camera frames the whole clip, not frame one")
    make_box("Traveller", 10.0)
    animate_object(object="Traveller", clear=True,
                   keys=[{"frame": 1, "location": [0.0, 0.0, 0.0]},
                         {"frame": 10, "location": [0.0, 0.0, 0.2]}])
    reply = render_animation(path=os.path.join(tmpdir, "travel.mp4"),
                             frame_start=1, frame_end=10, resolution=200,
                             engine="workbench", objects=["Traveller"])
    if not check("a moving subject renders", ok(reply), message(reply)):
        return
    data = result(reply)
    size = (data.get("bounds_mm") or {}).get("size") or [0, 0, 0]
    check("the measured bounds span the travel (10 mm box + 200 mm of motion)",
          size[2] > 190.0, size)
    check("the ortho scale grew to the motion, not to the 10 mm box",
          (data.get("ortho_scale_mm") or 0) > 150.0, data.get("ortho_scale_mm"))
    bpy.data.objects.remove(bpy.data.objects["Traveller"], do_unlink=True)


def test_render_bad_input(tmpdir):
    section("render_animation — bad input is a sentence")
    for label, params, needle in (
        ("no path", {"frame_start": 1, "frame_end": 4}, "'path'"),
        ("no frame_start", {"path": os.path.join(tmpdir, "x.mp4"), "frame_end": 4},
         "frame_start"),
        ("a backwards range", {"path": os.path.join(tmpdir, "x.mp4"),
                               "frame_start": 10, "frame_end": 2}, "is before"),
        ("a clip nobody would wait for", {"path": os.path.join(tmpdir, "x.mp4"),
                                          "frame_start": 1, "frame_end": 2000},
         "more than this command renders"),
        ("an unknown engine", {"path": os.path.join(tmpdir, "x.mp4"),
                               "frame_start": 1, "frame_end": 4, "engine": "cycles"},
         "must be one of"),
        ("an impossible fps", {"path": os.path.join(tmpdir, "x.mp4"),
                               "frame_start": 1, "frame_end": 4, "fps": 0},
         "fps"),
        ("a folder for a path", {"path": tmpdir, "frame_start": 1, "frame_end": 4},
         "is a folder"),
        ("an object nobody has", {"path": os.path.join(tmpdir, "x.mp4"),
                                  "frame_start": 1, "frame_end": 4,
                                  "objects": ["Nope"]}, "No object named"),
    ):
        reply = render_animation(**params)
        check("refused: %s" % label, not ok(reply))
        check("...saying why (%s)" % label, needle in message(reply), message(reply))

    check("nothing was written by any of the refusals",
          not os.path.exists(os.path.join(tmpdir, "x.mp4")))


def test_extension_is_forced(tmpdir):
    section("render_animation — a path without .mp4 gets one")
    reply = render_animation(path=os.path.join(tmpdir, "noext"), frame_start=1,
                             frame_end=3, resolution=160, engine="workbench",
                             objects=["Flame"])
    if not check("it rendered", ok(reply), message(reply)):
        return
    data = result(reply)
    check("the extension was added", data.get("path", "").endswith("noext.mp4"),
          data.get("path"))
    check("and that file is what exists", os.path.exists(data.get("path", "")))


# ---------------------------------------------------------------------------
# 6. the end-to-end demo
# ---------------------------------------------------------------------------

def test_end_to_end_press_and_light(tmpdir):
    section("end to end — press 1.8 mm over 12 frames, LED on at the latch")
    make_box("DemoFlame", 18.0, center_mm=(0.0, 0.0, 20.0))
    make_box("DemoBody", 30.0, center_mm=(0.0, 0.0, 0.0))

    press = animate_object(
        object="DemoFlame", clear=True, interpolation="BEZIER",
        keys=[{"frame": 1, "location_mm": [0, 0, 0]},
              {"frame": 8, "location_mm": [0, 0, -1.8]},
              {"frame": 12, "location_mm": [0, 0, -1.5]}])
    dark = set_material_emission(object="DemoFlame", strength=0.0,
                                 color=[1.0, 0.6, 0.2], frame=1)
    lit = set_material_emission(object="DemoFlame", strength=8.0,
                                color=[1.0, 0.6, 0.2], frame=8)
    check("the press was keyed", ok(press), message(press))
    check("the LED was keyed off", ok(dark), message(dark))
    check("the LED was keyed on at the latch frame", ok(lit), message(lit))

    path = os.path.join(tmpdir, "litwick-demo.mp4")
    started = time.monotonic()
    film = render_animation(path=path, frame_start=1, frame_end=12, fps=24,
                            resolution=320, engine="eevee",
                            objects=["DemoFlame", "DemoBody"])
    elapsed = time.monotonic() - started
    if not check("the demo rendered", ok(film), message(film)):
        return
    data = result(film)
    note("12 frames at 320 px on %s in %.1f s" % (data.get("engine"), elapsed))
    check("both objects are in the film", data.get("objects") == ["DemoFlame", "DemoBody"],
          data.get("objects"))
    info = mp4_info(path)
    check("the demo is a real mp4", info is not None and (info["duration_s"] or 0) > 0,
          info)
    if data.get("engine") != "BLENDER_WORKBENCH":
        check("it rendered on EEVEE, so the LED is visible",
              "EEVEE" in (data.get("engine") or ""), data.get("engine"))
    else:
        note("EEVEE was unavailable here; the fallback note says so: %s"
             % (data.get("notes") or []))

    curve = curve_for(bpy.data.objects["DemoFlame"].animation_data.action,
                      "location", 2)
    check("the flame really moves 1.8 mm at the click",
          abs(curve.evaluate(8) * 1000.0 + 1.8) < 1e-3, curve.evaluate(8))
    check("and rides back up to the latched 1.5 mm",
          abs(curve.evaluate(12) * 1000.0 + 1.5) < 1e-3, curve.evaluate(12))


# ---------------------------------------------------------------------------
# 7. the budget
# ---------------------------------------------------------------------------

def test_budget(tmpdir):
    section("the budget — 48 frames at 640 px is tens of seconds")
    path = os.path.join(tmpdir, "budget.mp4")
    started = time.monotonic()
    reply = render_animation(path=path, frame_start=1, frame_end=BUDGET_FRAMES,
                             fps=24, resolution=BUDGET_RESOLUTION, engine="eevee",
                             objects=["DemoFlame", "DemoBody"])
    elapsed = time.monotonic() - started
    if not check("the budget render succeeded", ok(reply), message(reply)):
        return
    data = result(reply)
    note("MEASURED: %d frames at %d px on %s in %.1f s (%.2f s/frame)"
         % (BUDGET_FRAMES, BUDGET_RESOLUTION, data.get("engine"), elapsed,
            elapsed / float(BUDGET_FRAMES)))
    check("under the loose upper bound of %.0f s" % BUDGET_SECONDS,
          elapsed < BUDGET_SECONDS, "%.1f s" % elapsed)
    info = mp4_info(path)
    check("48 frames at 24 fps is about 2 seconds of film",
          info is not None and abs((info["duration_s"] or 0) - 2.0) < 0.35,
          info["duration_s"] if info else None)
    check("the resolution stayed even", data.get("resolution") == BUDGET_RESOLUTION)


def test_odd_resolution_is_rounded(tmpdir):
    section("render_animation — an odd resolution is rounded, and said so")
    reply = render_animation(path=os.path.join(tmpdir, "odd.mp4"), frame_start=1,
                             frame_end=3, resolution=201, engine="workbench",
                             objects=["Flame"])
    if not check("it rendered anyway", ok(reply), message(reply)):
        return
    data = result(reply)
    check("rounded up to 202", data.get("resolution") == 202, data.get("resolution"))
    check("and said why", any("even pixel" in n for n in data.get("notes") or []),
          data.get("notes"))


def test_port_is_free_after():
    section("teardown")
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
    print("Forge headless tests — Phase 17 mechanism demos")
    enable_addon()
    tmpdir = tempfile.mkdtemp(prefix="forge_mechanism_")
    note("films in %s" % tmpdir)

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
        test_animate_round_trip()
        test_metres_and_degrees()
        test_quaternion_object_is_moved_to_euler()
        test_clear_is_idempotent()
        test_animate_bad_input()
        test_emission_material_is_created()
        test_emission_keyframes_snap()
        test_emission_reuses_an_existing_material()
        test_emission_bad_input()
        test_render_animation_writes_a_film(tmpdir)
        test_moving_subject_stays_in_frame(tmpdir)
        test_render_bad_input(tmpdir)
        test_extension_is_forced(tmpdir)
        test_odd_resolution_is_rounded(tmpdir)
        test_end_to_end_press_and_light(tmpdir)
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
