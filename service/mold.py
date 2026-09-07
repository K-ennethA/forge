"""Mold mode: the same part script, but you get the negative instead of the part.

The plan's promise is one switch: *draft angles applied to vertical faces, a
two-piece mold box with a parting plane you can drag, registration keys, a pour
spout and vents.*  This module is that switch.  The two halves it returns are
**mold masters** -- printed, then used to cast silicone or resin.

The pipeline, in order
----------------------

1. **Parting plane.**  ``parting_z_mm`` is a height, or ``"auto"``, which takes
   the part's widest horizontal cross-section.  For the decorative parts this
   pipeline exists to make, the widest slice is the one place a two-piece mold
   opens without an undercut fighting it.
2. **Draft.**  The part is split at that plane and each side is tapered so its
   cavity opens toward the parting face.  See *Draft* below.
3. **Cavity.**  The two tapered halves, each clipped back to its own side of the
   parting plane, fused into one solid.  It always contains the part exactly.
4. **Box.**  The part's bounding box grown by ``shell_mm`` on all six sides,
   split at the parting plane, with the cavity subtracted from each half.
5. **Registration keys.**  Spherical bosses on ``mold_top``, matching sockets
   grown by the printer's ``press_fit`` in ``mold_bottom``, spaced around the
   parting face inside the shell band.
6. **Spout and vents.**  A cone from the top of the box down into the cavity's
   high point, and straight vent channels from the cavity's other local high
   points to the same top face.

Both halves are then re-tessellated and re-checked: **a mold half that is not
watertight is a 400**, exactly as with ``/segment``.  A mold you cannot slice is
not a mold.

Draft
-----
Two implementations, tried in that order, and the response says which one ran:

``occ_draft``
    ``BRepOffsetAPI_DraftAngle`` with the far end of the half as the neutral
    plane.  This is the real thing -- OCC tilts the faces and rebuilds the
    solid, so the result has no steps in it.  OCC only tapers **planar,
    cylindrical and conical** faces, so it covers boxes, cylinders, rings and
    prisms, and gives up on anything freeform.

``taper_union``
    The documented fallback.  The half is unioned with a series of copies of
    itself, each scaled up about a point on the neutral plane, which sweeps the
    silhouette outward toward the parting face.  It always contains the part and
    it always opens, but the taper is a staircase (sub-millimetre steps) and the
    widening is *radial from the part's axis*, so it is proportionally weaker
    near that axis than at the rim.

``none``
    ``draft_deg: 0``, or a half with nothing tapered.

Either way the drafted cavity is **up to ``(half height) x tan(draft)`` wider
than the part at the parting line** -- draft adds material toward the parting
face rather than removing it from the far end, because a cavity that does not
contain the part is not a cavity.  At the default 2 degrees over a 10 mm half
that is 0.35 mm.

The section arithmetic at the top of this module is pure Python on the welded
mesh -- no build123d, no OCP -- so it runs and is tested outside the worker, the
same way :mod:`checks` is.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import casting, undercut
from .errors import ParamError, ScriptError
from .printer import DEFAULT_PLATE_MARGIN_MM, bed_size, tolerance as printer_tolerance

Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]

#: Default wall thickness of the mold box, per side.
DEFAULT_SHELL_MM = 4.0

#: Default draft angle applied to the cavity walls, degrees from vertical.
DEFAULT_DRAFT_DEG = 2.0

#: Ceiling on the draft angle a request may ask for.  Past this the cavity is a
#: funnel and the casting is not the part any more.
MAX_DRAFT_DEG = 30.0

#: Default number of registration keys around the parting face.
DEFAULT_REGISTRATION_KEYS = 4

#: Most keys, vents and section samples a request can provoke.
MAX_REGISTRATION_KEYS = 12
MAX_VENTS = 8
PARTING_SAMPLES = 65

#: A face steeper than this from horizontal is "near-vertical" and gets draft.
#: 45 degrees is the natural split: below it the face is more floor than wall.
DRAFT_FACE_MIN_DEG = 45.0

#: Steps in the ``taper_union`` fallback, per millimetre of half height, then
#: clamped.  More steps mean a smoother taper and more booleans.
TAPER_STEPS_PER_MM = 1.0
TAPER_STEPS_MIN = 4
TAPER_STEPS_MAX = 16

#: How far past the neutral plane the taper's scale centre sits, as a fraction
#: of the half's height.  Keeping it off the plane keeps an apex or a pole from
#: being a fixed point of every scaled copy, which is what makes the fused
#: result invalid.  See :func:`_draft_via_taper`.
TAPER_CENTRE_OFFSET = 0.05

#: The cavity must keep at least this much material either side of the parting
#: plane for a two-piece mold to mean anything.
MIN_HALF_HEIGHT_MM = 0.5

#: Multiples of a channel's own radius it may be walked looking for a spot where
#: its whole mouth is inside the cavity; negative walks outward.  See
#: :func:`_nudge_ladder`.
CHANNEL_NUDGE_STEPS = (0.75, 1.5, 2.5, -0.75, -1.5)

#: Volumes below this are numerical noise, not material.
VOLUME_EPS = 1e-6


# --------------------------------------------------------------------------
# Mesh arithmetic: cross-sections and high points (no build123d)
# --------------------------------------------------------------------------


def _plane_segment(
    a: Sequence[float], b: Sequence[float], c: Sequence[float], z: float
) -> Optional[Tuple[Vec2, Vec2]]:
    """Where triangle ``abc`` crosses the horizontal plane at *z*, or ``None``.

    A triangle that merely touches the plane with one vertex, or lies in it, is
    not a crossing: it contributes no boundary to the section.
    """
    heights = (a[2] - z, b[2] - z, c[2] - z)
    if min(heights) >= 0.0 or max(heights) <= 0.0:
        return None

    points: List[Vec2] = []
    corners = (a, b, c)
    for index in range(3):
        p, q = corners[index], corners[(index + 1) % 3]
        dp, dq = heights[index], heights[(index + 1) % 3]
        if dp == 0.0:
            points.append((p[0], p[1]))
            continue
        if dp * dq < 0.0:
            t = dp / (dp - dq)
            points.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))

    if len(points) != 2:
        return None
    return points[0], points[1]


def cross_section_area(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    z: float,
) -> float:
    """Area of the part's horizontal cross-section at height *z*, in mm^2.

    Every triangle that straddles the plane contributes one segment of the
    section's boundary.  Orienting each segment by its facet normal (the
    material stays on the left) makes the shoelace sum come out as *outer loops
    minus holes*, so a ring reports its annulus rather than its disc.
    """
    total = 0.0
    for tri in triangles:
        if len(tri) != 3:
            continue
        a = vertices[tri[0]]
        b = vertices[tri[1]]
        c = vertices[tri[2]]
        segment = _plane_segment(a, b, c, z)
        if segment is None:
            continue
        (x1, y1), (x2, y2) = segment

        # The in-plane direction that keeps material on the left is n x z_hat,
        # which is (n_y, -n_x) once the vertical component is dropped.
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        if (x2 - x1) * ny - (y2 - y1) * nx < 0.0:
            x1, y1, x2, y2 = x2, y2, x1, y1

        total += 0.5 * (x1 * y2 - x2 * y1)

    return abs(total)


def section_profile(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    z_min: float,
    z_max: float,
    samples: int = PARTING_SAMPLES,
) -> List[Tuple[float, float]]:
    """``(z, area)`` at evenly spaced heights strictly inside the part."""
    span = z_max - z_min
    if span <= 0.0:
        return []
    count = max(3, int(samples))
    profile: List[Tuple[float, float]] = []
    for index in range(1, count + 1):
        z = z_min + span * index / (count + 1)
        profile.append((z, cross_section_area(vertices, triangles, z)))
    return profile


def auto_parting_z(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    z_min: float,
    z_max: float,
    samples: int = PARTING_SAMPLES,
) -> Tuple[float, List[Tuple[float, float]]]:
    """The widest horizontal slice: where a two-piece mold wants to open.

    A part with a real waist or bulge has one clear maximum and that is the
    answer.  A prismatic part -- a ring band, a box -- has a whole band of
    identical slices, and the tie is broken towards the *middle* of that band,
    which splits a straight wall into two halves worth printing instead of
    shaving a sliver off one end.
    """
    profile = section_profile(vertices, triangles, z_min, z_max, samples)
    if not profile:
        return (z_min + z_max) / 2.0, []
    best_area = max(area for _z, area in profile)
    if best_area <= 0.0:
        return (z_min + z_max) / 2.0, profile
    # Within a thousandth of the maximum is "as wide as it gets" -- the sections
    # are sampled off a tessellation, so exact equality is not on offer.
    threshold = best_area * (1.0 - 1e-3)
    tied = [z for z, area in profile if area >= threshold]
    return tied[len(tied) // 2], profile


def resolve_parting_z(
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    z_min: float,
    z_max: float,
    requested: Any = "auto",
) -> Tuple[float, str, List[Tuple[float, float]]]:
    """``(z, source, profile)`` for a request's ``parting_z_mm``.

    Split out of :func:`build_mold` because ``master_box`` mode has no parting
    plane of its own and still wants one to run the undercut analysis against:
    the whole point of the report is "here is what a two-piece mold would fight".
    """
    if requested is None or requested == "auto":
        parting_z, profile = auto_parting_z(vertices, triangles, z_min, z_max)
        return parting_z, "auto", profile
    return float(requested), "request", []


def local_high_points(
    vertices: Sequence[Sequence[float]],
    count: int,
    cell_mm: float,
    min_separation_mm: float,
    avoid: Sequence[Vec2] = (),
) -> List[Vec3]:
    """The tallest points of the mesh, spread out: where a vent has to go.

    Vertices are bucketed onto an XY grid and each cell keeps only its highest,
    so a dense tessellation of one dome does not win every slot.  Cells are then
    taken tallest-first, skipping any within *min_separation_mm* of a point
    already chosen or of anything in *avoid*.
    """
    if count <= 0 or not vertices:
        return []
    cell = max(float(cell_mm), 1e-3)
    buckets: Dict[Tuple[int, int], Vec3] = {}
    for vertex in vertices:
        x, y, z = float(vertex[0]), float(vertex[1]), float(vertex[2])
        key = (int(math.floor(x / cell)), int(math.floor(y / cell)))
        current = buckets.get(key)
        if current is None or z > current[2]:
            buckets[key] = (x, y, z)

    ordered = sorted(buckets.values(), key=lambda p: -p[2])
    chosen: List[Vec3] = []
    taken: List[Vec2] = [(float(p[0]), float(p[1])) for p in avoid]
    for point in ordered:
        if len(chosen) >= count:
            break
        if any(
            math.hypot(point[0] - tx, point[1] - ty) < min_separation_mm
            for tx, ty in taken
        ):
            continue
        chosen.append(point)
        taken.append((point[0], point[1]))
    return chosen


# --------------------------------------------------------------------------
# Request options
# --------------------------------------------------------------------------


def _number(value: Any, label: str, minimum: float, maximum: float, default: float) -> float:
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


def normalize_options(job: Mapping[str, Any], printer: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the ``/mold`` request body into resolved, typed options."""
    parting = job.get("parting_z_mm", "auto")
    if parting is None:
        parting = "auto"
    if isinstance(parting, str):
        if parting.strip().lower() != "auto":
            raise ParamError(
                f'parting_z_mm must be a number or "auto", got {parting!r}'
            )
        parting = "auto"
    elif isinstance(parting, bool) or not isinstance(parting, (int, float)):
        raise ParamError(f'parting_z_mm must be a number or "auto", got {parting!r}')
    else:
        parting = float(parting)
        if not math.isfinite(parting):
            raise ParamError("parting_z_mm must be finite")

    keys = job.get("registration_keys", DEFAULT_REGISTRATION_KEYS)
    if keys is None:
        keys = DEFAULT_REGISTRATION_KEYS
    if isinstance(keys, bool) or not isinstance(keys, int):
        raise ParamError(f"registration_keys must be an integer, got {keys!r}")
    if not 0 <= keys <= MAX_REGISTRATION_KEYS:
        raise ParamError(
            f"registration_keys must sit between 0 and {MAX_REGISTRATION_KEYS}, "
            f"got {keys}"
        )

    vents = job.get("vents", "auto")
    if vents is None:
        vents = "auto"
    if isinstance(vents, str):
        if vents.strip().lower() != "auto":
            raise ParamError(f'vents must be an integer or "auto", got {vents!r}')
        vents = "auto"
    elif isinstance(vents, bool) or not isinstance(vents, int):
        raise ParamError(f'vents must be an integer or "auto", got {vents!r}')
    elif not 0 <= vents <= MAX_VENTS:
        raise ParamError(f"vents must sit between 0 and {MAX_VENTS}, got {vents}")

    spout = job.get("spout")
    spout_options: Optional[Dict[str, Any]]
    if spout is False:
        spout_options = None
    elif spout is None:
        spout_options = {}
    elif isinstance(spout, Mapping):
        spout_options = dict(spout)
        position = spout_options.get("position")
        if position is not None:
            if (
                not isinstance(position, (list, tuple))
                or len(position) != 2
                or any(
                    isinstance(v, bool) or not isinstance(v, (int, float))
                    for v in position
                )
            ):
                raise ParamError(
                    "spout.position must be [x, y] in millimetres, in the part's "
                    "own coordinates"
                )
            spout_options["position"] = (float(position[0]), float(position[1]))
        spout_options["diameter_mm"] = (
            None
            if spout_options.get("diameter_mm") is None
            else _number(spout_options.get("diameter_mm"), "spout.diameter_mm", 0.1, 100.0, 0.0)
        )
    else:
        raise ParamError(
            f"spout must be an object or false, got {type(spout).__name__}"
        )

    examples = job.get("undercut_examples")
    if examples is None:
        examples = undercut.DEFAULT_EXAMPLES
    if isinstance(examples, bool) or not isinstance(examples, int):
        raise ParamError(f"undercut_examples must be an integer, got {examples!r}")
    if not 0 <= examples <= 50:
        raise ParamError(
            f"undercut_examples must sit between 0 and 50, got {examples}"
        )

    min_feature = float(printer["min_feature_size"])
    return {
        # Phase 12: which of the two molds, and how hard to look for undercuts.
        # Both are additive -- an unchanged request still gets the printed
        # negative it always got.
        "mode": casting.normalize_mode(job.get("mode")),
        "undercut_threshold_deg": _number(
            job.get("undercut_threshold_deg"),
            "undercut_threshold_deg",
            0.0,
            45.0,
            undercut.DEFAULT_THRESHOLD_DEG,
        ),
        "undercut_examples": examples,
        "master_box": casting.normalize_master_box_options(job, printer),
        "parting_z_mm": parting,
        "draft_deg": _number(job.get("draft_deg"), "draft_deg", 0.0, MAX_DRAFT_DEG, DEFAULT_DRAFT_DEG),
        "shell_mm": _number(job.get("shell_mm"), "shell_mm", max(min_feature, 0.2), 200.0, DEFAULT_SHELL_MM),
        "clearance_mm": _number(job.get("clearance_mm"), "clearance_mm", 0.0, 10.0, 0.0),
        "registration_keys": keys,
        "vents": vents,
        "spout": spout_options,
    }


