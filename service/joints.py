"""The joint library: what gets added to a cut face so two segments mate.

Three joints, all parameterised off the printer's fit tolerances:

``dovetail``
    A trapezoidal rail across the cut face -- narrow at the face, wider at the
    tip -- unioned onto one segment and cut (grown by ``press_fit``) out of the
    other.  It resists pulling the segments apart; the segments assemble by
    sliding along the rail.

``pin``
    Blind cylindrical sockets in *both* faces plus a separate printed pin per
    pair.  The sockets are grown by ``press_fit`` on the radius; the pin is
    printed at nominal size and is emitted as its own segment.

``magnet``
    A blind cylindrical pocket in both faces, sized for a disc magnet
    (6 x 3 mm by default) plus ``magnet_pocket_extra``.  Nothing is printed to
    fill it; the magnets are hardware you buy.

``none``
    A plain cut, for when the joint is somebody else's problem.

The cut frame
-------------
Every joint is built in a local frame attached to the cut, and the same frame
serves radial and planar cuts:

* local **+Y** is the cut normal.  Segment *A* is the material at ``y < 0``,
  segment *B* the material at ``y > 0``.
* local **X** (``u``) and local **Z** (``v``) span the cut face.  For a radial
  cut ``u`` is the radius from the part's central axis and ``v`` is world Z; for
  a planar Z cut ``u`` is world X and ``v`` is world -Y.

:class:`CutFrame` carries that frame plus the measured extents of the cut
cross-section, which is what every default dimension below is derived from.

How tolerance is applied
------------------------
Male features are built at nominal size; the matching negative is grown by the
tolerance, so the printed pieces are ``tolerance`` apart across every mating
face.  On the dovetail's slanted flanks the in-plane offset is divided by the
cosine of the flank angle, so the *perpendicular* clearance is the tolerance the
caller asked for and not a foreshortened version of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import ParamError
from .printer import tolerance as printer_tolerance

JOINT_TYPES = ("none", "dovetail", "pin", "magnet")

#: How far past the material the through-cutting tools are extended, so a
#: boolean never has to resolve a face exactly touching another face.
OVERSHOOT_MM = 1.0

#: Default disc magnet: 6 mm across, 3 mm thick.
DEFAULT_MAGNET_DIAMETER_MM = 6.0
DEFAULT_MAGNET_THICKNESS_MM = 3.0

#: Total end float designed into a pin so it seats before it bottoms out.
DEFAULT_PIN_CLEARANCE_MM = 0.4


@dataclass
class CutFrame:
    """One cut, its local frame, and the size of the face being joined."""

    name: str
    kind: str  # "radial" | "planar"
    to_world: Any  # build123d Location: local -> world
    u_min: float
    u_max: float
    v_min: float
    v_max: float
    side_a: int  # index of the segment at local y < 0
    side_b: int  # index of the segment at local y > 0
    depth_available: Optional[float] = None  # material each side along the normal
    #: ``(u, v, half_size) -> bool``: is there material at this spot on the face?
    #: Set by the segmenter once the face has been measured.
    material_probe: Optional[Any] = None

    def has_material(self, u: float, v: float, half: float) -> bool:
        if self.material_probe is None:
            return True
        return bool(self.material_probe(u, v, half))

    @property
    def u_extent(self) -> float:
        return self.u_max - self.u_min

    @property
    def v_extent(self) -> float:
        return self.v_max - self.v_min

    @property
    def u_centre(self) -> float:
        return (self.u_min + self.u_max) / 2.0

    @property
    def v_centre(self) -> float:
        return (self.v_min + self.v_max) / 2.0

    def spread_axis(self) -> str:
        """The longer in-plane axis: joints are spread along it."""
        return "v" if self.v_extent >= self.u_extent else "u"

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "face_extent_mm": [round(self.u_extent, 4), round(self.v_extent, 4)],
            "segments": [self.side_a, self.side_b],
        }


@dataclass
class JointTools:
    """Boolean work to apply for one cut, plus any hardware it needs printed."""

    add_to_a: List[Any] = field(default_factory=list)
    sub_from_a: List[Any] = field(default_factory=list)
    sub_from_b: List[Any] = field(default_factory=list)
    hardware: List[Tuple[str, Any]] = field(default_factory=list)
    resolved: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Request parsing
# --------------------------------------------------------------------------


def _number(spec: Mapping[str, Any], key: str, default: Optional[float]) -> Optional[float]:
    if key not in spec or spec[key] is None:
        return default
    value = spec[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamError(f"joint.{key} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ParamError(f"joint.{key} must be a finite positive number, got {value!r}")
    return number


def resolve_joint(
    joint: Optional[Mapping[str, Any]], printer: Mapping[str, Any]
) -> Dict[str, Any]:
    """Normalise the request's ``joint`` object and pick the default tolerance.

    ``dovetail`` and ``pin`` default to the profile's ``press_fit``; ``magnet``
    to ``magnet_pocket_extra``.  An explicit ``tolerance`` in the request wins.
    """
    if joint is None:
        joint = {}
    if not isinstance(joint, Mapping):
        raise ParamError(f"joint must be an object, got {type(joint).__name__}")

    kind = joint.get("type", "dovetail")
    if not isinstance(kind, str) or kind.strip().lower() not in JOINT_TYPES:
        raise ParamError(
            f"joint.type {kind!r} is not supported; use one of {', '.join(JOINT_TYPES)}"
        )
    kind = kind.strip().lower()

    if kind == "magnet":
        default_tolerance = printer_tolerance(printer, "magnet_pocket_extra", 0.05)
    else:
        default_tolerance = printer_tolerance(printer, "press_fit", 0.1)

    raw_tolerance = joint.get("tolerance")
    if raw_tolerance is None:
        resolved_tolerance = float(default_tolerance)
    else:
        if isinstance(raw_tolerance, bool) or not isinstance(raw_tolerance, (int, float)):
            raise ParamError(
                f"joint.tolerance must be a number, got {raw_tolerance!r}"
            )
        resolved_tolerance = float(raw_tolerance)
        if not math.isfinite(resolved_tolerance) or resolved_tolerance < 0.0:
            raise ParamError(
                "joint.tolerance must be a finite non-negative number, got "
                f"{raw_tolerance!r}"
            )

    spec: Dict[str, Any] = {
        "type": kind,
        "tolerance": resolved_tolerance,
        "tolerance_source": (
            "request"
            if raw_tolerance is not None
            else ("printer.tolerances.magnet_pocket_extra" if kind == "magnet" else "printer.tolerances.press_fit")
        ),
    }

    if kind == "dovetail":
        spec["width_mm"] = _number(joint, "width_mm", None)
        spec["depth_mm"] = _number(joint, "depth_mm", None)
        spec["flare_mm"] = _number(joint, "flare_mm", None)
    elif kind == "pin":
        spec["diameter_mm"] = _number(joint, "diameter_mm", None)
        spec["socket_depth_mm"] = _number(joint, "socket_depth_mm", None)
        spec["clearance_mm"] = _number(joint, "clearance_mm", DEFAULT_PIN_CLEARANCE_MM)
        chamfer = joint.get("chamfer_mm")
        spec["chamfer_mm"] = (
            0.3 if chamfer is None else max(0.0, float(chamfer))
        )
        spec["count"] = _count(joint)
    elif kind == "magnet":
        spec["diameter_mm"] = _number(joint, "diameter_mm", DEFAULT_MAGNET_DIAMETER_MM)
        spec["thickness_mm"] = _number(
            joint, "thickness_mm", DEFAULT_MAGNET_THICKNESS_MM
        )
        spec["count"] = _count(joint)

    return spec


def _count(joint: Mapping[str, Any]) -> Optional[int]:
    value = joint.get("count")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParamError(f"joint.count must be an integer, got {value!r}")
    if not 1 <= value <= 8:
        raise ParamError(f"joint.count must be between 1 and 8, got {value}")
    return value


# --------------------------------------------------------------------------
# Sizing, per cut
# --------------------------------------------------------------------------


def plan_joint(spec: Mapping[str, Any], frame: CutFrame) -> Dict[str, Any]:
    """Fill a joint spec's blanks from the measured cut face, and check it fits."""
    kind = spec["type"]
    if kind == "none":
        return dict(spec)

    u_extent, v_extent = frame.u_extent, frame.v_extent
    if min(u_extent, v_extent) <= 0.0:
        raise ParamError(
            f"cut {frame.name}: the cut face has no measurable size "
            f"({u_extent:.3f} x {v_extent:.3f} mm); there is nothing to join"
        )

    if kind == "dovetail":
        return _plan_dovetail(spec, frame, u_extent, v_extent)
    if kind == "pin":
        return _plan_round(spec, frame, u_extent, v_extent, "pin")
    return _plan_round(spec, frame, u_extent, v_extent, "magnet")


