"""``floorplan_extract`` -- the drawing, read by arithmetic instead of by eye.

This module exists because of one real failure.  Given a drawn floor plan, the
assistant looked at the bitmap and *typed out* ``floorplan.json`` coordinates
from what it thought it saw.  The level came out with the wrong footprint, the
rooms in the wrong places, and a **diagonal wall that exists nowhere in the
drawing**.  Every one of those is the same bug: a language model estimating
geometry off pixels.

``docs/automation-thesis.md`` says what to do instead -- deterministic geometry
first, LLM judgment second, over *computed features* and never over raw pixels
-- and this is that layer for Phase 19.  The division of labour is absolute:

* **Geometry comes from here.**  Where the rooms are, how big they are, which
  rooms share a wall, where the doors sit along it.  All of it is colour
  classification, connected components and a boundary walk on the pixel
  lattice.  Nothing in this file has an opinion.
* **Names come from the model.**  :func:`extract_floorplan` hands back a crop
  box per region so the assistant can *look at that part of the drawing* and
  read the word written in it.  It renames the room's **label**; the **id** is
  fixed here and is never renamed, because an id names a Blender object and a
  changed id deletes the artist's work (Phase 19's whole incremental law).

**A diagonal is impossible by construction.**  The boundary of a region is
traced on the pixel lattice as unit steps -- every one of them axis-aligned --
and then snapped to a detected grid pitch, which moves coordinates but cannot
tilt an edge.  Walls are derived from those edges.  There is no code path in
this module that can emit a wall at 17 degrees, which is the point.

What comes out
--------------
``extract_floorplan(image_path, legend=None, mm_per_px=None, grid_px=None)``
returns four things:

``plan``
    A ``floorplan.json`` object that **passes**
    :func:`service.floorplan.validate_plan`.  Rooms are ``room-r1`` ..
    ``room-rN`` in reading order (top to bottom, then left to right), walls are
    ``wall-r1-r2`` (the wall those two rooms share) or ``wall-r1-out-n`` (the
    north side of r1, facing nothing), openings are ``open-<wall>-<n>``.  Every
    id is a pure function of the image, so re-extracting the same drawing
    produces the same ids and therefore rebuilds nothing.
``regions``
    One record per room-shaped blob, each with its pixel bbox and a **crop box
    to look at**, so the naming pass is "read the word in this rectangle"
    rather than "guess from the whole picture".
``mask``
    The drawn room fill rasterised on :func:`service.floorplan.plan_mask`'s own
    contract -- same keys, same "row 0 is the lowest y" rule -- so measuring the
    built level against the drawing is one :func:`service.floorplan.mask_iou`
    call and not a coordinate argument.
``report``
    Everything the model is allowed to reason over: the colours found and what
    each was taken for, the grid pitch, the wall gap, per-region confidence,
    and a list of everything ambiguous.  Plus, when no scale was given, the ONE
    calibration question -- because a drawing has no size in it and exactly one
    measured length fixes every number downstream.

The drawing style this is built for
-----------------------------------
Flat axis-aligned colour blocks out of a design tool: grey room fills, white
background (and white halls), coloured strips for doors with a legend of
swatches in a corner, black text for labels.  Colours are exact and edges are
pixel-clean, so classification is a palette lookup rather than a segmentation
problem.  Text strokes are *noise* here -- thin dark marks inside a fill -- and
are absorbed back into the fill they sit in before anything is measured; the
words in them are the model's job, not this module's.

Pillow loads the file and numpy does everything after it.  Nothing here imports
build123d or bpy, nothing starts a process, and the only thing written to disk
is whatever the caller decides to save.
"""

from __future__ import annotations

import colorsys
import math
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from . import floorplan as fp
from .floorplan import FloorPlanError

# ==========================================================================
# Knobs, each with the reason it is the number it is
# ==========================================================================

#: Pillow is the only thing in Forge that reads a PNG, and it is imported
#: lazily so that a service without it still serves every other route.
_NO_PILLOW = (
    "Reading a drawing needs Pillow, and it is not installed in this "
    "environment ({detail}). Install it into the service venv "
    "(`pip install pillow`) -- it is the only image decoder Forge uses, and "
    "floorplan extraction is the only thing that needs one. Until then the "
    "only way to author a plan is to describe the rooms in words; do NOT type "
    "coordinates off a picture."
)

#: Per-channel distance at which a pixel still counts as one of the palette
#: colours.  A design tool writes exact colours, so this only has to swallow
#: PNG antialiasing along an edge, and a bigger number starts merging a light
#: grey room into a white background.
COLOR_TOLERANCE = 24

#: A colour has to cover this share of the image before it is a palette entry.
#: Below it, it is an antialiasing fringe or a JPEG artefact.  A legend swatch
#: in a 2000 px drawing is about 0.05%, so this is deliberately low.
MIN_COLOR_FRACTION = 0.0002

#: More distinct colours than this in one floor plan means the drawing is not
#: the flat-block kind this module reads, and the report says so.
MAX_COLORS = 24

#: ``max(r, g, b) - min(r, g, b)``.  Below the first number a colour is a grey
#: (a room fill or the background); above the second it is a door strip.  The
#: gap between them is where an "ambiguous colour" note comes from.
GREY_CHROMA_MAX = 40
STRIP_CHROMA_MIN = 60

#: Brighter than this is not text.  Text is near-black strokes, and it is noise
#: to this module -- the words in it are the model's job.
TEXT_VALUE_MAX = 110

#: How many rounds of "take the nearest classified neighbour" an unclassified
#: pixel gets.  Text strokes and antialiasing fringes are a few pixels across;
#: anything that survives this many rounds is a real unclassified area and is
#: left alone rather than invented into a room.
NOISE_ROUNDS = 6

#: How far a coordinate may be moved to land on the grid.  This is the whole
#: safety argument for grid snapping: whatever pitch is detected, no coordinate
#: moves further than this, so a wrong pitch costs three pixels and never a
#: wall in the wrong place.
SNAP_TOLERANCE_PX = 3.5

#: A pitch has to be bigger than twice the snap tolerance or it explains every
#: position trivially and snapping becomes a no-op with a confident number
#: attached to it.
GRID_MIN_PX = 10
GRID_MAX_PX = 400

#: A blob smaller than this share of the image is a legend swatch, a stray
#: mark, or a letter -- not a room.
MIN_REGION_FRACTION = 0.0012

#: Two facing edges have to overlap by at least this much before they are one
#: shared wall; below it they are two rooms that happen to line up at a corner.
MIN_SHARED_OVERLAP_PX = 6.0

#: Anything shorter than this is a jog in the outline, not a wall.
MIN_WALL_PX = 4.0

#: How far apart two regions may be and still be separated by a *wall* rather
#: than by open space.  Derived from the thinnest separation actually found in
#: the drawing (see :func:`_wall_gap_limit`); this is only the ceiling on that
#: search, so a hall never becomes a very thick wall.
MAX_WALL_GAP_FRACTION = 0.25

#: What a legend swatch is, geometrically: a coloured blob that touches no room
#: at all.  A door strip always lies on the boundary of one; a swatch floats on
#: the background next to its caption.  That single rule is what makes legend
#: detection deterministic instead of a guess about corners.
LEGEND_CLEARANCE_PX = 3

#: Roles a legend entry may name, and what each means in the plan schema.
#: ``where`` is what the geometry should agree with, and a disagreement is a
#: note in the report rather than an override -- the drawing wins.
OPENING_ROLES: Dict[str, Tuple[str, Optional[str]]] = {
    "house_door": ("door", "exterior"),
    "front_door": ("door", "exterior"),
    "exterior_door": ("door", "exterior"),
    "room_door": ("door", "interior"),
    "interior_door": ("door", "interior"),
    "door": ("door", None),
    "doorway": ("gap", None),
    "opening": ("gap", None),
    "gap": ("gap", None),
    "window": ("window", None),
}

#: With no legend given and no captions read (this module never reads text),
#: the hue of a strip is the only evidence there is.  These four bands are the
#: owner's own key -- blue house door, green open doorway, red room door -- and
#: every one of them is reported as an ASSUMPTION, by name, every time.
AUTO_ROLE_BY_HUE: Tuple[Tuple[float, float, str], ...] = (
    (0.0, 45.0, "room_door"),
    (45.0, 190.0, "doorway"),
    (190.0, 290.0, "house_door"),
    (290.0, 360.0, "room_door"),
)

