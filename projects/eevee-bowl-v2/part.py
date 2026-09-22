"""Eevee-inspired dog-bowl holder -- v2, built to the signed-off design sheet.

Four printed pieces, one script, one ``part`` selector:

    part = 0  ->  the BASE: bowl seat, arcaded feet, a plain cylindrical neck,
                  and the rim lugs the ears and the tail plug into
    part = 1  ->  one EAR (print it twice; both go in the base's side sockets)
    part = 2  ->  the TAIL (mounts on the back with a round peg -- see below)
    part = 3  ->  the FUR COLLAR, alone -- a separate printed ring sized to
                  SLIDE onto the base's neck, not fused to it

This differs from the ``eevee_style_bowl_base.py`` sample it is built from in
the ways the signed-off sheet asked for:

* the collar is its own piece (part 3) that slides over a plain, constant
  radius NECK rather than being unioned into a swelling body -- a slide fit
  only works cleanly on a section that is genuinely cylindrical, so the body's
  silhouette now has a flat neck band *below* the swell instead of swelling
  from the foot;
* every clearance is a named parameter at the value the sheet settled on,
  not a default borrowed from the printer profile.

REVISION, authorized: the tail's joint was originally a hand-built dovetail
(a planning choice, never the sheet's requirement). Its trapezoid flank
corners needed more width than the tail's own outline has near its root --
two sizing moves only got the thinnest wall to 0.628 mm against the 0.8 mm
floor. Replaced with the SAME joint the ears use: `silhouette_part`'s own
``peg=``, forge_lib's tested peg + anti-rotation-rib mechanism, sized by
measuring the root itself (`tail_peg_diameter_mm`'s description below has
the derivation) rather than picked and hoped for.

Both the ears and the tail mount as RIBS running down into the body's own
shoulder (never bosses hanging off the rim) for the same reason the sample
uses ribs: a boss leaves its own flat underside in mid-air.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "part": {
        "value": 0,
        "unit": "count",
        "min": 0,
        "max": 3,
        "step": 1,
        "description": "Which piece to build: 0 the base, 1 an ear, 2 the tail, 3 the fur collar",
    },
    "bowl_diameter": {
        "value": 5.5,
        "unit": "in",
        "min": 3.0,
        "max": 6.0,
        "step": 0.25,
        "description": "Rim diameter of the stainless bowl insert that drops in",
    },
    "bowl_height": {
        "value": 2.0,
        "unit": "in",
        "min": 1.5,
        "max": 4.0,
        "step": 0.25,
        "description": "Height of the bowl insert from its base to its rim",
    },
    "fit_clearance": {
        "value": 0.5,
        "unit": "mm",
        "min": 0.1,
        "max": 2.0,
        "step": 0.05,
        "description": "Insert seat clearance -- radial gap between the bowl rim and the seat bore, all the way round",
    },
    "wall": {
        "value": 3.0,
        "unit": "mm",
        "min": 2.0,
        "max": 8.0,
        "step": 0.5,
        "description": "Thickness of the ring wall (five 0.4 mm perimeters plus margin)",
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
        "description": (
            "Number of piers around the arcaded base -- flat-bottomed, so the "
            "optional stick-on rubber feet (bought separately) have somewhere "
            "flat to land"
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
        "description": "Root-to-tip length of one collar leaf -- clamped so the collar's band fits the base's neck",
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
            "where the leaf tips are support-free as well as their undersides"
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
        "description": "How far each leaf's front arches above its own edges. 0 is a flat cut-out; 0.3 and up reads as fur",
    },
    "collar_clearance_mm": {
        "value": 0.15,
        "unit": "mm",
        "min": 0.05,
        "max": 0.6,
        "step": 0.05,
        "description": "Slide-fit gap between the collar's bore and the base's neck, all the way round -- the collar is its own printed piece, never fused to the base",
    },
    "body_swell": {
        "value": 0.16,
        "unit": "ratio",
        "min": 0.0,
        "max": 0.24,
        "step": 0.02,
        "description": "How far the shoulder (above the neck) swells past the neck radius, as a fraction of it. 0 is a straight cylinder",
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
        "description": "Blade thickness of the ears and the tail; must bury the ear/tail peg",
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
        "description": "Ear peg diameter, and the matching socket in the base rim",
    },
    "peg_length": {
        "value": 11.0,
        "unit": "mm",
        "min": 5.0,
        "max": 18.0,
        "step": 0.5,
        "description": "How far an ear's peg goes into its socket",
    },
    "ear_clearance_mm": {
        "value": 0.2,
        "unit": "mm",
        "min": 0.05,
        "max": 0.6,
        "step": 0.05,
        "description": "Ear slot clearance, per side -- slide fit between the peg and its socket",
    },
    "tail_peg_diameter_mm": {
        "value": 6.8,
        "unit": "mm",
        "min": 3.0,
        "max": 7.0,
        "step": 0.1,
        "description": (
            "Derived, not guessed: forge_lib measured the tail root's own "
            "width at 8.84 mm (an oversized probe peg made it name the "
            "number in its refusal). 8.84 - 2x0.8mm wall floor = 7.24 mm, "
            "but the 9 mm appendage thickness caps it lower first -- "
            "9 - 2x1.0mm (forge_lib's own margin) = 7.0 mm -- so the "
            "thickness constraint binds, not the root. Set to 6.8 mm, just "
            "under that 7.0 mm ceiling rather than exactly on it. Checked: "
            "min_wall thinnest 2.38 mm, comfortably clear of 0.8 mm"
        ),
    },
    "tail_peg_length_mm": {
        "value": 11.0,
        "unit": "mm",
        "min": 5.0,
        "max": 18.0,
        "step": 0.5,
        "description": "How far the tail's peg goes into its socket -- same convention as the ear peg",
    },
    "tail_peg_clearance_mm": {
        "value": 0.2,
        "unit": "mm",
        "min": 0.05,
        "max": 0.6,
        "step": 0.05,
        "description": "Tail socket clearance, per side -- same 0.2 mm slide fit as the ear pegs",
    },
}

#: Where the ears and the tail plug in, in degrees round the rim.
#:
#: CORRECTED -- the previous derivation measured the right pixels but
#: anchored them to the wrong axis. It read the ear tips as "48 deg either
#: side of the back axis (180 deg, where the tail is)", which put both ears
#: 132/228 deg -- right next to the tail. The sheet's top view (and the two
#: assembled-photo angles beside it) shows the opposite: the ears flank the
#: FACE side, directly across the bowl from the tail, and the tail is the
#: one thing that does NOT appear in the top view at all (it's low and to
#: the back, hidden under the rim from directly above).
#:
#: Re-measured from the top-view circle, pixel-fit (not eyeballed): circle
#: centre (120, 134) in the cropped panel's own coordinates, radius ~76 px
#: for the plain rim ring, found by fitting the two thin concentric circles
#: independent of the ears/feet/text noise. Ear tips read at (58, 68) and
#: (182, 70) in the same coordinates -- symmetric about the centre to
#: within 2 px, confirming the pixel fit. Angle of each tip from the
#: vertical (front, 0 deg -- opposite the tail's 180 deg): atan2(dx, -dy)
#: gives 43.2 deg and 44.1 deg either side, average 43.7 deg.
#:
#: So: front axis (0 deg) +/- 43.7 deg, not back axis (180 deg) +/- 48 deg.
#: Old: (132.0, 228.0) -- both hugging the tail.
#: New: (43.7, 316.3) -- flanking the face, opposite the tail.
_EAR_ANGLES_DEG = (43.7, 316.3)
_TAIL_ANGLE_DEG = 180.0

#: Material left around a socket inside its lug.
_LUG_WALL_MM = 3.2

#: Vertical land at the seat ledge's inner edge.
_LIP_LAND_MM = 2.0

#: How much taller than the collar's own band the neck is built, so the band
#: never rides up into the swelling shoulder above it.
_NECK_MARGIN_MM = 2.0


# --------------------------------------------------------------------------
# Outlines: proportions read off the reference sheet, NOT a pixel trace
# --------------------------------------------------------------------------

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


def _collar_plan_for(p, ring_radius, leaf_length):
    """The fur-collar plan at a given leaf length, on a fixed-radius neck.

    Unlike the fused sample, ``ring_radius`` never has to be searched for --
    the neck below is built to be exactly this radius, constant over its
    whole height, so a plain cylindrical bore fits it everywhere at once.
    """
    leaf_length = max(leaf_length, 8.0)
    return forge_lib.leaf_collar_plan(
        ring_radius=ring_radius,
        leaf_length=leaf_length,
        leaf_width=0.7 * leaf_length,
        count=int(p["collar_leaves"]),
        overlap=p["collar_overlap"],
        droop_deg=p["collar_droop"],
        thickness=p["collar_thickness"],
        jitter=p["collar_jitter"],
        seed=int(p["collar_seed"]),
        clearance=p["collar_clearance_mm"],
        cup=p["collar_cup"],
    )


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
    taper_h = lip_w
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

    height = total_h - wall_z0

    # The neck: a plain cylindrical band, sized to the collar it has to carry
    # -- computed once, at this fixed radius, so there is nothing to search
    # for the way the fused sample had to search for "the narrowest point".
    neck_budget = 0.55 * height
    collar_len = min(p["collar_length"], max(0.6 * neck_budget, 10.0))
    collar_plan = _collar_plan_for(p, r_out, collar_len)
    neck_h = min(max(collar_plan["band_height_mm"] + _NECK_MARGIN_MM, 3.0 * land), neck_budget)
    if neck_h <= 2.0 * land:
        raise ValueError(
            "There is no room left for the neck once the seat and the rim are "
            "built. Lower Bowl lift or Rim capture, or shorten Collar length."
        )

    swell_span = height - neck_h
    swell = min(
        p["body_swell"] * r_out,
        0.45 * swell_span * math.tan(math.radians(forge_lib.max_overhang_deg() - 6.0)),
    )

    return {
        "land": land,
        "wall": wall,
        "swell": swell,
        "body_h": height,
        "neck_h": neck_h,
        "collar_length": collar_len,
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

    Seven control points: a plain neck at constant radius (where the collar
    slides on), then the swelling shoulder, then the rim. `forge_lib.soft_body`
    splines them, samples the spline into a fine polygon and revolves it. The
    neck stays genuinely cylindrical -- no spline wobble -- because both its
    points share the same radius; only the shoulder above it curves.
    """
    neck_h = d["neck_h"]
    swell_span = d["body_h"] - neck_h
    swell = d["swell"]
    return [
        (d["r_out"], 0.0),
        (d["r_out"], neck_h),
        (d["r_out"] + 0.55 * swell, neck_h + 0.16 * swell_span),
        (d["r_out"] + swell, neck_h + 0.45 * swell_span),
        (d["r_out"] + 0.80 * swell, neck_h + 0.70 * swell_span),
        (d["r_out"] + 0.25 * swell, neck_h + 0.90 * swell_span),
        (d["r_out"], d["body_h"]),
    ]


