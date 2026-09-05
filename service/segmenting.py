"""Cutting a solid into printable segments, and laying them out on the bed.

Two cut modes, both of which end up as a list of *region* solids that are
intersected with the part:

``{"radial": N}``
    ``N`` equal wedges about Z through the part's bounding-box centre in XY.
    Each wedge is an exact circular sector (not a polygonal approximation), so
    a cut ring keeps its true curvature.

``{"planar": [z, ...]}``
    Horizontal cuts at the given Z heights, producing ``len(z) + 1`` slabs.

``"auto"``
    Whatever :func:`checks.suggest_segmentation` proposes for this part and
    printer -- radial for ring-like parts, planar otherwise.

Each cut gets a joint from :mod:`joints`, and every resulting segment is
re-tessellated and re-checked: **a segment that is not watertight is an error**,
not a warning.  A segment that cannot be sliced is not a segment.

Plate packing is deliberately dumb -- shelf rows, biggest first, each part free
to take a quarter turn about Z -- because the honest alternative is a real
nesting solver and the honest fallback is "it does not fit, tell the user".
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .errors import ParamError, ScriptError
from .joints import CutFrame, JointTools, build_joint, plan_joint
from .printer import (
    DEFAULT_PLATE_MARGIN_MM,
    DEFAULT_PLATE_SPACING_MM,
    bed_size,
)

#: Thickness of the probe slab used to measure a cut's cross-section.
SECTION_SLAB_MM = 0.02

#: Regions are built this far beyond the part so no boolean face is coincident.
REGION_OVERSHOOT_MM = 1.0

#: Ceiling on radial segment counts a request may ask for.
MAX_RADIAL_SEGMENTS = 64

#: Ceiling on planar cuts a request may ask for.
MAX_PLANAR_CUTS = 63


# --------------------------------------------------------------------------
# Mode parsing
# --------------------------------------------------------------------------


def normalize_mode(mode: Any) -> Dict[str, Any]:
    """Validate a request ``mode`` into ``{"kind": ..., ...}``.

    ``"auto"`` survives as ``{"kind": "auto"}``; it is resolved against the
    part's own bed-fit suggestion once the geometry exists.
    """
    if mode is None:
        return {"kind": "auto"}
    if isinstance(mode, str):
        if mode.strip().lower() != "auto":
            raise ParamError(
                f"mode {mode!r} is not supported; use \"auto\", {{\"radial\": N}} or "
                "{\"planar\": [z, ...]}"
            )
        return {"kind": "auto"}
    if not isinstance(mode, Mapping):
        raise ParamError(f"mode must be \"auto\" or an object, got {type(mode).__name__}")

    keys = set(mode) - {"start_angle_deg"}
    if keys == {"radial"}:
        count = mode["radial"]
        if isinstance(count, bool) or not isinstance(count, int):
            raise ParamError(f"mode.radial must be an integer, got {count!r}")
        if not 2 <= count <= MAX_RADIAL_SEGMENTS:
            raise ParamError(
                f"mode.radial must be between 2 and {MAX_RADIAL_SEGMENTS}, got {count}"
            )
        start = mode.get("start_angle_deg", 0.0)
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise ParamError(
                f"mode.start_angle_deg must be a number, got {start!r}"
            )
        return {"kind": "radial", "count": int(count), "start_angle_deg": float(start)}

    if keys == {"planar"}:
        raw = mode["planar"]
        if not isinstance(raw, (list, tuple)):
            raise ParamError(
                f"mode.planar must be a list of Z heights in mm, got "
                f"{type(raw).__name__}"
            )
        heights: List[float] = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ParamError(f"mode.planar entries must be numbers, got {value!r}")
            number = float(value)
            if not math.isfinite(number):
                raise ParamError(f"mode.planar entries must be finite, got {value!r}")
            heights.append(number)
        if len(heights) > MAX_PLANAR_CUTS:
            raise ParamError(
                f"mode.planar asks for {len(heights)} cuts; the ceiling is "
                f"{MAX_PLANAR_CUTS}"
            )
        heights.sort()
        for a, b in zip(heights, heights[1:]):
            if abs(b - a) < 1e-6:
                raise ParamError(f"mode.planar has duplicate cuts at z={a}")
        return {"kind": "planar", "heights": heights}

    raise ParamError(
        f"mode must be \"auto\", {{\"radial\": N}} or {{\"planar\": [z, ...]}}; got keys "
        f"{sorted(mode)}"
    )


def resolve_auto_mode(suggestion: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn a :func:`checks.suggest_segmentation` result into a concrete mode."""
    if not suggestion.get("feasible") or not suggestion.get("mode"):
        raise ParamError(
            "mode \"auto\" cannot propose a segmentation for this part: "
            + str(suggestion.get("reason", "no reason given"))
        )
    return normalize_mode(suggestion["mode"])


