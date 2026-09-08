"""The silicone half of mold mode: the pour box, and the workflow in plain words.

``mode: "printed_negative"`` -- :mod:`service.mold` -- prints the *negative*: two
halves with the part's shape cut out of them.  It is exact, it is rigid, and a
rigid mold only opens if nothing on the part hangs back over it.  Figures hang
back over everything.

``mode: "master_box"`` is the other route, and the one figure casters actually
use.  Nothing is subtracted from anything: this mode prints **two separate
objects** -- the master (the figure itself, untouched) and an open-topped box
big enough to hold it with room to spare.  The caster glues the master to the
pad on the box floor, pours liquid silicone in until it stands over the
figure's highest point, waits for it to cure, and pulls out a rubber block with
the figure's shape inside it.  Rubber bends, so the overhangs that made the
rigid mold impossible are not a problem any more.

The box, piece by piece
-----------------------

* **Interior** -- the master's bounding box grown by ``margin_mm`` (default 10)
  on all four sides, its floor dropped by ``platform_mm`` so the master sits on
  the pad, and its ceiling raised ``pour_clearance_mm`` (default 15) above the
  master's highest point.  That headroom is the silicone over the top of the
  figure: it is the roof of the finished mold, and a thin one tears.
* **Walls and floor** -- ``wall_mm`` thick, refused below the printer's minimum
  feature size and defaulting to whichever is thicker: 3 mm or two of the
  printer's minimum walls.  No lid: the top is open, because that is where the
  silicone goes in.
* **The pad** -- a short cone on the floor, wide at the bottom, that the master
  is glued to.  It is not decoration: after the silicone cures and the master
  comes out, the pad's shape is the **pour hole** of the mold, funnel-side up.
  Skip it (``platform_mm: 0``) and the finished mold has a flat closed bottom
  with no way to get resin in.
* **Corner funnel** -- a flared collar over one interior corner.  Silicone is
  poured into it as a thin stream so it climbs the box and floods the figure
  from below rather than falling on top of it and trapping air.  On a split box
  the collar is shortened so its flare stays on its own side of the seam, and
  left off with a note when it cannot: a collar reaching across the split prints
  as a loose chip on the other half.
* **``split: true``** -- the box is cut down the middle in X into ``box_a`` and
  ``box_b`` with the same spherical registration keys ``/mold`` uses, so a
  stiff mold can be peeled off in two pieces instead of fought out of a
  one-piece bucket.  The seam runs through the floor and the pad too, so tape
  or a band around the closed box is not optional, and the cured block will
  carry a hairline of flash along the seam.

Workflow assumption, stated once
--------------------------------

**The master is glued down.**  It is not suspended on wires or keyed into the
floor; the pad is a landing spot and a pour hole, and one dab of hot glue holds
the figure against the silicone trying to float it.  Every instruction this
module writes assumes that.

Geometry is built through build123d and only ever runs inside the worker; the
option validation and the instruction text are plain Python and run anywhere.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ParamError, ScriptError
from .printer import bed_size, tolerance as printer_tolerance

Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

#: Gap between the master and the inside of the wall, per side.  This is the
#: thickness of the silicone around the figure; thinner than about 8 mm and the
#: mold walls tear when you flex them.
DEFAULT_MARGIN_MM = 10.0

#: Box wall thickness, before the printer profile has its say.
DEFAULT_WALL_MM = 3.0

#: Silicone kept over the master's highest point: the roof of the mold.
DEFAULT_POUR_CLEARANCE_MM = 15.0

#: Height of the pad the master is glued to, which becomes the pour hole.
DEFAULT_PLATFORM_MM = 3.0

#: Corner pour funnels.  One is enough; a wide box sometimes wants two.
DEFAULT_FUNNELS = 1
MAX_FUNNELS = 4

#: How tall the funnel collar stands above the rim, and how much it flares.
FUNNEL_HEIGHT_MM = 8.0
FUNNEL_DRAFT_DEG = 25.0

#: Clearance kept between a funnel collar and a split plane, and the shortest
#: collar worth building.  A collar that reaches over the split leaves a loose
#: chip of itself on the far half; see :func:`build_master_box`.  A collar
#: squeezed below half its design height is a stub, not a funnel -- silicone
#: pours straight over it -- so it is left off and the note says why.
FUNNEL_SPLIT_GAP_MM = 1.0
MIN_FUNNEL_RISE_MM = FUNNEL_HEIGHT_MM / 2.0

#: The pad's own draft, so it releases from the cured silicone.
PLATFORM_DRAFT_DEG = 25.0

#: Ceilings, so a typo cannot ask for a metre of anything.
MAX_MARGIN_MM = 200.0
MAX_WALL_MM = 50.0
MAX_CLEARANCE_MM = 200.0
MAX_PLATFORM_MM = 50.0

#: Registration keys on a split box's parting face.
DEFAULT_SPLIT_KEYS = 4
MAX_SPLIT_KEYS = 12

MODES = ("printed_negative", "master_box")


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


def _number(
    value: Any, label: str, minimum: float, maximum: float, default: float
) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamError(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ParamError(f"{label} must be finite, got {value!r}")
    if not minimum <= number <= maximum:
        raise ParamError(
            f"{label} must sit between {minimum:g} and {maximum:g}, got {number:g}"
        )
    return number


def normalize_mode(value: Any) -> str:
    """``mode``: which of the two molds the caller wants.

    Args:
        value: The mode name; None defaults to "printed_negative".

    Returns:
        The normalized mode name: "printed_negative" or "master_box".
    """
    if value is None:
        return "printed_negative"
    if not isinstance(value, str):
        raise ParamError(
            f'mode must be one of {" | ".join(MODES)}, got {type(value).__name__}'
        )
    mode = value.strip().lower()
    if mode not in MODES:
        raise ParamError(f'mode must be one of {" | ".join(MODES)}, got {value!r}')
    return mode


def default_wall_mm(printer: Mapping[str, Any]) -> float:
    """The thinnest box wall this printer has any business being asked for.

    Args:
        printer: Printer configuration mapping containing min_wall_thickness and min_feature_size.

    Returns:
        The minimum wall thickness in millimetres.
    """
    return max(
        DEFAULT_WALL_MM,
        2.0 * float(printer["min_wall_thickness"]),
        float(printer["min_feature_size"]),
    )


def normalize_master_box_options(
    job: Mapping[str, Any], printer: Mapping[str, Any]
) -> Dict[str, Any]:
    """Validate the pour-box fields.  Harmless to call in either mode.

    Args:
        job: Job configuration mapping; may contain a "master_box" key with mold options.
        printer: Printer configuration mapping.

    Returns:
        A dictionary of validated master_box options including margin_mm, wall_mm, floor_mm,
        pour_clearance_mm, platform_mm, funnels, split, and registration_keys.
    """
    source = job.get("master_box")
    if source is None:
        source = job
    elif not isinstance(source, Mapping):
        raise ParamError(
            f"master_box must be an object, got {type(source).__name__}"
        )

    min_feature = float(printer["min_feature_size"])
    wall = _number(
        source.get("wall_mm"), "wall_mm", min_feature, MAX_WALL_MM, default_wall_mm(printer)
    )
    floor = _number(
        source.get("floor_mm"), "floor_mm", min_feature, MAX_WALL_MM, wall
    )

    keys = source.get("registration_keys")
    if keys is None:
        keys = DEFAULT_SPLIT_KEYS
    if isinstance(keys, bool) or not isinstance(keys, int):
        raise ParamError(
            f"master_box registration_keys must be an integer, got {keys!r}"
        )
    if not 0 <= keys <= MAX_SPLIT_KEYS:
        raise ParamError(
            f"master_box registration_keys must sit between 0 and {MAX_SPLIT_KEYS}, "
            f"got {keys}"
        )

    funnels = source.get("funnels")
    if funnels is None:
        funnels = DEFAULT_FUNNELS
    if funnels is True:
        funnels = DEFAULT_FUNNELS
    elif funnels is False:
        funnels = 0
    elif not isinstance(funnels, int):
        raise ParamError(f"funnels must be an integer or a boolean, got {funnels!r}")
    if not 0 <= funnels <= MAX_FUNNELS:
        raise ParamError(f"funnels must sit between 0 and {MAX_FUNNELS}, got {funnels}")

    split = source.get("split")
    if split is None:
        split = False
    if not isinstance(split, bool):
        raise ParamError(f"split must be true or false, got {split!r}")

    return {
        "margin_mm": _number(
            source.get("margin_mm"), "margin_mm", 0.5, MAX_MARGIN_MM, DEFAULT_MARGIN_MM
        ),
        "wall_mm": wall,
        "floor_mm": floor,
        "pour_clearance_mm": _number(
            source.get("pour_clearance_mm"),
            "pour_clearance_mm",
            0.0,
            MAX_CLEARANCE_MM,
            DEFAULT_POUR_CLEARANCE_MM,
        ),
        "platform_mm": _number(
            source.get("platform_mm"),
            "platform_mm",
            0.0,
            MAX_PLATFORM_MM,
            DEFAULT_PLATFORM_MM,
        ),
        "funnels": int(funnels),
        "split": bool(split),
        "registration_keys": int(keys),
    }


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def check_bed(
    printer: Mapping[str, Any],
    pieces: Sequence[Tuple[str, Sequence[float]]],
    margin_mm: float,
) -> Dict[str, Any]:
    """Every printed piece has to fit the bed; the box is the one that usually does not.

    Args:
        printer: Printer configuration mapping containing bed dimensions.
        pieces: Sequence of (name, [width, depth, height]) tuples for each piece to check.
        margin_mm: Safety margin to keep from the bed edges in millimetres.

    Returns:
        A dictionary containing the bed dimensions and rounded size of each piece.
    """
    bed_x, bed_y, bed_z = bed_size(printer)
    usable_x = bed_x - 2.0 * margin_mm
    usable_y = bed_y - 2.0 * margin_mm
    report: Dict[str, Any] = {"bed_mm": [bed_x, bed_y, bed_z], "margin_mm": margin_mm}
    for name, size in pieces:
        width, depth, height = (float(size[0]), float(size[1]), float(size[2]))
        flat = (width <= usable_x and depth <= usable_y) or (
            depth <= usable_x and width <= usable_y
        )
        if not flat or height > bed_z:
            raise ScriptError(
                f"{name} is {width:.1f}x{depth:.1f}x{height:.1f} mm, over the usable "
                f"bed ({usable_x:g}x{usable_y:g}x{bed_z:g} mm). Lower margin_mm or "
                "pour_clearance_mm, split the box (split: true) and print the two "
                "halves separately, or scale the figure down."
            )
        report[name] = [round(width, 3), round(depth, 3), round(height, 3)]
    return report


def _box_between(low: Sequence[float], high: Sequence[float]) -> Any:
    """An axis-aligned box from *low* to *high*, in world millimetres."""
    from build123d import Box, Pos  # noqa: PLC0415

    size = [float(high[i]) - float(low[i]) for i in range(3)]
    centre = [(float(low[i]) + float(high[i])) / 2.0 for i in range(3)]
    return Pos(*centre) * Box(*size)


def _frustum(centre: Vec2, z_low: float, z_high: float, r_low: float, r_high: float) -> Any:
    """A cone frustum standing on its own axis between two heights."""
    from build123d import Cone, Pos  # noqa: PLC0415

    height = z_high - z_low
    return Pos(centre[0], centre[1], (z_low + z_high) / 2.0) * Cone(
        bottom_radius=max(r_low, 1e-4),
        top_radius=max(r_high, 1e-4),
        height=height,
    )


def _keep_half(solid: Any, plane: Any, keep: Any, name: str) -> Any:
    """One side of a plane cut, as a single solid.

    ``Shape.split`` answers a solid, a list of them, or ``None`` depending on
    what the cut found.  **More than one solid is a refusal, not something to
    fuse**: two disjoint lumps that merely touch fuse into a shape OCC will not
    certify, and even if they did, the small one is a chip that falls off the
    print bed.  The funnel clamp above is what stops this happening; this is the
    net under it, with words that say which knob to turn.
    """
    result = solid.split(plane, keep=keep)
    if isinstance(result, (list, tuple)):
        parts = [part for part in result if part is not None]
        if len(parts) == 1:
            return parts[0]
        if not parts:  # pragma: no cover - the plane runs through the box
            raise ScriptError(f"{name} came out empty; the split plane missed the box")
        sizes = ", ".join(f"{float(part.volume):.1f} mm3" for part in parts)
        raise ScriptError(
            f"{name} came apart into {len(parts)} separate pieces ({sizes}) rather "
            "than one: something on the box reaches across the split plane and "
            "would print as a loose fragment. Lower funnels, raise margin_mm, or "
            "set split to false."
        )
    if result is None:  # pragma: no cover - same
        raise ScriptError(f"{name} came out empty; the split plane missed the box")
    return result


def build_master_box(
    shape: Any,
    stats: Mapping[str, Any],
    printer: Mapping[str, Any],
    options: Mapping[str, Any],
    margin_mm: float,
) -> Dict[str, Any]:
    """The pour box (and the untouched master) as solids, plus the report.

    Returns ``{"pieces": [{"name", "solid"}], "geometry": {...}, "bed": {...}}``.
    The caller tessellates and verifies each piece, exactly as ``/mold`` does
    with its two halves.

    Args:
        shape: The master shape as a build123d solid.
        stats: Statistics mapping containing bounding_box_min_mm and bounding_box_max_mm.
        printer: Printer configuration mapping.
        options: Options mapping containing a "master_box" key with mold parameters.
        margin_mm: Safety margin in millimetres (used to compute bed report).

    Returns:
        A dictionary containing "pieces" (list of {name, solid}), "geometry" (detailed
        dimensions and specifications), and "bed" (bed fit report with rounded dimensions).
    """
    from build123d import Pos, Sphere  # noqa: PLC0415

    from .mold import contains, has_volume, solid_volume  # noqa: PLC0415

    box_options = dict(options["master_box"])
    low = [float(v) for v in stats["bounding_box_min_mm"]]
    high = [float(v) for v in stats["bounding_box_max_mm"]]
    size = [high[i] - low[i] for i in range(3)]
    centre: Vec2 = ((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0)

    margin = float(box_options["margin_mm"])
    wall = float(box_options["wall_mm"])
    floor = float(box_options["floor_mm"])
    clearance = float(box_options["pour_clearance_mm"])
    platform = float(box_options["platform_mm"])
    min_feature = float(printer["min_feature_size"])

    # -- the empty volume the silicone fills -------------------------------
    interior_low = (low[0] - margin, low[1] - margin, low[2] - platform)
    interior_high = (high[0] + margin, high[1] + margin, high[2] + clearance)
    outer_low = (
        interior_low[0] - wall,
        interior_low[1] - wall,
        interior_low[2] - floor,
    )
    outer_high = (interior_high[0] + wall, interior_high[1] + wall, interior_high[2])

    if interior_high[2] - interior_low[2] <= 0.0:  # pragma: no cover - bbox is positive
        raise ScriptError("the master has no height; there is nothing to mold")

    shell = _box_between(outer_low, outer_high)
    # The interior is cut open through the top: the box has no lid, and a
    # coplanar face at the rim is how a boolean comes back non-manifold.
    cavity = _box_between(
        interior_low, (interior_high[0], interior_high[1], interior_high[2] + wall + 1.0)
    )
    box = shell - cavity
    if not has_volume(box):  # pragma: no cover - the walls always have volume
        raise ScriptError("the pour box came out empty; raise wall_mm")

    # -- the pad the master is glued to, which becomes the pour hole -------
    platform_report: Optional[Dict[str, Any]] = None
    if platform > 0.0:
        footprint = max(min(size[0], size[1]), 2.0 * min_feature)
        top_radius = max(min(footprint * 0.2, 8.0), min_feature)
        bottom_radius = min(
            top_radius + platform * math.tan(math.radians(PLATFORM_DRAFT_DEG)),
            footprint * 0.45,
        )
        bottom_radius = max(bottom_radius, top_radius)
        pad = _frustum(centre, interior_low[2], low[2], bottom_radius, top_radius)
        box = box + pad
        platform_report = {
            "height_mm": round(platform, 4),
            "top_diameter_mm": round(top_radius * 2.0, 4),
            "bottom_diameter_mm": round(bottom_radius * 2.0, 4),
            "centre_mm": [round(centre[0], 4), round(centre[1], 4)],
            "top_z_mm": round(low[2], 4),
            "role": "the master is glued to this; it becomes the mold's pour hole",
        }

    # -- corner pour funnels ------------------------------------------------
    split = bool(box_options["split"])
    split_x = (outer_low[0] + outer_high[0]) / 2.0
    funnel_count = int(box_options["funnels"])
    rim_z = interior_high[2]
    throat_radius = max(min(margin * 0.6, 8.0), 2.5)
    funnel_positions: List[Vec2] = []
    funnel_note: Optional[str] = None
    funnel_height = FUNNEL_HEIGHT_MM
    if funnel_count > 0:
        corners = [
            (interior_low[0], interior_low[1]),
            (interior_high[0], interior_high[1]),
            (interior_high[0], interior_low[1]),
            (interior_low[0], interior_high[1]),
        ][:funnel_count]
        skirt = 2.0 * wall
        tangent = math.tan(math.radians(FUNNEL_DRAFT_DEG))
        dropped = 0
        for corner in corners:
            total_height = FUNNEL_HEIGHT_MM + skirt
            if split:
                # A collar wide enough to reach across the split plane leaves a
                # loose chip of itself on the far half: two solids where the
                # printer wanted one.  Shrink the collar until it stays on its
                # own side, and if it cannot, skip that corner and say so.
                reach = abs(corner[0] - split_x) - FUNNEL_SPLIT_GAP_MM
                room = reach - throat_radius - wall
                total_height = min(total_height, room / tangent if room > 0.0 else 0.0)
            if total_height - skirt < MIN_FUNNEL_RISE_MM:
                dropped += 1
                continue
            rise = total_height - skirt
            flare = total_height * tangent
            throat = _frustum(
                corner,
                rim_z - skirt,
                rim_z + rise,
                throat_radius,
                throat_radius + flare,
            )
            collar = _frustum(
                corner,
                rim_z - skirt,
                rim_z + rise,
                throat_radius + wall,
                throat_radius + flare + wall,
            )
            box = (box + (collar - throat)) - throat
            funnel_positions.append(corner)
            funnel_height = min(funnel_height, rise)
        if not has_volume(box):  # pragma: no cover
            raise ScriptError(
                "the corner funnels ate the whole box; lower funnels or raise wall_mm"
            )
        if dropped:
            funnel_note = (
                f"{dropped} of {funnel_count} corner funnels were left off: a "
                "collar that reaches across the split plane would print as a "
                "loose chip on the other half. Raise margin_mm, lower wall_mm, "
                "or pour into a bare corner instead."
            )

    # -- optional split, with the same keys /mold uses ---------------------
    key_report: Optional[Dict[str, Any]] = None
    pieces: List[Dict[str, Any]] = [{"name": "master", "solid": shape}]

    if not split:
        pieces.append({"name": "box", "solid": box})
    else:
        from build123d import Keep, Plane  # noqa: PLC0415

        plane = Plane(origin=(split_x, 0.0, 0.0), z_dir=(1.0, 0.0, 0.0))

        key_count = int(box_options["registration_keys"])
        key_tolerance = printer_tolerance(printer, "press_fit", 0.1)
        key_radius = max(min(wall, floor) * 0.35, min_feature * 0.5)
        positions: List[Vec3] = []
        if key_count > 0:
            if key_radius * 2.0 > min(wall, floor):
                raise ScriptError(
                    f"a registration key needs about {key_radius * 2.0:.2f} mm of "
                    f"material and the thinnest of wall_mm/floor_mm is "
                    f"{min(wall, floor):g} mm; raise them or set registration_keys to 0"
                )
            per_wall = max(1, key_count // 2)
            wall_y = (
                interior_low[1] - wall / 2.0,
                interior_high[1] + wall / 2.0,
            )
            low_z = interior_low[2] + key_radius * 3.0
            high_z = rim_z - key_radius * 3.0
            for index in range(per_wall):
                fraction = (index + 1) / (per_wall + 1) if per_wall > 1 else 0.5
                z = low_z + (high_z - low_z) * fraction
                for y in wall_y:
                    positions.append((split_x, y, z))
            if key_count % 2:
                positions.append((split_x, centre[1], outer_low[2] + floor / 2.0))
            positions = positions[:key_count]
            for x, y, z in positions:
                if not contains(Pos(x, y, z) * Sphere(key_radius + key_tolerance), box):
                    raise ScriptError(
                        f"the registration key at ({x:.1f}, {y:.1f}, {z:.1f}) mm "
                        "does not sit inside the box wall; raise wall_mm or "
                        "floor_mm, or set the box's registration_keys to 0"
                    )

        # Order matters, and not for taste.  Carving a hemisphere into a face a
        # boolean has *just* created comes back as a solid OCC cannot clean --
        # measured on this build123d, mesh closed and oriented but
        # BRepCheck_Analyzer unhappy, which is exactly the silent-wrong-mold
        # failure mode the whole pipeline refuses.  Cutting the sockets out of
        # the whole box and splitting afterwards is valid, so that is the order.
        socketed = box
        for x, y, z in positions:
            socketed = socketed - Pos(x, y, z) * Sphere(key_radius + key_tolerance)

        box_a = _keep_half(box, plane, Keep.BOTTOM, "box_a")
        box_b = _keep_half(socketed, plane, Keep.TOP, "box_b")
        for x, y, z in positions:
            box_a = box_a + Pos(x, y, z) * Sphere(key_radius)
        for name, solid in (("box_a", box_a), ("box_b", box_b)):
            if not has_volume(solid):  # pragma: no cover - the split is central
                raise ScriptError(f"{name} came out empty; the split plane missed the box")

        key_report = {
            "count": len(positions),
            "radius_mm": round(key_radius, 4),
            "tolerance_mm": key_tolerance,
            "male_piece": "box_a",
            "female_piece": "box_b",
            "positions_mm": [[round(v, 4) for v in point] for point in positions],
        }
        pieces.append({"name": "box_a", "solid": box_a})
        pieces.append({"name": "box_b", "solid": box_b})

    interior_size = [
        interior_high[i] - interior_low[i] for i in range(3)
    ]
    silicone_volume = (
        interior_size[0] * interior_size[1] * interior_size[2] - solid_volume(shape)
    )

    geometry: Dict[str, Any] = {
        "margin_mm": margin,
        "wall_mm": wall,
        "floor_mm": floor,
        "pour_clearance_mm": clearance,
        "platform_mm": platform,
        "split": split,
        "open_top": True,
        "master_bbox_mm": [round(v, 4) for v in size],
        "master_min_mm": [round(v, 4) for v in low],
        "master_max_mm": [round(v, 4) for v in high],
        "interior_min_mm": [round(v, 4) for v in interior_low],
        "interior_max_mm": [round(v, 4) for v in interior_high],
        "interior_size_mm": [round(v, 4) for v in interior_size],
        "outer_min_mm": [round(v, 4) for v in outer_low],
        "outer_max_mm": [round(v, 4) for v in outer_high],
        "outer_size_mm": [
            round(outer_high[i] - outer_low[i], 4) for i in range(3)
        ],
        "platform": platform_report,
        "funnels": {
            "count": len(funnel_positions),
            "requested": int(box_options["funnels"]),
            "throat_diameter_mm": round(throat_radius * 2.0, 4),
            "height_mm": round(funnel_height, 4) if funnel_positions else 0.0,
            "draft_deg": FUNNEL_DRAFT_DEG,
            "positions_mm": [[round(x, 4), round(y, 4)] for x, y in funnel_positions],
            "rim_z_mm": round(rim_z, 4),
            "note": funnel_note,
        },
        "registration_keys": key_report,
        "silicone_volume_mm3": round(max(silicone_volume, 0.0), 2),
        "silicone_volume_ml": round(max(silicone_volume, 0.0) / 1000.0, 2),
    }
    return {"pieces": pieces, "geometry": geometry}


# --------------------------------------------------------------------------
# The workflow, in words a beginner can follow
# --------------------------------------------------------------------------


def printed_negative_instructions(mold: Mapping[str, Any]) -> List[str]:
    """Steps for the two printed halves ``/mold`` produces.

    Args:
        mold: Mold specification mapping containing registration_keys, vents, and spout.

    Returns:
        A list of instruction strings describing the printed_negative workflow.
    """
    keys = int((mold.get("registration_keys") or {}).get("count") or 0)
    vents = int((mold.get("vents") or {}).get("count") or 0)
    spout = mold.get("spout")

    close = (
        f"Press the halves together -- the {keys} bumps on mold_top drop into the "
        f"{keys} dimples in mold_bottom -- then band, clamp or tape them shut all "
        "the way round."
        if keys
        else "Press the halves together, line the edges up by eye, and band, clamp "
        "or tape them shut all the way round."
    )
    if spout:
        vent_line = f" and comes up in the {vents} vent holes." if vents else "."
        pour = (
            "Pour your casting resin into the spout in one thin, slow, steady "
            "stream. Stop when it reaches the top of the spout" + vent_line
        )
    else:
        pour = (
            "Pour your casting resin in slowly along one edge until the cavity "
            "is full."
        )

    return [
        "Print mold_top and mold_bottom. Use a small layer height (0.1 mm or "
        "finer) and no supports inside the cavity: every layer line in there "
        "shows up on every copy you cast.",
        "Sand the two cavity faces until they feel smooth, then wash the halves "
        "with warm soapy water and let them dry completely. Dust and grease both "
        "print themselves onto the casting.",
        "Brush a thin, even coat of mold release into both cavities and let it "
        "flash off. Printed plastic without release will bond to resin and you "
        "will lose the mold and the copy together.",
        close,
        "Mix your resin by the bottle's ratio, in a clean cup, stirring slowly "
        "for the time it says. Stirring fast whips in bubbles that end up as "
        "craters on the copy's surface.",
        pour,
        "Leave it flat and still for the full demold time on the bottle. Opening "
        "early tears the copy while it is still soft.",
        "Unclamp, ease the halves apart, and lift the copy out. Snip the spout "
        "and vent stubs off, then sand the parting line flush.",
        "Before the next copy: wipe the cavities out and re-coat with release. "
        "A printed mold is good for a handful of pulls, not a production run -- "
        'if you want dozens of copies, ask for mode: "master_box" and cast them '
        "in silicone instead.",
    ]


def master_box_instructions(geometry: Mapping[str, Any]) -> List[str]:
    """Steps for the print-a-master-and-pour-silicone workflow.

    Args:
        geometry: Geometry specification mapping from build_master_box containing pour_clearance_mm,
            split, platform_mm, silicone_volume_ml, funnels, and registration_keys.

    Returns:
        A list of instruction strings describing the master_box workflow.
    """
    clearance = float(geometry.get("pour_clearance_mm") or 0.0)
    split = bool(geometry.get("split"))
    platform = float(geometry.get("platform_mm") or 0.0)
    volume_ml = geometry.get("silicone_volume_ml")
    funnels = int((geometry.get("funnels") or {}).get("count") or 0)

    pieces = (
        "master.stl (the figure) and box_a.stl + box_b.stl (the two halves of "
        "the pour box)"
        if split
        else "master.stl (the figure) and box.stl (the pour box)"
    )
    keys = int((geometry.get("registration_keys") or {}).get("count") or 0)
    if split:
        mate = (
            f"the {keys} bumps on box_a drop into the {keys} dimples in box_b"
            if keys
            else "line the two halves up by their edges"
        )
        assemble = (
            f"Put the box together: {mate}. Run tape or two rubber bands right "
            "round the outside -- liquid silicone finds every gap, so seal the "
            "seam with a smear of hot glue along the inside of the joint as well."
        )
    else:
        assemble = (
            "Check the box for print gaps. Liquid silicone finds every one, so "
            "run a smear of hot glue along the inside corners of the floor if "
            "you can see daylight through them."
        )
    glue = (
        "Glue the master onto the little pad in the middle of the box floor -- "
        "one dab of hot glue or a drop of super glue under its base. It must "
        "not float, tip or shift: silicone will try to lift it. That pad is not "
        "decoration; it becomes the hole you pour resin through later."
        if platform > 0.0
        else "Glue the master flat onto the middle of the box floor with a dab of "
        "hot glue or super glue. It must not float, tip or shift when the "
        "silicone goes in."
    )
    pour_target = (
        f"until it stands {clearance:g} mm above the highest point of the figure"
        if clearance > 0.0
        else "until the figure is just covered"
    )
    pour_where = (
        "Pour into the flared corner funnel, never straight onto the figure"
        if funnels
        else "Pour into one corner of the box, never straight onto the figure"
    )
    volume_line = (
        f" You need roughly {volume_ml:g} ml of silicone to fill this box; mix a "
        "little extra rather than running out halfway."
        if isinstance(volume_ml, (int, float)) and volume_ml
        else ""
    )
    demold = (
        "Undo the tape and take box_a and box_b apart. The silicone block lifts "
        "straight out."
        if split
        else "Flex the box walls outward and work the silicone block out. It will "
        "come; rubber is more patient than you are."
    )

    return [
        f"Print two things: {pieces}. Print the master standing the way it looks "
        "best, at a small layer height (0.1 mm or finer) -- every layer line on "
        "the master is copied into every casting you will ever pull.",
        "Finish the master properly: sand it, fill any layer lines you can feel, "
        "then give it two light coats of primer or clear lacquer and let it dry "
        "hard. This is the one step people skip and the one that decides how "
        "good the copies look.",
        assemble,
        glue,
        "Brush or spray a light coat of mold release over the master and the "
        "inside of the box, and let it dry. Silicone does not stick to much, but "
        "it sticks to bare printed plastic more than you want.",
        "Mix the silicone by weight, exactly to the ratio on the bottles, in a "
        "clean cup. Stir slowly for two full minutes, scraping the sides and the "
        "bottom of the cup as you go -- unmixed silicone stays sticky forever, "
        "and fast stirring whips in bubbles.",
        f"{pour_where}: hold the cup high and let it fall in a thin string, so "
        "the silicone climbs the box and floods the figure from underneath, "
        f"pushing air out ahead of it. Keep going {pour_target}.{volume_line}",
        "Leave the box flat, level and untouched for the full cure time on the "
        "bottle -- usually 6 to 24 hours. Warm room, no poking, no moving it.",
        demold,
        "Get the master out: flex the rubber open and ease the figure out through "
        "the pour hole in the bottom of the block. Take your time and never cut "
        "the silicone if you can bend it instead. Keep the master -- you can pour "
        "a fresh mold from it whenever this one wears out.",
        "Cast a copy: stand the block pour-hole up, mix your resin, and pour it "
        "slowly into the hole until the cavity is full and the resin sits level "
        "in the funnel. Wait the full demold time, then flex the copy out.",
        "Repeat for as many copies as you want. A silicone mold like this is "
        "good for dozens of pulls; wash it with soapy water and dry it whenever "
        "the surface starts to feel tacky.",
    ]


__all__ = [
    "DEFAULT_FUNNELS",
    "DEFAULT_MARGIN_MM",
    "DEFAULT_PLATFORM_MM",
    "DEFAULT_POUR_CLEARANCE_MM",
    "DEFAULT_WALL_MM",
    "MODES",
    "build_master_box",
    "check_bed",
    "default_wall_mm",
    "master_box_instructions",
    "normalize_master_box_options",
    "normalize_mode",
    "printed_negative_instructions",
]