#: Said out loud in every report, for the same reason the greybox one is.
HONESTY = (
    "This is a MEASUREMENT of the drawing, not an understanding of it. Every "
    "coordinate here was computed from pixels -- colour classes, connected "
    "components, a boundary walk, a grid snap -- and every one is rectilinear "
    "by construction. What it cannot know is what anything is CALLED: the room "
    "names come from you reading the crops, and the door kinds come from the "
    "colour key. Check both against the drawing before anything is built."
)

_EPS = 1e-9


# ==========================================================================
# Image in, palette out
# ==========================================================================


def _image_module():
    try:
        from PIL import Image  # noqa: PLC0415 -- lazy on purpose
    except Exception as exc:  # noqa: BLE001
        raise FloorPlanError(_NO_PILLOW.format(detail=exc)) from exc
    return Image


def _load_rgb(image_path: Any) -> Tuple[np.ndarray, str]:
    """The picture as an ``(H, W, 3)`` uint8 array, over white."""
    if image_path is None or not str(image_path).strip():
        raise FloorPlanError(
            "No image given. floorplan_extract reads a drawing file -- a PNG of "
            "the plan as it was drawn."
        )
    path = str(image_path)
    image_mod = _image_module()
    try:
        with image_mod.open(path) as handle:
            handle.load()
            frame = handle.convert("RGBA")
    except FileNotFoundError as exc:
        raise FloorPlanError(f"No image at {path}.") from exc
    except Exception as exc:  # noqa: BLE001 -- every decode failure is one answer
        raise FloorPlanError(
            f"Could not read {path} as an image ({exc}). Save the drawing as a "
            f"PNG and try again."
        ) from exc

    data = np.asarray(frame, dtype=np.uint8)
    if data.ndim != 3 or data.shape[2] != 4:
        raise FloorPlanError(f"{path} did not decode to colour pixels.")
    height, width = int(data.shape[0]), int(data.shape[1])
    if height < 8 or width < 8:
        raise FloorPlanError(
            f"{path} is {width} x {height} pixels. That is too small to be a "
            f"floor plan -- there is nothing in it to measure."
        )

    #: A transparent drawing is a drawing on white: that is what the artist saw
    #: in the tool that exported it, and compositing over black would turn the
    #: background into text.
    alpha = data[:, :, 3].astype(np.float32) / 255.0
    rgb = data[:, :, :3].astype(np.float32)
    composited = rgb * alpha[:, :, None] + 255.0 * (1.0 - alpha[:, :, None])
    return np.clip(np.rint(composited), 0, 255).astype(np.uint8), path


