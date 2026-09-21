"""``sprite_cutout`` — a sprite image becomes a cardboard-cutout card mesh.

The owner's ask, verbatim: *"pixel-art sprites as 3D sprites for Godot — like a
cardboard cutout, we don't care about the side or back."*

What this command does
----------------------
One picture in, one mesh out: the sprite's own outline, traced off the pixels,
extruded a few percent of its height into a shallow card, wearing the picture as
its material with nearest-neighbour filtering and a hard alpha cut, standing on
z = 0 at a world height the caller names.  The side and the back are the cheapest
thing that is still honest — the rim samples the outline's own pixels and the
back is the front seen through the card.

The extraction law (the floor-plan law, applied to pixels)
----------------------------------------------------------
**Every number below is measured off the image; nothing is eyeballed and nothing
is drawn by hand.**  The chain, in order, each step deterministic:

1. **Background.**  A real alpha channel is the answer, exactly, at
   :data:`ALPHA_CUTOFF` — tier ``measured``.  With no alpha the background is the
   **most common colour on the border ring** (the mode, not the median: a flat
   plate is one exact colour repeated thousands of times, and the mode finds it
   where a median would average two of them), and any pixel within
   :data:`BACKGROUND_TOLERANCE` of it is background — tier ``heuristic``, with the
   border's own uniformity and the margin around the threshold reported as
   numbers rather than assumed.
2. **Reachability.**  Only background that is **connected to the frame border**
   (4-connected) is background.  A white pixel inside the sprite — an eye
   highlight, the hole in a doughnut — is not reachable from outside, so it stays
   part of the card and is reported as an enclosed region rather than punched out.
   This is what keeps a cutout a cutout instead of a colander.
3. **Islands.**  The subject is split into 8-connected components (8 for the
   subject, 4 for the background: the classic pairing, the one that does not make
   a checkerboard both connected and disconnected at once).  Every island is
   reported with its pixel area and bounding box; ``islands="largest"`` keeps one
   and ``min_island_fraction`` drops codec speckle, both by measured area.
4. **Contour.**  The outline is traced along the **pixel boundary itself** — the
   corner grid, where a pixel's edge really is — by chaining the directed
   boundary segments of the mask.  No blur, no smoothing, no resampling: the
   traced polygon passes exactly through the corners of the pixels the artist
   drew, and at a diagonal pinch the walk crosses to the other blob (the turn
   with the smallest cross product), so an 8-connected island traces as one loop.
   That loop is then **split back into simple rings at every corner it visits
   twice** — a pinched loop is not a polygon anything can triangulate, and the
   one time it was handed on whole it built a card covering 38.8% of its own
   sprite. See :func:`split_pinches`.
5. **Simplify.**  Exactly collinear points go first (integer coordinates, so that
   test is exact and free), then Douglas–Peucker at ``simplify_px`` source pixels
   (default :data:`SIMPLIFY_PX`, half a pixel).  Half a pixel is the derivation:
   the texture carries the art, so the silhouette only has to be right to within
   less than the size of the thing it is made of, and a 45° staircase collapses
   to the straight line it was always meant to be.

The card
--------
Front cap at ``y = -thickness/2`` facing −Y (Blender's front view, the way the
sprite was drawn), back cap at ``+thickness/2``, one quad per contour edge around
the rim, and an optional bevel on the two rims.  Thickness is
``thickness_fraction`` of the **sprite's** height (default
:data:`THICKNESS_FRACTION`); the sprite's height is the subject's bounding box,
not the picture's frame, so padding around the art never changes the size of the
model.

UVs are a **planar projection from the front** — ``u = x_pixels / width``,
``v = 1 − y_pixels / height``, computed from each vertex's own world position —
so the front covers the source image exactly, the back (same X and Z, seen from
the other side) reads mirrored, the rim samples the outline pixels it sits on,
and a bevel gets correct UVs for free because a projection does not care how the
geometry got there.

The texture
-----------
A picture with a real alpha channel is used **as it is**.  A picture without one
(a sprite on a flat plate — the common case) would drag its background into the
card's fringe, so the command writes a **cutout texture**: the source's own RGB,
with alpha taken from the extraction mask of step 2.  It is a new image datablock
beside the source file, not an edit of it, and the report says which of the two
happened.  Either way the material is a Principled BSDF with the image in Base
Color and Alpha, ``Closest`` interpolation (pixel art is not supposed to be
smooth), ``CLIP`` extension, roughness 1 and specular 0 — a flat card that
catches a highlight stops reading as a drawing.  The alpha cut is a **node**
(``Math: alpha > 0.5``) rather than a material setting, for a reason worth
knowing before changing it: see :func:`_alpha_clip`.

What it honestly is not
-----------------------
* Not a model.  It is a picture on a shallow slab; from the side it is a slab,
  and ``relief`` only fakes a *shading* gradient off the picture's own luminance
  — it is not depth, it was not measured, and it is off by default.
* Not a vectoriser.  The outline is the pixel boundary, simplified to a stated
  tolerance; it does not find the shape the artist "meant".
* The silhouette is only as separable as the background.  On a plain plate this
  is exact to the byte; on a busy photograph it is a threshold, and the report
  says so in the same sentence both times.

Undo
----
``sprite_cutout`` builds an object, so it is deliberately **not** in
``READ_ONLY_COMMANDS``: the registry pushes ``Forge: sprite_cutout`` before it
runs and one Ctrl+Z takes the card, its material and its texture back.
"""

import math
import os
import time

import bpy

from . import common
from . import verify
from .common import M_TO_MM, get_float, get_int, get_str
from .registry import ForgeError, command
from .verify import HEURISTIC, MEASURED, claim

try:  # Blender ships numpy; the guard keeps this honest if a build ever does not.
    import numpy as _np
except ImportError:  # pragma: no cover - numpy is part of Blender
    _np = None

__all__ = [
    "ALPHA_CUTOFF",
    "BACKGROUND_TOLERANCE",
    "SIMPLIFY_PX",
    "THICKNESS_FRACTION",
    "DEFAULT_HEIGHT_M",
    "extract_islands",
    "trace_contour",
    "simplify_closed",
]


# ---------------------------------------------------------------------------
# constants — each one derived, and each derivation written down
# ---------------------------------------------------------------------------

#: A pixel is the sprite when its alpha is above this, for a picture that has a
#: real alpha channel.  0.5 because a cut-out sprite's alpha is binary: the
#: midpoint is the one cut that is equally far from both answers, and it is the
#: same number the material's alpha clip uses, so the mesh and the texture agree
#: about where the sprite ends.
ALPHA_CUTOFF = 0.5

#: A picture's alpha channel is *usable* only if it actually varies: an opaque
#: RGBA sprite (alpha 1.0 everywhere - the fixture in this add-on's own suite is
#: one) has an alpha channel that says nothing.  More than this fraction of the
#: frame must be non-opaque before the alpha is believed.  1% is the same test
#: ``silhouette.mask_from_image`` makes, and the two must not drift apart.
ALPHA_PRESENT_FRACTION = 0.01

#: How far a pixel may sit from the background colour (RGB distance, in the
#: image's own encoding) and still be background.
#:
#: Derived, not picked.  Blender hands these pixels back byte-exact - the test
#: fixture's plate reads 0.996078 = 254/255 - so one 8-bit step is 1/255 =
#: 0.0039 in one channel and 0.0068 across three.  0.04 is about six of those
#: steps: above the overshoot a lossy codec leaves beside a hard edge (WebP and
#: JPEG both ring a few steps into a flat plate) and far below the distance to
#: any colour an artist chose.  Measured on the harpy fixture: 838 964 px sit at
#: distance 0 and 656 593 px past 0.3, while the whole band between 0.02 and
#: 0.08 holds 12 055 - the threshold sits in a valley two orders of magnitude
#: thinner than either side, which is what makes it a threshold rather than a
#: dial.  Every report carries that margin measured for the picture in hand.
BACKGROUND_TOLERANCE = 0.04

#: The band either side of the tolerance the report measures, as a multiple of
#: it.  Half and double: if a threshold is real, moving it by a factor of two in
#: either direction barely changes the mask.
MARGIN_FACTOR = 2.0

