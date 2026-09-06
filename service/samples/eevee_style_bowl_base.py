"""Eevee-style dog-bowl holder -- the reference for FUNCTION PLUS CHARACTER.

A stainless bowl drops into a ring that seats it by its rim.  So far, an
ordinary parametric part.  What makes it the sample worth reading is the rest:
a fur collar of overlapping leaves around its waist, two ears and a tail --
none of it sculpted, all of it generated from `forge_lib` ornament helpers.

The insight the ornament helpers exist for
------------------------------------------
Nothing about a character-shaped functional part is sculpture:

* the body is a SILHOUETTE OF REVOLUTION -- six ``(radius, z)`` proportions
  splined, sampled and turned, :func:`forge_lib.soft_body`.  Deliberately not a
  cylinder: a collar's band is a cone, and against a straight wall a cone reads
  as a hat brim, while against a body that already swells and tapers it reads
  as the body's own shoulder;
* a fur collar is ONE leaf, arrayed round a ring with overlap, droop and a
  CUP -- :func:`forge_lib.leaf_collar`.  The cup arches each leaf's front over
  its flat back; flat leaves read as cut-outs, cupped ones as fur;
* an ear, a tail, a fin, a wing is a SILHOUETTE with thickness and rounding --
  :func:`forge_lib.silhouette_part`.

All three are parameter sets, so all three are generated rather than modelled.
The ear's outline below is ten numbers read off a reference image as
*proportions* -- tip here, widest point there, root that wide -- never a pixel
trace.  Nudge them and the ear changes; the peg, the socket and the
printability all follow.

One script, three printed pieces
--------------------------------
`build()` has to return one thing, and these three pieces have three different
print orientations: the base stands up on its feet, the ear and the tail lie
flat on the bed.  A `Compound` of all three would force one orientation on all
of them and make `/check`'s answer meaningless -- an ear standing on its tip
"needs supports" is not a fact about the ear.  So the choice is a parameter:

    part = 0  ->  the base ring, with the collar and the three sockets
    part = 1  ->  one ear (print it twice; it is symmetric about its own plane)
    part = 2  ->  the tail

Check each one separately.  That is the convention to copy for any multi-piece
design: **one `part` selector, one `/check` per piece.**

What the checks say, at the defaults
------------------------------------
* **part 0, the base** -- all four checks **pass**: swollen body, cupped
  collar and all.  Three decisions bought that, and all three are worth
  stealing:

  1. `collar_droop` defaults to 44 degrees.  Between 42 and 48 degrees is the
     window where a drooping leaf is support-free *at both ends*: its underside
     is inside the 50 degree limit because it leans no further than that, and
     its tip land is inside the limit because it leans no *less*.  It is also,
     as it happens, about the flare the reference has.  Cupping costs nothing
     here: a collar's arched face points outward and UP, so the face the check
     measures is still the flat back, at exactly the droop.
  2. The socket lugs are ribs that run down into the collar band, not bosses
     hanging off the rim.  A boss leaves its own flat underside in mid-air:
     three of them measured 354 mm2 of 90 degree overhang.
  3. The collar's bore is sized to the **narrowest** the body gets over the
     band's own height, not to the body's radius under the band's top.  On a
     cylinder those are the same number.  On a soft body they are not, and the
     difference is 535 mm2 of 90 degree overhang: the band's bore is a
     cylinder, so where the body shrinks away from it the band's bottom rim
     stops being buried.  Measured, on the first cut of this after the body
     stopped being a cylinder.

  The collar band's own bottom rim -- the one face `leaf_collar_plan`
  ["unsupported"] always names -- disappears here, because the band is unioned
  into the body's wall (see `_collar_args`: a **negative** clearance).

  `body_swell` and `collar_cup` are both sliders, and 0 on either gives back
  the straight-cylinder, flat-leaf part this sample used to be.

* **part 1 and part 2** -- `bed_fit`, `min_wall` and `watertight` pass.
  `overhangs` warns at 63 mm2 (ear) and 80 mm2 (tail): that is the underside of
  the peg, which is a horizontal cylinder however you draw it.  It bridges at
  6 mm, and `-Y up` -- standing the ear on its root -- would leave nothing
  unsupported at all if you would rather print it that way.  The blade itself
  has no downward face: it lies flat with every side face vertical.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "part": {
        "value": 0,
        "unit": "count",
        "min": 0,
        "max": 2,
        "step": 1,
        "description": "Which piece to build: 0 the base, 1 an ear, 2 the tail",
    },
    "bowl_diameter": {
        "value": 5.5,
        "unit": "in",
        "min": 3.0,
        "max": 6.0,
        "step": 0.25,
        "description": "Rim diameter of the stainless bowl that drops in",
    },
    "bowl_height": {
        "value": 2.0,
        "unit": "in",
        "min": 1.5,
        "max": 4.0,
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
    "wall": {
        "value": 5.0,
        "unit": "mm",
        "min": 2.5,
        "max": 10.0,
        "step": 0.5,
        "description": "Thickness of the ring wall",
    },
    "bowl_lift": {
        "value": 14.0,
        "unit": "mm",
        "min": 4.0,
        "max": 40.0,
        "step": 1.0,
        "description": "Air under the bowl's base once it is seated",
    },
    "rim_capture": {
        "value": 12.0,
        "unit": "mm",
        "min": 4.0,
        "max": 24.0,
        "step": 0.5,
        "description": "Plain wall standing above the seat, around the bowl rim",
    },
    "lip_width": {
        "value": 5.0,
        "unit": "mm",
        "min": 2.0,
        "max": 12.0,
        "step": 0.5,
        "description": "How far the seat ledge reaches in under the bowl rim",
    },
    "foot_height": {
        "value": 14.0,
        "unit": "mm",
        "min": 6.0,
        "max": 30.0,
        "step": 0.5,
        "description": "Height of the arcaded base zone",
    },
    "feet_count": {
        "value": 5,
        "unit": "count",
        "min": 3,
        "max": 8,
        "step": 1,
        "description": "Number of piers around the arcaded base",
    },
    "collar_leaves": {
        "value": 16,
        "unit": "count",
        "min": 8,
        "max": 26,
        "step": 1,
        "description": "How many leaves in the fur collar",
    },
    "collar_length": {
        "value": 26.0,
        "unit": "mm",
        "min": 10.0,
        "max": 40.0,
        "step": 1.0,
        "description": "Root-to-tip length of one collar leaf",
    },
    "collar_overlap": {
        "value": 0.35,
        "unit": "ratio",
        "min": 0.0,
        "max": 0.8,
        "step": 0.05,
        "description": "How much each leaf overlaps its neighbour, as a fraction of the pitch",
    },
    "collar_droop": {
        "value": 44.0,
        "unit": "deg",
        "min": 15.0,
        "max": 48.0,
        "step": 1.0,
        "description": (
            "How far the leaves lean out from vertical. 42-48 deg is the window "
            "where the leaf tips are support-free as well as their undersides. "
            "The floor is 15, not 0: leaves hanging dead vertical against a "
            "swelling body graze it, and the declared range is a promise"
        ),
    },
    "collar_jitter": {
        "value": 0.35,
        "unit": "ratio",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "description": "Per-leaf variation, so the collar reads as fur and not as a machined ring",
    },
    "collar_seed": {
        "value": 3,
        "unit": "count",
        "min": 0,
        "max": 999,
        "step": 1,
        "description": "Change it for a different collar; the same number always builds the same one",
    },
    "collar_thickness": {
        "value": 2.6,
        "unit": "mm",
        "min": 1.2,
        "max": 5.0,
        "step": 0.2,
        "description": "Thickness of one leaf",
    },
    "collar_cup": {
        "value": 0.32,
        "unit": "ratio",
        "min": 0.0,
        "max": 0.42,
        "step": 0.02,
        "description": (
            "How far each leaf's front arches above its own edges, as a fraction "
            "of its half-width. 0 is the flat cut-out; 0.3 and up reads as fur"
        ),
    },
    "body_swell": {
        "value": 0.16,
        "unit": "ratio",
        "min": 0.0,
        "max": 0.24,
        "step": 0.02,
        "description": (
            "How far the body's waist swells past its rim, as a fraction of the "
            "rim radius. 0 is a straight cylinder"
        ),
    },
    "ear_length": {
        "value": 70.0,
        "unit": "mm",
        "min": 30.0,
        "max": 110.0,
        "step": 1.0,
        "description": "Root to tip of an ear, not counting its peg",
    },
    "ear_width": {
        "value": 30.0,
        "unit": "mm",
        "min": 16.0,
        "max": 55.0,
        "step": 1.0,
        "description": "Width of an ear at its widest",
    },
    "tail_length": {
        "value": 78.0,
        "unit": "mm",
        "min": 30.0,
        "max": 120.0,
        "step": 1.0,
        "description": "Root to tip of the tail",
    },
    "tail_width": {
        "value": 34.0,
        "unit": "mm",
        "min": 16.0,
        "max": 60.0,
        "step": 1.0,
        "description": "Width of the tail at its widest",
    },
    "appendage_thickness": {
        "value": 9.0,
        "unit": "mm",
        "min": 5.0,
        "max": 16.0,
        "step": 0.5,
        "description": "Blade thickness of the ears and the tail; must bury the peg",
    },
    "appendage_rounding": {
        "value": 1.6,
        "unit": "mm",
        "min": 0.0,
        "max": 5.0,
        "step": 0.1,
        "description": "Fillet on the top edge of an ear or tail; stepped down if the kernel refuses it",
    },
    "peg_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 4.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Peg on the appendages, and the socket in the base rim",
    },
    "peg_length": {
        "value": 11.0,
        "unit": "mm",
        "min": 5.0,
        "max": 18.0,
        "step": 0.5,
        "description": "How far a peg goes into its socket",
    },
}

#: Where the ears and the tail plug in, in degrees round the rim.
_EAR_ANGLES_DEG = (-38.0, 38.0)
_TAIL_ANGLE_DEG = 180.0

#: Material left around a socket inside its lug.
_LUG_WALL_MM = 3.2

#: Vertical land at the seat ledge's inner edge.
_LIP_LAND_MM = 2.0

#: How far the collar band bites into the ring wall, so the union is one solid
#: rather than two solids a slide fit apart.
_COLLAR_BITE_MM = 1.0


# --------------------------------------------------------------------------
# Outlines: proportions read off a reference, NOT a pixel trace
# --------------------------------------------------------------------------
#
# Ten control points each, as fractions of (width, length).  That is the whole
# ear: an artist moves one number and gets a different ear, and every one of
# them is still a printable part with a peg that fits the base.

_EAR_SHAPE = [
    (0.00, 0.000),   # root, on the centre line
    (0.36, 0.085),   # the root swells out fast
    (0.50, 0.340),
    (0.40, 0.660),
    (0.13, 0.885),
    (0.00, 1.000),   # tip
    (-0.20, 0.830),
    (-0.43, 0.490),
    (-0.47, 0.170),
    (-0.27, 0.028),
]

_TAIL_SHAPE = [
    (0.00, 0.000),   # root
    (0.26, 0.130),
    (0.47, 0.360),
    (0.50, 0.640),
    (0.33, 0.880),
    (0.05, 1.000),   # the curl at the tip
    (-0.22, 0.900),
    (-0.14, 0.690),
    (-0.30, 0.430),
    (-0.29, 0.160),
]


def _outline(shape, width, length):
    """Scale a proportion table into millimetres."""
    return [[u * width, v * length] for u, v in shape]


# --------------------------------------------------------------------------
# The base
# --------------------------------------------------------------------------


def _derived(p):
    """Every number the base uses, clamped so no slider combination breaks it."""
    land = forge_lib.min_land()
    wall = max(p["wall"], forge_lib.min_wall())

    r_bore = 0.5 * p["bowl_diameter"] + p["fit_clearance"]
    r_out = r_bore + wall

    seat_z = p["bowl_lift"] + p["bowl_height"]
    total_h = seat_z + max(p["rim_capture"], 2.0 * land)

    foot_h = min(p["foot_height"], 0.40 * seat_z)
    sill = max(forge_lib.min_wall(), min(3.0, 0.25 * foot_h))

    lip_land = max(_LIP_LAND_MM, land)
    lip_w = min(p["lip_width"], 0.6 * r_bore, r_bore - 2.0)
    taper_h = lip_w                      # a 45 deg underside, inside the 50 deg limit
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
    wall_z0 = max(foot_h - 0.5, 0.0)
    if z_taper_bot <= wall_z0 + 4.0:
        raise ValueError(
            "The seat ledge and the arcaded base run into each other. Lower Foot "
            "height, or raise Bowl lift."
        )

    # How far the body's waist stands proud of its rim.  A silhouette that
    # leans outward as it rises IS an overhang, so the swell is clamped to what
    # the run up to the waist can carry -- the range is a promise, not a hope.
    height = total_h - wall_z0
    swell = min(
        p["body_swell"] * r_out,
        0.45 * height * math.tan(math.radians(forge_lib.max_overhang_deg() - 6.0)),
    )

    return {
        "land": land,
        "wall": wall,
        "swell": swell,
        "body_h": height,
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
    }


def _body_points(d):
    """The body's outer silhouette as (radius, z), base first.

    Six control points, not a trace: foot, the swell out of the foot, the
    waist, the shoulder, the rim.  `forge_lib.soft_body` splines them, samples
    the spline into a fine polygon and revolves it -- which is why this reads
    as a soft rounded body instead of a straight cylinder, and why the collar's
    band lands on a shoulder rather than standing out of a wall like a brim.
    """
    height = d["body_h"]
    swell = d["swell"]
    return [
        (d["r_out"], 0.0),
        (d["r_out"] + 0.55 * swell, 0.16 * height),
        (d["r_out"] + swell, 0.45 * height),
        (d["r_out"] + 0.80 * swell, 0.70 * height),
        (d["r_out"] + 0.25 * swell, 0.90 * height),
        (d["r_out"], height),
    ]


def _body_radius_at(d, z):
    """The silhouette's radius at world height *z*, read straight-line.

    Close enough to place a collar by: the band bites `_COLLAR_BITE_MM` into
    the body anyway, and the spline never leaves its control polygon by more
    than a fraction of that.
    """
    points = _body_points(d)
    local = z - d["wall_z0"]
    if local <= points[0][1]:
        return points[0][0]
    for (r0, z0), (r1, z1) in zip(points, points[1:]):
        if local <= z1:
            return r0 + (r1 - r0) * (local - z0) / max(z1 - z0, 1e-9)
    return points[-1][0]


def _cavity_points(d):
    """The bore, the seat ledge and the rim, as one negative to subtract.

    The same section the base used to be revolved from, turned inside out: with
    the outside now coming from `soft_body`, the inside is a cutter.  Every
    sloped edge still starts and ends on a straight one -- the no-knife-edges
    rule written as a polygon -- and it overshoots the body at both ends so the
    boolean is never coplanar.
    """
    return [
        (0.0, d["wall_z0"] - 1.0),
        (d["r_bore"], d["wall_z0"] - 1.0),
        (d["r_bore"], d["z_taper_bot"]),
        (d["r_seat"], d["z_land_bot"]),
        (d["r_seat"], d["seat_z"]),
        (d["r_bore"], d["seat_z"]),
        (d["r_bore"], d["total_h"] + 1.0),
        (0.0, d["total_h"] + 1.0),
    ]


def _collar_args(p, d, ring_radius):
    """The collar's arguments, sized to the body it wraps.

    ``ring_radius`` is the **narrowest** the body gets over the band's own
    height, not its radius at the band's top.  On a straight cylinder those are
    the same number; on a soft body they are not, and the difference is not
    cosmetic: the band's bore is a cylinder, so if the body shrinks away from
    it anywhere along the band, the band's bottom rim -- the one face
    ``leaf_collar_plan`` always flags -- stops being buried and comes back as
    535 mm2 of 90 degree overhang.  Measured, on the first cut of this sample
    after the body stopped being a cylinder.

    ``clearance`` is NEGATIVE on purpose: the band bites ``_COLLAR_BITE_MM``
    into the body's wall, so the union is one solid.  The positive slide-fit
    default is for a collar that slips over a separate printed cylinder.

    ``cup`` is what turns a leaf from a cut-out into a leaf: the front of each
    one arches above its own edges, so the collar reads as fur rather than as
    a ring of flat blades.  The default 2-layer, 35%-overlap collar is what
    covers the band -- the two ranks shingle, and the outer one is stepped
    along the leaves' own normal so it lies OVER the inner rank rather than
    hiding it behind the band's skirt.
    """
    length = min(p["collar_length"], 0.55 * (d["z_taper_bot"] - d["wall_z0"]) + 8.0)
    return dict(
        ring_radius=ring_radius,
        leaf_length=max(length, 8.0),
        leaf_width=0.7 * length,
        count=int(p["collar_leaves"]),
        overlap=p["collar_overlap"],
        droop_deg=p["collar_droop"],
        thickness=p["collar_thickness"],
        jitter=p["collar_jitter"],
        seed=int(p["collar_seed"]),
        clearance=-_COLLAR_BITE_MM,
        cup=p["collar_cup"],
    )


def _lug_plan(p, d, band_top_z):
    """The rim lugs the sockets are bored into.

    A lug is a **rib**, not a boss: it runs all the way down from the rim into
    the collar band, so its lower end is buried in material.  A short boss
    hanging off the rim would leave its own flat underside in mid-air, which is
    a 90 degree overhang and about 350 mm2 of it for three lugs -- measured,
    before this was changed.  A rib has nothing but vertical faces and an
    upward-facing top.
    """
    tol = forge_lib.fit_tolerance("slide_fit")
    lug_r = 0.5 * p["peg_diameter"] + tol + 0.3 + _LUG_WALL_MM
    depth = p["peg_length"]
    bore_r = 0.5 * p["peg_diameter"] + tol + 0.3
    # Start the rib a little below the collar band's top face so its foot is
    # inside the band rather than resting on it.
    lug_z0 = min(band_top_z - 2.0, d["total_h"] - depth - 3.0)
    lug_h = d["total_h"] - lug_z0
    if lug_h - depth < 2.5:
        raise ValueError(
            "The sockets are deeper than the rim can hold. Lower Peg length, or "
            "raise Rim capture so the rim is taller."
        )
    # How far the rib buries itself in the wall: enough to weld on, never so
    # much that it leaves under the minimum wall behind it.
    bite = max(min(0.6 * d["wall"], d["wall"] - forge_lib.min_wall() - 0.2, 3.0), 0.2)

    # A rib on a SOFT body has one more thing to satisfy: it has to stand clear
    # of the body's own surface over its whole height, or the two graze.  Where
    # a straight cylinder is tangent to a swelling wall the boolean leaves a
    # feather edge -- 0.014 mm, measured, on the widest body this sample will
    # build -- which is the same knife-edge failure a taper has, in plan view.
    # So the rib is widened until its outer face clears the widest the body
    # gets under it by a millimetre.  It is a rib; a fatter one is still a rib.
    widest = max(
        _body_radius_at(d, lug_z0 + (d["total_h"] - lug_z0) * step / 8.0)
        for step in range(9)
    )
    lug_r = max(lug_r, 0.5 * (widest - d["r_out"] + bite + 1.0))
    if lug_r - bore_r < _LUG_WALL_MM - 1e-9:  # pragma: no cover - belt and braces
        lug_r = bore_r + _LUG_WALL_MM
    return {
        "tol": tol,
        "depth": depth,
        "lug_r": lug_r,
        "lug_h": lug_h,
        "lug_cr": d["r_out"] + lug_r - bite,
        "lug_z0": lug_z0,
    }


def _build_base(p):
    d = _derived(p)

    # The body: one soft silhouette, splined and revolved, then bored.
    part = Pos(0.0, 0.0, d["wall_z0"]) * forge_lib.soft_body(  # noqa: F405
        _body_points(d)
    )
    part -= revolve(  # noqa: F405
        Plane.XZ * Polygon(*_cavity_points(d), align=None), axis=Axis.Z  # noqa: F405
    )

    # Feet: an openwork plinth with pointed arches, which pass the overhang
    # check outright where a round arch only bridges.
    part += forge_lib.arcade_base(
        d["r_plinth"],
        d["foot_h"],
        int(p["feet_count"]),
        style="pad",
        inner_r=d["r_bore"],
        arch="pointed",
        sill=d["sill"],
    )

    # The fur collar.  Ask for the plan first so the script can place the band
    # by its own height instead of guessing at it -- and then ask again, now
    # that the band's height is known, with the bore sized to the narrowest the
    # body gets underneath it.  Two passes, because the band's height depends
    # on its radius and its radius depends on where the band ends up.  The
    # second pass moves it by a millimetre or so; the extra half millimetre of
    # bite absorbs that.
    band_top = d["z_taper_bot"] - 2.0
    args = _collar_args(p, d, _body_radius_at(d, band_top))
    collar_plan = forge_lib.leaf_collar_plan(**args)
    for _ in range(2):
        band_h = collar_plan["band_height_mm"]
        narrowest = min(
            _body_radius_at(d, band_top - band_h * step / 8.0) for step in range(9)
        )
        args = _collar_args(p, d, narrowest - 0.5)
        collar_plan = forge_lib.leaf_collar_plan(**args)
    bottom = max(band_top - collar_plan["height_mm"], d["foot_h"] + 1.0)
    part += Pos(0.0, 0.0, bottom) * forge_lib.leaf_collar(**args)  # noqa: F405

    # Lug ribs from the collar up to the rim, then the sockets bored down into
    # them.  A socket that opens upward is the only orientation that prints
    # without a ceiling.
    lp = _lug_plan(p, d, bottom + collar_plan["height_mm"])
    spec = forge_lib.peg_spec(d=p["peg_diameter"], l=lp["depth"])
    angles = list(_EAR_ANGLES_DEG) + [_TAIL_ANGLE_DEG]

    for angle in angles:
        frame = Rot(0.0, 0.0, angle) * Pos(lp["lug_cr"], 0.0, lp["lug_z0"])  # noqa: F405
        part += frame * Pos(0.0, 0.0, 0.5 * lp["lug_h"]) * Cylinder(  # noqa: F405
            radius=lp["lug_r"], height=lp["lug_h"]
        )
    for angle in angles:
        # Flipped so it opens at the lug's top face, overshooting by 0.5 mm so
        # the mouth is never a coplanar boolean.
        part -= (
            Rot(0.0, 0.0, angle)  # noqa: F405
            * Pos(lp["lug_cr"], 0.0, d["total_h"] + 0.5)  # noqa: F405
            * Rot(180.0, 0.0, 0.0)  # noqa: F405
            * forge_lib.socket_for(spec, lp["tol"])
        )

    return part


# --------------------------------------------------------------------------
# The appendages
# --------------------------------------------------------------------------


def _build_appendage(p, shape, width, length):
    """One ear or the tail: a silhouette with a peg, lying flat on the bed."""
    return forge_lib.silhouette_part(
        _outline(shape, width, length),
        p["appendage_thickness"],
        rounding=p["appendage_rounding"],
        peg={"d": p["peg_diameter"], "l": p["peg_length"]},
    )


def build(p):
    """Return whichever piece ``part`` selects, modelled in its print orientation."""
    which = int(round(p["part"]))
    if which == 1:
        return _build_appendage(p, _EAR_SHAPE, p["ear_width"], p["ear_length"])
    if which == 2:
        return _build_appendage(p, _TAIL_SHAPE, p["tail_width"], p["tail_length"])
    return _build_base(p)
