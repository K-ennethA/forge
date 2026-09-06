"""The workspace copilot: the assistant DRIVES the viewport instead of describing it.

The motivating bug is one sentence long.  An artist in Sculpt Mode asked "I want
to enable grid view for x,y,z axis" and got a nine-step tutorial.  The add-on was
already running *inside* their Blender; every one of those nine steps is one line
of ``bpy``.  The tutorial happened because there was no vocabulary here for the
viewport — so this module is that vocabulary.

Seven commands, and one rule they all share: **do it, then say what changed and
where the switch lives.**  Every result carries

* ``changed`` — one plain-language line naming what is different now, and
* ``where``  — the place in Blender's own UI the artist would have clicked,

because a copilot that only does things leaves the artist no better off next
time.  Those two fields are the whole teaching contract; the system prompt turns
them into the reply.

Undo
----
None of these commands are in the undo stack's world.  Viewport state (shading,
overlays, the camera angle) is not Ctrl-Z-able in Blender at all, and mode and
brush changes are noise in an undo history the artist keeps for their *sculpt*.
So all seven are in ``READ_ONLY_COMMANDS``: no checkpoint is pushed, and the
``changed`` line is what makes the change reversible by hand.  Transparency
replaces undo.

Which viewport
--------------
**Every VIEW_3D area in every window.**  An artist with a quad split, or a
second window on a second monitor, asked for the grid *on*, not for the grid on
whichever area Blender happened to hand us.  ``capture_viewport`` is the one
exception — a picture is one picture, so it uses the **largest** VIEW_3D, which
is the one they are working in.  Both choices are reported in the result
(``viewports``, ``area``).

Headless
--------
``blender --background`` has no artist and no windows worth changing (it does
build one off-screen screen, which is exactly the trap: the commands would
"succeed" against a viewport nobody is looking at).  So every window-dependent
command refuses in ``--background`` with :data:`NO_VIEWPORT`, a sentence rather
than a crash.  ``set_mode`` and ``sculpt_brush`` do NOT refuse: mode lives on the
object and brush settings live in the scene's tool settings, so both are real,
testable work with no window involved.
"""

import difflib
import math
import os

import bpy
from mathutils import Euler

from . import common
from .registry import ForgeError, command

__all__ = [
    "NO_VIEWPORT",
    "view3d_areas",
    "largest_view3d",
    "view_quaternion",
    "apply_view",
    "apply_shading",
    "apply_overlays",
    "overlay_summary",
    "mode_key",
    "frame_distance",
    "apply_framing",
    "brush_candidates",
    "essentials_brushes",
    "activate_brush",
    "resolve_brush",
    "WHERE",
]


#: Said when there is no artist to change anything for.  Names the reason and
#: the fix in one sentence, because this message reaches the model, and a model
#: that reads "no viewport" retries; one that reads this explains.
NO_VIEWPORT = (
    "There is no 3D viewport to change: this Blender is running headless "
    "(--background), so it has no windows. Viewport commands only mean "
    "something in the artist's live Blender session."
)

#: Said when there ARE windows but none of them is showing a 3D viewport.
NO_VIEW3D_OPEN = (
    "No 3D viewport is open in Blender right now — every area is showing "
    "something else (the Shader editor, the Outliner, a spreadsheet). Switch "
    "one area back to the 3D Viewport with the editor-type button in its "
    "top-left corner, then ask again."
)


# ---------------------------------------------------------------------------
# where the switch lives — the second half of every reply
# ---------------------------------------------------------------------------

#: command -> the place in Blender's UI the artist would have clicked.  Written
#: the way the rest of Forge writes manual steps: the panel, the box, the button
#: and the key, never "in the appropriate panel".
WHERE = {
    "set_view": ("the View menu at the top of the viewport, or the numpad: "
                 "1 front, 3 side, 7 top, 0 camera, 5 toggles flat/perspective"),
    "frame_object": ("View menu > Frame Selected, or select it and press "
                     "Numpad . (the dot)"),
    "local_view": ("View menu > Local View > Toggle Local View, or press / "
                   "(forward slash) with something selected"),
    "set_shading": ("the four little spheres at the top right of the viewport "
                    "(wireframe, solid, material, rendered), or press Z"),
    "set_overlays": ("the Overlays dropdown — the two overlapping circles at "
                     "the top right of the viewport; click the small arrow "
                     "next to them for Grid, Axes, Wireframe and Statistics"),
    "set_mode": ("the mode dropdown at the top LEFT of the viewport (it says "
                 "Object Mode), or press Ctrl+Tab for the pie menu"),
    "sculpt_brush": ("the toolbar down the left of the viewport (press T to "
                     "show it) for the brush, and the row along the top for "
                     "Radius and Strength — or press F to drag the size and "
                     "Shift+F for the strength"),
    "capture_viewport": ("nothing changed — this is a screenshot of what you "
                         "are already looking at"),
}


# ---------------------------------------------------------------------------
# finding viewports
# ---------------------------------------------------------------------------

def _windows():
    manager = getattr(bpy.context, "window_manager", None)
    try:
        return list(getattr(manager, "windows", None) or [])
    except (AttributeError, TypeError):
        return []


def _space_of(area):
    """The VIEW_3D space of ``area``, or ``None``."""
    space = getattr(getattr(area, "spaces", None), "active", None)
    if space is not None and getattr(space, "type", None) == "VIEW_3D":
        return space
    for candidate in getattr(area, "spaces", []) or []:
        if getattr(candidate, "type", None) == "VIEW_3D":
            return candidate
    return None


def _window_region(area):
    """The area's main WINDOW region — what an operator override needs."""
    for region in getattr(area, "regions", []) or []:
        if getattr(region, "type", None) == "WINDOW":
            return region
    return None