def _plan_dovetail(
    spec: Mapping[str, Any], frame: CutFrame, u_extent: float, v_extent: float
) -> Dict[str, Any]:
    across = min(u_extent, v_extent)  # the axis the trapezoid's width lives on
    along = max(u_extent, v_extent)

    width = spec.get("width_mm") or min(max(0.40 * across, 2.0), 12.0)
    width = min(width, 0.70 * across)
    flare = spec.get("flare_mm")
    if flare is None:
        flare = 0.30 * width
    depth = spec.get("depth_mm") or min(max(0.75 * width, 1.5), 8.0)

    # The widest part of the tail plus the socket's tolerance has to leave
    # material on both sides of the pocket, or the segment splits in two.
    tolerance = float(spec["tolerance"])
    widest = width + 2.0 * flare + 2.0 * tolerance
    if widest > 0.90 * across:
        allowed = max((0.90 * across - width - 2.0 * tolerance) / 2.0, 0.0)
        flare = allowed
        widest = width + 2.0 * flare + 2.0 * tolerance

    if width < 1.0 or widest > 0.95 * across:
        raise ParamError(
            f"cut {frame.name}: a dovetail needs about 1.5 mm of face to work with; "
            f"this face is only {across:.2f} mm across (tail would be "
            f"{widest:.2f} mm wide). Use joint type 'pin' with a small diameter, or "
            "'none'."
        )
    if flare <= 1e-6:
        raise ParamError(
            f"cut {frame.name}: no room for a dovetail flare on a {across:.2f} mm "
            "face; the joint would be a plain rail, not a dovetail"
        )

    planned = dict(spec)
    planned.update(
        {
            "width_mm": round(width, 4),
            "flare_mm": round(flare, 4),
            "depth_mm": round(depth, 4),
            "across_axis": "u" if u_extent <= v_extent else "v",
            "slide_axis": "v" if u_extent <= v_extent else "u",
            "slide_length_mm": round(along, 4),
            "flank_angle_deg": round(math.degrees(math.atan2(flare, depth)), 2),
        }
    )
    return planned