def _body_radius_at(d, z):
    """The silhouette's radius at world height *z*, read straight-line."""
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


def _round_lug_plan(d, z0, diameter, length, clearance):
    """Sizing for one round lug: a rib from *z0* up to the rim, holding a
    peg/socket pair. Shared by the ears and (now) the tail -- same joint
    type, same helper, just a different diameter/length/clearance triple."""
    tol = clearance
    depth = length
    bore_r = 0.5 * diameter + tol + 0.3
    lug_r = bore_r + _LUG_WALL_MM
    lug_h = d["total_h"] - z0
    if lug_h - depth < 2.5:
        raise ValueError(
            "A socket is deeper than the rim can hold. Lower the peg length, "
            "or raise Rim capture so the rim is taller."
        )
    widest = max(_body_radius_at(d, z0 + lug_h * step / 8.0) for step in range(9))
    bite = max(min(0.6 * d["wall"], d["wall"] - forge_lib.min_wall() - 0.2, 3.0), 0.2)
    lug_r = max(lug_r, 0.5 * (widest - d["r_out"] + bite + 1.0))
    if lug_r - bore_r < _LUG_WALL_MM - 1e-9:  # pragma: no cover - belt and braces
        lug_r = bore_r + _LUG_WALL_MM
    return {"tol": tol, "depth": depth, "lug_r": lug_r, "lug_h": lug_h, "lug_cr": d["r_out"] + lug_r - bite, "lug_z0": z0}


