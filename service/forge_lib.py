"""``forge_lib`` -- the printability library PartForge scripts compose parts from.

Two halves, one idea: *never hand the kernel a shape you have not already proved
printable*.

**Appendage slots** (:func:`peg`, :func:`socket_for`) join two printed pieces.
Decorative pieces (ears, feet, fins, a tail) bolt onto a base part through a
*keyed peg*: a cylinder with a flat rib down one side so the appendage cannot
spin in its socket.  The base gets the matching negative subtracted out of it.
Because both come from the same spec, changing the peg once changes every
socket.

**Printability-guaranteed features** (:func:`blunted_taper`, :func:`flared_lip`,
:func:`textured_band`, :func:`feet_ring`, :func:`arcade_base`,
:func:`magnet_pocket`, :func:`shell_box`, :func:`wall_safe_shell`,
:func:`screw_boss`) are the shapes a part is actually made of.  Each one takes
explicit millimetres plus an optional ``printer`` profile (the built-in Centauri
Carbon default when omitted), clamps itself to something the printer can make,
and reports every clamp it applied through a matching ``*_plan()`` function.
None of them can silently emit a sub-minimum feature: they clamp and tell you,
or they raise :class:`PrintabilityError` with a plain sentence.

The rule that motivated the whole thing: **a taper must never end in a knife
edge.**  A cone that runs into a flat face at an acute angle leaves a feather
edge thinner than the nozzle for its first fraction of a millimetre, which is a
failed ``min_wall`` check on a part that looks fine on screen.  Every taper in
here ends in a straight land instead -- see :func:`blunted_taper`.

Authoring guide: ``docs/part-authoring.md``.

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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ForgeError, ScriptError
from .printer import DEFAULT_PROFILE, normalize_printer
from .printer import tolerance as _printer_tolerance


class PrintabilityError(ScriptError):
    """A helper was asked for something the printer cannot make.

    It is a :class:`~service.errors.ForgeError` (so the service recognises it)
    and a :class:`~service.errors.ScriptError`, which is what makes it an HTTP
    400 -- the caller can fix it by changing a parameter -- rather than a 500.
    The message is always a plain sentence naming the number that is wrong and
    the number it would have to be.
    """


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


# ==========================================================================
# Printability core
#
# Everything below shares one contract:
#
# * dimensions are explicit millimetres, never ratios of something implicit;
# * ``printer`` is an optional partial profile merged over the built-in
#   Centauri Carbon default (``service/printer.py``), so a helper called with
#   no printer still knows what 0.8 mm means;
# * every helper has a ``*_plan()`` twin that returns the numbers it will
#   actually build with, including a ``clamped`` list naming every value the
#   printability rules moved and why;
# * nothing is ever silently made smaller than the printer can hold.  A helper
#   clamps (and says so in the plan) or raises :class:`PrintabilityError`.
# ==========================================================================

#: Degrees of headroom kept under ``max_unsupported_overhang_deg``.  The check
#: measures triangles, and a tessellated cone's facets sit a fraction of a
#: degree off the ideal surface; a helper that aimed at exactly the limit would
#: land on the wrong side of it about half the time.
OVERHANG_SAFETY_DEG = 2.0

#: A taper shorter than this stops reading as a taper, so it is the floor the
#: sloped section is never clamped below.  Matches the bowl-holder's proven
#: ``_MIN_FLARE_TAPER_MM``.
MIN_TAPER_HEIGHT_MM = 0.6

#: How far a blind pocket's mouth pokes out past the face it is cut into, so a
#: subtraction is never a coplanar-face boolean.
MOUTH_OVERSHOOT_MM = 0.2

#: Fraction of the circumferential pitch one texture element may occupy; the
#: rest is the flat land between elements.  From the bowl-holder's flute band.
DEFAULT_PITCH_FRACTION = 0.78

#: Relief shallower than this is below one layer of texture; suppress it.
MIN_RELIEF_DEPTH_MM = 0.15

#: Most cutters one :func:`textured_band` call will emit.
MAX_BAND_CUTTERS = 480


# --------------------------------------------------------------------------
# Profile access
# --------------------------------------------------------------------------


def profile(printer: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The resolved printer profile: *printer* merged over the built-in default.

    Handy in a script that wants to quote a number in an error message::

        prof = forge_lib.profile()
        raise ValueError(f"wall must clear {prof['min_wall_thickness']} mm")
    """
    return normalize_printer(printer)


def min_wall(printer: Optional[Mapping[str, Any]] = None) -> float:
    """``printer.min_wall_thickness`` -- 0.8 mm on the Centauri Carbon."""
    return float(profile(printer)["min_wall_thickness"])


def min_feature(printer: Optional[Mapping[str, Any]] = None) -> float:
    """``printer.min_feature_size`` -- 1.0 mm on the Centauri Carbon."""
    return float(profile(printer)["min_feature_size"])


def max_overhang_deg(printer: Optional[Mapping[str, Any]] = None) -> float:
    """``printer.max_unsupported_overhang_deg`` -- 50 deg on the Centauri Carbon.

    Angle **from vertical**, the slicer's convention and ``checks.py``'s: 0 is a
    wall parallel to the build direction, 90 is a flat ceiling.
    """
    return float(profile(printer)["max_unsupported_overhang_deg"])


def fit_tolerance(
    name: str = "slide_fit", printer: Optional[Mapping[str, Any]] = None
) -> float:
    """One named clearance from ``printer.tolerances``.

    ``press_fit`` 0.1, ``slide_fit`` 0.2, ``loose_fit`` 0.3,
    ``magnet_pocket_extra`` 0.05 on the built-in profile.
    """
    default = float(DEFAULT_PROFILE["tolerances"].get(name, 0.1))
    return float(_printer_tolerance(profile(printer), name, default))


def min_land(
    min_land_mm: Optional[float] = None,
    printer: Optional[Mapping[str, Any]] = None,
) -> float:
    """The narrowest flat land any taper in this library is allowed to end on.

    ``max(min_land_mm, printer.min_wall_thickness)``.  ``min_land_mm`` defaults
    to the profile's ``min_feature_size`` (1.0 mm), which is the value the
    bowl-holder base ring proved on a real print: a rim narrower than the
    minimum feature is not something the slicer can put a perimeter on, it is a
    feather edge.  Passing a *smaller* ``min_land_mm`` cannot take the land
    below the minimum wall -- the floor is a floor.
    """
    prof = profile(printer)
    floor = float(prof["min_wall_thickness"])
    if min_land_mm is None:
        requested = float(prof["min_feature_size"])
    else:
        requested = _finite_positive(min_land_mm, "min_land_mm", allow_zero=True)
    return max(requested, floor)


def max_flare_for(
    taper_height: float, printer: Optional[Mapping[str, Any]] = None
) -> float:
    """How far a surface may lean out over *taper_height* and still self-support.

    ``taper_height * tan(max_unsupported_overhang_deg - OVERHANG_SAFETY_DEG)``.
    """
    height = _finite_positive(taper_height, "taper_height")
    limit = max(max_overhang_deg(printer) - OVERHANG_SAFETY_DEG, 1.0)
    return height * math.tan(math.radians(min(limit, 89.0)))


# --------------------------------------------------------------------------
# Small validation helpers
# --------------------------------------------------------------------------