def _plan_round(
    spec: Mapping[str, Any],
    frame: CutFrame,
    u_extent: float,
    v_extent: float,
    kind: str,
) -> Dict[str, Any]:
    tolerance = float(spec["tolerance"])

    if kind == "pin":
        diameter = spec.get("diameter_mm") or min(
            max(0.45 * min(u_extent, v_extent), 2.0), 6.0
        )
        socket_depth = spec.get("socket_depth_mm") or max(0.75 * diameter, 2.0)
        pocket_diameter = diameter + 2.0 * tolerance
    else:
        diameter = float(spec.get("diameter_mm") or DEFAULT_MAGNET_DIAMETER_MM)
        thickness = float(spec.get("thickness_mm") or DEFAULT_MAGNET_THICKNESS_MM)
        socket_depth = thickness + tolerance
        pocket_diameter = diameter + 2.0 * tolerance

    across = min(u_extent, v_extent)
    if pocket_diameter > 0.90 * across:
        raise ParamError(
            f"cut {frame.name}: a {pocket_diameter:.2f} mm {kind} pocket does not fit "
            f"a cut face only {across:.2f} mm across. Reduce joint.diameter_mm below "
            f"{0.90 * across:.2f} mm, or use joint type 'dovetail'."
        )
    if frame.depth_available is not None and socket_depth > 0.45 * frame.depth_available:
        raise ParamError(
            f"cut {frame.name}: a {socket_depth:.2f} mm deep {kind} pocket would eat "
            f"more than half of the {frame.depth_available:.2f} mm of material either "
            "side of the cut; reduce joint.socket_depth_mm (or the magnet thickness)"
        )

    spread = max(u_extent, v_extent)
    count = spec.get("count")
    if count is None:
        count = 2 if spread >= 4.0 * pocket_diameter else 1
    if (count + 1) * pocket_diameter > spread:
        count = max(1, int(spread // (1.6 * pocket_diameter)))

    planned = dict(spec)
    planned.update(
        {
            "diameter_mm": round(diameter, 4),
            "pocket_diameter_mm": round(pocket_diameter, 4),
            "socket_depth_mm": round(socket_depth, 4),
            "count": int(count),
            "spread_axis": frame.spread_axis(),
            "spread_length_mm": round(spread, 4),
        }
    )
    if kind == "pin":
        clearance = float(spec.get("clearance_mm") or DEFAULT_PIN_CLEARANCE_MM)
        planned["pin_length_mm"] = round(2.0 * socket_depth - clearance, 4)
        planned["clearance_mm"] = round(clearance, 4)
    return planned


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def build_joint(
    planned: Mapping[str, Any], frame: CutFrame, part: Any, region_b: Any
) -> JointTools:
    """Turn a planned joint into the solids the segmenter has to boolean.

    *part* is the whole solid and *region_b* the half-space (or wedge) on the
    ``+Y`` side of the cut; both are needed to trim a male feature back to the
    material it is supposed to grow out of.
    """
    kind = planned["type"]
    if kind == "none":
        return JointTools(resolved=dict(planned))
    if kind == "dovetail":
        return _build_dovetail(planned, frame, part, region_b)
    return _build_round(planned, frame)


def _positions(frame: CutFrame, planned: Mapping[str, Any]) -> List[Tuple[float, float]]:
    """Local (u, v) centres for ``count`` features spread along the longer axis."""
    count = int(planned["count"])
    if planned["spread_axis"] == "v":
        low, high, other = frame.v_min, frame.v_max, frame.u_centre
    else:
        low, high, other = frame.u_min, frame.u_max, frame.v_centre

    span = high - low
    centres = [low + span * (i + 1) / (count + 1) for i in range(count)]
    if planned["spread_axis"] == "v":
        return [(other, c) for c in centres]
    return [(c, other) for c in centres]


def _trapezoid_prism(
    half_width_at_face: float,
    half_width_at_tip: float,
    y_start: float,
    y_end: float,
    length: float,
) -> Any:
    """Trapezoid in local (x, y), extruded +/- ``length``/2 along local Z."""
    from build123d import BuildPart, BuildSketch, Plane, Polygon, extrude  # noqa: PLC0415

    with BuildPart() as builder:
        with BuildSketch(Plane.XY):
            Polygon(
                (-half_width_at_face, y_start),
                (half_width_at_face, y_start),
                (half_width_at_tip, y_end),
                (-half_width_at_tip, y_end),
                align=None,
            )
        extrude(amount=length / 2.0, both=True)
    return builder.part


def _build_dovetail(
    planned: Mapping[str, Any], frame: CutFrame, part: Any, region_b: Any
) -> JointTools:
    from build123d import Pos, Rot  # noqa: PLC0415

    width = float(planned["width_mm"])
    flare = float(planned["flare_mm"])
    depth = float(planned["depth_mm"])
    tolerance = float(planned["tolerance"])

    slide_is_v = planned["slide_axis"] == "v"
    if slide_is_v:
        slide_low, slide_high = frame.v_min, frame.v_max
        across_centre, slide_centre = frame.u_centre, frame.v_centre
    else:
        slide_low, slide_high = frame.u_min, frame.u_max
        across_centre, slide_centre = frame.v_centre, frame.u_centre
    length = (slide_high - slide_low) + 2.0 * OVERSHOOT_MM

    male = _trapezoid_prism(width / 2.0, width / 2.0 + flare, 0.0, depth, length)

    # The flanks are slanted, so an in-plane offset of `tolerance` would leave
    # only tolerance*cos(flank) of real clearance.  Divide it back out.
    slope = flare / depth if depth > 0 else 0.0
    lateral = tolerance * math.sqrt(1.0 + slope * slope)
    behind = min(OVERSHOOT_MM, 0.4 * depth)
    half_at_start = max(width / 2.0 + lateral - slope * behind, 0.05)
    half_at_tip = width / 2.0 + flare + lateral + slope * tolerance
    female = _trapezoid_prism(
        half_at_start, half_at_tip, -behind, depth + tolerance, length
    )

    # Canonical build has the rail running along local Z; rotate it a quarter
    # turn about local Y when the face is wider across u than v.  Local Y (the
    # cut normal, and the direction the tail sticks out) is the rotation axis,
    # so the protrusion survives untouched.
    spin = 0.0 if slide_is_v else 90.0
    if slide_is_v:
        place = Pos(across_centre, 0.0, slide_centre)
        probe_u, probe_v = across_centre, slide_centre
    else:
        place = Pos(slide_centre, 0.0, across_centre)
        probe_u, probe_v = slide_centre, across_centre

    if not frame.has_material(probe_u, probe_v, width / 2.0):
        raise ParamError(
            f"cut {frame.name}: the middle of the cut face is a hole, so a dovetail "
            "rail there would join nothing. Use joint type 'pin' or 'magnet' with an "
            "explicit count, or cut somewhere the section is solid."
        )

    def to_world(solid: Any) -> Any:
        return frame.to_world * place * Rot(0.0, spin, 0.0) * solid

    male_world = to_world(male)
    female_world = to_world(female)

    # Trim the tail to the material it grows into: the tail is only ever the
    # part of the rail that sits inside B's territory *and* inside the solid.
    key = male_world & region_b
    key = key & part

    tools = JointTools(resolved=dict(planned))
    if _has_volume(key):
        tools.add_to_a.append(key)
    tools.sub_from_b.append(female_world)
    return tools


def _build_round(planned: Mapping[str, Any], frame: CutFrame) -> JointTools:
    from build123d import Cylinder, Pos, Rot  # noqa: PLC0415

    radius = float(planned["pocket_diameter_mm"]) / 2.0
    depth = float(planned["socket_depth_mm"])
    tools = JointTools(resolved=dict(planned))

    wanted = _positions(frame, planned)
    usable = [(u, v) for (u, v) in wanted if frame.has_material(u, v, radius)]
    if not usable:
        raise ParamError(
            f"cut {frame.name}: every {planned['type']} pocket would land on a hole in "
            f"the cut face ({len(wanted)} tried). Move the cut, or raise joint.count "
            "so a pocket lands on material."
        )

    for index, (u, v) in enumerate(usable, start=1):
        # One cylinder spanning +/- depth about the cut plane cuts a blind
        # socket into each side at once: A only exists below it, B only above.
        pocket = (
            frame.to_world
            * Pos(u, -depth, v)
            * Rot(-90.0, 0.0, 0.0)
            * Cylinder(radius, 2.0 * depth, align=None)
        )
        tools.sub_from_a.append(pocket)
        tools.sub_from_b.append(pocket)

        if planned["type"] == "pin":
            tools.hardware.append(
                (f"{frame.name}_pin_{index}", _pin_solid(planned))
            )

    tools.resolved["placed_count"] = len(usable)
    tools.resolved["positions_uv_mm"] = [
        [round(u, 3), round(v, 3)] for (u, v) in usable
    ]
    return tools


def _pin_solid(planned: Mapping[str, Any]) -> Any:
    from build123d import Cylinder  # noqa: PLC0415

    radius = float(planned["diameter_mm"]) / 2.0
    length = float(planned["pin_length_mm"])
    pin = Cylinder(radius, length)

    chamfer_size = float(planned.get("chamfer_mm") or 0.0)
    if chamfer_size > 0.0:
        chamfer_size = min(chamfer_size, radius * 0.4, length * 0.2)
    if chamfer_size > 0.01:
        try:
            from build123d import GeomType, chamfer  # noqa: PLC0415

            pin = chamfer(
                pin.edges().filter_by(GeomType.CIRCLE), length=chamfer_size
            )
        except Exception:  # noqa: BLE001 - a plain cylinder still prints
            pin = Cylinder(radius, length)
    return pin


def _has_volume(shape: Any) -> bool:
    """An empty boolean result *asserts* on ``.wrapped`` in build123d 0.11."""
    if shape is None:
        return False
    try:
        if shape.wrapped is None:
            return False
        return float(shape.volume) > 1e-9
    except Exception:  # noqa: BLE001 - empty is the answer, not an error
        return False


__all__ = [
    "CutFrame",
    "DEFAULT_MAGNET_DIAMETER_MM",
    "DEFAULT_MAGNET_THICKNESS_MM",
    "JOINT_TYPES",
    "JointTools",
    "build_joint",
    "plan_joint",
    "resolve_joint",
]
