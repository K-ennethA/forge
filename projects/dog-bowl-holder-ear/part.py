"""Eevee-style dog-bowl holder -- the EAR, part 2 of the reference sheet.

One ear.  Print it twice; the shape is symmetric about its own mid-plane, so
the same file serves the left and the right.

Modelled LYING FLAT on the bed, which is its print orientation: the leaf
outline is drawn in XY and extruded upward in Z, so every side face is
vertical and the only horizontal surfaces are the top and the bottom.  That is
the cheapest, strongest way to print a thin blade, and it needs no supports.

The peg at the root is ``forge_lib.peg`` built from the same spec the base's
sockets are cut with, so the two mate on the printer's slide fit.  Match
``peg_diameter`` / ``peg_length`` here to ``socket_diameter`` / ``socket_depth``
on the base and the fit is guaranteed.

Design notes worth knowing before turning knobs
-----------------------------------------------
* The tip is a short flat land, not a point.  A knife edge in plan view is a
  wall a fraction of a millimetre thick at the nozzle, and it fails min_wall.
* The inner-ear detail is a subtracted pocket, never an added rib, and its
  depth is capped against the blade thickness so it can never break through.
* ``ear_thickness`` is a floor, not a fact: it is raised if it is too thin to
  bury the peg with a wall either side.
"""

import math

from build123d import *  # noqa: F403 - the build123d house style

import forge_lib  # noqa: F401 - already bound in the service namespace

PARAMS = {
    "ear_length": {
        "value": 70.0,
        "unit": "mm",
        "min": 25.0,
        "max": 130.0,
        "step": 1.0,
        "description": "Root to tip, not counting the peg",
    },
    "ear_width": {
        "value": 30.0,
        "unit": "mm",
        "min": 12.0,
        "max": 60.0,
        "step": 0.5,
        "description": "Width at the widest point of the leaf",
    },
    "ear_thickness": {
        "value": 9.0,
        "unit": "mm",
        "min": 3.0,
        "max": 16.0,
        "step": 0.5,
        "description": "Blade thickness; raised automatically to bury the peg",
    },
    "widest_at": {
        "value": 0.30,
        "unit": "ratio",
        "min": 0.15,
        "max": 0.6,
        "step": 0.01,
        "description": "How far up the ear its widest point sits; low = a lance, high = a leaf",
    },
    "peg_diameter": {
        "value": 6.0,
        "unit": "mm",
        "min": 3.0,
        "max": 10.0,
        "step": 0.5,
        "description": "Must match the base's Socket diameter",
    },
    "peg_length": {
        "value": 10.0,
        "unit": "mm",
        "min": 3.0,
        "max": 16.0,
        "step": 0.5,
        "description": "How far the peg sticks out; must match the base's Socket depth",
    },
    "inner_depth": {
        "value": 1.5,
        "unit": "mm",
        "min": 0.0,
        "max": 5.0,
        "step": 0.1,
        "description": "Depth of the inner-ear recess; 0 leaves the face flat",
    },
    "inner_margin": {
        "value": 3.5,
        "unit": "mm",
        "min": 1.0,
        "max": 12.0,
        "step": 0.5,
        "description": "Border of solid ear left around the inner recess",
    },
}

_PEG_EMBED_MM = 3.0       # how far the peg is buried in the root, so the
                          # union has volume to bite on rather than a
                          # coplanar face
_OUTLINE_STEPS = 48       # points per side of the leaf


def _half_width(t, root_hw, max_hw, tip_hw, t_max):
    """Half the ear's width at fraction ``t`` along its length.

    Two cosine eases meeting at ``t_max``, so the widest point is smooth and
    the outline is never wider than ``max_hw`` -- which is what keeps the
    polygon from folding back on itself at any slider combination.
    """
    if t <= t_max:
        u = t / t_max
        return root_hw + (max_hw - root_hw) * (0.5 - 0.5 * math.cos(math.pi * u))
    u = (t - t_max) / (1.0 - t_max)
    return tip_hw + (max_hw - tip_hw) * (0.5 + 0.5 * math.cos(math.pi * u))