def view3d_areas(require=True):
    """``[(window, area, space)]`` for every 3D viewport in every window.

    Refuses in ``--background`` before looking: Blender builds an off-screen
    screen there, and "succeeding" against a viewport with no artist in front of
    it is worse than saying so.
    """
    if bpy.app.background:
        if require:
            raise ForgeError(NO_VIEWPORT)
        return []
    found = []
    for window in _windows():
        screen = getattr(window, "screen", None)
        for area in getattr(screen, "areas", []) or []:
            if getattr(area, "type", None) != "VIEW_3D":
                continue
            space = _space_of(area)
            if space is not None:
                found.append((window, area, space))
    if require and not found:
        raise ForgeError(NO_VIEW3D_OPEN)
    return found


def largest_view3d(require=True):
    """The biggest 3D viewport — the one the artist is actually working in."""
    areas = view3d_areas(require=require)
    if not areas:
        return None
    return max(areas, key=lambda entry: (getattr(entry[1], "width", 0)
                                         * getattr(entry[1], "height", 0)))


def _regions_3d(space):
    """The space's main ``region_3d`` plus any quad-view sub-regions.

    A quad split has four independent cameras; changing only the "main" one
    leaves three of the four panes where they were, which reads as the command
    having half-worked.
    """
    regions = []
    main = getattr(space, "region_3d", None)
    if main is not None:
        regions.append(main)
    for quad in getattr(space, "region_quadviews", []) or []:
        if quad is not None and quad not in regions:
            regions.append(quad)
    return regions


