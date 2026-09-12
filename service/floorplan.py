"""``floorplan`` -- the plan file is the model, and this module is its contract.

Phase 19 turns a drawing of rooms into a walkable greybox, and it does it with
no generative 3D anywhere: the drawing's *meaning* lives in one typed file,
``projects/<slug>/design/floorplan.json``, and everything downstream -- the
echo-back SVG the artist approves, the Blender objects, the top-down
verification mask -- is a pure function of that file.  This module owns the
file.

The schema (``docs/architecture.md``, Phase 19) in one breath::

    {"version": 1, "units": "mm", "scale": {"mm_per_px"?, "calibrated_by"?},
     "defaults": {"ceiling_mm": 2400, "wall_mm": 100,
                  "door_w_mm": 820, "door_h_mm": 2040},
     "rooms":  [{"id", "label"?, "polygon_mm": [[x, y], ...]}],
     "walls":  [{"id", "from_mm", "to_mm", "thickness_mm"?, "height_mm"?,
                 "openings": [{"id", "kind", "at_mm", "width_mm"?, "height_mm"?,
                               "sill_mm"?, "swing"?}]}],
     "labels": [{"id", "label", "footprint_mm": [x, y, w, d], "height_mm"?,
                 "rotation_deg"?, "source"}],
     "history": [{"rev", "date", "note"}]}

Five entry points, in the order a build uses them:

:func:`validate_plan`
    The gate.  Refuses anything that cannot be built, in a sentence that names
    the entry -- because "invalid polygon" sends you reading JSON and
    "room 'room-kitchen' crosses itself between corners 1 and 3" sends you to
    the corner.
:func:`fill_defaults`
    Resolution.  Every optional number becomes a concrete one, and every entry
    records which of its numbers came from the ``defaults`` block, so a report
    can say *why* a wall is 2400 tall.
:func:`diff_plans`
    The incremental-regen law.  Two plans in, four lists of ids out.  The addon
    rebuilds ``added`` + ``changed``, deletes ``removed``, and **touches
    nothing else** -- which is what makes a hand edit to an untouched wall
    survive the next rebuild by construction.
:func:`component_build_specs`
    Pure data the add-on materialises verbatim: wall boxes, door cutouts
    already resolved to absolute coordinates, floor slabs, fixture boxes.  No
    geometry library is imported here and none is needed -- a wall is a
    rectangle and a doorway is a smaller rectangle inside it.
:func:`plan_mask`
    The plan rasterised top-down, so the built level can be measured against
    the drawing that asked for it (:func:`mask_iou`).

Plus :func:`snap_segments`, which is the deterministic half of extraction: raw
line segments off a drawing, straightened and welded into walls, with a report
of every degree and millimetre it moved.  The VLM never invents a wall; it only
says which of *these* gaps is a doorway.

Four conventions worth knowing before you read a number
-------------------------------------------------------
These are the add-on's conventions, not this module's opinions.  The resolved
plan produced here is exactly what ``build_floorplan`` consumes, so where the
schema left something open, the answer is whatever ``addon/forge/tools/
floorplan.py`` decided -- and any disagreement is a level built somewhere other
than where it was drawn.

**An opening's ``at_mm`` is its CENTRE** measured along the wall from
``from_mm``.  ``start_mm`` gives the near edge instead; giving both is refused,
because they disagree by half the opening's width.  :func:`fill_defaults`
converts ``start_mm`` to ``at_mm`` once the width is known, so everything
downstream reads one spelling.

**A label's ``footprint_mm`` is ``[x, y, w, d]`` with ``x, y`` the box's
CENTRE**, unless that label (or ``defaults.label_anchor``) says
``"anchor": "corner"``.  ``rotation_deg`` turns it about that centre,
counter-clockwise.  Build specs carry ``center_mm`` and the four rotated
corners as well, so nothing downstream has to redo the trigonometry or the
anchor.

**Z = 0 is the finished floor.**  Walls run 0 to their height, floor slabs hang
below at ``-thickness`` to 0, fixtures stand on 0.  A door's ``sill_mm`` is 0,
a window's is the height of its bottom edge.

**:data:`DEFAULTS` is the add-on's ``PLAN_DEFAULTS``, key for key.**  Nine
numbers and one word; the schema spells the first four and the rest are what a
greybox still needs (a window has to be *some* size).

Nothing in here imports build123d or bpy, and nothing in here touches disk.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from . import appliance_dims
from .errors import ScriptError


class FloorPlanError(ScriptError):
    """A plan that cannot be built -- an HTTP 400, because an edit fixes it.

    Every message names the entry it is talking about (``wall 'wall-03'``), or,
    when the entry has no id to name it by, the slot it sits in
    (``walls[2]``).  Naming the entry is the whole point: the artist is looking
    at a drawing, not at JSON, and the id is the only word both of us share.
    """


#: The only schema version this module speaks.
PLAN_VERSION = 1
SUPPORTED_VERSIONS: Tuple[int, ...] = (1,)

#: Millimetres, everywhere, like the rest of Forge.
UNITS = "mm"

#: The ``defaults`` block, complete.  The first four are the contract's; the
#: rest are the numbers a build still needs and the contract left open, each
#: chosen to be the boring residential answer.
#: These are the add-on's ``PLAN_DEFAULTS``, name for name and number for
#: number.  They have to be: the resolved plan this module produces is what
#: ``build_floorplan`` consumes, so a default the service spells differently is
#: a default the add-on silently substitutes its own value for.  Any change
#: here is a change in two files or it is a bug.
DEFAULTS: Dict[str, Any] = {
    "ceiling_mm": 2400.0,       # 8 ft, the residential default everywhere
    "wall_mm": 100.0,           # a 2x4 stud wall with board both sides, near enough
    "door_w_mm": 820.0,         # a 32 in leaf: the narrowest an accessible route allows
    "door_h_mm": 2040.0,        # a standard 6 ft 8 in door
    "window_w_mm": 1200.0,      # an ordinary double-hung pair
    "window_h_mm": 1200.0,
    "sill_mm": 900.0,           # counter height, which is where windows usually start
    "label_h_mm": 850.0,        # a washer is 850 mm tall, and so is most casework
    "floor_mm": 50.0,           # the room slab, built BELOW z = 0
    "label_anchor": "center",   # what footprint_mm's [x, y] means
}

#: The one default that is a word rather than a number.
ANCHORS: Tuple[str, ...] = ("center", "corner")
HINGES: Tuple[str, ...] = ("left", "right")

OPENING_KINDS: Tuple[str, ...] = ("door", "window", "gap")
SWING_VALUES: Tuple[Optional[str], ...] = ("in", "out", None)
LABEL_SOURCES: Tuple[str, ...] = ("user", "library")
ENTRY_KINDS: Tuple[str, ...] = ("room", "wall", "opening", "label")

#: An id names a Blender object (``FP:wall-01``), so it lives under the same
#: rule Phase 16 put on filenames: letters, digits, dot, dash, underscore,
#: starting with a letter or digit.  Refused, never slugged -- see the module
#: note on why nothing here is invented.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_ID_LEN = 60

#: Shorter than this and it is a stray mark, not a wall.
MIN_WALL_LENGTH_MM = 1.0

#: A room polygon enclosing less than this is a line, not a room.
MIN_ROOM_AREA_MM2 = 100.0

#: How far a cutout is grown through the wall's thickness, each side, so a
#: boolean never has to resolve two faces occupying the same plane.  Same
#: reason and same number as ``joints.OVERSHOOT_MM``.
CUTOUT_OVERSHOOT_MM = 1.0

#: Two wall ends this close are the same corner, so both walls grow half a
#: thickness into it and the corner comes out square instead of notched.
JOIN_TOLERANCE_MM = 1.0

#: Values are compared at this many decimals when diffing, so a round-trip
#: through JSON never reports a wall as changed.
COMPARE_DECIMALS = 6

#: Keys that say where a value came from rather than what it is.  A resolved
#: plan keeps these in its top-level ``provenance`` map rather than on the
#: entries, so the add-on's per-entry fingerprint never sees them; they are
#: named here as well so the diff and the build specs stay right if an older
#: plan arrives with them inline.  Dropping an explicit ``thickness_mm: 100``
#: when the default is already 100 changes the provenance and not the wall, and
#: rebuilding a wall that did not move is what the incremental law exists to
#: prevent.
PROVENANCE_KEYS: Tuple[str, ...] = ("from_defaults", "height_from", "appliance_match")

#: A grid bigger than this is a mistake in ``cell_mm``, not a request.
MAX_MASK_CELLS = 8_000_000

#: How far off square a wall may run before :func:`plan_warnings` says so.  A
#: drawn plan is rectilinear -- rooms are blocks and walls are the lines between
#: them -- so a wall at 17 degrees is almost always a coordinate that was typed
#: rather than measured.  It is a WARNING and never a refusal: a genuinely
#: angled wall is a real thing to draw, and refusing it would make this module
#: an opinion about architecture instead of a check on arithmetic.
RECTILINEAR_TOLERANCE_DEG = 1.0

_POINT_EPS_MM = 1e-6


# ==========================================================================
# Small typed readers -- every one of them names the thing it is reading
# ==========================================================================


def _mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FloorPlanError(
            f"{what} must be an object with named keys, got {type(value).__name__}."
        )
    return value


def _sequence(value: Any, what: str, *, allow_missing: bool = True) -> List[Any]:
    if value is None:
        if allow_missing:
            return []
        raise FloorPlanError(f"{what} is missing.")
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise FloorPlanError(
            f"{what} must be a list, got {type(value).__name__}."
        )
    return list(value)


def _number(value: Any, what: str, *, positive: bool = False,
            allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FloorPlanError(f"{what} must be a number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise FloorPlanError(
            f"{what} must be a finite number, got {value!r}. A plan with an "
            f"infinity in it has no size."
        )
    if positive and (number < 0.0 or (number == 0.0 and not allow_zero)):
        raise FloorPlanError(
            f"{what} must be {'zero or more' if allow_zero else 'greater than zero'}, "
            f"got {number:g}."
        )
    return number


def _text(value: Any, what: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise FloorPlanError(f"{what} must be text, got {type(value).__name__}.")
    text = value.strip()
    if not text and not allow_empty:
        raise FloorPlanError(f"{what} must not be empty.")
    return text


def _point(value: Any, what: str) -> List[float]:
    items = _sequence(value, what, allow_missing=False)
    if len(items) != 2:
        raise FloorPlanError(
            f"{what} must be a point [x, y] in millimetres, got {len(items)} numbers."
        )
    return [_number(items[0], f"{what} x"), _number(items[1], f"{what} y")]


def _check_id(raw: Any, where: str, kind: str, seen: Dict[str, str]) -> str:
    """An id, checked for shape and for having been used once.

    The id is the incremental-regen law's only handle: it survives edits, it
    names the Blender object, and it is what makes an edit rebuild one wall
    instead of a level.  So a missing one is refused rather than generated --
    an invented id changes on the next run and the artist's hand edits die with
    it.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise FloorPlanError(
            f"{where} has no id. Every room, wall, opening and label needs an id "
            f"that survives edits: it names the Blender object (FP:<id>) and it is "
            f"what lets a change rebuild one component instead of the whole level. "
            f"Give this {kind} one -- Forge will not invent it, because an invented "
            f"id would change next run and take your hand edits with it."
        )
    if not isinstance(raw, str):
        raise FloorPlanError(
            f"{where} has an id that is not text: {raw!r}. Ids are names, not numbers."
        )
    ident = raw.strip()
    if len(ident) > MAX_ID_LEN:
        raise FloorPlanError(
            f"{kind} id {ident!r} is {len(ident)} characters; keep ids to "
            f"{MAX_ID_LEN} or fewer so the Blender object name stays readable."
        )
    if not ID_RE.match(ident):
        raise FloorPlanError(
            f"{kind} id {ident!r} is not a usable name. Use letters, digits, dot, "
            f"dash and underscore, starting with a letter or digit (like "
            f"'wall-01' or 'room.kitchen'); the id becomes a Blender object name."
        )
    if ident in seen:
        raise FloorPlanError(
            f"id {ident!r} is used twice -- once by a {seen[ident]} and again by "
            f"this {kind}. Ids name Blender objects (FP:{ident}), so two entries "
            f"cannot share one; rename the second."
        )
    seen[ident] = kind
    return ident