def _finite_positive(value: Any, label: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrintabilityError(f"{label} must be a number in millimetres, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise PrintabilityError(f"{label} must be a finite number, got {value!r}")
    if number < 0.0 or (number == 0.0 and not allow_zero):
        raise PrintabilityError(
            f"{label} must be {'zero or more' if allow_zero else 'greater than zero'}, "
            f"got {number:g} mm"
        )
    return number


def _positive_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrintabilityError(f"{label} must be a whole number, got {value!r}")
    count = int(round(float(value)))
    if count < 1:
        raise PrintabilityError(f"{label} must be at least 1, got {value!r}")
    return count


def _compound(solids: Sequence[Any]) -> Any:
    """Gather cutters into one Compound so a caller subtracts them in one boolean."""
    from build123d import Compound  # noqa: PLC0415

    return Compound(children=list(solids))


def _revolve_profile(points: Sequence[Tuple[float, float]]) -> Any:
    """Revolve a ``(radius, z)`` polygon about Z, the bowl-holder's own idiom."""
    from build123d import Axis, Plane, Polygon, revolve  # noqa: PLC0415

    sketch = Plane.XZ * Polygon(*points, align=None)
    return revolve(sketch, axis=Axis.Z)


# --------------------------------------------------------------------------
# blunted_taper -- the knife-edge rule, as a function
# --------------------------------------------------------------------------


def blunted_taper_plan(
    bottom_r: float,
    top_r: float,
    height: float,
    *,
    role: str = "add",
    printer: Optional[Mapping[str, Any]] = None,
    min_land_mm: Optional[float] = None,
    bore_r: float = 0.0,
    taper_height: Optional[float] = None,
    support_free: bool = True,
) -> Dict[str, Any]:
    """Work out the printable frustum :func:`blunted_taper` will build.

    See :func:`blunted_taper` for what every field means and what is guaranteed.
    """
    role = str(role).strip().lower()
    if role not in ("add", "cut"):
        raise PrintabilityError(f"role must be 'add' or 'cut', got {role!r}")

    r_bottom = _finite_positive(bottom_r, "bottom_r", allow_zero=True)
    r_top = _finite_positive(top_r, "top_r", allow_zero=True)
    total_h = _finite_positive(height, "height")
    bore = _finite_positive(bore_r, "bore_r", allow_zero=True)

    prof = profile(printer)
    land = min_land(min_land_mm, prof)
    wall_floor = float(prof["min_wall_thickness"])
    feature_floor = float(prof["min_feature_size"])
    clamped: List[str] = []

    if bore > 0.0 and bore >= max(r_bottom, r_top):
        raise PrintabilityError(
            f"bore_r ({bore:g} mm) is at least as big as the taper "
            f"({max(r_bottom, r_top):g} mm), so there would be no material left"
        )

    # ---- rule 1: the thin end lands on a flat >= land wide -----------------
    # For a solid (role "add") the thin end is a real face and it must be a land,
    # not a point.  For a cut the thin end is the narrowest the hole ever gets,
    # and a hole below the minimum feature does not print at all.
    if role == "add":
        floor_r = (bore + land) if bore > 0.0 else (land / 2.0)
        what = "annulus" if bore > 0.0 else "disc"
        reason = f"thin end would be narrower than the {land:g} mm minimum land"
    else:
        floor_r = feature_floor / 2.0
        what = "hole"
        reason = f"narrowest point would be under the {feature_floor:g} mm minimum feature"

    if min(r_bottom, r_top) < floor_r - 1e-9:
        if r_bottom <= r_top:
            clamped.append(
                f"bottom_r {r_bottom:g} -> {floor_r:g} mm: {reason} ({what})"
            )
            r_bottom = floor_r
        else:
            clamped.append(f"top_r {r_top:g} -> {floor_r:g} mm: {reason} ({what})")
            r_top = floor_r

    # A wall that survives the thin-end clamp still has to be a wall everywhere.
    if bore > 0.0 and min(r_bottom, r_top) - bore < wall_floor - 1e-9:
        raise PrintabilityError(
            f"a bore of {bore:g} mm leaves only "
            f"{min(r_bottom, r_top) - bore:.3f} mm of wall at the thin end; the "
            f"printer needs {wall_floor:g} mm"
        )

    # ---- rule 2: the land goes on the end whose edge would be acute --------
    # Adding material: the WIDE end is the acute one -- the cone leans inward as
    # it leaves that face, so the rim is a wedge lying on the bed.
    # Cutting material: the NARROW end is the acute one -- the hole widens as it
    # leaves that face, so the material around the mouth is a wedge.
    if abs(r_top - r_bottom) <= 1e-9:
        land_at_bottom = True
        land_needed = 0.0
    else:
        bottom_is_wide = r_bottom > r_top
        land_at_bottom = bottom_is_wide if role == "add" else (not bottom_is_wide)
        land_needed = land

    # ---- how the height splits between land and slope ---------------------
    if land_needed <= 0.0:
        land_h = 0.0
        taper_h = total_h
    else:
        if total_h < land_needed + MIN_TAPER_HEIGHT_MM - 1e-9:
            raise PrintabilityError(
                f"height {total_h:g} mm cannot carry a blunted taper: it needs a "
                f"{land_needed:g} mm straight land plus at least "
                f"{MIN_TAPER_HEIGHT_MM:g} mm of slope "
                f"({land_needed + MIN_TAPER_HEIGHT_MM:g} mm in total)"
            )
        if taper_height is None:
            taper_h = total_h - land_needed
        else:
            taper_h = _finite_positive(taper_height, "taper_height")
            if taper_h > total_h - land_needed + 1e-9:
                clamped.append(
                    f"taper_height {taper_h:g} -> {total_h - land_needed:g} mm: the "
                    f"{land_needed:g} mm land has to fit in the same {total_h:g} mm"
                )
                taper_h = total_h - land_needed
            if taper_h < MIN_TAPER_HEIGHT_MM:
                clamped.append(
                    f"taper_height {taper_h:g} -> {MIN_TAPER_HEIGHT_MM:g} mm: "
                    "anything shorter stops being a taper"
                )
                taper_h = MIN_TAPER_HEIGHT_MM
        land_h = total_h - taper_h

    # ---- rule 3: the slope self-supports ----------------------------------
    # Adding material, an outward lean going up is the overhang.  Cutting it,
    # the overhang is the hole that widens going DOWN: the material above it is
    # a ceiling.  Same cone, opposite sign -- this is the one thing scripts get
    # backwards, so the role is an argument rather than a guess.
    limit = max(float(prof["max_unsupported_overhang_deg"]) - OVERHANG_SAFETY_DEG, 1.0)
    allowed = taper_h * math.tan(math.radians(min(limit, 89.0)))
    overhanging = (r_top > r_bottom) if role == "add" else (r_bottom > r_top)
    delta = abs(r_top - r_bottom)

    if support_free and overhanging and delta > allowed + 1e-9:
        if role == "add":
            clamped.append(
                f"top_r {r_top:g} -> {r_bottom + allowed:g} mm: a {delta:g} mm flare "
                f"over {taper_h:g} mm leans "
                f"{math.degrees(math.atan2(delta, taper_h)):.1f} deg from vertical, "
                f"past the {prof['max_unsupported_overhang_deg']:g} deg limit"
            )
            r_top = r_bottom + allowed
        else:
            clamped.append(
                f"bottom_r {r_bottom:g} -> {r_top + allowed:g} mm: a hole that widens "
                f"{delta:g} mm downward over {taper_h:g} mm puts a "
                f"{math.degrees(math.atan2(delta, taper_h)):.1f} deg ceiling over "
                f"itself, past the {prof['max_unsupported_overhang_deg']:g} deg limit"
            )
            r_bottom = r_top + allowed
        delta = abs(r_top - r_bottom)
        overhanging = delta > 1e-9 and overhanging

    angle = math.degrees(math.atan2(delta, taper_h)) if taper_h > 0 else 0.0

    return {
        "role": role,
        "bottom_r_mm": round(r_bottom, 5),
        "top_r_mm": round(r_top, 5),
        "height_mm": round(total_h, 5),
        "bore_r_mm": round(bore, 5),
        "land_mm": round(land_h, 5),
        "land_at": "bottom" if land_at_bottom else "top",
        "taper_height_mm": round(taper_h, 5),
        "angle_from_vertical_deg": round(angle, 3),
        "min_land_mm": round(land, 5),
        "thin_end_land_mm": round(
            (min(r_bottom, r_top) - bore) if bore > 0.0 else 2.0 * min(r_bottom, r_top),
            5,
        ),
        "support_free": bool(not overhanging or delta <= allowed + 1e-9),
        "overhang_limit_deg": float(prof["max_unsupported_overhang_deg"]),
        "clamped": clamped,
    }


def blunted_taper(
    bottom_r: float,
    top_r: float,
    height: float,
    *,
    role: str = "add",
    printer: Optional[Mapping[str, Any]] = None,
    min_land_mm: Optional[float] = None,
    bore_r: float = 0.0,
    taper_height: Optional[float] = None,
    support_free: bool = True,
) -> Any:
    """A cone frustum that cannot end in a knife edge.  Axis +Z, base on Z = 0.

    This is the fix for the exact failure this library was written for: a taper
    modelled straight from radius A to radius B meets the flat face at one end
    at an acute dihedral, and the sliver of material along that rim is thinner
    than the nozzle.  It looks like a clean chamfer on screen and comes back as
    a ``min_wall`` failure.

    Guarantees
    ----------
    1. **The thin end is a land, never a point.**  With ``role="add"`` the
       narrow face is at least ``max(min_land_mm, printer.min_wall_thickness)``
       across -- a disc for a solid taper, an annulus when ``bore_r`` is set.
       With ``role="cut"`` the narrowest part of the hole is at least the
       printer's minimum feature.
    2. **The acute end is blunted with a straight land** of that same width,
       measured along Z.  Adding material that is the *wide* end (the rim that
       would otherwise feather out onto the bed); cutting it, the *narrow* end
       (the mouth the hole widens away from).
    3. **The slope self-supports** when ``support_free`` (default): the lean is
       clamped to ``max_unsupported_overhang_deg`` minus a 2 deg tessellation
       margin.  ``role`` decides which direction is the overhang -- flaring
       upward for solid material, widening downward for a hole.
    4. With ``bore_r`` set, the wall between the bore and the cone is at least
       the printer's minimum wall at every height, or it raises.

    Anything clamped is listed in ``blunted_taper_plan(...)["clamped"]`` with
    the old value, the new value and the reason.  Nothing is clamped silently.

    Examples
    --------
    A flared foot that lands blunt on the bed::

        foot = forge_lib.blunted_taper(12.0, 9.0, 6.0)

    A countersink -- a *cut*, so the safe direction is the other one.  Give it a
    long land so the same cutter drills the through hole as well, and let the
    cone finish above the face so the boolean is never coplanar::

        cutter = Pos(x, y, -1.0) * forge_lib.blunted_taper(
            2.1, 3.6, thickness + 1.0 + 1.5, role="cut", taper_height=1.5)
    """
    plan = blunted_taper_plan(
        bottom_r,
        top_r,
        height,
        role=role,
        printer=printer,
        min_land_mm=min_land_mm,
        bore_r=bore_r,
        taper_height=taper_height,
        support_free=support_free,
    )

    r_bottom = plan["bottom_r_mm"]
    r_top = plan["top_r_mm"]
    total_h = plan["height_mm"]
    bore = plan["bore_r_mm"]
    land_h = plan["land_mm"]

    points: List[Tuple[float, float]] = [(bore, 0.0), (r_bottom, 0.0)]
    if land_h > 1e-9:
        if plan["land_at"] == "bottom":
            points.append((r_bottom, land_h))
            points.append((r_top, total_h))
        else:
            points.append((r_top, total_h - land_h))
            points.append((r_top, total_h))
    else:
        points.append((r_top, total_h))
    points.append((bore, total_h))

    solid = _revolve_profile(points)
    _attach(solid, "forge_taper_plan", plan)
    return solid


def _attach(shape: Any, name: str, value: Any) -> None:
    """Best-effort: hang the plan off the solid so a caller can read it back."""
    try:
        setattr(shape, name, value)
    except Exception:  # noqa: BLE001 - not every build123d version allows this
        pass


# --------------------------------------------------------------------------
# flared_lip
# --------------------------------------------------------------------------


def flared_lip_plan(
    inner_r: float,
    wall: float,
    height: float,
    flare: float,
    *,
    direction: str = "down",
    printer: Optional[Mapping[str, Any]] = None,
    min_land_mm: Optional[float] = None,
    taper_height: Optional[float] = None,
    support_free: bool = True,
) -> Dict[str, Any]:
    """The numbers :func:`flared_lip` will build with."""
    direction = str(direction).strip().lower()
    if direction not in ("down", "up"):
        raise PrintabilityError(f"direction must be 'down' or 'up', got {direction!r}")

    r_in = _finite_positive(inner_r, "inner_r", allow_zero=True)
    total_h = _finite_positive(height, "height")
    spread = _finite_positive(flare, "flare", allow_zero=True)

    prof = profile(printer)
    wall_floor = float(prof["min_wall_thickness"])
    land = min_land(min_land_mm, prof)
    clamped: List[str] = []

    thickness = _finite_positive(wall, "wall")
    if thickness < wall_floor - 1e-9:
        clamped.append(
            f"wall {thickness:g} -> {wall_floor:g} mm: the printer's minimum wall"
        )
        thickness = wall_floor
    r_out = r_in + thickness

    # The straight part of the collar, above (or below) the flared zone.
    if spread <= 1e-9:
        return {
            "direction": direction,
            "inner_r_mm": round(r_in, 5),
            "outer_r_mm": round(r_out, 5),
            "wall_mm": round(thickness, 5),
            "height_mm": round(total_h, 5),
            "flare_mm": 0.0,
            "land_mm": 0.0,
            "taper_height_mm": 0.0,
            "angle_from_vertical_deg": 0.0,
            "support_free": True,
            "min_land_mm": round(land, 5),
            "clamped": clamped,
        }

    if total_h < land + MIN_TAPER_HEIGHT_MM - 1e-9:
        raise PrintabilityError(
            f"height {total_h:g} mm cannot carry a flare: it needs a {land:g} mm "
            f"blunt land plus at least {MIN_TAPER_HEIGHT_MM:g} mm of taper"
        )

    if taper_height is None:
        taper_h = max(spread, MIN_TAPER_HEIGHT_MM)
        if direction == "up" and support_free:
            # Give the flare enough run so it never needs supports.
            needed = spread / math.tan(
                math.radians(
                    min(
                        max(
                            float(prof["max_unsupported_overhang_deg"])
                            - OVERHANG_SAFETY_DEG,
                            1.0,
                        ),
                        89.0,
                    )
                )
            )
            taper_h = max(taper_h, needed)
    else:
        taper_h = _finite_positive(taper_height, "taper_height")

    available = total_h - land
    if taper_h > available + 1e-9:
        clamped.append(
            f"taper_height {taper_h:g} -> {available:g} mm: the {land:g} mm land and "
            f"the taper share the same {total_h:g} mm"
        )
        taper_h = available
    taper_h = max(taper_h, MIN_TAPER_HEIGHT_MM)

    limit = max(float(prof["max_unsupported_overhang_deg"]) - OVERHANG_SAFETY_DEG, 1.0)
    allowed = taper_h * math.tan(math.radians(min(limit, 89.0)))
    overhanging = direction == "up"
    if support_free and overhanging and spread > allowed + 1e-9:
        clamped.append(
            f"flare {spread:g} -> {allowed:g} mm: {spread:g} mm of outward lean over "
            f"{taper_h:g} mm is "
            f"{math.degrees(math.atan2(spread, taper_h)):.1f} deg from vertical, past "
            f"the {prof['max_unsupported_overhang_deg']:g} deg limit"
        )
        spread = allowed

    return {
        "direction": direction,
        "inner_r_mm": round(r_in, 5),
        "outer_r_mm": round(r_out, 5),
        "wall_mm": round(thickness, 5),
        "height_mm": round(total_h, 5),
        "flare_mm": round(spread, 5),
        "land_mm": round(land, 5),
        "taper_height_mm": round(taper_h, 5),
        "angle_from_vertical_deg": round(math.degrees(math.atan2(spread, taper_h)), 3),
        "support_free": bool(not overhanging or spread <= allowed + 1e-9),
        "min_land_mm": round(land, 5),
        "clamped": clamped,
    }


def flared_lip(
    inner_r: float,
    wall: float,
    height: float,
    flare: float,
    *,
    direction: str = "down",
    printer: Optional[Mapping[str, Any]] = None,
    min_land_mm: Optional[float] = None,
    taper_height: Optional[float] = None,
    support_free: bool = True,
) -> Any:
    """A collar whose outside flares out at one end and lands blunt.  Base Z = 0.

    The bowl-holder's flared foot, generalised: a tube of ``wall`` thickness
    around a bore of ``inner_r``, whose outer surface swells by ``flare`` at the
    bottom (``direction="down"`` -- a foot) or at the top (``"up"`` -- a rim).

    Guarantees
    ----------
    * The flared end finishes with a **straight vertical land** at least
      ``max(min_land_mm, printer.min_wall_thickness)`` tall, so the widest rim
      meets the end face at a right angle instead of feathering out.
    * ``wall`` is clamped up to the printer's minimum wall if it was under it.
      The wall only ever gets thicker along the flare, so minimum wall holds
      over the whole collar.
    * ``direction="up"`` is an overhang, so with ``support_free`` (default) the
      taper is given enough height -- or the flare is reduced -- to stay inside
      ``max_unsupported_overhang_deg``.  ``direction="down"`` faces upward and
      is support-free by construction.

    ``flared_lip_plan(...)`` returns the same numbers plus ``clamped``.
    """
    plan = flared_lip_plan(
        inner_r,
        wall,
        height,
        flare,
        direction=direction,
        printer=printer,
        min_land_mm=min_land_mm,
        taper_height=taper_height,
        support_free=support_free,
    )

    r_in = plan["inner_r_mm"]
    r_out = plan["outer_r_mm"]
    total_h = plan["height_mm"]
    spread = plan["flare_mm"]
    land_h = plan["land_mm"]
    taper_h = plan["taper_height_mm"]

    if spread <= 1e-9:
        points = [(r_in, 0.0), (r_out, 0.0), (r_out, total_h), (r_in, total_h)]
    elif plan["direction"] == "down":
        points = [
            (r_in, 0.0),
            (r_out + spread, 0.0),
            (r_out + spread, land_h),
            (r_out, land_h + taper_h),
            (r_out, total_h),
            (r_in, total_h),
        ]
    else:
        points = [
            (r_in, 0.0),
            (r_out, 0.0),
            (r_out, total_h - land_h - taper_h),
            (r_out + spread, total_h - land_h),
            (r_out + spread, total_h),
            (r_in, total_h),
        ]

    solid = _revolve_profile(points)
    _attach(solid, "forge_lip_plan", plan)
    return solid


# --------------------------------------------------------------------------
# textured_band -- subtracted relief only
# --------------------------------------------------------------------------

_BAND_STYLES = ("flute", "scallop", "chevron")


def textured_band_plan(
    solid_face_radius: float,
    height: float,
    count: int,
    depth: float,
    style: str = "flute",
    *,
    wall: Optional[float] = None,
    printer: Optional[Mapping[str, Any]] = None,
    pitch_fraction: float = DEFAULT_PITCH_FRACTION,
    support_free: bool = True,
    chevron_deg: float = 45.0,
) -> Dict[str, Any]:
    """The clamped relief :func:`textured_band` will cut."""
    style = str(style).strip().lower()
    if style not in _BAND_STYLES:
        raise PrintabilityError(
            f"style must be one of {', '.join(_BAND_STYLES)}, got {style!r}"
        )

    radius = _finite_positive(solid_face_radius, "solid_face_radius")
    band_h = _finite_positive(height, "height")
    requested_depth = _finite_positive(depth, "depth")
    n = _positive_count(count, "count")

    prof = profile(printer)
    wall_floor = float(prof["min_wall_thickness"])
    clamped: List[str] = []

    if not 0.05 <= float(pitch_fraction) <= 0.95:
        raise PrintabilityError(
            f"pitch_fraction must sit between 0.05 and 0.95, got {pitch_fraction!r}"
        )

    pitch = 2.0 * math.pi * radius / n
    surface_width = float(pitch_fraction) * pitch

    # The proven clamp chain from the bowl-holder's fluted band, plus the
    # absolute one: whatever else happens, the wall that is left is a wall.
    effective = requested_depth
    reasons: List[str] = []
    if wall is not None:
        thickness = _finite_positive(wall, "wall")
        if thickness <= wall_floor + 1e-9:
            return _empty_band_plan(
                style,
                radius,
                band_h,
                n,
                requested_depth,
                pitch,
                surface_width,
                prof,
                clamped
                + [
                    f"suppressed: a {thickness:g} mm wall is already at the "
                    f"{wall_floor:g} mm minimum, so there is nothing to carve"
                ],
            )
        if 0.40 * thickness < effective:
            effective = 0.40 * thickness
            reasons.append(f"40% of the {thickness:g} mm wall")
        if thickness - wall_floor < effective:
            effective = thickness - wall_floor
            reasons.append(f"leaving the {wall_floor:g} mm minimum wall behind")
    if 0.45 * surface_width < effective:
        effective = 0.45 * surface_width
        reasons.append(
            f"45% of the {surface_width:.2f} mm surface width (a circle cannot bite "
            "deeper than it is wide)"
        )
    if 0.45 * radius < effective:
        effective = 0.45 * radius
        reasons.append(f"45% of the {radius:g} mm radius")

    if effective < requested_depth - 1e-9:
        clamped.append(
            f"depth {requested_depth:g} -> {effective:.3f} mm: "
            + "; ".join(reasons)
        )

    if effective < MIN_RELIEF_DEPTH_MM:
        return _empty_band_plan(
            style,
            radius,
            band_h,
            n,
            requested_depth,
            pitch,
            surface_width,
            prof,
            clamped
            + [
                f"suppressed: {effective:.3f} mm of relief is under the "
                f"{MIN_RELIEF_DEPTH_MM:g} mm a layer can show"
            ],
        )

    # Cutter radius solved from the wanted surface width:
    #   width = 2 * sqrt(R**2 - (R - d)**2)  ->  R = (width**2/4 + d**2) / (2d)
    cutter_radius = (surface_width * surface_width / 4.0 + effective * effective) / (
        2.0 * effective
    )

    limit = max(float(prof["max_unsupported_overhang_deg"]) - OVERHANG_SAFETY_DEG, 1.0)
    tan_limit = math.tan(math.radians(min(limit, 89.0)))

    plan: Dict[str, Any] = {
        "style": style,
        "radius_mm": round(radius, 5),
        "height_mm": round(band_h, 5),
        "count": n,
        "requested_depth_mm": round(requested_depth, 5),
        "depth_mm": round(effective, 5),
        "pitch_mm": round(pitch, 5),
        "surface_width_mm": round(surface_width, 5),
        "cutter_radius_mm": round(cutter_radius, 5),
        "remaining_wall_mm": (
            round(float(wall) - effective, 5) if wall is not None else None
        ),
        "min_wall_mm": wall_floor,
        "suppressed": False,
        "clamped": clamped,
    }

    if style == "flute":
        # Flat cutter ends would leave a horizontal ceiling at the top of every
        # groove.  Cone the ends off instead, at the overhang limit.
        runout = cutter_radius / tan_limit if support_free else 0.0
        max_runout = 0.35 * band_h
        if runout > max_runout:
            runout = max_runout
            clamped.append(
                f"flute run-out {cutter_radius / tan_limit:.2f} -> {runout:.2f} mm: "
                f"a {band_h:g} mm band has no room for a full-angle run-out, so the "
                "groove ends stay slightly over the overhang limit"
            )
        plan["runout_mm"] = round(runout, 5)
        plan["cutter_count"] = n
        plan["support_free"] = bool(
            runout > 0.0 and runout >= cutter_radius / tan_limit - 1e-6
        )
    elif style == "scallop":
        rows = max(1, int(band_h // max(pitch, 1e-6)))
        while n * rows > MAX_BAND_CUTTERS and rows > 1:
            rows -= 1
        plan["rows"] = rows
        plan["cutter_count"] = n * rows
        # A dimple's crown is horizontal for a vanishing area; it bridges, but
        # the facet check counts it.
        plan["support_free"] = False
    else:  # chevron
        tilt = float(chevron_deg)
        low = 90.0 - limit
        if support_free:
            if tilt > limit:
                clamped.append(
                    f"chevron_deg {tilt:g} -> {limit:g}: the groove flanks would lean "
                    f"past the {prof['max_unsupported_overhang_deg']:g} deg limit"
                )
                tilt = limit
            if tilt < low:
                clamped.append(
                    f"chevron_deg {tilt:g} -> {low:g}: the groove ends would be flatter "
                    f"than the {prof['max_unsupported_overhang_deg']:g} deg limit"
                )
                tilt = low
        plan["chevron_deg"] = round(tilt, 3)
        plan["cutter_count"] = 2 * n
        plan["support_free"] = bool(low - 1e-9 <= tilt <= limit + 1e-9)

    return plan


def _empty_band_plan(
    style: str,
    radius: float,
    band_h: float,
    count: int,
    requested_depth: float,
    pitch: float,
    surface_width: float,
    prof: Mapping[str, Any],
    clamped: List[str],
) -> Dict[str, Any]:
    return {
        "style": style,
        "radius_mm": round(radius, 5),
        "height_mm": round(band_h, 5),
        "count": count,
        "requested_depth_mm": round(requested_depth, 5),
        "depth_mm": 0.0,
        "pitch_mm": round(pitch, 5),
        "surface_width_mm": round(surface_width, 5),
        "cutter_radius_mm": 0.0,
        "remaining_wall_mm": None,
        "min_wall_mm": float(prof["min_wall_thickness"]),
        "cutter_count": 0,
        "support_free": True,
        "suppressed": True,
        "clamped": clamped,
    }


def textured_band(
    solid_face_radius: float,
    height: float,
    count: int,
    depth: float,
    style: str = "flute",
    *,
    wall: Optional[float] = None,
    printer: Optional[Mapping[str, Any]] = None,
    z_bottom: float = 0.0,
    pitch_fraction: float = DEFAULT_PITCH_FRACTION,
    support_free: bool = True,
    chevron_deg: float = 45.0,
) -> Any:
    """Decorative relief for a cylindrical face, **as a negative to subtract**.

    Returns a ``Compound`` of cutters sitting around the Z axis, spanning
    ``z_bottom`` to ``z_bottom + height``, tangent to a cylinder of
    ``solid_face_radius``::

        part -= forge_lib.textured_band(r_out, 24.0, 24, 1.6,
                                        wall=wall, z_bottom=18.0)

    Relief on a printed part is **always subtracted, never added**: an added rib
    is a free-standing feature that has to be at least a nozzle wide on its own,
    while a groove only has to leave enough wall behind -- which is a condition
    this helper can actually enforce.

    Guarantees
    ----------
    * Depth is clamped to ``min(depth, 0.40 * wall, wall - min_wall,
      0.45 * surface_width, 0.45 * radius)`` -- the bowl-holder's proven chain
      plus the absolute one, so the wall left behind is never under the
      printer's minimum.  Pass ``wall`` or the wall-based clamps cannot apply.
    * The cutter radius is solved from the circumferential pitch, so the
      scallops always leave a flat land between them; a very fine ``count``
      forces a shallower cut rather than cutters that merge into each other.
    * When the clamps leave less than 0.15 mm of relief the band is suppressed
      entirely and the returned compound is empty -- ``part - empty`` is a
      no-op, so the call site needs no branch.
    * ``style="flute"`` cones off both ends of every groove at the overhang
      limit, so the band does not hang a ceiling over itself.  ``"chevron"``
      clamps its tilt into the window where both the flanks and the ends stay
      inside the limit.  ``"scallop"`` (spherical dimples) is the one style
      whose crown the facet check still counts -- it bridges in practice.

    Read ``textured_band_plan(...)`` for the effective depth and every clamp.
    """
    plan = textured_band_plan(
        solid_face_radius,
        height,
        count,
        depth,
        style,
        wall=wall,
        printer=printer,
        pitch_fraction=pitch_fraction,
        support_free=support_free,
        chevron_deg=chevron_deg,
    )
    if plan["suppressed"]:
        band = _compound([])
        _attach(band, "forge_band_plan", plan)
        return band

    from build123d import Box, Cylinder, Pos, Rot, Sphere  # noqa: PLC0415

    n = int(plan["count"])
    radius = plan["radius_mm"]
    band_h = plan["height_mm"]
    effective = plan["depth_mm"]
    cutter_radius = plan["cutter_radius_mm"]
    z0 = float(z_bottom)
    z1 = z0 + band_h
    z_mid = (z0 + z1) / 2.0

    cutters: List[Any] = []

    if plan["style"] == "flute":
        centre_radius = radius + cutter_radius - effective
        runout = float(plan["runout_mm"])
        for index in range(n):
            angle = 2.0 * math.pi * index / n
            x = centre_radius * math.cos(angle)
            y = centre_radius * math.sin(angle)
            if runout > 1e-6:
                cutters.append(
                    Pos(x, y, 0.0)
                    * _revolve_profile(
                        [
                            (0.0, z0),
                            (cutter_radius, z0 + runout),
                            (cutter_radius, z1 - runout),
                            (0.0, z1),
                        ]
                    )
                )
            else:
                cutters.append(
                    Pos(x, y, z_mid) * Cylinder(radius=cutter_radius, height=band_h)
                )
    elif plan["style"] == "scallop":
        rows = int(plan["rows"])
        centre_radius = radius + cutter_radius - effective
        row_step = band_h / rows
        for row in range(rows):
            z = z0 + row_step * (row + 0.5)
            offset = math.pi / n if row % 2 else 0.0
            for index in range(n):
                angle = 2.0 * math.pi * index / n + offset
                cutters.append(
                    Pos(
                        centre_radius * math.cos(angle),
                        centre_radius * math.sin(angle),
                        z,
                    )
                    * Sphere(radius=cutter_radius)
                )
    else:  # chevron
        tilt = float(plan["chevron_deg"])
        width = plan["surface_width_mm"]
        thickness = effective + 2.0
        centre_radius = radius - effective + thickness / 2.0
        rad = math.radians(tilt)
        # Half the band per limb, shortened so the tilted corners stay inside it.
        limb = max(
            (band_h / 2.0 - width * math.sin(rad)) / max(math.cos(rad), 1e-6), 0.2
        )
        for index in range(n):
            angle_deg = 360.0 * index / n
            for sign, z_centre in ((1.0, z0 + band_h * 0.25), (-1.0, z0 + band_h * 0.75)):
                cutters.append(
                    Rot(0.0, 0.0, angle_deg)
                    * Pos(centre_radius, 0.0, z_centre)
                    * Rot(sign * tilt, 0.0, 0.0)
                    * Box(thickness, width, limb)
                )

    band = _compound(cutters)
    _attach(band, "forge_band_plan", plan)
    return band


# --------------------------------------------------------------------------
# feet_ring / arcade_base -- support-free base features
# --------------------------------------------------------------------------

_BASE_STYLES = ("pier", "pad")


def feet_ring_plan(
    outer_r: float,
    height: float,
    count: int,
    style: str = "pad",
    *,
    printer: Optional[Mapping[str, Any]] = None,
    foot_size: Optional[float] = None,
    foot_depth: Optional[float] = None,
    inset: float = 0.0,
    chamfer: bool = True,
) -> Dict[str, Any]:
    """The feet :func:`feet_ring` will place."""
    style = str(style).strip().lower()
    if style not in _BASE_STYLES:
        raise PrintabilityError(
            f"style must be one of {', '.join(_BASE_STYLES)}, got {style!r}"
        )

    radius = _finite_positive(outer_r, "outer_r")
    foot_h = _finite_positive(height, "height")
    n = _positive_count(count, "count")
    pull_in = _finite_positive(inset, "inset", allow_zero=True)

    prof = profile(printer)
    feature = float(prof["min_feature_size"])
    clamped: List[str] = []

    ring_radius = radius - pull_in
    if ring_radius <= feature:
        raise PrintabilityError(
            f"inset {pull_in:g} mm pulls the feet to a {ring_radius:g} mm circle, which "
            f"is under the {feature:g} mm minimum feature"
        )

    pitch = 2.0 * math.pi * ring_radius / n
    size = float(foot_size) if foot_size is not None else max(0.55 * pitch, feature)
    size = _finite_positive(size, "foot_size")

    if size < feature - 1e-9:
        clamped.append(
            f"foot_size {size:g} -> {feature:g} mm: the printer's minimum feature"
        )
        size = feature

    # Feet have to stay separate feet: leave at least a minimum feature of air.
    max_size = pitch - feature
    if max_size < feature:
        raise PrintabilityError(
            f"{n} feet on a {2.0 * ring_radius:g} mm circle leaves {pitch:.2f} mm of "
            f"pitch each, too little for a {feature:g} mm foot plus a {feature:g} mm "
            "gap; use fewer feet"
        )
    if size > max_size:
        clamped.append(
            f"foot_size {size:g} -> {max_size:.3f} mm: {n} feet need a {feature:g} mm "
            f"gap between them on a {pitch:.2f} mm pitch"
        )
        size = max_size

    # How far a pier reaches inward.  A pad is round, so its depth is its size.
    if style == "pad":
        depth = size
    else:
        depth = float(foot_depth) if foot_depth is not None else size
        depth = _finite_positive(depth, "foot_depth")
        if depth < feature - 1e-9:
            clamped.append(
                f"foot_depth {depth:g} -> {feature:g} mm: the printer's minimum feature"
            )
            depth = feature
        if depth > ring_radius:
            clamped.append(
                f"foot_depth {depth:g} -> {ring_radius:g} mm: a pier cannot reach past "
                "the axis"
            )
            depth = ring_radius

    # A bottom chamfer relieves elephant's foot.  45 deg is inside every sane
    # overhang limit; if the profile is stricter than that, drop the chamfer
    # rather than print a lip the check will flag.
    limit = max(float(prof["max_unsupported_overhang_deg"]) - OVERHANG_SAFETY_DEG, 1.0)
    chamfer_mm = 0.0
    if chamfer and limit >= 45.0:
        chamfer_mm = min(0.4, 0.25 * size, 0.25 * foot_h)
        if chamfer_mm < 0.1:
            chamfer_mm = 0.0

    return {
        "style": style,
        "outer_r_mm": round(radius, 5),
        "ring_r_mm": round(ring_radius, 5),
        "height_mm": round(foot_h, 5),
        "count": n,
        "foot_size_mm": round(size, 5),
        "foot_depth_mm": round(depth, 5),
        "pitch_mm": round(pitch, 5),
        "gap_mm": round(pitch - size, 5),
        "chamfer_mm": round(chamfer_mm, 5),
        "chamfer_deg": 45.0 if chamfer_mm else 0.0,
        "min_feature_mm": feature,
        "support_free": True,
        "clamped": clamped,
    }


def feet_ring(
    outer_r: float,
    height: float,
    count: int,
    style: str = "pad",
    *,
    printer: Optional[Mapping[str, Any]] = None,
    foot_size: Optional[float] = None,
    foot_depth: Optional[float] = None,
    inset: float = 0.0,
    chamfer: bool = True,
) -> Any:
    """``count`` feet on a circle, as a positive to union on.  Base on Z = 0.

    ``style="pad"`` gives round pucks whose outer edge touches ``outer_r``;
    ``"pier"`` gives radial rectangular blocks ``foot_size`` wide and
    ``foot_depth`` deep whose outer corners touch it.  Both are straight
    vertical prisms standing on the bed, which is what makes them support-free
    with nothing to prove.

    Guarantees
    ----------
    * Every foot is at least the printer's minimum feature across, and at least
      a minimum feature of air is left between neighbours -- the size is clamped
      to fit the pitch, and an impossible ``count`` raises rather than producing
      feet that fuse into a solid ring.
    * The optional bottom chamfer is 45 deg, inside the overhang limit, and is
      dropped entirely when it would be under 0.1 mm.

    ``feet_ring_plan(...)`` reports the placed size, the gap and every clamp.
    """
    plan = feet_ring_plan(
        outer_r,
        height,
        count,
        style,
        printer=printer,
        foot_size=foot_size,
        foot_depth=foot_depth,
        inset=inset,
        chamfer=chamfer,
    )

    from build123d import Box, Cylinder, Plane, Pos, Rot, chamfer as _chamfer  # noqa: PLC0415

    n = int(plan["count"])
    size = plan["foot_size_mm"]
    depth = plan["foot_depth_mm"]
    foot_h = plan["height_mm"]
    chamfer_mm = plan["chamfer_mm"]
    ring_radius = plan["ring_r_mm"]

    if plan["style"] == "pad":
        centre = ring_radius - size / 2.0
        if chamfer_mm > 0.0:
            prototype = _revolve_profile(
                [
                    (0.0, 0.0),
                    (size / 2.0 - chamfer_mm, 0.0),
                    (size / 2.0, chamfer_mm),
                    (size / 2.0, foot_h),
                    (0.0, foot_h),
                ]
            )
        else:
            prototype = Pos(0.0, 0.0, foot_h / 2.0) * Cylinder(
                radius=size / 2.0, height=foot_h
            )
    else:
        # Put the pier's outer *corners* on the circle, not its centre line, so
        # a ring of piers never reaches past ``outer_r``.
        outer_face = math.sqrt(max(ring_radius**2 - (size / 2.0) ** 2, 0.0))
        centre = outer_face - depth / 2.0
        prototype = Pos(0.0, 0.0, foot_h / 2.0) * Box(depth, size, foot_h)
        if chamfer_mm > 0.0:
            try:
                bottom = prototype.edges().group_by(Plane.XY.z_dir)[0]
                prototype = _chamfer(bottom, length=chamfer_mm)
            except Exception:  # noqa: BLE001 - a square-footed pier still prints
                plan["chamfer_mm"] = 0.0
                plan["chamfer_deg"] = 0.0

    feet: List[Any] = [
        Rot(0.0, 0.0, 360.0 * index / n) * Pos(centre, 0.0, 0.0) * prototype
        for index in range(n)
    ]

    ring = feet[0]
    for foot in feet[1:]:
        ring = ring + foot
    _attach(ring, "forge_feet_plan", plan)
    return ring


_ARCH_SHAPES = ("pointed", "round")


def arcade_base_plan(
    outer_r: float,
    height: float,
    count: int,
    style: str = "pier",
    *,
    printer: Optional[Mapping[str, Any]] = None,
    inner_r: float = 0.0,
    arch: str = "pointed",
    opening_fraction: float = 0.6,
    sill: Optional[float] = None,
) -> Dict[str, Any]:
    """The plinth and openings :func:`arcade_base` will build."""
    style = str(style).strip().lower()
    if style not in _BASE_STYLES:
        raise PrintabilityError(
            f"style must be one of {', '.join(_BASE_STYLES)}, got {style!r}"
        )
    arch = str(arch).strip().lower()
    if arch not in _ARCH_SHAPES:
        raise PrintabilityError(
            f"arch must be one of {', '.join(_ARCH_SHAPES)}, got {arch!r}"
        )

    radius = _finite_positive(outer_r, "outer_r")
    plinth_h = _finite_positive(height, "height")
    n = _positive_count(count, "count")
    bore = _finite_positive(inner_r, "inner_r", allow_zero=True)

    prof = profile(printer)
    feature = float(prof["min_feature_size"])
    wall_floor = float(prof["min_wall_thickness"])
    clamped: List[str] = []

    if bore >= radius - wall_floor:
        raise PrintabilityError(
            f"inner_r {bore:g} mm leaves {radius - bore:.3f} mm of ring; the printer "
            f"needs {wall_floor:g} mm"
        )
    if not 0.1 <= float(opening_fraction) <= 0.9:
        raise PrintabilityError(
            f"opening_fraction must sit between 0.1 and 0.9, got {opening_fraction!r}"
        )

    sill_h = 0.0
    if style == "pad":
        sill_h = float(sill) if sill is not None else max(0.25 * plinth_h, wall_floor)
        sill_h = max(sill_h, wall_floor)
        if sill_h > 0.5 * plinth_h:
            clamped.append(
                f"sill {sill_h:g} -> {0.5 * plinth_h:g} mm: half the plinth is as much "
                "as a continuous foot ring may take"
            )
            sill_h = 0.5 * plinth_h

    pitch = 2.0 * math.pi * radius / n
    width = float(opening_fraction) * pitch
    pier_width = pitch - width
    if pier_width < feature:
        width = pitch - feature
        pier_width = feature
        clamped.append(
            f"opening width -> {width:.3f} mm: {n} openings must leave a {feature:g} mm "
            "pier between them"
        )
    if width < feature:
        raise PrintabilityError(
            f"{n} arches on a {2.0 * radius:g} mm circle leaves {pitch:.2f} mm of pitch "
            f"each, which cannot hold a {feature:g} mm opening and a {feature:g} mm "
            "pier; use fewer arches"
        )

    limit = max(float(prof["max_unsupported_overhang_deg"]) - OVERHANG_SAFETY_DEG, 1.0)
    tan_limit = math.tan(math.radians(min(limit, 89.0)))

    opening_h = plinth_h - sill_h
    if arch == "pointed":
        apex_rise = (width / 2.0) / tan_limit
    else:
        apex_rise = width / 2.0

    # The opening's straight shoulders plus its cap have to fit under the top of
    # the plinth with a minimum wall of material left over it.
    shoulder = opening_h - apex_rise - wall_floor
    if shoulder < feature:
        # Narrow the opening until the cap fits.
        room = max(opening_h - feature - wall_floor, 0.0)
        if arch == "pointed":
            new_width = 2.0 * room * tan_limit
        else:
            new_width = 2.0 * room
        if new_width < feature:
            raise PrintabilityError(
                f"a {plinth_h:g} mm plinth has no room for a {arch} arch: the cap alone "
                f"needs {apex_rise:.2f} mm plus {wall_floor:g} mm of material over it. "
                "Raise height, or use feet_ring() instead of an arcade."
            )
        clamped.append(
            f"opening width {width:.3f} -> {new_width:.3f} mm: the {arch} cap has to "
            f"fit under a {plinth_h:g} mm plinth"
        )
        width = new_width
        pier_width = pitch - width
        apex_rise = (width / 2.0) / tan_limit if arch == "pointed" else width / 2.0
        shoulder = opening_h - apex_rise - wall_floor

    return {
        "style": style,
        "arch": arch,
        "outer_r_mm": round(radius, 5),
        "inner_r_mm": round(bore, 5),
        "height_mm": round(plinth_h, 5),
        "count": n,
        "pitch_mm": round(pitch, 5),
        "opening_width_mm": round(width, 5),
        "pier_width_mm": round(pier_width, 5),
        "sill_mm": round(sill_h, 5),
        "shoulder_mm": round(shoulder, 5),
        "apex_rise_mm": round(apex_rise, 5),
        "crown_z_mm": round(sill_h + shoulder + apex_rise, 5),
        "arch_angle_from_vertical_deg": (
            round(math.degrees(math.atan2(width / 2.0, apex_rise)), 3)
            if apex_rise > 0
            else 0.0
        ),
        "support_free": arch == "pointed",
        "min_feature_mm": feature,
        "clamped": clamped,
    }


def arcade_base(
    outer_r: float,
    height: float,
    count: int,
    style: str = "pier",
    *,
    printer: Optional[Mapping[str, Any]] = None,
    inner_r: float = 0.0,
    arch: str = "pointed",
    opening_fraction: float = 0.6,
    sill: Optional[float] = None,
) -> Any:
    """An arcaded plinth ring, as a positive.  Base on Z = 0.

    A tube from ``inner_r`` to ``outer_r``, ``height`` tall, with ``count``
    openings cut radially through it.  What is left between the openings are the
    piers the part stands on.

    ``style="pier"`` cuts the openings all the way to the bed, so the plinth is
    ``count`` separate legs.  ``"pad"`` leaves a continuous foot ring ``sill``
    tall underneath -- more bed adhesion, less air.

    ``arch="pointed"`` (the default) caps each opening with a tent whose flanks
    sit exactly on the overhang limit, so the arcade needs no supports and the
    ``overhangs`` check agrees.  ``arch="round"`` is the classical semicircular
    arch: it prints without supports in practice, but its crown is horizontal
    for a few facets, so the check will flag it -- ``plan["support_free"]`` says
    so.

    Guarantees
    ----------
    * Piers are never thinner than the printer's minimum feature: the opening
      width is clamped to the pitch, and a ``count`` that cannot work raises.
    * A minimum wall of material is always left over the arch crown, and the
      opening is narrowed rather than allowed to break through the top.
    * The ring wall itself (``outer_r - inner_r``) is checked against the
      minimum wall up front.
    """
    plan = arcade_base_plan(
        outer_r,
        height,
        count,
        style,
        printer=printer,
        inner_r=inner_r,
        arch=arch,
        opening_fraction=opening_fraction,
        sill=sill,
    )

    from build123d import Plane, Polygon, Rot, extrude  # noqa: PLC0415

    radius = plan["outer_r_mm"]
    bore = plan["inner_r_mm"]
    plinth_h = plan["height_mm"]
    n = int(plan["count"])
    width = plan["opening_width_mm"]
    sill_h = plan["sill_mm"]
    shoulder = plan["shoulder_mm"]
    apex = plan["apex_rise_mm"]

    ring = _revolve_profile(
        [(bore, 0.0), (radius, 0.0), (radius, plinth_h), (bore, plinth_h)]
    )

    half = width / 2.0
    z0 = sill_h if sill_h > 0.0 else -1.0
    z_shoulder = sill_h + shoulder
    if plan["arch"] == "pointed":
        section = [
            (-half, z0),
            (half, z0),
            (half, z_shoulder),
            (0.0, z_shoulder + apex),
            (-half, z_shoulder),
        ]
    else:
        steps = 12
        section = [(-half, z0), (half, z0), (half, z_shoulder)]
        for step in range(1, steps):
            angle = math.pi * step / steps
            section.append(
                (half * math.cos(angle), z_shoulder + apex * math.sin(angle))
            )
        section.append((-half, z_shoulder))

    reach = radius + 2.0
    cutter = extrude(Plane.XZ * Polygon(*section, align=None), amount=reach)
    # Plane.XZ extrudes along -Y, so face each opening outward by rotating the
    # cutter to the angle plus 180 deg.
    for index in range(n):
        angle_deg = 360.0 * index / n + 180.0
        ring = ring - (Rot(0.0, 0.0, angle_deg) * cutter)

    _attach(ring, "forge_arcade_plan", plan)
    return ring


# --------------------------------------------------------------------------
# magnet_pocket
# --------------------------------------------------------------------------


def magnet_pocket_plan(
    diameter: float,
    depth: float,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    tolerance: Optional[float] = None,
    available_depth: Optional[float] = None,
) -> Dict[str, Any]:
    """The pocket :func:`magnet_pocket` will cut, sized exactly like ``/segment``."""
    magnet_d = _finite_positive(diameter, "diameter")
    magnet_t = _finite_positive(depth, "depth")

    prof = profile(printer)
    wall_floor = float(prof["min_wall_thickness"])
    extra = (
        fit_tolerance("magnet_pocket_extra", prof)
        if tolerance is None
        else _finite_positive(tolerance, "tolerance", allow_zero=True)
    )

    pocket_d = magnet_d + 2.0 * extra
    pocket_depth = magnet_t + extra

    clamped: List[str] = []
    if available_depth is not None:
        material = _finite_positive(available_depth, "available_depth")
        if pocket_depth + wall_floor > material + 1e-9:
            raise PrintabilityError(
                f"a {pocket_depth:.2f} mm pocket in {material:g} mm of material leaves "
                f"{material - pocket_depth:.2f} mm of floor under the magnet; the "
                f"printer needs {wall_floor:g} mm. Make the part at least "
                f"{pocket_depth + wall_floor:.2f} mm thick there."
            )

    return {
        "magnet_diameter_mm": round(magnet_d, 5),
        "magnet_thickness_mm": round(magnet_t, 5),
        "tolerance_mm": round(extra, 5),
        "tolerance_source": (
            "printer.tolerances.magnet_pocket_extra" if tolerance is None else "caller"
        ),
        "pocket_diameter_mm": round(pocket_d, 5),
        "pocket_depth_mm": round(pocket_depth, 5),
        "mouth_overshoot_mm": MOUTH_OVERSHOOT_MM,
        "floor_mm": (
            round(float(available_depth) - pocket_depth, 5)
            if available_depth is not None
            else None
        ),
        "min_wall_mm": wall_floor,
        "clamped": clamped,
    }


def magnet_pocket(
    diameter: float,
    depth: float,
    printer: Optional[Mapping[str, Any]] = None,
    *,
    tolerance: Optional[float] = None,
    available_depth: Optional[float] = None,
) -> Any:
    """A blind pocket for a disc magnet, **as a negative to subtract**.

    The pocket hangs **below Z = 0 with its mouth facing up**, so you place it
    at the face it is bored into and it drills straight down::

        part -= Pos(x, y, top_z) * forge_lib.magnet_pocket(6.0, 3.0)

    ``diameter`` and ``depth`` are the *magnet's* dimensions -- the numbers on
    the packet.  The pocket is grown by the profile's ``magnet_pocket_extra``
    (0.05 mm), the same sizing ``/segment``'s magnet joints use: diameter plus
    twice the tolerance, depth plus the tolerance once (the magnet only has to
    clear the floor at one end).

    Guarantees
    ----------
    * The mouth pokes ``MOUTH_OVERSHOOT_MM`` (0.2 mm) above Z = 0, so
      subtracting it at a face is never a coplanar-face boolean.
    * With ``available_depth`` given -- how much material sits under the mouth --
      the helper raises rather than leaving a floor thinner than the printer's
      minimum wall.  Pass it; it is the check that catches a pocket punching
      through the back of a part.
    * Open **upward**.  A pocket bored from the underside leaves a flat ceiling,
      which is a 90 deg overhang; that is a rule the helper cannot enforce for
      you because it does not know which way up you place it.
    """
    plan = magnet_pocket_plan(
        diameter,
        depth,
        printer,
        tolerance=tolerance,
        available_depth=available_depth,
    )

    from build123d import Cylinder, Pos  # noqa: PLC0415

    pocket_depth = plan["pocket_depth_mm"]
    total = pocket_depth + MOUTH_OVERSHOOT_MM
    cavity = Pos(0.0, 0.0, MOUTH_OVERSHOOT_MM - total / 2.0) * Cylinder(
        radius=plan["pocket_diameter_mm"] / 2.0, height=total
    )
    _attach(cavity, "forge_magnet_plan", plan)
    return cavity


# --------------------------------------------------------------------------
# shell_box / wall_safe_shell
# --------------------------------------------------------------------------


def shell_box_plan(
    length: float,
    width: float,
    height: float,
    wall: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    floor: Optional[float] = None,
    open_top: bool = True,
    corner_r: float = 0.0,
) -> Dict[str, Any]:
    """The hollow box :func:`shell_box` will build."""
    outer_l = _finite_positive(length, "length")
    outer_w = _finite_positive(width, "width")
    outer_h = _finite_positive(height, "height")

    prof = profile(printer)
    wall_floor = float(prof["min_wall_thickness"])
    feature = float(prof["min_feature_size"])
    clamped: List[str] = []

    thickness = _finite_positive(wall, "wall")
    if thickness < wall_floor - 1e-9:
        clamped.append(
            f"wall {thickness:g} -> {wall_floor:g} mm: the printer's minimum wall"
        )
        thickness = wall_floor

    base = float(floor) if floor is not None else thickness
    base = _finite_positive(base, "floor")
    if base < wall_floor - 1e-9:
        clamped.append(f"floor {base:g} -> {wall_floor:g} mm: the printer's minimum wall")
        base = wall_floor

    inner_l = outer_l - 2.0 * thickness
    inner_w = outer_w - 2.0 * thickness
    inner_h = outer_h - base - (0.0 if open_top else thickness)

    if min(inner_l, inner_w) < feature:
        raise PrintabilityError(
            f"a {thickness:g} mm wall in a {outer_l:g} x {outer_w:g} mm box leaves a "
            f"{inner_l:.2f} x {inner_w:.2f} mm cavity, under the {feature:g} mm minimum "
            f"feature. The box has to be at least "
            f"{2.0 * thickness + feature:.2f} mm across."
        )
    if inner_h < feature:
        raise PrintabilityError(
            f"a {base:g} mm floor in a {outer_h:g} mm box leaves {inner_h:.2f} mm of "
            f"cavity, under the {feature:g} mm minimum feature"
        )

    radius = _finite_positive(corner_r, "corner_r", allow_zero=True)
    max_radius = min(outer_l, outer_w) / 2.0 - 1e-6
    if radius > max_radius:
        clamped.append(
            f"corner_r {radius:g} -> {max_radius:.3f} mm: half the shorter side is the "
            "most a corner can take"
        )
        radius = max_radius

    return {
        "length_mm": round(outer_l, 5),
        "width_mm": round(outer_w, 5),
        "height_mm": round(outer_h, 5),
        "wall_mm": round(thickness, 5),
        "floor_mm": round(base, 5),
        "corner_r_mm": round(radius, 5),
        "inner_length_mm": round(inner_l, 5),
        "inner_width_mm": round(inner_w, 5),
        "inner_height_mm": round(inner_h, 5),
        "open_top": bool(open_top),
        "support_free": bool(open_top),
        "min_wall_mm": wall_floor,
        "clamped": clamped,
    }


def shell_box(
    length: float,
    width: float,
    height: float,
    wall: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    floor: Optional[float] = None,
    open_top: bool = True,
    corner_r: float = 0.0,
) -> Any:
    """A hollow rectangular box with a wall the printer can hold.  Base on Z = 0.

    Centred in XY, sitting on Z = 0, with a cavity of ``floor``-thick bottom and
    ``wall``-thick sides.

    Guarantees
    ----------
    * ``wall`` and ``floor`` are clamped **up** to the printer's minimum wall,
      never accepted below it, and the clamp is reported.
    * A cavity that the walls would swallow raises with the outer size the box
      would have to be, instead of returning a solid block.
    * ``open_top=True`` (the default) is support-free: every internal face is
      vertical or upward.  ``open_top=False`` puts a flat lid over the cavity --
      a 90 deg ceiling that needs supports or a print upside down, and
      ``plan["support_free"]`` says ``False``.
    * ``corner_r`` rounds the vertical corners of both the shell and the cavity;
      if the kernel refuses the fillet the box comes back square rather than
      failing, and the plan records it.
    """
    plan = shell_box_plan(
        length,
        width,
        height,
        wall,
        printer=printer,
        floor=floor,
        open_top=open_top,
        corner_r=corner_r,
    )

    from build123d import Axis, Box, GeomType, Pos, fillet  # noqa: PLC0415

    outer_l = plan["length_mm"]
    outer_w = plan["width_mm"]
    outer_h = plan["height_mm"]
    thickness = plan["wall_mm"]
    base = plan["floor_mm"]
    radius = plan["corner_r_mm"]
    inner_h = plan["inner_height_mm"]

    def _round_vertical(solid: Any, amount: float) -> Any:
        if amount <= 1e-6:
            return solid
        try:
            edges = solid.edges().filter_by(Axis.Z).filter_by(GeomType.LINE)
            return fillet(edges, radius=amount)
        except Exception:  # noqa: BLE001 - a square box is still a printable box
            plan.setdefault("clamped", []).append(
                f"corner_r {amount:g} -> 0 mm: the kernel would not fillet this box"
            )
            return solid

    shell = _round_vertical(
        Pos(0.0, 0.0, outer_h / 2.0) * Box(outer_l, outer_w, outer_h), radius
    )

    overshoot = MOUTH_OVERSHOOT_MM if plan["open_top"] else 0.0
    cavity_h = inner_h + overshoot
    cavity = Pos(0.0, 0.0, base + cavity_h / 2.0) * Box(
        plan["inner_length_mm"], plan["inner_width_mm"], cavity_h
    )
    cavity = _round_vertical(cavity, max(radius - thickness, 0.0))

    solid = shell - cavity
    _attach(solid, "forge_shell_plan", plan)
    return solid


def wall_safe_shell(
    solid: Any,
    wall: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    openings: Any = None,
) -> Any:
    """Hollow an arbitrary solid, with the wall clamped to the printer minimum.

    ``openings`` is the face (or list of faces) to leave open -- typically
    ``solid.faces().sort_by(Axis.Z)[-1]`` for a top-opening shell.  With no
    opening the result is a sealed void, which is a print with trapped support
    material; that is legal but rarely what you want.

    Guarantees
    ----------
    * ``wall`` is clamped up to ``printer.min_wall_thickness`` and the clamped
      value is returned on the shape as ``forge_shell_plan``.
    * A hollowing the kernel cannot do raises :class:`PrintabilityError` naming
      the wall it failed at, rather than surfacing an OCC error -- offsetting
      inward fails on solids with features smaller than the wall, so the message
      says exactly that.

    For a plain box use :func:`shell_box`: it is primitives and one boolean,
    where this is an inward offset and can be slow on a complicated solid.
    """
    from build123d import Kind, offset  # noqa: PLC0415

    prof = profile(printer)
    wall_floor = float(prof["min_wall_thickness"])
    clamped: List[str] = []
    thickness = _finite_positive(wall, "wall")
    if thickness < wall_floor - 1e-9:
        clamped.append(
            f"wall {thickness:g} -> {wall_floor:g} mm: the printer's minimum wall"
        )
        thickness = wall_floor

    try:
        hollow = offset(
            solid,
            amount=-thickness,
            openings=openings,
            kind=Kind.INTERSECTION,
        )
    except Exception as exc:  # noqa: BLE001 - OCC messages are not for humans
        raise PrintabilityError(
            f"could not hollow this solid to a {thickness:g} mm wall "
            f"({type(exc).__name__}). An inward offset fails where the solid has "
            "details smaller than the wall -- fillets, thin ribs, sharp corners. "
            "Build the shell from primitives (shell_box) or thicken the details."
        ) from exc

    _attach(
        hollow,
        "forge_shell_plan",
        {"wall_mm": round(thickness, 5), "min_wall_mm": wall_floor, "clamped": clamped},
    )
    return hollow


# --------------------------------------------------------------------------
# screw_boss
# --------------------------------------------------------------------------

_BOSS_STYLES = ("thread-forming", "clearance")


def screw_boss_plan(
    screw_diameter: float,
    height: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    wall: Optional[float] = None,
    hole_depth: Optional[float] = None,
    style: str = "thread-forming",
) -> Dict[str, Any]:
    """The boss :func:`screw_boss` will build."""
    style = str(style).strip().lower()
    if style not in _BOSS_STYLES:
        raise PrintabilityError(
            f"style must be one of {', '.join(_BOSS_STYLES)}, got {style!r}"
        )

    screw_d = _finite_positive(screw_diameter, "screw_diameter")
    boss_h = _finite_positive(height, "height")

    prof = profile(printer)
    wall_floor = float(prof["min_wall_thickness"])
    feature = float(prof["min_feature_size"])
    clamped: List[str] = []

    if style == "thread-forming":
        hole_d = 0.80 * screw_d
    else:
        hole_d = screw_d + 2.0 * fit_tolerance("slide_fit", prof)
    if hole_d < feature - 1e-9:
        clamped.append(
            f"hole {hole_d:.3f} -> {feature:g} mm: a hole under the minimum feature "
            "closes up when it prints"
        )
        hole_d = feature

    thickness = float(wall) if wall is not None else max(0.5 * screw_d, 2.0 * wall_floor)
    thickness = _finite_positive(thickness, "wall")
    if thickness < wall_floor - 1e-9:
        clamped.append(
            f"wall {thickness:g} -> {wall_floor:g} mm: the printer's minimum wall"
        )
        thickness = wall_floor

    boss_d = hole_d + 2.0 * thickness

    depth = float(hole_depth) if hole_depth is not None else boss_h - wall_floor
    depth = _finite_positive(depth, "hole_depth")
    if depth > boss_h - wall_floor + 1e-9:
        clamped.append(
            f"hole_depth {depth:g} -> {boss_h - wall_floor:g} mm: a {wall_floor:g} mm "
            "floor has to stay under the screw"
        )
        depth = boss_h - wall_floor
    if depth < feature:
        raise PrintabilityError(
            f"a {boss_h:g} mm boss cannot hold a screw hole: after the {wall_floor:g} mm "
            f"floor there is only {depth:.2f} mm of depth, under the {feature:g} mm "
            "minimum feature"
        )

    lead = min(0.5, 0.25 * thickness)

    return {
        "style": style,
        "screw_diameter_mm": round(screw_d, 5),
        "hole_diameter_mm": round(hole_d, 5),
        "hole_depth_mm": round(depth, 5),
        "boss_diameter_mm": round(boss_d, 5),
        "wall_mm": round(thickness, 5),
        "height_mm": round(boss_h, 5),
        "floor_mm": round(boss_h - depth, 5),
        "lead_in_mm": round(lead, 5),
        "min_wall_mm": wall_floor,
        "support_free": True,
        "clamped": clamped,
    }


def screw_boss(
    screw_diameter: float,
    height: float,
    *,
    printer: Optional[Mapping[str, Any]] = None,
    wall: Optional[float] = None,
    hole_depth: Optional[float] = None,
    style: str = "thread-forming",
) -> Any:
    """A screw boss: a cylinder with a blind hole down it.  Base on Z = 0.

    ``style="thread-forming"`` (the default) bores 0.80 x the screw diameter, so
    a self-tapping screw cuts its own thread in the plastic.  ``"clearance"``
    bores the screw diameter plus a slide fit, for a screw that passes through
    into a nut or another boss.

    Guarantees
    ----------
    * The wall around the hole is at least the printer's minimum wall; the
      default is half the screw diameter, giving the usual 2 x D boss.
    * A floor of at least one minimum wall always stays under the hole, and a
      boss too short to hold a hole raises instead of drilling through.
    * A hole clamped up to the minimum feature never prints closed.
    * Support-free by construction: a vertical cylinder with a vertical bore and
      an upward-facing lead-in chamfer at the mouth.
    """
    plan = screw_boss_plan(
        screw_diameter,
        height,
        printer=printer,
        wall=wall,
        hole_depth=hole_depth,
        style=style,
    )

    boss_r = plan["boss_diameter_mm"] / 2.0
    hole_r = plan["hole_diameter_mm"] / 2.0
    boss_h = plan["height_mm"]
    depth = plan["hole_depth_mm"]
    lead = plan["lead_in_mm"]

    z_floor = boss_h - depth
    points: List[Tuple[float, float]] = [
        (0.0, 0.0),
        (boss_r, 0.0),
        (boss_r, boss_h),
        (hole_r + lead, boss_h),
        (hole_r, boss_h - lead),
        (hole_r, z_floor),
        (0.0, z_floor),
    ]
    solid = _revolve_profile(points)
    _attach(solid, "forge_boss_plan", plan)
    return solid


__all__ = [
    "DEFAULT_DEPTH_EXTRA_MM",
    "DEFAULT_PEG_DIAMETER_MM",
    "DEFAULT_PEG_LENGTH_MM",
    "DEFAULT_PITCH_FRACTION",
    "DEFAULT_TOLERANCE_MM",
    "ForgeError",
    "KEY_HEIGHT_RATIO",
    "KEY_WIDTH_RATIO",
    "MAX_BAND_CUTTERS",
    "MIN_RELIEF_DEPTH_MM",
    "MIN_TAPER_HEIGHT_MM",
    "MOUTH_OVERSHOOT_MM",
    "OVERHANG_SAFETY_DEG",
    "PrintabilityError",
    "arcade_base",
    "arcade_base_plan",
    "blunted_taper",
    "blunted_taper_plan",
    "feet_ring",
    "feet_ring_plan",
    "fit_tolerance",
    "flared_lip",
    "flared_lip_plan",
    "magnet_pocket",
    "magnet_pocket_plan",
    "max_flare_for",
    "max_overhang_deg",
    "min_feature",
    "min_land",
    "min_wall",
    "peg",
    "peg_spec",
    "profile",
    "screw_boss",
    "screw_boss_plan",
    "shell_box",
    "shell_box_plan",
    "socket_for",
    "textured_band",
    "textured_band_plan",
    "wall_safe_shell",
]