def _build_base(p):
    d = _derived(p)

    part = Pos(0.0, 0.0, d["wall_z0"]) * forge_lib.soft_body(_body_points(d))  # noqa: F405
    part -= revolve(Plane.XZ * Polygon(*_cavity_points(d), align=None), axis=Axis.Z)  # noqa: F405

    # A SOLID plinth, not an openwork one. `arcade_base` was here before --
    # it builds the ring AND cuts a pointed arch between each pair of feet,
    # which is exactly the "small triangle cutouts at the bottom" the artist
    # flagged: the sheet's own base is solid all the way round, with small
    # round toe bumps, not archwork. Root cause was the choice of helper, not
    # a parameter on it -- a solid cylinder plus `feet_ring`'s discrete bumps
    # is the shape the sheet actually draws.
    part += Pos(0.0, 0.0, 0.5 * d["foot_h"]) * Cylinder(radius=d["r_plinth"], height=d["foot_h"])  # noqa: F405
    part += forge_lib.feet_ring(
        d["r_plinth"], min(6.0, 0.45 * d["foot_h"]), int(p["feet_count"]), style="pad"
    )

    # Ear lugs: ribs from the shoulder (just above the neck) up to the rim.
    #
    # A fix was tried here and reverted: widening the WHOLE body (a full
    # revolved shelf, on the body's own axis) to cover the three lugs' reach
    # made things worse, not better -- 2667 mm2 of exposed lug-bottom became
    # 17892 mm2, because the shelf's own top face is then exposed everywhere
    # AROUND its circumference except under the three small lug footprints, a
    # much bigger flat ceiling than the one it was meant to remove. The
    # honest fix (burying each lug's foot in something already that wide, the
    # way the fused sample buries it in the collar band) is not available
    # here, because this design's collar is deliberately a separate,
    # slide-fit piece rather than fused to the body -- see the requirements
    # doc. So this warns rather than passes: each lug's outward-facing
    # bottom disc, beyond the body's own narrower radius at that height,
    # needs supports. `docs/part-authoring.md` calls this shippable ("a part
    # that only warns is shippable; a part that passes is better") and it is
    # a small, local, well-understood warning rather than a structural one.
    ear_z0 = d["wall_z0"] + d["neck_h"] + 2.0
    spec = forge_lib.peg_spec(d=p["peg_diameter"], l=p["peg_length"])
    for angle in _EAR_ANGLES_DEG:
        lp = _round_lug_plan(d, ear_z0, p["peg_diameter"], p["peg_length"], p["ear_clearance_mm"])
        frame = Rot(0.0, 0.0, angle) * Pos(lp["lug_cr"], 0.0, lp["lug_z0"])  # noqa: F405
        part += frame * Pos(0.0, 0.0, 0.5 * lp["lug_h"]) * Cylinder(radius=lp["lug_r"], height=lp["lug_h"])  # noqa: F405
    for angle in _EAR_ANGLES_DEG:
        lp = _round_lug_plan(d, ear_z0, p["peg_diameter"], p["peg_length"], p["ear_clearance_mm"])
        part -= (
            Rot(0.0, 0.0, angle)  # noqa: F405
            * Pos(lp["lug_cr"], 0.0, d["total_h"] + 0.5)  # noqa: F405
            * Rot(180.0, 0.0, 0.0)  # noqa: F405
            * forge_lib.socket_for(spec, lp["tol"])
        )

    # Tail lug: SAME joint type as the ears now -- a round peg with an
    # anti-rotation rib, not a hand-built dovetail. The dovetail was a
    # planning choice, not the sheet's requirement, and its flat flanks were
    # what pinched the tail's narrowing root (0.628 mm thinnest wall after
    # two sizing moves, still short of the 0.8 mm floor). `tail_peg_spec`
    # below is built once and shared with `_build_tail` so the socket here
    # and the peg there can never drift apart.
    tail_z0 = ear_z0
    tail_spec = forge_lib.peg_spec(d=p["tail_peg_diameter_mm"], l=p["tail_peg_length_mm"])
    tlp = _round_lug_plan(d, tail_z0, p["tail_peg_diameter_mm"], p["tail_peg_length_mm"], p["tail_peg_clearance_mm"])
    frame = Rot(0.0, 0.0, _TAIL_ANGLE_DEG) * Pos(tlp["lug_cr"], 0.0, tlp["lug_z0"])  # noqa: F405
    part += frame * Pos(0.0, 0.0, 0.5 * tlp["lug_h"]) * Cylinder(radius=tlp["lug_r"], height=tlp["lug_h"])  # noqa: F405
    part -= (
        Rot(0.0, 0.0, _TAIL_ANGLE_DEG)  # noqa: F405
        * Pos(tlp["lug_cr"], 0.0, d["total_h"] + 0.5)  # noqa: F405
        * Rot(180.0, 0.0, 0.0)  # noqa: F405
        * forge_lib.socket_for(tail_spec, tlp["tol"])
    )

    return part