def _safe_set(owner, name, value):
    """Set ``owner.name = value`` if this Blender build has that property."""
    if owner is None or not hasattr(owner, name):
        return False
    try:
        setattr(owner, name, value)
    except (AttributeError, TypeError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# set_view
# ---------------------------------------------------------------------------

#: view -> XYZ euler (degrees) of the VIEWER.  Blender's ``view_rotation`` maps
#: view space to world space and the eye looks down its own -Z, so these are the
#: same three angles ``render_preview`` points its camera with — a `front`
#: viewport and a `front` render are the same projection, on purpose.
VIEW_EULER_DEG = {
    "FRONT": (90.0, 0.0, 0.0),
    "BACK": (90.0, 0.0, 180.0),
    "SIDE": (90.0, 0.0, 90.0),
    "RIGHT": (90.0, 0.0, 90.0),
    "LEFT": (90.0, 0.0, -90.0),
    "TOP": (0.0, 0.0, 0.0),
    "BOTTOM": (180.0, 0.0, 0.0),
    "ISO": (90.0 - common.PREVIEW_ISO_ELEVATION_DEG, 0.0, 45.0),
}

#: What the artist typed -> the canonical view name.
VIEW_CHOICES = {name: name for name in VIEW_EULER_DEG}
VIEW_CHOICES["CAMERA"] = "CAMERA"

#: The axis views are flat by default, the way Numpad 1/3/7 are flat: an
#: orthographic front view is the one you can measure against a reference.  The
#: 3/4 orbit stays in perspective, because that is what it is for.
VIEW_DEFAULT_ORTHO = {
    "FRONT": True, "BACK": True, "SIDE": True, "RIGHT": True, "LEFT": True,
    "TOP": True, "BOTTOM": True, "ISO": False,
}

VIEW_LABELS = {
    "FRONT": "front", "BACK": "back", "SIDE": "right side", "RIGHT": "right side",
    "LEFT": "left side", "TOP": "top", "BOTTOM": "bottom",
    "ISO": "3/4 (isometric)", "CAMERA": "camera",
}


def view_quaternion(view):
    """The ``region_3d.view_rotation`` for a named view, or ``None`` for camera."""
    angles = VIEW_EULER_DEG.get(str(view).upper())
    if angles is None:
        return None
    return Euler([math.radians(a) for a in angles], "XYZ").to_quaternion()


def apply_view(region_3d, view, ortho):
    """Point one ``region_3d`` at ``view``. Works on a stub with the two fields.

    Deliberately property assignment rather than ``bpy.ops.view3d.view_axis``:
    no operator context to build, no smooth-view animation to wait on, and the
    whole thing is testable against a plain object with a ``view_rotation`` and
    a ``view_perspective``.
    """
    view = str(view).upper()
    if view == "CAMERA":
        _safe_set(region_3d, "view_perspective", "CAMERA")
        return "CAMERA"
    quat = view_quaternion(view)
    if quat is not None:
        _safe_set(region_3d, "view_rotation", quat)
    perspective = "ORTHO" if ortho else "PERSP"
    _safe_set(region_3d, "view_perspective", perspective)
    return perspective


@command("set_view")
def cmd_set_view(params):
    """Turn the artist's viewport to a named angle.

    - ``view``: ``front`` / ``back`` / ``left`` / ``right`` (``side``) / ``top``
      / ``bottom`` / ``iso`` / ``camera``.
    - ``ortho``: flat (orthographic) or perspective. Defaults to flat for the
      axis views and perspective for ``iso``, which is Blender's own habit.
    """
    view = common.get_choice(params, "view", VIEW_CHOICES)
    ortho = params.get("ortho")
    if ortho is None:
        ortho = VIEW_DEFAULT_ORTHO.get(view, True)
    else:
        ortho = common.get_bool(params, "ortho")

    if view == "CAMERA":
        scene = common.get_scene()
        if getattr(scene, "camera", None) is None:
            raise ForgeError(
                "There is no camera in this scene to look through. Add one "
                "(Add > Camera) or ask for 'front', 'side', 'top' or 'iso' "
                "instead."
            )

    areas = view3d_areas()
    applied = 0
    for _window, _area, space in areas:
        for region in _regions_3d(space):
            apply_view(region, view, ortho)
            applied += 1

    label = VIEW_LABELS.get(view, view.lower())
    if view == "CAMERA":
        changed = "Looking through the camera now."
    else:
        changed = "Turned the view to %s, %s." % (
            label, "flat (orthographic)" if ortho else "perspective")
    return {
        "view": view.lower(),
        "ortho": bool(ortho) if view != "CAMERA" else None,
        "viewports": len(areas),
        "regions": applied,
        "changed": changed,
        "where": WHERE["set_view"],
    }


# ---------------------------------------------------------------------------
# frame_object
# ---------------------------------------------------------------------------

#: Blender's default 50 mm lens on a 36 mm sensor.  Used only to turn a radius
#: into a viewing distance; the real ``space.lens`` is preferred when readable.
DEFAULT_LENS_MM = 50.0
SENSOR_MM = 36.0
FRAME_MARGIN = 1.25


def frame_distance(radius, lens=DEFAULT_LENS_MM, margin=FRAME_MARGIN):
    """How far back the eye has to sit for a sphere of ``radius`` to fit.

    ``view_distance`` drives both the perspective distance and the orthographic
    zoom, so one number frames both. Pure arithmetic, unit-testable.
    """
    radius = max(float(radius), 1e-5)
    lens = float(lens) if lens and lens > 0 else DEFAULT_LENS_MM
    half_fov = math.atan((SENSOR_MM / 2.0) / lens)
    return max(radius / max(math.sin(half_fov), 1e-6) * float(margin), 1e-4)


def apply_framing(region_3d, center, radius, lens=DEFAULT_LENS_MM,
                  margin=FRAME_MARGIN):
    """Centre one ``region_3d`` on ``center`` and pull back far enough."""
    _safe_set(region_3d, "view_location", tuple(center))
    distance = frame_distance(radius, lens, margin)
    _safe_set(region_3d, "view_distance", distance)
    return distance


@command("frame_object")
def cmd_frame_object(params):
    """Zoom the viewport onto one object (or everything visible).

    - ``object``: what to frame; omitted = the active object.
    - ``all``: true to frame every visible mesh instead.
    - ``margin``: breathing room around it, 1.0-4.0 (default 1.25).

    Nothing is selected or deselected: the artist's selection is theirs.
    """
    margin = common.get_float(params, "margin", FRAME_MARGIN,
                              minimum=1.0, maximum=4.0)
    frame_all = common.get_bool(params, "all", False)
    # The viewport check comes before the object is resolved: "there is no
    # viewport" is the truer answer in a headless session than "there is no
    # active object", and the second one would send the caller looking for a
    # problem that is not there.
    areas = view3d_areas()
    if frame_all:
        targets, _defaulted = common._preview_targets({})
    else:
        targets = [common.resolve_object(params)]

    low, high = common._preview_bounds(targets)
    center = tuple((low[i] + high[i]) / 2.0 for i in range(3))
    radius = max(
        math.sqrt(sum(((high[i] - low[i]) / 2.0) ** 2 for i in range(3))),
        1e-4,
    )

    distance = 0.0
    for _window, _area, space in areas:
        lens = getattr(space, "lens", DEFAULT_LENS_MM) or DEFAULT_LENS_MM
        for region in _regions_3d(space):
            distance = apply_framing(region, center, radius, lens, margin)

    names = [obj.name for obj in targets]
    subject = names[0] if len(names) == 1 else "%d objects" % len(names)
    size_mm = [round((high[i] - low[i]) * common.M_TO_MM, 1) for i in range(3)]
    return {
        "objects": names,
        "framed_all_visible": bool(frame_all),
        "center_mm": [round(v * common.M_TO_MM, 1) for v in center],
        "size_mm": size_mm,
        "radius_mm": round(radius * common.M_TO_MM, 1),
        "view_distance": round(distance, 6),
        "viewports": len(areas),
        "changed": "Zoomed the view onto %s (%g x %g x %g mm)."
                   % tuple([subject] + size_mm),
        "where": WHERE["frame_object"],
    }


# ---------------------------------------------------------------------------
# local_view
# ---------------------------------------------------------------------------

def _in_local_view(space):
    return getattr(space, "local_view", None) is not None


@command("local_view")
def cmd_local_view(params):
    """Isolate the selection, so everything else stops getting in the way.

    - ``enable``: true to go in, false to come out, omitted to toggle.
    - ``object``: isolate this one — it is selected and made active first,
      because local view isolates the *selection* and there is no other way to
      say which object the artist meant.
    """
    wanted = params.get("enable")
    if wanted is not None:
        wanted = common.get_bool(params, "enable")

    areas = view3d_areas()
    name = params.get("object")
    subject = None
    if isinstance(name, str) and name.strip():
        subject = common.find_object(name)
        view_layer = common.require_in_view_layer(subject)
        for obj in list(view_layer.objects):
            try:
                obj.select_set(False)
            except (RuntimeError, ReferenceError):
                pass
        try:
            subject.hide_set(False)
            subject.select_set(True)
        except (RuntimeError, ReferenceError):
            pass
        view_layer.objects.active = subject

    changed_areas = 0
    states = []
    for window, area, space in areas:
        already = _in_local_view(space)
        if wanted is not None and already == wanted:
            states.append(already)
            continue
        region = _window_region(area)
        if region is None:
            states.append(already)
            continue
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.localview()
        except (RuntimeError, TypeError, AttributeError) as exc:
            raise ForgeError(
                "Blender would not toggle Local View: %s. It needs something "
                "selected — click the object first, or pass 'object'." % exc
            )
        changed_areas += 1
        states.append(_in_local_view(space))

    now = bool(states and all(states))
    label = subject.name if subject is not None else "the selection"
    changed = ("Isolated %s — everything else is hidden for now." % label
               if now else "Back to the whole scene; nothing is isolated any more.")
    return {
        "enabled": now,
        "object": subject.name if subject is not None else None,
        "viewports": len(areas),
        "viewports_changed": changed_areas,
        "changed": changed,
        "where": WHERE["local_view"],
    }


# ---------------------------------------------------------------------------
# set_shading
# ---------------------------------------------------------------------------

SHADING_CHOICES = {
    "SOLID": "SOLID",
    "WIREFRAME": "WIREFRAME",
    "WIRE": "WIREFRAME",
    "MATERIAL": "MATERIAL",
    "PREVIEW": "MATERIAL",
    "RENDERED": "RENDERED",
    "RENDER": "RENDERED",
}

SHADING_LABELS = {
    "SOLID": "solid grey (fast, shows form)",
    "WIREFRAME": "wireframe (see-through, edges only)",
    "MATERIAL": "material preview (the object's own colours)",
    "RENDERED": "rendered (full lighting; the slowest)",
}


def apply_shading(space, mode):
    """Set one viewport's shading mode. Stub-friendly."""
    shading = getattr(space, "shading", None)
    return _safe_set(shading, "type", mode)


@command("set_shading")
def cmd_set_shading(params):
    """Switch how the viewport draws: solid, wireframe, material or rendered."""
    mode = common.get_choice(params, "mode", SHADING_CHOICES)
    areas = view3d_areas()
    applied = 0
    for _window, _area, space in areas:
        if apply_shading(space, mode):
            applied += 1
    if not applied:
        raise ForgeError(
            "This Blender build would not accept %r as a viewport shading mode."
            % mode.lower()
        )
    return {
        "mode": mode.lower(),
        "viewports": len(areas),
        "changed": "Viewport shading is now %s." % SHADING_LABELS.get(mode, mode.lower()),
        "where": WHERE["set_shading"],
    }


# ---------------------------------------------------------------------------
# set_overlays — the motivating case
# ---------------------------------------------------------------------------

AXIS_NAMES = ("X", "Y", "Z")


def _axis_set(raw):
    """``["x","z"]`` / ``"xy"`` / ``true`` / ``false`` -> ``{"X","Z"}``."""
    if raw is True:
        return set(AXIS_NAMES)
    if raw is False:
        return set()
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"all", "xyz"}:
            return set(AXIS_NAMES)
        if text in {"none", ""}:
            return set()
        raw = [ch for ch in text.replace(",", " ").replace("+", " ").split()] or list(text)
    if not isinstance(raw, (list, tuple, set)):
        raise ForgeError(
            "'axes' must be a list like [\"x\", \"y\", \"z\"], the word \"all\", "
            "or true/false (got %r)." % (raw,)
        )
    chosen = set()
    for entry in raw:
        letter = str(entry).strip().upper().lstrip("+")
        if letter not in AXIS_NAMES:
            raise ForgeError(
                "'axes' entries must be \"x\", \"y\" or \"z\" (got %r)." % (entry,)
            )
        chosen.add(letter)
    return chosen