#: Douglas-Peucker tolerance, in source pixels.  Half a pixel: the art itself is
#: made of pixels and the texture carries every one of them, so the silhouette
#: only has to be true to less than the size of its own smallest feature.  At
#: this tolerance a 45-degree staircase collapses to the straight line it was
#: drawn as and a one-pixel notch survives.
SIMPLIFY_PX = 0.5

#: Card thickness as a fraction of the sprite's height.  3%: thick enough that
#: the card still has an edge at a grazing angle and never z-fights the ground
#: it stands on, thin enough that turning it never reads as a box.  On a 1 m
#: sprite that is 30 mm - foam board at the scale a standee is actually built.
THICKNESS_FRACTION = 0.03

#: World height of the sprite when the caller does not say.  One metre = one
#: Blender/Godot unit, so the number the artist types is the number the engine
#: gets, and the width follows from the sprite's own pixel aspect.
DEFAULT_HEIGHT_M = 1.0

#: Islands smaller than this fraction of the largest island's pixel area are
#: codec speckle rather than art, and are dropped (and counted).  Measured on
#: the harpy fixture: the sprite is one island of 671 469 px and the next
#: largest thing in the frame is 78 px - a factor of 8 600, because a lossy
#: codec's ringing around a hard edge crosses the colour threshold in ones and
#: twos.  0.001 of the largest island puts the cut at 672 px: 8x above the
#: biggest speck and 1000x below the sprite.  Pass 0 to keep every speck.
MIN_ISLAND_FRACTION = 0.001

#: How much of its own outline the card's face must cover before the command
#: will hand it over.  1.0 is the only right answer — a triangulated simple
#: polygon covers exactly its own area — so this is a float-noise allowance and
#: not a tolerance: on the two fixtures it measures 1.000000 and 1.000000.
#: Anything materially under it means a ring crossed itself and the triangulator
#: quit part way, which is the defect this floor exists to catch (see
#: :func:`split_pinches`: it once shipped a card covering 38.8% of its sprite).
CAP_COVERAGE_FLOOR = 0.999

#: Bevel segments when a bevel is asked for and no count is given.  One segment
#: is a chamfer, which is all a 30 mm rim can show.
BEVEL_SEGMENTS = 1

#: Rec. 709 luminance, the same weights Blender's own colour management uses -
#: for the optional relief map only.
LUMA = (0.2126, 0.7152, 0.0722)

#: Above this many contour points the command refuses rather than building a
#: mesh nobody can use: 200 000 points is 400 000 vertices before the bevel, and
#: a sprite that traces that long is a photograph, not a sprite.  Raise
#: ``simplify_px`` instead.
MAX_CONTOUR_POINTS = 200000

#: Said in every report.
HONESTY = (
    "This is a picture on a shallow slab, not a model. The outline is the "
    "sprite's own pixel boundary (simplified to a stated tolerance, never "
    "smoothed or redrawn), the front carries the image exactly, the back is the "
    "same projection seen through the card and so reads mirrored, and the rim "
    "samples the outline pixels it sits on. Nothing here knows what the sprite "
    "looks like from the side, because nothing in the picture ever said."
)

#: Added when ``relief`` is on.
RELIEF_HONESTY = (
    "The relief normal map is FABRICATED from the picture's own luminance: it "
    "tilts the shading where the art is bright or dark, which is not depth, was "
    "not measured and is not what the object looks like. It is a lighting trick "
    "on a flat card; turn it off if the card is ever seen from an angle."
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _require_numpy():
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so sprite_cutout cannot run — the "
            "whole extraction is array arithmetic.")


def _round(value, places=4):
    try:
        number = float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if not math.isfinite(number):
        return None
    number = round(number, places)
    return 0.0 if number == 0 else number


def _choice(params, key, allowed, default):
    raw = params.get(key)
    if raw is None:
        return default
    if not isinstance(raw, str) or raw.strip().lower() not in allowed:
        raise ForgeError("'%s' must be one of %s; got %r."
                         % (key, ", ".join(repr(a) for a in allowed), raw))
    return raw.strip().lower()


# ---------------------------------------------------------------------------
# 1. the background, and what the picture says about it
# ---------------------------------------------------------------------------

def _border_ring(rgb):
    """The four edges of the frame, as one ``(n, 3)`` array."""
    return _np.concatenate([rgb[0, :, :], rgb[-1, :, :],
                            rgb[:, 0, :], rgb[:, -1, :]], axis=0)


def _dominant_border_colour(rgb):
    """The most common exact colour on the border ring, and how much of it.

    The MODE rather than the median: a flat plate is one colour repeated
    thousands of times, and the mode lands on it exactly, where a median across
    two plates (a gradient, a vignette) would return a colour that is on neither.
    Ties break on the sorted colour so two runs of the same picture answer the
    same way.
    """
    ring = _border_ring(rgb)
    colours, counts = _np.unique(ring, axis=0, return_counts=True)
    # ``np.unique`` sorts lexicographically, so argmax already breaks ties on the
    # smallest colour - deterministic without a second sort.
    best = int(_np.argmax(counts))
    return colours[best], float(counts[best]) / float(len(ring))


def _background_mask(pixels, channels, tolerance):
    """``(is_background, report)`` for a picture, alpha first, colour second."""
    height, width = pixels.shape[0], pixels.shape[1]
    alpha_usable = (channels >= 4
                    and float((pixels[:, :, 3] < 0.99).mean()) > ALPHA_PRESENT_FRACTION)
    if alpha_usable:
        background = pixels[:, :, 3] <= ALPHA_CUTOFF
        report = {
            "source": claim("alpha channel", MEASURED,
                            "the picture carries a real alpha channel, so which "
                            "pixels are the sprite is stated rather than "
                            "estimated"),
            "cutoff": ALPHA_CUTOFF,
            "colour": None,
            "border_uniformity": None,
            "tolerance": None,
            "margin": None,
            "transparent_fraction": _round(float(background.mean()), 5),
        }
        return background, report, True

    rgb = pixels[:, :, :3]
    colour, share = _dominant_border_colour(rgb)
    distance = _np.sqrt(((rgb - colour) ** 2).sum(axis=2))
    background = distance <= tolerance
    tight = float((distance <= tolerance / MARGIN_FACTOR).mean())
    loose = float((distance <= tolerance * MARGIN_FACTOR).mean())
    here = float(background.mean())
    report = {
        "source": claim("border colour", HEURISTIC,
                        "the picture has no usable alpha, so the background is "
                        "the most common colour on its border ring — this "
                        "assumes a PLAIN PLATE behind the sprite"),
        "cutoff": None,
        "colour": [_round(float(v), 6) for v in colour],
        "border_uniformity": _round(share, 4),
        "tolerance": _round(tolerance, 5),
        # How much the answer moves when the threshold moves by a factor of two
        # either way. A real threshold sits in a valley and these three numbers
        # are nearly equal; a tuned one does not and they are not.
        "margin": {
            "at_half_tolerance": _round(tight, 5),
            "at_tolerance": _round(here, 5),
            "at_double_tolerance": _round(loose, 5),
            "swing": _round(loose - tight, 5),
        },
        "transparent_fraction": None,
    }
    return background, report, False


# ---------------------------------------------------------------------------
# 2 + 3. reachability and islands — one run-based component labeller, twice
# ---------------------------------------------------------------------------

def _runs(mask):
    """Row runs of a boolean mask as ``(rows, starts, ends_inclusive)`` arrays.

    Runs rather than pixels because everything downstream (connectivity, areas,
    bounding boxes) is an interval operation, and a 1.5-megapixel sprite has a
    few thousand runs where it has a million and a half pixels.
    """
    height, width = mask.shape
    padded = _np.zeros((height, width + 2), dtype="i1")
    padded[:, 1:width + 1] = mask
    diff = _np.diff(padded, axis=1)
    start_rows, start_cols = _np.nonzero(diff == 1)
    end_rows, end_cols = _np.nonzero(diff == -1)
    # One opening for every closing, in the same row order, because the mask was
    # padded with a zero column on both sides.
    return start_rows, start_cols, end_cols - 1


