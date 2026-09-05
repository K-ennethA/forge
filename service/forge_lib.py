"""``forge_lib`` -- the appendage-slot library PartForge scripts can import.

Decorative pieces (ears, feet, fins, a tail) bolt onto a base part through a
*keyed peg*: a cylinder with a flat rib down one side so the appendage cannot
spin in its socket.  The base gets the matching negative subtracted out of it.
Because both come from the same spec, changing the peg once changes every
socket.

Usage from a PartForge script
-----------------------------

``forge_lib`` is available to a script the same way ``build123d`` is: import it::

    from build123d import *
    import forge_lib

    PARAMS = {
        "peg_diameter": {"value": 6.0, "unit": "mm", "min": 3.0, "max": 20.0},
        "peg_length":   {"value": 8.0, "unit": "mm", "min": 3.0, "max": 40.0},
        "fit":          {"value": 0.2, "unit": "mm", "min": 0.0, "max": 1.0},
    }

    def build(p):
        spec = forge_lib.peg_spec(d=p["peg_diameter"], l=p["peg_length"])
        body = Box(30, 30, 10)
        # The socket: a negative, positioned where the appendage plugs in.
        socket = Pos(0, 0, 10) * Rot(180, 0, 0) * forge_lib.socket_for(spec, p["fit"])
        return body - socket

The script's namespace also has ``forge_lib`` bound already, so the ``import``
line is a convenience for editors rather than a requirement.

Geometry conventions
--------------------
Both :func:`peg` and :func:`socket_for` are built **axis along +Z, base on the
Z=0 plane, centred on the origin in XY**, with the key rib on the +X side.  Move
them with ``Pos``/``Rot`` like any other build123d solid.  A peg is a positive
to union onto (or return as) the appendage; a socket is a negative to subtract
from the base.

Tolerance is applied to the socket only: the peg is nominal, and the socket is
grown by ``tolerance`` on the cylinder's radius and on both flanks and the tip
of the key, so the printed pair are ``tolerance`` apart across every mating
face.  ``printer.json``'s ``tolerances.slide_fit`` (0.2 mm) is the default and
the right starting point for something meant to come apart again;
``press_fit`` (0.1 mm) for something glued in once.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

#: Default keyed peg: 6 mm across, 8 mm long.
DEFAULT_PEG_DIAMETER_MM = 6.0
DEFAULT_PEG_LENGTH_MM = 8.0

#: Key rib proportions, as fractions of the peg diameter.
KEY_WIDTH_RATIO = 0.35
KEY_HEIGHT_RATIO = 0.20

#: Default socket clearance -- ``printer.json``'s ``tolerances.slide_fit``.
DEFAULT_TOLERANCE_MM = 0.2

#: How much deeper than the peg a socket is bored, so the peg seats on its
#: shoulder rather than bottoming out on a slightly-over-extruded floor.
DEFAULT_DEPTH_EXTRA_MM = 0.5


def peg_spec(
    d: float = DEFAULT_PEG_DIAMETER_MM,
    l: float = DEFAULT_PEG_LENGTH_MM,
    key: bool = True,
    key_width: Optional[float] = None,
    key_height: Optional[float] = None,
    chamfer: Optional[float] = None,
) -> Dict[str, Any]:
    """The single source of truth for one peg/socket pair.

    Keep this in a variable and hand it to both :func:`peg` and
    :func:`socket_for`; that is what makes "change the peg diameter once and
    every socket updates" true rather than aspirational.
    """
    diameter = float(d)
    length = float(l)
    if not math.isfinite(diameter) or diameter <= 0.0:
        raise ValueError(f"peg diameter must be positive, got {d!r}")
    if not math.isfinite(length) or length <= 0.0:
        raise ValueError(f"peg length must be positive, got {l!r}")

    width = float(key_width) if key_width else KEY_WIDTH_RATIO * diameter
    height = float(key_height) if key_height else KEY_HEIGHT_RATIO * diameter
    lead = DEFAULT_TOLERANCE_MM if chamfer is None else float(chamfer)
    lead = max(0.0, min(lead, diameter * 0.2, length * 0.2))

    if key and width >= diameter:
        raise ValueError(
            f"key_width ({width:g} mm) must be narrower than the peg "
            f"({diameter:g} mm) or the key stops being a key"
        )

    return {
        "d": diameter,
        "l": length,
        "key": bool(key),
        "key_width": width,
        "key_height": height,
        "chamfer": lead,
    }


def _as_spec(peg_params: Any, **overrides: Any) -> Dict[str, Any]:
    """Accept a spec dict, a peg solid built by :func:`peg`, or bare kwargs."""
    given = {key: value for key, value in overrides.items() if value is not None}
    if peg_params is None:
        return peg_spec(**given)
    if isinstance(peg_params, Mapping):
        merged = dict(peg_params)
        merged.update(given)
        return peg_spec(**merged)
    if isinstance(peg_params, (int, float)) and not isinstance(peg_params, bool):
        # peg(6, 8) -- the positional form from the contract.
        return peg_spec(d=float(peg_params), **given)
    attached = getattr(peg_params, "forge_peg_spec", None)
    if isinstance(attached, Mapping):
        merged = dict(attached)
        merged.update(given)
        return peg_spec(**merged)
    raise TypeError(
        "expected a peg spec dict (from forge_lib.peg_spec), a solid returned by "
        f"forge_lib.peg(), or a diameter; got {type(peg_params).__name__}"
    )


def peg(
    d: Any = DEFAULT_PEG_DIAMETER_MM,
    l: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """A keyed peg: cylinder plus an anti-rotation rib, base on Z=0.

    ``peg(6, 8)`` and ``peg(spec)`` both work; the second is the one to use when
    a socket has to match.
    """
    from build123d import Axis, Box, Cylinder, GeomType, Pos, chamfer  # noqa: PLC0415

    spec = _as_spec(d, l=l, **kwargs)
    radius = spec["d"] / 2.0
    length = spec["l"]

    body = Pos(0.0, 0.0, length / 2.0) * Cylinder(radius, length)

    lead = spec["chamfer"]
    if lead > 0.01:
        try:
            edges = body.edges().filter_by(GeomType.CIRCLE).sort_by(Axis.Z)
            body = chamfer(edges[-1], length=lead)
        except Exception:  # noqa: BLE001 - a square-topped peg still works
            lead = 0.0

    if spec["key"]:
        height = spec["key_height"]
        width = spec["key_width"]
        # The rib overlaps the cylinder wall so the fuse is a single solid, and
        # stops short of the tip so the lead-in chamfer stays a clean cone.
        rib_length = max(length - max(lead, 0.0) - 0.2, length * 0.5)
        rib = (
            Pos(radius, 0.0, rib_length / 2.0)
            * Box(2.0 * height, width, rib_length)
        )
        body = body + rib

    try:
        body.forge_peg_spec = dict(spec)  # so socket_for(peg(...)) can work
    except Exception:  # noqa: BLE001 - not every build123d version allows this
        pass
    return body


def socket_for(
    peg_params: Any,
    tolerance: float = DEFAULT_TOLERANCE_MM,
    depth_extra: float = DEFAULT_DEPTH_EXTRA_MM,
    mouth_chamfer: Optional[float] = None,
) -> Any:
    """The negative that mates with :func:`peg`, base on Z=0, opening downward.

    Subtract it from the base part with its Z=0 face on the surface the
    appendage plugs into.  ``tolerance`` is the clearance on every mating face.
    """
    from build123d import Box, Cylinder, Pos  # noqa: PLC0415

    spec = _as_spec(peg_params)
    clearance = float(tolerance)
    if not math.isfinite(clearance) or clearance < 0.0:
        raise ValueError(f"tolerance must be a finite non-negative number, got {tolerance!r}")

    depth = spec["l"] + float(depth_extra)
    radius = spec["d"] / 2.0 + clearance

    cavity = Pos(0.0, 0.0, depth / 2.0) * Cylinder(radius, depth)

    if spec["key"]:
        height = spec["key_height"]
        width = spec["key_width"] + 2.0 * clearance
        # Same rib box as the peg, grown by the clearance on the tip; the inner
        # end stays buried in the cylinder so the slot is one cavity.
        slot_depth = 2.0 * height + clearance
        cavity = cavity + (
            Pos(spec["d"] / 2.0 + clearance / 2.0, 0.0, depth / 2.0)
            * Box(slot_depth, width, depth)
        )

    lead = DEFAULT_TOLERANCE_MM if mouth_chamfer is None else float(mouth_chamfer)
    lead = max(0.0, min(lead, radius * 0.3))
    if lead > 0.01:
        try:
            from build123d import Cone  # noqa: PLC0415

            cavity = cavity + (
                Pos(0.0, 0.0, lead / 2.0)
                * Cone(bottom_radius=radius + lead, top_radius=radius, height=lead)
            )
        except Exception:  # noqa: BLE001 - a square-mouthed socket still works
            pass

    try:
        cavity.forge_socket_spec = {"tolerance": clearance, **spec}
    except Exception:  # noqa: BLE001
        pass
    return cavity


__all__ = [
    "DEFAULT_DEPTH_EXTRA_MM",
    "DEFAULT_PEG_DIAMETER_MM",
    "DEFAULT_PEG_LENGTH_MM",
    "DEFAULT_TOLERANCE_MM",
    "KEY_HEIGHT_RATIO",
    "KEY_WIDTH_RATIO",
    "peg",
    "peg_spec",
    "socket_for",
]