def apply_overlays(space, options):
    """Apply normalized overlay ``options`` to one viewport. Returns [(key, value)].

    ``options`` keys are the ones the command accepts; ``axes`` is a set of
    ``"X"``/``"Y"``/``"Z"``.  Takes any object exposing ``overlay`` and
    ``shading``, so a stub tests every branch.
    """
    overlay = getattr(space, "overlay", None)
    shading = getattr(space, "shading", None)
    applied = []

    if "overlays" in options:
        value = bool(options["overlays"])
        if _safe_set(overlay, "show_overlays", value):
            applied.append(("overlays", value))

    if "grid" in options:
        value = bool(options["grid"])
        landed = _safe_set(overlay, "show_floor", value)
        # The floor grid and the orthographic grid are two switches for one
        # idea; an artist who asked for "the grid" wants it in both views.
        landed = _safe_set(overlay, "show_ortho_grid", value) or landed
        if landed:
            applied.append(("grid", value))

    if "axes" in options:
        wanted = options["axes"]
        for axis in AXIS_NAMES:
            _safe_set(overlay, "show_axis_%s" % axis.lower(), axis in wanted)
        applied.append(("axes", "".join(sorted(wanted)) or "none"))

    for key, owner, prop in (
        ("wireframe", overlay, "show_wireframes"),
        ("stats", overlay, "show_stats"),
        ("origins", overlay, "show_object_origins"),
        ("cursor", overlay, "show_cursor"),
        ("text", overlay, "show_text"),
        ("face_orientation", overlay, "show_face_orientation"),
        ("xray", shading, "show_xray"),
    ):
        if key in options:
            value = bool(options[key])
            if _safe_set(owner, prop, value):
                applied.append((key, value))
    return applied


#: The overlay switches this command understands, and the plain words used to
#: report them.  ``grid`` + ``axes`` together are the motivating case.
OVERLAY_LABELS = {
    "overlays": "all overlays",
    "grid": "the floor grid",
    "wireframe": "wireframe over the surface",
    "stats": "the statistics readout",
    "origins": "object origins",
    "cursor": "the 3D cursor",
    "text": "the viewport text",
    "face_orientation": "face orientation (blue = outside, red = inside)",
    "xray": "x-ray (see-through)",
}

OVERLAY_BOOL_KEYS = tuple(k for k in OVERLAY_LABELS if k != "axes")


def overlay_summary(applied):
    """``[(key, value)]`` -> the one ``changed`` line the artist reads.

    Its own function because the motivating case deserves its own sentence:
    "grid on plus all three axes" is what was asked for in the bug that started
    Phase 8, and it should not come back as a comma-separated switch dump.
    """
    if not applied:
        return "Nothing changed."
    values = dict(applied)
    axes = values.get("axes")
    if values.get("grid") is True and axes == "XYZ":
        return ("Grid on, and the X, Y and Z axis lines are showing, in every "
                "3D viewport.")
    words = []
    for key, value in applied:
        if key == "axes":
            if value == "none":
                words.append("axis lines off")
            else:
                letters = "/".join(value)
                words.append("the %s axis line%s on"
                             % (letters, "" if len(value) == 1 else "s"))
        else:
            words.append("%s %s" % (OVERLAY_LABELS.get(key, key),
                                    "on" if value else "off"))
    sentence = "; ".join(words)
    # Only the first letter: `.capitalize()` would lower-case the rest and turn
    # "the X axis line on" into "the x axis line on".
    return "%s%s." % (sentence[:1].upper(), sentence[1:])