class _Union(object):
    """Union-find over run indices. Roots are always the smallest member."""

    def __init__(self, count):
        self.parent = list(range(count))

    def find(self, index):
        parent = self.parent
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra


def _label(mask, diagonal):
    """Connected components of ``mask``; ``(labels, count, areas, boxes)``.

    ``diagonal`` picks 8-connectivity (the subject) over 4 (the background) — the
    classic pairing, because making both 8-connected turns a checkerboard into a
    shape that is simultaneously one piece and many.

    Components are numbered 1..N **in raster order of their first pixel**, so the
    same picture always produces the same island ids.
    """
    rows, starts, ends = _runs(mask)
    count = len(rows)
    labels = _np.zeros(mask.shape, dtype="i4")
    if not count:
        return labels, 0, _np.zeros(0, dtype="i8"), []

    union = _Union(count)
    # Runs arrive in raster order, so the runs of each row are contiguous.
    row_starts = _np.searchsorted(rows, _np.arange(mask.shape[0] + 1))
    touch = 0 if diagonal else 1  # how far apart two runs may sit and still touch
    for row in range(1, mask.shape[0]):
        above_lo, above_hi = row_starts[row - 1], row_starts[row]
        here_lo, here_hi = row_starts[row], row_starts[row + 1]
        a = above_lo
        for b in range(here_lo, here_hi):
            s, e = starts[b], ends[b]
            while a < above_hi and ends[a] + 1 - touch < s:
                a += 1
            probe = a
            while probe < above_hi and starts[probe] <= e + 1 - touch:
                union.union(int(probe), int(b))
                probe += 1

    roots = _np.array([union.find(i) for i in range(count)], dtype="i8")
    unique_roots = _np.unique(roots)  # sorted: the first run of each component
    remap = {int(root): index + 1 for index, root in enumerate(unique_roots)}
    run_label = _np.array([remap[int(root)] for root in roots], dtype="i4")

    areas = _np.zeros(len(unique_roots) + 1, dtype="i8")
    boxes = [[mask.shape[1], mask.shape[0], -1, -1]
             for _ in range(len(unique_roots) + 1)]
    for index in range(count):
        row, start, end, label = (int(rows[index]), int(starts[index]),
                                  int(ends[index]), int(run_label[index]))
        labels[row, start:end + 1] = label
        areas[label] += end - start + 1
        box = boxes[label]
        if start < box[0]:
            box[0] = start
        if row < box[1]:
            box[1] = row
        if end > box[2]:
            box[2] = end
        if row > box[3]:
            box[3] = row
    return labels, len(unique_roots), areas, boxes


def extract_islands(background, warnings):
    """``(solid, labels, count, areas, boxes, enclosed)`` for a background mask.

    ``solid`` is everything the background cannot reach from the frame border, so
    an enclosed pocket of background colour (an eye highlight on white, the hole
    in a doughnut) is part of the sprite.  ``enclosed`` counts those pixels,
    because "12 040 pixels of background were kept as card" is the sort of thing
    that should never be a surprise.
    """
    height, width = background.shape
    labels, count, areas, boxes = _label(background, diagonal=False)
    outside = _np.zeros(count + 1, dtype=bool)
    for label in range(1, count + 1):
        left, top, right, bottom = boxes[label]
        if left == 0 or top == 0 or right == width - 1 or bottom == height - 1:
            outside[label] = True
    reachable = outside[labels] & (labels > 0)
    solid = ~reachable
    enclosed = int(_np.count_nonzero(background & solid))
    if not reachable.any() and background.any():
        warnings.append(
            "every pixel that reads as background is walled in — no background "
            "region touches the frame border — so the whole picture was kept as "
            "card. That usually means the sprite runs off the edge of its frame.")
    island_labels, island_count, island_areas, island_boxes = _label(
        solid, diagonal=True)
    return (solid, island_labels, island_count, island_areas, island_boxes,
            enclosed)


# ---------------------------------------------------------------------------
# 4. the contour — chained boundary segments on the pixel corner grid
# ---------------------------------------------------------------------------

def _boundary_segments(mask):
    """Directed boundary segments of ``mask`` on the corner grid.

    A subject pixel at ``(row, col)`` occupies the square
    ``[col, col+1] x [row, row+1]`` in corner coordinates.  Each side whose
    neighbour is not subject contributes one directed segment, wound so the
    subject is on the right of the direction of travel::

        top    (c, r)     -> (c+1, r)
        right  (c+1, r)   -> (c+1, r+1)
        bottom (c+1, r+1) -> (c, r+1)
        left   (c, r+1)   -> (c, r)

    Every corner then has as many segments leaving it as arriving, which is what
    makes the chaining below terminate.
    """
    height, width = mask.shape
    padded = _np.zeros((height + 2, width + 2), dtype=bool)
    padded[1:height + 1, 1:width + 1] = mask
    inside = padded[1:height + 1, 1:width + 1]
    up = inside & ~padded[0:height, 1:width + 1]
    down = inside & ~padded[2:height + 2, 1:width + 1]
    left = inside & ~padded[1:height + 1, 0:width]
    right = inside & ~padded[1:height + 1, 2:width + 2]

    segments = []
    for flags, (sx, sy, ex, ey) in (
            (up,    (0, 0, 1, 0)),
            (right, (1, 0, 1, 1)),
            (down,  (1, 1, 0, 1)),
            (left,  (0, 1, 0, 0))):
        rows, cols = _np.nonzero(flags)
        if not len(rows):
            continue
        segments.append(_np.stack([cols + sx, rows + sy, cols + ex, rows + ey],
                                  axis=1))
    if not segments:
        return _np.zeros((0, 4), dtype="i8")
    return _np.concatenate(segments, axis=0).astype("i8")


def trace_contour(mask):
    """Closed contours of a binary mask, as lists of ``(x, y)`` corner points.

    The walk starts at the lexicographically smallest corner, so the same mask
    always produces the same list starting at the same point.  Where two blobs
    meet diagonally a corner has two ways out; the walk takes the one with the
    smallest cross product against the direction it arrived on, which crosses
    into the other blob and keeps an 8-connected island a single loop.
    """
    segments = _boundary_segments(mask)
    if not len(segments):
        return []
    outgoing = {}
    for sx, sy, ex, ey in segments.tolist():
        outgoing.setdefault((sx, sy), []).append((ex, ey))
    for point in outgoing:
        outgoing[point].sort()

    loops = []
    for start in sorted(outgoing):
        while outgoing.get(start):
            loop = [start]
            point = start
            direction = None
            while True:
                choices = outgoing.get(point)
                if not choices:  # pragma: no cover - degrees are balanced
                    break
                if len(choices) == 1 or direction is None:
                    nxt = choices[0]
                else:
                    best = None
                    for candidate in choices:
                        step = (candidate[0] - point[0], candidate[1] - point[1])
                        cross = direction[0] * step[1] - direction[1] * step[0]
                        key = (cross, candidate)
                        if best is None or key < best[0]:
                            best = (key, candidate)
                    nxt = best[1]
                choices.remove(nxt)
                if not choices:
                    del outgoing[point]
                direction = (nxt[0] - point[0], nxt[1] - point[1])
                point = nxt
                if point == start:
                    break
                loop.append(point)
            if len(loop) >= 3:
                loops.append(loop)
    return loops


def split_pinches(points):
    """Split a traced loop into SIMPLE loops at every corner it visits twice.

    Returns ``(loops, dropped)``.

    Why this exists, measured the expensive way (2026-09-21).  An 8-connected
    island pinched at a corner traces as one loop that passes through that
    corner twice — a *non-simple* polygon.  Blender's ear clipper does not
    refuse such a polygon, it **silently gives up part way**: on this add-on's
    harpy fixture the traced ring has 105 pinch corners, and
    ``tessellate_polygon`` returned triangles covering **38.8% of the ring's
    area** (14 288 triangles where a simple 19 782-gon must give 19 780).  The
    card built from it had a cap over two fifths of the sprite and empty space
    over the rest — which renders as a floating outline, and which every count
    in the report agreed with, because the counts were counting the outline.

    Splitting at the repeats is both the fix and the honest topology: a pinched
    blob really is two regions that touch at a point.  Measured on the same
    fixture: 106 simple loops, every one triangulating to exactly ``N − 2``,
    total area 671 469.0 — **the pixel count, exactly**.

    The walk is a stack: each point is pushed, and a point already on the stack
    closes the loop above it.  Loops of fewer than three points enclose nothing
    (a one-pixel filament doubling back on itself) and are dropped and counted.
    """
    loops = []
    dropped = 0
    stack = []
    seen = {}
    for point in points:
        if point in seen:
            start = seen[point]
            loop = stack[start:]
            del stack[start:]
            for gone in loop:
                seen.pop(gone, None)
            if len(loop) >= 3:
                loops.append(loop)
            else:
                dropped += 1
        seen[point] = len(stack)
        stack.append(point)
    if len(stack) >= 3:
        loops.append(stack)
    elif stack:
        dropped += 1
    return loops, dropped


