"""A first-person playtest camera (the "POV character").

The artist's ask, near-verbatim: *"add a pov character that acts as camera
where we can switch views easily to get a better perspective ... like a
preliminary test to exporting to godot or something so we can see how it
feels and will play like."*

One socket command, ``playtest_pov``, which stands a simple human-height
body in the level, hangs a camera at its eyes, flips the artist's viewport
into it, and (by default) configures Blender's built-in Walk Navigation to
feel like a game: WASD + mouse, gravity on, eyes at 1.6 m, walking pace.
``Shift + `` (backtick) then walks; Esc exits; Numpad 0 toggles the camera.

This is a *feel* test, said plainly in every report: no collision beyond
walk-mode's own, no game logic, nothing exported — the .glb exit is the
real handoff, and this is the two-second check that the doorways read right
before bothering with it.

The body and camera are ordinary objects (``POV Body`` / ``POV Camera``),
deliberately NOT ``FP:``-prefixed: they are the artist's rig, not plan
geometry — a floor-plan rebuild never touches them and never reports them.

Units: millimetres on the wire, metres at the bpy boundary, as everywhere.
``playtest_pov`` changes the scene (objects, scene camera), so it is not
read-only and one Ctrl+Z takes it back.
"""

import math
import time

import bmesh
import bpy

from .common import MM_TO_M
from .registry import ForgeError, command

BODY_NAME = "POV Body"
CAMERA_NAME = "POV Camera"

#: Eye height and body proportions for the default human stand-in.
DEFAULT_HEIGHT_MM = 1600.0
BODY_HEIGHT_MM = 1700.0
BODY_RADIUS_MM = 220.0

#: Indoor playtest feel for Blender's walk navigation.
WALK_SPEED_M_S = 2.2
WALK_JUMP_M = 0.4


def _num(value, label, default=None):
    if value is None:
        if default is not None:
            return float(default)
        raise ForgeError("%s is required." % label)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ForgeError("%s must be a number, got %r." % (label, value))


def _ensure_body(x_m, y_m):
    body = bpy.data.objects.get(BODY_NAME)
    if body is None or body.type != "MESH":
        mesh = bpy.data.meshes.new(BODY_NAME)
        bm = bmesh.new()
        bmesh.ops.create_cone(
            bm, cap_ends=True, segments=12,
            radius1=BODY_RADIUS_MM * MM_TO_M,
            radius2=BODY_RADIUS_MM * 0.72 * MM_TO_M,
            depth=BODY_HEIGHT_MM * MM_TO_M)
        bm.to_mesh(mesh)
        bm.free()
        body = bpy.data.objects.new(BODY_NAME, mesh)
        bpy.context.scene.collection.objects.link(body)
    body.location = (x_m, y_m, BODY_HEIGHT_MM * MM_TO_M / 2.0)
    return body


def _ensure_camera(x_m, y_m, z_m, yaw_rad):
    cam_data = bpy.data.cameras.get(CAMERA_NAME)
    if cam_data is None:
        cam_data = bpy.data.cameras.new(CAMERA_NAME)
    cam_data.lens = 24.0  # wide, the indoor feel of a game FOV
    cam = bpy.data.objects.get(CAMERA_NAME)
    if cam is None or cam.type != "CAMERA":
        cam = bpy.data.objects.new(CAMERA_NAME, cam_data)
        bpy.context.scene.collection.objects.link(cam)
    cam.data = cam_data
    cam.location = (x_m, y_m, z_m)
    # (90, 0, 0) looks north (+y); yaw spins left-handed about Z, so 90 is
    # west, 180 south, 270 east — compass-ish and documented in the README.
    cam.rotation_euler = (math.pi / 2.0, 0.0, yaw_rad)
    return cam


def _viewports():
    manager = getattr(bpy.context, "window_manager", None)
    if manager is None:
        return []
    spaces = []
    for window in manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        spaces.append(space)
    return spaces


def _set_view(perspective):
    switched = 0
    for space in _viewports():
        try:
            space.region_3d.view_perspective = perspective
            switched += 1
        except AttributeError:
            continue
    return switched


@command("playtest_pov")
def cmd_playtest_pov(params):
    """Stand a POV character in the level and look through its eyes.

    ``{"position_mm"?: [x, y], "facing_deg"?: 0 (north; 90 west, 180 south,
    270 east), "height_mm"?: 1600, "walk"?: true, "exit"?: false}``

    ``exit: true`` ignores everything else and hands the viewport back to
    the artist's own perspective (the rig stays for next time).
    """
    started = time.perf_counter()

    if bool(params.get("exit", False)):
        restored = _set_view("PERSP")
        return {
            "exited": True,
            "viewports_restored": restored,
            "note": ("Back to your own view. The POV rig is still standing "
                     "where it was; Numpad 0 returns to it."),
            "seconds": round(time.perf_counter() - started, 3),
        }

    position = params.get("position_mm")
    if position is None:
        cursor = bpy.context.scene.cursor.location
        x_mm, y_mm = cursor.x / MM_TO_M, cursor.y / MM_TO_M
        placed_by = "3d_cursor"
    else:
        if (not isinstance(position, (list, tuple))) or len(position) < 2:
            raise ForgeError(
                "'position_mm' must be [x, y] in millimetres — where the "
                "character stands on the floor.")
        x_mm = _num(position[0], "position_mm[0]")
        y_mm = _num(position[1], "position_mm[1]")
        placed_by = "position_mm"

    height_mm = _num(params.get("height_mm"), "height_mm", DEFAULT_HEIGHT_MM)
    if not 200.0 <= height_mm <= 3000.0:
        raise ForgeError(
            "height_mm %g is nobody's eye level — give something between "
            "200 and 3000." % height_mm)
    facing = math.radians(_num(params.get("facing_deg"), "facing_deg", 0.0))

    body = _ensure_body(x_mm * MM_TO_M, y_mm * MM_TO_M)
    cam = _ensure_camera(x_mm * MM_TO_M, y_mm * MM_TO_M,
                         height_mm * MM_TO_M, facing)
    bpy.context.scene.camera = cam

    walk = bool(params.get("walk", True))
    if walk:
        prefs = bpy.context.preferences.inputs.walk_navigation
        prefs.view_height = height_mm * MM_TO_M
        prefs.walk_speed = WALK_SPEED_M_S
        prefs.use_gravity = True
        prefs.jump_height = WALK_JUMP_M
    switched = _set_view("CAMERA")

    return {
        "body": body.name,
        "camera": cam.name,
        "position_mm": [round(x_mm, 1), round(y_mm, 1)],
        "placed_by": placed_by,
        "eye_height_mm": round(height_mm, 1),
        "facing_deg": round(math.degrees(facing), 1) % 360.0,
        "walk_configured": walk,
        "viewports_switched": switched,
        "controls": ("Shift+` starts Walk Navigation: WASD moves, mouse "
                     "looks, G toggles gravity, Esc exits. Numpad 0 leaves "
                     "the camera; playtest_pov {\"exit\": true} also works."),
        "honesty": ("A feel test through a stand-in's eyes — no collision "
                    "beyond walk-mode's own, no game logic, nothing "
                    "exported. The .glb exit is the real handoff."),
        "seconds": round(time.perf_counter() - started, 3),
    }