@command("set_overlays")
def cmd_set_overlays(params):
    """Turn viewport overlays on and off — the grid, the X/Y/Z axis lines, and more.

    This is the command behind "I want to enable grid view for x, y, z axis":
    ``{"grid": true, "axes": ["x", "y", "z"]}`` and it is on, in every open 3D
    viewport, in one call.

    Switches: ``grid``, ``axes`` (a list of ``x``/``y``/``z``, or ``true`` for
    all three, or ``[]`` for none), ``wireframe``, ``stats``, ``overlays`` (the
    master switch), ``origins``, ``cursor``, ``text``, ``face_orientation``,
    ``xray``.  Anything not named is left exactly as the artist had it.
    """
    options = {}
    for key in OVERLAY_BOOL_KEYS:
        if params.get(key) is not None:
            options[key] = common.get_bool(params, key)
    if params.get("axes") is not None:
        options["axes"] = _axis_set(params.get("axes"))

    if not options:
        raise ForgeError(
            "Nothing to change: name at least one overlay. Known switches: %s, "
            "axes." % ", ".join(sorted(OVERLAY_BOOL_KEYS))
        )

    areas = view3d_areas()
    applied = []
    for _window, _area, space in areas:
        applied = apply_overlays(space, options)

    return {
        "applied": {key: (value if not isinstance(value, set) else sorted(value))
                    for key, value in applied},
        "requested": {key: (sorted(value) if isinstance(value, set) else value)
                      for key, value in options.items()},
        "viewports": len(areas),
        "changed": overlay_summary(applied),
        "where": WHERE["set_overlays"],
    }


# ---------------------------------------------------------------------------
# set_mode
# ---------------------------------------------------------------------------

#: What the artist (or the model) may type -> Blender's own mode identifier.
MODE_CHOICES = {
    "OBJECT": "OBJECT",
    "EDIT": "EDIT",
    "SCULPT": "SCULPT",
    "VERTEX_PAINT": "VERTEX_PAINT", "VERTEX PAINT": "VERTEX_PAINT",
    "WEIGHT_PAINT": "WEIGHT_PAINT", "WEIGHT PAINT": "WEIGHT_PAINT",
    "TEXTURE_PAINT": "TEXTURE_PAINT", "TEXTURE PAINT": "TEXTURE_PAINT",
    "PAINT": "TEXTURE_PAINT",
    "POSE": "POSE",
}

#: mode -> the object types that support it.  ``None`` = anything.  This is the
#: validation the contract asks for: "sculpt this armature" is a sentence
#: Blender answers with a modal error box nobody sees, so it is answered here.
MODE_TYPES = {
    "OBJECT": None,
    "EDIT": {"MESH", "CURVE", "CURVES", "SURFACE", "META", "FONT", "ARMATURE",
             "LATTICE", "GPENCIL", "GREASEPENCIL", "POINTCLOUD"},
    "SCULPT": {"MESH"},
    "VERTEX_PAINT": {"MESH"},
    "WEIGHT_PAINT": {"MESH"},
    "TEXTURE_PAINT": {"MESH"},
    "POSE": {"ARMATURE"},
}

MODE_LABELS = {
    "OBJECT": "Object Mode",
    "EDIT": "Edit Mode",
    "SCULPT": "Sculpt Mode",
    "VERTEX_PAINT": "Vertex Paint",
    "WEIGHT_PAINT": "Weight Paint",
    "TEXTURE_PAINT": "Texture Paint",
    "POSE": "Pose Mode",
}


def mode_key(mode):
    """Blender's ``context.mode`` (``"PAINT_VERTEX"``) -> ours (``"VERTEX_PAINT"``)."""
    text = str(mode or "").upper()
    return {
        "EDIT_MESH": "EDIT", "EDIT_CURVE": "EDIT", "EDIT_CURVES": "EDIT",
        "EDIT_SURFACE": "EDIT", "EDIT_METABALL": "EDIT", "EDIT_TEXT": "EDIT",
        "EDIT_ARMATURE": "EDIT", "EDIT_LATTICE": "EDIT",
        "PAINT_VERTEX": "VERTEX_PAINT", "PAINT_WEIGHT": "WEIGHT_PAINT",
        "PAINT_TEXTURE": "TEXTURE_PAINT",
    }.get(text, text)


def _current_mode():
    try:
        return mode_key(bpy.context.mode)
    except (AttributeError, TypeError):
        obj = common.get_active_object()
        return mode_key(getattr(obj, "mode", "OBJECT"))


