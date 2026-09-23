"""bench-ear-sculpt -- a mirrored pair of curved Eevee-style ears.

Round 4. Round 3 fixed ``min_wall`` (Spline -> Polyline through the same 12
traced points) but paid for it in shape fidelity: silhouette IoU dropped to
0.646 (floor 0.74) because a 12-point straight-edged polygon reads as an
angular obelisk, not a curved ear, and the rectangular inner-ear cup made it
worse. This round combines both proven halves instead of trading one for the
other:

* the SAME 12-point traced control polygon (``_EAR_OUTLINE``, unchanged) is
  still fit with a periodic Spline -- exactly as round 2 did, which measured
  0.947 IoU -- but that spline is now SAMPLED into ~100 points
  (``_dense_outline``) and the face is built from a Polyline through those
  samples. The built face is smooth (round 2's fidelity) AND is a literal
  polyline (round 3's fix): every width calculation in this file reads the
  same dense points the face is built from, so there is no gap between what
  is measured and what is built, the exact mismatch round 3's own recipe
  (docs/recipes/outline-face-curve-fidelity.md) named.
* the inner-ear cup is no longer a rectangle. ``_cup_outline_mm`` insets the
  real (dense) outline's own local span at each station in the untapered
  base band, so the pocket is a smaller, contour-following copy of the ear's
  own shape -- concave on the inner edge, convex on the outer -- instead of
  four straight sides. Still confined to before ``taper_len``, so
  ``wall = base_thickness - cup_depth`` still holds by construction,
  independent of the pocket's new outline.

Requirements this build carries (artist + benchmark gates):

1. Silhouette from ``ear_outline.json`` -- ``_EAR_OUTLINE`` below, densely
   sampled, not typed from a look at the picture and not left coarse.
2. A real-ear thickness PROFILE, not a uniform slab: ``base_thickness``
   (7-9 mm) at the root, tapering along the length to ``tip_thickness``
   (>= 1.2 mm) starting at ``taper_start``. Unchanged from round 3 -- this
   half already measured a clean min_wall pass and nothing here touches it.
3. A recessed inner-ear cup >= 1.5 mm deep with >= 0.8 mm of wall left under
   it, shaped like an ear (inset outline) rather than a rectangle --
   guaranteed by construction: the cup is confined to the region before
   ``taper_start``, where the shell is still the full, untapered
   ``base_thickness``, so ``wall = base_thickness - cup_depth`` regardless
   of how the inset contour bends.
4. Mirrored placement -- unchanged from round 2/3: ``side`` flips the
   outline itself (a true bilateral mirror, not a copy) and flips the sign
   of the rim yaw. The cup's own centre is now correctly mirrored too (round
   3 computed it in the unmirrored frame and forgot to flip it -- a small,
   latent asymmetry this round removes while rebuilding the cup anyway).
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "side": {
        "value": 1,
        "unit": "count",
        "min": -1,
        "max": 1,
        "step": 2,
        "description": "+1 the left ear (mirrors the outline, yaws +yaw_deg), -1 the right ear",
    },
    "ear_length": {
        "value": 42.0,
        "unit": "mm",
        "min": 20.0,
        "max": 70.0,
        "step": 1.0,
        "description": "Root to tip length of the ear",
    },
    "ear_width": {
        "value": 13.7,
        "unit": "mm",
        "min": 8.0,
        "max": 28.0,
        "step": 0.5,
        "description": "Width of the ear at its widest -- 13.7 mm at the 42 mm default length matches the reference outline's own aspect (0.3256); pushing this far off that ratio trades silhouette fidelity for a chunkier ear",
    },
    "base_thickness": {
        "value": 8.0,
        "unit": "mm",
        "min": 7.0,
        "max": 9.0,
        "step": 0.1,
        "description": "Depth at the root, before any taper -- the real-ear base range",
    },
    "tip_thickness": {
        "value": 1.4,
        "unit": "mm",
        "min": 1.2,
        "max": 3.0,
        "step": 0.1,
        "description": "Depth at the tip once the taper finishes -- the printable floor is 1.2 mm",
    },
    "taper_start": {
        "value": 0.40,
        "unit": "ratio",
        "min": 0.20,
        "max": 0.65,
        "step": 0.05,
        "description": "Fraction of the length (from the root) where the front face starts sloping down to tip_thickness; below this the shell is full base_thickness",
    },
    "back_round": {
        "value": 1.6,
        "unit": "mm",
        "min": 0.3,
        "max": 4.0,
        "step": 0.1,
        "description": "Requested dome radius on the back edge; always clamped to fit the TIP thickness (the thinnest station), so a big ask here just means 'as much as the tip allows'",
    },
    "rim_margin": {
        "value": 0.30,
        "unit": "ratio",
        "min": 0.15,
        "max": 0.45,
        "step": 0.01,
        "description": "How far the inner-ear cup is inset from the ear's width, as a fraction of ear_width",
    },
    "cup_depth": {
        "value": 2.0,
        "unit": "mm",
        "min": 1.5,
        "max": 3.0,
        "step": 0.1,
        "description": "Depth of the recessed inner-ear cup below the front rim -- floor is 1.5 mm",
    },
    "rim_radius": {
        "value": 34.0,
        "unit": "mm",
        "min": 15.0,
        "max": 80.0,
        "step": 1.0,
        "description": "Distance of the ear's root from the bowl's centre (Z) axis",
    },
    "rim_height": {
        "value": 20.0,
        "unit": "mm",
        "min": 0.0,
        "max": 60.0,
        "step": 1.0,
        "description": "Height of the ear's root above Z = 0",
    },
    "yaw_deg": {
        "value": 43.7,
        "unit": "deg",
        "min": 10.0,
        "max": 75.0,
        "step": 0.1,
        "description": "Yaw about Z from +X; mirrored in sign by side, never copied unmirrored",
    },
}

# benchmark/tasks/ear-sculpt/ear_outline.json, key "outline.polygon" (144
# points traced from the artist's sheet), reduced with
# benchmark/silhouette.py simplify_closed(polygon, tolerance=0.012) -> 10
# points, then v shifted by -v_min so the root sits at v = 0 (the reference
# frame already has the tip at v = height = 1). u is unchanged. That 10-point
# trace is measurement, not a guess -- but its root and tip are each a real
# apex, a single point where the outline's own width is exactly zero, and no
# fillet radius fits an apex: even clamped to the tip thickness, a clean
# solid still measured 58 probes under 0.8 mm (thinnest 0.158 mm) right at
# those two corners. Per docs/recipes/constructional-thinness.md ("rebuild
# the generator... a blunt ring or blunted tip, before the walls join"),
# each apex is replaced with a short flat shelf 0.018 of the length in from
# the true corner and about 1.6 mm wide -- deliberately more separation than
# the raw trace implies, which is the whole point of blunting in
# construction rather than shrinking the fillet further and further.
_EAR_OUTLINE = [
    (-0.1366, 0.0180),  # root, inner side (blunted; was the single point idx0)
    (-0.1212, 0.1126),
    (-0.1562, 0.2482),
    (-0.1468, 0.5325),
    (-0.0743, 0.8147),
    (0.0012, 0.9465),
    (-0.0167, 0.9820),  # tip, inner side (blunted; was the single point idx6)
    (0.1183, 0.9820),  # tip, outer side (blunted)
    (0.0734, 0.7386),
    (0.1643, 0.3799),
    (0.1560, 0.1272),
    (0.0134, 0.0180),  # root, outer side (blunted)
]


def _dense_outline(control_points, per_segment=9):
    """Sample the periodic Spline through ``control_points`` into
    ``len(control_points) * per_segment`` polyline points (108 at the
    default 9 -- inside the 80-120 target).

    This is round 2's curve (a periodic Spline through the same 12 traced
    points, measured at 0.947 IoU) with round 3's fix applied on top of it:
    the face gets built from a Polyline through these exact samples, so the
    built geometry is smooth (many short straight segments read as a curve
    at print scale) AND is a literal polyline no width calculation can
    diverge from, because every calculation below reads this same point
    list.
    """
    curve = Spline(*control_points, periodic=True)
    n = len(control_points) * per_segment
    return [(pos.X, pos.Y) for pos in (curve.position_at(i / n) for i in range(n))]


_EAR_OUTLINE_DENSE = _dense_outline(_EAR_OUTLINE)


def _split_dense_chains(dense):
    """Split the closed, densely-sampled loop into two monotonic-in-v
    chains (inner root->tip, outer root->tip) by finding the loop's own
    global v minimum (the root) and maximum (the tip) and walking between
    them -- rather than assuming the dense samples land at any particular
    index relative to the 12 control points. OCCT's own spline
    parameterisation (chord-length, not uniform-by-point) makes that
    assumption unsafe; reading v directly off the sampled geometry is not.
    """
    n = len(dense)
    i_min = min(range(n), key=lambda i: dense[i][1])
    i_max = max(range(n), key=lambda i: dense[i][1])
    ascending = []
    i = i_min
    while True:
        ascending.append(dense[i])
        if i == i_max:
            break
        i = (i + 1) % n
    descending = []
    i = i_max
    while True:
        descending.append(dense[i])
        if i == i_min:
            break
        i = (i + 1) % n
    descending.reverse()
    return ascending, descending


_CHAIN_A, _CHAIN_B = _split_dense_chains(_EAR_OUTLINE_DENSE)  # root->tip, inner and outer


def _interp_chain(chain, v):
    v = min(max(v, chain[0][1]), chain[-1][1])
    for (u0, v0), (u1, v1) in zip(chain, chain[1:]):
        if v0 <= v <= v1:
            t = 0.0 if v1 == v0 else (v - v0) / (v1 - v0)
            return u0 + t * (u1 - u0)
    return chain[-1][0]


def _outline_span(v):
    """(left_u, right_u) of the real outline at length-fraction v."""
    return _interp_chain(_CHAIN_A, v), _interp_chain(_CHAIN_B, v)


def _cup_outline_mm(width, length, taper_len, rim_margin):
    """Closed polygon (mm, unmirrored, root->tip along +Y) for the
    inner-ear cup: the real outline's own local span at each station in the
    band, inset on both sides, instead of a rectangle -- so the pocket
    follows the ear's own curvature (concave inner edge, convex outer edge)
    at every station rather than cutting straight through it.

    Confined to before ``taper_len`` on purpose, exactly as round 3's
    rectangle was: that is where the shell is still full
    ``base_thickness``, so the wall under the cup is just
    ``base_thickness - cup_depth`` by construction, independent of where
    this contour bulges or narrows. A flat 1.0 mm of side wall is kept
    clear of the outline at every station regardless of rim_margin -- a
    request that would eat into it is clamped down instead of honoured.
    Returned UNMIRRORED; the caller mirrors it by ``side`` together with
    the rest of the blade, so the cup's own centre moves with the ear
    instead of staying fixed in the unmirrored frame.
    """
    v_lo = 0.14
    v_hi = taper_len / length - 0.05
    if v_hi - v_lo < 0.05:
        return None
    knots = sorted(
        {v_lo, v_hi}
        | {v for _, v in _CHAIN_A if v_lo <= v <= v_hi}
        | {v for _, v in _CHAIN_B if v_lo <= v <= v_hi}
    )
    side_wall = 1.0
    left_pts, right_pts = [], []
    for v in knots:
        l_u, r_u = _outline_span(v)
        l_mm, r_mm = l_u * width, r_u * width
        half = (r_mm - l_mm) / 2.0
        mid = (r_mm + l_mm) / 2.0
        inset_half = min(half - side_wall, half * max(0.1, 1.0 - 2.0 * rim_margin))
        if inset_half < 0.5:
            return None
        left_pts.append((mid - inset_half, v * length))
        right_pts.append((mid + inset_half, v * length))
    return left_pts + list(reversed(right_pts))


def build(p):
    side = 1 if p["side"] >= 0 else -1
    width = p["ear_width"]
    length = p["ear_length"]
    min_wall = forge_lib.min_wall()

    base_thickness = max(p["base_thickness"], min_wall * 3.0)
    tip_thickness = max(min(p["tip_thickness"], base_thickness - 0.5), min_wall * 1.4)
    taper_start = min(max(p["taper_start"], 0.15), 0.85)
    taper_len = taper_start * length

    outline = [(side * u * width, v * length) for u, v in _EAR_OUTLINE_DENSE]

    # --- local blank: X = width, Y = length, Z = thickness --------------
    # Z = 0 will become the rounded back, Z = base_thickness the front rim
    # before the taper cut narrows it toward the tip.
    # ~100-point Polyline through the sampled spline, not a 12-point one and
    # not a raw Spline: round 3's 12-point polyline matched the width math
    # exactly but read as an angular obelisk (silhouette IoU 0.646, floor
    # 0.74); round 2's raw Spline read as an ear (0.947) but overshot the
    # width math near the blunted corners (min_wall FAIL, 0.145 mm at the
    # root). Sampling the same spline densely and building the face from
    # THOSE points gets both: smooth at print scale, and exactly equal to
    # what _outline_span/_cup_outline_mm below measure, because they read
    # this identical point list.
    face = Plane.XY * make_face(Polyline(*outline, close=True))
    blade = extrude(face, amount=base_thickness)

    # Round the back into a dome -- clamped to what the TIP thickness (the
    # thinnest station once tapered) can actually carry, never the base.
    # A radius sized off the base alone is exactly the six-attempt failure
    # docs/recipes/constructional-thinness.md records: the root is already
    # narrow before it is thin, and a fillet asking for more room than the
    # thinnest station has leaves a knife edge wherever it doesn't fit.
    back_face = blade.faces().sort_by(Axis.Z)[0]
    radius = min(p["back_round"], 0.45 * tip_thickness, 0.48 * base_thickness)
    for _attempt in range(5):
        try:
            blade = fillet(back_face.edges(), radius=radius)
            break
        except Exception:
            radius *= 0.6
            if radius < 0.15:
                break

    # --- taper the FRONT down to tip_thickness past taper_start ----------
    # A trapezoid in the Y-Z plane (Y = length, Z = thickness), extruded the
    # full width and subtracted. Flat at base_thickness up to taper_len,
    # then a straight slope down to tip_thickness at the tip -- the back is
    # never touched, so it stays domed all the way to the tip.
    # The flat corner is nudged 0.05 mm ABOVE base_thickness rather than
    # sitting exactly on it -- an exactly coincident cutter face and blade
    # face is a tangent boolean, and OCC left a non-manifold seam and a
    # phantom near-zero wall reading right at that seam (measured: 1
    # non-manifold edge and a 0.006 mm probe, both at the same point). The
    # nudge costs a fraction of a millimetre of delayed taper start and nets
    # a clean solid.
    big_x = width * 1.5 + 5.0
    top = base_thickness + 50.0
    trap = Plane.YZ * make_face(
        Polyline(
            (taper_len, base_thickness + 0.05),
            (taper_len, top),
            (length + 2.0, top),
            (length + 2.0, tip_thickness),
            close=True,
        )
    )
    cutter = Pos(-big_x, 0.0, 0.0) * extrude(trap, amount=2.0 * big_x)
    blade -= cutter

    # --- recessed inner-ear cup, confined to the untapered base region ---
    # base_thickness is unchanged there, so wall = base_thickness - cup_depth
    # by construction -- no per-station measurement needed to guarantee it.
    # An inset copy of the real outline, not a rectangle: _cup_outline_mm
    # returns it unmirrored, so it is mirrored by `side` here together with
    # the rest of the blade -- round 3 built this in the unmirrored frame
    # and never flipped it, a small latent off-centre error on the mirrored
    # ear (side=-1) this round removes while rebuilding the cup anyway.
    cup_depth = min(p["cup_depth"], base_thickness - 0.8 - 0.6)
    rim_margin = min(max(p["rim_margin"], 0.15), 0.45)
    cup_poly = _cup_outline_mm(width, length, taper_len, rim_margin)
    if cup_poly is not None and cup_depth >= 1.0:
        cup_poly = [(side * x, y) for x, y in cup_poly]
        pocket_face = Plane.XY * make_face(Polyline(*cup_poly, close=True))
        try:
            pocket_face = fillet(pocket_face.vertices(), radius=0.4)
        except Exception:
            pass
        pocket = extrude(pocket_face, amount=cup_depth + 0.6)
        blade -= Pos(0.0, 0.0, base_thickness - cup_depth) * pocket

    # --- place on the bowl's rim -----------------------------------------
    # local X (width) -> tangent, local Y (length) -> +Z (up),
    # local Z (thickness) -> outward radial. One rigid transform does both
    # the rim position and the yaw; side flips its sign, never its shape.
    theta = math.radians(side * p["yaw_deg"])
    tangent = (-math.sin(theta), math.cos(theta), 0.0)
    radial = (math.cos(theta), math.sin(theta), 0.0)
    base = (
        p["rim_radius"] * radial[0],
        p["rim_radius"] * radial[1],
        p["rim_height"],
    )
    frame = Plane(origin=base, x_dir=tangent, z_dir=radial)
    return frame * blade
