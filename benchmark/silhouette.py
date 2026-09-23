"""Silhouette-match fidelity: how closely a part's outline matches a reference.

The per-OBB metrics (mirror, volume ratio) pass a well-mirrored, well-filled
part of the wrong SHAPE — the artist rejected a pair of tall narrow blade ears
that passed both, pointing at the character sheet: "ears are more curved like
this".  This module turns "the outline matches the reference" into a number.

Pure Python, stdlib only (no ``bpy``, no numpy, no PIL), like
:mod:`benchmark.geometry`: it runs the same under Blender's Python, where the
artifact is measured, and under plain pytest.

The pipeline, the same for both sides:

1. **an outline polygon** — for the reference, extracted from the frozen image
   by algorithm (:func:`extract_ear_outline`, below); for a mesh, the evaluated
   triangles projected onto the part's widest OBB plane (normal = the surface's
   thinnest principal axis — the blade face), rasterized into a working grid by
   exact pixel-centre sampling, and the union's boundary traced on the pixel
   corner grid (:func:`mesh_outline`).  Not a convex hull: an ear's curve is
   concave, and a hull would erase exactly the property being measured;
2. **normalized** (:func:`normalize`) — area centroid at the origin, the
   polygon's own 2D principal (long) axis vertical and signed toward ``up``
   (the image's up for the reference, the world up projected into the blade
   plane for a mesh), scaled to unit height along that axis.  Width is NOT
   normalized: the width-to-height ratio is part of the shape;
3. **rasterized** on a fixed :data:`GRID` x :data:`GRID` canonical grid over
   ``[-1, 1]^2`` by pixel-centre even-odd scanline fill (rows are Python int
   bitmasks), and scored by **intersection over union**, taking the better of
   the candidate and its left-right mirror — a left ear and a right ear are
   mirror images, so the score is mirror-invariant by construction.

Regenerate the frozen reference outline (byte-stable; the pytest suite checks
the committed file against a fresh extraction)::

    service\\.venv\\Scripts\\python.exe benchmark\\silhouette.py --write-ear-outline
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import sys
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, os.pardir))

Point = Tuple[float, float]

#: canonical raster: GRID x GRID cells over [-GRID_HALF, GRID_HALF]^2.  With
#: the centroid at the origin and unit height, any outline fits vertically;
#: 256 cells put a 0.3-wide ear about 38 cells across.
GRID = 256
GRID_HALF = 1.0

#: working raster for a mesh projection: cells along the larger extent.  The
#: canonical cell is 1/128 of the height, so this is 4x finer than the score.
WORK_CELLS = 512

#: Douglas-Peucker tolerance, in cells of whichever raster the outline was
#: traced from: half a cell is the quantization of the trace itself, so this
#: removes the pixel staircase and nothing the source actually resolved.
SIMPLIFY_CELLS = 0.5

# --- the frozen ear reference ------------------------------------------------

EAR_TASK_DIR = os.path.join(HERE, "tasks", "ear-sculpt")
EAR_SHEET = "eevee_sheet.png"
EAR_SHEET_SHA256 = "ca0dafd9ed017c52e29880175b76b0b5ce36fc8404a537cb80be5bc3888401e3"
EAR_OUTLINE = "ear_outline.json"

#: The pose choice (documented, not geometry): the FRONT view, top-left of the
#: sheet, and its image-left ear (Eevee's right ear) — the front view shows the
#: ear face-on (inner face toward the viewer), which is the view a projection
#: onto the blade plane reproduces, and the image-left ear is drawn clear of the
#: tail, which overlaps nothing near it.  The rectangle is a half-open pixel box
#: ``(x0, y0, x1, y1)`` on the frozen 600 x 468 image that contains that whole
#: ear with margin plus part of the head it joins; WHERE the ear ends and the
#: head begins is not in this constant — it is found by the convexity-defect
#: rule in :func:`extract_ear_outline`.
EAR_CROP = (30, 10, 130, 110)

#: Background = within this Euclidean RGB distance (0..1 per channel) of the
#: crop border's modal colour (255, 255, 255).  Measured, not tuned: the sheet
#: is a re-saved JPEG, and spriteforge's 0.04 lands on its ringing — ear pixel
#: count 2353 / 1997 / 1728 at 0.02 / 0.04 / 0.08 (a 31% swing), 30 speckle
#: islands, the halo fattening the ear by 2-4 px and moving both notches.
#: From 0.12 to 0.6 the extraction is on a plateau: 1 island, the notches fixed
#: at (108, 70) and (103, 91), ear cells 1695 -> 1638, aspect 0.325-0.328.  0.2
#: sits mid-plateau; the half/double margin is recorded in the outline file.
BACKGROUND_TOLERANCE = 0.2


# ---------------------------------------------------------------------------
# PNG, stdlib only
# ---------------------------------------------------------------------------

def read_png(path: str):
    """``(width, height, channels, rows)`` of an 8-bit non-interlaced PNG.

    ``rows`` is a list of ``bytes``, ``width * channels`` long each.  Only the
    formats a frozen reference needs (grey, grey+alpha, RGB, RGBA at 8 bits).
    """
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s is not a PNG" % path)
    pos, idat, header = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("%s has no IHDR" % path)
    width, height, depth, colour, _comp, _filter, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    if depth != 8 or channels is None or interlace:
        raise ValueError("%s: only 8-bit non-interlaced grey/RGB(A) PNGs "
                         "(depth %d, colour type %d, interlace %d)"
                         % (path, depth, colour, interlace))
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows: List[bytes] = []
    prev = bytearray(stride)
    at = 0
    for _ in range(height):
        kind = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        if kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        elif kind != 0:
            raise ValueError("%s: bad PNG filter %d" % (path, kind))
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows


def _rgb(rows, channels, x, y):
    base = x * channels
    row = rows[y]
    if channels >= 3:
        return (row[base], row[base + 1], row[base + 2])
    return (row[base], row[base], row[base])


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# ---------------------------------------------------------------------------
# masks: background flood, islands, the pixel-corner contour
# ---------------------------------------------------------------------------

def background_mask(pixels, tolerance):
    """``(is_background, colour, share)`` for a 2D grid of RGB tuples.

    Background colour = the most common exact colour on the border ring (ties
    to the smallest colour, so it is deterministic) — spriteforge's rule.
    """
    height, width = len(pixels), len(pixels[0])
    ring = ([pixels[0][x] for x in range(width)] + [pixels[height - 1][x] for x in range(width)]
            + [pixels[y][0] for y in range(height)] + [pixels[y][width - 1] for y in range(height)])
    counts: Dict[Tuple[int, int, int], int] = {}
    for colour in ring:
        counts[colour] = counts.get(colour, 0) + 1
    colour = min(counts, key=lambda c: (-counts[c], c))
    limit = (tolerance * 255.0) ** 2
    mask = [[sum((p[k] - colour[k]) ** 2 for k in range(3)) <= limit for p in row]
            for row in pixels]
    return mask, colour, counts[colour] / float(len(ring))


def _components(mask, diagonal):
    """Connected components of ``mask`` in raster order of their first cell."""
    height, width = len(mask), len(mask[0])
    label = [[0] * width for _ in range(height)]
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if diagonal:
        steps += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    comps = []
    for y in range(height):
        for x in range(width):
            if not mask[y][x] or label[y][x]:
                continue
            index = len(comps) + 1
            label[y][x] = index
            cells, stack = [], [(x, y)]
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for dx, dy in steps:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < width and 0 <= ny < height and mask[ny][nx] \
                            and not label[ny][nx]:
                        label[ny][nx] = index
                        stack.append((nx, ny))
            comps.append(cells)
    return comps


def solid_island(background):
    """The largest 8-connected island of what the border's background cannot reach.

    Background is flooded 4-connected from the frame border (spriteforge's
    pairing: 4 for background, 8 for subject); everything else is subject, so
    an enclosed pocket of background colour stays solid.  Of the subject
    islands the largest is kept (ties: first in raster order), which drops
    isolated JPEG speckles.
    """
    height, width = len(background), len(background[0])
    reach = [[False] * width for _ in range(height)]
    stack = [(x, y) for y in range(height) for x in range(width)
             if (x in (0, width - 1) or y in (0, height - 1)) and background[y][x]]
    for x, y in stack:
        reach[y][x] = True
    while stack:
        cx, cy = stack.pop()
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if 0 <= nx < width and 0 <= ny < height and background[ny][nx] \
                    and not reach[ny][nx]:
                reach[ny][nx] = True
                stack.append((nx, ny))
    solid = [[not reach[y][x] for x in range(width)] for y in range(height)]
    comps = _components(solid, diagonal=True)
    if not comps:
        return solid, 0, 0
    best = max(range(len(comps)), key=lambda i: (len(comps[i]), -i))
    island = [[False] * width for _ in range(height)]
    for x, y in comps[best]:
        island[y][x] = True
    return island, len(comps[best]), len(comps)


def trace_loops(mask) -> List[List[Tuple[int, int]]]:
    """Closed boundary loops of a boolean grid on the pixel corner grid.

    A stdlib port of ``addon/forge/tools/spriteforge.py`` ``trace_contour``:
    cell ``(x, y)`` occupies ``[x, x+1] x [y, y+1]``; each side facing a
    non-subject neighbour contributes one directed segment (subject on the
    right), and the segments are chained starting from the lexicographically
    smallest corner.  At a diagonal pinch the walk takes the smallest cross
    product, which keeps an 8-connected island a single loop.
    """
    height = len(mask)
    width = len(mask[0]) if height else 0

    def filled(x, y):
        return 0 <= y < height and 0 <= x < width and mask[y][x]

    outgoing: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    def add(a, b):
        outgoing.setdefault(a, []).append(b)

    for y in range(height):
        row = mask[y]
        for x in range(width):
            if not row[x]:
                continue
            if not filled(x, y - 1):
                add((x, y), (x + 1, y))
            if not filled(x + 1, y):
                add((x + 1, y), (x + 1, y + 1))
            if not filled(x, y + 1):
                add((x + 1, y + 1), (x, y + 1))
            if not filled(x - 1, y):
                add((x, y + 1), (x, y))
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


def signed_area(ring: Sequence[Point]) -> float:
    total = 0.0
    count = len(ring)
    for i in range(count):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % count]
        total += x0 * y1 - x1 * y0
    return 0.5 * total


def outer_loop(loops):
    """The loop enclosing the most area (ties: first) — the island's outline."""
    if not loops:
        return None
    return max(loops, key=lambda loop: abs(signed_area(loop)))