def _drop_collinear(points):
    """Remove points that sit exactly on the segment between their neighbours.

    Integer corner coordinates, so "exactly" is exact: this is free accuracy,
    and it takes a 1 000-pixel straight edge from 1 000 points to 2 before any
    tolerance is spent.
    """
    count = len(points)
    if count < 3:
        return list(points)
    kept = []
    for index in range(count):
        previous = points[index - 1]
        current = points[index]
        nxt = points[(index + 1) % count]
        ax, ay = current[0] - previous[0], current[1] - previous[1]
        bx, by = nxt[0] - current[0], nxt[1] - current[1]
        if ax * by - ay * bx != 0:
            kept.append(current)
    return kept if len(kept) >= 3 else list(points)


def _douglas_peucker(points, tolerance):
    """Open-chain Douglas-Peucker, iterative (a 100 000-point chain is legal)."""
    count = len(points)
    if count < 3:
        return list(points)
    keep = [False] * count
    keep[0] = keep[count - 1] = True
    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        ax, ay = points[first]
        bx, by = points[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst, at = -1.0, -1
        for index in range(first + 1, last):
            px, py = points[index]
            if length <= 1e-12:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dx * (ay - py) - (ax - px) * dy) / length
            if distance > worst:
                worst, at = distance, index
        if worst > tolerance and at > first:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))
    return [points[index] for index in range(count) if keep[index]]


def simplify_closed(points, tolerance):
    """Douglas-Peucker on a closed ring, split at two fixed, far-apart points.

    A closed ring has no ends to anchor the recursion on, so it is cut into two
    chains at its first point (the lexicographically smallest corner, where the
    trace started) and at the point furthest from it — both derived from the ring
    itself, so the cut is the same on every run and the result does not depend on
    where the trace happened to begin.
    """
    points = _drop_collinear(points)
    count = len(points)
    if count < 4 or tolerance <= 0.0:
        return points
    ax, ay = points[0]
    far, at = -1.0, 0
    for index in range(1, count):
        px, py = points[index]
        distance = (px - ax) ** 2 + (py - ay) ** 2
        if distance > far:
            far, at = distance, index
    first = _douglas_peucker(points[0:at + 1], tolerance)
    second = _douglas_peucker(points[at:] + [points[0]], tolerance)
    ring = first[:-1] + second[:-1]
    return ring if len(ring) >= 3 else points


def _signed_area(ring):
    """Twice the signed area of a ring, in whatever coordinates it is given."""
    total = 0.0
    count = len(ring)
    for index in range(count):
        x0, y0 = ring[index]
        x1, y1 = ring[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return total


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------

def _fan_triangles(ring, area2):
    """Triangulate a cap ring. Returns ``(triangles, degenerate_dropped)``.

    ``tessellate_polygon`` is Blender's own ear clipper: deterministic for
    identical input and correct for the concave outline a sprite always has.  It
    also emits **zero-area slivers** wherever three non-adjacent contour points
    happen to be collinear — measured on this add-on's own test cross, where 2 of
    the 10 triangles it returns have exactly no area (the three points sit on one
    horizontal edge of the cross, two of them not neighbours).  A sliver covers
    nothing, carries no normal, and inflates every triangle count downstream, so
    it is dropped here and counted in the report.

    "Zero area" is measured against the ring's own area rather than an absolute
    epsilon, so the test means the same thing on a 30 mm sprite and a 30 m one.
    """
    from mathutils import Vector
    from mathutils.geometry import tessellate_polygon

    polygon = [Vector((float(x), float(y), 0.0)) for x, y in ring]
    triangles = tessellate_polygon([polygon])
    if not triangles:  # pragma: no cover - a >=3 point ring always tessellates
        triangles = [(0, index, index + 1) for index in range(1, len(ring) - 1)]
    epsilon = abs(area2) * 1e-9
    kept = []
    dropped = 0
    for tri in triangles:
        ax, ay = ring[tri[0]]
        bx, by = ring[tri[1]]
        cx, cy = ring[tri[2]]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) <= epsilon:
            dropped += 1
            continue
        kept.append(tuple(int(i) for i in tri))
    return kept, dropped


def _build_card(rings, thickness, bevel, segments, warnings):
    """``(vertices, faces, stats)`` for the extruded cards of every ring.

    ``rings`` are already in metres, in the world XZ plane, wound so the front
    cap's normal comes out −Y (the direction the sprite was drawn facing).
    """
    import bmesh

    half = thickness * 0.5
    bm = bmesh.new()
    rim_edges = []
    front_faces = []
    back_faces = []
    slivers = 0
    cap_area = 0.0
    ring_area = 0.0
    for ring in rings:
        front = [bm.verts.new((x, -half, z)) for x, z in ring]
        back = [bm.verts.new((x, half, z)) for x, z in ring]
        count = len(ring)
        area2 = _signed_area(ring)
        ring_area += 0.5 * abs(area2)
        triangles, dropped = _fan_triangles(ring, area2)
        slivers += dropped
        for tri in triangles:
            ax, az = ring[tri[0]]
            bx, bz = ring[tri[1]]
            cx, cz = ring[tri[2]]
            cap_area += 0.5 * abs((bx - ax) * (cz - az) - (bz - az) * (cx - ax))
        for tri in triangles:
            front_faces.append(bm.faces.new([front[i] for i in tri]))
            back_faces.append(bm.faces.new([back[i] for i in reversed(tri)]))
        for index in range(count):
            nxt = (index + 1) % count
            # (front[i], back[i], back[i+1], front[i+1]) is the winding whose
            # normal points away from the card; the other one points into it.
            bm.faces.new((front[index], back[index], back[nxt], front[nxt]))
        bm.verts.index_update()
        bm.edges.ensure_lookup_table()
        for index in range(count):
            nxt = (index + 1) % count
            for ring_verts in (front, back):
                edge = bm.edges.get((ring_verts[index], ring_verts[nxt]))
                if edge is not None:
                    rim_edges.append(edge)

    bevelled = 0
    if bevel > 0.0 and rim_edges:
        try:
            result = bmesh.ops.bevel(
                bm, geom=rim_edges, offset=bevel, offset_type="OFFSET",
                segments=segments, profile=0.5, affect="EDGES",
                clamp_overlap=True)
            bevelled = len(result.get("faces") or ())
        except (TypeError, ValueError, RuntimeError) as exc:
            warnings.append(
                "the bevel was skipped: this Blender's bmesh.ops.bevel refused "
                "the call (%s: %s). The card is built square-edged; everything "
                "else in this report is unaffected."
                % (type(exc).__name__, exc))

    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.ensure_lookup_table()
    bm.normal_update()  # face.normal is lazy; without this it reads (0, 0, 0)
    vertices = [(v.co.x, v.co.y, v.co.z) for v in bm.verts]
    faces = [[v.index for v in f.verts] for f in bm.faces]
    front_normal = None
    if front_faces:
        try:
            front_normal = tuple(round(float(v), 6) for v in front_faces[0].normal)
        except ReferenceError:  # pragma: no cover - bevel may replace the face
            front_normal = None
    bm.free()

    triangle_count = sum(len(face) - 2 for face in faces)
    stats = {
        "cap_triangles": len(front_faces) + len(back_faces),
        "side_quads": sum(len(ring) for ring in rings),
        "bevel_faces": bevelled,
        "triangles": triangle_count,
        "slivers_dropped": slivers * 2,  # the front's and the back's
        "front_normal": front_normal,
        "cap_area": cap_area,
        "ring_area": ring_area,
    }
    return vertices, faces, stats


