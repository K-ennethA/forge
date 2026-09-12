"""Floor plans to prototype levels (Phase 19) — the add-on half.

One socket command, ``build_floorplan``, which turns a ``floorplan.json`` object
into scene objects and — the whole point of the phase — **updates them in place
on every call after the first**.

The artist's ask, near-verbatim: *"I draw rooms, walls, doors, and give it a
label key — washer/dryer here — and I want a simple 3d level created with the
walls and doors, and washer/dryer as rectangles to help map out. Then from there
we can edit a shape and make it more complex. We should be able to do
prototypes; on the floor plans and basic 3d rooms we should allow modifications
on drawings without full regen; we support additions."*

The incremental law
-------------------
*Rebuilding is a DIFF against ids, never a full regen.*  Every room, wall,
opening and label in the plan carries an id that survives edits, and that id
names the Blender object: ``FP:wall-01``, ``FP:wd-01``.  So:

* an **unchanged** entry is not touched **at all** — same object, same mesh
  datablock, same materials, same custom properties, same transform.  The code
  path for it is a ``continue``;
* a **changed** entry rebuilds only its own object (the object survives, its
  mesh datablock is swapped, so parenting/modifiers/props the artist added
  survive too);
* a **new** id creates;
* a **missing** id deletes — and only ever an ``FP:``-prefixed object inside the
  named collection, because deleting something Forge did not build would be the
  one unforgivable bug in a command whose job is to leave things alone.

"Changed" is decided by a **fingerprint**: a content hash of the entry's fully
resolved values (every default filled in, every millimetre computed) written
into ``obj["forge_fp_hash"]`` when the object is built.  Same hash, same
geometry, nothing to do.  A hash is used rather than a re-derived comparison
because the plan's *source* text can change in a hundred ways that mean nothing
— key order, whitespace, a default written out explicitly — and only the
resolved numbers decide what gets built.  ``BUILD_VERSION`` is hashed in too, so
the day this module's geometry changes, every object correctly reads as stale.

Never clobber the artist's hands
--------------------------------
Promotion is one-way.  Once the artist has elaborated a placeholder — scaled it,
sculpted it, swapped its mesh — the plan keeps the slot's footprint as the size
contract but the *geometry* belongs to them.  So before rebuilding or deleting
anything, the object is asked whether it still looks like what Forge built:

* ``obj["forge_fp_keep"] = True`` set by hand is an unconditional "hands off",
  honoured even in ``mode: "rebuild"``;
* a vertex count that no longer matches ``obj["forge_fp_verts"]``;
* dimensions that no longer match ``obj["forge_fp_dims_mm"]`` by more than
  ``KEEP_TOL_MM`` — which is what catches a scale in the viewport, the most
  common way a placeholder gets adjusted;
* no fingerprint at all, which means Forge did not build this object and has no
  business overwriting it.

Any of those and the entry is **skipped**, reported under ``kept`` with a reason
naming it, and warned about.  ``mode: "rebuild"`` is the escape hatch for the
mesh heuristics — it throws the greybox away and builds again — but it still
honours ``forge_fp_keep``, because an explicit marker outranks a mode.

What this is NOT
----------------
A construction drawing.  Walls are boxes on the plan's centrelines, openings are
rectangular cutouts with a header above and (for windows) a sill below, and a
label is a box at its stated footprint.  Nothing is framed, structural,
code-compliant or load-bearing; the mechanism records describe an *intended*
swing rather than a rigged hinge (Phase 17's data shape, no rigging here); and
the sizes are only as true as whatever calibrated the plan.  Said in the
``honesty`` field of every single report.

Units
-----
Millimetres on the wire, metres in Blender, converted once at the ``bpy``
boundary by ``common.build_mesh_object(scale=MM_TO_M)`` — the house rule, and
the reason every number in this file is a millimetre until it is handed over.

Undo
----
``build_floorplan`` changes the .blend, so it is deliberately **not** in
``READ_ONLY_COMMANDS``: the registry pushes ``Forge: build_floorplan`` before it
runs and one Ctrl+Z takes the whole diff back.
"""

import difflib
import hashlib
import json
import math
import time

import bpy
from mathutils import Vector

from . import common
from .common import MM_TO_M, get_str
from .registry import ForgeError, command

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: Every object this command owns starts with this.  It is the whole safety
#: story for deletion: an object without it is somebody else's.
PREFIX = "FP:"

DEFAULT_COLLECTION = "Floorplan"

#: Hashed into every fingerprint.  Bump it whenever the geometry this module
#: builds changes, so existing objects read as stale and rebuild instead of
#: silently keeping last version's shape under this version's hash.
BUILD_VERSION = 1

#: Blender truncates names past this, and a truncated name is a name we can no
#: longer look the object up by. Refused up front rather than half-worked.
MAX_NAME_CHARS = 63

#: Geometry comparisons, in millimetres. Plans arrive with computed numbers, so
#: this is float noise, not tolerance.
EPS_MM = 1e-6

#: How far an object may drift from what Forge built before it counts as the
#: artist's work. Half a millimetre on a 2.4 m wall is nobody's edit and every
#: float's rounding.
KEEP_TOL_MM = 0.5

#: The plan's ``defaults`` block, with this module's fallbacks behind it. The
#: first four names are the schema's; the rest are the numbers a greybox still
#: needs and the schema does not spell (a window has to be *some* size).
PLAN_DEFAULTS = {
    "ceiling_mm": 2400.0,     # wall height
    "wall_mm": 100.0,         # wall thickness
    "door_w_mm": 820.0,
    "door_h_mm": 2040.0,
    "window_w_mm": 1200.0,
    "window_h_mm": 1200.0,
    "sill_mm": 900.0,         # window sill height off the floor
    "label_h_mm": 850.0,      # a washer is 850 mm tall; so is most casework
    "floor_mm": 50.0,         # room slab thickness, built BELOW z = 0
    "label_anchor": "center",
}

OPENING_KINDS = ("door", "window", "gap")
ANCHORS = ("center", "corner")
HINGES = ("left", "right")
SWINGS = ("in", "out")
MODES = ("update", "rebuild")