# ---------------------------------------------------------------------------
# simplification (spriteforge's, stdlib)
# ---------------------------------------------------------------------------

def _drop_collinear(points):
    count = len(points)
    if count < 3:
        return list(points)
    kept = []
    for i in range(count):
        px, py = points[i - 1]
        cx, cy = points[i]
        nx, ny = points[(i + 1) % count]
        if (cx - px) * (ny - cy) - (cy - py) * (nx - cx) != 0:
            kept.append(points[i])
    return kept if len(kept) >= 3 else list(points)


def _douglas_peucker(points, tolerance):
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
        for i in range(first + 1, last):
            px, py = points[i]
            if length <= 1e-12:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dx * (ay - py) - (ax - px) * dy) / length
            if distance > worst:
                worst, at = distance, i
        if worst > tolerance and at > first:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))
    return [points[i] for i in range(count) if keep[i]]


def simplify_closed(points, tolerance):
    """Douglas-Peucker on a closed ring, cut at its first point and the point
    furthest from it (both derived from the ring, so the cut is deterministic)."""
    points = _drop_collinear(points)
    count = len(points)
    if count < 4 or tolerance <= 0.0:
        return points
    ax, ay = points[0]
    far, at = -1.0, 0
    for i in range(1, count):
        px, py = points[i]
        distance = (px - ax) ** 2 + (py - ay) ** 2
        if distance > far:
            far, at = distance, i
    first = _douglas_peucker(points[0:at + 1], tolerance)
    second = _douglas_peucker(points[at:] + [points[0]], tolerance)
    ring = first[:-1] + second[:-1]
    return ring if len(ring) >= 3 else points