# --------------------------------------------------------------------------
# Kernel helpers (build123d imported per call, worker-side only)
# --------------------------------------------------------------------------


def has_volume(shape: Any, tolerance: float = 1e-9) -> bool:
    """Does this boolean result contain material?

    Same guard as :mod:`segmenting`: build123d 0.11 *asserts* on ``.wrapped`` for
    an empty boolean result rather than returning ``None``, so both the access
    and the measurement have to be wrapped.
    """
    if shape is None:
        return False
    try:
        if shape.wrapped is None:
            return False
        return float(shape.volume) > tolerance
    except Exception:  # noqa: BLE001 - an empty result raises, and empty is the answer
        return False


def solid_volume(shape: Any) -> float:
    try:
        return float(shape.volume)
    except Exception:  # noqa: BLE001
        return 0.0


def contains(inner: Any, outer: Any) -> bool:
    """Is every cubic millimetre of *inner* inside *outer*?"""
    try:
        return not has_volume(inner - outer, tolerance=VOLUME_EPS)
    except Exception:  # noqa: BLE001 - a failed boolean is a "no"
        return False


#: The name this module used before :mod:`service.casting` needed it too.
_contains = contains


def _is_valid(shape: Any) -> bool:
    """OCC's own verdict on a solid, read across build123d versions.

    A drafted half that fails ``BRepCheck_Analyzer`` is worse than no draft at
    all: later booleans against it come back *empty* rather than failing, so the
    spout quietly misses the cavity and the mold is silently wrong.
    """
    try:
        verdict = getattr(shape, "is_valid", None)
        if callable(verdict):
            return bool(verdict())
        if verdict is None:
            return True  # nothing to ask; assume the kernel is happy
        return bool(verdict)
    except Exception:  # noqa: BLE001
        return False