# --------------------------------------------------------------------------
# Regions and cut frames
# --------------------------------------------------------------------------


def _bbox(shape: Any) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    box = shape.bounding_box()
    return (
        (float(box.min.X), float(box.min.Y), float(box.min.Z)),
        (float(box.max.X), float(box.max.Y), float(box.max.Z)),
    )


def _plan_radial(
    shape: Any, count: int, start_angle: float
) -> Tuple[List[Any], List[CutFrame]]:
    from build123d import Cylinder, Pos, Rot  # noqa: PLC0415

    low, high = _bbox(shape)
    centre_x = (low[0] + high[0]) / 2.0
    centre_y = (low[1] + high[1]) / 2.0
    radius = (
        math.hypot(high[0] - low[0], high[1] - low[1]) / 2.0 + REGION_OVERSHOOT_MM * 2.0
    )
    z_low = low[2] - REGION_OVERSHOOT_MM
    height = (high[2] - low[2]) + 2.0 * REGION_OVERSHOOT_MM
    step = 360.0 / count

    regions = [
        Pos(centre_x, centre_y, z_low)
        * Rot(0.0, 0.0, start_angle + index * step)
        * Cylinder(radius, height, arc_size=step, align=None)
        for index in range(count)
    ]

    frames: List[CutFrame] = []
    for index in range(count):
        angle = start_angle + index * step
        frames.append(
            CutFrame(
                name=f"cut_{index + 1}",
                kind="radial",
                to_world=Pos(centre_x, centre_y, 0.0) * Rot(0.0, 0.0, angle),
                u_min=0.0,
                u_max=radius,
                v_min=low[2],
                v_max=high[2],
                side_a=(index - 1) % count,
                side_b=index,
            )
        )
    return regions, frames


def _plan_planar(shape: Any, heights: Sequence[float]) -> Tuple[List[Any], List[CutFrame]]:
    from build123d import Box, Pos, Rot  # noqa: PLC0415

    low, high = _bbox(shape)
    centre_x = (low[0] + high[0]) / 2.0
    centre_y = (low[1] + high[1]) / 2.0
    span = (
        max(high[0] - low[0], high[1] - low[1]) + 4.0 * REGION_OVERSHOOT_MM
    )

    for z in heights:
        if not low[2] + 1e-6 < z < high[2] - 1e-6:
            raise ParamError(
                f"planar cut at z={z} is outside the part, which spans "
                f"z={low[2]:.3f} to {high[2]:.3f} mm"
            )

    edges = [low[2] - REGION_OVERSHOOT_MM, *heights, high[2] + REGION_OVERSHOOT_MM]
    regions = []
    for lower, upper in zip(edges, edges[1:]):
        thickness = upper - lower
        regions.append(
            Pos(centre_x, centre_y, (lower + upper) / 2.0) * Box(span, span, thickness)
        )

    frames: List[CutFrame] = []
    for index, z in enumerate(heights):
        below = z - edges[index]
        above = edges[index + 2] - z
        frames.append(
            CutFrame(
                name=f"cut_{index + 1}",
                kind="planar",
                # local +Y -> world +Z, local X -> world X, local Z -> world -Y.
                to_world=Pos(centre_x, centre_y, z) * Rot(90.0, 0.0, 0.0),
                u_min=low[0] - centre_x,
                u_max=high[0] - centre_x,
                v_min=-(high[1] - centre_y),
                v_max=-(low[1] - centre_y),
                side_a=index,
                side_b=index + 1,
                depth_available=min(below, above),
            )
        )
    return regions, frames


def _measure_face(shape: Any, frame: CutFrame) -> None:
    """Shrink a frame's extents to the actual cross-section at the cut."""
    from build123d import Box, Pos  # noqa: PLC0415

    # A radial frame's u range already runs from the central axis out past the
    # part, and must not be extended backwards: negative u is the far side of
    # the ring, whose material is not part of this cut face.
    u_low, u_high = frame.u_min, frame.u_max
    if frame.kind != "radial":
        u_low -= REGION_OVERSHOOT_MM
        u_high += REGION_OVERSHOOT_MM
    v_low = frame.v_min - REGION_OVERSHOOT_MM
    v_high = frame.v_max + REGION_OVERSHOOT_MM

    slab = (
        frame.to_world
        * Pos((u_low + u_high) / 2.0, 0.0, (v_low + v_high) / 2.0)
        * Box(u_high - u_low, SECTION_SLAB_MM, v_high - v_low)
    )

    section = shape & slab
    if not _has_volume(section, tolerance=1e-12):
        raise ParamError(
            f"{frame.name}: the cut plane passes through no material; nothing to join"
        )

    local = frame.to_world.inverse() * section
    box = local.bounding_box()
    frame.u_min = float(box.min.X)
    frame.u_max = float(box.max.X)
    frame.v_min = float(box.min.Z)
    frame.v_max = float(box.max.Z)
    frame.material_probe = _make_material_probe(shape, frame)