# ---------------------------------------------------------------------------
# UVs and materials
# ---------------------------------------------------------------------------

def _assign_uvs(mesh, centre_x, baseline, scale, width_px, height_px):
    """Planar projection from the front, onto the source image's own pixels.

    The inverse of the mapping that built the card: ``x = (px − centre_x)·scale``
    and ``z = (baseline − py)·scale`` go back to pixel coordinates, then
    ``u = px / width`` and ``v = 1 − py / height``.  Computed per loop from its
    own vertex position, which is why a bevel needs no special case — a
    projection does not care how the geometry got there.
    """
    layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    loop_count = len(mesh.loops)
    if not loop_count:  # pragma: no cover - defensive
        return layer.name, [0.0, 0.0, 0.0, 0.0]
    vertex_index = _np.empty(loop_count, dtype="i4")
    mesh.loops.foreach_get("vertex_index", vertex_index)
    coords = _np.empty(len(mesh.vertices) * 3, dtype="f8")
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)[vertex_index]
    px = coords[:, 0] / scale + centre_x
    py = baseline - coords[:, 2] / scale
    uvs = _np.stack([px / float(width_px), 1.0 - py / float(height_px)], axis=1)
    flat = uvs.astype("f4").ravel()
    try:
        layer.data.foreach_set("uv", flat)
    except (AttributeError, RuntimeError):  # pragma: no cover - 4.x/5.x differ
        layer.uv.foreach_set("vector", flat)
    mesh.update()
    return layer.name, [_round(float(uvs[:, 0].min()), 5),
                        _round(float(uvs[:, 1].min()), 5),
                        _round(float(uvs[:, 0].max()), 5),
                        _round(float(uvs[:, 1].max()), 5)]


def _cutout_image(name, pixels, solid, source_path):
    """An RGBA image datablock: the source's colours, the extraction's alpha.

    A sprite on a flat plate has no alpha to clip, so the card would wear its own
    background in the fringe between the traced outline and the art.  This builds
    the texture the card actually needs — the source RGB untouched, alpha 1 where
    the extraction says sprite and 0 where it says plate — as a NEW datablock.
    The file on disk is never written to.
    """
    height, width = solid.shape
    image = bpy.data.images.get(name)
    if image is not None:
        bpy.data.images.remove(image)
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    rgba = _np.ones((height, width, 4), dtype="f4")
    rgba[:, :, :3] = pixels[:, :, :3]
    rgba[:, :, 3] = solid.astype("f4")
    # Blender's pixel buffer runs bottom-up; the mask and the source array are
    # top-down (``verify._image_pixels`` flips on the way in).
    image.pixels.foreach_set(rgba[::-1].reshape(-1))
    image.update()
    try:
        image.pack()
    except (AttributeError, RuntimeError) as exc:  # pragma: no cover
        print("[Forge] sprite_cutout could not pack %r (%s)" % (name, exc))
    image["forge_sprite_source"] = source_path
    return image


def _relief_image(name, pixels, solid, strength):
    """A tangent-space normal map off the picture's own luminance.

    ``n = normalise((−dL/dx · k, −dL/dy · k, 1))`` over the whole picture, forced
    flat outside the silhouette, with central differences and
    ``k = strength × 8`` (eight is the scale at which a full-swing 0→1 luminance
    step over one pixel tilts the normal ~83°, so ``strength = 1`` is the
    steepest the trick is worth and ``0.25`` is the usable look).  Encoded the
    way every engine reads a normal map: ``0.5 + 0.5·n``, alpha 1, Non-Color,
    into an 8-bit datablock — so a flat pixel reads back as 128/255 = 0.501961
    rather than 0.5, which is the encoding and not an error.
    """
    height, width = solid.shape
    luma = (pixels[:, :, 0] * LUMA[0] + pixels[:, :, 1] * LUMA[1]
            + pixels[:, :, 2] * LUMA[2])
    # The gradient is taken from the picture AS IT IS and the result is forced
    # flat outside the silhouette afterwards. Masking the luminance first was
    # tried and is wrong twice over, measured: it invents a cliff all the way
    # round a bright sprite, and on a dark one (a black cross on white) it makes
    # the whole field zero and the relief vanishes completely.
    dx = _np.zeros_like(luma)
    dy = _np.zeros_like(luma)
    dx[:, 1:-1] = (luma[:, 2:] - luma[:, :-2]) * 0.5
    dy[1:-1, :] = (luma[2:, :] - luma[:-2, :]) * 0.5
    k = strength * 8.0
    nx, ny, nz = -dx * k, -dy * k, _np.ones_like(luma)
    length = _np.sqrt(nx * nx + ny * ny + nz * nz)
    rgba = _np.ones((height, width, 4), dtype="f4")
    rgba[:, :, 0] = 0.5 + 0.5 * (nx / length)
    # +Y in a normal map points UP the image, which is -row.
    rgba[:, :, 1] = 0.5 - 0.5 * (ny / length)
    rgba[:, :, 2] = 0.5 + 0.5 * (nz / length)
    # Outside the sprite nothing is drawn, so nothing should be tilted.
    flat = ~solid
    rgba[flat, 0] = 0.5
    rgba[flat, 1] = 0.5
    rgba[flat, 2] = 1.0
    image = bpy.data.images.get(name)
    if image is not None:
        bpy.data.images.remove(image)
    image = bpy.data.images.new(name, width=width, height=height, alpha=True,
                                is_data=True)
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(rgba[::-1].reshape(-1))
    image.update()
    try:
        image.pack()
    except (AttributeError, RuntimeError) as exc:  # pragma: no cover
        print("[Forge] sprite_cutout could not pack %r (%s)" % (name, exc))
    return image


def _alpha_clip(material, tree, texture, principled, warnings):
    """Clip the alpha in the NODE TREE, and say what the material settings did.

    Measured on Blender 5.0.1 rather than assumed, because the obvious call no
    longer does anything:

    * ``material.blend_method = 'CLIP'`` **silently becomes ``'HASHED'``**.
      EEVEE Next replaced the old blend modes with ``surface_render_method``
      (``DITHERED`` / ``BLENDED``) and ``blend_method`` survives only as a
      two-value alias — setting ``CLIP`` and reading it back returns ``HASHED``,
      every time.
    * The glTF exporter no longer reads ``blend_method`` at all.  It looks at the
      **nodes** (``search_node_tree.detect_alpha_clip``): a ``Math`` node with
      ``GREATER_THAN`` (or ``ROUND``) feeding Principled's Alpha is what makes it
      write ``alphaMode: "MASK"`` with the cutoff.  Without that node a sprite
      exports as ``alphaMode: "BLEND"``, which in Godot is a sorted transparent
      surface instead of an alpha-scissored one — measured, in this add-on's own
      suite, by reading the .glb back.

    So the cut is a node: ``alpha > ALPHA_CUTOFF``.  It is binary in EEVEE, in
    Cycles and in the glTF, one implementation for all three, and the material's
    own render method is set beside it (and reported as it reads back, not as it
    was asked for) so the viewport agrees with the export.
    """
    clip = tree.nodes.new("ShaderNodeMath")
    clip.operation = "GREATER_THAN"
    clip.name = "Forge Alpha Clip"
    clip.label = "Alpha clip"
    clip.location = (-200.0, 120.0)
    clip.inputs[1].default_value = ALPHA_CUTOFF
    tree.links.new(texture.outputs["Alpha"], clip.inputs[0])
    tree.links.new(clip.outputs["Value"], principled.inputs["Alpha"])

    applied = {
        "node": claim({"type": "MATH", "operation": clip.operation,
                       "cutoff": _round(clip.inputs[1].default_value, 4)},
                      MEASURED,
                      "read back off the node that was built — this is what "
                      "makes the glTF exporter write alphaMode MASK"),
    }
    properties = set(material.bl_rna.properties.keys())
    if "surface_render_method" in properties:
        try:
            material.surface_render_method = "DITHERED"
            applied["surface_render_method"] = material.surface_render_method
        except (TypeError, ValueError):  # pragma: no cover
            pass
    if "blend_method" in properties:
        try:
            material.blend_method = "CLIP"
        except (TypeError, ValueError):  # pragma: no cover
            pass
        # Read back, never assumed: on 5.0 this says HASHED however it was set.
        applied["blend_method"] = material.blend_method
    if "alpha_threshold" in properties:
        material.alpha_threshold = ALPHA_CUTOFF
        applied["alpha_threshold"] = _round(material.alpha_threshold, 4)
    if "use_transparent_shadow" in properties:
        material.use_transparent_shadow = True
        applied["use_transparent_shadow"] = bool(material.use_transparent_shadow)
    if len(applied) == 1:  # pragma: no cover - every build has at least one
        warnings.append(
            "this Blender build exposes neither 'surface_render_method' nor "
            "'blend_method', so only the node-level alpha cut is in place. It is "
            "the one that exports, but the viewport may still sort the card as "
            "an opaque surface.")
    return applied