def _slab(centre: Vec2, span: float, z_low: float, z_high: float) -> Any:
    """An axis-aligned box covering ``span`` in XY and ``z_low..z_high`` in Z."""
    from build123d import Box, Pos  # noqa: PLC0415

    height = z_high - z_low
    return Pos(centre[0], centre[1], (z_low + z_high) / 2.0) * Box(span, span, height)


def _draft_via_occ(
    solid: Any, angle_deg: float, neutral_z: float, toward_z: float
) -> Optional[Any]:
    """``BRepOffsetAPI_DraftAngle`` on every near-vertical face.

    OCC's convention: with ``Direction`` naming a side of the neutral plane, a
    *negative* angle adds material on that side.  We want material added toward
    the parting plane, so the direction is "from the neutral plane toward the
    parting plane" and the angle is negative.  The sign is verified rather than
    trusted -- the result has to still contain the original solid, or it is not
    a cavity -- and the other sign is tried before giving up.
    """
    from build123d import Compound  # noqa: PLC0415

    from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle  # noqa: PLC0415
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt  # noqa: PLC0415

    sign = 1.0 if toward_z > neutral_z else -1.0
    horizontal_limit = math.cos(math.radians(DRAFT_FACE_MIN_DEG))

    faces = []
    for face in solid.faces():
        try:
            normal = face.normal_at()
        except Exception:  # noqa: BLE001 - a face with no defined normal is not drafted
            continue
        if abs(float(normal.Z)) >= horizontal_limit:
            continue  # more floor than wall
        faces.append(face)
    if not faces:
        return None

    for angle in (-math.radians(angle_deg), math.radians(angle_deg)):
        try:
            builder = BRepOffsetAPI_DraftAngle(solid.wrapped)
            direction = gp_Dir(0.0, 0.0, sign)
            neutral = gp_Pln(gp_Pnt(0.0, 0.0, neutral_z), gp_Dir(0.0, 0.0, 1.0))
            added = 0
            for face in faces:
                builder.Add(face.wrapped, direction, angle, neutral, True)
                if not builder.AddDone():
                    # OCC only tapers planar / cylindrical / conical faces; the
                    # rest are put back and the solid keeps them as modelled.
                    builder.Remove(face.wrapped)
                    continue
                added += 1
            if added == 0:
                return None
            builder.Build()
            if not builder.IsDone():
                continue
            # DraftAngle hands back a TopoDS_COMPOUND wrapping the rebuilt
            # solid.  Shape.cast() answers None for a compound, so wrap it
            # explicitly and unwrap the single solid inside when there is one.
            drafted = Compound(builder.Shape())
            solids = drafted.solids()
            if len(solids) == 1:
                drafted = solids[0]
        except Exception:  # noqa: BLE001 - draft is best-effort by design
            continue
        if drafted is not None and _contains(solid, drafted):
            return drafted
    return None