def _derived(p):
    """Feature sizes first, body dimensions derived from them."""
    min_wall = forge_lib.min_wall()

    peg_d = p["peg_diameter"]
    # The blade has to be thick enough to bury the peg with a wall each side.
    thickness = max(p["ear_thickness"], peg_d + 2.0 * forge_lib.min_feature() + 0.2)

    length = p["ear_length"]
    max_hw = 0.5 * p["ear_width"]

    # The root has to carry the peg; the tip finishes on a land, never a point.
    tip_hw = max(0.75, 0.5 * forge_lib.min_feature())
    root_hw = max(0.5 * peg_d + 2.0, 0.30 * max_hw)
    root_hw = min(root_hw, 0.90 * max_hw)
    if root_hw >= max_hw or max_hw <= tip_hw + 0.5:
        raise ValueError(
            "The ear is too narrow for its peg. Raise Ear width above "
            f"{2.0 * (0.5 * peg_d + 2.5):.0f} mm, or lower Peg diameter."
        )
    if length < 3.0 * max_hw * 0.5:
        # A leaf shorter than it is wide still builds; nothing to clamp.
        pass

    return {
        "thickness": thickness,
        "length": length,
        "max_hw": max_hw,
        "root_hw": root_hw,
        "tip_hw": tip_hw,
        "t_max": p["widest_at"],
        "peg_d": peg_d,
        "peg_l": p["peg_length"],
    }


def _outline(d, inset=0.0, t0=0.0, t1=1.0, floor_hw=0.6):
    """The leaf as a closed list of (x, y) points, optionally shrunk inward."""
    left, right = [], []
    widest = 0.0
    for i in range(_OUTLINE_STEPS + 1):
        t = t0 + (t1 - t0) * i / _OUTLINE_STEPS
        hw = _half_width(t, d["root_hw"], d["max_hw"], d["tip_hw"], d["t_max"]) - inset
        if hw < floor_hw:
            hw = floor_hw
        else:
            widest = max(widest, hw)
        y = t * d["length"]
        left.append((-hw, y))
        right.append((hw, y))
    # Wound counter-clockwise (up the +x side, back down the -x side) so the
    # face normal points at +Z and extrude() grows the blade UPWARD off the bed.
    return right + list(reversed(left)), widest


def build(p):
    d = _derived(p)
    min_wall = forge_lib.min_wall()

    pts, _ = _outline(d)
    ear = extrude(Plane.XY * Polygon(*pts, align=None), amount=d["thickness"])  # noqa: F405

    # --- inner-ear recess: subtracted from the top face, depth capped -----
    depth = min(
        p["inner_depth"],
        0.40 * d["thickness"],
        d["thickness"] - min_wall - 0.1,
    )
    # Stop the recess where the blade gets too narrow to carry it: past that
    # point the strip left between the pocket and the outside edge drops under
    # the minimum feature size and min_wall warns.  Truncating is the fix --
    # clamping the pocket narrower only moves the thin strip, it does not
    # remove it.
    margin = p["inner_margin"]
    floor_hw = forge_lib.min_feature()
    live = [
        i / 200.0
        for i in range(201)
        if _half_width(i / 200.0, d["root_hw"], d["max_hw"], d["tip_hw"], d["t_max"])
        - margin
        >= floor_hw
    ]
    if not live or live[-1] - live[0] < 0.05:
        return _with_peg(ear, d)

    inner_pts, widest = _outline(
        d, inset=margin, t0=live[0], t1=live[-1], floor_hw=floor_hw
    )
    if depth >= 0.3 and widest >= 1.5:
        pocket = extrude(  # noqa: F405
            Plane.XY * Polygon(*inner_pts, align=None), amount=depth + 0.5  # noqa: F405
        )
        ear -= Pos(0.0, 0.0, d["thickness"] - depth) * pocket  # noqa: F405

    return _with_peg(ear, d)


def _with_peg(ear, d):
    """The peg, lying in the blade's own plane and pointing out of the root."""
    spec = forge_lib.peg_spec(
        d=d["peg_d"], l=d["peg_l"] + _PEG_EMBED_MM, key=False
    )
    # peg() grows along +Z from Z=0; a +90 deg turn about X swings that to -Y,
    # and the +Y offset buries _PEG_EMBED_MM of it inside the root.
    return ear + (
        Pos(0.0, _PEG_EMBED_MM, 0.5 * d["thickness"])  # noqa: F405
        * Rot(90.0, 0.0, 0.0)  # noqa: F405
        * forge_lib.peg(spec)
    )
