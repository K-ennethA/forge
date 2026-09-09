"""Mechanism demos (Phase 17) — showing how a functional design *works*.

Three commands, and between them the robotics-site trick: a part that presses,
a light that comes on, and a short film of the two happening together.

* ``animate_object`` — object-level keyframing.  The sibling of
  ``rigforge_keyframe``: same validation shape, same near-miss hints, same
  parse-everything-before-you-touch-anything rule, but on an object's own
  transform rather than on a rig's pose bones.  A plunger is not a character.
* ``set_material_emission`` — the LED turning on.  Ensures the object has a
  material that can emit, sets the strength and the colour, and keyframes both
  when a ``frame`` is given.
* ``render_animation`` — the film.  A temporary orthographic camera framed the
  way ``render_preview`` frames one, Blender's own ffmpeg writing an ``.mp4``,
  and **every borrowed setting put back** in a ``finally``.

What these three are NOT
------------------------
A simulation.  Nothing here computes a force, a spring rate or a collision: the
motion is the numbers ``plunger_plan`` already worked out, played back.  The
demo is an **illustration of the intended motion**, not proof that the
mechanism moves — the same credibility gap between a rendered picture and a
print.  The assistant is told to say so in words every time it shows one, and
this docstring is where that rule is written down.

Undo
----
``animate_object`` and ``set_material_emission`` change the .blend, so both push
a checkpoint (the registry does it).  ``render_animation`` is in
``READ_ONLY_COMMANDS`` for exactly the reasons ``render_preview`` and
``turntable`` are: it borrows the render settings and a camera, puts every one
of them back, and writes one file that undo could never take back anyway.
"""

import difflib
import math
import os
import time

import bpy

from . import common
from . import rigforge_rig
from .common import (
    MM_TO_M,
    M_TO_MM,
    get_bool,
    get_choice,
    get_int,
    get_str,
)
from .registry import ForgeError, command

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

#: The same interpolation vocabulary ``rigforge_keyframe`` takes, so an animator
#: who learned one call has learned the other.
INTERPOLATIONS = ("BEZIER", "LINEAR", "CONSTANT", "SINE", "QUAD", "CUBIC",
                  "BACK", "BOUNCE", "ELASTIC")

#: Rotation modes ``rotation_euler`` is meaningful in.
EULER_MODES = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")


def _object_hint(wanted, limit=25):
    """" Did you mean 'Flame'? Objects here: ..." — the ``_bone_hint`` shape."""
    names = [obj.name for obj in bpy.data.objects]
    if not names:
        return " The file contains no objects."
    close = difflib.get_close_matches(wanted, names, n=3, cutoff=0.5)
    if not close and wanted:
        lowered = wanted.lower()
        close = [name for name in names if lowered in name.lower()][:3]
    hint = ""
    if close:
        hint = " Did you mean %s?" % ", ".join(repr(name) for name in close)
    shown = names[:limit]
    more = ", ... (%d in all)" % len(names) if len(names) > limit else ""
    return "%s Objects here: %s%s." % (hint, ", ".join(shown), more)


def resolve_target(params, key="object"):
    """The object to act on, with a near-miss suggestion when the name is wrong.

    ``common.find_object`` lists the scene; this lists the scene *and* guesses,
    because a demo is written from a plan and the plan spells the flame's name
    slightly differently from the artist's viewport about half the time.
    """
    name = params.get(key)
    if isinstance(name, str) and name.strip():
        wanted = name.strip()
        obj = bpy.data.objects.get(wanted)
        if obj is None:
            raise ForgeError("There is no object called %r.%s"
                             % (wanted, _object_hint(wanted)))
        return obj
    if name is not None and not isinstance(name, str):
        raise ForgeError("Parameter %r must be a string (an object name), got %s."
                         % (key, type(name).__name__))
    obj = common.get_active_object()
    if obj is None:
        raise ForgeError("No %r was given and there is no active object.%s"
                         % (key, _object_hint("")))
    return obj


def _vector3(value, label, index):
    """Three finite numbers, or one number meaning all three."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ForgeError("keys[%d].%s must be finite, got %r." % (index, label, value))
        return [number] * 3
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ForgeError("keys[%d].%s must be three numbers [x, y, z], got %r."
                         % (index, label, value))
    out = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ForgeError("keys[%d].%s must be three numbers, got %r."
                             % (index, label, value))
        number = float(component)
        if not math.isfinite(number):
            raise ForgeError("keys[%d].%s must be finite, got %r." % (index, label, value))
        out.append(number)
    return out


def object_action(obj):
    """The action holding this object's own animation, or ``None``."""
    data = getattr(obj, "animation_data", None)
    return getattr(data, "action", None) if data is not None else None


