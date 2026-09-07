"""Eevee-style dog-bowl holder -- split into a plain bowl and a details plate.

A stainless bowl drops into a ring that seats it by its rim.  The ring itself
is one soft, swollen silhouette of revolution -- the curved "bowl shape" --
with sockets on its rim for the character pieces.  Everything that makes it
look like Eevee -- the fur collar, the two ears, the tail -- lives in a
second group, generated the same way (`forge_lib` ornament helpers, nothing
sculpted) but never fused onto the plain body.

Defaults match the reference sheet: bowl 5.5 in (139.7 mm) diameter, 2 in
(50.8 mm) deep -- the recommended stainless insert size.

Two components, five buildable pieces
--------------------------------------
`build()` returns one thing, so which piece comes out is a parameter:

    part = 0  ->  the BOWL: curved base + feet + the three sockets, no fur
    part = 1  ->  one ear (print it twice; both go in the same two sockets)
    part = 2  ->  the tail
    part = 3  ->  the fur collar alone, sized to SLIDE onto the bowl's neck
    part = 4  ->  the DETAILS plate: collar + two ears + tail, stacked so
                  they read as one group without overlapping or blowing the
                  print bed (the collar alone is nearly as wide as the bed)

part 0 and part 4 are the two components asked for: the bowl with its
curve, and a second mesh carrying the details. part 1-3 are what part 4 is
built from, kept selectable so each piece can still be checked and printed
on its own -- the collar and the ear/tail have different print orientations,
so a single fused "details" solid would make `/check`'s overhang answer
meaningless for the pieces that lie flat.

Assembly: the fur collar is a slide fit over the bowl's neck (positive
clearance -- `forge_lib`'s default for "a collar that slips over a
separately printed cylinder"), and the ears/tail plug into the rim sockets
with the standard peg/socket pair. Nothing is glued by the geometry; the fit
is.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "part": {
        "value": 0,
        "unit": "count",
        "min": 0,
        "max": 4,
        "step": 1,
        "description": (
            "Which piece to build: 0 the bowl, 1 an ear, 2 the tail, "
            "3 the fur collar (slides onto the bowl), 4 a details plate "
            "(collar + two ears + tail, stacked for one look together)"
        ),
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
    "body_swell": {
        "value": 0.16,
        "unit": "ratio",
        "min": 0.0,
        "max": 0.24,
        "step": 0.02,
        "description": (
            "How far the bowl's waist swells past its rim, as a fraction of "
            "the rim radius -- this is the curve. 0 is a straight cylinder"
        ),
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
            "How far the leaves lean out from vertical. 42-48 deg is the "
            "window where the leaf tips are support-free as well as their "
            "undersides. The floor is 15, not 0: leaves hanging dead "
            "vertical against a swelling body graze it"
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
            "How far each leaf's front arches above its own edges, as a "
            "fraction of its half-width. 0 is a flat cut-out; 0.3 and up "
            "reads as fur"
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
        "description": "Peg on the appendages, and the socket in the bowl's rim",
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

#: Air left between stacked pieces on the details plate.
_PLATE_GAP_MM = 6.0


# --------------------------------------------------------------------------
# Outlines: proportions read off a reference, NOT a pixel trace
# --------------------------------------------------------------------------
#
# Ten control points each, as fractions of (width, length).  That is the
# whole ear: an artist moves one number and gets a different ear, and every
# one of them is still a printable part with a peg that fits the bowl.

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
# The bowl -- a curved silhouette of revolution, no fur
# --------------------------------------------------------------------------


def _derived(p):
    """Every number the bowl uses, clamped so no slider combination breaks it."""
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
    # leans outward as it rises IS an overhang, so the swell is clamped to
    # what the run up to the waist can carry -- the range is a promise, not a
    # hope.
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
    """The bowl's outer silhouette as (radius, z), base first.

    Six control points, not a trace: foot, the swell out of the foot, the
    waist, the shoulder, the rim.  `forge_lib.soft_body` splines them,
    samples the spline into a fine polygon and revolves it -- which is why
    this reads as a soft rounded curve instead of a straight cylinder.
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

    Close enough to size the collar and the lug ribs by: the spline never
    leaves its control polygon by more than a fraction of a millimetre.
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
    """The bore, the seat ledge and the rim, as one negative to subtract."""
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


def _lug_plan(p, d, band_top_z):
    """The rim lugs the sockets are bored into.

    A lug is a **rib**, not a boss: it runs down from the rim into the wall,
    so its lower end is buried in material rather than hanging off the rim
    as a flat-bottomed boss. It is sized and widened exactly as it would be
    if a fur collar still sat behind it -- the rib was always anchored in
    the plain wall by its own bite, the collar was never load-bearing for it.
    """
    tol = forge_lib.fit_tolerance("slide_fit")
    lug_r = 0.5 * p["peg_diameter"] + tol + 0.3 + _LUG_WALL_MM
    depth = p["peg_length"]
    lug_z0 = min(band_top_z - 2.0, d["total_h"] - depth - 3.0)
    lug_h = d["total_h"] - lug_z0
    if lug_h - depth < 2.5:
        raise ValueError(
            "The sockets are deeper than the rim can hold. Lower Peg length, or "
            "raise Rim capture so the rim is taller."
        )
    bite = max(min(0.6 * d["wall"], d["wall"] - forge_lib.min_wall() - 0.2, 3.0), 0.2)

    # A rib on a SOFT body has to stand clear of the body's own surface over
    # its whole height, or the two graze -- see part-authoring.md pattern C.
    widest = max(
        _body_radius_at(d, lug_z0 + (d["total_h"] - lug_z0) * step / 8.0)
        for step in range(9)
    )
    lug_r = max(lug_r, 0.5 * (widest - d["r_out"] + bite + 1.0))
    bore_r = 0.5 * p["peg_diameter"] + tol + 0.3
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


