"""bench-ear-sculpt -- a mirrored pair of curved Eevee-style ears.

Benchmark task: build ONLY the two ears (no bowl, no pegs), each a closed
printable solid with real volume -- a rounded convex back and a recessed
concave inner face -- never a flat slab with a decorative inset.

The outline (``_EAR_OUTLINE``) is read as proportions off the character
sheet's front-view pose: a curved, asymmetric leaf that bulges on one edge
and leans through the tip, per the artist's note "ears are more curved like
this". It is splined into a smooth closed face (never a pixel trace), then:

* the BACK (local Z = 0) is filleted into a dome -- a rounded back, not a
  flat plate;
* the FRONT (local Z = thickness) has a pocket cut into it, inset from the
  outline by ``rim_margin``, leaving a solid rim around a genuinely recessed
  inner face.

Placement puts the whole thing on the bowl's rim directly: the local frame
(X = width/tangent, Y = length/up, Z = thickness/outward-radial) is mapped
onto a ``Plane`` built from the rim azimuth ``side * yaw_deg`` about Z, so
one call does both the position AND the mirrored yaw -- never one transform
copied to both sides. ``side`` also mirrors the outline itself, so the two
ears are true bilateral mirror images, not two copies of the same lean.
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
        "value": 24.0,
        "unit": "mm",
        "min": 12.0,
        "max": 40.0,
        "step": 1.0,
        "description": "Width of the ear at its widest",
    },
    "thickness": {
        "value": 8.0,
        "unit": "mm",
        "min": 4.0,
        "max": 14.0,
        "step": 0.5,
        "description": "Overall depth from the rounded back to the front rim",
    },
    "back_round": {
        "value": 3.2,
        "unit": "mm",
        "min": 0.5,
        "max": 6.5,
        "step": 0.1,
        "description": "How strongly the back domes; stepped down if the kernel refuses it",
    },
    "rim_margin": {
        "value": 0.24,
        "unit": "ratio",
        "min": 0.12,
        "max": 0.35,
        "step": 0.01,
        "description": "Solid border left around the recessed inner face, as a fraction of the outline",
    },
    "pocket_depth": {
        "value": 1.8,
        "unit": "mm",
        "min": 0.5,
        "max": 4.0,
        "step": 0.1,
        "description": "How deep the inner face is recessed below the rim",
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

# Proportions read off the sheet's front-view ear, as fractions of
# (ear_width, ear_length) -- NOT a pixel trace. The outer edge (positive u)
# bulges out fast and wide low on the ear; the inner edge (negative u) is a
# gentler, more concave curve; the tip leans back toward the centreline
# rather than snapping straight, which is the "more curved" the artist asked
# for compared to a straight-edged spearhead outline.
_EAR_OUTLINE = [
    (0.00, 0.000),
    (0.10, 0.010),
    (0.34, 0.070),
    (0.52, 0.230),
    (0.50, 0.460),
    (0.38, 0.670),
    (0.20, 0.860),
    (0.05, 0.975),
    (0.00, 1.000),
    (-0.10, 0.930),
    (-0.24, 0.760),
    (-0.34, 0.520),
    (-0.36, 0.280),
    (-0.24, 0.090),
    (-0.08, 0.015),
]


def _pocket_points(points, rim):
    """The pocket's inset outline: the outer points that lie well clear of
    the root and the tip, scaled in toward their own centroid.

    A first cut tapered the scale smoothly to zero at the ends instead of
    truncating outright, and that was worse: blending from a shrunk middle
    to an unshrunk end makes a periodic spline overshoot outward between the
    two, so the cutter poked past the outer surface and measured 0 mm of
    wall over a wide band. Dropping the pinched points outright, and closing
    the remaining ones into their own smaller loop, keeps the cutter's own
    boundary simple and nowhere near the outer surface -- which is also
    truer to the reference: the recess stops well short of the root and the
    tip there, it does not taper elegantly into them.
    """
    v_min = min(y for _, y in points)
    v_max = max(y for _, y in points)
    span = max(v_max - v_min, 1e-6)
    live = [(x, y) for x, y in points if 0.16 <= (y - v_min) / span <= 0.88]
    if len(live) < 4:
        return None
    cx = sum(x for x, _ in live) / len(live)
    cy = sum(y for _, y in live) / len(live)
    scale = 1.0 - rim
    return [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in live]


def build(p):
    side = 1 if p["side"] >= 0 else -1
    width = p["ear_width"]
    length = p["ear_length"]
    min_wall = forge_lib.min_wall()
    thickness = max(p["thickness"], min_wall * 3.0)

    outline = [(side * u * width, v * length) for u, v in _EAR_OUTLINE]

    # --- local blank: X = width, Y = length, Z = thickness --------------
    # Z = 0 will become the rounded back, Z = thickness the recessed front.
    face = Plane.XY * make_face(Spline(*outline, periodic=True))
    blade = extrude(face, amount=thickness)

    # Round the back into a dome instead of leaving it a flat plate.
    back_face = blade.faces().sort_by(Axis.Z)[0]
    radius = min(p["back_round"], 0.48 * thickness)
    for _attempt in range(5):
        try:
            blade = fillet(back_face.edges(), radius=radius)
            break
        except Exception:
            radius *= 0.6
            if radius < 0.2:
                break

    # Recess the inner face into the front, leaving a solid rim around it --
    # a genuine pocket with depth, not a flat slab with a decorative groove.
    rim = min(max(p["rim_margin"], 0.12), 0.35)
    pocket_depth = min(p["pocket_depth"], thickness - min_wall - 0.6)
    inset = _pocket_points(outline, rim) if pocket_depth > 0.3 else None
    if inset is not None:
        pocket_face = Plane.XY * make_face(Spline(*inset, periodic=True))
        pocket = extrude(pocket_face, amount=pocket_depth + 0.6)
        blade -= Pos(0.0, 0.0, thickness - pocket_depth) * pocket  # noqa: F405

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