def action_fcurves(action):
    """Every F-curve of an action, legacy or slotted (Blender 5.0)."""
    if action is None:
        return []
    return rigforge_rig.action_fcurves(action)


def _fcurve_containers(action):
    """The collections the curves actually live in — needed to remove them."""
    containers = []
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        return [curves]
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for slot in getattr(action, "slots", ()):
                try:
                    bag = strip.channelbag(slot)
                except (AttributeError, TypeError, RuntimeError):
                    bag = None
                if bag is not None:
                    containers.append(bag.fcurves)
    return containers


def clear_action(action):
    """Remove every F-curve from an action. Returns how many went away."""
    removed = 0
    for container in _fcurve_containers(action):
        for curve in list(container):
            try:
                container.remove(curve)
                removed += 1
            except (RuntimeError, ReferenceError, TypeError):
                continue
    return removed


def _apply_interpolation(action, touched, interpolation):
    """Set ``interpolation`` on the keyframe points this call actually made."""
    applied = 0
    for curve in action_fcurves(action):
        wanted = touched.get((curve.data_path, curve.array_index))
        if not wanted:
            continue
        for point in curve.keyframe_points:
            if int(round(point.co.x)) in wanted:
                point.interpolation = interpolation
                applied += 1
        try:
            curve.update()
        except (AttributeError, RuntimeError):
            pass
    return applied


# ---------------------------------------------------------------------------
# animate_object
# ---------------------------------------------------------------------------

#: ``{"frame": ..., <channel>: ...}`` -> the object property it writes.
KEY_CHANNELS = {
    "location": "location",
    "location_mm": "location",
    "rotation_euler_deg": "rotation_euler",
    "scale": "scale",
}