#: Flat colours by kind: grey wall, darker floor, accent fixture. Created once
#: and reused by name — a greybox with a hundred materials is not a greybox.
MATERIALS = {
    "wall": ("Forge FP Wall", (0.620, 0.612, 0.596, 1.0)),
    "floor": ("Forge FP Floor", (0.235, 0.227, 0.216, 1.0)),
    "fixture": ("Forge FP Fixture", (0.851, 0.451, 0.129, 1.0)),
}

#: Which material each entry kind wears.
KIND_MATERIAL = {"room": "floor", "wall": "wall", "label": "fixture"}

HONESTY = (
    "This is a prototype greybox at real sizes, not a construction drawing. "
    "Walls are boxes on the plan's centrelines, openings are rectangular "
    "cutouts with a header above and (for windows) a sill below, and every "
    "label is a plain box at its stated footprint. Nothing here is framed, "
    "structural, insulated, code-compliant or load-bearing; a door's mechanism "
    "record describes the INTENDED swing and nothing is rigged; and every "
    "dimension is only as true as whatever calibrated the plan. It is for "
    "mapping the space out and for standing in — treat it as the sketch it is."
)


# ---------------------------------------------------------------------------
# small parsing helpers — every message names the entry it is about
# ---------------------------------------------------------------------------

def _hint(wanted, choices):
    """" Did you mean 'window'?" — the house's difflib nudge."""
    close = difflib.get_close_matches(str(wanted).lower(), list(choices), n=1, cutoff=0.4)
    return (" Did you mean %r?" % close[0]) if close else ""


def _num(value, where, minimum=None, maximum=None):
    """One finite number, or a sentence saying which field was wrong."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ForgeError("%s must be a number, got %r." % (where, value))
    value = float(value)
    if not math.isfinite(value):
        raise ForgeError("%s must be a finite number, got %r." % (where, value))
    if minimum is not None and value < minimum:
        raise ForgeError("%s must be >= %g (got %g)." % (where, minimum, value))
    if maximum is not None and value > maximum:
        raise ForgeError("%s must be <= %g (got %g)." % (where, maximum, value))
    return value


def _point(value, where):
    """``[x, y]`` in millimetres."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ForgeError("%s must be [x, y] in millimetres, got %r." % (where, value))
    return (_num(value[0], where + "[0]"), _num(value[1], where + "[1]"))


def _entry_id(entry, where, index):
    if not isinstance(entry, dict):
        raise ForgeError("%s[%d] must be an object, got %s."
                         % (where, index, type(entry).__name__))
    raw = entry.get("id")
    if not isinstance(raw, str) or not raw.strip():
        raise ForgeError(
            "%s[%d] has no 'id'. Every room, wall, opening and label needs a "
            "stable id — it is what names the object (FP:wall-01) and what lets "
            "the next call edit this one thing instead of rebuilding the level."
            % (where, index))
    ident = raw.strip()
    if len(PREFIX) + len(ident) > MAX_NAME_CHARS:
        raise ForgeError(
            "The id %r is too long: %r would be %d characters and Blender "
            "truncates object names at %d, after which Forge could no longer "
            "find this object to update it."
            % (ident, PREFIX + ident, len(PREFIX) + len(ident), MAX_NAME_CHARS))
    return ident


def _round(value, digits=4):
    """Rounded for the report *and* for the hash — one canonical number."""
    number = round(float(value) + 0.0, digits)
    return 0.0 if number == 0 else number


def _round_all(values, digits=4):
    return [_round(v, digits) for v in values]


# ---------------------------------------------------------------------------
# resolving the plan — all of it, before one thing in the scene is touched
# ---------------------------------------------------------------------------

def _resolve_defaults(plan, warnings):
    """The plan's ``defaults`` over this module's fallbacks."""
    raw = plan.get("defaults")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ForgeError("'defaults' must be an object of named millimetre "
                         "values, got %s." % type(raw).__name__)
    out = dict(PLAN_DEFAULTS)
    for key, value in raw.items():
        if key not in PLAN_DEFAULTS:
            warnings.append("defaults.%s is not a name Forge knows, so it was "
                            "ignored. Known: %s." % (key, ", ".join(sorted(PLAN_DEFAULTS))))
            continue
        if key == "label_anchor":
            if not isinstance(value, str) or value.strip().lower() not in ANCHORS:
                raise ForgeError("defaults.label_anchor must be 'center' or "
                                 "'corner', got %r." % (value,))
            out[key] = value.strip().lower()
            continue
        out[key] = _num(value, "defaults.%s" % key, minimum=0.0)
    if out["ceiling_mm"] <= 0.0:
        raise ForgeError("defaults.ceiling_mm must be greater than 0 — a wall "
                         "with no height is not a wall.")
    if out["wall_mm"] <= 0.0:
        raise ForgeError("defaults.wall_mm must be greater than 0 — a wall with "
                         "no thickness would build as a sheet of nothing.")
    return out


def _resolve_room(entry, index, defaults, floors, warnings):
    ident = _entry_id(entry, "rooms", index)
    polygon = entry.get("polygon_mm")
    if not isinstance(polygon, (list, tuple)) or len(polygon) < 3:
        raise ForgeError(
            "Room %r needs a 'polygon_mm' of at least three [x, y] points in "
            "millimetres — that outline is what the floor slab is cut from."
            % ident)
    points = [_point(p, "room %r polygon_mm[%d]" % (ident, i))
              for i, p in enumerate(polygon)]
    # A repeated closing point is how half the world writes a polygon; drop it
    # rather than build a zero-length edge out of it.
    if len(points) > 3 and _close(points[0], points[-1]):
        points = points[:-1]
    if len(points) < 3:
        raise ForgeError("Room %r collapses to fewer than three distinct "
                         "corners once its repeated closing point is dropped."
                         % ident)
    area = abs(_signed_area(points))
    if area < 1.0:  # one square millimetre
        raise ForgeError("Room %r has a polygon with no area (%g mm^2) — its "
                         "corners are collinear or coincident, so there is "
                         "nothing to lay a floor on." % (ident, area))
    if _signed_area(points) < 0.0:
        points = list(reversed(points))
    label = entry.get("label")
    if label is not None and not isinstance(label, str):
        raise ForgeError("Room %r has a non-string 'label' (%r)." % (ident, label))
    thickness = _num(entry.get("floor_mm", defaults["floor_mm"]),
                     "room %r floor_mm" % ident, minimum=0.0)
    if thickness <= 0.0:
        raise ForgeError("Room %r would get a floor slab of zero thickness; "
                         "give it a 'floor_mm' or pass floor:false." % ident)
    resolved = {
        "kind": "room",
        "id": ident,
        "label": label or "",
        "polygon_mm": [_round_all(p, 3) for p in points],
        "thickness_mm": _round(thickness, 3),
        "area_mm2": _round(area, 2),
    }
    if not floors:
        return None, resolved
    return resolved, resolved


