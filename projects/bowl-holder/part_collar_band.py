"""Bowl-holder collar band -- PartForge part 2 of 2.

A separate decorative sleeve that slides down over the base ring's outer wall
and sits wherever you want it on the plain upper section.  It carries the same
flute family as the ring's band -- subtracted scallops between two plain rims
-- so the two parts read as one object even though they print separately.

The bore is the base ring's outer diameter plus a ``slide_fit`` clearance
(``templates/printer.json`` calls 0.2 mm a slide fit on the Centauri Carbon),
so changing the ring's ``bowl_diameter``/``fit_clearance``/``wall_thickness``
means updating ``ring_outer_diameter`` here to
``bowl_diameter + 2 * (fit_clearance + wall_thickness)``.

Modelled in millimetres, sitting on Z = 0 -- the flat rim is the print face.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

PARAMS = {
    "ring_outer_diameter": {
        "value": 164.0,
        "unit": "mm",
        "min": 40.0,
        "max": 220.0,
        "step": 0.5,
        "description": "Outer diameter of the base ring this collar wraps",
    },
    "slide_fit": {
        "value": 0.2,
        "unit": "mm",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "description": "Radial clearance on the bore so the collar slides on",
    },
    "wall_thickness": {
        "value": 4.0,
        "unit": "mm",
        "min": 1.5,
        "max": 12.0,
        "step": 0.5,
        "description": "Radial thickness of the collar wall",
    },
    "band_height": {
        "value": 20.0,
        "unit": "mm",
        "min": 6.0,
        "max": 60.0,
        "step": 0.5,
        "description": "Height of the collar along Z",
    },
    "rim_height": {
        "value": 3.0,
        "unit": "mm",
        "min": 1.0,
        "max": 15.0,
        "step": 0.5,
        "description": "Plain rim above and below the flutes",
    },
    "flute_count": {
        "value": 24,
        "unit": "count",
        "min": 6,
        "max": 60,
        "step": 1,
        "description": "Number of scalloped flutes around the collar",
    },
    "flute_depth": {
        "value": 1.6,
        "unit": "mm",
        "min": 0.3,
        "max": 4.0,
        "step": 0.1,
        "description": "Depth of each flute, capped at 40% of the wall",
    },
    "edge_chamfer": {
        "value": 0.8,
        "unit": "mm",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "description": "Chamfer breaking the four rim circles; 0 leaves them sharp",
    },
}

#: Fraction of the circumferential pitch a flute occupies; the rest is the
#: flat land between flutes.  Matches part_base_ring.py so the two bands
#: read as the same texture family.
_FLUTE_PITCH_FRACTION = 0.78

#: A flute shallower than this is below one layer of relief; skip it.
_MIN_FLUTE_DEPTH_MM = 0.15

#: Shortest flute zone worth cutting.
_MIN_FLUTE_ZONE_MM = 1.5

#: Below this a chamfer is smaller than any nozzle can print, so skip it.
_MIN_USEFUL_CHAMFER_MM = 0.05

#: Rims may not eat more than this fraction of the height each.
_MAX_RIM_FRACTION = 0.35


def _flute_cutters(r_out, count, depth, wall, z_bottom, z_top):
    """Vertical scallop cutters -- identical solve to the base ring's band.

    Each cutter is a cylinder tangent to a circle of radius ``r_out - depth``,
    so the deepest point of every flute is exactly ``depth`` below the outer
    surface.  The cutter radius is solved from the wanted surface width::

        width = 2 * sqrt(radius**2 - (radius - depth)**2)

    which inverts to ``radius = (width**2 / 4 + depth**2) / (2 * depth)``.
    Capping the depth at 40 % of the wall is what guarantees a flute can never
    break through into the bore, at any count or depth in range.
    """
    pitch = 2.0 * math.pi * r_out / count
    width = _FLUTE_PITCH_FRACTION * pitch

    depth = min(depth, wall * 0.40, 0.45 * width)
    if depth < _MIN_FLUTE_DEPTH_MM:
        return []

    cutter_radius = (width * width / 4.0 + depth * depth) / (2.0 * depth)
    centre_radius = r_out + cutter_radius - depth
    zone_height = z_top - z_bottom
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
            * Cylinder(radius=cutter_radius, height=zone_height)  # noqa: F405
        )
    return cutters


def build(p):
    """Return the collar band as a Build123d ``Part``."""
    wall = p["wall_thickness"]
    height = p["band_height"]

    r_in = p["ring_outer_diameter"] / 2.0 + p["slide_fit"]
    r_out = r_in + wall

    if r_in <= 0.0:
        raise ValueError("ring_outer_diameter must be positive")

    # --- the plain tube ---------------------------------------------------
    part = Pos(0.0, 0.0, height / 2.0) * (  # noqa: F405
        Cylinder(radius=r_out, height=height)  # noqa: F405
        - Cylinder(radius=r_in, height=height + 2.0)  # noqa: F405
    )

    # --- the fluted zone between the two plain rims ------------------------
    rim = min(p["rim_height"], height * _MAX_RIM_FRACTION)
    zone_bottom = rim
    zone_top = height - rim
    if zone_top - zone_bottom >= _MIN_FLUTE_ZONE_MM:
        for cutter in _flute_cutters(
            r_out,
            int(p["flute_count"]),
            p["flute_depth"],
            wall,
            zone_bottom,
            zone_top,
        ):
            part -= cutter

    # --- break the four rim circles ---------------------------------------
    # A chamfer cannot eat more than half the wall (inner and outer chamfers
    # would meet) or half a rim (it would run into the flutes).
    chamfer_size = min(p["edge_chamfer"], wall * 0.45, rim * 0.45)
    if chamfer_size >= _MIN_USEFUL_CHAMFER_MM:
        tolerance = 1e-6
        rims = [
            edge
            for edge in part.edges().filter_by(GeomType.CIRCLE)  # noqa: F405
            if abs(edge.center().Z) < tolerance
            or abs(edge.center().Z - height) < tolerance
        ]
        if rims:
            part = chamfer(rims, length=chamfer_size)  # noqa: F405

    return part