@command("set_mode")
def cmd_set_mode(params):
    """Switch Blender's interaction mode, checking the object can take it first.

    - ``mode``: ``object`` / ``edit`` / ``sculpt`` / ``vertex_paint`` /
      ``weight_paint`` / ``texture_paint`` / ``pose``.
    - ``object``: which object to put in that mode; omitted = the active one.
      It is selected and made active, because Blender's mode belongs to the
      active object and switching without that is the commonest silent no-op.

    Works headless: mode lives on the object, not on a window.
    """
    mode = common.get_choice(params, "mode", MODE_CHOICES)
    previous = _current_mode()

    if mode == "OBJECT":
        obj = common.get_active_object()
        if obj is None or getattr(obj, "mode", "OBJECT") == "OBJECT":
            # Nothing is in a mode to leave; that is a success, not an error.
            with common.object_mode():
                pass
            return {
                "mode": "object", "blender_mode": "OBJECT",
                "object": obj.name if obj is not None else None,
                "previous": previous.lower(),
                "changed": "Already in Object Mode." if previous == "OBJECT"
                           else "Back in Object Mode.",
                "where": WHERE["set_mode"],
            }
    else:
        obj = common.resolve_object(params)

    allowed = MODE_TYPES.get(mode)
    if allowed is not None and obj.type not in allowed:
        kinds = " or ".join(sorted(allowed)).lower()
        raise ForgeError(
            "%r is %s, and %s only works on %s objects. Pick a %s object, or "
            "name the one you meant with 'object'."
            % (obj.name, _article(obj.type), MODE_LABELS.get(mode, mode),
               kinds, kinds)
        )

    view_layer = common.require_in_view_layer(obj)
    try:
        if getattr(obj, "hide_viewport", False):
            obj.hide_viewport = False
        obj.hide_set(False)
    except (RuntimeError, ReferenceError, AttributeError):
        pass

    # Leaving whatever mode some other object is in first: Blender refuses a
    # direct hop between two objects' modes, and the refusal is a RuntimeError
    # with no useful text.
    active = view_layer.objects.active
    if active is not None and active is not obj \
            and getattr(active, "mode", "OBJECT") != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
    try:
        obj.select_set(True)
    except (RuntimeError, ReferenceError):
        pass
    view_layer.objects.active = obj

    try:
        status = bpy.ops.object.mode_set(mode=mode)
    except RuntimeError as exc:
        raise ForgeError(
            "Blender would not put %r into %s: %s"
            % (obj.name, MODE_LABELS.get(mode, mode), exc)
        )
    if "FINISHED" not in status and getattr(obj, "mode", None) != mode:
        raise ForgeError(
            "Blender refused to switch %r into %s (it returned %s)."
            % (obj.name, MODE_LABELS.get(mode, mode),
               ", ".join(sorted(status)) or "nothing")
        )

    now = _current_mode()
    return {
        "mode": now.lower(),
        "blender_mode": now,
        "object": obj.name,
        "object_type": obj.type,
        "previous": previous.lower(),
        "changed": "%r is in %s now." % (obj.name, MODE_LABELS.get(now, now)),
        "where": WHERE["set_mode"],
    }


def _article(word):
    return ("an %s" if str(word)[:1].upper() in "AEIOU" else "a %s") % str(word).lower()


# ---------------------------------------------------------------------------
# sculpt_brush
# ---------------------------------------------------------------------------

#: Blender's brush size is in screen pixels; 1-5000 is the range the slider
#: itself allows, so anything outside it is a typo worth naming.
BRUSH_SIZE_MIN = 1
BRUSH_SIZE_MAX = 5000

#: No brushes anywhere.  Should be unreachable — Blender ships 62 sculpt brushes
#: in its own essentials library — so this is the "something is very wrong with
#: this install" message rather than an everyday one.
NO_BRUSHES = (
    "No sculpt brushes are available in this Blender at all — not in the file "
    "and not in Blender's own essentials library. Pick a brush by hand from "
    "the toolbar down the left of the viewport (press T to show it)."
)

#: Blender 4.3 turned brushes into ASSETS.  ``bpy.data.brushes`` now holds only
#: the ones this file has actually used (one, in a fresh scene), and
#: ``Paint.brush`` is read-only — the way to switch is
#: ``bpy.ops.brush.asset_activate`` against a weak reference into an asset
#: library.  So validating against ``bpy.data.brushes`` alone would reject
#: "Clay Strips" in every scene where the artist had not already clicked it,
#: which is the opposite of helpful.  The catalog is read straight out of
#: Blender's own shipped library instead, once, and cached.
ESSENTIALS_SCULPT_BLEND = "brushes/essentials_brushes-mesh_sculpt.blend"

_ESSENTIALS_CACHE = None


def essentials_brushes():
    """Blender's own built-in sculpt brushes, by name (read once, cached)."""
    global _ESSENTIALS_CACHE
    if _ESSENTIALS_CACHE is not None:
        return _ESSENTIALS_CACHE
    names = []
    try:
        root = bpy.utils.system_resource("DATAFILES", path="assets")
        path = os.path.join(root, *ESSENTIALS_SCULPT_BLEND.split("/"))
        if os.path.isfile(path):
            with bpy.data.libraries.load(path, assets_only=True) as (source, _into):
                names = [str(name) for name in source.brushes]
    except Exception:  # noqa: BLE001 - an unreadable library is not a failure
        names = []
    _ESSENTIALS_CACHE = sorted(set(names), key=str.lower)
    return _ESSENTIALS_CACHE


def brush_candidates():
    """Every sculpt brush the artist could pick: this file's, plus Blender's own."""
    names = set(essentials_brushes())
    for brush in getattr(bpy.data, "brushes", []) or []:
        try:
            names.add(brush.name)
        except (AttributeError, ReferenceError):
            continue
    return sorted(names, key=str.lower)


def activate_brush(name, paint):
    """Make ``name`` the active sculpt brush, whichever era of Blender this is.

    Three paths, cheapest first: it is already active; the build still lets
    ``Paint.brush`` be assigned (4.2 and earlier); or it is an asset, which is
    every 4.3+ build and needs the activation operator.
    """
    current = getattr(paint, "brush", None)
    if current is not None and getattr(current, "name", None) == name:
        return current

    brush = getattr(bpy.data, "brushes", {}).get(name)
    if brush is not None and _safe_set(paint, "brush", brush):
        return brush

    if name in essentials_brushes():
        reference = {
            "asset_library_type": "ESSENTIALS",
            "asset_library_identifier": "",
            "relative_asset_identifier": "%s/Brush/%s" % (ESSENTIALS_SCULPT_BLEND, name),
        }
    else:
        reference = {
            "asset_library_type": "LOCAL",
            "asset_library_identifier": "",
            "relative_asset_identifier": name,
        }
    try:
        bpy.ops.brush.asset_activate(**reference)
    except (RuntimeError, TypeError, AttributeError) as exc:
        raise ForgeError(
            "Blender would not switch the sculpt brush to %r: %s. Pick it by "
            "hand from the toolbar down the left of the viewport (press T to "
            "show it)." % (name, str(exc).strip().splitlines()[0] if str(exc) else exc)
        )
    current = getattr(paint, "brush", None)
    if current is None or getattr(current, "name", None) != name:
        raise ForgeError(
            "Blender accepted the switch to %r but the active brush is %r. "
            "Pick it by hand from the toolbar (press T)."
            % (name, getattr(current, "name", None))
        )
    return current