def _resolve_opening(raw, index, wall_id, length, wall_height, defaults, warnings):
    if not isinstance(raw, dict):
        raise ForgeError("wall %r openings[%d] must be an object, got %s."
                         % (wall_id, index, type(raw).__name__))
    ident = _entry_id(raw, "wall %r openings" % wall_id, index)

    kind = raw.get("kind", "door")
    if not isinstance(kind, str) or kind.strip().lower() not in OPENING_KINDS:
        raise ForgeError(
            "Opening %r on wall %r has kind %r, which Forge does not know. An "
            "opening is a door, a window or a gap.%s"
            % (ident, wall_id, kind, _hint(kind, OPENING_KINDS)))
    kind = kind.strip().lower()

    if kind == "door":
        width = _num(raw.get("width_mm", defaults["door_w_mm"]),
                     "opening %r width_mm" % ident, minimum=0.0)
        height = _num(raw.get("height_mm", defaults["door_h_mm"]),
                      "opening %r height_mm" % ident, minimum=0.0)
        sill = 0.0
    elif kind == "window":
        width = _num(raw.get("width_mm", defaults["window_w_mm"]),
                     "opening %r width_mm" % ident, minimum=0.0)
        height = _num(raw.get("height_mm", defaults["window_h_mm"]),
                      "opening %r height_mm" % ident, minimum=0.0)
        sill = _num(raw.get("sill_mm", defaults["sill_mm"]),
                    "opening %r sill_mm" % ident, minimum=0.0)
    else:  # gap — a doorway with nothing in it, full height unless told otherwise
        width = _num(raw.get("width_mm", defaults["door_w_mm"]),
                     "opening %r width_mm" % ident, minimum=0.0)
        height = _num(raw.get("height_mm", wall_height),
                      "opening %r height_mm" % ident, minimum=0.0)
        sill = 0.0
    if "sill_mm" in raw and kind != "window":
        sill = _num(raw["sill_mm"], "opening %r sill_mm" % ident, minimum=0.0)

    if width <= 0.0:
        raise ForgeError("Opening %r on wall %r has no width. An opening with "
                         "zero width is not an opening." % (ident, wall_id))
    if height <= 0.0:
        raise ForgeError("Opening %r on wall %r has no height." % (ident, wall_id))

    # THE refusal the brief names: an opening wider than the wall it is in.
    if width > length + EPS_MM:
        raise ForgeError(
            "Opening %r is %g mm wide but wall %r is only %g mm long, so it "
            "cannot be cut into it. Widen the wall or narrow the opening."
            % (ident, width, wall_id, length))

    has_at = "at_mm" in raw and raw["at_mm"] is not None
    has_start = "start_mm" in raw and raw["start_mm"] is not None
    if has_at and has_start:
        raise ForgeError(
            "Opening %r gives both 'at_mm' (the CENTRE along the wall) and "
            "'start_mm' (the near edge). Give one; they disagree by half the "
            "opening's width." % ident)
    if has_start:
        start = _num(raw["start_mm"], "opening %r start_mm" % ident)
    elif has_at:
        start = _num(raw["at_mm"], "opening %r at_mm" % ident) - width / 2.0
    else:
        start = length / 2.0 - width / 2.0  # centred, which is what a sketch means
        warnings.append("Opening %r on wall %r gave no position, so it was "
                        "centred on the wall." % (ident, wall_id))
    end = start + width

    if start < -EPS_MM or end > length + EPS_MM:
        raise ForgeError(
            "Opening %r runs from %g mm to %g mm along wall %r, which is %g mm "
            "long — it hangs off the end. 'at_mm' is the opening's CENTRE "
            "measured from the wall's 'from_mm' end."
            % (ident, start, end, wall_id, length))
    start = min(max(start, 0.0), length)
    end = min(max(end, 0.0), length)

    top = sill + height
    if top > wall_height + EPS_MM:
        warnings.append(
            "Opening %r reaches %g mm but wall %r is only %g mm high, so it was "
            "clamped to full height and gets no header."
            % (ident, top, wall_id, wall_height))
        top = wall_height
    if sill > wall_height - EPS_MM:
        raise ForgeError("Opening %r sits at %g mm on a wall only %g mm high — "
                         "its sill is above the ceiling." % (ident, sill, wall_height))

    swing = raw.get("swing")
    if swing is not None:
        if not isinstance(swing, str) or swing.strip().lower() not in SWINGS:
            raise ForgeError("Opening %r has swing %r; a door swings 'in', "
                             "'out', or nothing at all.%s"
                             % (ident, swing, _hint(swing, SWINGS)))
        swing = swing.strip().lower()
        if kind != "door":
            warnings.append("Opening %r is a %s, not a door, so its 'swing' was "
                            "ignored." % (ident, kind))
            swing = None

    hinge = raw.get("hinge", "left")
    if not isinstance(hinge, str) or hinge.strip().lower() not in HINGES:
        raise ForgeError("Opening %r has hinge %r; a door hinges 'left' (the "
                         "end nearer the wall's from_mm) or 'right'.%s"
                         % (ident, hinge, _hint(hinge, HINGES)))
    hinge = hinge.strip().lower()

    return {
        "id": ident,
        "kind": kind,
        "start_mm": _round(start, 3),
        "end_mm": _round(end, 3),
        "width_mm": _round(end - start, 3),
        "bottom_mm": _round(sill, 3),
        "top_mm": _round(top, 3),
        "height_mm": _round(top - sill, 3),
        "swing": swing,
        "hinge": hinge,
    }