def _draft_via_taper(
    solid: Any,
    angle_deg: float,
    neutral_z: float,
    toward_z: float,
    centre: Vec2,
    radius: float,
) -> Optional[Any]:
    """The fallback: union the half with scaled copies of itself.

    Each copy is the half scaled up about a point behind the neutral plane, so
    the silhouette sweeps outward as it approaches the parting face.  The union
    trivially contains the original (the first copy *is* the original), and it
    opens, which is the whole job -- but the taper is a staircase and its
    widening is radial about the part's axis rather than normal to each face.

    The scale centre sits :data:`TAPER_CENTRE_OFFSET` *past* the neutral plane
    rather than on it.  A point of the part that lands exactly on the centre --
    the apex of a cone, the pole of a dome -- is a fixed point of every copy,
    and the fused result then fails ``BRepCheck_Analyzer``: booleans against it
    come back empty instead of failing, so the spout silently misses the cavity.
    """
    height = abs(toward_z - neutral_z)
    if height <= 0.0 or radius <= 0.0:
        return None

    reach = height * math.tan(math.radians(angle_deg))
    if reach <= 1e-6:
        return None

    steps = int(min(TAPER_STEPS_MAX, max(TAPER_STEPS_MIN, height * TAPER_STEPS_PER_MM)))
    away = 1.0 if neutral_z > toward_z else -1.0
    about = (
        centre[0],
        centre[1],
        neutral_z + away * max(height * TAPER_CENTRE_OFFSET, 0.1),
    )

    copies = []
    for index in range(1, steps + 1):
        factor = 1.0 + (reach * index / steps) / radius
        try:
            copies.append(solid.scale(factor, about=about))
        except TypeError:
            # Defensive: a release without the `about` keyword.
            from build123d import Pos  # noqa: PLC0415

            shifted = Pos(-about[0], -about[1], -about[2]) * solid
            copies.append(Pos(*about) * shifted.scale(factor))
    if not copies:
        return None
    try:
        return solid.fuse(*copies)
    except Exception:  # noqa: BLE001
        fused = solid
        for copy in copies:
            fused = fused + copy
        return fused