def resolve_brush(name, names=None):
    """Find a brush by name, case-insensitively, or say what was meant instead.

    The suggestion follows the ``rigforge_keyframe`` precedent exactly: a
    ``difflib`` close match first, a substring match second, then the list of
    what is actually there.  A model that mistypes ``"Clay Strips"`` as
    ``"clay_strips"`` gets the brush; one that invents ``"Smoothify"`` gets a
    sentence naming ``Smooth``.
    """
    if names is None:
        names = brush_candidates()
    wanted = str(name or "").strip()
    if not wanted:
        raise ForgeError("'brush' must be a brush name, like \"Clay Strips\".")
    if not names:
        raise ForgeError(NO_BRUSHES)

    lowered = {entry.lower(): entry for entry in names}
    if wanted.lower() in lowered:
        return lowered[wanted.lower()]
    # "clay_strips" and "clay strips" are the same brush to a person.
    loose = {entry.lower().replace("_", " ").replace("-", " "): entry
             for entry in names}
    flat = wanted.lower().replace("_", " ").replace("-", " ")
    if flat in loose:
        return loose[flat]

    close = difflib.get_close_matches(wanted, names, n=3, cutoff=0.5)
    if not close:
        close = [entry for entry in names if flat in
                 entry.lower().replace("_", " ").replace("-", " ")][:3]
    hint = (" Did you mean %s?" % ", ".join(repr(entry) for entry in close)
            if close else "")
    shown = names[:30]
    more = ", ... (%d in all)" % len(names) if len(names) > len(shown) else ""
    raise ForgeError(
        "There is no sculpt brush called %r.%s Brushes available: %s%s"
        % (wanted, hint, ", ".join(shown), more)
    )


def _sculpt_settings():
    tool_settings = getattr(bpy.context, "tool_settings", None)
    sculpt = getattr(tool_settings, "sculpt", None)
    if sculpt is None:
        raise ForgeError(
            "This scene has no sculpt tool settings, so there is no brush to "
            "change. Switch to Sculpt Mode once and try again."
        )
    return sculpt


def _dyntopo_state(obj):
    return bool(getattr(obj, "use_dynamic_topology_sculpting", False))


@command("sculpt_brush")
def cmd_sculpt_brush(params):
    """Pick the sculpt brush and set it up — size, strength, symmetry, dyntopo.

    Brush *technique* is the artist's (nobody can drag their stylus for them);
    brush *selection and settings* are ours, and this is that half.

    - ``brush``: the brush name (``"Clay Strips"``, ``"Draw"``, ``"Smooth"``).
      Case- and underscore-insensitive, with a closest-match suggestion on a
      miss. Omit it to leave the brush alone and only change settings.
    - ``size`` (1-5000 screen pixels), ``strength`` (0.0-10.0).
    - ``symmetry_x`` / ``symmetry_y`` / ``symmetry_z``: mirror the strokes.
    - ``dyntopo``: dynamic topology — the mesh grows detail where you sculpt.
    - ``object``, ``enter_mode`` (default true): the object to sculpt on, and
      whether to put it in Sculpt Mode first. Entering Sculpt Mode is what makes
      the brush list exist at all, so leaving it on is almost always right.
    """
    # Everything numeric is validated FIRST, before a mode is entered or a
    # brush is touched. `sculpt_brush(size=0)` has to come back saying the size
    # is out of range; coming back with "there are no brushes" because the
    # mode switch happened to fail first would send the caller after the wrong
    # problem entirely.
    enter_mode = common.get_bool(params, "enter_mode", True)
    size = None
    if params.get("size") is not None:
        size = common.get_int(params, "size", minimum=BRUSH_SIZE_MIN,
                              maximum=BRUSH_SIZE_MAX)
    strength = None
    if params.get("strength") is not None:
        strength = common.get_float(params, "strength", minimum=0.0, maximum=10.0)
    symmetry = {}
    for axis in ("x", "y", "z"):
        key = "symmetry_%s" % axis
        if params.get(key) is not None:
            symmetry[axis] = common.get_bool(params, key)
    dyntopo = None
    if params.get("dyntopo") is not None:
        dyntopo = common.get_bool(params, "dyntopo")
    if params.get("brush") is not None:
        common.get_str(params, "brush")

    obj = None
    mode_result = None
    if enter_mode:
        try:
            obj = common.resolve_object(params, mesh_only=True)
        except ForgeError:
            obj = None
        if obj is not None and _current_mode() != "SCULPT":
            mode_result = cmd_set_mode({"mode": "sculpt", "object": obj.name})
    if obj is None:
        obj = common.get_active_object()

    sculpt = _sculpt_settings()
    changed = []

    wanted_brush = params.get("brush")
    if wanted_brush is not None:
        name = resolve_brush(common.get_str(params, "brush"))
        activate_brush(name, sculpt)
        changed.append("brush is %s" % name)

    brush = getattr(sculpt, "brush", None)
    if brush is None and (size is not None or strength is not None):
        raise ForgeError(NO_BRUSHES)

    if size is not None and _safe_set(brush, "size", size):
        changed.append("size %d px" % size)
    if strength is not None and _safe_set(brush, "strength", strength):
        changed.append("strength %g" % strength)

    for axis, value in symmetry.items():
        if _safe_set(sculpt, "use_symmetry_%s" % axis, value):
            changed.append("%s symmetry %s" % (axis.upper(),
                                               "on" if value else "off"))

    if dyntopo is not None:
        wanted = dyntopo
        if obj is None:
            raise ForgeError(
                "Dynamic topology needs an object in Sculpt Mode; there is no "
                "active object to turn it on for."
            )
        if _dyntopo_state(obj) != wanted:
            try:
                bpy.ops.sculpt.dynamic_topology_toggle()
            except RuntimeError as exc:
                raise ForgeError(
                    "Blender would not toggle dynamic topology: %s. It needs "
                    "the object in Sculpt Mode." % exc
                )
        changed.append("dynamic topology %s" % ("on" if wanted else "off"))

    brush = getattr(sculpt, "brush", None)
    result = {
        "object": obj.name if obj is not None else None,
        "brush": getattr(brush, "name", None),
        "brush_type": getattr(brush, "sculpt_brush_type", None),
        "size": getattr(brush, "size", None),
        "strength": (round(float(brush.strength), 4)
                     if brush is not None and hasattr(brush, "strength") else None),
        "symmetry": {
            "x": bool(getattr(sculpt, "use_symmetry_x", False)),
            "y": bool(getattr(sculpt, "use_symmetry_y", False)),
            "z": bool(getattr(sculpt, "use_symmetry_z", False)),
        },
        "dyntopo": _dyntopo_state(obj) if obj is not None else None,
        "mode": _current_mode().lower(),
        "available": brush_candidates()[:40],
        "changed": ("Sculpt %s." % ", ".join(changed)) if changed
                   else "Nothing to change — the brush is already set that way.",
        "where": WHERE["sculpt_brush"],
    }
    if mode_result is not None:
        result["entered_sculpt_mode"] = True
    return result