def _resolve_wall(entry, index, defaults, warnings):
    ident = _entry_id(entry, "walls", index)
    if "from_mm" not in entry or "to_mm" not in entry:
        raise ForgeError("Wall %r needs 'from_mm' and 'to_mm' — a centreline is "
                         "two points in millimetres." % ident)
    start = _point(entry["from_mm"], "wall %r from_mm" % ident)
    end = _point(entry["to_mm"], "wall %r to_mm" % ident)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1.0:
        raise ForgeError("Wall %r starts and ends in the same place (%g mm "
                         "long), so there is no centreline to build along."
                         % (ident, length))

    thickness = _num(entry.get("thickness_mm", defaults["wall_mm"]),
                     "wall %r thickness_mm" % ident, minimum=0.0)
    height = _num(entry.get("height_mm", defaults["ceiling_mm"]),
                  "wall %r height_mm" % ident, minimum=0.0)
    if thickness <= 0.0:
        raise ForgeError("Wall %r has zero thickness." % ident)
    if height <= 0.0:
        raise ForgeError("Wall %r has zero height." % ident)

    raw_openings = entry.get("openings") or []
    if not isinstance(raw_openings, (list, tuple)):
        raise ForgeError("Wall %r has an 'openings' that is not a list (%s)."
                         % (ident, type(raw_openings).__name__))
    openings = [_resolve_opening(raw, i, ident, length, height, defaults, warnings)
                for i, raw in enumerate(raw_openings)]
    openings.sort(key=lambda o: o["start_mm"])
    for previous, following in zip(openings, openings[1:]):
        if following["start_mm"] < previous["end_mm"] - EPS_MM:
            raise ForgeError(
                "Openings %r and %r overlap on wall %r (%g..%g mm and "
                "%g..%g mm). Two holes in the same hole is one hole; say so."
                % (previous["id"], following["id"], ident,
                   previous["start_mm"], previous["end_mm"],
                   following["start_mm"], following["end_mm"]))

    return {
        "kind": "wall",
        "id": ident,
        "from_mm": _round_all(start, 3),
        "to_mm": _round_all(end, 3),
        "length_mm": _round(length, 3),
        "angle_deg": _round(math.degrees(math.atan2(dy, dx)), 6),
        "thickness_mm": _round(thickness, 3),
        "height_mm": _round(height, 3),
        "openings": openings,
    }


def _resolve_label(entry, index, defaults, warnings):
    ident = _entry_id(entry, "labels", index)
    footprint = entry.get("footprint_mm")
    if footprint is None:
        raise ForgeError(
            "Label %r has no 'footprint_mm'. A label becomes a box, and a box "
            "needs [x, y, w, d] in millimetres — where it sits and how big it "
            "is. Give it the real size of the thing (a washer is about "
            "600 x 600 mm) and Forge will stand a placeholder there." % ident)
    if not isinstance(footprint, (list, tuple)) or len(footprint) != 4:
        raise ForgeError("Label %r has a 'footprint_mm' of %r; it must be "
                         "[x, y, w, d] in millimetres." % (ident, footprint))
    x = _num(footprint[0], "label %r footprint_mm[0] (x)" % ident)
    y = _num(footprint[1], "label %r footprint_mm[1] (y)" % ident)
    width = _num(footprint[2], "label %r footprint_mm[2] (w)" % ident, minimum=0.0)
    depth = _num(footprint[3], "label %r footprint_mm[3] (d)" % ident, minimum=0.0)
    if width <= 0.0 or depth <= 0.0:
        raise ForgeError("Label %r has a footprint of %g x %g mm — a placeholder "
                         "with no floor area is invisible." % (ident, width, depth))

    height = _num(entry.get("height_mm", defaults["label_h_mm"]),
                  "label %r height_mm" % ident, minimum=0.0)
    if height <= 0.0:
        raise ForgeError("Label %r has zero height." % ident)

    rotation = _num(entry.get("rotation_deg", 0.0), "label %r rotation_deg" % ident)

    anchor = entry.get("anchor", defaults["label_anchor"])
    if not isinstance(anchor, str) or anchor.strip().lower() not in ANCHORS:
        raise ForgeError("Label %r has anchor %r; footprint_mm's [x, y] is "
                         "either the 'center' of the box (the default) or its "
                         "'corner'.%s" % (ident, anchor, _hint(anchor, ANCHORS)))
    anchor = anchor.strip().lower()
    if anchor == "corner":
        x += width / 2.0
        y += depth / 2.0

    text = entry.get("label")
    if text is not None and not isinstance(text, str):
        raise ForgeError("Label %r has a non-string 'label' (%r)." % (ident, text))
    source = entry.get("source")
    if source is not None and not isinstance(source, str):
        raise ForgeError("Label %r has a non-string 'source' (%r)." % (ident, source))

    return {
        "kind": "label",
        "id": ident,
        "label": text or "",
        "centre_mm": [_round(x, 3), _round(y, 3)],
        "size_mm": [_round(width, 3), _round(depth, 3)],
        "height_mm": _round(height, 3),
        "rotation_deg": _round(rotation, 6),
        "anchor": anchor,
        "source": source or "user",
    }