# --------------------------------------------------------------------------
# The separate pieces
# --------------------------------------------------------------------------


def _build_ear(p):
    return forge_lib.silhouette_part(
        _outline(_EAR_SHAPE, p["ear_width"], p["ear_length"]),
        p["appendage_thickness"],
        rounding=p["appendage_rounding"],
        peg={"d": p["peg_diameter"], "l": p["peg_length"]},
    )


def _build_tail(p):
    """Same mechanism as `_build_ear`: `silhouette_part`'s own `peg=` -- a
    round peg with a keyed anti-rotation rib, embedded and positioned by
    forge_lib's own tested logic (the same logic that already proves out on
    the ear). `tail_peg_diameter_mm` is derived from the root's own measured
    width; see the module docstring for the derivation."""
    return forge_lib.silhouette_part(
        _outline(_TAIL_SHAPE, p["tail_width"], p["tail_length"]),
        p["appendage_thickness"],
        rounding=p["appendage_rounding"],
        peg={"d": p["tail_peg_diameter_mm"], "l": p["tail_peg_length_mm"]},
    )


def _build_collar(p):
    d = _derived(p)
    return forge_lib.leaf_collar(**{
        "ring_radius": d["r_out"],
        "leaf_length": d["collar_length"],
        "leaf_width": 0.7 * d["collar_length"],
        "count": int(p["collar_leaves"]),
        "overlap": p["collar_overlap"],
        "droop_deg": p["collar_droop"],
        "thickness": p["collar_thickness"],
        "jitter": p["collar_jitter"],
        "seed": int(p["collar_seed"]),
        "clearance": p["collar_clearance_mm"],
        "cup": p["collar_cup"],
    })


def build(p):
    """Return whichever piece ``part`` selects, modelled in its print orientation."""
    which = int(round(p["part"]))
    if which == 1:
        return _build_ear(p)
    if which == 2:
        return _build_tail(p)
    if which == 3:
        return _build_collar(p)
    return _build_base(p)