# ---------------------------------------------------------------------------
# capture_viewport — buddy mode's first eye
# ---------------------------------------------------------------------------

#: The longer side of the capture, in pixels.  The aspect follows the viewport's
#: own, because the point of this picture is that it is what the artist sees.
CAPTURE_RESOLUTION = 1024
CAPTURE_MIN_RESOLUTION = 128
CAPTURE_MAX_RESOLUTION = 4096


def _capture_size(area, longest):
    width = max(int(getattr(area, "width", 0) or 0), 1)
    height = max(int(getattr(area, "height", 0) or 0), 1)
    scale = float(longest) / float(max(width, height))
    return max(int(round(width * scale)), 4), max(int(round(height * scale)), 4)


@command("capture_viewport")
def cmd_capture_viewport(params):
    """A screenshot of what the ARTIST is looking at, as a PNG you can Read.

    ``render_preview`` renders a clean studio picture of the geometry;  this
    renders *their* viewport — their angle, their shading, their overlays, the
    mask they have painted, the wireframe they left on.  Buddy mode needs both:
    one to judge the form, one to see what they are actually working on.

    - ``path``: absolute ``.png`` to write.
    - ``resolution``: the longer side in pixels, 128-4096 (default 1024). The
      shorter side follows the viewport's aspect, so nothing is stretched.

    Read-only, and everything it borrows from the render settings is put back.
    Live sessions only: there is no viewport to photograph in ``--background``.
    """
    path = common.resolve_path(common.get_str(params, "path"))
    if os.path.isdir(path):
        raise ForgeError(
            "%r is a folder, not a file to write a picture to. Give the whole "
            "filename, ending in .png." % path
        )
    if os.path.splitext(path)[1].lower() != ".png":
        path += ".png"
    path = common.resolve_path(path, make_parents=True)
    longest = common.get_int(params, "resolution", CAPTURE_RESOLUTION,
                             minimum=CAPTURE_MIN_RESOLUTION,
                             maximum=CAPTURE_MAX_RESOLUTION)

    entry = largest_view3d()
    window, area, space = entry
    region = _window_region(area)
    if region is None:
        raise ForgeError(
            "The 3D viewport has no drawable region to capture — try clicking "
            "in it once, then ask again."
        )

    scene = common.get_scene()
    width, height = _capture_size(area, longest)
    restore = []
    try:
        common._preview_snapshot(restore, scene.render, (
            "filepath", "resolution_x", "resolution_y", "resolution_percentage",
            "film_transparent", "use_overwrite", "use_file_extension",
            "use_stamp", "use_border",
        ))
        common._preview_snapshot(restore, scene.render.image_settings,
                                 ("file_format", "color_mode", "color_depth"))
        render = scene.render
        render.filepath = path
        render.resolution_x = width
        render.resolution_y = height
        render.resolution_percentage = 100
        render.film_transparent = False
        render.use_overwrite = True
        render.use_file_extension = True
        render.use_border = False
        try:
            render.use_stamp = False
        except (AttributeError, TypeError):
            pass
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGB"

        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                status = bpy.ops.render.opengl(write_still=True, view_context=True)
        except (RuntimeError, TypeError) as exc:
            raise ForgeError("Blender could not photograph the viewport: %s" % exc)
        if status is not None and "FINISHED" not in status:
            raise ForgeError(
                "The viewport capture returned %s instead of finishing."
                % (", ".join(sorted(status)) or "nothing")
            )
    finally:
        common._preview_restore(restore)

    if not os.path.exists(path):
        raise ForgeError(
            "The capture reported success but nothing was written to %r." % path
        )
    size_bytes = os.path.getsize(path)
    if size_bytes <= 0:
        raise ForgeError("The capture wrote an empty file at %r." % path)

    shading = getattr(getattr(space, "shading", None), "type", None)
    region_3d = getattr(space, "region_3d", None)
    return {
        "path": path,
        "resolution": [width, height],
        "size_bytes": size_bytes,
        "area": {"width": int(getattr(area, "width", 0) or 0),
                 "height": int(getattr(area, "height", 0) or 0)},
        "shading": str(shading).lower() if shading else None,
        "perspective": str(getattr(region_3d, "view_perspective", "")).lower() or None,
        "mode": _current_mode().lower(),
        "viewports": len(view3d_areas(require=False)),
        "note": "This is the artist's own view — their angle, shading and overlays.",
    }
