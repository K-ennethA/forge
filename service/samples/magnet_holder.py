"""Magnet holder bar -- the reference for a script written from ``forge_lib``.

A flat bar with a row of blind pockets for disc magnets, rounded ends, and an
optional countersunk screw hole at each end so it can be mounted to a wall.
Stick it under a shelf and steel tools hang off it; screw it to a door frame and
it holds the door shut.

This script exists to be **imitated**.  Every printability decision in it is a
call into ``forge_lib`` rather than arithmetic written out here, and every one
of the four print-readiness checks passes at the declared defaults *and* at both
ends of every declared range:

* ``bed_fit``    -- the ranges are chosen so the longest possible bar still fits
                    the plate with its margin; nothing to segment.
* ``min_wall``   -- ``wall`` is the one number that sets every thickness in the
                    part, and it is clamped up to the printer's minimum.
* ``overhangs``  -- *passes*, it does not merely warn.  The bar is flat on the
                    bed and every face is vertical or upward: pockets open
                    **up**, the screw hole is a straight bore, and its
                    countersink is a cut that widens **upward**, which is the
                    safe direction for a hole.
* ``watertight`` -- primitives and clean booleans, no coplanar faces (every
                    cutter overshoots the face it enters).

The three rules worth stealing
------------------------------
1. Ask ``forge_lib`` for the number, then build with the number it gives back.
   ``magnet_pocket_plan`` knows the pocket is the magnet plus twice
   ``magnet_pocket_extra``; this script never writes ``+ 0.05`` anywhere.
2. Derive the part's size *from* the clamped feature sizes, not the other way
   round.  ``bar_thickness`` is a request; the thickness actually built is
   ``max(request, pocket depth + floor)``, so no slider combination can put a
   pocket through the back of the bar.
3. Blind pockets open **upward**.  The same pocket bored from the underside is
   a flat ceiling -- a 90 degree overhang -- and no helper can save a part from
   being placed upside down.
"""

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - provided by the geometry service

PARAMS = {
    "magnet_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 3.0,
        "max": 20.0,
        "step": 0.5,
        "description": "Diameter of the disc magnets, as sold (the pocket is bigger)",
    },
    "magnet_thickness": {
        "value": 3.0,
        "unit": "mm",
        "min": 1.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Thickness of the disc magnets, as sold",
    },
    "magnet_count": {
        "value": 3,
        "unit": "count",
        "min": 1,
        "max": 6,
        "step": 1,
        "description": "How many magnets sit in the bar",
    },
    "magnet_spacing": {
        "value": 18.0,
        "unit": "mm",
        "min": 8.0,
        "max": 30.0,
        "step": 1.0,
        "description": "Centre-to-centre spacing; widened if the pockets would touch",
    },
    "wall": {
        "value": 2.4,
        "unit": "mm",
        "min": 1.2,
        "max": 6.0,
        "step": 0.2,
        "description": "Material around every pocket and hole; sets the bar's width",
    },
    "bar_thickness": {
        "value": 6.0,
        "unit": "mm",
        "min": 3.0,
        "max": 25.0,
        "step": 0.5,
        "description": "How thick the bar is; raised if a magnet would not fit",
    },
    "mount_holes": {
        "value": True,
        "unit": "bool",
        "description": "Add a countersunk screw hole at each end",
    },
    "mount_hole_diameter": {
        "value": 4.2,
        "unit": "mm",
        "min": 2.0,
        "max": 8.0,
        "step": 0.2,
        "description": "Clearance diameter of the mounting screws",
    },
    "min_land_mm": {
        "value": 1.0,
        "unit": "mm",
        "min": 1.0,
        "max": 5.0,
        "step": 0.1,
        "description": (
            "Narrowest flat this part is allowed to end on; can only be raised "
            "above the printer's printability floor"
        ),
    },
}

