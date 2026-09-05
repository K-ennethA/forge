"""Bowl-holder base ring -- PartForge part 1 of 2.

A footed collar that a 6 in stainless mixing bowl drops into.  The bowl is
lowered through the top bore and its rim lands on an internal seat lip; below
the seat the bore opens back up so the bowl's body hangs free.  The lower
outside carries a fluted decorative band, the base is an arcaded plinth (the
"feet" are the piers between the arches), and two lugs on the X axis carry
blind sockets for the slot-in ears, which are a separate appendage part.

Everything is modelled in millimetres with the base of the feet on Z = 0, so
the part lands on the print bed in its print orientation.

Design notes worth knowing before turning knobs
-----------------------------------------------
* ``total_height`` is the whole part, feet included.  ``foot_height`` and
  ``lip_height`` are clamped to a fraction of it, so no combination of the
  declared ranges can collapse the profile.
* The flutes are *subtracted* cylinders, never added ribs: the cutter's closest
  approach to the axis is ``r_out - flute_depth`` and the effective depth is
  capped at 40 % of the wall, so a flute can never break into the bore no
  matter how the sliders are set.
* The flute cutter radius is solved from the circumferential pitch so the
  scallops always leave a flat land between them; at very high ``flute_count``
  on a small ring the depth is reduced rather than letting the cuts merge.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

PARAMS = {
    "bowl_diameter": {
        "value": 6.0,
        "unit": "in",
        "min": 3.0,
        "max": 7.0,
        "step": 0.25,
        "description": "Rim diameter of the bowl the ring wraps",
    },
    "fit_clearance": {
        "value": 0.8,
        "unit": "mm",
        "min": 0.0,
        "max": 5.0,
        "step": 0.1,
        "description": "Radial gap between the bowl rim and the bore",
    },
    "wall_thickness": {
        "value": 5.0,
        "unit": "mm",
        "min": 2.0,
        "max": 12.0,
        "step": 0.5,
        "description": "Radial thickness of the ring wall",
    },
    "total_height": {
        "value": 62.0,
        "unit": "mm",
        "min": 40.0,
        "max": 150.0,
        "step": 1.0,
        "description": "Overall height, bottom of the feet to the top rim",
    },
    "lip_width": {
        "value": 6.5,
        "unit": "mm",
        "min": 2.0,
        "max": 20.0,
        "step": 0.5,
        "description": "How far the seat lip reaches in under the bowl rim",
    },
    "lip_height": {
        "value": 8.0,
        "unit": "mm",
        "min": 2.0,
        "max": 25.0,
        "step": 0.5,
        "description": "Height of the seat lip; its underside is the print-safe taper",
    },
    "flute_count": {
        "value": 24,
        "unit": "count",
        "min": 6,
        "max": 60,
        "step": 1,
        "description": "Number of scalloped flutes in the decorative band",
    },
    "flute_depth": {
        "value": 1.6,
        "unit": "mm",
        "min": 0.3,
        "max": 4.0,
        "step": 0.1,
        "description": "Depth of each flute, capped at 40% of the wall",
    },
    "feet_count": {
        "value": 4,
        "unit": "count",
        "min": 3,
        "max": 8,
        "step": 1,
        "description": "Number of feet (piers) around the arcaded base",
    },
    "foot_height": {
        "value": 12.0,
        "unit": "mm",
        "min": 4.0,
        "max": 30.0,
        "step": 0.5,
        "description": "Height of the arcaded base zone",
    },
    "socket_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 3.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Diameter of the slot-in ear sockets in the top rim lugs",
    },
    "socket_depth": {
        "value": 10.0,
        "unit": "mm",
        "min": 3.0,
        "max": 25.0,
        "step": 0.5,
        "description": "Depth of the ear sockets below the top rim",
    },
}

#: Angles (degrees) where the ear lugs sit.  Two ears, on the X axis.
EAR_ANGLES_DEG = (0.0, 180.0)

#: Fraction of the circumferential pitch a flute is allowed to occupy.  The
#: remainder is the flat land between flutes; at 0.78 the land is ~22 % of the
#: pitch, wide enough to survive a 0.4 mm nozzle at every declared count.
_FLUTE_PITCH_FRACTION = 0.78

#: A flute shallower than this is below one layer of relief; skip it.
_MIN_FLUTE_DEPTH_MM = 0.15

#: A band shorter than this cannot hold a readable flute; leave it plain.
_MIN_BAND_HEIGHT_MM = 3.0

#: Wall left between the ear socket and the outside of its lug.
_SOCKET_LUG_WALL_MM = 2.5


def _seat_profile_points(r_in, r_out, height, z_lip, lip_h, r_lip, flare, flare_h):
    """The revolved cross-section, as (radius, z) pairs, counter-clockwise.

    Bottom outward, up the flared plinth and the outer wall, in across the top
    rim, down the upper bore, in along the seat's top face, back out down the
    seat's tapered underside, then down the lower bore to the start.
    """
    return [
        (r_in, 0.0),
        (r_out + flare, 0.0),
        (r_out, flare_h),
        (r_out, height),
        (r_in, height),
        (r_in, z_lip + lip_h),
        (r_lip, z_lip + lip_h),
        (r_in, z_lip),
    ]


def _flute_cutters(r_out, count, depth, wall, z_bottom, z_top):
    """Vertical scallop cutters for the decorative band.

    Each cutter is a cylinder tangent to a circle of radius ``r_out - depth``,
    so the deepest point of every flute is exactly ``depth`` below the outer
    surface.  The cutter radius is solved from the wanted surface width::

        width = 2 * sqrt(radius**2 - (radius - depth)**2)

    which inverts to ``radius = (width**2 / 4 + depth**2) / (2 * depth)``.
    """
    pitch = 2.0 * math.pi * r_out / count
    width = _FLUTE_PITCH_FRACTION * pitch

    # A circle can only reach depth d over a width of 2d, so a very fine pitch
    # forces a shallower flute rather than a cutter smaller than its own bite.
    depth = min(depth, wall * 0.40, 0.45 * width)
    if depth < _MIN_FLUTE_DEPTH_MM:
        return []

    cutter_radius = (width * width / 4.0 + depth * depth) / (2.0 * depth)
    centre_radius = r_out + cutter_radius - depth
    band_height = z_top - z_bottom
    z_mid = (z_bottom + z_top) / 2.0

    cutters = []
    for index in range(count):
        angle = 2.0 * math.pi * index / count
        cutters.append(
            Pos(  # noqa: F405
                centre_radius * math.cos(angle),
                centre_radius * math.sin(angle),
                z_mid,
            )
            * Cylinder(radius=cutter_radius, height=band_height)  # noqa: F405
        )
    return cutters


def _arch_cutters(r_outer_max, count, foot_height):
    """Round-topped arch openings that leave ``count`` piers in the plinth.

    Each cutter is a box capped with a horizontal cylinder -- an arch section --
    swept radially outward from the axis, so the opening reads the same on the
    inside and the outside of the base.  The rounded top is what makes the
    arcade printable without support.
    """
    arch_top = foot_height * 0.82
    if arch_top <= 1.5:
        return []

    pitch = 2.0 * math.pi * r_outer_max / count
    width = min(0.60 * pitch, 2.0 * (arch_top - 1.0))
    if width < 1.5:
        return []

    reach = r_outer_max + 10.0
    box_top = arch_top - width / 2.0
    box_bottom = -2.0
    box_height = box_top - box_bottom

    cutters = []
    for index in range(count):
        # Half a pitch of offset puts a pier under each ear lug.
        angle = 360.0 * index / count + 180.0 / count
        arch = (
            Pos(0.0, reach / 2.0, (box_bottom + box_top) / 2.0)  # noqa: F405
            * Box(width, reach, box_height)  # noqa: F405
        ) + (
            Pos(0.0, reach / 2.0, box_top)  # noqa: F405
            * Rot(90.0, 0.0, 0.0)  # noqa: F405
            * Cylinder(radius=width / 2.0, height=reach)  # noqa: F405
        )
        cutters.append(Rot(0.0, 0.0, angle) * arch)  # noqa: F405
    return cutters


def build(p):
    """Return the bowl-holder base ring as a Build123d ``Part``."""
    bowl_radius = p["bowl_diameter"] / 2.0
    wall = p["wall_thickness"]
    height = p["total_height"]

    r_in = bowl_radius + p["fit_clearance"]
    r_out = r_in + wall

    # --- proportions, clamped so every declared combination stays buildable --
    foot_h = min(p["foot_height"], height * 0.35)
    lip_h = min(p["lip_height"], height * 0.25)
    lip_w = min(p["lip_width"], r_in * 0.6)
    r_lip = r_in - lip_w

    # The plain wall above the seat that captures the bowl rim.
    upper_wall = min(max(height * 0.16, 4.0), 22.0, height * 0.25)
    z_lip = height - upper_wall - lip_h

    if z_lip <= foot_h + 2.0:
        # Unreachable with the declared ranges (z_lip >= 0.5 * height while
        # foot_h <= 0.35 * height), but a collapsed profile must never reach
        # the kernel as a self-intersecting polygon.
        raise ValueError(
            f"no room for the decorative band: the seat lip lands at "
            f"{z_lip:.1f} mm but the arcaded base already reaches "
            f"{foot_h:.1f} mm; raise total_height or lower foot_height"
        )

    flare = min(2.5, wall * 0.5)
    flare_h = min(4.0, foot_h * 0.4)

    # --- the body of revolution ------------------------------------------
    profile = Plane.XZ * Polygon(  # noqa: F405
        *_seat_profile_points(
            r_in, r_out, height, z_lip, lip_h, r_lip, flare, flare_h
        ),
        align=None,
    )
    part = revolve(profile, axis=Axis.Z)  # noqa: F405

    # --- subtract the arcade, then the fluted band ------------------------
    for cutter in _arch_cutters(r_out + flare, int(p["feet_count"]), foot_h):
        part -= cutter

    band_bottom = foot_h + 2.0
    band_height = min(z_lip - 3.0 - band_bottom, height * 0.5)
    if band_height >= _MIN_BAND_HEIGHT_MM:
        for cutter in _flute_cutters(
            r_out,
            int(p["flute_count"]),
            p["flute_depth"],
            wall,
            band_bottom,
            band_bottom + band_height,
        ):
            part -= cutter

    # --- ear lugs, then the sockets bored into them -----------------------
    socket_d = p["socket_diameter"]
    lug_radius = socket_d / 2.0 + _SOCKET_LUG_WALL_MM
    lug_height = min(p["socket_depth"] + 8.0, height * 0.45)
    lug_z_bottom = height - lug_height
    # Bury the lug 60 % of the wall deep so the union always has volume to bite
    # on, while its inner face still stops short of the bore.
    lug_centre_radius = r_out + lug_radius - 0.6 * wall

    socket_depth = min(p["socket_depth"], lug_height - 4.0, height * 0.40)

    for angle_deg in EAR_ANGLES_DEG:
        angle = math.radians(angle_deg)
        x = lug_centre_radius * math.cos(angle)
        y = lug_centre_radius * math.sin(angle)
        part += Pos(x, y, lug_z_bottom + lug_height / 2.0) * Cylinder(  # noqa: F405
            radius=lug_radius, height=lug_height
        )
        # A domed foot on the lug: the underside is a sphere rather than a flat
        # ledge, which is what keeps it printable where it leaves the wall.
        part += Pos(x, y, lug_z_bottom) * Sphere(radius=lug_radius)  # noqa: F405

    if socket_depth > 0.5:
        for angle_deg in EAR_ANGLES_DEG:
            angle = math.radians(angle_deg)
            x = lug_centre_radius * math.cos(angle)
            y = lug_centre_radius * math.sin(angle)
            # Overshoot the top face by 1 mm so the mouth of the socket is a
            # clean through-cut rather than a coplanar-face boolean.
            part -= Pos(  # noqa: F405
                x, y, height - socket_depth / 2.0 + 0.5
            ) * Cylinder(radius=socket_d / 2.0, height=socket_depth + 1.0)  # noqa: F405

    return part