def _build_bowl(p):
    """The bowl alone: the curved body, its feet and its three empty sockets."""
    d = _derived(p)

    part = Pos(0.0, 0.0, d["wall_z0"]) * forge_lib.soft_body(_body_points(d))  # noqa: F405
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

    # Lug ribs from partway up the wall to the rim, then the sockets bored
    # down into them. A socket that opens upward is the only orientation
    # that prints without a ceiling.
    lp = _lug_plan(p, d, d["z_taper_bot"] - 2.0)
    spec = forge_lib.peg_spec(d=p["peg_diameter"], l=lp["depth"])
    angles = list(_EAR_ANGLES_DEG) + [_TAIL_ANGLE_DEG]

    for angle in angles:
        frame = Rot(0.0, 0.0, angle) * Pos(lp["lug_cr"], 0.0, lp["lug_z0"])  # noqa: F405
        part += frame * Pos(0.0, 0.0, 0.5 * lp["lug_h"]) * Cylinder(  # noqa: F405
            radius=lp["lug_r"], height=lp["lug_h"]
        )
    for angle in angles:
        part -= (
            Rot(0.0, 0.0, angle)  # noqa: F405
            * Pos(lp["lug_cr"], 0.0, d["total_h"] + 0.5)  # noqa: F405
            * Rot(180.0, 0.0, 0.0)  # noqa: F405
            * forge_lib.socket_for(spec, lp["tol"])
        )

    return part


# --------------------------------------------------------------------------
# The details: the fur collar (standalone) and the appendages
# --------------------------------------------------------------------------


def _collar_args(p, d, ring_radius, clearance):
    """The collar's arguments, sized to the bowl neck it slides onto.

    ``clearance`` is POSITIVE here -- the collar is no longer fused onto the
    bowl, it slides over it, so it wants `forge_lib`'s slide-fit clearance
    rather than the negative "bite" a unioned collar would use.
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
        clearance=clearance,
        cup=p["collar_cup"],
    )


def _collar_fit(p, d):
    """The collar's build args and plan, bore sized to the bowl's narrowest
    point over the band's own height -- see part-authoring.md pattern C.
    Two passes: the first sizes the band, the second re-bores now that the
    band's true height is known.
    """
    tol = forge_lib.fit_tolerance("slide_fit")
    band_top = d["z_taper_bot"] - 2.0
    args = _collar_args(p, d, _body_radius_at(d, band_top), tol)
    plan = forge_lib.leaf_collar_plan(**args)
    for _ in range(2):
        band_h = plan["band_height_mm"]
        narrowest = min(
            _body_radius_at(d, band_top - band_h * step / 8.0) for step in range(9)
        )
        args = _collar_args(p, d, narrowest, tol)
        plan = forge_lib.leaf_collar_plan(**args)
    return args, plan


def _build_collar(p):
    """The fur collar alone, standing on its own base -- a slide fit over
    the bowl's neck, not fused to it."""
    d = _derived(p)
    args, _ = _collar_fit(p, d)
    return forge_lib.leaf_collar(**args)  # noqa: F405


def _build_appendage(p, shape, width, length):
    """One ear or the tail: a silhouette with a peg, lying flat on the bed."""
    return forge_lib.silhouette_part(
        _outline(shape, width, length),
        p["appendage_thickness"],
        rounding=p["appendage_rounding"],
        peg={"d": p["peg_diameter"], "l": p["peg_length"]},
    )


def _build_details_plate(p):
    """Collar + two ears + tail as one group -- the second component.

    The collar's own diameter is already close to the bed's, so the pieces
    are stacked upward in Z with air between them rather than spread out
    sideways: each keeps its own footprint and print orientation, and the
    group's footprint stays the collar's, not the sum of everyone's.
    """
    d = _derived(p)
    args, plan = _collar_fit(p, d)
    collar = forge_lib.leaf_collar(**args)  # noqa: F405

    ear = _build_appendage(p, _EAR_SHAPE, p["ear_width"], p["ear_length"])
    tail = _build_appendage(p, _TAIL_SHAPE, p["tail_width"], p["tail_length"])

    z = plan["height_mm"] + _PLATE_GAP_MM
    group = collar
    group += Pos(0.0, 0.0, z) * ear  # noqa: F405
    z += p["appendage_thickness"] + _PLATE_GAP_MM
    group += Pos(0.0, 0.0, z) * ear  # noqa: F405
    z += p["appendage_thickness"] + _PLATE_GAP_MM
    group += Pos(0.0, 0.0, z) * tail  # noqa: F405
    return group


def build(p):
    """Return whichever piece ``part`` selects, modelled in its print orientation."""
    which = int(round(p["part"]))
    if which == 1:
        return _build_appendage(p, _EAR_SHAPE, p["ear_width"], p["ear_length"])
    if which == 2:
        return _build_appendage(p, _TAIL_SHAPE, p["tail_width"], p["tail_length"])
    if which == 3:
        return _build_collar(p)
    if which == 4:
        return _build_details_plate(p)
    return _build_bowl(p)
