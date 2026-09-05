"""Dog-bowl holder -- a raised collar that wraps all the way around the bowl.

The bowl is lowered through the top bore and its rim lands on an internal seat
lip near the top; below the seat the bore opens back out so the bowl's body
hangs free inside the ring.  The result wraps the bowl from rim to base and
lifts it clear of the floor.  An arcaded plinth carries the load -- the piers
between the arches are the feet -- and a continuous sill under them gives the
first layer something to hold on to.

Everything is millimetres with the feet on Z = 0, so the part is modelled in
its print orientation.

Design notes worth knowing before turning knobs
-----------------------------------------------
* ``bowl_lift`` is the air under the bowl's base once it is seated, so the
  overall height is ``bowl_height + bowl_lift + rim_capture``.  Every declared
  combination stays inside the Centauri Carbon's plate.
* The seat lip's underside is a 45 deg cone, comfortably inside the 50 deg
  overhang limit, and it finishes on a straight vertical land so its inner
  edge is never a feather edge.
* The flutes are subtracted, never added ribs, and ``forge_lib`` caps their
  depth against the wall, so no slider combination can cut into the bore.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "bowl_diameter": {
        "value": 6.0,
        "unit": "in",
        "min": 3.0,
        "max": 7.0,
        "step": 0.25,
        "description": "Rim diameter of the dog bowl",
    },
    "bowl_height": {
        "value": 4.0,
        "unit": "in",
        "min": 1.5,
        "max": 6.0,
        "step": 0.25,
        "description": "Height of the bowl from its base to its rim",
    },
    "fit_clearance": {
        "value": 1.0,
        "unit": "mm",
        "min": 0.0,
        "max": 4.0,
        "step": 0.1,
        "description": "Gap between the bowl rim and the bore, all the way round",
    },
    "wall_thickness": {
        "value": 5.0,
        "unit": "mm",
        "min": 2.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Thickness of the ring wall",
    },
    "bowl_lift": {
        "value": 12.0,
        "unit": "mm",
        "min": 0.0,
        "max": 60.0,
        "step": 1.0,
        "description": "Air under the bowl's base once it is seated",
    },
    "rim_capture": {
        "value": 10.0,
        "unit": "mm",
        "min": 3.0,
        "max": 30.0,
        "step": 0.5,
        "description": "Plain wall standing above the seat, around the bowl rim",
    },
    "lip_width": {
        "value": 5.0,
        "unit": "mm",
        "min": 2.0,
        "max": 14.0,
        "step": 0.5,
        "description": "How far the seat ledge reaches in under the bowl rim",
    },
    "foot_height": {
        "value": 14.0,
        "unit": "mm",
        "min": 6.0,
        "max": 40.0,
        "step": 0.5,
        "description": "Height of the arcaded base zone",
    },
    "feet_count": {
        "value": 4,
        "unit": "count",
        "min": 3,
        "max": 8,
        "step": 1,
        "description": "Number of feet (piers) around the arcaded base",
    },
    "flute_count": {
        "value": 24,
        "unit": "count",
        "min": 6,
        "max": 60,
        "step": 1,
        "description": "Number of flutes in the decorative band",
    },
    "flute_depth": {
        "value": 1.6,
        "unit": "mm",
        "min": 0.0,
        "max": 3.0,
        "step": 0.1,
        "description": "Depth of each flute; 0 leaves the wall plain",
    },
}

_LIP_LAND_MM = 2.0        # vertical land at the seat's inner edge
_PLINTH_GAP_MM = 3.0      # plain wall between the plinth and the fluted band
_WALL_OVERLAP_MM = 0.5    # the wall dips into the plinth's solid crown band


def _derived(p):
    """Every number build() actually uses, clamped so no slider can break it."""
    land = forge_lib.min_land()

    wall = max(p["wall_thickness"], forge_lib.min_wall())
    r_bore = 0.5 * p["bowl_diameter"] + p["fit_clearance"]
    r_out = r_bore + wall

    seat_z = p["bowl_lift"] + p["bowl_height"]
    total_h = seat_z + max(p["rim_capture"], 2.0 * land)

    foot_h = min(p["foot_height"], 0.40 * seat_z)
    sill = max(forge_lib.min_wall(), min(3.0, 0.25 * foot_h))

    lip_land = max(_LIP_LAND_MM, land)
    lip_w = min(p["lip_width"], 0.6 * r_bore, r_bore - 2.0)
    taper_h = lip_w                       # 45 deg underside, inside the 50 deg limit
    if taper_h + lip_land > 0.35 * seat_z:
        taper_h = max(0.35 * seat_z - lip_land, land)
        lip_w = min(lip_w, 0.95 * taper_h)
    if lip_w < 1.5:
        raise ValueError(
            "The seat ledge collapses at these settings. Raise Bowl height or "
            "Bowl lift, or lower Lip width, so the ledge is at least 1.5 mm wide."
        )

    z_land_bot = seat_z - lip_land
    z_taper_bot = z_land_bot - taper_h
    wall_z0 = max(foot_h - _WALL_OVERLAP_MM, 0.0)
    if z_taper_bot <= wall_z0 + 2.0:
        raise ValueError(
            "The seat ledge and the base run into each other. Lower Foot height "
            "or raise Bowl lift."
        )

    band_z0 = foot_h + _PLINTH_GAP_MM
    band_top = min(z_taper_bot - 4.0, band_z0 + 0.55 * total_h)

    return {
        "wall": wall,
        "r_bore": r_bore,
        "r_out": r_out,
        "r_seat": r_bore - lip_w,
        "r_plinth": r_out + min(0.6 * wall, 3.0),
        "seat_z": seat_z,
        "total_h": total_h,
        "foot_h": foot_h,
        "sill": sill,
        "z_land_bot": z_land_bot,
        "z_taper_bot": z_taper_bot,
        "wall_z0": wall_z0,
        "band_z0": band_z0,
        "band_h": band_top - band_z0,
    }


def _section_points(d):
    """The revolved cross-section as (radius, z) pairs, counter-clockwise.

    Out along the bottom, up the outside, in across the top rim, down the
    bore, in across the seat's top face, down its land, then out along the
    45 deg underside back to the bore.  Every sloped edge starts and ends on a
    straight one -- that is the no-knife-edges rule written as a polygon.
    """
    return [
        (d["r_bore"], d["wall_z0"]),
        (d["r_out"], d["wall_z0"]),
        (d["r_out"], d["total_h"]),
        (d["r_bore"], d["total_h"]),
        (d["r_bore"], d["seat_z"]),
        (d["r_seat"], d["seat_z"]),
        (d["r_seat"], d["z_land_bot"]),
        (d["r_bore"], d["z_taper_bot"]),
    ]


def build(p):
    d = _derived(p)

    sketch = Plane.XZ * Polygon(*_section_points(d), align=None)  # noqa: F405
    part = revolve(sketch, axis=Axis.Z)  # noqa: F405

    part += forge_lib.arcade_base(
        d["r_plinth"],
        d["foot_h"],
        int(p["feet_count"]),
        style="pad",
        inner_r=d["r_bore"],
        arch="pointed",
        sill=d["sill"],
    )

    if d["band_h"] >= 6.0 and p["flute_depth"] >= 0.15:
        part -= forge_lib.textured_band(
            d["r_out"],
            d["band_h"],
            int(p["flute_count"]),
            p["flute_depth"],
            wall=d["wall"],
            z_bottom=d["band_z0"],
        )

    return part