# ---------------------------------------------------------------------------
# convex hull + convexity defects (where an ear meets the head)
# ---------------------------------------------------------------------------

def convex_hull(points):
    """Andrew's monotone chain; counter-clockwise, no collinear points."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def convexity_defects(loop):
    """``[(depth, index, hull_a, hull_b)]`` deepest first, one per hull edge.

    For each pair of consecutive hull vertices (in the loop's own order) the
    loop point between them furthest from the hull edge — the bottom of that
    concavity — and its depth.  Ties break on the loop index.
    """
    hull = set(convex_hull(loop))
    marks = [i for i, p in enumerate(loop) if p in hull]
    count = len(loop)
    defects = []
    for k, a in enumerate(marks):
        b = marks[(k + 1) % len(marks)]
        ax, ay = loop[a]
        bx, by = loop[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= 0.0:
            continue
        best_depth, best_index = 0.0, None
        i = (a + 1) % count
        while i != b:
            px, py = loop[i]
            depth = abs(dx * (ay - py) - (ax - px) * dy) / length
            if depth > best_depth:
                best_depth, best_index = depth, i
            i = (i + 1) % count
        if best_index is not None:
            defects.append((best_depth, best_index, a, b))
    defects.sort(key=lambda d: (-d[0], d[1]))
    return defects


# ---------------------------------------------------------------------------
# normalization + canonical raster + IoU
# ---------------------------------------------------------------------------

def polygon_moments(poly: Sequence[Point]):
    """``(area, (cx, cy), (cxx, cyy, cxy))`` — exact, by Green's theorem.

    Integrated about the vertex mean for precision; the sign of the winding
    cancels in the central moments.
    """
    n = len(poly)
    mx = math.fsum(p[0] for p in poly) / n
    my = math.fsum(p[1] for p in poly) / n
    a = sx = sy = sxx = syy = sxy = 0.0
    for i in range(n):
        x0, y0 = poly[i][0] - mx, poly[i][1] - my
        x1, y1 = poly[(i + 1) % n][0] - mx, poly[(i + 1) % n][1] - my
        c = x0 * y1 - x1 * y0
        a += c
        sx += (x0 + x1) * c
        sy += (y0 + y1) * c
        sxx += (x0 * x0 + x0 * x1 + x1 * x1) * c
        syy += (y0 * y0 + y0 * y1 + y1 * y1) * c
        sxy += (x0 * y1 + 2.0 * x0 * y0 + 2.0 * x1 * y1 + x1 * y0) * c
    area = a / 2.0
    if abs(area) <= 1e-300:
        raise ValueError("outline encloses no area")
    cx, cy = sx / (6.0 * area), sy / (6.0 * area)
    cxx = sxx / (12.0 * area) - cx * cx
    cyy = syy / (12.0 * area) - cy * cy
    cxy = sxy / (24.0 * area) - cx * cy
    return abs(area), (cx + mx, cy + my), (cxx, cyy, cxy)


def normalize(poly: Sequence[Point], up: Optional[Point] = None) -> Dict[str, Any]:
    """The canonical form of an outline (see the module docstring, step 2).

    The long axis is signed toward ``up`` when ``up`` has a component along it;
    otherwise toward the extreme furthest from the centroid (a leaf's tip — its
    mass sits near the wide base).  ``u`` is that axis turned -90 deg, so the
    frame stays right-handed and a mirrored input gives a u-mirrored output.
    """
    area, (cx, cy), (cxx, cyy, cxy) = polygon_moments(poly)
    theta = 0.5 * math.atan2(2.0 * cxy, cxx - cyy)
    ex, ey = math.cos(theta), math.sin(theta)
    rel = [(p[0] - cx, p[1] - cy) for p in poly]
    vs = [x * ex + y * ey for x, y in rel]
    sign = 0.0
    if up is not None:
        along = up[0] * ex + up[1] * ey
        if abs(along) > 1e-9:
            sign = 1.0 if along > 0.0 else -1.0
    if sign == 0.0:
        sign = 1.0 if max(vs) >= -min(vs) else -1.0
    ex, ey = ex * sign, ey * sign
    ux, uy = ey, -ex
    us = [x * ux + y * uy for x, y in rel]
    vs = [x * ex + y * ey for x, y in rel]
    height = max(vs) - min(vs)
    if height <= 0.0:
        raise ValueError("outline has no height")
    canon = [(u / height, v / height) for u, v in zip(us, vs)]
    return {"polygon": canon,
            "height": height,
            "aspect": (max(us) - min(us)) / height,
            "area": area / (height * height),
            "axis": [ex, ey],
            "centroid": [cx, cy]}


def rasterize(poly: Sequence[Point], grid: int = GRID, half: float = GRID_HALF,
              mirror: bool = False) -> List[int]:
    """Even-odd pixel-centre fill of a polygon; one int bitmask per row.

    A cell is filled when its centre is inside (edges half-open in y, spans
    half-open in x), so two polygons sharing an edge never both claim a cell.
    ``mirror`` negates u first.
    """
    cell = 2.0 * half / grid
    edges = []
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if mirror:
            x0, x1 = -x0, -x1
        if y0 != y1:
            edges.append((x0, y0, x1, y1))
    rows = [0] * grid
    for r in range(grid):
        yc = -half + (r + 0.5) * cell
        xs = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
        if not xs:
            continue
        xs.sort()
        bits = 0
        for k in range(0, len(xs) - 1, 2):
            j0 = max(0, math.ceil((xs[k] + half) / cell - 0.5))
            j1 = min(grid, math.ceil((xs[k + 1] + half) / cell - 0.5))
            if j1 > j0:
                bits |= ((1 << (j1 - j0)) - 1) << j0
        rows[r] = bits
    return rows


def _popcount(value: int) -> int:
    return bin(value).count("1")


def iou_rows(a: Sequence[int], b: Sequence[int]) -> float:
    inter = sum(_popcount(x & y) for x, y in zip(a, b))
    union = sum(_popcount(x | y) for x, y in zip(a, b))
    return inter / float(union) if union else 0.0


def silhouette_iou(candidate: Sequence[Point], reference: Sequence[Point],
                   grid: int = GRID) -> Dict[str, Any]:
    """IoU of two CANONICAL polygons, the better of candidate and its mirror."""
    ref = rasterize(reference, grid)
    plain = iou_rows(rasterize(candidate, grid), ref)
    mirrored = iou_rows(rasterize(candidate, grid, mirror=True), ref)
    return {"iou": max(plain, mirrored), "mirrored": mirrored > plain,
            "iou_plain": plain, "iou_mirrored": mirrored}


# ---------------------------------------------------------------------------
# the mesh side
# ---------------------------------------------------------------------------

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def projected_mask(points2d, triangles, work_cells: int = WORK_CELLS):
    """Pixel-centre raster of the union of 2D triangles.

    Returns ``(mask, origin, cell)``: ``mask`` a list of boolean rows (row ``r``
    covers ``v`` in ``origin[1] + [r, r+1) * cell``), padded by one empty cell
    on every side so the trace always closes.
    """
    us = [p[0] for p in points2d]
    vs = [p[1] for p in points2d]
    lo_u, lo_v = min(us), min(vs)
    span = max(max(us) - lo_u, max(vs) - lo_v)
    if span <= 0.0:
        raise ValueError("projection has no extent")
    cell = span / work_cells
    ou, ov = lo_u - cell, lo_v - cell
    width = int(math.ceil((max(us) - lo_u) / cell)) + 3
    height = int(math.ceil((max(vs) - lo_v) / cell)) + 3
    rows = [0] * height
    for tri in triangles:
        pts = [((points2d[i][0] - ou) / cell, (points2d[i][1] - ov) / cell) for i in tri]
        (x0, y0), (x1, y1), (x2, y2) = pts
        if (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) == 0.0:
            continue  # edge-on: covers no area
        r0 = max(0, math.ceil(min(y0, y1, y2) - 0.5))
        r1 = min(height, math.ceil(max(y0, y1, y2) - 0.5))
        edges = ((x0, y0, x1, y1), (x1, y1, x2, y2), (x2, y2, x0, y0))
        for r in range(r0, r1):
            yc = r + 0.5
            xs = [ax + (yc - ay) * (bx - ax) / (by - ay)
                  for ax, ay, bx, by in edges
                  if (ay <= yc < by) or (by <= yc < ay)]
            if len(xs) < 2:
                continue
            j0 = max(0, math.ceil(min(xs) - 0.5))
            j1 = min(width, math.ceil(max(xs) - 0.5))
            if j1 > j0:
                rows[r] |= ((1 << (j1 - j0)) - 1) << j0
    mask = [[bool((bits >> j) & 1) for j in range(width)] for bits in rows]
    return mask, (ou, ov), cell


def mesh_outline(vertices, triangles, up_axis=None,
                 work_cells: int = WORK_CELLS) -> Dict[str, Any]:
    """The canonical silhouette of a mesh seen face-on (module docstring, step 1)."""
    from benchmark import geometry

    frame = geometry.principal_frame(vertices, triangles)
    _thin, mid, long_ = frame["axes"]
    c = frame["centroid"]
    points = [(_dot((v[0] - c[0], v[1] - c[1], v[2] - c[2]), mid),
               _dot((v[0] - c[0], v[1] - c[1], v[2] - c[2]), long_)) for v in vertices]
    mask, (ou, ov), cell = projected_mask(points, triangles, work_cells)
    loops = trace_loops(mask)
    ring = outer_loop(loops)
    if ring is None:
        raise ValueError("the projection covered no cell")
    ring = simplify_closed(ring, SIMPLIFY_CELLS)
    poly = [(ou + x * cell, ov + y * cell) for x, y in ring]
    up = None
    if up_axis is not None:
        up = (_dot(up_axis, mid), _dot(up_axis, long_))
    out = normalize(poly, up)
    out.update(loops=len(loops), outline_points=len(ring), work_cell_mm=cell,
               plane_normal=list(frame["axes"][0]))
    return out


# ---------------------------------------------------------------------------
# the frozen reference: extraction, file, loading
# ---------------------------------------------------------------------------

def _rounded(value, places=6):
    if isinstance(value, float):
        out = round(value, places)
        return 0.0 if out == 0.0 else out
    if isinstance(value, (list, tuple)):
        return [_rounded(v, places) for v in value]
    if isinstance(value, dict):
        return {k: _rounded(v, places) for k, v in value.items()}
    return value


def extract_ear_outline(sheet_path: Optional[str] = None) -> Dict[str, Any]:
    """The reference ear outline, extracted from the frozen sheet by rule.

    1. crop :data:`EAR_CROP` (the documented pose choice);
    2. background = the crop border's modal colour within
       :data:`BACKGROUND_TOLERANCE`, flooded from the border; the largest
       8-connected solid island is the ear plus the part of the head in frame;
    3. trace its boundary on the pixel corner grid;
    4. **where the ear meets the head** = the two deepest convexity defects of
       that boundary.  The crop's edges cut the head along straight lines that
       lie ON the hull (depth 0), so the only deep concavities in frame are the
       two notches either side of the ear's base;
    5. the ear = the boundary chain between the two notches that never touches
       the crop edge, closed by the chord across the base; staircase removed
       by Douglas-Peucker at :data:`SIMPLIFY_CELLS` pixels;
    6. y flipped to point up, then :func:`normalize` with up = image up.
    """
    path = sheet_path or os.path.join(EAR_TASK_DIR, EAR_SHEET)
    digest = sha256_file(path)
    if digest != EAR_SHEET_SHA256:
        raise ValueError("%s sha256 %s is not the frozen %s" % (path, digest, EAR_SHEET_SHA256))
    width, height, channels, rows = read_png(path)
    x0, y0, x1, y1 = EAR_CROP
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("crop %r outside the %dx%d image" % (EAR_CROP, width, height))
    pixels = [[_rgb(rows, channels, x, y) for x in range(x0, x1)] for y in range(y0, y1)]

    margins = {}
    for label, tol in (("at_half_tolerance", BACKGROUND_TOLERANCE / 2.0),
                       ("at_double_tolerance", BACKGROUND_TOLERANCE * 2.0)):
        margins[label] = _extract_ear(pixels, tol)["ear_cells"]
    ear = _extract_ear(pixels, BACKGROUND_TOLERANCE)
    margins["at_tolerance"] = ear["ear_cells"]
    margins["swing"] = (margins["at_half_tolerance"] - margins["at_double_tolerance"]) \
        / float(ear["ear_cells"])
    loop, defects = ear["loop"], ear["defects"]
    notch_a, notch_b = ear["notches"]
    pixel_ring = [(x + x0, y + y0) for x, y in ear["ring"]]
    canon = normalize([(float(x), -float(y)) for x, y in pixel_ring], up=(0.0, 1.0))

    return _rounded({
        "about": ("Reference ear outline for benchmark/tasks/ear-sculpt, extracted by "
                  "benchmark/silhouette.py extract_ear_outline from the frozen sheet - "
                  "no hand-placed point. Regenerate with: python benchmark/silhouette.py "
                  "--write-ear-outline"),
        "source": {"file": EAR_SHEET, "sha256": digest, "size_px": [width, height],
                   "artist": "ears are more curved like this"},
        "pose": ("front view (top-left of the sheet), image-left ear (Eevee's right): "
                 "seen face-on, clear of the tail"),
        "crop_px": list(EAR_CROP),
        "background": {"colour_rgb": list(ear["colour"]), "border_share": ear["share"],
                       "tolerance": BACKGROUND_TOLERANCE,
                       "ear_cells_margin": margins},
        "island": {"cells": ear["island_cells"], "islands": ear["islands"],
                   "loops": ear["loops"], "loop_points": len(loop)},
        "junction": {
            "rule": "the two deepest convexity defects of the traced crop outline",
            "notches_px": [[loop[notch_a][0] + x0, loop[notch_a][1] + y0],
                           [loop[notch_b][0] + x0, loop[notch_b][1] + y0]],
            "depths_px": [defects[0][0], defects[1][0]],
            "next_depth_px": defects[2][0] if len(defects) > 2 else 0.0,
        },
        "ear_px": {"polygon": [list(p) for p in pixel_ring], "points": len(pixel_ring),
                   "cells_inside": ear["ear_cells"], "height_px": canon["height"],
                   "simplify_px": SIMPLIFY_CELLS},
        "outline": {"frame": ("centroid at origin, long principal axis = +v (toward image "
                              "up), u = v turned -90 deg, unit height along v"),
                    "aspect": canon["aspect"], "area": canon["area"],
                    "polygon": [list(p) for p in canon["polygon"]]},
    })


def _extract_ear(pixels, tolerance):
    """Steps 2-5 of :func:`extract_ear_outline` on a cropped RGB grid."""
    ch, cw = len(pixels), len(pixels[0])
    background, colour, share = background_mask(pixels, tolerance)
    island, island_cells, islands = solid_island(background)
    loops = trace_loops(island)
    loop = outer_loop(loops)
    if loop is None:
        raise ValueError("nothing but background in the crop")
    defects = convexity_defects(loop)
    if len(defects) < 2:
        raise ValueError("fewer than two concavities: no ear/head junction in the crop")
    notch_a, notch_b = sorted((defects[0][1], defects[1][1]))

    def on_frame(p):
        return p[0] in (0, cw) or p[1] in (0, ch)

    chain_one = loop[notch_a:notch_b + 1]
    chain_two = loop[notch_b:] + loop[:notch_a + 1]
    free = [c for c in (chain_one, chain_two) if not any(on_frame(p) for p in c)]
    if len(free) != 1:
        raise ValueError("the notches do not split off exactly one free chain (%d)" % len(free))
    ring = simplify_closed(free[0], SIMPLIFY_CELLS)
    return {"colour": colour, "share": share, "island_cells": island_cells,
            "islands": islands, "loops": len(loops), "loop": loop, "defects": defects,
            "notches": (notch_a, notch_b), "ring": ring,
            "ear_cells": _cells_inside(ring, cw, ch)}


def _cells_inside(ring, width, height):
    """Pixel centres inside a corner-grid ring — the ear's own pixel count."""
    size = max(width, height)
    half = size / 2.0
    rows = rasterize([((x - half) / half, (y - half) / half) for x, y in ring],
                     grid=size, half=1.0)
    return sum(_popcount(r) for r in rows)


def outline_bytes(payload: Dict[str, Any]) -> bytes:
    """The committed file's exact bytes: sorted keys, fixed indent, LF, 6 dp."""
    return (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode("utf-8")


def write_ear_outline(task_dir: str = EAR_TASK_DIR) -> str:
    path = os.path.join(task_dir, EAR_OUTLINE)
    with open(path, "wb") as handle:
        handle.write(outline_bytes(extract_ear_outline(os.path.join(task_dir, EAR_SHEET))))
    return path


def load_outline(path: str) -> Dict[str, Any]:
    """``{"polygon": canonical [(u, v)], "aspect", "area"}`` from an outline file."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    outline = payload["outline"]
    return {"polygon": [tuple(p) for p in outline["polygon"]],
            "aspect": outline.get("aspect"), "area": outline.get("area")}


def measure_part(vertices, triangles, reference: Dict[str, Any], up_axis=None,
                 grid: int = GRID) -> Dict[str, Any]:
    """One part's silhouette IoU against a loaded reference outline."""
    canon = mesh_outline(vertices, triangles, up_axis)
    score = silhouette_iou(canon["polygon"], reference["polygon"], grid)
    return {"iou": score["iou"], "mirrored": score["mirrored"],
            "aspect": canon["aspect"], "reference_aspect": reference.get("aspect"),
            "area": canon["area"], "reference_area": reference.get("area"),
            "outline_points": canon["outline_points"], "loops": canon["loops"]}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--write-ear-outline"]:
        print("wrote %s" % write_ear_outline())
        return 0
    if argv == ["--check-ear-outline"]:
        path = os.path.join(EAR_TASK_DIR, EAR_OUTLINE)
        with open(path, "rb") as handle:
            same = handle.read() == outline_bytes(extract_ear_outline())
        print("%s %s" % (path, "matches a fresh extraction" if same else "DIFFERS"))
        return 0 if same else 1
    print("usage: silhouette.py --write-ear-outline | --check-ear-outline")
    return 2


if __name__ == "__main__":
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    sys.exit(main())