def _make_material_probe(shape: Any, frame: CutFrame):
    """Ask whether the cut face actually has material at a local (u, v) point.

    A joint placed on the middle of a cut face is placed on a *hole* when the
    face is an annulus (a planar cut through a tube) or otherwise pierced.  The
    boolean would then quietly do nothing, so every joint asks first.
    """

    def probe(u: float, v: float, half: float) -> bool:
        from build123d import Box, Pos  # noqa: PLC0415

        side = max(2.0 * half, 0.2)
        window = (
            frame.to_world
            * Pos(u, 0.0, v)
            * Box(side, 10.0 * SECTION_SLAB_MM, side)
        )
        return _has_volume(shape & window, tolerance=1e-12)

    return probe


def _has_volume(shape: Any, tolerance: float = 1e-9) -> bool:
    """Does this boolean result contain any material?

    An empty intersection is not merely a shape with zero volume: build123d
    0.11's ``Shape.wrapped`` *asserts* rather than returning ``None`` for one,
    so both the access and the measurement have to be guarded.
    """
    if shape is None:
        return False
    try:
        if shape.wrapped is None:
            return False
        return float(shape.volume) > tolerance
    except Exception:  # noqa: BLE001 - an empty result raises, and empty is the answer
        return False


# --------------------------------------------------------------------------
# The cut itself
# --------------------------------------------------------------------------


def segment_shape(
    shape: Any,
    mode: Mapping[str, Any],
    joint_spec: Mapping[str, Any],
) -> Dict[str, Any]:
    """Cut *shape* and return the segments plus the hardware they need.

    Returns ``{"items": [{"name", "kind", "solid"}], "cuts": [...],
    "mode": ...}``.  ``kind`` is ``"segment"`` for a piece of the part and
    ``"hardware"`` for a pin printed to join two of them.
    """
    kind = mode["kind"]
    if kind == "radial":
        regions, frames = _plan_radial(shape, mode["count"], mode["start_angle_deg"])
        step = 360.0 / mode["count"]
    elif kind == "planar":
        regions, frames = _plan_planar(shape, mode["heights"])
        step = None
    else:  # pragma: no cover - normalize_mode has already rejected anything else
        raise ParamError(f"cannot segment with mode kind {kind!r}")

    raw: List[Any] = []
    for index, region in enumerate(regions):
        piece = shape & region
        if not _has_volume(piece):
            raise ParamError(
                f"segment {index + 1} of {len(regions)} came out empty; the cut "
                "misses the part (check the radial count or the planar heights)"
            )
        raw.append(piece)

    if len(raw) == 1:
        return {
            "items": [{"name": "segment_1", "kind": "segment", "solid": raw[0]}],
            "cuts": [],
            "joint": dict(joint_spec),
        }

    add: Dict[int, List[Any]] = {i: [] for i in range(len(raw))}
    subtract: Dict[int, List[Any]] = {i: [] for i in range(len(raw))}
    hardware: List[Tuple[str, Any]] = []
    cut_reports: List[Dict[str, Any]] = []

    for frame in frames:
        _measure_face(shape, frame)
        if frame.kind == "radial" and step is not None:
            mid_radius = max(frame.u_centre, 1e-6)
            frame.depth_available = mid_radius * math.tan(math.radians(min(step, 89.0)))

        planned = plan_joint(joint_spec, frame)
        tools = build_joint(planned, frame, shape, regions[frame.side_b])
        _accumulate(tools, frame, add, subtract, hardware)
        report = frame.summary()
        report["joint"] = tools.resolved
        cut_reports.append(report)

    items: List[Dict[str, Any]] = []
    for index, piece in enumerate(raw):
        solid = piece
        for tool in subtract[index]:
            solid = solid - tool
        for tool in add[index]:
            solid = solid + tool
        if not _has_volume(solid):
            raise ScriptError(
                f"segment_{index + 1} is empty after its joints were applied; the "
                "joint is larger than the material at that cut"
            )
        items.append(
            {"name": f"segment_{index + 1}", "kind": "segment", "solid": solid}
        )

    for name, solid in hardware:
        items.append({"name": name, "kind": "hardware", "solid": solid})

    return {"items": items, "cuts": cut_reports, "joint": dict(joint_spec)}