# ==========================================================================
# Polygon arithmetic -- stdlib only, because a room outline is four points
# ==========================================================================


def _signed_area(points: Sequence[Sequence[float]]) -> float:
    """Twice the signed area, halved: positive is counter-clockwise."""
    total = 0.0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def _centroid(points: Sequence[Sequence[float]]) -> List[float]:
    area = _signed_area(points)
    if abs(area) < 1e-9:
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        return [sum(xs) / len(xs), sum(ys) / len(ys)]
    cx = cy = 0.0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return [cx / (6.0 * area), cy / (6.0 * area)]


def _cross(o: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _orientation(o: Sequence[float], a: Sequence[float], b: Sequence[float]) -> int:
    value = _cross(o, a, b)
    scale = (abs(a[0] - o[0]) + abs(a[1] - o[1])) * (abs(b[0] - o[0]) + abs(b[1] - o[1]))
    eps = 1e-9 * max(scale, 1.0)
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


def _on_segment(a: Sequence[float], b: Sequence[float], p: Sequence[float]) -> bool:
    """Is *p* (known collinear with a-b) inside the a-b box?"""
    return (
        min(a[0], b[0]) - _POINT_EPS_MM <= p[0] <= max(a[0], b[0]) + _POINT_EPS_MM
        and min(a[1], b[1]) - _POINT_EPS_MM <= p[1] <= max(a[1], b[1]) + _POINT_EPS_MM
    )


def _segments_touch(p1, p2, p3, p4) -> bool:
    """True if segment p1-p2 meets segment p3-p4 anywhere, touching included."""
    d1 = _orientation(p3, p4, p1)
    d2 = _orientation(p3, p4, p2)
    d3 = _orientation(p1, p2, p3)
    d4 = _orientation(p1, p2, p4)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True
    if d1 == 0 and _on_segment(p3, p4, p1):
        return True
    if d2 == 0 and _on_segment(p3, p4, p2):
        return True
    if d3 == 0 and _on_segment(p1, p2, p3):
        return True
    if d4 == 0 and _on_segment(p1, p2, p4):
        return True
    return False


def _self_intersection(points: Sequence[Sequence[float]]) -> Optional[Tuple[int, int]]:
    """The first pair of non-adjacent edges that meet, or ``None``."""
    count = len(points)
    for i in range(count):
        a1, a2 = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or (i + 1) % count == j:
                continue
            b1, b2 = points[j], points[(j + 1) % count]
            if _segments_touch(a1, a2, b1, b2):
                return (i, j)
    return None


def _same_point(a: Sequence[float], b: Sequence[float]) -> bool:
    return abs(a[0] - b[0]) <= _POINT_EPS_MM and abs(a[1] - b[1]) <= _POINT_EPS_MM


# ==========================================================================
# validate_plan
# ==========================================================================


def _validate_defaults(raw: Any) -> Dict[str, float]:
    """The defaults block, completed.

    Unknown keys are refused here and nowhere else in the schema, because a
    mistyped ``ceiling_mm`` is the one typo that silently builds an entire
    level at the wrong height.  Entries keep their unknown keys (that is how a
    door carries a Phase 17 ``mechanism`` record); a nine-key closed set does
    not need the same room.
    """
    given = _mapping(raw if raw is not None else {}, "defaults")
    unknown = sorted(key for key in given if key not in DEFAULTS)
    if unknown:
        raise FloorPlanError(
            f"defaults has {'a key' if len(unknown) == 1 else 'keys'} Forge does not "
            f"know: {', '.join(repr(k) for k in unknown)}. The defaults block takes "
            f"{', '.join(sorted(DEFAULTS))}. A default that is not read is a level "
            f"built at the wrong size, so this is a refusal rather than a shrug."
        )
    resolved = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in given or given[key] is None:
            continue
        if key == "label_anchor":
            anchor = _text(given[key], "defaults.label_anchor").lower()
            if anchor not in ANCHORS:
                raise FloorPlanError(
                    f"defaults.label_anchor is {given[key]!r}; it says what a label's "
                    f"footprint_mm [x, y] means, so it is "
                    f"{' or '.join(repr(a) for a in ANCHORS)}."
                )
            resolved[key] = anchor
            continue
        resolved[key] = _number(
            given[key], f"defaults.{key}", positive=True,
            allow_zero=(key == "sill_mm"),
        )
    return resolved


def _validate_scale(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    given = _mapping(raw, "scale")
    out: Dict[str, Any] = {}
    if "mm_per_px" in given and given["mm_per_px"] is not None:
        out["mm_per_px"] = _number(
            given["mm_per_px"], "scale.mm_per_px", positive=True, allow_zero=False
        )
    if "calibrated_by" in given and given["calibrated_by"] is not None:
        out["calibrated_by"] = _text(given["calibrated_by"], "scale.calibrated_by")
    for key, value in given.items():
        if key not in ("mm_per_px", "calibrated_by"):
            out[key] = copy.deepcopy(value)
    return out


def _extras(given: Mapping[str, Any], known: Iterable[str]) -> Dict[str, Any]:
    """Keys the schema does not name, kept verbatim.

    This is deliberate: a door's Phase 17 ``mechanism`` record and a promoted
    fixture's mesh reference ride along here, and they take part in the diff,
    so editing one rebuilds its component like any other change.
    """
    known_set = set(known)
    return {k: copy.deepcopy(v) for k, v in given.items() if k not in known_set}


_ROOM_KEYS = ("id", "label", "polygon_mm", "floor", "floor_mm")
_WALL_KEYS = ("id", "from_mm", "to_mm", "thickness_mm", "height_mm", "openings")
_OPENING_KEYS = ("id", "kind", "at_mm", "start_mm", "width_mm", "height_mm",
                 "sill_mm", "swing", "hinge")
_LABEL_KEYS = ("id", "label", "footprint_mm", "height_mm", "rotation_deg", "source",
               "anchor")


def _validate_room(raw: Any, index: int, seen: Dict[str, str]) -> Dict[str, Any]:
    where = f"rooms[{index}]"
    given = _mapping(raw, where)
    ident = _check_id(given.get("id"), where, "room", seen)

    points_raw = _sequence(given.get("polygon_mm"), f"room {ident!r} polygon_mm",
                           allow_missing=False)
    points = [_point(p, f"room {ident!r} polygon_mm[{i}]") for i, p in enumerate(points_raw)]
    if len(points) >= 2 and _same_point(points[0], points[-1]):
        points = points[:-1]
    if len(points) < 3:
        raise FloorPlanError(
            f"room {ident!r} has {len(points)} corner(s); a room needs at least 3. "
            f"(Repeating the first corner at the end to close the loop is fine -- "
            f"Forge drops the repeat -- but it does not count as a corner.)"
        )
    for i in range(len(points)):
        if _same_point(points[i], points[(i + 1) % len(points)]):
            raise FloorPlanError(
                f"room {ident!r} repeats corner {i} at "
                f"({points[i][0]:g}, {points[i][1]:g}); two corners in the same spot "
                f"make a zero-length wall. Delete one."
            )
    # Crossing is checked BEFORE area, because a bow-tie has zero signed area
    # and "this room encloses nothing" is the true but useless diagnosis of it.
    crossing = _self_intersection(points)
    if crossing is not None:
        i, j = crossing
        raise FloorPlanError(
            f"room {ident!r} crosses itself: the wall from corner {i} to {i + 1} meets "
            f"the wall from corner {j} to {j + 1}. Untangle the outline -- a room is "
            f"one simple loop, and a bow-tie has no inside to put a floor in."
        )
    area = _signed_area(points)
    if abs(area) < MIN_ROOM_AREA_MM2:
        raise FloorPlanError(
            f"room {ident!r} encloses {abs(area):.3g} mm2, which is a line and not a "
            f"room. Check the corners: they may all be on one axis."
        )
    if area < 0.0:
        points.reverse()

    room: Dict[str, Any] = {"id": ident, "polygon_mm": points}
    if given.get("label") is not None:
        room["label"] = _text(given["label"], f"room {ident!r} label")
    if given.get("floor") is not None:
        value = given["floor"]
        if not isinstance(value, bool):
            raise FloorPlanError(
                f"room {ident!r} floor must be true or false, got {value!r}."
            )
        room["floor"] = value
    if given.get("floor_mm") is not None:
        room["floor_mm"] = _number(
            given["floor_mm"], f"room {ident!r} floor_mm", positive=True, allow_zero=False
        )
    room.update(_extras(given, _ROOM_KEYS))
    return room


def _validate_opening(raw: Any, index: int, wall_id: str, wall_length: float,
                      seen: Dict[str, str]) -> Dict[str, Any]:
    where = f"wall {wall_id!r} openings[{index}]"
    given = _mapping(raw, where)
    ident = _check_id(given.get("id"), where, "opening", seen)

    kind_raw = given.get("kind")
    if kind_raw is None:
        raise FloorPlanError(
            f"opening {ident!r} on wall {wall_id!r} has no kind. Say which of "
            f"{', '.join(OPENING_KINDS)} it is -- they get different default sizes "
            f"and a door is the only one that can carry a swing."
        )
    kind = _text(kind_raw, f"opening {ident!r} kind")
    if kind not in OPENING_KINDS:
        raise FloorPlanError(
            f"opening {ident!r} on wall {wall_id!r} is a {kind!r}; Forge knows "
            f"{', '.join(OPENING_KINDS)}."
        )

    has_at = given.get("at_mm") is not None
    has_start = given.get("start_mm") is not None
    if has_at and has_start:
        raise FloorPlanError(
            f"opening {ident!r} on wall {wall_id!r} gives both 'at_mm' (its centre) "
            f"and 'start_mm' (its near edge). Give one; they disagree by half the "
            f"opening's width, and guessing which you meant is how a door ends up "
            f"410 mm from where you drew it."
        )
    if not has_at and not has_start:
        raise FloorPlanError(
            f"opening {ident!r} on wall {wall_id!r} has no at_mm. That is how far "
            f"along the wall its CENTRE sits, measured from the wall's from_mm end "
            f"(or give start_mm for its near edge instead); the wall is "
            f"{wall_length:.0f} mm long."
        )

    opening: Dict[str, Any] = {"id": ident, "kind": kind}
    at: Optional[float] = None
    if has_at:
        at = _number(given["at_mm"], f"opening {ident!r} at_mm")
        if at < -_POINT_EPS_MM or at > wall_length + _POINT_EPS_MM:
            raise FloorPlanError(
                f"opening {ident!r} sits at {at:g} mm along wall {wall_id!r}, which is "
                f"{wall_length:.0f} mm long. at_mm is measured from the wall's from_mm "
                f"end; move the opening, or lengthen the wall."
            )
        opening["at_mm"] = at
    else:
        start = _number(given["start_mm"], f"opening {ident!r} start_mm")
        if start < -_POINT_EPS_MM or start > wall_length + _POINT_EPS_MM:
            raise FloorPlanError(
                f"opening {ident!r} starts at {start:g} mm along wall {wall_id!r}, "
                f"which is {wall_length:.0f} mm long. start_mm is the opening's near "
                f"edge, measured from the wall's from_mm end."
            )
        opening["start_mm"] = start
    for key in ("width_mm", "height_mm"):
        if given.get(key) is not None:
            opening[key] = _number(
                given[key], f"opening {ident!r} {key}", positive=True, allow_zero=False
            )
    if given.get("sill_mm") is not None:
        opening["sill_mm"] = _number(
            given["sill_mm"], f"opening {ident!r} sill_mm", positive=True, allow_zero=True
        )
    if "swing" in given and given["swing"] is not None:
        swing = _text(given["swing"], f"opening {ident!r} swing")
        if swing not in [value for value in SWING_VALUES if value is not None]:
            raise FloorPlanError(
                f"opening {ident!r} has swing {swing!r}; a swing is 'in', 'out', or "
                f"left out entirely."
            )
        if kind != "door":
            raise FloorPlanError(
                f"opening {ident!r} is a {kind} with a swing. Only a door swings -- "
                f"drop the swing, or make it a door."
            )
        opening["swing"] = swing
    if "hinge" in given and given["hinge"] is not None:
        hinge = _text(given["hinge"], f"opening {ident!r} hinge").lower()
        if hinge not in HINGES:
            raise FloorPlanError(
                f"opening {ident!r} has hinge {hinge!r}; a door hinges 'left' (the "
                f"jamb nearer the wall's from_mm end) or 'right'."
            )
        if kind != "door":
            raise FloorPlanError(
                f"opening {ident!r} is a {kind} with a hinge. Only a door hinges -- "
                f"drop the hinge, or make it a door."
            )
        opening["hinge"] = hinge

    if "width_mm" in opening and at is not None:
        _check_opening_span(ident, wall_id, wall_length, at, opening["width_mm"])
    opening.update(_extras(given, _OPENING_KEYS))
    return opening


def _check_opening_span(ident: str, wall_id: str, wall_length: float,
                        at: float, width: float) -> None:
    half = width / 2.0
    if at - half < -_POINT_EPS_MM or at + half > wall_length + _POINT_EPS_MM:
        raise FloorPlanError(
            f"opening {ident!r} is {width:g} mm wide centred at {at:g} mm, so it runs "
            f"from {at - half:g} to {at + half:g} mm along wall {wall_id!r} -- a wall "
            f"that is {wall_length:.0f} mm long. An opening has to fit inside its own "
            f"wall; move it, narrow it, or lengthen the wall."
        )


def _check_opening_height(ident: str, wall_id: str, wall_height: float,
                          sill: float, height: float) -> None:
    if sill + height > wall_height + _POINT_EPS_MM:
        raise FloorPlanError(
            f"opening {ident!r} reaches {sill + height:g} mm up a wall ({wall_id!r}) "
            f"that is only {wall_height:g} mm tall. Lower the sill, shorten the "
            f"opening, or raise the ceiling."
        )


def _validate_wall(raw: Any, index: int, seen: Dict[str, str]) -> Dict[str, Any]:
    where = f"walls[{index}]"
    given = _mapping(raw, where)
    ident = _check_id(given.get("id"), where, "wall", seen)

    start = _point(given.get("from_mm"), f"wall {ident!r} from_mm")
    end = _point(given.get("to_mm"), f"wall {ident!r} to_mm")
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if not math.isfinite(length) or length < MIN_WALL_LENGTH_MM:
        raise FloorPlanError(
            f"wall {ident!r} is {length:.3g} mm long, from ({start[0]:g}, {start[1]:g}) "
            f"to ({end[0]:g}, {end[1]:g}). A wall needs two different ends at least "
            f"{MIN_WALL_LENGTH_MM:g} mm apart."
        )

    wall: Dict[str, Any] = {"id": ident, "from_mm": start, "to_mm": end}
    for key in ("thickness_mm", "height_mm"):
        if given.get(key) is not None:
            wall[key] = _number(
                given[key], f"wall {ident!r} {key}", positive=True, allow_zero=False
            )

    openings_raw = _sequence(given.get("openings"), f"wall {ident!r} openings")
    openings = [
        _validate_opening(item, i, ident, length, seen)
        for i, item in enumerate(openings_raw)
    ]
    #: Sorted along the wall so the build order of a wall's cutouts is the order
    #: you walk past them, and two plans that list the same doors in a different
    #: order are the same plan.
    openings.sort(key=lambda o: (o.get("at_mm", o.get("start_mm", 0.0)), o["id"]))
    wall["openings"] = openings
    wall.update(_extras(given, _WALL_KEYS))
    return wall


def _validate_label(raw: Any, index: int, seen: Dict[str, str]) -> Dict[str, Any]:
    where = f"labels[{index}]"
    given = _mapping(raw, where)
    ident = _check_id(given.get("id"), where, "label", seen)

    if given.get("label") is None:
        raise FloorPlanError(
            f"label {ident!r} has no label text. The text is the whole point of a key "
            f"entry -- it is what says 'washer/dryer' rather than 'box'."
        )
    text = _text(given["label"], f"label {ident!r} label")

    footprint_raw = _sequence(given.get("footprint_mm"), f"label {ident!r} footprint_mm",
                              allow_missing=False)
    if len(footprint_raw) != 4:
        raise FloorPlanError(
            f"label {ident!r} footprint_mm must be [x, y, w, d] -- where the box sits "
            f"then how big it is, all in millimetres -- got "
            f"{len(footprint_raw)} numbers."
        )
    x = _number(footprint_raw[0], f"label {ident!r} footprint x")
    y = _number(footprint_raw[1], f"label {ident!r} footprint y")
    width = _number(footprint_raw[2], f"label {ident!r} footprint width",
                    positive=True, allow_zero=False)
    depth = _number(footprint_raw[3], f"label {ident!r} footprint depth",
                    positive=True, allow_zero=False)

    entry: Dict[str, Any] = {"id": ident, "label": text,
                             "footprint_mm": [x, y, width, depth]}
    if given.get("height_mm") is not None:
        entry["height_mm"] = _number(
            given["height_mm"], f"label {ident!r} height_mm", positive=True, allow_zero=False
        )
    if given.get("rotation_deg") is not None:
        rotation = _number(given["rotation_deg"], f"label {ident!r} rotation_deg")
        entry["rotation_deg"] = rotation % 360.0
    if given.get("source") is not None:
        source = _text(given["source"], f"label {ident!r} source")
        if source not in LABEL_SOURCES:
            raise FloorPlanError(
                f"label {ident!r} has source {source!r}; it is "
                f"{' or '.join(repr(s) for s in LABEL_SOURCES)} -- who put the box "
                f"there, the artist or the library."
            )
        entry["source"] = source
    if given.get("anchor") is not None:
        anchor = _text(given["anchor"], f"label {ident!r} anchor").lower()
        if anchor not in ANCHORS:
            raise FloorPlanError(
                f"label {ident!r} has anchor {anchor!r}; footprint_mm's [x, y] is "
                f"either the box's {' or its '.join(repr(a) for a in ANCHORS)}."
            )
        entry["anchor"] = anchor
    entry.update(_extras(given, _LABEL_KEYS))
    return entry


def _validate_history(raw: Any) -> List[Dict[str, Any]]:
    items = _sequence(raw, "history")
    out: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        given = _mapping(item, f"history[{index}]")
        record: Dict[str, Any] = {}
        if given.get("rev") is not None:
            record["rev"] = _number(given["rev"], f"history[{index}].rev")
        for key in ("date", "note"):
            if given.get(key) is not None:
                record[key] = _text(given[key], f"history[{index}].{key}", allow_empty=True)
        record.update(_extras(given, ("rev", "date", "note")))
        out.append(record)
    return out


#: ``provenance`` is in this list so a resolved plan fed back in loses its old
#: map rather than carrying a stale one: :func:`fill_defaults` re-derives it
#: every time, and provenance that disagrees with the numbers beside it is
#: worse than none.
_PLAN_KEYS = ("version", "units", "scale", "defaults", "rooms", "walls", "labels",
              "history", "provenance")


def plan_warnings(plan: Any, *,
                  tolerance_deg: float = RECTILINEAR_TOLERANCE_DEG) -> List[str]:
    """Sentences about a plan that is legal but probably not what was drawn.

    Today there is exactly one, and it exists because of a real failure: a
    drawing was read by eye instead of measured, and the level came out with a
    **diagonal wall that exists nowhere in the drawing**.  Nothing in the schema
    forbids that wall -- two points make a wall at any angle -- so the check
    cannot be a refusal, and a warning naming the wall is what turns it into a
    question the artist can answer in one word.

    Reads defensively rather than validating: it is called from
    :func:`validate_plan` on its own output, and an entry it cannot understand
    is one :func:`validate_plan` has already refused or will.
    """
    out: List[str] = []
    if not isinstance(plan, Mapping):
        return out
    limit = float(tolerance_deg)
    for index, wall in enumerate(plan.get("walls") or []):
        if not isinstance(wall, Mapping):
            continue
        ident = wall.get("id") or f"walls[{index}]"
        start, end = wall.get("from_mm"), wall.get("to_mm")
        if not isinstance(start, Sequence) or not isinstance(end, Sequence):
            continue
        if len(start) < 2 or len(end) < 2:
            continue
        try:
            x0, y0 = float(start[0]), float(start[1])
            x1, y1 = float(end[0]), float(end[1])
        except (TypeError, ValueError):
            continue
        dx, dy = x1 - x0, y1 - y0
        if not (math.isfinite(dx) and math.isfinite(dy)):
            continue
        if math.hypot(dx, dy) < MIN_WALL_LENGTH_MM:
            continue
        angle = math.degrees(math.atan2(abs(dy), abs(dx)))
        off_axis = min(angle, 90.0 - angle)
        if off_axis > limit:
            out.append(
                f"wall {ident!r} runs {off_axis:.0f} degrees off axis, from "
                f"({x0:g}, {y0:g}) to ({x1:g}, {y1:g}) -- drawn plans are "
                f"rectilinear, so an angled wall is usually a coordinate that was "
                f"typed rather than measured; is that intended?"
            )
    return out


def validate_plan(plan: Any, *, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Check a plan against the schema and return a normalised copy.

    What "normalised" means, exactly:

    * every number is a ``float`` and every id a stripped ``str``;
    * a room polygon is an **open** ring (a repeated closing corner is dropped)
      wound **counter-clockwise**, so two plans that drew the same room in
      opposite directions compare equal;
    * a wall's openings are sorted along the wall;
    * ``defaults`` is complete, merged over :data:`DEFAULTS`;
    * missing optional per-entry numbers stay missing -- resolving them is
      :func:`fill_defaults`'s job, and keeping them apart is what lets a report
      say which numbers the artist chose.

    Unknown keys on an entry are kept verbatim (that is the Phase 17
    ``mechanism`` record's ride); unknown keys inside ``defaults`` are refused.

    Pass a list as *warnings* to collect the sentences from
    :func:`plan_warnings` -- today, any wall that does not run square.  They are
    collected into a list the CALLER owns rather than added to the returned
    document on purpose: the normalised plan is what gets written to
    ``floorplan.json`` and fingerprinted by the add-on, and a note about the
    geometry living inside the file would be a note that has to be diffed.

    The input is never mutated.
    """
    given = _mapping(plan, "plan")

    if "version" not in given or given["version"] is None:
        raise FloorPlanError(
            f"the plan has no version. Write \"version\": {PLAN_VERSION} at the top -- "
            f"a plan file outlives the code that reads it, and a version-less file is "
            f"one whose meaning we would be guessing at."
        )
    version_raw = given["version"]
    if isinstance(version_raw, bool) or not isinstance(version_raw, (int, float)):
        raise FloorPlanError(f"the plan version must be a number, got {version_raw!r}.")
    version = int(version_raw)
    if version != float(version_raw) or version not in SUPPORTED_VERSIONS:
        raise FloorPlanError(
            f"the plan says version {version_raw!r}; this Forge reads "
            f"{', '.join(str(v) for v in SUPPORTED_VERSIONS)}."
        )

    if "units" not in given or given["units"] is None:
        raise FloorPlanError(
            f"the plan has no units. Write \"units\": \"{UNITS}\" -- Forge is "
            f"millimetres end to end, and saying so is how a plan proves it knows."
        )
    units = _text(given["units"], "plan units").lower()
    if units != UNITS:
        raise FloorPlanError(
            f"the plan is in {units!r}; Forge is millimetres end to end, so a plan "
            f"has to be \"{UNITS}\". Convert the numbers, or set "
            f"scale.mm_per_px and give the drawing's pixels instead."
        )

    seen: Dict[str, str] = {}
    normalized: Dict[str, Any] = {
        "version": version,
        "units": UNITS,
        "scale": _validate_scale(given.get("scale")),
        "defaults": _validate_defaults(given.get("defaults")),
        "rooms": [
            _validate_room(item, index, seen)
            for index, item in enumerate(_sequence(given.get("rooms"), "rooms"))
        ],
        "walls": [
            _validate_wall(item, index, seen)
            for index, item in enumerate(_sequence(given.get("walls"), "walls"))
        ],
        "labels": [
            _validate_label(item, index, seen)
            for index, item in enumerate(_sequence(given.get("labels"), "labels"))
        ],
        "history": _validate_history(given.get("history")),
    }
    normalized.update(_extras(given, _PLAN_KEYS))
    if warnings is not None:
        warnings.extend(plan_warnings(normalized))
    return normalized


# ==========================================================================
# fill_defaults
# ==========================================================================


def _opening_defaults(kind: str, defaults: Mapping[str, float],
                      ceiling: float) -> Dict[str, float]:
    """Width, height and sill for an opening nobody dimensioned.

    A ``gap`` is a doorway with no door in it, so it takes the door's width and
    runs floor to ceiling: that is what a cased opening between a kitchen and a
    dining room is, and it is the only reading that does not need a number the
    schema never asked for.
    """
    if kind == "door":
        return {"width_mm": float(defaults["door_w_mm"]),
                "height_mm": float(defaults["door_h_mm"]),
                "sill_mm": 0.0}
    if kind == "window":
        return {"width_mm": float(defaults["window_w_mm"]),
                "height_mm": float(defaults["window_h_mm"]),
                "sill_mm": float(defaults["sill_mm"])}
    return {"width_mm": float(defaults["door_w_mm"]),
            "height_mm": float(ceiling),
            "sill_mm": 0.0}


def fill_defaults(plan: Any) -> Dict[str, Any]:
    """Resolve every optional number, without touching the input.

    Validates first (so this is the only call most callers need), then fills:

    * a wall with no ``thickness_mm`` takes ``defaults.wall_mm``, with no
      ``height_mm`` takes ``defaults.ceiling_mm``;
    * a door takes ``door_w_mm`` x ``door_h_mm`` at sill 0, a window takes the
      window defaults, a gap takes the door's width at full wall height;
    * a room gets a floor slab unless it said ``"floor": false``, at
      ``defaults.floor_mm``.  **A caveat, stated because it is a divergence:**
      the add-on's ``floor`` is a whole-build toggle, not a per-room one, so a
      room that opts out here is left out of the build specs and out of the
      mask but would still get a slab from today's ``build_floorplan``.  The
      per-room flag is honoured everywhere the service is the authority; make
      it true everywhere until the add-on reads it.

    * a fixture's **height** comes from its own ``height_mm``, else from
      :mod:`service.appliance_dims` if the label is recognised, else from
      ``defaults.label_h_mm``.  Its width and depth always come from the
      drawing -- the library never overrides a footprint somebody measured.
    * an opening given as ``start_mm`` (its near edge) becomes ``at_mm`` (its
      centre) once the width is known, because that is the one the add-on's
      geometry is written in and carrying both is how they drift apart.

    **Provenance lives in one top-level ``provenance`` map, keyed by id, not on
    the entries.**  Every entry that comes out of here is therefore pure
    resolved schema -- which matters, because the add-on fingerprints each
    entry's canonical JSON to decide what to rebuild, and a note saying *where*
    a number came from would make a wall look changed when only the bookkeeping
    moved.  Each record is ``{"from_defaults": [...]}`` plus, for a label,
    ``height_from`` (``"plan"``/``"appliance"``/``"default"``) and
    ``appliance_match``.

    The openings are re-checked here, against their *resolved* sizes: a door
    that fitted at 820 mm does not fit if the plan's ``door_w_mm`` is raised to
    1200, and that has to be a refusal rather than a wall with a hole out its
    end.  Two openings that overlap are refused for the same reason the add-on
    refuses them -- one hole where two were drawn is not what anybody asked
    for.

    Idempotency note: feeding a resolved plan back in returns the same
    geometry, because every value is now explicit.  Only ``provenance``
    changes -- it empties, correctly, since nothing was taken from the defaults
    the second time.
    """
    normalized = validate_plan(plan)
    defaults = normalized["defaults"]
    provenance: Dict[str, Dict[str, Any]] = {}

    for room in normalized["rooms"]:
        came_from: List[str] = []
        if "floor" not in room:
            room["floor"] = True
        if "floor_mm" not in room:
            room["floor_mm"] = float(defaults["floor_mm"])
            came_from.append("floor_mm")
        provenance[room["id"]] = {"from_defaults": sorted(came_from)}

    for wall in normalized["walls"]:
        came_from = []
        if "thickness_mm" not in wall:
            wall["thickness_mm"] = float(defaults["wall_mm"])
            came_from.append("thickness_mm")
        if "height_mm" not in wall:
            wall["height_mm"] = float(defaults["ceiling_mm"])
            came_from.append("height_mm")
        provenance[wall["id"]] = {"from_defaults": sorted(came_from)}

        length = math.hypot(wall["to_mm"][0] - wall["from_mm"][0],
                            wall["to_mm"][1] - wall["from_mm"][1])
        for opening in wall["openings"]:
            fallbacks = _opening_defaults(opening["kind"], defaults, wall["height_mm"])
            opening_from: List[str] = []
            for key, value in fallbacks.items():
                if key not in opening:
                    opening[key] = value
                    opening_from.append(key)
            if "at_mm" not in opening:
                opening["at_mm"] = opening.pop("start_mm") + opening["width_mm"] / 2.0
            opening.pop("start_mm", None)
            if "swing" not in opening:
                opening["swing"] = None
            if opening["kind"] == "door" and "hinge" not in opening:
                opening["hinge"] = "left"
            provenance[opening["id"]] = {"from_defaults": sorted(opening_from)}
            _check_opening_span(opening["id"], wall["id"], length,
                                opening["at_mm"], opening["width_mm"])
            _check_opening_height(opening["id"], wall["id"], wall["height_mm"],
                                  opening["sill_mm"], opening["height_mm"])
        wall["openings"].sort(key=lambda o: (o["at_mm"], o["id"]))
        _check_openings_clear(wall)

    for label in normalized["labels"]:
        came_from = []
        if "source" not in label:
            label["source"] = "user"
        if "anchor" not in label:
            label["anchor"] = defaults["label_anchor"]
            came_from.append("anchor")
        if "height_mm" in label:
            height_from = "plan"
            match = appliance_dims.lookup(label["label"])
            match_name = match["match"] if match else None
        else:
            height, height_from, match_name = appliance_dims.height_for(
                label["label"], defaults["label_h_mm"]
            )
            label["height_mm"] = height
            if height_from == "default":
                came_from.append("height_mm")
        provenance[label["id"]] = {
            "from_defaults": sorted(came_from),
            "height_from": height_from,
            "appliance_match": match_name,
        }

    normalized["provenance"] = provenance
    return normalized


def _check_openings_clear(wall: Mapping[str, Any]) -> None:
    """No two openings in one wall may share any of it.

    The add-on refuses this and the service has to agree, or a plan passes here
    and fails there -- which is the worst of both, because the artist gets the
    refusal after the approval gate instead of before it.
    """
    previous: Optional[Dict[str, Any]] = None
    for opening in wall["openings"]:
        start = opening["at_mm"] - opening["width_mm"] / 2.0
        end = opening["at_mm"] + opening["width_mm"] / 2.0
        if previous is not None and start < previous["end"] - _POINT_EPS_MM:
            raise FloorPlanError(
                f"openings {previous['id']!r} ({previous['start']:g} to "
                f"{previous['end']:g} mm) and {opening['id']!r} ({start:g} to "
                f"{end:g} mm) overlap on wall {wall['id']!r}. Two holes drawn in the "
                f"same place build as one hole; move one, or delete one."
            )
        previous = {"id": opening["id"], "start": start, "end": end}


# ==========================================================================
# diff_plans -- the incremental-regen law
# ==========================================================================


def _canonical(value: Any) -> Any:
    """A value reduced to something comparable across a JSON round trip."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), COMPARE_DECIMALS) + 0.0
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _canonical(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_canonical(v) for v in value)
    return value


def _comparable(entry: Mapping[str, Any], *, drop: Iterable[str] = ()) -> Any:
    dropped = set(drop) | set(PROVENANCE_KEYS) | {"id"}
    return _canonical({k: v for k, v in entry.items() if k not in dropped})


def _entry_records(resolved: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every addressable component of a resolved plan, by id."""
    records: Dict[str, Dict[str, Any]] = {}
    for room in resolved["rooms"]:
        records[room["id"]] = {"kind": "room", "value": _comparable(room)}
    for wall in resolved["walls"]:
        records[wall["id"]] = {
            "kind": "wall",
            "value": _comparable(wall, drop=("openings",)),
        }
        for opening in wall["openings"]:
            body = dict(opening)
            body["wall"] = wall["id"]
            records[opening["id"]] = {
                "kind": "opening", "wall": wall["id"], "value": _comparable(body),
            }
    for label in resolved["labels"]:
        records[label["id"]] = {"kind": "label", "value": _comparable(label)}
    return records


def diff_plans(old: Any, new: Any) -> Dict[str, List[str]]:
    """What changed between two plans, by id.

    Returns exactly ``{"added", "removed", "changed", "unchanged"}``, each a
    sorted list of ids drawn from rooms, walls, openings and labels alike.

    This is the foundation of the incremental-regen law: the add-on rebuilds
    ``added`` and ``changed``, deletes ``removed``, and does not look at
    ``unchanged`` at all -- which is precisely why a wall the artist hand-edited
    survives an edit to the room next door.

    Three rules the naive version gets wrong:

    * **Both plans are resolved first.**  Comparison is of resolved values, so
      raising ``defaults.ceiling_mm`` marks every wall that was taking its
      height from the defaults as changed, and leaves a wall with its own
      explicit height alone.
    * **An opening's change is its wall's change.**  A door is built as a
      boolean into a wall, so a moved door means that wall is rebuilt; the door
      id is reported too, because the add-on names the cutout after it.  A door
      that moves from one wall to another marks both.
    * **An id is in exactly one list.**  A wall that is itself new is
      ``added``, never also ``changed``, even when it arrives full of new
      doors.
    """
    old_records = _entry_records(fill_defaults(old))
    new_records = _entry_records(fill_defaults(new))

    added = set(new_records) - set(old_records)
    removed = set(old_records) - set(new_records)
    changed: set = set()
    unchanged: set = set()

    for ident in set(old_records) & set(new_records):
        before, after = old_records[ident], new_records[ident]
        if before["kind"] != after["kind"] or before["value"] != after["value"]:
            changed.add(ident)
        else:
            unchanged.add(ident)

    # An opening that appeared, vanished or moved is a rebuild of its wall.
    touched_walls: set = set()
    for ident in added | changed:
        record = new_records.get(ident)
        if record is not None and record["kind"] == "opening":
            touched_walls.add(record["wall"])
    for ident in removed | changed:
        record = old_records.get(ident)
        if record is not None and record["kind"] == "opening":
            touched_walls.add(record["wall"])
    for wall_id in touched_walls:
        if wall_id in added or wall_id in removed:
            continue  # a new or deleted wall is already being built or dropped
        if wall_id in new_records:
            changed.add(wall_id)
            unchanged.discard(wall_id)

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "changed": sorted(changed),
        "unchanged": sorted(unchanged),
    }


# ==========================================================================
# component_build_specs -- pure data the add-on materialises verbatim
# ==========================================================================


def _carry_extras(spec: Dict[str, Any], entry: Mapping[str, Any],
                  known: Iterable[str]) -> Dict[str, Any]:
    """Copy an entry's unrecognised keys onto its build spec.

    The Phase 17 ``mechanism`` record on a door, a promoted fixture's mesh
    reference: the schema does not name them, the diff still watches them, and
    the add-on is the one that knows what to do with them.  Dropping them here
    would be the one place in the pipeline that quietly loses data.
    """
    skip = set(known) | set(PROVENANCE_KEYS)
    for key, value in entry.items():
        if key not in skip and key not in spec:
            spec[key] = copy.deepcopy(value)
    return spec


def _wall_frame(wall: Mapping[str, Any]) -> Tuple[List[float], List[float], float, float]:
    """``(along unit, across unit, length, angle_deg)`` for a wall."""
    ax, ay = wall["from_mm"]
    bx, by = wall["to_mm"]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    along = [dx / length, dy / length]
    across = [-along[1], along[0]]
    return along, across, length, math.degrees(math.atan2(dy, dx))


def _rect_corners(centre: Sequence[float], along: Sequence[float],
                  across: Sequence[float], half_along: float,
                  half_across: float) -> List[List[float]]:
    """Four corners, counter-clockwise from (+along, +across)."""
    cx, cy = centre
    return [
        [cx + along[0] * half_along + across[0] * half_across,
         cy + along[1] * half_along + across[1] * half_across],
        [cx - along[0] * half_along + across[0] * half_across,
         cy - along[1] * half_along + across[1] * half_across],
        [cx - along[0] * half_along - across[0] * half_across,
         cy - along[1] * half_along - across[1] * half_across],
        [cx + along[0] * half_along - across[0] * half_across,
         cy + along[1] * half_along - across[1] * half_across],
    ]


def _corner_shared(point: Sequence[float], wall_id: str,
                   walls: Sequence[Mapping[str, Any]]) -> bool:
    for other in walls:
        if other["id"] == wall_id:
            continue
        for end in (other["from_mm"], other["to_mm"]):
            if (abs(end[0] - point[0]) <= JOIN_TOLERANCE_MM
                    and abs(end[1] - point[1]) <= JOIN_TOLERANCE_MM):
                return True
    return False


def _from_defaults(provenance: Mapping[str, Any], ident: str) -> List[str]:
    return list(provenance.get(ident, {}).get("from_defaults", []))


def _wall_spec(wall: Mapping[str, Any], walls: Sequence[Mapping[str, Any]],
               provenance: Mapping[str, Any]) -> Dict[str, Any]:
    along, across, length, angle = _wall_frame(wall)
    thickness = float(wall["thickness_mm"])
    height = float(wall["height_mm"])
    start, end = wall["from_mm"], wall["to_mm"]

    # A wall whose end lands on another wall's end grows half a thickness into
    # that corner, so the corner comes out square instead of notched.  A free
    # end does not grow, so a wall never sticks out past where it was drawn.
    grow_from = thickness / 2.0 if _corner_shared(start, wall["id"], walls) else 0.0
    grow_to = thickness / 2.0 if _corner_shared(end, wall["id"], walls) else 0.0
    box_length = length + grow_from + grow_to
    mid = [
        (start[0] + end[0]) / 2.0 + along[0] * (grow_to - grow_from) / 2.0,
        (start[1] + end[1]) / 2.0 + along[1] * (grow_to - grow_from) / 2.0,
    ]

    openings: List[Dict[str, Any]] = []
    for opening in wall["openings"]:
        at = float(opening["at_mm"])
        width = float(opening["width_mm"])
        sill = float(opening["sill_mm"])
        top = sill + float(opening["height_mm"])
        centre = [start[0] + along[0] * at, start[1] + along[1] * at]
        across_size = thickness + 2.0 * CUTOUT_OVERSHOOT_MM
        jambs = [
            [start[0] + along[0] * (at - width / 2.0),
             start[1] + along[1] * (at - width / 2.0)],
            [start[0] + along[0] * (at + width / 2.0),
             start[1] + along[1] * (at + width / 2.0)],
        ]
        spec = {
            "id": opening["id"],
            "kind": opening["kind"],
            "at_mm": at,
            "width_mm": width,
            "height_mm": float(opening["height_mm"]),
            "sill_mm": sill,
            "swing": opening.get("swing"),
            "cutout_mm": {
                "center_mm": [centre[0], centre[1], (sill + top) / 2.0],
                "size_mm": [width, across_size, float(opening["height_mm"])],
                "angle_deg": angle,
                "corners_mm": _rect_corners(centre, along, across, width / 2.0,
                                            across_size / 2.0),
                "z_min_mm": sill,
                "z_max_mm": top,
                "overshoot_mm": CUTOUT_OVERSHOOT_MM,
            },
            "jambs_mm": jambs,
            #: The hinge edge for a Phase 17 revolute record. ``hinge: "left"``
            #: (the default) is the jamb nearer the wall's from_mm end, so the
            #: answer is the same every run rather than the same only when
            #: nobody reversed the wall.
            "hinge": opening.get("hinge"),
            "hinge_mm": jambs[1] if opening.get("hinge") == "right" else jambs[0],
            "from_defaults": _from_defaults(provenance, opening["id"]),
        }
        _carry_extras(spec, opening, _OPENING_KEYS)
        openings.append(spec)

    return _carry_extras({
        "id": wall["id"],
        "kind": "wall",
        "centerline_mm": [list(start), list(end)],
        "length_mm": length,
        "angle_deg": angle,
        "thickness_mm": thickness,
        "height_mm": height,
        "z_min_mm": 0.0,
        "z_max_mm": height,
        "center_mm": [mid[0], mid[1], height / 2.0],
        "size_mm": [box_length, thickness, height],
        "footprint_mm": _rect_corners(mid, along, across, box_length / 2.0,
                                      thickness / 2.0),
        "extended_mm": [grow_from, grow_to],
        "openings": openings,
        "from_defaults": _from_defaults(provenance, wall["id"]),
    }, wall, _WALL_KEYS)


def _floor_spec(room: Mapping[str, Any], provenance: Mapping[str, Any]) -> Dict[str, Any]:
    polygon = [list(point) for point in room["polygon_mm"]]
    thickness = float(room["floor_mm"])
    return _carry_extras({
        "id": room["id"],
        "kind": "floor",
        "label": room.get("label"),
        "polygon_mm": polygon,
        "thickness_mm": thickness,
        "z_min_mm": -thickness,
        "z_max_mm": 0.0,
        "area_mm2": abs(_signed_area(polygon)),
        "centroid_mm": _centroid(polygon),
        "from_defaults": _from_defaults(provenance, room["id"]),
    }, room, _ROOM_KEYS)


def _fixture_spec(label: Mapping[str, Any],
                  provenance: Mapping[str, Any]) -> Dict[str, Any]:
    x, y, width, depth = (float(v) for v in label["footprint_mm"])
    height = float(label["height_mm"])
    rotation = float(label.get("rotation_deg", 0.0))
    anchor = label.get("anchor", DEFAULTS["label_anchor"])
    # [x, y] is the box's centre by default -- the add-on's convention, and the
    # one a plan file has to be read in or every fixture lands half its own
    # size away from where it was drawn.
    centre = ([x, y] if anchor == "center"
              else [x + width / 2.0, y + depth / 2.0])
    radians = math.radians(rotation)
    along = [math.cos(radians), math.sin(radians)]
    across = [-along[1], along[0]]

    match = appliance_dims.lookup(label["label"])
    size_note = None
    if match is not None:
        want_w, want_d, _want_h = match["size_mm"]
        drawn = max(abs(width - want_w), abs(depth - want_d))
        if drawn > 50.0:
            size_note = (
                f"you drew {width:.0f} x {depth:.0f} mm; a {match['match']} is usually "
                f"{want_w:.0f} x {want_d:.0f} mm ({match['note']}). The drawing wins -- "
                f"this is a note, not a change."
            )

    return _carry_extras({
        "id": label["id"],
        "kind": "fixture",
        "label": label["label"],
        "footprint_mm": [x, y, width, depth],
        "anchor": anchor,
        "center_mm": [centre[0], centre[1], height / 2.0],
        "size_mm": [width, depth, height],
        "rotation_deg": rotation,
        "corners_mm": _rect_corners(centre, along, across, width / 2.0, depth / 2.0),
        "z_min_mm": 0.0,
        "z_max_mm": height,
        "source": label.get("source", "user"),
        "height_from": provenance.get(label["id"], {}).get("height_from", "plan"),
        "appliance": (
            None if match is None else {
                "match": match["match"],
                "size_mm": match["size_mm"],
                "confidence": match["confidence"],
                "how": match["how"],
                "note": match["note"],
            }
        ),
        "size_note": size_note,
        "from_defaults": _from_defaults(provenance, label["id"]),
    }, label, _LABEL_KEYS)


def component_build_specs(plan: Any) -> List[Dict[str, Any]]:
    """One build spec per component, in a deterministic order.

    Walls first (with every opening already resolved to an absolute cutout
    box), then the floor slab of each room that wants one, then the fixture box
    of each label.  Within a group the plan's own order is kept, so the list is
    stable across runs and a diff of two spec lists is readable.

    Each spec's ``id`` is the component's plan id, which is also its Blender
    object name (``FP:<id>``) and the id :func:`diff_plans` reports -- so a
    rebuild is "take the specs whose ids are in ``added`` + ``changed``" and
    nothing more.

    The output is pure data: numbers, lists and strings.  No geometry library
    is imported to produce it and none is needed downstream to read it -- a
    wall is a box with a centre, a size and an angle, and a doorway is a
    smaller box with the same three.  Cutouts are grown
    :data:`CUTOUT_OVERSHOOT_MM` through the wall on each side so the boolean
    never meets two coincident faces.
    """
    resolved = fill_defaults(plan)
    provenance = resolved.get("provenance", {})
    walls = resolved["walls"]
    specs: List[Dict[str, Any]] = [_wall_spec(wall, walls, provenance) for wall in walls]
    specs.extend(_floor_spec(room, provenance)
                 for room in resolved["rooms"] if room.get("floor", True))
    specs.extend(_fixture_spec(label, provenance) for label in resolved["labels"])
    return specs


# ==========================================================================
# snap_segments -- the deterministic half of extraction
# ==========================================================================


def _cluster_1d(values: Sequence[float], merge: float) -> List[List[int]]:
    """Group indices whose values are within *merge* of the group's first.

    Capped width, not single-link chaining: a run of walls 100 mm apart must
    not collapse into one line just because each is close to the last.
    """
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    clusters: List[List[int]] = []
    for index in order:
        if clusters and values[index] - values[clusters[-1][0]] <= merge:
            clusters[-1].append(index)
        else:
            clusters.append([index])
    return clusters


def snap_segments(segments: Any, mm_per_px: float = 1.0, tolerance_deg: float = 5.0,
                  merge_mm: float = 150.0, angles_deg: Sequence[float] = (0.0, 90.0),
                  min_length_mm: float = 10.0,
                  join_collinear: bool = True) -> Dict[str, Any]:
    """Straighten raw extracted line segments into wall centrelines.

    This is the deterministic half of the two-layer extraction law: the
    threshold/line pass hands us wobbly hand-drawn segments in **pixels**, and
    this turns them into millimetre centrelines that are actually straight and
    actually meet.  The VLM never sees this step; it is arithmetic, and it runs
    the same way twice.

    Input is ``[{"from_px": [x, y], "to_px": [x, y], "id"?}, ...]`` (or
    ``from_mm``/``to_mm`` if the caller has already scaled, in which case
    *mm_per_px* is not applied to that segment).  Four passes, in order:

    1. **Angle snap.**  A segment within *tolerance_deg* of one of
       *angles_deg* (mod 180) is rotated about its own midpoint onto that
       angle, so its length and its position are preserved and only its
       wobble is removed.
    2. **Weld.**  Endpoint coordinates within *merge_mm* are pulled onto their
       shared mean, x and y independently.  This is the pass that closes the
       corners the angle snap opened: rotating four sides of a hand-drawn
       rectangle straight leaves four small gaps, and this is what makes it a
       rectangle.
    3. **Join.**  Collinear axis-aligned segments that overlap or nearly touch
       become one, so a wall broken into three strokes is one wall.  A doorway
       gap is far wider than *merge_mm*, so this never swallows a door.
    4. **Drop.**  Anything shorter than *min_length_mm* after all that was a
       tick mark.

    Returns ``{"segments": [...], "report": {...}}``.  The report names every
    id it moved and by how much -- a snap that quietly moved a wall 300 mm is
    the failure this whole function has to be auditable against.

    Direction: an axis-aligned output segment always runs low coordinate to
    high, whether or not anything was joined into it, so two extractions of the
    same drawing agree.  Nothing downstream reads a raw segment's direction --
    an opening's ``at_mm`` is measured along a *wall*, and a wall gets its
    direction when a person gives it an id.

    Ids: a segment that arrives with one keeps it.  A segment without one is
    numbered ``seg-01`` by input order and listed in ``report["ids_assigned"]``.
    These are extraction artefacts and not yet plan entries -- the moment they
    become plan entries, the no-invented-ids law binds and
    :func:`validate_plan` refuses anything unnamed.
    """
    scale = _number(mm_per_px, "mm_per_px", positive=True, allow_zero=False)
    tolerance = _number(tolerance_deg, "tolerance_deg", positive=True, allow_zero=True)
    merge = _number(merge_mm, "merge_mm", positive=True, allow_zero=True)
    minimum = _number(min_length_mm, "min_length_mm", positive=True, allow_zero=True)
    targets = [
        _number(angle, "angles_deg entry") % 180.0
        for angle in _sequence(angles_deg, "angles_deg", allow_missing=False)
    ]
    if not targets:
        raise FloorPlanError("angles_deg must name at least one angle to snap to.")

    raw = _sequence(segments, "segments")
    seen_ids: Dict[str, int] = {}
    assigned: List[str] = []
    items: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    for index, entry in enumerate(raw):
        where = f"segments[{index}]"
        given = _mapping(entry, where)
        if "from_mm" in given or "to_mm" in given:
            if "from_px" in given or "to_px" in given:
                raise FloorPlanError(
                    f"{where} gives both pixels and millimetres. Pick one -- mixing "
                    f"them is how a wall ends up scaled twice."
                )
            start = _point(given.get("from_mm"), f"{where} from_mm")
            end = _point(given.get("to_mm"), f"{where} to_mm")
        else:
            start = [v * scale for v in _point(given.get("from_px"), f"{where} from_px")]
            end = [v * scale for v in _point(given.get("to_px"), f"{where} to_px")]

        ident_raw = given.get("id")
        if ident_raw is None:
            ident = f"seg-{index + 1:02d}"
            assigned.append(ident)
        else:
            ident = _text(ident_raw, f"{where} id")
        if ident in seen_ids:
            raise FloorPlanError(
                f"{where} reuses the segment id {ident!r}; the report is keyed by id, "
                f"so two segments cannot share one."
            )
        seen_ids[ident] = index

        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length < _POINT_EPS_MM:
            dropped.append({"id": ident, "reason": "zero length in the input",
                            "length_mm": length})
            continue
        items.append({"id": ident, "from": start, "to": end,
                      "input_from": list(start), "input_to": list(end),
                      "axis": None, "snapped": False})

    # ---- 1. angle snap ---------------------------------------------------
    angle_snapped: List[Dict[str, Any]] = []
    for item in items:
        dx = item["to"][0] - item["from"][0]
        dy = item["to"][1] - item["from"][1]
        length = math.hypot(dx, dy)
        angle = math.degrees(math.atan2(dy, dx))
        folded = angle % 180.0
        best = None
        for target in targets:
            delta = (folded - target + 90.0) % 180.0 - 90.0
            if best is None or abs(delta) < abs(best[1]):
                best = (target, delta)
        target, delta = best
        if abs(delta) <= tolerance and abs(delta) > 1e-12:
            new_angle = math.radians(angle - delta)
            mid = [(item["from"][0] + item["to"][0]) / 2.0,
                   (item["from"][1] + item["to"][1]) / 2.0]
            half = length / 2.0
            item["from"] = [mid[0] - math.cos(new_angle) * half,
                            mid[1] - math.sin(new_angle) * half]
            item["to"] = [mid[0] + math.cos(new_angle) * half,
                          mid[1] + math.sin(new_angle) * half]
            item["snapped"] = True
            angle_snapped.append({
                "id": item["id"],
                "was_deg": round(angle % 180.0, 4),
                "now_deg": round(round(angle - delta, 9) % 180.0, 4),
                "delta_deg": round(delta, 4),
            })
        elif abs(delta) <= tolerance:
            item["snapped"] = True  # already exactly on axis
        item["axis"] = _axis_of(item["from"], item["to"])

    # ---- 2. weld endpoint coordinates -----------------------------------
    welded: List[Dict[str, Any]] = []
    for axis_index, axis_name in ((0, "x"), (1, "y")):
        handles: List[Tuple[Dict[str, Any], str]] = []
        for item in items:
            handles.append((item, "from"))
            handles.append((item, "to"))
        values = [handle[0][handle[1]][axis_index] for handle in handles]
        for cluster in _cluster_1d(values, merge):
            if len(cluster) < 2:
                continue
            members = [values[i] for i in cluster]
            if max(members) - min(members) <= _POINT_EPS_MM:
                continue
            target = sum(members) / len(members)
            moved = 0.0
            ids: List[str] = []
            for i in cluster:
                item, end_name = handles[i]
                moved = max(moved, abs(item[end_name][axis_index] - target))
                item[end_name][axis_index] = target
                if item["id"] not in ids:
                    ids.append(item["id"])
            welded.append({
                "axis": axis_name,
                "to_mm": round(target, 4),
                "from_mm": [round(v, 4) for v in sorted(members)],
                "ids": ids,
                "moved_mm": round(moved, 4),
            })
    for item in items:
        item["axis"] = _axis_of(item["from"], item["to"])

    # ---- 3. join collinear runs -----------------------------------------
    joined: List[Dict[str, Any]] = []
    if join_collinear:
        items, joined = _join_collinear(items, merge)

    # ---- 4. drop the tick marks -----------------------------------------
    kept: List[Dict[str, Any]] = []
    for item in items:
        length = math.hypot(item["to"][0] - item["from"][0],
                            item["to"][1] - item["from"][1])
        if length < minimum:
            dropped.append({"id": item["id"],
                            "reason": f"shorter than min_length_mm ({minimum:g} mm) "
                                      f"after snapping",
                            "length_mm": round(length, 4)})
            continue
        kept.append(item)

    out_segments: List[Dict[str, Any]] = []
    max_move = 0.0
    right_angles = 0
    for item in kept:
        #: Measured against whichever way round the input was drawn: the join
        #: pass normalises an axis-aligned segment to run low-to-high, and a
        #: segment reported as having moved 4 m because somebody drew it
        #: right-to-left would be a lie in the one report that has to be
        #: trustworthy.
        moved = min(
            max(math.hypot(item["from"][0] - item["input_from"][0],
                           item["from"][1] - item["input_from"][1]),
                math.hypot(item["to"][0] - item["input_to"][0],
                           item["to"][1] - item["input_to"][1])),
            max(math.hypot(item["from"][0] - item["input_to"][0],
                           item["from"][1] - item["input_to"][1]),
                math.hypot(item["to"][0] - item["input_from"][0],
                           item["to"][1] - item["input_from"][1])),
        )
        max_move = max(max_move, moved)
        axis = _axis_of(item["from"], item["to"])
        if axis is not None:
            right_angles += 1
        length = math.hypot(item["to"][0] - item["from"][0],
                            item["to"][1] - item["from"][1])
        out_segments.append({
            "id": item["id"],
            "from_mm": [round(v, 6) for v in item["from"]],
            "to_mm": [round(v, 6) for v in item["to"]],
            "length_mm": round(length, 6),
            "angle_deg": round(math.degrees(math.atan2(item["to"][1] - item["from"][1],
                                                       item["to"][0] - item["from"][0])), 6),
            "axis": axis,
            "snapped": bool(item["snapped"]),
            "moved_mm": round(moved, 4),
        })

    report = {
        "input": len(raw),
        "output": len(out_segments),
        "mm_per_px": scale,
        "tolerance_deg": tolerance,
        "merge_mm": merge,
        "min_length_mm": minimum,
        "angles_deg": targets,
        "join_collinear": bool(join_collinear),
        "angle_snapped": angle_snapped,
        "welded": welded,
        "joined": joined,
        "dropped": dropped,
        "ids_assigned": assigned,
        "axis_aligned": right_angles,
        "max_move_mm": round(max_move, 4),
        "summary": (
            f"{len(out_segments)} of {len(raw)} segments kept; "
            f"{len(angle_snapped)} straightened onto an axis, "
            f"{len(welded)} coordinate welds, {len(joined)} joins, "
            f"{len(dropped)} dropped; nothing moved more than "
            f"{max_move:.1f} mm."
        ),
    }
    return {"segments": out_segments, "report": report}


def _axis_of(start: Sequence[float], end: Sequence[float]) -> Optional[str]:
    """``"x"`` for a horizontal segment, ``"y"`` for a vertical one, else None."""
    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    if dy <= _POINT_EPS_MM and dx > _POINT_EPS_MM:
        return "x"
    if dx <= _POINT_EPS_MM and dy > _POINT_EPS_MM:
        return "y"
    return None


def _join_collinear(items: List[Dict[str, Any]],
                    merge: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Merge axis-aligned segments sharing a line into single runs."""
    groups: Dict[Tuple[str, float], List[Dict[str, Any]]] = {}
    free: List[Dict[str, Any]] = []
    for item in items:
        axis = _axis_of(item["from"], item["to"])
        if axis is None:
            free.append(item)
            continue
        constant = item["from"][1] if axis == "x" else item["from"][0]
        groups.setdefault((axis, round(constant, 6)), []).append(item)

    joined: List[Dict[str, Any]] = []
    survivors: List[Dict[str, Any]] = []
    for (axis, _constant), members in groups.items():
        index = 0 if axis == "x" else 1
        spans = []
        for item in members:
            low = min(item["from"][index], item["to"][index])
            high = max(item["from"][index], item["to"][index])
            spans.append((low, high, item))
        spans.sort(key=lambda span: (span[0], span[1], span[2]["id"]))
        current_low, current_high, keeper = spans[0]
        absorbed: List[str] = []
        runs: List[Tuple[float, float, Dict[str, Any], List[str]]] = []
        for low, high, item in spans[1:]:
            if low <= current_high + merge:
                current_high = max(current_high, high)
                absorbed.append(item["id"])
            else:
                runs.append((current_low, current_high, keeper, absorbed))
                current_low, current_high, keeper, absorbed = low, high, item, []
        runs.append((current_low, current_high, keeper, absorbed))
        for low, high, item, absorbed_ids in runs:
            if axis == "x":
                item["from"] = [low, item["from"][1]]
                item["to"] = [high, item["from"][1]]
            else:
                item["from"] = [item["from"][0], low]
                item["to"] = [item["from"][0], high]
            if absorbed_ids:
                joined.append({
                    "id": item["id"],
                    "absorbed": sorted(absorbed_ids),
                    "axis": axis,
                    "length_mm": round(high - low, 4),
                })
            survivors.append(item)

    survivors.extend(free)
    order = {id(item): position for position, item in enumerate(items)}
    survivors.sort(key=lambda item: order[id(item)])
    return survivors, joined


# ==========================================================================
# plan_mask -- the plan as a picture, so a build can be measured against it
# ==========================================================================


def plan_bounds(plan: Any) -> List[float]:
    """``[x0, y0, x1, y1]`` covering every component of the plan, in mm."""
    return _bounds_from_specs(component_build_specs(plan))


def _bounds_from_specs(specs: Sequence[Mapping[str, Any]]) -> List[float]:
    xs: List[float] = []
    ys: List[float] = []
    for spec in specs:
        if spec["kind"] == "floor":
            points = spec["polygon_mm"]
        elif spec["kind"] == "wall":
            points = spec["footprint_mm"]
        else:
            points = spec["corners_mm"]
        for x, y in points:
            xs.append(x)
            ys.append(y)
    if not xs:
        raise FloorPlanError(
            "the plan has nothing in it -- no rooms, no walls, no labels -- so there "
            "is nothing to measure. Draw something first."
        )
    return [min(xs), min(ys), max(xs), max(ys)]


def _rect_mask(grid_x: np.ndarray, grid_y: np.ndarray, centre: Sequence[float],
               angle_deg: float, size: Sequence[float]) -> np.ndarray:
    """Cells whose centre lies inside a rotated rectangle."""
    radians = math.radians(angle_deg)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    rel_x = grid_x - centre[0]
    rel_y = grid_y - centre[1]
    along = rel_x * cos_a + rel_y * sin_a
    across = -rel_x * sin_a + rel_y * cos_a
    return (np.abs(along) <= size[0] / 2.0) & (np.abs(across) <= size[1] / 2.0)


def plan_mask(plan: Any, cell_mm: float = 50.0, bounds_mm: Optional[Sequence[float]] = None,
              pad_mm: Optional[float] = None,
              include_openings: bool = True) -> Dict[str, Any]:
    """Rasterise the plan top-down: what the level should look like from above.

    This is the plan's half of the verification pair.  The other half is a
    top-down orthographic render of the built level; :func:`mask_iou` puts the
    two numbers together, per component or whole-plan, and that IoU is a
    *measured* claim rather than an opinion.

    Walls and fixtures occupy cells.  **Floor slabs do not** -- a floor covers
    every room completely and an IoU against a filled rectangle measures
    nothing.

    Openings cut the mask only when they run floor to ceiling (a ``gap``, or a
    door drawn full height).  That is the honest rule: seen from above, a
    doorway with a header over it is still a solid wall, and pretending
    otherwise would make every correctly built level score badly.

    Returns ``{"mask": <numpy bool array, rows x cols>, "cell_mm", "origin_mm",
    "bounds_mm", "shape", "cells_filled", "area_mm2", "components"}``.  Row 0
    is the LOWEST y -- this is millimetre space, not an image, and it is not
    flipped.  ``components`` counts the cells each component covers, overlaps
    counted in both.

    Pass *bounds_mm* to rasterise two plans onto the same grid; without it the
    grid is the plan's own extent, padded and snapped down to a whole number of
    cells so the answer does not wander between runs.
    """
    cell = _number(cell_mm, "cell_mm", positive=True, allow_zero=False)
    specs = component_build_specs(plan)

    if bounds_mm is None:
        pad = cell * 2.0 if pad_mm is None else _number(pad_mm, "pad_mm", positive=True)
        x0, y0, x1, y1 = _bounds_from_specs(specs)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    else:
        extent = _sequence(bounds_mm, "bounds_mm", allow_missing=False)
        if len(extent) != 4:
            raise FloorPlanError(
                f"bounds_mm must be [x0, y0, x1, y1] in millimetres, got "
                f"{len(extent)} numbers."
            )
        x0 = _number(extent[0], "bounds_mm x0")
        y0 = _number(extent[1], "bounds_mm y0")
        x1 = _number(extent[2], "bounds_mm x1")
        y1 = _number(extent[3], "bounds_mm y1")
        if x1 <= x0 or y1 <= y0:
            raise FloorPlanError(
                f"bounds_mm is empty: x {x0:g}..{x1:g}, y {y0:g}..{y1:g}. The second "
                f"corner has to be above and right of the first."
            )

    origin_x = math.floor(x0 / cell) * cell
    origin_y = math.floor(y0 / cell) * cell
    cols = max(1, int(math.ceil((x1 - origin_x) / cell)))
    rows = max(1, int(math.ceil((y1 - origin_y) / cell)))
    if cols * rows > MAX_MASK_CELLS:
        raise FloorPlanError(
            f"a {cols} x {rows} grid is {cols * rows} cells, past the "
            f"{MAX_MASK_CELLS} cap. Raise cell_mm (it is {cell:g} mm now) -- the "
            f"plan spans {x1 - x0:.0f} x {y1 - y0:.0f} mm."
        )

    centres_x = origin_x + (np.arange(cols) + 0.5) * cell
    centres_y = origin_y + (np.arange(rows) + 0.5) * cell
    grid_x, grid_y = np.meshgrid(centres_x, centres_y)

    mask = np.zeros((rows, cols), dtype=bool)
    components: Dict[str, int] = {}
    for spec in specs:
        if spec["kind"] == "wall":
            covered = _rect_mask(grid_x, grid_y, spec["center_mm"][:2],
                                 spec["angle_deg"], spec["size_mm"][:2])
            if include_openings:
                for opening in spec["openings"]:
                    full_height = (
                        opening["sill_mm"] <= _POINT_EPS_MM
                        and opening["sill_mm"] + opening["height_mm"]
                        >= spec["height_mm"] - _POINT_EPS_MM
                    )
                    if not full_height:
                        continue
                    cut = _rect_mask(grid_x, grid_y, opening["cutout_mm"]["center_mm"][:2],
                                     opening["cutout_mm"]["angle_deg"],
                                     opening["cutout_mm"]["size_mm"][:2])
                    covered &= ~cut
        elif spec["kind"] == "fixture":
            covered = _rect_mask(grid_x, grid_y, spec["center_mm"][:2],
                                 spec["rotation_deg"], spec["size_mm"][:2])
        else:
            continue
        components[spec["id"]] = int(covered.sum())
        mask |= covered

    filled = int(mask.sum())
    return {
        "mask": mask,
        "cell_mm": cell,
        "origin_mm": [origin_x, origin_y],
        "bounds_mm": [origin_x, origin_y, origin_x + cols * cell, origin_y + rows * cell],
        "shape": [rows, cols],
        "cells_filled": filled,
        "area_mm2": filled * cell * cell,
        "components": components,
        "rows_are": "+Y ascending: row 0 is the lowest y, not an image's top row",
    }


def _as_array(value: Any, what: str) -> Tuple[np.ndarray, Optional[Dict[str, Any]]]:
    if isinstance(value, Mapping):
        if "mask" not in value:
            raise FloorPlanError(f"{what} is an object with no 'mask' in it.")
        return np.asarray(value["mask"], dtype=bool), dict(value)
    array = np.asarray(value, dtype=bool)
    if array.ndim != 2:
        raise FloorPlanError(
            f"{what} must be a 2-D mask, got {array.ndim} dimension(s)."
        )
    return array, None


def mask_iou(first: Any, second: Any) -> float:
    """Intersection over union of two plan masks, 0.0 to 1.0.

    Takes either :func:`plan_mask` results or bare boolean arrays.  Two results
    rasterised on different grids are a refusal rather than a number: an IoU
    between two different coordinate systems is arithmetic that means nothing,
    and it would be believed.

    Two empty masks score 1.0 -- nothing was asked for and nothing is missing.
    """
    array_a, meta_a = _as_array(first, "the first mask")
    array_b, meta_b = _as_array(second, "the second mask")
    if meta_a is not None and meta_b is not None:
        if (abs(float(meta_a["cell_mm"]) - float(meta_b["cell_mm"])) > 1e-9
                or any(abs(a - b) > 1e-6 for a, b in zip(meta_a["origin_mm"],
                                                         meta_b["origin_mm"]))):
            raise FloorPlanError(
                f"the two masks were rasterised on different grids "
                f"(cell {meta_a['cell_mm']:g} mm at {meta_a['origin_mm']} vs "
                f"{meta_b['cell_mm']:g} mm at {meta_b['origin_mm']}). Pass the same "
                f"bounds_mm and cell_mm to both -- an IoU across two coordinate "
                f"systems is a number that means nothing and gets believed anyway."
            )
    if array_a.shape != array_b.shape:
        raise FloorPlanError(
            f"the two masks are different sizes ({array_a.shape} and "
            f"{array_b.shape}); rasterise both with the same bounds_mm."
        )
    union = int(np.logical_or(array_a, array_b).sum())
    if union == 0:
        return 1.0
    intersection = int(np.logical_and(array_a, array_b).sum())
    return intersection / union


__all__ = [
    "ANCHORS",
    "CUTOUT_OVERSHOOT_MM",
    "DEFAULTS",
    "ENTRY_KINDS",
    "HINGES",
    "FloorPlanError",
    "LABEL_SOURCES",
    "OPENING_KINDS",
    "PLAN_VERSION",
    "RECTILINEAR_TOLERANCE_DEG",
    "SUPPORTED_VERSIONS",
    "component_build_specs",
    "diff_plans",
    "fill_defaults",
    "mask_iou",
    "plan_bounds",
    "plan_mask",
    "plan_warnings",
    "snap_segments",
    "validate_plan",
]