#: How far the screw-hole cutter pokes out past the top face, so the boolean is
#: never a coplanar-face cut and the countersink's rim lands exactly on Z=top.
_CUTTER_OVERSHOOT_MM = 0.5

#: A countersink shallower than this is not worth a cut.
_MIN_COUNTERSINK_MM = 0.3


def build(p):
    """Return the magnet holder bar as a Build123d ``Part``, sitting on Z = 0."""
    # ---- ask the library for the printable numbers -----------------------
    # The default Centauri Carbon profile; pass a printer dict here to build
    # for a different machine and every clamp below follows it.
    land = forge_lib.min_land(p["min_land_mm"])
    wall = max(p["wall"], forge_lib.min_wall())

    pocket = forge_lib.magnet_pocket_plan(p["magnet_diameter"], p["magnet_thickness"])
    pocket_d = pocket["pocket_diameter_mm"]
    pocket_depth = pocket["pocket_depth_mm"]

    # ---- size the bar from the features, never the other way round -------
    count = int(p["magnet_count"])
    mount = bool(p["mount_holes"])
    mount_d = p["mount_hole_diameter"] if mount else 0.0

    # A floor under every magnet, and a bar thick enough to carry it.
    floor = max(wall, land)
    thickness = max(p["bar_thickness"], pocket_depth + floor)

    # Pockets that would run into each other are pushed apart, not merged.
    spacing = max(p["magnet_spacing"], pocket_d + wall)

    width = max(pocket_d, mount_d) + 2.0 * wall
    end_pad = pocket_d / 2.0 + wall
    tab = (mount_d + 2.0 * wall) if mount else 0.0
    length = (count - 1) * spacing + 2.0 * end_pad + 2.0 * tab
    # Keep a straight middle section between the two round ends.
    length = max(length, width + land)

    # ---- the body: a stadium, so the ends are round and still vertical ---
    bar = Pos(0, 0, thickness / 2.0) * Box(  # noqa: F405
        length - width, width, thickness
    )
    for side in (-1.0, 1.0):
        bar += Pos(  # noqa: F405
            side * (length - width) / 2.0, 0.0, thickness / 2.0
        ) * Cylinder(radius=width / 2.0, height=thickness)  # noqa: F405

    # ---- the magnet pockets, bored down from the top face ----------------
    # ``available_depth`` is what makes this a guarantee rather than a hope:
    # the helper raises if the floor under a magnet would be under minimum wall.
    for index in range(count):
        x = (index - (count - 1) / 2.0) * spacing
        bar -= Pos(x, 0.0, thickness) * forge_lib.magnet_pocket(  # noqa: F405
            p["magnet_diameter"],
            p["magnet_thickness"],
            available_depth=thickness,
        )

    # ---- the mounting holes ---------------------------------------------
    if mount:
        mount_r = mount_d / 2.0
        # Leave at least a land of top face outside the countersink's rim.
        countersink = min(0.5 * mount_r, max(0.0, wall - land - 0.2))
        hole_x = length / 2.0 - mount_r - wall

        for side in (-1.0, 1.0):
            if countersink >= _MIN_COUNTERSINK_MM:
                # One cutter does both jobs: its straight land is the through
                # bore, and the taper above it is the countersink.  ``role="cut"``
                # is what tells the helper that a hole widening UPWARD is the
                # safe direction -- the opposite of a solid taper.
                taper_h = countersink + _CUTTER_OVERSHOOT_MM
                bar -= Pos(side * hole_x, 0.0, -1.0) * forge_lib.blunted_taper(  # noqa: F405
                    mount_r,
                    mount_r + taper_h,
                    thickness + 1.0 + _CUTTER_OVERSHOOT_MM,
                    role="cut",
                    taper_height=taper_h,
                    min_land_mm=p["min_land_mm"],
                )
            else:
                bar -= Pos(  # noqa: F405
                    side * hole_x, 0.0, thickness / 2.0
                ) * Cylinder(radius=mount_r, height=thickness + 2.0)  # noqa: F405

    return bar