def _hex(color: Sequence[int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(int(color[0]), int(color[1]), int(color[2]))


def _parse_hex(value: Any) -> Optional[Tuple[int, int, int]]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _hue_deg(color: Sequence[int]) -> float:
    r, g, b = (float(c) / 255.0 for c in color[:3])
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def _palette(rgb: np.ndarray, tolerance: int,
             min_fraction: float) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    """``(class index per pixel, palette colours, pixel counts)``.

    Exact colours are counted first (a design tool writes exact colours), then
    every colour in the image is snapped to the nearest frequent one within
    *tolerance*.  Anything further away than that is left unclassified: it is an
    antialiasing fringe or a letter, and both are handled by
    :func:`_absorb_noise` rather than by widening the tolerance until a light
    grey room becomes the white background.
    """
    height, width = rgb.shape[0], rgb.shape[1]
    total = height * width
    codes = (rgb[:, :, 0].astype(np.uint32) << 16
             | rgb[:, :, 1].astype(np.uint32) << 8
             | rgb[:, :, 2].astype(np.uint32))
    uniq, inverse, counts = np.unique(codes.reshape(-1), return_inverse=True,
                                      return_counts=True)
    uniq_rgb = np.stack([(uniq >> 16) & 0xFF, (uniq >> 8) & 0xFF, uniq & 0xFF],
                        axis=1).astype(np.int16)

    order = np.argsort(-counts, kind="stable")
    floor_count = max(1, int(math.ceil(min_fraction * total)))
    seeds: List[int] = []
    for index in order.tolist():
        if counts[index] < floor_count:
            break
        candidate = uniq_rgb[index]
        if any(int(np.abs(candidate - uniq_rgb[s]).max()) <= tolerance for s in seeds):
            continue
        seeds.append(index)
        if len(seeds) >= MAX_COLORS:
            break
    if not seeds:
        seeds = [int(order[0])]

    palette = uniq_rgb[np.array(seeds, dtype=np.int64)]
    distance = np.abs(uniq_rgb[:, None, :] - palette[None, :, :]).max(axis=2)
    nearest = np.argmin(distance, axis=1)
    close = distance[np.arange(uniq_rgb.shape[0]), nearest] <= tolerance
    per_unique = np.where(close, nearest, -1).astype(np.int16)

    classes = per_unique[inverse].reshape(height, width)
    tally = [int((classes == index).sum()) for index in range(palette.shape[0])]
    return classes, palette.astype(np.int16), tally


def _classify_palette(palette: np.ndarray, tally: Sequence[int],
                      classes: np.ndarray) -> Tuple[List[str], List[str]]:
    """What each palette colour IS: background, room fill, door strip or text."""
    border = np.concatenate([
        classes[0, :].reshape(-1), classes[-1, :].reshape(-1),
        classes[:, 0].reshape(-1), classes[:, -1].reshape(-1),
    ])
    border = border[border >= 0]
    if border.size:
        background = int(np.bincount(border, minlength=palette.shape[0]).argmax())
    else:
        background = int(np.argmax(np.asarray(tally)))

    kinds: List[str] = []
    notes: List[str] = []
    for index in range(palette.shape[0]):
        color = palette[index]
        chroma = int(color.max() - color.min())
        value = int(color.max())
        if index == background:
            kinds.append("background")
            continue
        if value <= TEXT_VALUE_MAX and chroma <= GREY_CHROMA_MAX:
            kinds.append("text")
            continue
        if chroma >= STRIP_CHROMA_MIN:
            kinds.append("opening")
            continue
        if chroma > GREY_CHROMA_MAX:
            kinds.append("opening")
            notes.append(
                f"{_hex(color)} is only faintly coloured (chroma {chroma}); it was "
                f"read as a door strip rather than a room fill. If it is a room, "
                f"pass it in `legend` or redraw it as a grey."
            )
            continue
        kinds.append("room")
    return kinds, notes


def _absorb_noise(classes: np.ndarray, kinds: Sequence[str],
                  rounds: int) -> Tuple[np.ndarray, int]:
    """Text strokes and edge fringes take the class around them.

    A label written inside a room is a hole in that room's fill, and a hole is
    the difference between a rectangle and a rectangle with a bite out of it.
    So every text pixel and every unclassified pixel is given the class of its
    nearest classified neighbour, with room fills winning ties over openings and
    openings over the background -- the conservative direction, because a fill
    that grew by one pixel is snapped back to the grid and a fill that lost one
    is a wall in the wrong place.
    """
    work = classes.copy().astype(np.int16)
    for index, kind in enumerate(kinds):
        if kind == "text":
            work[work == index] = -1
    before = int((work < 0).sum())

    priority = np.array(
        [{"room": 0, "opening": 1, "background": 2}.get(kind, 3) for kind in kinds]
        + [9],
        dtype=np.int16,
    )
    big = np.int16(len(kinds))

    for _ in range(max(0, int(rounds))):
        unknown = work < 0
        if not unknown.any():
            break
        padded = np.where(work < 0, big, work)
        stack = np.full((4,) + work.shape, big, dtype=np.int16)
        stack[0, 1:, :] = padded[:-1, :]
        stack[1, :-1, :] = padded[1:, :]
        stack[2, :, 1:] = padded[:, :-1]
        stack[3, :, :-1] = padded[:, 1:]
        ranked = priority[stack]
        choice = np.argmin(ranked, axis=0)
        picked = np.take_along_axis(stack, choice[None, ...], axis=0)[0]
        take = unknown & (picked < big)
        if not take.any():
            break
        work[take] = picked[take]

    after = int((work < 0).sum())
    return work, before - after


# ==========================================================================
# Connected components -- numpy, 4-connectivity, no scipy
# ==========================================================================


def _label_components(mask: np.ndarray) -> Tuple[np.ndarray, int]:
    """Two-pass run-length labelling.  Deterministic, and about as fast as
    numpy gets without a compiled neighbourhood op."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parent: List[int] = [0]

    def find(node: int) -> int:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(left: int, right: int) -> int:
        a, b = find(left), find(right)
        if a == b:
            return a
        if a > b:
            a, b = b, a
        parent[b] = a
        return a

    flat = mask.astype(np.int8)
    for y in range(height):
        row = flat[y]
        if not row.any():
            continue
        marks = np.flatnonzero(np.diff(np.concatenate(([0], row, [0]))))
        above = labels[y - 1] if y > 0 else None
        for start, end in zip(marks[0::2].tolist(), marks[1::2].tolist()):
            label = 0
            if above is not None:
                segment = above[start:end]
                touching = np.unique(segment[segment > 0])
                if touching.size:
                    label = find(int(touching[0]))
                    for other in touching[1:].tolist():
                        label = union(label, int(other))
            if label == 0:
                label = len(parent)
                parent.append(label)
            labels[y, start:end] = label

    if len(parent) <= 1:
        return labels, 0
    roots = np.array([find(i) for i in range(len(parent))], dtype=np.int32)
    unique_roots = np.unique(roots[1:])
    remap = np.zeros(len(parent), dtype=np.int32)
    for new_label, root in enumerate(unique_roots.tolist(), start=1):
        remap[roots == root] = new_label
    remap[0] = 0
    return remap[labels], int(unique_roots.size)


# ==========================================================================
# Boundary walk -- where "no diagonals, ever" is enforced
# ==========================================================================

_STEP = {"R": (1, 0), "D": (0, 1), "L": (-1, 0), "U": (0, -1)}
_TURN_RIGHT = {"R": "D", "D": "L", "L": "U", "U": "R"}
_TURN_LEFT = {"R": "U", "U": "L", "L": "D", "D": "R"}


def _front(mask: np.ndarray, vx: int, vy: int, heading: str) -> Tuple[bool, bool]:
    """``(front-right, front-left)`` pixels at a lattice vertex."""
    height, width = mask.shape

    def at(x: int, y: int) -> bool:
        return bool(mask[y, x]) if 0 <= y < height and 0 <= x < width else False

    north_west = at(vx - 1, vy - 1)
    north_east = at(vx, vy - 1)
    south_west = at(vx - 1, vy)
    south_east = at(vx, vy)
    if heading == "R":
        return south_east, north_east
    if heading == "D":
        return south_west, south_east
    if heading == "L":
        return north_west, south_west
    return north_east, north_west


def _trace_outline(mask: np.ndarray, start_x: int, start_y: int) -> List[Tuple[float, float]]:
    """The outer outline of one blob, as lattice corners.

    Every step is one pixel along an axis; the walk keeps the blob on its right.
    Holes are not traced, which is exactly what should happen to a doorway
    number printed in the middle of a room: the outline goes round the room.
    """
    heading = "R"
    vx, vy = int(start_x), int(start_y)
    points: List[Tuple[float, float]] = []
    #: The walk ends when a (corner, direction-out-of-it) pair repeats, which is
    #: the only condition that is right for every shape: comparing positions
    #: alone stops early at a pinch point, and comparing the arrival direction
    #: never matches at the start, where there is no arrival.
    seen: set = set()
    limit = 4 * mask.shape[0] * mask.shape[1] + 32
    for _ in range(limit):
        front_right, front_left = _front(mask, vx, vy, heading)
        if not front_right:
            heading = _TURN_RIGHT[heading]
        elif front_left:
            heading = _TURN_LEFT[heading]
        state = (vx, vy, heading)
        if state in seen:
            break
        seen.add(state)
        points.append((float(vx), float(vy)))
        step_x, step_y = _STEP[heading]
        vx += step_x
        vy += step_y
    else:  # pragma: no cover -- a blob whose outline does not close is a bug
        raise FloorPlanError(
            "A region outline did not close. That is a bug in the extractor, not "
            "in the drawing -- report the image."
        )
    return points


def _sign(value: float) -> int:
    if value > _EPS:
        return 1
    if value < -_EPS:
        return -1
    return 0


def _collapse(points: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    """Drop repeated and collinear corners; the result is corners only."""
    out: List[Tuple[float, float]] = []
    for point in points:
        item = (float(point[0]), float(point[1]))
        if not out or out[-1] != item:
            out.append(item)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(out) < 3:
        return out
    kept: List[Tuple[float, float]] = []
    count = len(out)
    for index in range(count):
        before = out[index - 1]
        here = out[index]
        after = out[(index + 1) % count]
        first = (_sign(here[0] - before[0]), _sign(here[1] - before[1]))
        second = (_sign(after[0] - here[0]), _sign(after[1] - here[1]))
        if first != second:
            kept.append(here)
    return kept


def _polygon_area(points: Sequence[Sequence[float]]) -> float:
    total = 0.0
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


# ==========================================================================
# The grid
# ==========================================================================


def _best_phase(values: np.ndarray, pitch: float) -> Tuple[float, float]:
    """``(offset, worst deviation)`` for a lattice of this pitch.

    The offset is rounded to a whole pixel, because every position going in came
    off the pixel lattice and is an integer: a lattice at x.37 would answer a
    drawing of clean blocks with coordinates ending in .37, and the deviation is
    recomputed against the lattice actually used so nothing is reported against
    a lattice nobody snapped to.
    """
    if values.size == 0:
        return 0.0, 0.0
    angle = 2.0 * math.pi * values / pitch
    mean = math.atan2(float(np.sin(angle).mean()), float(np.cos(angle).mean()))
    offset = float(round((mean / (2.0 * math.pi)) * pitch)) % pitch
    deviation = ((values - offset + pitch / 2.0) % pitch) - pitch / 2.0
    return offset, float(np.abs(deviation).max())


def _detect_grid(xs: Sequence[float], ys: Sequence[float], tolerance: float,
                 size_px: int) -> Optional[Dict[str, float]]:
    """The largest pitch that explains every edge position within *tolerance*.

    Snapping is safe *because* of that last clause and not because the pitch is
    right: whatever comes back, no coordinate moves more than ``tolerance``
    pixels.  A wrong pitch therefore costs three pixels, and the three pixels of
    jitter a hand-placed block has are what this is for.
    """
    x_values = np.asarray(sorted(set(round(float(v), 3) for v in xs)), dtype=np.float64)
    y_values = np.asarray(sorted(set(round(float(v), 3) for v in ys)), dtype=np.float64)
    if x_values.size < 2 or y_values.size < 2:
        return None
    top = int(min(GRID_MAX_PX, max(GRID_MIN_PX, size_px // 3)))
    floor_pitch = max(GRID_MIN_PX, int(math.ceil(2.0 * tolerance + 2.0)))
    found: Optional[Dict[str, float]] = None
    for pitch in range(floor_pitch, top + 1):
        offset_x, deviation_x = _best_phase(x_values, float(pitch))
        if deviation_x > tolerance:
            continue
        offset_y, deviation_y = _best_phase(y_values, float(pitch))
        if deviation_y > tolerance:
            continue
        found = {
            "pitch_px": float(pitch),
            "offset_x_px": round(offset_x, 4),
            "offset_y_px": round(offset_y, 4),
            "worst_px": round(max(deviation_x, deviation_y), 4),
        }
    return found


def _snap_value(value: float, pitch: float, offset: float) -> float:
    return offset + round((value - offset) / pitch) * pitch


def _snap_polygon(points: Sequence[Sequence[float]],
                  grid: Optional[Mapping[str, float]]) -> List[Tuple[float, float]]:
    if not grid:
        return _collapse(points)
    pitch = float(grid["pitch_px"])
    snapped = [
        (_snap_value(float(x), pitch, float(grid["offset_x_px"])),
         _snap_value(float(y), pitch, float(grid["offset_y_px"])))
        for x, y in points
    ]
    return _collapse(snapped)


# ==========================================================================
# Region edges and the walls between them
# ==========================================================================


def _polygon_edges(points: Sequence[Sequence[float]]) -> List[Dict[str, float]]:
    """Each side of a rectilinear polygon, with the direction its interior lies.

    The outline walk keeps the blob on its right, so for every edge the interior
    is on the right of travel.  ``inward`` is +1 when the interior sits at a
    larger coordinate across the edge and -1 when it sits at a smaller one.
    """
    edges: List[Dict[str, float]] = []
    count = len(points)
    for index in range(count):
        x0, y0 = float(points[index][0]), float(points[index][1])
        x1, y1 = float(points[(index + 1) % count][0]), float(points[(index + 1) % count][1])
        if abs(y1 - y0) < _EPS and abs(x1 - x0) > _EPS:
            edges.append({
                "axis": "h", "pos": y0, "lo": min(x0, x1), "hi": max(x0, x1),
                "inward": 1.0 if x1 > x0 else -1.0, "index": float(index),
            })
        elif abs(x1 - x0) < _EPS and abs(y1 - y0) > _EPS:
            edges.append({
                "axis": "v", "pos": x0, "lo": min(y0, y1), "hi": max(y0, y1),
                "inward": -1.0 if y1 > y0 else 1.0, "index": float(index),
            })
    return edges


def _subtract(span: Tuple[float, float],
              taken: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """What is left of an edge once the shared walls are cut out of it."""
    pieces = [span]
    for lo, hi in sorted(taken):
        nxt: List[Tuple[float, float]] = []
        for start, end in pieces:
            if hi <= start + _EPS or lo >= end - _EPS:
                nxt.append((start, end))
                continue
            if lo > start + _EPS:
                nxt.append((start, min(lo, end)))
            if hi < end - _EPS:
                nxt.append((max(hi, start), end))
        pieces = nxt
    return [(a, b) for a, b in pieces if b - a > MIN_WALL_PX]


def _wall_gap_limit(gaps: Sequence[float], size_px: int) -> Tuple[float, Optional[float]]:
    """How wide a white separation may be before it is a hall, not a wall.

    Read out of the drawing rather than assumed: the thinnest separation between
    two rooms *is* the wall, and anything up to twice that is the same wall drawn
    a little differently.  Wider than that is open space, and calling it a wall
    would close a hall the artist drew open.
    """
    positive = sorted(g for g in gaps if g > 0.5)
    if not positive:
        return max(3.0, MIN_WALL_PX), None
    thinnest = positive[0]
    limit = min(max(thinnest * 2.0, thinnest + 2.0), MAX_WALL_GAP_FRACTION * size_px)
    return limit, thinnest


# ==========================================================================
# Rasterising a rectilinear polygon (for the fidelity number)
# ==========================================================================


def _fill_polygon(points: Sequence[Sequence[float]], height: int,
                  width: int) -> np.ndarray:
    """Even-odd scan fill of an axis-aligned polygon, on pixel centres."""
    mask = np.zeros((height, width), dtype=bool)
    verticals = []
    count = len(points)
    for index in range(count):
        x0, y0 = float(points[index][0]), float(points[index][1])
        x1, y1 = float(points[(index + 1) % count][0]), float(points[(index + 1) % count][1])
        if abs(x1 - x0) < _EPS and abs(y1 - y0) > _EPS:
            verticals.append((x0, min(y0, y1), max(y0, y1)))
    if not verticals:
        return mask
    top = max(0, int(math.floor(min(v[1] for v in verticals))))
    bottom = min(height, int(math.ceil(max(v[2] for v in verticals))))
    for row in range(top, bottom):
        centre = row + 0.5
        crossings = sorted(x for x, lo, hi in verticals if lo <= centre < hi)
        for index in range(0, len(crossings) - 1, 2):
            left = int(math.ceil(crossings[index] - 0.5))
            right = int(math.ceil(crossings[index + 1] - 0.5))
            left = max(0, min(width, left))
            right = max(0, min(width, right))
            if right > left:
                mask[row, left:right] = True
    return mask


# ==========================================================================
# The entry point
# ==========================================================================


def extract_floorplan(image_path: Any, legend: Optional[Mapping[str, str]] = None,
                      mm_per_px: Optional[float] = None,
                      grid_px: Optional[float] = None,
                      *,
                      color_tolerance: int = COLOR_TOLERANCE,
                      snap_tolerance_px: float = SNAP_TOLERANCE_PX,
                      min_region_fraction: float = MIN_REGION_FRACTION,
                      ) -> Dict[str, Any]:
    """Read a drawn floor plan into a plan file, deterministically.

    :param image_path: the drawing, as a PNG (or anything Pillow reads).
    :param legend: ``{"#4285f4": "house_door", "#34a853": "doorway", ...}`` --
        which colour means which kind of opening.  Matched with the same
        tolerance the palette uses, so the hex does not have to be exact.  Left
        out, the swatches in the drawing's own legend are found geometrically
        (a coloured blob touching no room) and their roles are ASSUMED from hue,
        with the assumption named in the report.
    :param mm_per_px: the scale.  Left out, the plan comes back in pixels
        marked uncalibrated, and ``report["calibration_question"]`` is the one
        question to ask.
    :param grid_px: the drawing's grid pitch, if it is known.  Left out, it is
        detected from where the edges actually fall.

    Returns ``{"plan", "regions", "mask", "report"}``.  ``plan`` passes
    :func:`service.floorplan.validate_plan`; ``mask`` is on
    :func:`service.floorplan.plan_mask`'s grid contract.
    """
    rgb, path = _load_rgb(image_path)
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    tolerance = int(color_tolerance)
    snap_tolerance = float(snap_tolerance_px)

    scaled = mm_per_px is not None
    scale = 1.0
    if scaled:
        scale = float(fp._number(mm_per_px, "mm_per_px", positive=True, allow_zero=False))

    notes: List[str] = []
    warnings: List[str] = []
    ambiguous: List[str] = []

    # -- colours ------------------------------------------------------------
    raw_classes, palette, tally = _palette(rgb, tolerance, MIN_COLOR_FRACTION)
    kinds, palette_notes = _classify_palette(palette, tally, raw_classes)
    notes.extend(palette_notes)
    classes, absorbed = _absorb_noise(raw_classes, kinds, NOISE_ROUNDS)
    if absorbed:
        notes.append(
            f"{absorbed} pixels of text and edge fringe were absorbed into the "
            f"fill around them -- words in a drawing are labels, not geometry."
        )

    room_indices = [i for i, kind in enumerate(kinds) if kind == "room"]
    strip_indices = [i for i, kind in enumerate(kinds) if kind == "opening"]
    if not room_indices:
        raise FloorPlanError(
            "No room fills were found in "
            + os.path.basename(path)
            + ". The colours in it are "
            + ", ".join(f"{_hex(palette[i])} ({kinds[i]})" for i in range(len(kinds)))
            + ". This reader wants flat blocks: a grey fill per room on a white "
            "background. If the plan is line-art only, describe the rooms in "
            "words instead -- do NOT type coordinates off the picture."
        )

    # -- regions ------------------------------------------------------------
    #: Labelled per FILL COLOUR rather than over all fills at once, so two rooms
    #: in two different greys that touch are two rooms, and two rooms in the same
    #: grey stay separate because the wall between them is drawn.
    min_area = max(16.0, min_region_fraction * height * width)
    blobs: List[Dict[str, Any]] = []
    for palette_index in room_indices:
        own = classes == palette_index
        if not own.any():
            continue
        labels, count = _label_components(own)
        for label in range(1, count + 1):
            blob = labels == label
            area = int(blob.sum())
            if area < min_area:
                continue
            ys, xs = np.nonzero(blob)
            blobs.append({
                "mask": blob,
                "area_px": area,
                "palette": palette_index,
                "start": (int(xs[ys == ys.min()].min()), int(ys.min())),
                "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                         int(ys.max()) + 1],
            })

    if not blobs:
        raise FloorPlanError(
            "Every room-coloured blob in "
            + os.path.basename(path)
            + " is smaller than "
            + f"{min_area:.0f} pixels, which makes them marks rather than rooms. "
            "Check that the drawing is the flat-block kind, or pass a smaller "
            "min_region_fraction."
        )

    # Reading order: top to bottom, then left to right, exactly how the artist
    # would list them, so `room-r1` is the one they would call the first room.
    blobs.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item["bbox"][3],
                                 item["bbox"][2]))

    # -- door strips, the legend, and the notches a strip leaves behind ------
    room_occupancy = np.zeros((height, width), dtype=bool)
    for blob in blobs:
        room_occupancy |= blob["mask"]
    near_room = _grow(room_occupancy, max(1, int(LEGEND_CLEARANCE_PX)))

    strips, swatches, healed = _find_strips(classes, strip_indices, palette, blobs,
                                            near_room, height, width)
    if healed:
        notes.append(
            f"{healed} pixels of door strip drawn OVER a room's fill were given "
            f"back to the room before it was measured -- a door drawn on top of a "
            f"wall is still that wall, and the bite it takes out of the colour is "
            f"not a bay window."
        )

    outlines: List[List[Tuple[float, float]]] = []
    for blob in blobs:
        traced = _collapse(_trace_outline(blob["mask"], blob["start"][0], blob["start"][1]))
        outlines.append(traced)

    # -- the grid -----------------------------------------------------------
    grid: Optional[Dict[str, float]] = None
    grid_source = "none"
    edge_xs: List[float] = []
    edge_ys: List[float] = []
    for outline in outlines:
        for x, y in outline:
            edge_xs.append(x)
            edge_ys.append(y)
    if grid_px is not None:
        pitch = float(fp._number(grid_px, "grid_px", positive=True, allow_zero=False))
        if pitch < 2.0:
            raise FloorPlanError(
                f"grid_px is {pitch:g}. A grid pitch under 2 pixels snaps nothing "
                f"and says it did."
            )
        offset_x, worst_x = _best_phase(np.asarray(edge_xs, dtype=np.float64), pitch)
        offset_y, worst_y = _best_phase(np.asarray(edge_ys, dtype=np.float64), pitch)
        grid = {"pitch_px": pitch, "offset_x_px": round(offset_x, 4),
                "offset_y_px": round(offset_y, 4),
                "worst_px": round(max(worst_x, worst_y), 4)}
        grid_source = "given"
        if grid["worst_px"] > snap_tolerance:
            warnings.append(
                f"the grid pitch you gave ({pitch:g} px) does not fit the drawing: "
                f"edges sit up to {grid['worst_px']:.1f} px off it, past the "
                f"{snap_tolerance:g} px this reader is willing to move a coordinate "
                f"to tidy it. Snapping to it MOVED walls rather than straightening "
                f"them -- check the rooms against the drawing, or leave grid_px out "
                f"and let the pitch be detected."
            )
    else:
        grid = _detect_grid(edge_xs, edge_ys, snap_tolerance, min(height, width))
        grid_source = "detected" if grid else "none"
        if grid is None:
            notes.append(
                "no grid pitch fits this drawing's edges within "
                f"{snap_tolerance:g} px, so coordinates were left exactly where "
                "they were measured. Nothing was invented; the outlines are still "
                "rectilinear."
            )

    polygons: List[List[Tuple[float, float]]] = []
    for index, outline in enumerate(outlines):
        snapped = _snap_polygon(outline, grid)
        if len(snapped) < 4 or _polygon_area(snapped) <= _EPS:
            snapped = _collapse(outline)
            warnings.append(
                f"region {index + 1} collapsed when snapped to the grid, so it kept "
                f"its measured outline. It is probably thinner than one grid cell."
            )
        polygons.append(snapped)

    role_by_palette, legend_report = _resolve_legend(
        palette, strip_indices, legend, swatches, tolerance, ambiguous,
    )

    # -- ids ----------------------------------------------------------------
    keys = [f"r{index + 1}" for index in range(len(polygons))]

    # -- walls --------------------------------------------------------------
    edges_per_region = [_polygon_edges(poly) for poly in polygons]
    region_labels = np.zeros((height, width), dtype=np.int32)
    for index, blob in enumerate(blobs, start=1):
        region_labels[blob["mask"]] = index

    candidates: List[Dict[str, Any]] = []
    span_cap = MAX_WALL_GAP_FRACTION * min(height, width)
    for first in range(len(polygons)):
        for second in range(first + 1, len(polygons)):
            for edge_a in edges_per_region[first]:
                for edge_b in edges_per_region[second]:
                    if edge_a["axis"] != edge_b["axis"]:
                        continue
                    if abs(edge_b["inward"] + edge_a["inward"]) > _EPS:
                        continue
                    delta = edge_b["pos"] - edge_a["pos"]
                    if abs(delta) > _EPS and _sign(delta) != -int(edge_a["inward"]):
                        continue
                    gap = abs(delta)
                    if gap > span_cap:
                        continue
                    lo = max(edge_a["lo"], edge_b["lo"])
                    hi = min(edge_a["hi"], edge_b["hi"])
                    if hi - lo < MIN_SHARED_OVERLAP_PX:
                        continue
                    candidates.append({
                        "a": first, "b": second, "axis": edge_a["axis"],
                        "pos_a": edge_a["pos"], "pos_b": edge_b["pos"],
                        "lo": lo, "hi": hi, "gap": gap,
                        "edge_a": edge_a, "edge_b": edge_b,
                    })

    gap_limit, thinnest_gap = _wall_gap_limit([c["gap"] for c in candidates],
                                              min(height, width))
    shared: List[Dict[str, Any]] = []
    for candidate in candidates:
        if candidate["gap"] > gap_limit + _EPS:
            ambiguous.append(
                f"{keys[candidate['a']]} and {keys[candidate['b']]} face each other "
                f"across {candidate['gap']:.0f} px, wider than the "
                f"{gap_limit:.0f} px this drawing's walls are, so it was read as "
                f"open space rather than a wall."
            )
            continue
        if not _nothing_between(region_labels, candidate, height, width):
            ambiguous.append(
                f"{keys[candidate['a']]} and {keys[candidate['b']]} face each other "
                f"but a third region lies between them, so no wall was made there."
            )
            continue
        shared.append(candidate)

    shared.sort(key=lambda c: (c["a"], c["b"], c["axis"], c["pos_a"], c["lo"]))

    consumed: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
    walls: List[Dict[str, Any]] = []
    wall_meta: Dict[str, Dict[str, Any]] = {}
    pair_counts: Dict[Tuple[int, int], int] = {}
    for candidate in shared:
        pair = (candidate["a"], candidate["b"])
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
        suffix = "" if pair_counts[pair] == 1 else f"-{pair_counts[pair]}"
        wall_key = f"{keys[pair[0]]}-{keys[pair[1]]}{suffix}"
        centre = (candidate["pos_a"] + candidate["pos_b"]) / 2.0
        wall = _wall_entry(f"wall-{wall_key}", candidate["axis"], centre,
                           candidate["lo"], candidate["hi"], height, scale)
        if wall is None:
            continue
        thickness = candidate["gap"] if candidate["gap"] > 0.5 else (thinnest_gap or 0.0)
        if thickness > 0.5:
            wall["thickness_mm"] = round(thickness * scale, 6)
        walls.append(wall)
        wall_meta[wall["id"]] = {
            "key": wall_key, "axis": candidate["axis"], "kind": "shared",
            "between": [keys[pair[0]], keys[pair[1]]],
            "gap_px": candidate["gap"],
        }
        consumed.setdefault((candidate["a"], int(candidate["edge_a"]["index"])), []).append(
            (candidate["lo"], candidate["hi"]))
        consumed.setdefault((candidate["b"], int(candidate["edge_b"]["index"])), []).append(
            (candidate["lo"], candidate["hi"]))

    default_thickness = thinnest_gap if thinnest_gap else None
    for region_index, edges in enumerate(edges_per_region):
        side_counts: Dict[str, int] = {}
        for edge in edges:
            taken = consumed.get((region_index, int(edge["index"])), [])
            for lo, hi in _subtract((edge["lo"], edge["hi"]), taken):
                side = _side_letter(edge["axis"], int(edge["inward"]))
                side_counts[side] = side_counts.get(side, 0) + 1
                number = "" if side_counts[side] == 1 else str(side_counts[side])
                wall_key = f"{keys[region_index]}-out-{side}{number}"
                wall = _wall_entry(f"wall-{wall_key}", edge["axis"], edge["pos"],
                                   lo, hi, height, scale)
                if wall is None:
                    continue
                if default_thickness:
                    wall["thickness_mm"] = round(default_thickness * scale, 6)
                walls.append(wall)
                wall_meta[wall["id"]] = {
                    "key": wall_key, "axis": edge["axis"], "kind": "exterior",
                    "between": [keys[region_index]], "gap_px": None,
                }

    walls.sort(key=lambda item: item["id"])

    # -- openings -----------------------------------------------------------
    unassigned: List[Dict[str, Any]] = []
    by_wall: Dict[str, List[Dict[str, Any]]] = {}
    for strip in sorted(strips, key=lambda s: (s["bbox"][1], s["bbox"][0])):
        placed = _place_strip(strip, walls, wall_meta, height, scale, snap_tolerance)
        if placed is None:
            unassigned.append(strip)
            ambiguous.append(
                f"a {strip['hex']} strip at pixels {strip['bbox']} sits on no wall "
                f"this reader found, so it became no opening. Check whether the "
                f"rooms either side of it were both filled in."
            )
            continue
        wall_id, at_mm, width_mm = placed
        role = role_by_palette.get(strip["palette"], "door")
        kind, expected = OPENING_ROLES.get(role, ("door", None))
        by_wall.setdefault(wall_id, []).append({
            "at_mm": at_mm, "width_mm": width_mm, "kind": kind, "role": role,
            "hex": strip["hex"], "bbox": strip["bbox"], "wall": wall_id,
            "expected": expected,
        })

    opening_report: List[Dict[str, Any]] = []
    for wall in walls:
        found = by_wall.get(wall["id"])
        if not found:
            continue
        found.sort(key=lambda item: item["at_mm"])
        meta = wall_meta[wall["id"]]
        length = math.hypot(wall["to_mm"][0] - wall["from_mm"][0],
                            wall["to_mm"][1] - wall["from_mm"][1])
        entries: List[Dict[str, Any]] = []
        for number, item in enumerate(found, start=1):
            opening_width = min(item["width_mm"], length)
            at = min(max(item["at_mm"], opening_width / 2.0), length - opening_width / 2.0)
            opening_id = f"open-{meta['key']}-{number}"
            entries.append({
                "id": opening_id, "kind": item["kind"],
                "at_mm": round(at, 6), "width_mm": round(opening_width, 6),
            })
            if item["expected"] == "exterior" and meta["kind"] != "exterior":
                ambiguous.append(
                    f"{opening_id} is {item['hex']}, which the key calls a "
                    f"{item['role']}, but it sits on {wall['id']} -- a wall between "
                    f"two rooms. The colour or the wall is wrong; the drawing wins."
                )
            if item["expected"] == "interior" and meta["kind"] == "exterior":
                ambiguous.append(
                    f"{opening_id} is {item['hex']}, which the key calls a "
                    f"{item['role']}, but it sits on {wall['id']} -- an outside wall."
                )
            opening_report.append({
                "id": opening_id, "wall": wall["id"], "kind": item["kind"],
                "role": item["role"], "color": item["hex"],
                "at_mm": round(at, 6), "width_mm": round(opening_width, 6),
                "bbox_px": item["bbox"],
            })
        wall["openings"] = entries

    # -- rooms --------------------------------------------------------------
    rooms: List[Dict[str, Any]] = []
    regions: List[Dict[str, Any]] = []
    fidelity_union = np.zeros((height, width), dtype=bool)
    for index, polygon in enumerate(polygons):
        blob = blobs[index]
        polygon_mm = [[round(x * scale, 6), round((height - y) * scale, 6)]
                      for x, y in polygon]
        room_id = f"room-{keys[index]}"
        rooms.append({"id": room_id, "polygon_mm": polygon_mm})

        filled = _fill_polygon(polygon, height, width)
        fidelity_union |= filled
        overlap = int(np.logical_and(filled, blob["mask"]).sum())
        union = int(np.logical_or(filled, blob["mask"]).sum())
        fidelity = (overlap / union) if union else 1.0

        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        pad = max(6.0, 0.04 * max(bbox[2] - bbox[0], bbox[3] - bbox[1]))
        crop = [max(0, int(math.floor(bbox[0] - pad))),
                max(0, int(math.floor(bbox[1] - pad))),
                min(width, int(math.ceil(bbox[2] + pad))),
                min(height, int(math.ceil(bbox[3] + pad)))]

        reasons: List[str] = []
        confidence = 1.0
        if fidelity < 0.995:
            confidence = min(confidence, fidelity)
            reasons.append(
                f"the snapped outline covers {fidelity * 100:.1f}% of the pixels "
                f"that were actually filled"
            )
        if len(polygon) > 4:
            confidence = min(confidence, 0.9)
            reasons.append(
                f"{len(polygon)} corners -- this room is not a rectangle, so check "
                f"the shape in the echo-back before building"
            )
        if not reasons:
            reasons.append("a clean rectangle on the grid, pixel for pixel")

        regions.append({
            "id": room_id,
            "key": keys[index],
            "label": None,
            "fill": _hex(palette[blob["palette"]]),
            "bbox_px": blob["bbox"],
            "crop_px": crop,
            "crop": {
                "box_px": crop,
                "why": ("crop the drawing to this box and read the words inside it; "
                        "rename this region's LABEL, never its id"),
            },
            "polygon_px": [[round(x, 4), round(y, 4)] for x, y in polygon],
            "polygon_mm": polygon_mm,
            "size_mm": [round((bbox[2] - bbox[0]) * scale, 4),
                        round((bbox[3] - bbox[1]) * scale, 4)],
            "size_px": [round(bbox[2] - bbox[0], 4), round(bbox[3] - bbox[1], 4)],
            "area_px": blob["area_px"],
            "area_mm2": round(_polygon_area(polygon) * scale * scale, 4),
            "corners": len(polygon),
            "fidelity": round(fidelity, 6),
            "confidence": round(confidence, 4),
            "confidence_reasons": reasons,
        })

    # -- the plan -----------------------------------------------------------
    plan: Dict[str, Any] = {
        "version": fp.PLAN_VERSION,
        "units": fp.UNITS,
        "scale": {
            "mm_per_px": scale,
            "calibrated_by": (
                "a caller-supplied mm_per_px"
                if scaled else
                "NOT CALIBRATED -- 1 px is standing in for 1 mm until one real "
                "dimension is given"
            ),
        },
        "defaults": {},
        "rooms": rooms,
        "walls": walls,
        "labels": [],
        "history": [{
            "rev": 1,
            "note": (f"extracted from {os.path.basename(path)} by "
                     f"floorplan_extract: geometry measured, names not read"),
        }],
    }

    axis_notes: List[str] = []
    plan = fp.validate_plan(plan, warnings=axis_notes)
    warnings.extend(axis_notes)

    # -- the mask, on plan_mask's own grid ----------------------------------
    occupancy = np.zeros((height, width), dtype=bool)
    for blob in blobs:
        occupancy |= blob["mask"]
    mask = {
        "mask": np.flipud(occupancy),
        "cell_mm": scale,
        "origin_mm": [0.0, 0.0],
        "bounds_mm": [0.0, 0.0, width * scale, height * scale],
        "shape": [height, width],
        "cells_filled": int(occupancy.sum()),
        "area_mm2": float(occupancy.sum()) * scale * scale,
        "components": {regions[i]["id"]: int(blobs[i]["area_px"])
                       for i in range(len(blobs))},
        "rows_are": "+Y ascending: row 0 is the lowest y, not an image's top row",
        "measures": (
            "the ROOM FILL as drawn, one cell per pixel. plan_mask() rasterises "
            "walls and fixtures instead, so compare this with the rooms it came "
            "from (report.fidelity) and compare plan_mask against the rendered "
            "level."
        ),
    }

    union = int(np.logical_or(fidelity_union, occupancy).sum())
    overall_fidelity = (int(np.logical_and(fidelity_union, occupancy).sum()) / union
                        if union else 1.0)

    confidences = [region["confidence"] for region in regions]
    exterior = sum(1 for meta in wall_meta.values() if meta["kind"] == "exterior")
    shared_count = len(wall_meta) - exterior
    by_kind: Dict[str, int] = {}
    for entry in opening_report:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1

    calibration = None
    if not scaled:
        calibration = _calibration_question(opening_report, regions)

    report: Dict[str, Any] = {
        "image": path,
        "image_size_px": [width, height],
        "colors": [
            {"hex": _hex(palette[i]), "rgb": [int(c) for c in palette[i]],
             "pixels": int(tally[i]),
             "share": round(tally[i] / float(height * width), 6),
             "read_as": kinds[i]}
            for i in range(len(kinds))
        ],
        "legend": legend_report,
        "grid": {
            "source": grid_source,
            "pitch_px": grid["pitch_px"] if grid else None,
            "offset_px": [grid["offset_x_px"], grid["offset_y_px"]] if grid else None,
            "worst_snap_px": grid["worst_px"] if grid else None,
            "tolerance_px": snap_tolerance,
            "why": ("no coordinate is ever moved further than the tolerance, so a "
                    "pitch that is slightly wrong costs pixels and never a wall in "
                    "the wrong place"),
        },
        "counts": {
            "rooms": len(rooms),
            "walls": len(walls),
            "walls_shared": shared_count,
            "walls_exterior": exterior,
            "openings": len(opening_report),
            "openings_by_kind": by_kind,
            "unassigned_strips": len(unassigned),
            "legend_swatches": len(swatches),
        },
        "wall_gap_px": round(thinnest_gap, 4) if thinnest_gap else None,
        "wall_gap_limit_px": round(gap_limit, 4),
        "openings": opening_report,
        "regions": [
            {"id": region["id"], "confidence": region["confidence"],
             "fidelity": region["fidelity"], "corners": region["corners"],
             "size_mm": region["size_mm"], "crop_px": region["crop_px"],
             "reasons": region["confidence_reasons"]}
            for region in regions
        ],
        "confidence": {
            "min": round(min(confidences), 4) if confidences else 1.0,
            "mean": round(sum(confidences) / len(confidences), 4) if confidences else 1.0,
        },
        "fidelity": {
            "rooms_vs_fill_iou": round(overall_fidelity, 6),
            "what_it_means": ("how much of the colour the artist filled in is covered "
                              "by the rectilinear rooms this reader produced. 1.0 is "
                              "a pixel-perfect reading of the drawing."),
        },
        "scale": {
            "mm_per_px": scale,
            "calibrated": scaled,
            "units_are": "mm" if scaled else "pixels standing in for millimetres",
        },
        "calibration_question": calibration,
        "calibration_hints": _calibration_hints(opening_report, regions, scale),
        "naming": (
            "every room is room-r1..room-rN in reading order with NO label. Look at "
            "each region's crop box, read the word written in it, and set that as "
            "the room's `label`. Never change an id: an id names a Blender object, "
            "and a renamed id deletes the artist's work."
        ),
        "ambiguous": ambiguous,
        "notes": notes,
        "warnings": warnings,
        "honesty": HONESTY,
    }

    return {"plan": plan, "regions": regions, "mask": mask, "report": report}


# ==========================================================================
# Pieces of the entry point, kept out of it so it reads top to bottom
# ==========================================================================


def _grow(mask: np.ndarray, rounds: int) -> np.ndarray:
    """*mask* dilated by *rounds* pixels, 4-connectivity."""
    out = mask.copy()
    for _ in range(max(0, int(rounds))):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _find_strips(classes: np.ndarray, strip_indices: Sequence[int],
                 palette: np.ndarray, blobs: List[Dict[str, Any]],
                 near_room: np.ndarray, height: int, width: int,
                 ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """Split the coloured blobs into door strips and legend swatches, and heal.

    Two rules, both geometric, because nothing in this module reads a caption:

    * a coloured blob that touches **no** room fill is a legend swatch; one on
      the edge of a room is a door;
    * a door drawn *over* a room's fill leaves a bite out of it, and a bite is
      the difference between a rectangle and a bay window.  A strip touching
      exactly ONE room gives back the pixels of itself that fall **inside that
      room's bounding box** -- inside, so a strip sticking out past the wall
      cannot grow the room, and one room, so a doorway bridging two of them
      stays the hole it is.
    """
    strips: List[Dict[str, Any]] = []
    swatches: List[Dict[str, Any]] = []
    healed = 0
    touch = [_grow(blob["mask"], 1) for blob in blobs]

    for palette_index in strip_indices:
        own = classes == palette_index
        if not own.any():
            continue
        labels, count = _label_components(own)
        for label in range(1, count + 1):
            piece = labels == label
            area = int(piece.sum())
            if area < 4:
                continue
            ys, xs = np.nonzero(piece)
            record = {
                "palette": int(palette_index),
                "hex": _hex(palette[palette_index]),
                "area_px": area,
                "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                         int(ys.max()) + 1],
            }
            if not bool((piece & near_room).any()):
                swatches.append(record)
                continue
            strips.append(record)

            touching = [index for index, reach in enumerate(touch)
                        if bool((piece & reach).any())]
            if len(touching) != 1:
                continue
            owner = blobs[touching[0]]
            x0, y0, x1, y1 = owner["bbox"]
            inside = np.zeros_like(piece)
            inside[y0:y1, x0:x1] = True
            give_back = piece & inside
            if not give_back.any():
                continue
            healed += int(give_back.sum())
            owner["mask"] = owner["mask"] | give_back
            owner["area_px"] = int(owner["mask"].sum())
            rows, cols = np.nonzero(owner["mask"])
            top = int(rows.min())
            owner["start"] = (int(cols[rows == top].min()), top)

    return strips, swatches, healed


def _side_letter(axis: str, inward: int) -> str:
    """Which side of its region an exterior edge is, in compass letters.

    Image rows run downwards and the plan's y runs upwards, so an edge whose
    interior lies at a SMALLER pixel y has the region above it in the drawing --
    which is the region's south side.
    """
    if axis == "h":
        return "s" if inward < 0 else "n"
    return "e" if inward < 0 else "w"


def _wall_entry(wall_id: str, axis: str, pos: float, lo: float, hi: float,
                height: int, scale: float) -> Optional[Dict[str, Any]]:
    """One wall, in millimetres, always pointing west-to-east or south-to-north.

    Fixing the direction is what makes ``at_mm`` mean the same thing on every
    re-extraction: an opening measured from the other end of the same wall is
    the same door in a different place.
    """
    if hi - lo < MIN_WALL_PX:
        return None
    if axis == "h":
        y_mm = round((height - pos) * scale, 6)
        start = [round(lo * scale, 6), y_mm]
        end = [round(hi * scale, 6), y_mm]
    else:
        x_mm = round(pos * scale, 6)
        start = [x_mm, round((height - hi) * scale, 6)]
        end = [x_mm, round((height - lo) * scale, 6)]
    if math.hypot(end[0] - start[0], end[1] - start[1]) < fp.MIN_WALL_LENGTH_MM:
        return None
    return {"id": wall_id, "from_mm": start, "to_mm": end, "openings": []}


def _nothing_between(region_labels: np.ndarray, candidate: Mapping[str, Any],
                     height: int, width: int) -> bool:
    """True when the strip between two facing edges holds no third region."""
    low = min(candidate["pos_a"], candidate["pos_b"])
    high = max(candidate["pos_a"], candidate["pos_b"])
    if high - low < 1.0:
        return True
    span_lo, span_hi = candidate["lo"], candidate["hi"]
    if candidate["axis"] == "h":
        y0, y1 = int(math.floor(low)), int(math.ceil(high))
        x0, x1 = int(math.floor(span_lo)), int(math.ceil(span_hi))
    else:
        x0, x1 = int(math.floor(low)), int(math.ceil(high))
        y0, y1 = int(math.floor(span_lo)), int(math.ceil(span_hi))
    y0 = max(0, min(height, y0))
    y1 = max(0, min(height, y1))
    x0 = max(0, min(width, x0))
    x1 = max(0, min(width, x1))
    if y1 <= y0 or x1 <= x0:
        return True
    patch = region_labels[y0:y1, x0:x1]
    present = set(np.unique(patch).tolist()) - {0}
    allowed = {candidate["a"] + 1, candidate["b"] + 1}
    return not (present - allowed)


def _place_strip(strip: Mapping[str, Any], walls: Sequence[Mapping[str, Any]],
                 wall_meta: Mapping[str, Mapping[str, Any]], height: int,
                 scale: float, snap_tolerance: float,
                 ) -> Optional[Tuple[str, float, float]]:
    """Put a coloured strip onto the wall it lies across.

    Geometry decides, not colour: the strip is matched to the nearest wall of
    the same orientation whose span it falls inside.  ``at_mm`` is the strip's
    own centre projected onto that wall from its ``from_mm`` end.
    """
    x0, y0, x1, y1 = (float(v) for v in strip["bbox"])
    left, right = x0 * scale, x1 * scale
    bottom, top = (height - y1) * scale, (height - y0) * scale
    centre = ((left + right) / 2.0, (bottom + top) / 2.0)
    across = right - left
    up = top - bottom
    horizontal = across >= up

    best: Optional[Tuple[float, str, float, float]] = None
    for wall in walls:
        start, end = wall["from_mm"], wall["to_mm"]
        wall_horizontal = abs(end[1] - start[1]) < _EPS
        if wall_horizontal != horizontal:
            continue
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length <= _EPS:
            continue
        if wall_horizontal:
            along = centre[0] - start[0]
            perpendicular = abs(centre[1] - start[1])
            span = across
            thickness = up
        else:
            along = centre[1] - start[1]
            perpendicular = abs(centre[0] - start[0])
            span = up
            thickness = across
        margin = span / 2.0 + snap_tolerance * scale
        if along < -margin or along > length + margin:
            continue
        wall_thickness = float(wall.get("thickness_mm") or fp.DEFAULTS["wall_mm"])
        limit = wall_thickness / 2.0 + thickness / 2.0 + 2.0 * snap_tolerance * scale + 1.0
        if perpendicular > limit:
            continue
        key = (round(perpendicular, 6), wall["id"])
        if best is None or key < (best[0], best[1]):
            best = (round(perpendicular, 6), wall["id"], along, span)
    if best is None:
        return None
    return best[1], best[2], best[3]


def _resolve_legend(palette: np.ndarray, strip_indices: Sequence[int],
                    legend: Optional[Mapping[str, str]],
                    swatches: Sequence[Mapping[str, Any]], tolerance: int,
                    ambiguous: List[str],
                    ) -> Tuple[Dict[int, str], Dict[str, Any]]:
    """Which colour means which kind of opening, and where that came from.

    A legend the caller passes wins outright.  Without one, the swatches in the
    drawing's own key are found geometrically -- but nothing here reads their
    captions, so the ROLE is assumed from hue and the assumption is spelled out
    by name in the report every single time.
    """
    roles: Dict[int, str] = {}
    assumed: List[str] = []
    given: List[Dict[str, str]] = []

    parsed: List[Tuple[Tuple[int, int, int], str]] = []
    if legend:
        for key, value in legend.items():
            color = _parse_hex(key)
            role = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
            if color is None:
                ambiguous.append(
                    f"legend key {key!r} is not a colour like '#4285f4', so it was "
                    f"ignored."
                )
                continue
            if role not in OPENING_ROLES:
                ambiguous.append(
                    f"legend says {key} is a {value!r}, which is not one of "
                    f"{', '.join(sorted(OPENING_ROLES))}; it was ignored."
                )
                continue
            parsed.append((color, role))
            given.append({"hex": _hex(color), "role": role})

    swatch_by_palette: Dict[int, Dict[str, Any]] = {}
    for swatch in swatches:
        swatch_by_palette.setdefault(int(swatch["palette"]), dict(swatch))

    for index in strip_indices:
        color = palette[index]
        matched: Optional[str] = None
        if parsed:
            distances = [(max(abs(int(color[0]) - c[0]), abs(int(color[1]) - c[1]),
                              abs(int(color[2]) - c[2])), role)
                         for c, role in parsed]
            distances.sort(key=lambda item: (item[0], item[1]))
            if distances[0][0] <= tolerance * 2:
                matched = distances[0][1]
        if matched is None:
            hue = _hue_deg(color)
            matched = "door"
            for low, high, role in AUTO_ROLE_BY_HUE:
                if low <= hue < high:
                    matched = role
                    break
            assumed.append(
                f"{_hex(color)} (hue {hue:.0f} degrees) was ASSUMED to be a "
                f"{matched}"
                + (f", and the drawing does have a swatch of it at pixels "
                   f"{swatch_by_palette[index]['bbox']}"
                   if index in swatch_by_palette else
                   ", and there is no swatch of it in any legend block")
                + ". Say so, or pass `legend` to fix it."
            )
        roles[int(index)] = matched

    source = "given" if given and not assumed else ("mixed" if given else "assumed")
    report = {
        "source": source,
        "given": given,
        "assumed": assumed,
        "swatches": [
            {"hex": swatch["hex"], "bbox_px": swatch["bbox"],
             "area_px": swatch["area_px"],
             "role": roles.get(int(swatch["palette"]))}
            for swatch in swatches
        ],
        "how_swatches_were_found": (
            "a coloured blob that touches no room fill is a legend swatch; a "
            "coloured blob on the edge of one is a door. No caption was read -- "
            "this module never reads text."
        ),
        "roles": {_hex(palette[index]): roles[int(index)] for index in strip_indices},
    }
    return roles, report


def _calibration_hints(openings: Sequence[Mapping[str, Any]],
                       regions: Sequence[Mapping[str, Any]],
                       scale: float) -> List[Dict[str, Any]]:
    """Things in the drawing whose real size the artist is likely to know.

    Each carries its length in PIXELS, so one stated millimetre figure turns
    into ``mm_per_px`` with a division and nothing has to be re-measured.
    """
    hints: List[Dict[str, Any]] = []
    for entry in openings[:6]:
        px = float(entry["width_mm"]) / scale if scale else 0.0
        hints.append({
            "what": f"{entry['id']} ({entry['role']}) on {entry['wall']}",
            "length_px": round(px, 3),
            "mm_per_px_if": "mm_per_px = the width you tell me / this length_px",
        })
    for region in regions[:4]:
        hints.append({
            "what": f"{region['id']} across its widest side",
            "length_px": round(float(region["size_px"][0]), 3),
            "mm_per_px_if": "mm_per_px = the width you tell me / this length_px",
        })
    return hints


def _calibration_question(openings: Sequence[Mapping[str, Any]],
                          regions: Sequence[Mapping[str, Any]]) -> str:
    """The ONE question.  Nothing else downstream can be repaired by asking."""
    example = "any wall you have actually measured"
    for entry in openings:
        if entry["role"] in ("house_door", "front_door", "exterior_door"):
            example = "the front door"
            break
    else:
        if openings:
            example = "one of the doorways"
    return (
        "A drawing has no size in it, so every number below is in PIXELS standing "
        "in for millimetres. Tell me ONE real dimension you already know -- "
        f"{example}, or the length of any wall you have measured -- and the whole "
        "plan rescales from it. If you would rather not measure anything, say so "
        "and I will assume a standard 820 mm door leaf and mark the plan as "
        "estimated."
    )


def mm_per_px_from(known_mm: float, length_px: float) -> float:
    """The scale, from one measured thing and its length in the drawing."""
    known = fp._number(known_mm, "known_mm", positive=True, allow_zero=False)
    pixels = fp._number(length_px, "length_px", positive=True, allow_zero=False)
    return known / pixels


__all__ = [
    "AUTO_ROLE_BY_HUE",
    "COLOR_TOLERANCE",
    "HONESTY",
    "OPENING_ROLES",
    "SNAP_TOLERANCE_PX",
    "extract_floorplan",
    "mm_per_px_from",
]