def _sprite_material(name, image, relief, warnings):
    """A Principled BSDF wearing ``image``, cut rather than blended."""
    material = bpy.data.materials.get(name)
    if material is not None:
        bpy.data.materials.remove(material)
    material = bpy.data.materials.new(name)
    # ``use_nodes`` is deprecated in 5.0 (a material arrives with a tree), so it
    # is only touched on a build that did not give it one.
    if getattr(material, "node_tree", None) is None:
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        if node.type not in ("OUTPUT_MATERIAL", "BSDF_PRINCIPLED"):
            tree.nodes.remove(node)
    principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if principled is None:
        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    if output is None:  # pragma: no cover - a new material always has one
        output = tree.nodes.new("ShaderNodeOutputMaterial")
    if not any(link.to_node is output for link in tree.links):
        tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    # Pixel art is not supposed to be smooth: Closest is the whole point, and
    # CLIP keeps a UV that lands a hair outside the frame from wrapping the
    # sprite's opposite edge into its own silhouette.
    texture.interpolation = "Closest"
    texture.extension = "CLIP"
    texture.location = (-420.0, 300.0)
    tree.links.new(texture.outputs["Color"], principled.inputs["Base Color"])

    settings = {}
    for socket, value in (("Roughness", 1.0), ("Metallic", 0.0),
                          ("Specular IOR Level", 0.0)):
        if socket in principled.inputs:
            principled.inputs[socket].default_value = value
            settings[socket] = value
    material.use_backface_culling = False

    relief_node = None
    if relief is not None:
        relief_node = tree.nodes.new("ShaderNodeTexImage")
        relief_node.image = relief
        relief_node.interpolation = "Closest"
        relief_node.extension = "CLIP"
        relief_node.location = (-420.0, -120.0)
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-200.0, -120.0)
        tree.links.new(relief_node.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    applied = _alpha_clip(material, tree, texture, principled, warnings)
    return material, texture, settings, applied


def _flat_material(name, colour):
    """The plain-colour back, when the caller asks for one."""
    material = bpy.data.materials.get(name)
    if material is not None:
        bpy.data.materials.remove(material)
    material = bpy.data.materials.new(name)
    if getattr(material, "node_tree", None) is None:
        material.use_nodes = True
    material.diffuse_color = (colour[0], colour[1], colour[2], 1.0)
    principled = next((n for n in material.node_tree.nodes
                       if n.type == "BSDF_PRINCIPLED"), None)
    if principled is not None:
        principled.inputs["Base Color"].default_value = (colour[0], colour[1],
                                                         colour[2], 1.0)
        if "Roughness" in principled.inputs:
            principled.inputs["Roughness"].default_value = 1.0
    return material


def _assign_back_slot(mesh, slot):
    """Point every back-facing polygon at material ``slot``. Returns the count."""
    count = len(mesh.polygons)
    if not count:  # pragma: no cover - defensive
        return 0
    normals = _np.empty(count * 3, dtype="f8")
    mesh.polygons.foreach_get("normal", normals)
    normals = normals.reshape(count, 3)
    centres = _np.empty(count * 3, dtype="f8")
    mesh.polygons.foreach_get("center", centres)
    centres = centres.reshape(count, 3)
    # A back cap is the only thing that both faces +Y and sits on the +Y side.
    back = (normals[:, 1] > 0.999) & (centres[:, 1] > 0.0)
    indices = _np.zeros(count, dtype="i4")
    indices[back] = slot
    mesh.polygons.foreach_set("material_index", indices)
    mesh.update()
    return int(_np.count_nonzero(back))


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

@command("sprite_cutout")
def cmd_sprite_cutout(params):
    """Turn a sprite image into a shallow extruded card wearing that image.

    Parameters
    ----------
    ``image``
        Path to the picture. Any format Blender reads (PNG, WebP, JPEG, ...).
    ``name`` (or ``object``)
        What to call the card in the scene. Default ``"Sprite <filename>"``.
    ``collection``
        Collection to build in. Default: the scene's own.
    ``height_m``
        World height of the SPRITE (its subject bounding box, never the picture's
        frame), in metres. Default 1.0 — one Blender/Godot unit, so the number
        asked for is the number the engine gets. Width follows from the sprite's
        pixel aspect.
    ``thickness_fraction``
        Card thickness as a fraction of ``height_m``. Default 0.03.
    ``bevel``
        Rim bevel as a fraction of the thickness, 0 (default, a square edge) to
        0.5 (a fully chamfered rim). ``bevel_segments`` (1-8, default 1) rounds it.
    ``islands``
        ``"all"`` (default) keeps every separated piece of the sprite as part of
        one card; ``"largest"`` keeps only the biggest.
    ``min_island_fraction``
        Islands under this fraction of the largest island's pixel area are
        speckle and are dropped. Default 0.001; 0 keeps everything.
    ``simplify_px``
        Douglas-Peucker tolerance in SOURCE PIXELS. Default 0.5 — half a pixel.
        0 keeps the exact pixel boundary (collinear runs are still merged, which
        is lossless).
    ``threshold``
        Background colour tolerance, overriding the derived default (0.04). Only
        consulted when the picture has no usable alpha channel.
    ``back``
        ``"mirror"`` (default) — the back is the front's own projection, so it
        reads mirrored, one material, one draw call. ``"flat_color"`` gives the
        back cap a second, plain-colour material.
    ``back_color``
        ``[r, g, b]`` for ``back="flat_color"``. Default: the median colour of
        the sprite's own pixels, measured.
    ``relief``
        0 (default, off) to 1. Builds a normal map from the picture's own
        luminance and wires it in. Fabricated shading, not depth — see
        ``honesty``.

    Returns a report whose load-bearing numbers are ``islands`` (found, kept,
    each with its pixel area), ``contour.points_traced`` and
    ``contour.points_simplified``, ``geometry.triangles``,
    ``geometry.thickness_mm``, ``texture.size`` and ``material.alpha``.
    """
    started = time.monotonic()
    _require_numpy()
    warnings = []
    notes = []

    path = common.resolve_path(get_str(params, "image"))
    if not os.path.isfile(path):
        raise ForgeError(
            "No image at %r to cut a sprite out of. Give the full path to a "
            "picture file (PNG, WebP, JPEG, ...)." % path)

    height_m = get_float(params, "height_m", DEFAULT_HEIGHT_M, minimum=1e-4,
                         maximum=1000.0)
    thickness_fraction = get_float(params, "thickness_fraction",
                                   THICKNESS_FRACTION, minimum=1e-4, maximum=1.0)
    bevel_fraction = get_float(params, "bevel", 0.0, minimum=0.0, maximum=0.5)
    bevel_segments = get_int(params, "bevel_segments", BEVEL_SEGMENTS,
                             minimum=1, maximum=8)
    simplify_px = get_float(params, "simplify_px", SIMPLIFY_PX, minimum=0.0,
                            maximum=64.0)
    tolerance = get_float(params, "threshold", BACKGROUND_TOLERANCE,
                          minimum=0.0, maximum=2.0)
    min_island_fraction = get_float(params, "min_island_fraction",
                                    MIN_ISLAND_FRACTION, minimum=0.0, maximum=1.0)
    relief_strength = get_float(params, "relief", 0.0, minimum=0.0, maximum=1.0)
    islands_mode = _choice(params, "islands", ("all", "largest"), "all")
    back_mode = _choice(params, "back", ("mirror", "flat_color"), "mirror")
    stem = os.path.splitext(os.path.basename(path))[0]
    # ``object`` is accepted as a spelling of ``name`` because every other
    # command in this add-on takes ``object`` to mean "the object this is
    # about", and a caller who writes it here should not silently get a card
    # called something else.
    raw_name = params.get("name", params.get("object"))
    if raw_name is None:
        name = "Sprite %s" % stem
    elif not isinstance(raw_name, str) or not raw_name.strip():
        raise ForgeError(
            "'name' is what to call the card in the scene, so it has to be a "
            "non-empty string; got %r. Leave it out for %r."
            % (raw_name, "Sprite %s" % stem))
    else:
        name = raw_name.strip()
    collection = params.get("collection")
    if collection is not None and not isinstance(collection, str):
        raise ForgeError("'collection' must be a string, got %s."
                         % type(collection).__name__)

    back_colour = params.get("back_color")
    if back_colour is not None:
        if (not isinstance(back_colour, (list, tuple)) or len(back_colour) != 3):
            raise ForgeError("'back_color' must be [r, g, b] in 0-1, got %r."
                             % (back_colour,))
        back_colour = [float(get_float({"c": v}, "c", minimum=0.0, maximum=1.0))
                       for v in back_colour]

    # --- the picture --------------------------------------------------------
    width, height, channels, pixels = verify._image_pixels(path)
    background, background_report, alpha_used = _background_mask(
        pixels, channels, tolerance)
    if background.all():
        raise ForgeError(
            "Every pixel of %s reads as background, so there is no sprite to cut "
            "out. %s" % (os.path.basename(path),
                         "Check the alpha channel." if alpha_used else
                         "Raise 'threshold' if the sprite is nearly the colour "
                         "of its plate, or crop the picture to the art."))

    solid, labels, island_count, areas, boxes, enclosed = extract_islands(
        background, warnings)
    if island_count == 0:  # pragma: no cover - guarded by the check above
        raise ForgeError("Nothing could be separated from the background in %s."
                         % os.path.basename(path))

    order = sorted(range(1, island_count + 1), key=lambda i: (-int(areas[i]), i))
    largest = int(areas[order[0]])
    minimum_area = max(1, int(math.ceil(min_island_fraction * largest)))
    kept = [index for index in order
            if index == order[0] or int(areas[index]) >= minimum_area]
    if islands_mode == "largest":
        kept = kept[:1]
    dropped = [index for index in order if index not in set(kept)]
    if dropped:
        notes.append(
            "%d island(s) under %d px (%.4f of the largest island's %d px) were "
            "dropped as speckle: %s"
            % (len(dropped), minimum_area, min_island_fraction, largest,
               ", ".join(str(int(areas[i])) for i in dropped[:12])
               + (", ..." if len(dropped) > 12 else "")))

    keep_mask = _np.isin(labels, _np.array(kept, dtype="i4"))
    rows, cols = _np.nonzero(keep_mask)
    left, right = int(cols.min()), int(cols.max())
    top, bottom = int(rows.min()), int(rows.max())
    sprite_w_px = float(right - left + 1)
    sprite_h_px = float(bottom - top + 1)

    # --- the outline --------------------------------------------------------
    traced_points = 0
    traced_loops = 0
    pinch_points = 0
    filaments = 0
    rings_px = []
    for index in kept:
        box = boxes[index]
        sub = keep_mask[box[1]:box[3] + 1, box[0]:box[2] + 1] & (
            labels[box[1]:box[3] + 1, box[0]:box[2] + 1] == index)
        loops = trace_contour(sub)
        if len(loops) > 1:
            warnings.append(
                "island %d traced as %d separate loops rather than one; all of "
                "them were kept. That happens when the island encloses a region "
                "the flood fill could reach from outside." % (index, len(loops)))
        for loop in loops:
            traced_points += len(loop)
            traced_loops += 1
            pinch_points += len(loop) - len(set(loop))
            # Every ring handed on from here is SIMPLE. A pinched one is not
            # triangulable — see split_pinches for what that cost.
            parts, spurs = split_pinches(
                [(x + box[0], y + box[1]) for x, y in loop])
            filaments += spurs
            rings_px.extend(parts)
    if pinch_points:
        notes.append(
            "the outline touches itself at %d corner(s) where the sprite is "
            "joined diagonally; it was split there into %d simple rings, which "
            "is what those corners actually are — two regions meeting at a point"
            % (pinch_points, len(rings_px)))
    if filaments:
        notes.append(
            "%d zero-area filament(s) (a one-pixel spur doubling back on "
            "itself) were dropped: they enclose nothing to build a card from"
            % filaments)

    if not rings_px:  # pragma: no cover - a non-empty island always traces
        raise ForgeError("The sprite's outline could not be traced.")

    tolerance_px = simplify_px
    rings_simple = [simplify_closed(ring, tolerance_px) for ring in rings_px]
    simplified_points = sum(len(ring) for ring in rings_simple)
    if simplified_points > MAX_CONTOUR_POINTS:
        raise ForgeError(
            "The outline simplifies to %d points, past this command's %d cap — "
            "that is %d vertices of card. Raise 'simplify_px' (it is %g source "
            "pixels now) or crop the picture."
            % (simplified_points, MAX_CONTOUR_POINTS, simplified_points * 2,
               tolerance_px))

    # --- pixels to metres ---------------------------------------------------
    # The sprite's own bounding box is the ruler, in CORNER coordinates: pixel
    # ``left`` starts at corner ``left`` and pixel ``right`` ends at ``right+1``.
    scale = height_m / sprite_h_px
    centre_x = 0.5 * (left + right + 1)
    baseline = float(bottom + 1)
    rings_m = []
    for ring in rings_simple:
        metres = [((x - centre_x) * scale, (baseline - y) * scale)
                  for x, y in ring]
        # Wound so the front cap's normal comes out -Y: counter-clockwise seen
        # from -Y is positive signed area in (x right, z up).
        if _signed_area(metres) < 0.0:
            metres.reverse()
        rings_m.append(metres)

    # What the simplification cost, measured rather than asserted: the area the
    # simplified outline encloses against the area of the pixels it was traced
    # from. At simplify_px = 0 these are equal to the last digit, because the
    # traced ring IS the pixel boundary.
    outline_area = 0.5 * sum(abs(_signed_area(ring)) for ring in rings_m)
    pixel_area = int(_np.count_nonzero(keep_mask)) * scale * scale

    thickness = thickness_fraction * height_m
    bevel = bevel_fraction * thickness
    vertices, faces, card = _build_card(rings_m, thickness, bevel,
                                        bevel_segments, warnings)

    # THE INVARIANT THAT MATTERS: a cap that does not cover its own outline is
    # a card you can see through, and every other number in this report would
    # still be right. Measured off the triangles themselves, before anything is
    # put in the scene, so a failure leaves nothing behind.
    coverage = (card["cap_area"] / card["ring_area"]) if card["ring_area"] else 0.0
    if coverage < CAP_COVERAGE_FLOOR:
        raise ForgeError(
            "The card's face covers only %.2f%% of the outline it was built "
            "from (%.1f of %.1f mm2), so most of the sprite would render as a "
            "hole with a floating rim. The triangulator gave up on a ring that "
            "crosses itself — try 'simplify_px': 0 (the traced pixel boundary "
            "never crosses itself), or a smaller value than %g."
            % (coverage * 100.0, card["cap_area"] * M_TO_MM * M_TO_MM,
               card["ring_area"] * M_TO_MM * M_TO_MM, tolerance_px))

    with common.object_mode():
        obj, built = common.build_mesh_object(name, vertices, faces,
                                              collection=collection, scale=1.0)
        mesh = obj.data

        uv_layer_name, uv_bounds = _assign_uvs(
            mesh, centre_x, baseline, scale, width, height)

        # --- the texture ----------------------------------------------------
        if alpha_used:
            image = bpy.data.images.load(path, check_existing=True)
            texture_source = claim(
                "source file", MEASURED,
                "the picture already carries the alpha the card needs, so it is "
                "used as it is and nothing was generated")
        else:
            image = _cutout_image("%s Cutout" % name, pixels, keep_mask, path)
            texture_source = claim(
                "generated cutout", MEASURED,
                "the picture has no usable alpha, so a new image datablock was "
                "built from its own RGB with alpha taken from the extraction "
                "mask — the file on disk was not touched")
        image.alpha_mode = "STRAIGHT"

        relief_image = None
        if relief_strength > 0.0:
            relief_image = _relief_image("%s Relief" % name, pixels, keep_mask,
                                         relief_strength)

        material, texture_node, shading, alpha_applied = _sprite_material(
            "%s Card" % name, image, relief_image, warnings)
        mesh.materials.clear()
        mesh.materials.append(material)

        back_faces = 0
        back_material = None
        if back_mode == "flat_color":
            if back_colour is None:
                subject = pixels[:, :, :3][keep_mask]
                back_colour = [_round(float(v), 6)
                               for v in _np.median(subject, axis=0)]
                notes.append(
                    "no 'back_color' was given, so the back wears the median "
                    "colour of the sprite's own pixels: %s" % (back_colour,))
            back_material = _flat_material("%s Back" % name, back_colour)
            mesh.materials.append(back_material)
            back_faces = _assign_back_slot(mesh, 1)

        obj["forge_sprite_image"] = path
        obj["forge_sprite_height_m"] = height_m
        common.refresh_view_layer()

    dimensions = [_round(float(v) * M_TO_MM, 3) for v in obj.dimensions]
    bound = [float(v) for corner in obj.bound_box for v in corner]
    z_min = min(bound[2::3])

    # The biggest handful, largest first: enough to see the gap between the
    # sprite and the speckle without printing a page of three-pixel specks.
    keep_set = set(kept)
    island_report = []
    for index in order[:8]:
        box = boxes[index]
        island_report.append({
            "id": int(index),
            "pixels": int(areas[index]),
            "bbox_px": [box[0], box[1], box[2], box[3]],
            "kept": index in keep_set,
        })

    if not alpha_used:
        margin = background_report["margin"]
        notes.append(
            "the background threshold was checked against itself: at half the "
            "tolerance %.4f of the frame reads as background, at the tolerance "
            "%.4f, at double it %.4f — a swing of %.4f."
            % (margin["at_half_tolerance"], margin["at_tolerance"],
               margin["at_double_tolerance"], margin["swing"]))
        if background_report["border_uniformity"] < 0.9:
            warnings.append(
                "only %.0f%% of the border ring is the one colour the "
                "background was taken from, so the plate behind the sprite is "
                "not plain and the silhouette may have caught scenery."
                % (background_report["border_uniformity"] * 100.0))
    if enclosed:
        notes.append(
            "%d pixel(s) the colour of the background are walled in by the "
            "sprite (an eye highlight, the hole in a doughnut), so they were "
            "kept as card rather than punched out; the texture's alpha still "
            "shows them as the artist drew them." % enclosed)

    report = {
        "object": obj.name,
        "image": path,
        "image_size": [width, height],
        "image_channels": channels,
        "background": background_report,
        "islands": {
            "found": island_count,
            "kept": len(kept),
            "dropped": len(dropped),
            "min_island_px": minimum_area,
            "largest_px": largest,
            "enclosed_background_px": enclosed,
            "largest_dropped_px": (int(areas[dropped[0]]) if dropped else 0),
            "detail": island_report,
        },
        "contour": {
            "loops": traced_loops,
            "rings": len(rings_px),
            "pinch_points": pinch_points,
            "filaments_dropped": filaments,
            "points_traced": traced_points,
            "points_simplified": simplified_points,
            "simplify_px": _round(tolerance_px, 4),
            "simplify_mm": _round(tolerance_px * scale * M_TO_MM, 5),
            "reduction": _round(1.0 - simplified_points / float(traced_points), 4),
            "area_mm2": claim({
                "outline": _round(outline_area * M_TO_MM * M_TO_MM, 3),
                "pixels": _round(pixel_area * M_TO_MM * M_TO_MM, 3),
                "ratio": _round(outline_area / pixel_area, 6) if pixel_area else None,
            }, MEASURED,
                "the area the simplified outline encloses against the area of "
                "the pixels it was traced from — what the simplification cost, "
                "in the units the sprite is built in"),
        },
        "geometry": claim({
            "vertices": len(mesh.vertices),
            "faces": len(mesh.polygons),
            "triangles": card["triangles"],
            "cap_triangles": card["cap_triangles"],
            "side_quads": card["side_quads"],
            "bevel_faces": card["bevel_faces"],
            "slivers_dropped": card["slivers_dropped"],
            "cap_area_mm2": _round(card["cap_area"] * M_TO_MM * M_TO_MM, 3),
            "cap_coverage": _round(coverage, 6),
            "front_normal": card["front_normal"],
        }, MEASURED, "counted off the mesh that was built, not predicted; "
           "cap_coverage is the face's own triangle area against the outline "
           "it was built from, and 1.0 is the only passing answer"),
        "scale": {
            "height_m": _round(height_m, 6),
            "sprite_px": [int(sprite_w_px), int(sprite_h_px)],
            "sprite_bbox_px": [left, top, right, bottom],
            "mm_per_pixel": _round(scale * M_TO_MM, 6),
            "thickness_mm": _round(thickness * M_TO_MM, 4),
            "thickness_fraction": _round(thickness_fraction, 5),
            "bevel_mm": _round(bevel * M_TO_MM, 4),
            "bevel_segments": bevel_segments if bevel > 0.0 else 0,
            "dimensions_mm": dimensions,
            "stands_on_mm": _round(z_min * M_TO_MM, 4),
            "pixel_aspect": _round(sprite_w_px / sprite_h_px, 5),
        },
        "uv": {
            "layer": uv_layer_name,
            "projection": ("planar from -Y: u = x_px / %d, v = 1 - y_px / %d, "
                           "per loop, from the vertex's own position"
                           % (width, height)),
            "bounds": uv_bounds,
            "back": back_mode,
            "back_faces": back_faces,
            "back_color": back_colour if back_mode == "flat_color" else None,
            "rim": "samples the outline pixels the rim sits on",
        },
        "texture": {
            "source": texture_source,
            "image": image.name,
            "size": [int(image.size[0]), int(image.size[1])],
            "packed": bool(getattr(image, "packed_file", None)),
            "colorspace": image.colorspace_settings.name,
            "alpha_mode": image.alpha_mode,
        },
        "material": {
            "name": material.name,
            "back_material": back_material.name if back_material else None,
            "interpolation": texture_node.interpolation,
            "extension": texture_node.extension,
            "alpha": alpha_applied,
            "shading": shading,
            "relief": {
                "strength": _round(relief_strength, 4),
                "image": relief_image.name if relief_image else None,
            },
        },
        "built": built,
        "method": (
            "background = %s; only background connected to the frame border (4-"
            "connected) counts, so enclosed pockets stay card; the subject is "
            "split into 8-connected islands; each island's outline is traced "
            "along the pixel corner grid, split into simple rings at every "
            "corner it visits twice, and simplified with Douglas-Peucker at "
            "%g source pixels (%s mm); each ring is triangulated, extruded %s mm "
            "and %s; UVs are a planar projection from -Y onto the source "
            "image's own pixels."
            % ("the alpha channel at %g" % ALPHA_CUTOFF if alpha_used else
               "every pixel within %g of the border's most common colour"
               % tolerance,
               tolerance_px, _round(tolerance_px * scale * M_TO_MM, 5),
               _round(thickness * M_TO_MM, 4),
               "bevelled %s mm" % _round(bevel * M_TO_MM, 4) if bevel > 0.0
               else "left square-edged")),
        "honesty": HONESTY + (" " + RELIEF_HONESTY if relief_image else ""),
        "notes": list(dict.fromkeys(notes)),
        "warnings": list(dict.fromkeys(warnings)),
        "seconds": round(time.monotonic() - started, 3),
    }
    return report