@command("animate_object")
def cmd_animate_object(params):
    """Keyframe an object's own transform — the press stroke, in one call.

    ``keys`` is a list of ``{"frame", "location"?, "location_mm"?,
    "rotation_euler_deg"?, "scale"?}``; at least one channel per entry.

    * ``location`` is **scene metres**, Blender's own unit for an object's
      transform, so a value read off the viewport goes straight in.
    * ``location_mm`` is the same channel in **millimetres**, which is the unit
      every number in this toolchain is quoted in — ``plunger_plan`` says the
      cap travels 2.1 mm, and ``{"location_mm": [0, 0, -2.1]}`` is that
      sentence. Giving both for one key is an error, not a merge.
    * ``rotation_euler_deg`` is degrees, and forces the object into ``XYZ``
      euler mode (reported) — the ``rigforge_keyframe`` rule, for the
      ``rigforge_keyframe`` reason.

    ``interpolation`` (default ``BEZIER``) applies to the points this call
    makes and to no others; ``clear`` empties the object's existing animation
    first, which is what makes re-running a demo idempotent.
    """
    started = time.monotonic()
    warnings = []
    obj = resolve_target(params)

    raw_keys = params.get("keys")
    if not isinstance(raw_keys, (list, tuple)) or not raw_keys:
        raise ForgeError(
            "'keys' must be a non-empty list of "
            '{"frame", "location"/"location_mm"/"rotation_euler_deg"/"scale"} objects.')
    interpolation = get_choice(
        params, "interpolation", {name: name for name in INTERPOLATIONS}, "BEZIER")
    clear = get_bool(params, "clear", False)

    # Parse and validate everything before touching a single channel: a typo in
    # keys[7] must not leave keys[0..6] half applied.
    plan = []
    for index, entry in enumerate(raw_keys):
        if not isinstance(entry, dict):
            raise ForgeError("keys[%d] must be an object, got %s."
                             % (index, type(entry).__name__))
        if entry.get("frame") is None:
            raise ForgeError("keys[%d] is missing 'frame'." % index)
        frame = get_int(entry, "frame", minimum=-1_000_000, maximum=1_000_000)
        if entry.get("location") is not None and entry.get("location_mm") is not None:
            raise ForgeError(
                "keys[%d] gives both 'location' (metres) and 'location_mm' "
                "(millimetres) — use one or the other, they are the same channel."
                % index)
        channels = {}
        if entry.get("location") is not None:
            channels["location"] = _vector3(entry["location"], "location", index)
        if entry.get("location_mm") is not None:
            channels["location"] = [
                v * MM_TO_M for v in _vector3(entry["location_mm"], "location_mm", index)]
        if entry.get("rotation_euler_deg") is not None:
            channels["rotation_euler"] = [
                math.radians(v) for v in
                _vector3(entry["rotation_euler_deg"], "rotation_euler_deg", index)]
        if entry.get("scale") is not None:
            channels["scale"] = _vector3(entry["scale"], "scale", index)
        if not channels:
            raise ForgeError(
                "keys[%d] (object %r, frame %d) sets nothing: give it at least one of "
                "location, location_mm, rotation_euler_deg or scale."
                % (index, obj.name, frame))
        plan.append((frame, channels))

    frames = []
    keys_set = 0
    touched = {}
    rotation_mode = None
    cleared = 0

    with common.object_mode():
        existing = object_action(obj)
        if clear and existing is not None:
            cleared = clear_action(existing)

        if any("rotation_euler" in channels for _frame, channels in plan) \
                and obj.rotation_mode not in EULER_MODES:
            rotation_mode = obj.rotation_mode
            obj.rotation_mode = "XYZ"

        for frame, channels in plan:
            for path, value in channels.items():
                setattr(obj, path, value)
                if not obj.keyframe_insert(path, frame=frame):
                    raise ForgeError("Blender refused a %s key on %r at frame %d."
                                     % (path, obj.name, frame))
                keys_set += 1
                for axis in range(3):
                    touched.setdefault((path, axis), set()).add(frame)
            frames.append(frame)

        action = object_action(obj)
        if action is None:
            raise ForgeError(
                "Blender accepted the keys but %r has no action to hold them — "
                "this build's animation data is not what the command expects."
                % obj.name)
        applied = _apply_interpolation(action, touched, interpolation)

    if rotation_mode is not None:
        warnings.append(
            "Rotation mode on %r changed to XYZ euler (it was %s); "
            "rotation_euler_deg is euler by contract." % (obj.name, rotation_mode))

    span = tuple(action.frame_range)
    channels_used = sorted({path for _f, ch in plan for path in ch})
    return {
        "object": obj.name,
        "action": action.name,
        "created_action": existing is None,
        "keys": len(raw_keys),
        "keys_set": keys_set,
        "channels": channels_used,
        "frame_range": [min(frames), max(frames)],
        "frames": max(frames) - min(frames) + 1,
        "action_frame_range": [round(float(span[0]), 4), round(float(span[1]), 4)],
        "interpolation": interpolation,
        "interpolated_points": applied,
        "cleared_fcurves": cleared,
        "fcurves": len(action_fcurves(action)),
        "rotation_mode": obj.rotation_mode,
        "rotation_mode_changed": rotation_mode,
        "units": ("location is scene metres, location_mm is millimetres, "
                  "rotation_euler_deg is degrees"),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# set_material_emission
# ---------------------------------------------------------------------------

#: The material this command makes when an object has none.  Named, not
#: numbered, so the artist can find it in the material list and change it.
GLOW_MATERIAL = "Forge Glow"
GLOW_NODE = "Forge Emission"

#: An LED does not fade up over seven frames; it is off and then it is on.
#: CONSTANT is therefore the honest default for a keyed emission, and the
#: parameter exists for the deliberate exception (a heater, a charge indicator).
EMISSION_INTERPOLATION = "CONSTANT"

MAX_EMISSION_STRENGTH = 1000.0


def _material_slots_owner(obj):
    data = getattr(obj, "data", None)
    if data is None or not hasattr(data, "materials"):
        raise ForgeError(
            "%r is a %s, which cannot hold a material — an emission needs a mesh "
            "(or a curve/text object) to sit on." % (obj.name, obj.type))
    return data


def _output_node(tree):
    for node in tree.nodes:
        if node.type == "OUTPUT_MATERIAL":
            return node
    return tree.nodes.new("ShaderNodeOutputMaterial")


def _new_emission_node(tree, notes):
    """Add a Forge Emission node and put it on the material's surface."""
    node = tree.nodes.new("ShaderNodeEmission")
    node.name = GLOW_NODE
    node.label = GLOW_NODE
    output = _output_node(tree)
    node.location = (output.location[0] - 220.0, output.location[1])
    # Socket names are Blender's, and Blender renames things between releases.
    # The first input of a Material Output is the surface and the first output
    # of an Emission node is the shader, on every build there has ever been.
    surface = output.inputs.get("Surface")
    if surface is None and len(output.inputs):
        surface = output.inputs[0]
    emission = node.outputs.get("Emission")
    if emission is None and len(node.outputs):
        emission = node.outputs[0]
    if surface is not None and emission is not None:
        if surface.is_linked:
            notes.append(
                "This material's surface shader was replaced by an emission node "
                "(Ctrl+Z puts it back).")
        for link in list(surface.links):
            try:
                tree.links.remove(link)
            except (RuntimeError, ReferenceError):
                pass
        tree.links.new(emission, surface)
    else:
        notes.append(
            "The emission node could not be wired to the material output on this "
            "Blender build; connect it by hand in the Shading workspace.")
    return node


def _ensure_emission(obj, notes):
    """``(material, node, strength_socket, colour_socket, created_material)``.

    Three ways in, in order of how little they disturb: an emission node that is
    already there, a Principled BSDF's own emission inputs, or a new emission
    node.  An object with no material at all gets ``Forge Glow`` — a bare
    emission shader, because an LED lens reads as a light and not as a lit
    surface.
    """
    data = _material_slots_owner(obj)
    material = obj.active_material
    created_material = False

    if material is None:
        material = bpy.data.materials.new(GLOW_MATERIAL)
        material.use_nodes = True
        created_material = True
        tree = material.node_tree
        for node in list(tree.nodes):
            if node.type != "OUTPUT_MATERIAL":
                try:
                    tree.nodes.remove(node)
                except (RuntimeError, ReferenceError):
                    pass
        data.materials.append(material)
        try:
            obj.active_material_index = len(data.materials) - 1
        except (AttributeError, TypeError, RuntimeError):
            pass
    elif not material.use_nodes:
        material.use_nodes = True
        notes.append("Turned nodes on for material %r — an emission shader needs them."
                     % material.name)

    tree = material.node_tree
    if tree is None:
        raise ForgeError("Material %r has no node tree to put an emission into."
                         % material.name)

    for node in tree.nodes:
        if node.type == "EMISSION":
            return (material, node, node.inputs.get("Strength"),
                    node.inputs.get("Color"), created_material)

    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            strength = node.inputs.get("Emission Strength")
            colour = node.inputs.get("Emission Color") or node.inputs.get("Emission")
            if strength is not None:
                notes.append(
                    "Used the existing Principled shader's own emission inputs, so "
                    "%r keeps the look it had." % material.name)
                return material, node, strength, colour, created_material

    node = _new_emission_node(tree, notes)
    return (material, node, node.inputs.get("Strength"), node.inputs.get("Color"),
            created_material)


def _colour(value):
    """``[r, g, b]`` or ``[r, g, b, a]`` -> four finite floats."""
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        raise ForgeError("'color' must be [r, g, b] (0..1 each), got %r." % (value,))
    out = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ForgeError("'color' must be numbers 0..1, got %r." % (value,))
        number = float(component)
        if not math.isfinite(number) or number < 0.0:
            raise ForgeError("'color' must be finite and >= 0, got %r." % (value,))
        out.append(number)
    if len(out) == 3:
        out.append(1.0)
    return out


def _socket_path(socket):
    try:
        return socket.path_from_id("default_value")
    except (AttributeError, RuntimeError, TypeError):
        return None


@command("set_material_emission")
def cmd_set_material_emission(params):
    """Make an object glow — and keyframe the moment it does.

    ``{"object", "strength", "color"?: [r, g, b], "frame"?, "interpolation"?}``.

    This is the LED.  With no ``frame`` it is a state: the thing is lit, now,
    and a ``render_preview`` with ``shading="material"`` will show it.  With a
    ``frame`` it is an event: the light is keyed *at* that frame, so
    ``strength: 0`` at the frame before the click and ``strength: 6`` at the
    click is an LED coming on when the latch catches.

    Keys are ``CONSTANT`` by default, because that is what an LED does.
    """
    started = time.monotonic()
    notes = []
    obj = resolve_target(params)

    strength = common.get_float(params, "strength", minimum=0.0,
                                maximum=MAX_EMISSION_STRENGTH)
    colour = _colour(params["color"]) if params.get("color") is not None else None
    frame = None
    if params.get("frame") is not None:
        frame = get_int(params, "frame", minimum=-1_000_000, maximum=1_000_000)
    interpolation = get_choice(
        params, "interpolation", {name: name for name in INTERPOLATIONS},
        EMISSION_INTERPOLATION)

    material, node, strength_socket, colour_socket, created = _ensure_emission(obj, notes)
    if strength_socket is None:
        raise ForgeError(
            "The %s node in material %r has no strength input, so there is "
            "nothing to turn up." % (node.type, material.name))

    keyed = []
    touched = {}

    strength_socket.default_value = strength
    if colour is not None:
        if colour_socket is None:
            notes.append(
                "This shader has no emission colour input, so only the strength "
                "was set.")
        else:
            try:
                size = len(colour_socket.default_value)
            except TypeError:
                size = 4
            colour_socket.default_value = colour[:size] if size <= 4 else colour

    if frame is not None:
        strength_socket.keyframe_insert("default_value", frame=frame)
        keyed.append("strength")
        path = _socket_path(strength_socket)
        if path:
            touched[(path, 0)] = {frame}
            touched[(path, -1)] = {frame}
        if colour is not None and colour_socket is not None:
            colour_socket.keyframe_insert("default_value", frame=frame)
            keyed.append("color")
            path = _socket_path(colour_socket)
            if path:
                for axis in range(4):
                    touched[(path, axis)] = {frame}
        action = getattr(getattr(material.node_tree, "animation_data", None),
                         "action", None)
        applied = _apply_interpolation(action, touched, interpolation) if action else 0
    else:
        action = getattr(getattr(material.node_tree, "animation_data", None),
                         "action", None)
        applied = 0

    if created:
        notes.append("%r had no material, so it got a new one called %r — a plain "
                     "emission shader, which is what an LED lens looks like."
                     % (obj.name, material.name))
    notes.append("Emission only shows in a render with materials: "
                 'render_preview(shading="material") or render_animation with '
                 'engine="eevee". Workbench draws clay and will not show it.')

    return {
        "object": obj.name,
        "material": material.name,
        "created_material": created,
        "node": node.name,
        "node_type": node.type,
        "strength": round(float(strength), 4),
        "color": [round(v, 4) for v in colour] if colour is not None else None,
        "frame": frame,
        "keyed": keyed,
        "keyframed": bool(keyed),
        "interpolation": interpolation if keyed else None,
        "interpolated_points": applied,
        "action": action.name if action is not None else None,
        "fcurves": len(action_fcurves(action)) if action is not None else 0,
        "notes": notes,
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# render_animation
# ---------------------------------------------------------------------------

#: 640 px square: big enough to read a 2 mm stroke, small enough that a
#: 48-frame EEVEE demo is tens of seconds rather than a coffee break.
ANIM_RESOLUTION = 640
ANIM_MIN_RESOLUTION = 128
ANIM_MAX_RESOLUTION = 1920

ANIM_FPS = 24
ANIM_MIN_FPS = 1
ANIM_MAX_FPS = 60

#: A mechanism demo is two seconds of a thing moving.  The cap is not a
#: judgement about art; it is the line past which a headless render stops being
#: something the artist waits for.
ANIM_MAX_FRAMES = 600

#: How many frames of the clip the camera measures before it commits.  The
#: subject MOVES — framing on frame one is how a plunger presses itself out of
#: shot — so the bounds are the union over the clip.
ANIM_BOUND_SAMPLES = 8

ENGINES = {
    "EEVEE": "eevee",
    "MATERIAL": "eevee",
    "RENDERED": "eevee",
    "WORKBENCH": "workbench",
    "SOLID": "workbench",
}


def _even(value):
    """H.264 wants both dimensions even; an odd one is a silent encoder failure."""
    return value if value % 2 == 0 else value + 1


def _clip_bounds(scene, targets, frame_start, frame_end):
    """World bounds over the whole clip, plus the frames that were measured."""
    if frame_end <= frame_start:
        sampled = [frame_start]
    else:
        span = frame_end - frame_start
        steps = min(ANIM_BOUND_SAMPLES, span + 1)
        sampled = sorted({
            int(round(frame_start + span * i / float(steps - 1)))
            for i in range(steps)
        })
    lows = []
    highs = []
    for frame in sampled:
        scene.frame_set(frame)
        common.refresh_view_layer()
        low, high = common._preview_bounds(targets)
        lows.append(low)
        highs.append(high)
    low = tuple(min(entry[i] for entry in lows) for i in range(3))
    high = tuple(max(entry[i] for entry in highs) for i in range(3))
    return low, high, sampled


def _configure_ffmpeg(scene, fps, notes):
    """Blender's own ffmpeg, writing an H.264 .mp4. Returns what it was set to.

    Blender 5.0 split the output formats in two: ``image_settings.media_type``
    picks IMAGE or VIDEO and ``file_format``'s enum is filtered by it, so on 5.0
    ``file_format = "FFMPEG"`` fails with *enum "FFMPEG" not found* until the
    media type says VIDEO. Older builds have no ``media_type`` at all and take
    the format directly, so both paths have to exist.
    """
    render = scene.render
    settings = render.image_settings
    if hasattr(settings, "media_type"):
        try:
            settings.media_type = "VIDEO"
        except (TypeError, ValueError) as exc:
            raise ForgeError(
                "This Blender build will not switch its output to video (%s), so "
                "it cannot write an .mp4." % exc)
    try:
        settings.file_format = "FFMPEG"
    except (TypeError, ValueError) as exc:
        raise ForgeError(
            "This Blender build has no FFMPEG output format (%s), so it cannot "
            "write an .mp4. Render a turntable or a PNG instead." % exc)
    settings.color_mode = "RGB"
    ffmpeg = getattr(render, "ffmpeg", None)
    if ffmpeg is None:  # pragma: no cover - every shipped build has it
        raise ForgeError(
            "This Blender build has no ffmpeg support, so it cannot write an "
            ".mp4. Render a turntable or a PNG instead.")
    for name, value in (
        ("format", "MPEG4"),
        ("codec", "H264"),
        ("constant_rate_factor", "MEDIUM"),
        ("ffmpeg_preset", "GOOD"),
        ("audio_codec", "NONE"),
    ):
        try:
            setattr(ffmpeg, name, value)
        except (AttributeError, TypeError, ValueError):
            notes.append("This Blender build would not take ffmpeg.%s = %r."
                         % (name, value))
    render.fps = fps
    render.fps_base = 1.0
    return {"container": getattr(ffmpeg, "format", None),
            "codec": getattr(ffmpeg, "codec", None)}


@command("render_animation")
def cmd_render_animation(params):
    """Render the keyed motion to an .mp4 — the mechanism demo itself.

    ``{"path" (.mp4), "frame_start", "frame_end", "fps"?: 24,
    "resolution"?: 640, "engine"?: "eevee"|"workbench", "objects"?, "view"?}``

    The camera is ``render_preview``'s camera: temporary, orthographic, and
    fitted to the subject — except that the subject moves, so the fit is over
    the **union of the bounds across the clip** and nothing presses itself out
    of frame.  ``eevee`` is the default because the LED is usually the point and
    Workbench cannot show an emission at all.

    Read-only: every borrowed setting comes back in a ``finally``, and the one
    thing left behind is the file.
    """
    started = time.monotonic()

    path = common.resolve_path(get_str(params, "path"))
    if os.path.isdir(path):
        raise ForgeError(
            "%r is a folder, not a file to write a film to. Give the whole "
            "filename, ending in .mp4." % path)
    if os.path.splitext(path)[1].lower() != ".mp4":
        path += ".mp4"
    path = common.resolve_path(path, make_parents=True)

    frame_start = get_int(params, "frame_start", minimum=-1_000_000, maximum=1_000_000)
    frame_end = get_int(params, "frame_end", minimum=-1_000_000, maximum=1_000_000)
    if frame_end < frame_start:
        raise ForgeError("'frame_end' (%d) is before 'frame_start' (%d)."
                         % (frame_end, frame_start))
    frame_count = frame_end - frame_start + 1
    if frame_count > ANIM_MAX_FRAMES:
        raise ForgeError(
            "%d frames is more than this command renders in one go (%d). A "
            "mechanism demo is a couple of seconds — shorten the range, or drop "
            "the fps." % (frame_count, ANIM_MAX_FRAMES))

    fps = get_int(params, "fps", ANIM_FPS, minimum=ANIM_MIN_FPS, maximum=ANIM_MAX_FPS)
    requested_resolution = get_int(
        params, "resolution", ANIM_RESOLUTION,
        minimum=ANIM_MIN_RESOLUTION, maximum=ANIM_MAX_RESOLUTION)
    resolution = _even(requested_resolution)
    engine_choice = get_choice(params, "engine", ENGINES, "eevee")
    view = get_choice(params, "view",
                      {name: name for name in common.PREVIEW_VIEWS}, "ISO")

    targets, defaulted = common._preview_targets(params)
    scene = common.get_scene()
    notes = []
    if resolution != requested_resolution:
        notes.append("Resolution rounded up to %d: H.264 needs even pixel "
                     "dimensions." % resolution)

    restore = []
    camera_object = None
    camera_data = None
    light_object = None
    light_data = None
    engine = None
    ffmpeg_info = {}
    ortho_scale = 0.0
    low = high = (0.0, 0.0, 0.0)
    sampled = []

    # A stale file must never be reported as a fresh render.
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            raise ForgeError("Could not replace the existing film at %r: %s"
                             % (path, exc))

    try:
        with common.object_mode():
            common._preview_snapshot(restore, scene, (
                "camera", "frame_start", "frame_end", "frame_current", "frame_step",
            ))
            common._preview_snapshot(restore, scene.render, (
                "engine", "filepath", "resolution_x", "resolution_y",
                "resolution_percentage", "film_transparent", "use_overwrite",
                "use_file_extension", "use_stamp", "use_border", "fps", "fps_base",
            ))
            # media_type LAST, so it is restored FIRST (`_preview_restore` walks
            # the list backwards): on Blender 5.0 the file_format enum is
            # filtered by the media type, and putting PNG back while the output
            # still says VIDEO would quietly fail.
            common._preview_snapshot(
                restore, scene.render.image_settings,
                ("file_format", "color_mode", "color_depth", "media_type"))
            ffmpeg = getattr(scene.render, "ffmpeg", None)
            if ffmpeg is not None:
                common._preview_snapshot(restore, ffmpeg, (
                    "format", "codec", "constant_rate_factor", "ffmpeg_preset",
                    "gopsize", "audio_codec",
                ))
            common._preview_snapshot(restore, scene.display, ("render_aa",))
            common._preview_snapshot(restore, scene.display.shading, (
                "light", "color_type", "single_color", "studio_light",
                "background_type", "background_color", "show_shadows",
                "show_specular_highlight", "show_cavity", "cavity_type",
                "show_object_outline", "show_xray",
            ))
            view_settings = getattr(scene, "view_settings", None)
            if view_settings is not None:
                common._preview_snapshot(restore, view_settings,
                                         ("view_transform", "look", "exposure", "gamma"))

            common.refresh_view_layer()
            low, high, sampled = _clip_bounds(scene, targets, frame_start, frame_end)

            chosen = {obj.name for obj in targets}
            for obj in bpy.data.objects:
                if obj.type not in common._RENDERABLE_TYPES or obj.name in chosen:
                    continue
                restore.append((obj, "hide_render", obj.hide_render))
                obj.hide_render = True
            for obj in targets:
                restore.append((obj, "hide_render", obj.hide_render))
                obj.hide_render = False

            if engine_choice == "eevee":
                engine, engine_notes = common._preview_engine(scene, "material")
                notes.extend(engine_notes)
            else:
                engine, _unused = common._preview_engine(scene, "solid")
            if engine == "BLENDER_WORKBENCH":
                common._preview_configure_workbench(scene)
                notes.append(
                    "Workbench draws clay and ignores materials, so an emission "
                    "will not light up in this film — use engine \"eevee\" for "
                    "the LED.")

            render = scene.render
            render.filepath = path
            render.resolution_x = resolution
            render.resolution_y = resolution
            render.resolution_percentage = 100
            render.film_transparent = False
            render.use_overwrite = True
            # The exact-path rule: with use_file_extension on and a path that
            # already ends in .mp4, Blender writes THAT file. Without the
            # extension it would append "0001-0048.mp4" and the caller would be
            # handed a path that does not exist.
            render.use_file_extension = True
            render.use_border = False
            try:
                render.use_stamp = False
            except (AttributeError, TypeError):
                pass
            ffmpeg_info = _configure_ffmpeg(scene, fps, notes)
            if view_settings is not None:
                for name, value in (("view_transform", "Standard"),
                                    ("look", "None"), ("exposure", 0.0),
                                    ("gamma", 1.0)):
                    try:
                        setattr(view_settings, name, value)
                    except (AttributeError, TypeError, ValueError):
                        pass

            scene.frame_start = frame_start
            scene.frame_end = frame_end
            scene.frame_step = 1

            camera_data = bpy.data.cameras.new("Forge Demo Camera")
            camera_object = bpy.data.objects.new("Forge Demo Camera", camera_data)
            scene.collection.objects.link(camera_object)
            ortho_scale, _distance, _radius = common._preview_frame(
                camera_object, common.PREVIEW_VIEWS[view], low, high)
            scene.camera = camera_object

            if engine != "BLENDER_WORKBENCH" and not any(
                obj.type == "LIGHT" for obj in scene.objects
            ):
                light_data = bpy.data.lights.new("Forge Demo Light", type="SUN")
                light_data.energy = 3.0
                light_object = bpy.data.objects.new("Forge Demo Light", light_data)
                scene.collection.objects.link(light_object)
                light_object.rotation_mode = "XYZ"
                light_object.rotation_euler = (math.radians(50.0), 0.0,
                                               math.radians(35.0))
                light_object.location = camera_object.location
                notes.append("Added a temporary sun light: the scene had none.")

            scene.frame_set(frame_start)
            common.refresh_view_layer()
            status = None
            try:
                status = bpy.ops.render.render(animation=True)
            except RuntimeError as exc:
                if engine == "BLENDER_WORKBENCH":
                    raise ForgeError("Blender could not render the animation: %s" % exc)
                notes.append(
                    "EEVEE could not render here (%s), so this demo is Workbench "
                    "clay instead — the motion is right, the LED will not light."
                    % exc)
                engine = "BLENDER_WORKBENCH"
                scene.render.engine = "BLENDER_WORKBENCH"
                common._preview_configure_workbench(scene)
                try:
                    status = bpy.ops.render.render(animation=True)
                except RuntimeError as fallback_exc:
                    raise ForgeError("Blender could not render the animation: %s"
                                     % fallback_exc)
            if status is not None and "FINISHED" not in status:
                raise ForgeError("The render returned %s instead of finishing."
                                 % (", ".join(sorted(status)) or "nothing"))
    finally:
        for obj, data, collection in (
            (camera_object, camera_data, bpy.data.cameras),
            (light_object, light_data, bpy.data.lights),
        ):
            if obj is not None:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except (ReferenceError, RuntimeError):
                    pass
            if data is not None:
                try:
                    if data.users == 0:
                        collection.remove(data)
                except (ReferenceError, RuntimeError):
                    pass
        common._preview_restore(restore)
        try:
            common.refresh_view_layer()
        except Exception:  # noqa: BLE001
            pass

    if not os.path.exists(path):
        raise ForgeError(
            "The render reported success but nothing was written to %r." % path)
    size_bytes = os.path.getsize(path)
    if size_bytes <= 0:
        raise ForgeError("The render wrote an empty file at %r." % path)

    return {
        "path": path,
        "objects": [obj.name for obj in targets],
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frames": frame_count,
        "fps": fps,
        "duration_s": round(frame_count / float(fps), 3),
        "resolution": resolution,
        "view": view.lower(),
        "engine": engine,
        "engine_requested": engine_choice,
        "container": ffmpeg_info.get("container"),
        "codec": ffmpeg_info.get("codec"),
        "size_bytes": size_bytes,
        "framed_all_visible": defaulted,
        "framed_over_frames": sampled,
        "bounds_mm": {
            "min": [round(v * M_TO_MM, 3) for v in low],
            "max": [round(v * M_TO_MM, 3) for v in high],
            "size": [round((high[i] - low[i]) * M_TO_MM, 3) for i in range(3)],
        },
        "ortho_scale_mm": round(ortho_scale * M_TO_MM, 3),
        "duration_ms": int((time.monotonic() - started) * 1000.0),
        "honesty": ("This is the INTENDED motion played back from the numbers, "
                    "not a physics simulation: nothing here computed a force, a "
                    "spring or a collision."),
        "notes": notes,
    }