def resolve_plan(plan, floors=True):
    """The whole plan, resolved to millimetres, before anything is built.

    Returns ``{"version", "units", "defaults", "entries", "rooms", "walls",
    "labels", "openings", "mechanisms", "notes", "warnings"}``.  Raises
    :class:`ForgeError` — in sentences — for anything that cannot be built.

    Parsed in full first, on purpose: a plan that is going to be refused should
    be refused *before* half a level exists in the scene.  It is the same rule
    ``animate_object`` and ``fit_to_silhouette`` follow.
    """
    if not isinstance(plan, dict):
        raise ForgeError(
            "'plan' must be a floorplan.json object — {\"version\": 1, "
            "\"units\": \"mm\", \"defaults\": {...}, \"rooms\": [...], "
            "\"walls\": [...], \"labels\": [...]} — got %s."
            % type(plan).__name__)

    warnings = []
    notes = []

    version = plan.get("version", 1)
    if version not in (1, None):
        warnings.append("This plan says version %r; Forge builds version 1 and "
                        "read it as one anyway." % (version,))

    units = plan.get("units", "mm")
    if not isinstance(units, str) or units.strip().lower() not in ("mm", "millimetre",
                                                                  "millimeter"):
        raise ForgeError(
            "This plan is in %r. Forge's floor plans are millimetres end to end "
            "(\"units\": \"mm\") — convert the numbers rather than the label, "
            "because every default in the schema is a millimetre."
            % (units,))

    defaults = _resolve_defaults(plan, warnings)

    sections = {}
    for key in ("rooms", "walls", "labels"):
        value = plan.get(key) or []
        if not isinstance(value, (list, tuple)):
            raise ForgeError("'%s' must be a list, got %s."
                             % (key, type(value).__name__))
        sections[key] = list(value)

    if not sections["walls"] and not sections["labels"] and not sections["rooms"]:
        raise ForgeError("This plan has no rooms, no walls and no labels, so "
                         "there is nothing to build. A floor plan needs at "
                         "least one of the three.")

    rooms = []
    room_specs = []
    for index, entry in enumerate(sections["rooms"]):
        built, spec = _resolve_room(entry, index, defaults, floors, warnings)
        room_specs.append(spec)
        if built is not None:
            rooms.append(built)
    walls = [_resolve_wall(entry, index, defaults, warnings)
             for index, entry in enumerate(sections["walls"])]
    labels = [_resolve_label(entry, index, defaults, warnings)
              for index, entry in enumerate(sections["labels"])]

    if sections["rooms"] and not floors:
        notes.append("floor:false, so the %d room%s in this plan got no slab "
                     "(and any slab a previous call built was removed)."
                     % (len(sections["rooms"]),
                        "" if len(sections["rooms"]) == 1 else "s"))

    # --- ids are unique across EVERYTHING, because ids name objects ----------
    seen = {}
    for spec in room_specs:
        seen.setdefault(spec["id"], []).append("room")
    for wall in walls:
        seen.setdefault(wall["id"], []).append("wall")
        for opening in wall["openings"]:
            seen.setdefault(opening["id"], []).append("opening on wall %r" % wall["id"])
    for label in labels:
        seen.setdefault(label["id"], []).append("label")
    for ident, owners in seen.items():
        if len(owners) > 1:
            raise ForgeError(
                "The id %r is used %d times in this plan (%s). Every id names "
                "one object (%s%s), and it is what lets the next call edit that "
                "one thing instead of rebuilding the level — so ids have to be "
                "unique across rooms, walls, openings and labels."
                % (ident, len(owners), ", ".join(owners), PREFIX, ident))

    entries = rooms + walls + labels
    mechanisms = []
    for wall in walls:
        mechanisms.extend(_mechanisms_for_wall(wall))

    opening_count = sum(len(wall["openings"]) for wall in walls)
    return {
        "version": 1,
        "units": "mm",
        "defaults": defaults,
        "entries": entries,
        "rooms": rooms,
        "walls": walls,
        "labels": labels,
        "opening_count": opening_count,
        "mechanisms": mechanisms,
        "notes": notes,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# mechanism records (Phase 17's data shape; nothing is rigged here)
# ---------------------------------------------------------------------------

def _mechanisms_for_wall(wall):
    """A revolute record per swinging door — data for the report, no rigging.

    The hinge of a door is a vertical edge, so the axis is world +Z and the
    origin is the hinge end of the opening on the wall's centreline.  ``range_deg``
    is 90 per the contract; ``direction`` carries the sign, which is the product
    of which way it swings and which end it hangs from — a right-hung door
    swinging in turns the opposite way about +Z from a left-hung one.
    """
    records = []
    angle = math.radians(wall["angle_deg"])
    ux, uy = math.cos(angle), math.sin(angle)
    origin_x, origin_y = wall["from_mm"]
    for opening in wall["openings"]:
        if opening["kind"] != "door" or not opening["swing"]:
            continue
        along = (opening["start_mm"] if opening["hinge"] == "left"
                 else opening["end_mm"])
        direction = (1 if opening["swing"] == "in" else -1)
        if opening["hinge"] == "right":
            direction = -direction
        records.append({
            "id": opening["id"],
            "opening": opening["id"],
            "wall": wall["id"],
            "object": PREFIX + wall["id"],
            "joint_type": "revolute",
            "axis": [0.0, 0.0, 1.0],
            "origin_mm": [_round(origin_x + ux * along, 3),
                          _round(origin_y + uy * along, 3),
                          0.0],
            "range_deg": 90,
            "direction": direction,
            "swing": opening["swing"],
            "hinge": opening["hinge"],
            "width_mm": opening["width_mm"],
            "height_mm": opening["height_mm"],
            "actuated_by": "hand",
            "note": ("intended motion only — the opening is a hole in the "
                     "greybox and no leaf object was built or rigged"),
        })
    return records


# ---------------------------------------------------------------------------
# geometry — millimetres in, millimetres out; metres happen at the bpy boundary
# ---------------------------------------------------------------------------

def _close(a, b, tol=EPS_MM):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _signed_area(points):
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        total += x0 * y1 - x1 * y0
    return total / 2.0


def _box(verts, faces, x0, x1, y0, y1, z0, z1):
    """Append one axis-aligned box, wound so every normal points out."""
    base = len(verts)
    verts.extend([
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ])
    for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        faces.append(tuple(base + i for i in face))


def _triangulate(points):
    """Triangles over a CCW polygon — concave-safe, with a fan as the fallback.

    ``mathutils.geometry.tessellate_polygon`` handles the L-shaped room, which a
    fan does not; if it is ever unavailable or returns nothing usable, a fan is
    still right for the convex rooms that are most of them, and being slightly
    wrong about a concave slab beats refusing to build the level.
    """
    count = len(points)
    try:
        from mathutils import Vector
        from mathutils.geometry import tessellate_polygon

        raw = tessellate_polygon([[Vector((x, y, 0.0)) for x, y in points]])
        triangles = []
        for tri in raw:
            if len(tri) != 3 or len(set(tri)) != 3:
                continue
            if any(i < 0 or i >= count for i in tri):
                continue
            a, b, c = (points[i] for i in tri)
            if _signed_area([a, b, c]) < 0.0:  # keep every cap wound the same way
                tri = (tri[0], tri[2], tri[1])
            triangles.append(tuple(tri))
        if triangles:
            return triangles
    except Exception:  # noqa: BLE001 - geometry helpers may refuse odd polygons
        pass
    return [(0, i, i + 1) for i in range(1, count - 1)]


def _prism(verts, faces, points, z0, z1):
    """Append a vertical prism over a CCW polygon: two caps and a side ring."""
    base = len(verts)
    count = len(points)
    for x, y in points:
        verts.append((x, y, z0))
    for x, y in points:
        verts.append((x, y, z1))
    triangles = _triangulate(points)
    for a, b, c in triangles:
        faces.append((base + count + a, base + count + b, base + count + c))
        faces.append((base + c, base + b, base + a))
    for i in range(count):
        j = (i + 1) % count
        faces.append((base + i, base + j, base + count + j, base + count + i))


def _wall_geometry(wall):
    """A wall's mesh in its own frame: x along the centreline, y across, z up.

    The openings are cut by **construction**, not by a boolean: the wall is the
    set of solid pieces left over — a full-height pier between openings, a sill
    below a window, a header above anything that does not reach the ceiling.
    Two reasons over a Boolean modifier: it is exact (no coplanar-face lottery,
    no solver to time out) and it is fast enough that rebuilding one wall on
    every keystroke of the plan is free.

    The pieces meet face to face where a header sits between two piers, which
    makes the wall a greybox rather than a printable solid.  Said out loud in
    the report rather than quietly pretended away.
    """
    length = wall["length_mm"]
    height = wall["height_mm"]
    half = wall["thickness_mm"] / 2.0
    verts, faces = [], []
    pieces = 0

    def box(x0, x1, z0, z1):
        if x1 - x0 <= EPS_MM or z1 - z0 <= EPS_MM:
            return 0
        _box(verts, faces, x0, x1, -half, half, z0, z1)
        return 1

    cursor = 0.0
    for opening in wall["openings"]:
        start, end = opening["start_mm"], opening["end_mm"]
        if start > cursor + EPS_MM:
            pieces += box(cursor, start, 0.0, height)
        pieces += box(start, end, 0.0, opening["bottom_mm"])   # sill
        pieces += box(start, end, opening["top_mm"], height)   # header
        cursor = max(cursor, end)
    if cursor < length - EPS_MM:
        pieces += box(cursor, length, 0.0, height)

    if not faces:
        # Every millimetre of the wall is opening. Legal, and worth a shape
        # rather than an empty mesh nobody can select.
        _box(verts, faces, 0.0, length, -half, half, 0.0, EPS_MM)
        pieces = 1

    location = (wall["from_mm"][0], wall["from_mm"][1], 0.0)
    return verts, faces, location, math.radians(wall["angle_deg"]), pieces


def _room_geometry(room):
    """A floor slab hanging BELOW z = 0, so the walls stand on top of it."""
    points = [tuple(p) for p in room["polygon_mm"]]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    centre = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    local = [(x - centre[0], y - centre[1]) for x, y in points]
    verts, faces = [], []
    _prism(verts, faces, local, -room["thickness_mm"], 0.0)
    return verts, faces, (centre[0], centre[1], 0.0), 0.0


def _label_geometry(label):
    """A placeholder box: footprint on the floor, height up, spun about Z."""
    width, depth = label["size_mm"]
    verts, faces = [], []
    _box(verts, faces, -width / 2.0, width / 2.0, -depth / 2.0, depth / 2.0,
         0.0, label["height_mm"])
    location = (label["centre_mm"][0], label["centre_mm"][1], 0.0)
    return verts, faces, location, math.radians(label["rotation_deg"])


def geometry_for(entry):
    """``(verts_mm, faces, location_mm, rotation_z_rad, pieces)`` for one entry."""
    if entry["kind"] == "wall":
        return _wall_geometry(entry)
    if entry["kind"] == "room":
        verts, faces, location, rotation = _room_geometry(entry)
        return verts, faces, location, rotation, 1
    verts, faces, location, rotation = _label_geometry(entry)
    return verts, faces, location, rotation, 1


# ---------------------------------------------------------------------------
# the fingerprint and the "did the artist touch this?" heuristic
# ---------------------------------------------------------------------------

def fingerprint(entry):
    """A content hash of one entry's fully resolved values.

    Canonical JSON (sorted keys, no whitespace) so that reordering the plan's
    keys is not a change, plus ``BUILD_VERSION`` so that changing this module's
    geometry is.
    """
    payload = {"_build": BUILD_VERSION, "entry": entry}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _local_dims_mm(obj):
    """Millimetre dimensions from the object's own mesh and scale.

    Deliberately computed rather than read off ``obj.dimensions``: that number
    comes from the evaluated depsgraph, which in ``--background`` is exactly the
    thing that is not guaranteed to be current, and a stale reading here would
    call an untouched object edited.
    """
    mesh = getattr(obj, "data", None)
    vertices = getattr(mesh, "vertices", None)
    if not vertices:
        return [0.0, 0.0, 0.0]
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    for vertex in vertices:
        for axis in range(3):
            value = vertex.co[axis]
            low[axis] = min(low[axis], value)
            high[axis] = max(high[axis], value)
    scale = obj.scale
    return [_round((high[a] - low[a]) * abs(scale[a]) * 1000.0, 3) for a in range(3)]


def keep_reason(obj):
    """Why this object must not be overwritten — or ``None`` if it may be.

    The order is the order of certainty: an explicit marker, then "Forge never
    built this", then the two cheap mesh heuristics.
    """
    if obj.get("forge_fp_keep"):
        return "forge_fp_keep is set on it, which is an explicit hands-off"
    if obj.type != "MESH":
        return ("it is an object of type %s now, not the mesh Forge builds"
                % obj.type)
    stored = obj.get("forge_fp_hash")
    if not stored:
        return ("it carries no Forge fingerprint, so Forge did not build it and "
                "has no business overwriting it")
    expected_verts = obj.get("forge_fp_verts")
    actual_verts = len(obj.data.vertices)
    if expected_verts is not None and int(expected_verts) != actual_verts:
        return ("its mesh has %d vertices and Forge built it with %d, so it has "
                "been edited" % (actual_verts, int(expected_verts)))
    expected_dims = obj.get("forge_fp_dims_mm")
    if expected_dims is not None:
        expected = [float(v) for v in expected_dims]
        actual = _local_dims_mm(obj)
        if len(expected) == 3 and any(abs(a - b) > KEEP_TOL_MM
                                      for a, b in zip(actual, expected)):
            return ("it measures %s mm and Forge built it %s mm, so it has been "
                    "scaled or reshaped"
                    % (" x ".join("%g" % v for v in actual),
                       " x ".join("%g" % v for v in expected)))
    return None


# ---------------------------------------------------------------------------
# scene plumbing
# ---------------------------------------------------------------------------

def _material(kind):
    """The flat colour for a kind, made once and reused by name."""
    name, colour = MATERIALS[kind]
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        tree = getattr(material, "node_tree", None)
        if tree is not None:
            for node in tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    base = node.inputs.get("Base Color")
                    if base is not None:
                        base.default_value = colour
                    rough = node.inputs.get("Roughness")
                    if rough is not None:
                        rough.default_value = 0.85
    # Solid-shading viewport colour too: a greybox is looked at in Workbench
    # far more often than it is rendered.
    try:
        material.diffuse_color = colour
    except (AttributeError, TypeError, ValueError):  # pragma: no cover
        pass
    return material


def _assign_material(obj, kind):
    material = _material(KIND_MATERIAL[kind])
    slots = obj.data.materials
    if not slots:
        slots.append(material)
    elif slots[0] is not material:
        slots[0] = material
    return material.name


def _stamp(obj, entry, digest, collection_name):
    """Write the fingerprint and everything the next call diffs against."""
    obj["forge_fp_id"] = entry["id"]
    obj["forge_fp_kind"] = entry["kind"]
    obj["forge_fp_hash"] = digest
    obj["forge_fp_verts"] = len(obj.data.vertices)
    obj["forge_fp_dims_mm"] = _local_dims_mm(obj)
    obj["forge_fp_collection"] = collection_name
    if entry.get("label"):
        obj["forge_fp_label"] = entry["label"]


def _build_one(entry, digest, collection, collection_name):
    """Create or rebuild one entry's object. Returns ``(obj, created, pieces)``."""
    name = PREFIX + entry["id"]
    existing = bpy.data.objects.get(name)
    if existing is not None and existing.type != "MESH":
        raise ForgeError(
            "There is already a %s called %r in this file, and Forge builds "
            "meshes. Rename it, or give the plan entry a different id."
            % (existing.type.lower(), name))

    verts, faces, location, rotation, pieces = geometry_for(entry)
    obj, _ = common.build_mesh_object(name, verts, faces, replace=True,
                                      collection=collection_name, scale=MM_TO_M)
    created = existing is None
    if created and obj.name not in collection.objects:
        # build_mesh_object links new objects into the named collection already;
        # this is the belt for the braces, and a no-op in the normal case.
        try:
            collection.objects.link(obj)
        except RuntimeError:
            pass
    obj.location = (location[0] * MM_TO_M, location[1] * MM_TO_M,
                    location[2] * MM_TO_M)
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (0.0, 0.0, rotation)
    obj.scale = (1.0, 1.0, 1.0)
    _assign_material(obj, entry["kind"])
    _stamp(obj, entry, digest, collection_name)
    return obj, created, pieces


def _delete(obj):
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError):  # pragma: no cover
            pass