def _accumulate(
    tools: JointTools,
    frame: CutFrame,
    add: Dict[int, List[Any]],
    subtract: Dict[int, List[Any]],
    hardware: List[Tuple[str, Any]],
) -> None:
    add[frame.side_a].extend(tools.add_to_a)
    subtract[frame.side_a].extend(tools.sub_from_a)
    subtract[frame.side_b].extend(tools.sub_from_b)
    hardware.extend(tools.hardware)


# --------------------------------------------------------------------------
# Orientation about Z
# --------------------------------------------------------------------------


def _convex_hull(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Andrew's monotone chain, on rounded points so ties do not multiply."""
    unique = sorted({(round(x, 6), round(y, 6)) for x, y in points})
    if len(unique) < 3:
        return unique

    def half(sequence):
        chain: List[Tuple[float, float]] = []
        for point in sequence:
            while len(chain) >= 2:
                (x1, y1), (x2, y2) = chain[-2], chain[-1]
                cross = (x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)
                if cross <= 0:
                    chain.pop()
                else:
                    break
            chain.append(point)
        return chain

    lower = half(unique)
    upper = half(reversed(unique))
    return lower[:-1] + upper[:-1]


def min_area_orientation(points: Sequence[Sequence[float]]) -> float:
    """The Z rotation, in degrees, that gives the tightest XY bounding box.

    A radial wedge comes out of the cut wherever its arc happens to sit, so its
    axis-aligned footprint can be the full outer diameter even though the wedge
    itself is slim.  Spinning it about the build axis costs nothing in print
    terms and is often the difference between fitting the bed and not.

    The minimum-area enclosing rectangle always has a side on a hull edge, so
    only the hull-edge directions are tried.
    """
    flat = [(float(p[0]), float(p[1])) for p in points]
    hull = _convex_hull(flat)
    if len(hull) < 3:
        return 0.0

    best_angle = 0.0
    best_area = math.inf
    for index in range(len(hull)):
        ax, ay = hull[index]
        bx, by = hull[(index + 1) % len(hull)]
        edge = math.hypot(bx - ax, by - ay)
        if edge < 1e-9:
            continue
        cos_a, sin_a = (bx - ax) / edge, (by - ay) / edge
        # Rotate the hull so this edge lies along +X.
        us = [px * cos_a + py * sin_a for px, py in hull]
        vs = [-px * sin_a + py * cos_a for px, py in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if area < best_area - 1e-9:
            best_area = area
            # Rotating the *part* by -edge_angle is what aligns that edge to +X.
            best_angle = -math.degrees(math.atan2(by - ay, bx - ax))
    return round(best_angle % 360.0, 6)


def rotated_bounds(
    points: Sequence[Sequence[float]], angle_deg: float
) -> Tuple[float, float]:
    """XY footprint of *points* after rotating them by *angle_deg* about Z."""
    radians = math.radians(angle_deg)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    xs = [float(p[0]) * cos_a - float(p[1]) * sin_a for p in points]
    ys = [float(p[0]) * sin_a + float(p[1]) * cos_a for p in points]
    if not xs:
        return 0.0, 0.0
    return max(xs) - min(xs), max(ys) - min(ys)


# --------------------------------------------------------------------------
# Plate packing
# --------------------------------------------------------------------------


def pack_plate(
    items: Sequence[Mapping[str, Any]],
    printer: Mapping[str, Any],
    margin_mm: float = DEFAULT_PLATE_MARGIN_MM,
    spacing_mm: float = DEFAULT_PLATE_SPACING_MM,
) -> Dict[str, Any]:
    """Shelf-pack part footprints onto the bed.

    *items* are ``{"name", "size_mm": [x, y, z]}``.  Each part may take a
    quarter turn about Z; parts are laid widest-first into rows.  The result
    gives, per item, the quarter turn and the position its bounding-box minimum
    corner should be moved to, with Z always 0 so everything sits on the plate.

    Raises :class:`ScriptError` when a part is taller than the bed, wider than
    the bed on its own, or when the rows run off the back of the plate.
    """
    bed_x, bed_y, bed_z = bed_size(printer)
    usable_x = bed_x - 2.0 * margin_mm
    usable_y = bed_y - 2.0 * margin_mm
    if usable_x <= 0 or usable_y <= 0:
        raise ParamError(
            f"a {margin_mm:g} mm margin leaves no usable area on a "
            f"{bed_x:g}x{bed_y:g} mm bed"
        )

    prepared = []
    for index, item in enumerate(items):
        size = [float(v) for v in item["size_mm"]]
        if size[2] > bed_z:
            raise ScriptError(
                f"{item['name']} is {size[2]:.1f} mm tall, over the {bed_z:g} mm bed "
                "height; segment it further or lay it on its side"
            )
        rotate = size[1] > size[0]
        width, depth = (size[1], size[0]) if rotate else (size[0], size[1])
        if width > usable_x or depth > usable_y:
            raise ScriptError(
                f"{item['name']} is {size[0]:.1f}x{size[1]:.1f} mm and does not fit "
                f"the usable bed ({usable_x:g}x{usable_y:g} mm) even alone; segment "
                "it further"
            )
        prepared.append(
            {
                "index": index,
                "name": item["name"],
                "pre_rotate_deg": float(item.get("orient_deg") or 0.0),
                "rotate_deg": 90.0 if rotate else 0.0,
                "width": width,
                "depth": depth,
                "height": size[2],
            }
        )

    order = sorted(prepared, key=lambda p: (-p["depth"], -p["width"], p["index"]))

    placements: List[Dict[str, Any]] = []
    cursor_x = margin_mm
    cursor_y = margin_mm
    row_depth = 0.0
    rows = 1
    used_x = 0.0

    for entry in order:
        if cursor_x > margin_mm and cursor_x + entry["width"] > margin_mm + usable_x:
            cursor_x = margin_mm
            cursor_y += row_depth + spacing_mm
            row_depth = 0.0
            rows += 1
        if cursor_y + entry["depth"] > margin_mm + usable_y:
            raise ScriptError(
                f"the segments do not fit on one {bed_x:g}x{bed_y:g} mm plate: "
                f"{entry['name']} would run off the back. Cut into more segments or "
                "raise the bed size in the printer profile."
            )
        placements.append(
            {
                "name": entry["name"],
                "index": entry["index"],
                "pre_rotate_deg": entry["pre_rotate_deg"],
                "rotate_deg": entry["rotate_deg"],
                "position_mm": [round(cursor_x, 4), round(cursor_y, 4), 0.0],
                "size_mm": [
                    round(entry["width"], 4),
                    round(entry["depth"], 4),
                    round(entry["height"], 4),
                ],
            }
        )
        cursor_x += entry["width"] + spacing_mm
        used_x = max(used_x, cursor_x - spacing_mm - margin_mm)
        row_depth = max(row_depth, entry["depth"])

    placements.sort(key=lambda p: p["index"])
    used_y = cursor_y + row_depth - margin_mm

    return {
        "bed_mm": [bed_x, bed_y, bed_z],
        "margin_mm": margin_mm,
        "spacing_mm": spacing_mm,
        "rows": rows,
        "used_mm": [round(used_x, 3), round(used_y, 3)],
        "fits": True,
        "items": placements,
    }


def place_on_plate(solid: Any, placement: Mapping[str, Any]) -> Any:
    """Apply one :func:`pack_plate` placement to a solid."""
    from build123d import Pos, Rot  # noqa: PLC0415

    spin = float(placement.get("pre_rotate_deg") or 0.0) + float(
        placement["rotate_deg"]
    )
    moved = Rot(0.0, 0.0, spin) * solid if spin else solid
    box = moved.bounding_box()
    target = placement["position_mm"]
    return (
        Pos(
            float(target[0]) - float(box.min.X),
            float(target[1]) - float(box.min.Y),
            float(target[2]) - float(box.min.Z),
        )
        * moved
    )


def drop_to_origin(solid: Any, spin_deg: float = 0.0) -> Any:
    """Centre a solid over the origin in XY and sit it on Z=0.

    ``spin_deg`` rotates it about the build axis first, which is how a radial
    wedge gets written out in the orientation that actually fits the bed.
    """
    from build123d import Pos, Rot  # noqa: PLC0415

    if spin_deg:
        solid = Rot(0.0, 0.0, float(spin_deg)) * solid
    box = solid.bounding_box()
    return (
        Pos(
            -(float(box.min.X) + float(box.max.X)) / 2.0,
            -(float(box.min.Y) + float(box.max.Y)) / 2.0,
            -float(box.min.Z),
        )
        * solid
    )


__all__ = [
    "MAX_PLANAR_CUTS",
    "MAX_RADIAL_SEGMENTS",
    "drop_to_origin",
    "min_area_orientation",
    "normalize_mode",
    "pack_plate",
    "place_on_plate",
    "resolve_auto_mode",
    "rotated_bounds",
    "segment_shape",
]