def apply_draft(
    solid: Any,
    angle_deg: float,
    neutral_z: float,
    toward_z: float,
    centre: Vec2,
    radius: float,
) -> Tuple[Any, str]:
    """Taper one half of the part so its cavity opens toward the parting plane.

    Returns the tapered solid and the name of the method that produced it.  A
    result that is not a valid solid is discarded, whichever method made it: the
    only thing worse than an undrafted mold is a mold whose booleans silently
    stop working.
    """
    if angle_deg <= 0.0:
        return solid, "none"

    drafted = _draft_via_occ(solid, angle_deg, neutral_z, toward_z)
    if drafted is not None and _is_valid(drafted):
        return drafted, "occ_draft"

    drafted = _draft_via_taper(solid, angle_deg, neutral_z, toward_z, centre, radius)
    if drafted is not None and has_volume(drafted) and _is_valid(drafted):
        return drafted, "taper_union"

    return solid, "none"


# --------------------------------------------------------------------------
# Placement helpers
# --------------------------------------------------------------------------


def perimeter_positions(
    low: Vec2, high: Vec2, count: int, inset: float
) -> List[Vec2]:
    """*count* points spread evenly around a rectangle, inset from its edges.

    With the default four they land on the corners, which is where a two-piece
    mold wants its keys; other counts walk the same perimeter at equal spacing.
    """
    if count <= 0:
        return []
    x0, y0 = low[0] + inset, low[1] + inset
    x1, y1 = high[0] - inset, high[1] - inset
    if x1 <= x0 or y1 <= y0:
        return []

    width, depth = x1 - x0, y1 - y0
    perimeter = 2.0 * (width + depth)
    corners_first = 0.0  # start at (x0, y0) so count == 4 lands on the corners
    points: List[Vec2] = []
    for index in range(count):
        distance = (corners_first + perimeter * index / count) % perimeter
        if distance < width:
            points.append((x0 + distance, y0))
        elif distance < width + depth:
            points.append((x1, y0 + (distance - width)))
        elif distance < 2.0 * width + depth:
            points.append((x1 - (distance - width - depth), y1))
        else:
            points.append((x0, y1 - (distance - 2.0 * width - depth)))
    return points


def _nudge_ladder(point: Vec2, centre: Vec2, radius: float) -> List[Vec2]:
    """*point*, then the same point walked toward the middle of the part.

    A mesh vertex sits on an edge of the cavity by construction, so a channel
    centred on one straddles the rim.  Walking inward by multiples of the
    channel's own radius is enough to clear the rim on anything with a wall
    thicker than the channel, and the caller keeps the original as a fallback
    for the walls that are thinner than that.
    """
    ladder = [point]
    dx, dy = centre[0] - point[0], centre[1] - point[1]
    distance = math.hypot(dx, dy)
    if distance < 1e-9 or radius <= 0.0:
        return ladder
    ux, uy = dx / distance, dy / distance
    for step in CHANNEL_NUDGE_STEPS:
        # Negative steps walk outward, for a vertex that was on an inner rim --
        # the top of a ring offers both, and only one of them has material
        # toward the middle of the part.
        reach = step * radius
        if reach > distance:
            continue
        ladder.append((point[0] + ux * reach, point[1] + uy * reach))
    return ladder