def _owned_objects(collection):
    """``{id: object}`` for every ``FP:``-prefixed object in the collection.

    ``all_objects`` rather than ``objects`` so a level the artist has tidied into
    sub-collections is still found — and still the *only* thing that can be
    deleted, because the prefix and the collection both have to agree.
    """
    found = {}
    for obj in collection.all_objects:
        if obj.name.startswith(PREFIX):
            found[obj.name[len(PREFIX):]] = obj
    return found


def _bounds_mm(objects):
    """World bounds over the built level, in millimetres."""
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    seen = False
    for obj in objects:
        matrix = obj.matrix_world
        for corner in obj.bound_box:
            world = matrix @ Vector(corner)
            for axis in range(3):
                value = world[axis] * 1000.0
                low[axis] = min(low[axis], value)
                high[axis] = max(high[axis], value)
            seen = True
    if not seen:
        zero = [0.0, 0.0, 0.0]
        return {"min": zero, "max": list(zero), "size": list(zero)}, list(zero)
    low = _round_all(low, 3)
    high = _round_all(high, 3)
    size = _round_all([high[a] - low[a] for a in range(3)], 3)
    return {"min": low, "max": high, "size": size}, size


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def _mode(params):
    raw = params.get("mode", "update")
    if raw is None:
        raw = "update"
    if not isinstance(raw, str):
        raise ForgeError("'mode' must be a string, got %s." % type(raw).__name__)
    mode = raw.strip().lower()
    if mode not in MODES:
        raise ForgeError(
            "mode must be 'update' (the default: diff this plan against what is "
            "already in the scene and touch only what changed) or 'rebuild' "
            "(throw the greybox away and build it again), got %r.%s"
            % (raw, _hint(raw, MODES)))
    return mode


