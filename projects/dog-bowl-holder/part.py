"""Eevee-style dog-bowl holder -- the BASE, part 1 of the reference sheet.

A raised collar that wraps all the way around a stainless bowl.  The bowl is
lowered through the top bore and its rim lands on an internal seat lip near the
top; below the seat the bore opens back out so the bowl's body hangs free
inside the ring.  An arcaded plinth carries the load -- the piers between the
arches are the feet -- and a continuous sill under them gives the first layer
something to hold on to.

Three lugs on the top rim carry blind sockets for the slot-in appendages: two
on the X axis for the ears and one at the back for the tail.  Each socket
leans outward by ``appendage_angle`` so the ears splay the way they do in the
reference, and each is a blind hole opening UPWARD, which is the only
orientation that prints without a ceiling.

Everything is millimetres with the feet on Z = 0, so the part is modelled in
its print orientation.

Defaults are taken from the reference sheet
-------------------------------------------
* bowl 5.5 in (139.7 mm) rim, 2 in (50.8 mm) deep -- the recommended insert
* body 75.8 mm tall and 157.7 mm across the plinth (sheet: 3.0 in x 6.5 in)
* 169 mm across the ear lugs (sheet: 7.5 in overall, ears included)

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
* The lug radius grows with the socket lean, so a tilted socket can never
  break out through the side of its own lug.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "bowl_diameter": {
        "value": 5.5,
        "unit": "in",
        "min": 3.0,
        "max": 6.5,
        "step": 0.25,
        "description": "Rim diameter of the stainless bowl that drops in",
    },
    "bowl_height": {
        "value": 2.0,
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
        "value": 13.0,
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
    "socket_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 3.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Diameter of the ear/tail sockets in the top rim lugs",
    },
    "socket_depth": {
        "value": 10.0,
        "unit": "mm",
        "min": 3.0,
        "max": 16.0,
        "step": 0.5,
        "description": "How deep the ear/tail pegs go into the rim",
    },
    "appendage_angle": {
        "value": 20.0,
        "unit": "deg",
        "min": 0.0,
        "max": 25.0,
        "step": 1.0,
        "description": "How far the ears and tail lean outward; 0 stands them upright",
    },
    "tail_socket": {
        "value": True,
        "unit": "bool",
        "description": "Add the third lug at the back for the tail",
    },
}

_LIP_LAND_MM = 2.0        # vertical land at the seat's inner edge
_PLINTH_GAP_MM = 3.0      # plain wall between the plinth and the fluted band
_WALL_OVERLAP_MM = 0.5    # the wall dips into the plinth's solid crown band
_EAR_ANGLES_DEG = (0.0, 180.0)   # the two ear lugs, on the X axis
_TAIL_ANGLE_DEG = 270.0          # the tail lug, at the back
_LUG_WALL_MM = 3.0        # material left around a socket inside its lug


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


def _lug_plan(p, d):
    """Sizes for the appendage lugs and the sockets bored into them.

    The lug LEANS with its socket rather than standing upright, so the hole
    always enters its own top face square on.  A tilted hole through a flat
    top would meet it at an acute angle and leave a feather edge round the
    mouth -- exactly the knife-edge failure the authoring rules warn about,
    and it fails min_wall.
    """
    tol = forge_lib.fit_tolerance("slide_fit")
    tilt = max(0.0, min(p["appendage_angle"], 25.0))
    depth = p["socket_depth"]

    # socket_for's mouth chamfer grows the cavity a touch past its bore, so
    # allow for it before the wall.
    lug_r = 0.5 * p["socket_diameter"] + tol + 0.3 + _LUG_WALL_MM
    lug_h = min(depth + 6.0, 0.45 * d["total_h"])
    if lug_h - depth < 2.0:
        raise ValueError(
            "The ear sockets are deeper than the rim lugs can hold. Lower "
            "Socket depth, or raise Rim capture so the rim is taller."
        )

    # How far the lug buries itself in the ring wall: enough to weld on, never
    # so much that it leaves under min_wall behind it.
    bite = max(min(0.6 * d["wall"], d["wall"] - forge_lib.min_wall() - 0.2, 3.0), 0.2)
    lug_cr = d["r_out"] + lug_r - bite

    return {
        "tilt": tilt,
        "tol": tol,
        "depth": depth,
        "lug_r": lug_r,
        "lug_h": lug_h,
        "lug_cr": lug_cr,
        # Lean the lug and its top face still lands on the rim.
        "lug_z0": d["total_h"] - lug_h * math.cos(math.radians(tilt)),
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

    # --- appendage lugs last, then the sockets bored down into them --------
    lp = _lug_plan(p, d)
    angles = list(_EAR_ANGLES_DEG)
    if p["tail_socket"]:
        angles.append(_TAIL_ANGLE_DEG)

    spec = forge_lib.peg_spec(d=p["socket_diameter"], l=lp["depth"], key=False)

    def _frame(a):
        """Stand at the lug's root on the rim, then lean outward.

        Everything built along this axis is coaxial with the socket, which is
        what keeps the hole square to the face it enters.
        """
        return (
            Rot(0.0, 0.0, a)  # noqa: F405
            * Pos(lp["lug_cr"], 0.0, lp["lug_z0"])  # noqa: F405
            * Rot(0.0, lp["tilt"], 0.0)  # noqa: F405
        )

    for a in angles:
        frame = _frame(a)
        part += frame * Pos(0.0, 0.0, 0.5 * lp["lug_h"]) * Cylinder(  # noqa: F405
            radius=lp["lug_r"], height=lp["lug_h"]
        )
        # A domed foot, so the lug leaves the wall on a curve rather than a
        # flat ceiling.
        part += frame * Sphere(radius=lp["lug_r"])  # noqa: F405

    for a in angles:
        # Flipped to open at the lug's top face, overshooting it by 0.5 mm so
        # the mouth is never a coplanar boolean.
        part -= (
            _frame(a)
            * Pos(0.0, 0.0, lp["lug_h"] + 0.5)  # noqa: F405
            * Rot(0.0, 180.0, 0.0)  # noqa: F405
            * forge_lib.socket_for(spec, lp["tol"])
        )

    return part