def resolve_vent_count(request: Any, footprint: Vec2) -> int:
    """``"auto"`` means one vent, plus one more per 40 mm of cavity width."""
    if isinstance(request, int):
        return request
    widest = max(footprint[0], footprint[1])
    return int(min(4, 1 + int(widest // 40.0)))


# --------------------------------------------------------------------------
# The mold itself
# --------------------------------------------------------------------------


def _check_bed(
    printer: Mapping[str, Any],
    halves: Mapping[str, Tuple[float, float, float]],
    margin_mm: float,
) -> Dict[str, Any]:
    """Each half, box and all, has to fit the bed -- else there is no point."""
    bed_x, bed_y, bed_z = bed_size(printer)
    usable_x = bed_x - 2.0 * margin_mm
    usable_y = bed_y - 2.0 * margin_mm
    report: Dict[str, Any] = {"bed_mm": [bed_x, bed_y, bed_z], "margin_mm": margin_mm}
    for name, size in halves.items():
        width, depth, height = size
        flat = (width <= usable_x and depth <= usable_y) or (
            depth <= usable_x and width <= usable_y
        )
        if not flat or height > bed_z:
            raise ScriptError(
                f"{name} is {width:.1f}x{depth:.1f}x{height:.1f} mm with the "
                f"shell, over the usable bed ({usable_x:g}x{usable_y:g}x{bed_z:g} mm). "
                "Reduce shell_mm, shrink the part, or run /segment on the part "
                "first and mold the segments."
            )
        report[name] = [round(v, 3) for v in size]
    return report


def build_mold(
    shape: Any,
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    stats: Mapping[str, Any],
    printer: Mapping[str, Any],
    options: Mapping[str, Any],
    margin_mm: float = DEFAULT_PLATE_MARGIN_MM,
) -> Dict[str, Any]:
    """Turn a built part into two mold-master solids plus the report about them.

    Returns ``{"halves": [{"name", "solid"}], "parting_z_mm", "draft", "box",
    "spout", "vents", "registration_keys", "cavity", "bed"}``.  The caller
    tessellates and verifies the halves; this function is the geometry.
    """
    from build123d import Box, Cone, Cylinder, Pos, Sphere, offset  # noqa: PLC0415

    low = [float(v) for v in stats["bounding_box_min_mm"]]
    high = [float(v) for v in stats["bounding_box_max_mm"]]
    size = [high[i] - low[i] for i in range(3)]
    centre = ((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0)

    shell = float(options["shell_mm"])
    clearance = float(options["clearance_mm"])
    draft_deg = float(options["draft_deg"])
    min_feature = float(printer["min_feature_size"])

    # -- parting plane -----------------------------------------------------
    parting_z, parting_source, profile = resolve_parting_z(
        vertices, triangles, low[2], high[2], options["parting_z_mm"]
    )
    if not low[2] + MIN_HALF_HEIGHT_MM <= parting_z <= high[2] - MIN_HALF_HEIGHT_MM:
        raise ParamError(
            f"parting_z_mm={parting_z:.3f} leaves less than {MIN_HALF_HEIGHT_MM} mm "
            f"of part on one side; the part spans z={low[2]:.3f} to {high[2]:.3f} mm"
        )

    # -- the box, and whether it can be printed at all ---------------------
    box_low = (low[0] - shell, low[1] - shell, low[2] - shell)
    box_high = (high[0] + shell, high[1] + shell, high[2] + shell)
    box_size = (
        box_high[0] - box_low[0],
        box_high[1] - box_low[1],
        box_high[2] - box_low[2],
    )
    bed = _check_bed(
        printer,
        {
            "mold_top": (box_size[0], box_size[1], box_high[2] - parting_z),
            "mold_bottom": (box_size[0], box_size[1], parting_z - box_low[2]),
        },
        margin_mm,
    )

    span = max(box_size[0], box_size[1]) * 2.0 + 10.0
    upper = _slab(centre, span, parting_z, box_high[2] + 1.0)
    lower = _slab(centre, span, box_low[2] - 1.0, parting_z)
    box = Pos(
        (box_low[0] + box_high[0]) / 2.0,
        (box_low[1] + box_high[1]) / 2.0,
        (box_low[2] + box_high[2]) / 2.0,
    ) * Box(*box_size)

    # -- the cavity --------------------------------------------------------
    cavity_source = shape
    if clearance > 0.0:
        try:
            grown = offset(shape, amount=clearance)
        except Exception as exc:  # noqa: BLE001
            raise ScriptError(
                f"clearance_mm={clearance} could not be applied: OCC's 3D offset "
                f"failed on this part ({type(exc).__name__}). Set clearance_mm to 0 "
                "and size the part itself instead."
            ) from exc
        if not has_volume(grown):
            raise ScriptError(
                f"clearance_mm={clearance} produced an empty solid; set it to 0."
            )
        cavity_source = grown

    part_top = cavity_source & upper
    part_bottom = cavity_source & lower
    if not has_volume(part_top) or not has_volume(part_bottom):
        raise ParamError(
            f"the parting plane at z={parting_z:.3f} does not cut the part in two; "
            'choose another parting_z_mm, or "auto"'
        )

    radius = max(math.hypot(size[0], size[1]) / 2.0, 1e-3)
    drafted_top, method_top = apply_draft(
        part_top, draft_deg, high[2], parting_z, centre, radius
    )
    drafted_bottom, method_bottom = apply_draft(
        part_bottom, draft_deg, low[2], parting_z, centre, radius
    )

    # Clip each drafted half back to its own side: the taper sweeps material
    # across the parting plane, and material from the top half's taper must not
    # be carved out of the bottom half.
    cavity_top = drafted_top & upper
    cavity_bottom = drafted_bottom & lower
    cavity = cavity_top + cavity_bottom
    if not has_volume(cavity):
        raise ScriptError("the mold cavity came out empty; check parting_z_mm")
    if not _contains(shape, cavity):
        raise ScriptError(
            "the drafted cavity does not contain the part, which would cast a "
            "smaller part than the script describes. Lower draft_deg, or set it "
            "to 0."
        )

    # -- the two halves ----------------------------------------------------
    mold_top = (box & upper) - cavity
    mold_bottom = (box & lower) - cavity
    for name, solid in (("mold_top", mold_top), ("mold_bottom", mold_bottom)):
        if not has_volume(solid):
            raise ScriptError(
                f"{name} came out empty: the cavity fills the whole box. Increase "
                "shell_mm."
            )
    plain_volumes = {"mold_top": solid_volume(mold_top), "mold_bottom": solid_volume(mold_bottom)}

    # -- registration keys -------------------------------------------------
    key_count = int(options["registration_keys"])
    key_tolerance = printer_tolerance(printer, "press_fit", 0.1)
    key_radius = max(min(shell * 0.35, min(size[0], size[1]) * 0.15), min_feature)
    key_positions: List[Vec2] = []
    if key_count > 0:
        if key_radius * 2.0 > shell:
            raise ScriptError(
                f"a registration key needs about {key_radius * 2.0:.2f} mm and the "
                f"shell is {shell:g} mm; raise shell_mm or set registration_keys to 0"
            )
        key_positions = perimeter_positions(
            (box_low[0], box_low[1]), (box_high[0], box_high[1]), key_count, shell / 2.0
        )
        if len(key_positions) < key_count:
            raise ScriptError(
                "there is no room around the parting face for "
                f"{key_count} registration keys; raise shell_mm or lower the count"
            )
        for x, y in key_positions:
            male = Pos(x, y, parting_z) * Sphere(key_radius)
            female = Pos(x, y, parting_z) * Sphere(key_radius + key_tolerance)
            if has_volume(female & cavity):
                raise ScriptError(
                    f"the registration key at ({x:.1f}, {y:.1f}) mm runs into the "
                    "cavity; raise shell_mm or set registration_keys to 0"
                )
            mold_top = mold_top + male
            mold_bottom = mold_bottom - female

    # -- where the channels meet the cavity --------------------------------
    # Every candidate the mesh offers is a *vertex*, which is by definition on
    # an edge of the cavity: a channel centred there straddles the rim, and a
    # boolean that grazes a wall tangentially is how a mold half comes out
    # non-manifold.  So each candidate is walked across the wall until the
    # channel's whole buried length sits inside the cavity, and only if nothing
    # fits does it fall back to "at least it reaches".
    channel_low = high[2] - min(1.0, size[2] * 0.25)
    channel_high = box_high[2] + 0.5

    def place_channel(
        candidates: Sequence[Vec2], radius: float
    ) -> Optional[Tuple[Vec2, bool]]:
        # The probe spans everything between the channel's mouth and the top of
        # the part, at the widest the channel gets in there: a chamfered rim is
        # narrower at the top than the wall below it, and it is the top that
        # decides whether the cut grazes an edge.
        buried = max(high[2] - channel_low, 1e-3)
        fallback: Optional[Vec2] = None
        for point in candidates:
            for x, y in _nudge_ladder(point, centre, radius):
                mouth = Pos(x, y, (channel_low + high[2]) / 2.0) * Cylinder(
                    radius, buried
                )
                if not has_volume(mouth & cavity):
                    continue
                if fallback is None:
                    fallback = (x, y)
                if _contains(mouth, cavity):
                    return (x, y), True
        return (fallback, False) if fallback is not None else None

    # -- pour spout --------------------------------------------------------
    spout_report: Optional[Dict[str, Any]] = None
    spout_options = options["spout"]
    spout_xy: Optional[Vec2] = None
    if spout_options is not None:
        diameter = spout_options.get("diameter_mm")
        if not diameter:
            diameter = max(min_feature * 3.0, 4.0)
        if diameter < min_feature:
            raise ParamError(
                f"spout.diameter_mm={diameter:g} is below the printer's minimum "
                f"feature size ({min_feature:g} mm); it would not print"
            )

        # A cone that narrows going down: easy to pour into, easy to snip off.
        # Its widest point inside the part is where it crosses the top face,
        # and that is the radius the placement search has to clear.
        length = channel_high - channel_low
        buried_radius = diameter * 0.3 + (diameter * 0.2) * (
            (high[2] - channel_low) / length
        )

        position = spout_options.get("position")
        if position is not None:
            asked = (float(position[0]), float(position[1]))
            placed = place_channel([asked], buried_radius)
            if placed is None:
                raise ScriptError(
                    f"the pour spout at ({asked[0]:.1f}, {asked[1]:.1f}) mm does "
                    "not reach the cavity; give spout.position an [x, y] over the "
                    "part"
                )
            # An explicit position is honoured, not corrected.
            spout_xy = asked
            placed = (asked, placed[1])
        else:
            peaks = local_high_points(
                vertices, 3, max(size[0], size[1]) / 12.0 + 0.5, diameter
            )
            placed = place_channel([(p[0], p[1]) for p in peaks], buried_radius)
            if placed is None:  # pragma: no cover - the cavity always has a top
                raise ScriptError(
                    "no pour spout could be placed over the cavity; give "
                    "spout.position an [x, y] over the part, or spout: false"
                )
            spout_xy = placed[0]

        spout = Pos(spout_xy[0], spout_xy[1], (channel_low + channel_high) / 2.0) * Cone(
            bottom_radius=diameter * 0.3, top_radius=diameter / 2.0, height=length
        )
        mold_top = mold_top - spout
        spout_report = {
            "diameter_mm": round(float(diameter), 4),
            "position_mm": [round(spout_xy[0], 4), round(spout_xy[1], 4)],
            "top_z_mm": round(channel_high, 4),
            "bottom_z_mm": round(channel_low, 4),
            "length_mm": round(length, 4),
            "fully_inside_cavity": placed[1],
        }

    # -- vents -------------------------------------------------------------
    vent_diameter = max(min_feature, 1.0)
    requested_vents = resolve_vent_count(options["vents"], (size[0], size[1]))
    vent_points: List[Vec3] = []
    if requested_vents > 0:
        separation = max(vent_diameter * 3.0, max(size[0], size[1]) / 6.0)
        candidates = local_high_points(
            vertices,
            requested_vents,
            max(size[0], size[1]) / 12.0 + 0.5,
            separation,
            avoid=[spout_xy] if spout_xy is not None else [],
        )
        for point in candidates:
            placed_vent = place_channel([(point[0], point[1])], vent_diameter / 2.0)
            if placed_vent is None:
                continue
            (vx, vy), _inside = placed_vent
            vent_low = min(point[2], high[2]) - 0.3
            vent = Pos(vx, vy, (vent_low + channel_high) / 2.0) * Cylinder(
                vent_diameter / 2.0, channel_high - vent_low
            )
            mold_top = mold_top - vent
            vent_points.append((vx, vy, point[2]))

    # -- final sanity ------------------------------------------------------
    for name, solid in (("mold_top", mold_top), ("mold_bottom", mold_bottom)):
        if not has_volume(solid):
            raise ScriptError(
                f"{name} came out empty once the spout and vents were cut; reduce "
                "their diameters or raise shell_mm"
            )

    # Additive (Phase 12): the same two draw directions this mold just committed
    # to, measured against the part's own faces.  It costs one pass over the
    # triangles and it is the difference between "your mold is ready" and "your
    # mold is ready and it will never open".
    undercuts = undercut.analyze(
        vertices,
        triangles,
        parting_z,
        low,
        high,
        threshold_deg=float(
            options.get("undercut_threshold_deg", undercut.DEFAULT_THRESHOLD_DEG)
        ),
        examples=int(options.get("undercut_examples", undercut.DEFAULT_EXAMPLES)),
    )

    return {
        "halves": [
            {"name": "mold_top", "solid": mold_top},
            {"name": "mold_bottom", "solid": mold_bottom},
        ],
        "undercuts": undercuts,
        "parting_z_mm": round(parting_z, 6),
        "parting_source": parting_source,
        "parting_profile": [
            [round(z, 4), round(area, 4)] for z, area in profile
        ],
        "draft": {
            "angle_deg": draft_deg,
            "mold_top": method_top,
            "mold_bottom": method_bottom,
            "face_threshold_deg": DRAFT_FACE_MIN_DEG,
        },
        "box": {
            "shell_mm": shell,
            "size_mm": [round(v, 4) for v in box_size],
            "min_mm": [round(v, 4) for v in box_low],
            "max_mm": [round(v, 4) for v in box_high],
        },
        "cavity": {
            "volume_mm3": round(solid_volume(cavity), 4),
            "part_volume_mm3": round(solid_volume(shape), 4),
            "clearance_mm": clearance,
            "plain_half_volume_mm3": {
                name: round(value, 4) for name, value in plain_volumes.items()
            },
        },
        "spout": spout_report,
        "vents": {
            "requested": requested_vents,
            "count": len(vent_points),
            "diameter_mm": round(vent_diameter, 4),
            "positions_mm": [[round(v, 4) for v in point] for point in vent_points],
        },
        "registration_keys": {
            "count": len(key_positions),
            "radius_mm": round(key_radius, 4),
            "tolerance_mm": key_tolerance,
            "male_half": "mold_top",
            "female_half": "mold_bottom",
            "positions_mm": [[round(x, 4), round(y, 4)] for x, y in key_positions],
        },
        "bed": bed,
    }


__all__ = [
    "DEFAULT_DRAFT_DEG",
    "DEFAULT_REGISTRATION_KEYS",
    "DEFAULT_SHELL_MM",
    "MAX_DRAFT_DEG",
    "apply_draft",
    "auto_parting_z",
    "build_mold",
    "contains",
    "cross_section_area",
    "has_volume",
    "resolve_parting_z",
    "local_high_points",
    "normalize_options",
    "perimeter_positions",
    "resolve_vent_count",
    "solid_volume",
    "section_profile",
]