def _floor_flag(params):
    raw = params.get("floor", True)
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    raise ForgeError("'floor' must be true or false (whether each room gets a "
                     "slab), got %r." % (raw,))


@command("build_floorplan")
def cmd_build_floorplan(params):
    """Materialise a floorplan.json into scene objects, incrementally.

    ``{"plan": <the floorplan.json object>, "collection"?: "Floorplan",
    "mode"?: "update"|"rebuild", "floor"?: true}``

    The plan arrives as data — resolved values, millimetres, ids on everything.
    This command does not read images, does not talk to the geometry service and
    does not guess: it builds exactly what the plan says, and on the second call
    it builds exactly what *changed*.
    """
    started = time.monotonic()

    if "plan" not in params or params.get("plan") is None:
        raise ForgeError(
            "This command builds a floor plan, so it needs one: pass the "
            "floorplan.json object as 'plan'. The shape is {\"version\": 1, "
            "\"units\": \"mm\", \"defaults\": {...}, \"rooms\": [...], "
            "\"walls\": [...], \"labels\": [...]}, with a stable id on every "
            "entry.")

    collection_name = get_str(params, "collection", DEFAULT_COLLECTION)
    mode = _mode(params)
    floors = _floor_flag(params)

    # Everything above this line is parsing. Nothing below it fails halfway:
    # the plan is fully resolved, and only then does the scene change.
    spec = resolve_plan(params["plan"], floors=floors)
    notes = list(spec["notes"])
    warnings = list(spec["warnings"])

    # One implementation of "find or make the collection", shared with
    # load_mesh: two would eventually disagree about where a level lives.
    collection = common._resolve_collection(collection_name)
    collection_name = collection.name
    existing = _owned_objects(collection)
    wanted = {entry["id"] for entry in spec["entries"]}

    built, updated, unchanged, deleted, kept = [], [], [], [], []
    kept_ids = set()
    pieces_total = 0

    if mode == "rebuild":
        for ident, obj in sorted(existing.items()):
            if obj.get("forge_fp_keep"):
                kept.append({"id": ident, "object": obj.name,
                             "why": "forge_fp_keep is set on it, which outranks "
                                    "mode:rebuild — an explicit hands-off is "
                                    "explicit in every mode"})
                kept_ids.add(ident)
                warnings.append("Kept %r even in mode:rebuild: forge_fp_keep is "
                                "set on it." % obj.name)
                continue
            _delete(obj)
            # An id the plan still has is about to come back as `built`; only an
            # id that is really gone belongs in `deleted`.
            if ident not in wanted:
                deleted.append(ident)
        existing = _owned_objects(collection)
        notes.append("mode:rebuild — the greybox was thrown away and built "
                     "again, so any hand edits to it are gone (that is what the "
                     "mode is for). forge_fp_keep still held.")

    for entry in spec["entries"]:
        ident = entry["id"]
        if ident in kept_ids:
            continue
        digest = fingerprint(entry)
        obj = existing.get(ident)

        if obj is None:
            obj = bpy.data.objects.get(PREFIX + ident)
            if obj is not None and obj.name not in collection.all_objects:
                # Same name, somewhere else in the file: rebuilding it would
                # reach outside the collection this call was pointed at.
                reason = keep_reason(obj)
                if reason is None:
                    reason = ("it lives outside the %r collection, and this call "
                              "only owns what is inside it" % collection_name)
                kept.append({"id": ident, "object": obj.name, "why": reason})
                kept_ids.add(ident)
                warnings.append("Kept %r: %s." % (obj.name, reason))
                continue

        if obj is not None:
            if obj.get("forge_fp_hash") == digest and obj.type == "MESH":
                # THE point of the phase: an unchanged entry is not touched at
                # all. No mesh read, no material, no transform, no property.
                unchanged.append(ident)
                continue
            reason = keep_reason(obj)
            if reason is not None:
                kept.append({"id": ident, "object": obj.name, "why": reason})
                kept_ids.add(ident)
                warnings.append(
                    "Kept %r rather than rebuilding it: %s. The plan still "
                    "holds the slot's footprint; promote it or delete the "
                    "object by hand if you want the plan's version back."
                    % (obj.name, reason))
                continue

        obj, created, pieces = _build_one(entry, digest, collection, collection_name)
        pieces_total += pieces
        (built if created else updated).append(ident)

    # --- deletions: only FP: objects, only in this collection, only ids the
    #     plan no longer has, and never something the artist has been at.
    for ident, obj in sorted(_owned_objects(collection).items()):
        if ident in wanted or ident in kept_ids:
            continue
        reason = keep_reason(obj)
        if reason is not None:
            kept.append({"id": ident, "object": obj.name, "why": reason})
            warnings.append(
                "Kept %r rather than deleting it: %s. It is no longer in the "
                "plan, so nothing will update it — remove it by hand when you "
                "are done with it." % (obj.name, reason))
            continue
        _delete(obj)
        deleted.append(ident)

    common.refresh_view_layer()

    remaining = _owned_objects(collection)
    bounds, dimensions = _bounds_mm(remaining.values())

    if spec["walls"]:
        notes.append("Walls are built as solid pieces — a full-height pier "
                     "between openings, a sill under a window, a header over "
                     "anything that does not reach the ceiling — which meet "
                     "face to face where they touch. Greybox geometry, not a "
                     "printable solid.")
    if spec["mechanisms"]:
        notes.append("%d door%s carr%s a revolute mechanism record (axis +Z "
                     "through the hinge edge, range_deg 90). They are data for "
                     "the report and for a later export; no leaf was built and "
                     "nothing is rigged."
                     % (len(spec["mechanisms"]),
                        "" if len(spec["mechanisms"]) == 1 else "s",
                        "ies" if len(spec["mechanisms"]) == 1 else "y"))
    if not built and not updated and not deleted:
        notes.append("Nothing changed: every entry in this plan already matches "
                     "what is in the scene, so not one object was touched.")

    return {
        "collection": collection_name,
        "built": built,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged,
        "kept": kept,
        "objects": len(remaining),
        "object_names": sorted(obj.name for obj in remaining.values()),
        "walls": len(spec["walls"]),
        "openings": spec["opening_count"],
        "fixtures": len(spec["labels"]),
        "floors": len(spec["rooms"]),
        "rooms": len(spec["rooms"]),
        "pieces": pieces_total,
        "mechanisms": spec["mechanisms"],
        "bounds_mm": bounds,
        "dimensions_mm": dimensions,
        "mode": mode,
        "floor": floors,
        "defaults_mm": spec["defaults"],
        "plan_version": spec["version"],
        "units": spec["units"],
        "honesty": HONESTY,
        "notes": list(dict.fromkeys(notes)),
        "warnings": list(dict.fromkeys(warnings)),
        "seconds": round(time.monotonic() - started, 3),
    }
