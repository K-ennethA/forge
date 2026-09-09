"""``fit_to_silhouette`` (Phase 18b) — move a sculpt until its outline is theirs.

The artist's ask, verbatim: *"take an image and use the silhouette to map out or
move the sculpt to match that silhouette, and do multiple sides if provided, so
front and side."*

What this command does
----------------------
One or more reference pictures, one per orthographic view, each thresholded to a
binary silhouette.  For every view the mesh is projected along that view's axis
and warped **in that view's plane only**, so a front reference moves X and Z and
never touches Y, a side reference moves Y and Z and never touches X, and a
vertex constrained by both moves in all three because two views asked it to.
The depth axis of a view is the one thing that view is not entitled to an
opinion about, and it is left exactly where the artist put it.

The warp itself, said plainly
-----------------------------
Inside a view's plane the mesh is described as a **radial profile** about its own
projected centre: for each of 256 angular bins, how far the outline sits from
the centre.  Both outlines are measured the same way, by the same function — a
ray marched out from the shared centre until it leaves the silhouette — the
reference off its thresholded picture and the mesh off a rasterised projection
of its own faces.  The ratio of the two radii at an angle is how much the mesh
has to grow or shrink *at that angle*, and every vertex is moved along its own
radius by that ratio, interpolated smoothly between bins.

That is a **smooth anisotropic scale field**, not a projection onto a curve, and
the difference is the whole point:

* the projected outline lands on the reference outline, because at the outline
  ``r`` equals the mesh radius and the ratio takes it exactly there;
* everything inside moves by the *same proportion*, so the relative position of
  every feature the artist sculpted — the ridge of a nose, the line of a jaw —
  survives the fit. Nothing is flattened onto the silhouette and no interior
  detail is averaged away.

``falloff`` is the knob for artists who disagree: at ``0`` (the default) the
whole cross-section scales together, and above ``0`` the displacement is
weighted by ``(r / R_mesh) ** falloff``, so the rim moves and the core stays put.

Fairing without damage
----------------------
``smooth`` smooths the **displacement field**, never the mesh.  A Laplacian pass
over the mesh's own edge graph, applied to the per-vertex movement vectors
before any of them are applied, removes spikes where the reference outline is
noisy — and because the mesh itself is never smoothed, a 200 000-vertex sculpt
comes out of this with every pore it went in with.

What it is honestly not
-----------------------
* The radial parametrisation is **star-shaped**: a concavity a ray from the
  centre cannot see (the inside of a horseshoe, the gap between two legs above
  the crotch) is approximated by the outline the ray does see.  The report says
  so, every time.
* Matching a silhouette is matching an *outline*.  Two very different objects
  can share one, and the fit cannot invent depth the reference never had.  This
  is a fit **to the reference's outline**, not a reconstruction of the object in
  the photograph.
* The alignment is bbox-to-bbox: the reference is scaled so its silhouette is
  the same **height** as the mesh's projection (``fit`` changes which extent is
  the anchor) and centred on it.  The command changes the shape of the sculpt,
  not where in the world it stands.

Undo
----
This edits the artist's mesh, so it is deliberately **not** in
``READ_ONLY_COMMANDS``: the registry pushes ``Forge: fit_to_silhouette`` before
it runs, and one Ctrl+Z puts the sculpt back the way it was.

Measurement
-----------
Every view reports silhouette **IoU before and after**, measured against the
same fixed target on the same fixed grid, plus the mean and worst radial
outline error in millimetres.  The masks are extracted by ``verify.py``'s own
machinery (one measurement, one implementation) and carry its credibility tiers:
a mask off a real alpha channel is ``measured``, a mask thresholded off a
background is ``heuristic`` and says so.
"""

import difflib
import math
import os
import time

from . import common
from . import mechanism
from . import verify
from .common import (
    M_TO_MM,
    get_bool,
    get_float,
    get_int,
)
from .registry import ForgeError, command
from .verify import HEURISTIC, MEASURED, claim

try:  # Blender ships numpy; the guard keeps this honest if a build ever does not.
    import numpy as _np
except ImportError:  # pragma: no cover - numpy is part of Blender
    _np = None

__all__ = [
    "VIEW_AXES",
    "VIEW_ALIASES",
    "AXIS_NAMES",
    "resolve_view",
    "view_frame",
    "mask_from_image",
]


# ---------------------------------------------------------------------------
# the views, and which world axes each one is allowed to touch
# ---------------------------------------------------------------------------
#
# Blender's own orthographic views, written as the two world directions that
# point right and up on screen.  Derived rather than guessed: for a viewer
# looking along ``d`` with ``up``, screen-right is ``d x up`` — front (viewer at
# -Y) gives +X, right (viewer at +X) gives +Y, top (viewer above, +Y up on
# screen) gives +X.  These are the same three angles ``render_preview`` and
# ``set_view`` point a camera at, so "front" means the same thing in every
# command in this add-on.

AXIS_NAMES = ("X", "Y", "Z")

VIEW_AXES = {
    "front":  {"right": (0, 1.0),  "up": (2, 1.0), "depth": 1},
    "back":   {"right": (0, -1.0), "up": (2, 1.0), "depth": 1},
    "right":  {"right": (1, 1.0),  "up": (2, 1.0), "depth": 0},
    "left":   {"right": (1, -1.0), "up": (2, 1.0), "depth": 0},
    "top":    {"right": (0, 1.0),  "up": (1, 1.0), "depth": 2},
    "bottom": {"right": (0, -1.0), "up": (1, 1.0), "depth": 2},
}

#: What an artist says versus what the axis table calls it.  "Side" is the word
#: in the request that started this phase, and it means the right-hand side —
#: the view Blender's numpad 3 gives you.
VIEW_ALIASES = {
    "side": "right",
    "profile": "right",
    "rear": "back",
    "underside": "bottom",
}

#: Top-level shorthand, so the Phase 18 sketch's
#: ``{"front_image": ..., "side_image": ...}`` is a legal call and nobody has to
#: write a ``views`` list to fit two pictures.
IMAGE_SUGAR = {
    "front_image": "front",
    "back_image": "back",
    "side_image": "side",
    "right_image": "right",
    "left_image": "left",
    "top_image": "top",
    "bottom_image": "bottom",
}


# ---------------------------------------------------------------------------
# constants — every one of them a stated judgement call
# ---------------------------------------------------------------------------

#: Angular bins in the radial profiles.  256 bins is 1.4 degrees, which is finer
#: than the outline of any silhouette a 512-pixel reference can actually resolve,
#: and the profiles are interpolated between bins anyway so this is a sampling
#: rate rather than a quantisation.
ANGLE_BINS = 256

#: A circular moving average over the profiles, in bins.  Three bins (4.2
#: degrees) takes the stair-stepping off a pixel outline without rounding off a
#: corner the artist would notice.
PROFILE_SMOOTH_BINS = 3

#: Laplacian passes over the displacement field per iteration.  Two is enough to
#: kill a spike and few enough that a deliberate point stays pointy.
SMOOTH_PASSES = 2

#: The grid both silhouettes are rasterised onto for the IoU. Larger than
#: ``verify.SILHOUETTE_GRID`` (128) because this one is measuring a *change*,
#: and the change worth reporting can be a fraction of a percent.
IOU_GRID = 192

#: The IoU grid is padded this far beyond the union of the two silhouettes, so a
#: mesh that grows during the fit still lands inside the frame it was measured
#: in before it grew.
IOU_PAD = 0.25

#: The grid the mesh's own outline is read off, per iteration.
#:
#: The mesh's outline is measured from a **rasterised projection of its faces**,
#: not from the projected vertices binned by angle, and the difference is the
#: difference between a fit and a lumpy fit.  Binning vertices, a bin whose
#: farthest vertex happens to sit slightly inside the true outline reports a
#: radius that is too small, and every vertex at that angle is then pushed too
#: far — a 10% lump on a UV sphere, measured.  The surface has an outline
#: whether or not a vertex landed on it, so the surface is what gets measured.
#: 384 cells across a frame padded to twice the silhouette puts the radial
#: precision near 0.5% of the radius.
PROFILE_GRID = 384
PROFILE_PAD = 0.5

#: Triangles above this are strided down for the rasteriser only. The fit itself
#: always uses every vertex; this cap is about the picture we take of it.
MAX_RASTER_TRIANGLES = 200000

#: Area samples per grid cell when rasterising the mesh's projection. Four is
#: enough that a triangle spanning one cell fills it; the count is derived from
#: the projected area, so a small silhouette does not pay for a large one.
SPLAT_PER_CELL = 4.0
MIN_SPLAT_SAMPLES = 20000
MAX_SPLAT_SAMPLES = 600000

#: Fixed, so two runs of the same fit report the same IoU to the last digit.
SPLAT_SEED = 20180

#: A vertex that moved less than this did not move. 1 micron, well under any
#: printer's resolution and any sculptor's eye.
MOVED_EPS_MM = 1e-3

#: Symmetry pairs every vertex with its mirror through a KD-tree, one Python
#: call per vertex each way. Past this the pairing costs more than the symmetry
#: is worth and the command says so instead of stalling.
SYMMETRY_VERTEX_LIMIT = 200000

MAX_VIEWS = 6
MAX_ITERATIONS = 12

#: Said in every report, because it is the thing most likely to be forgotten
#: between the fit and the print.
HONESTY = (
    "A silhouette fit matches an OUTLINE. The projected outline now follows the "
    "reference in each view given; depth the reference never had was not "
    "invented, interior form was scaled with the outline rather than rebuilt, "
    "and a concavity a ray from the centre cannot see (the gap between two legs, "
    "the inside of a horseshoe) is approximated by the outline it can see."
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _require_numpy():
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so fit_to_silhouette cannot run — "
            "the whole fit is array arithmetic."
        )


def _view_hint(wanted):
    """" Did you mean 'side'? Views: ..." — the ``_object_hint`` shape."""
    known = sorted(set(VIEW_AXES) | set(VIEW_ALIASES))
    close = difflib.get_close_matches(str(wanted), known, n=2, cutoff=0.4)
    hint = " Did you mean %s?" % ", ".join(repr(name) for name in close) if close else ""
    return "%s Views: %s." % (hint, ", ".join(known))


def resolve_view(axis):
    """A view name (or alias) as a key of :data:`VIEW_AXES`."""
    if not isinstance(axis, str) or not axis.strip():
        raise ForgeError(
            "Each view needs an 'axis' naming which side the picture is of.%s"
            % _view_hint(axis))
    key = axis.strip().lower().replace(" ", "_").replace("-", "_")
    key = VIEW_ALIASES.get(key, key)
    if key not in VIEW_AXES:
        raise ForgeError("There is no %r view.%s" % (axis, _view_hint(axis)))
    return key


def view_frame(axis):
    """``(right_index, right_sign, up_index, up_sign, depth_index)`` for a view."""
    frame = VIEW_AXES[resolve_view(axis)]
    (ri, rs), (ui, us) = frame["right"], frame["up"]
    return ri, rs, ui, us, frame["depth"]


def _plane_name(right_index, up_index):
    return "".join(sorted((AXIS_NAMES[right_index], AXIS_NAMES[up_index])))


def _round(value, places=4):
    try:
        number = float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if not math.isfinite(number):
        return None
    return round(number, places)


# ---------------------------------------------------------------------------
# mesh arrays
# ---------------------------------------------------------------------------

def _edge_pairs(mesh):
    """``(m, 2)`` array of the mesh's edges, for the displacement smoother."""
    count = len(mesh.edges)
    if not count:
        return _np.zeros((0, 2), dtype="i4")
    flat = _np.empty(count * 2, dtype="i4")
    mesh.edges.foreach_get("vertices", flat)
    return flat.reshape(count, 2)


def _triangles(mesh):
    """``(m, 3)`` vertex indices of the mesh's triangulation, or ``None``."""
    try:
        mesh.calc_loop_triangles()
    except (AttributeError, RuntimeError):  # pragma: no cover - 4.x/5.x differ
        pass
    tris = getattr(mesh, "loop_triangles", None)
    count = len(tris) if tris is not None else 0
    if not count:
        return None
    flat = _np.empty(count * 3, dtype="i4")
    tris.foreach_get("vertices", flat)
    return flat.reshape(count, 3)


def _world_coords(mesh, matrix):
    return verify._to_world(verify._local_coords(mesh), matrix)


def _bounds_mm(world):
    return {
        "min": [_round(v * M_TO_MM, 3) for v in world.min(axis=0)],
        "max": [_round(v * M_TO_MM, 3) for v in world.max(axis=0)],
    }


def _dimensions_mm(world):
    span = world.max(axis=0) - world.min(axis=0)
    return [_round(v * M_TO_MM, 3) for v in span]


# ---------------------------------------------------------------------------
# the reference mask
# ---------------------------------------------------------------------------

def _background_mask(pixels, tolerance):
    """``verify._mask_from_background`` with the tolerance made a parameter.

    At the default tolerance this *is* verify's function, called — the two
    silhouette extractors in this add-on must not be allowed to drift apart. A
    caller-supplied ``threshold`` is the only reason a second code path exists,
    and it is the same five lines with one constant replaced.
    """
    if tolerance is None or abs(tolerance - verify.BACKGROUND_TOLERANCE) < 1e-12:
        return verify._mask_from_background(pixels)
    rgb = pixels[:, :, :3]
    border = _np.concatenate([
        rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :],
    ], axis=0)
    background = _np.median(border, axis=0)
    distance = _np.sqrt(((rgb - background) ** 2).sum(axis=2))
    mask = distance > tolerance
    border_distance = _np.sqrt(((border - background) ** 2).sum(axis=1))
    uniformity = float((border_distance <= tolerance).mean())
    return mask, uniformity, [float(v) for v in background]


def mask_from_image(path, threshold=None):
    """A binary silhouette off a picture on disk, plus how much to trust it.

    Two doors, and the picture chooses which:

    * a real **alpha channel** (a render, a cut-out PNG) is the mask, exactly,
      at ``threshold`` (default 0.5) — no guessing, tier ``measured``;
    * otherwise the mask is every pixel further than ``threshold`` (default
      ``verify.BACKGROUND_TOLERANCE``) from the **median colour of the border
      ring**, which is a luminance threshold when the border is white paper and
      a colour threshold when it is a photographer's backdrop. It assumes a
      PLAIN BACKGROUND, tier ``heuristic``, and how plain the background really
      was comes back as a number rather than an assumption.
    """
    _require_numpy()
    if not isinstance(path, str) or not path.strip():
        raise ForgeError("Each view needs an 'image' — the path to a picture on disk.")
    resolved = common.resolve_path(path)
    if not os.path.isfile(resolved):
        raise ForgeError(
            "No image at %r to read a silhouette from. Give the full path to a "
            "picture file (PNG, JPEG, ...)." % resolved)

    _, _, channels, pixels = verify._image_pixels(resolved)
    alpha_present = channels >= 4 and float((pixels[:, :, 3] < 0.99).mean()) > 0.01
    if alpha_present:
        cut = 0.5 if threshold is None else threshold
        mask = pixels[:, :, 3] > cut
        source = claim("alpha", MEASURED,
                       "the reference has a real alpha channel, so its "
                       "silhouette is exact rather than estimated")
        uniformity, background = 1.0, None
    else:
        cut = verify.BACKGROUND_TOLERANCE if threshold is None else threshold
        mask, uniformity, background = _background_mask(pixels, cut)
        source = claim("background threshold", HEURISTIC,
                       "the reference has no alpha, so its silhouette was "
                       "thresholded against the median border colour — this "
                       "assumes a PLAIN BACKGROUND")

    coverage = float(mask.mean())
    reasons = []
    confidence = "high" if alpha_present else "medium"
    if not alpha_present and uniformity < verify.BORDER_UNIFORMITY:
        confidence = "low"
        reasons.append(
            "only %.0f%% of the reference's border is one flat colour, so the "
            "background is not plain and the extracted silhouette may include "
            "scenery" % (uniformity * 100.0))
    if coverage > 0.9:
        confidence = "low"
        reasons.append("the extracted silhouette covers over 90%% of the frame "
                       "(%.0f%%), which usually means the threshold caught the "
                       "background instead of the subject — the mask may be "
                       "inverted" % (coverage * 100.0))
    if coverage < 0.005:
        confidence = "low"
        reasons.append("the extracted silhouette is under 0.5%% of the frame "
                       "(%.2f%%) — too little to fit anything to"
                       % (coverage * 100.0))

    return {
        "path": resolved,
        "mask": mask,
        "size": [int(mask.shape[1]), int(mask.shape[0])],
        "channels": channels,
        "threshold": _round(cut, 4),
        "source": source,
        "coverage": _round(coverage, 5),
        "pixels": int(_np.count_nonzero(mask)),
        "background_uniformity": _round(uniformity, 3),
        "background_color": background,
        "confidence": confidence,
        "confidence_reasons": reasons,
    }


def _mask_bbox(mask):
    rows, cols = _np.nonzero(mask)
    if not len(rows):
        return None
    return int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())


def _speck_warning(mask, bbox, path):
    """Warn when the mask's bounding box is decided by a handful of pixels.

    A single stray pixel in a corner moves the bounding box, and the bounding
    box is what the whole alignment is built on. Trimming it silently would clip
    a foot, so this measures and tells rather than deciding.
    """
    left, top, right, bottom = bbox
    rows = mask.sum(axis=1).astype("f8")
    cols = mask.sum(axis=0).astype("f8")
    total = rows.sum()
    if total <= 0:
        return None
    def _quantile_span(counts, fallback):
        cumulative = _np.cumsum(counts) / total
        inside = _np.nonzero((cumulative > 0.005) & (cumulative < 0.995))[0]
        if not len(inside):
            return fallback
        return int(inside.max()) - int(inside.min())
    row_span = max(bottom - top, 1)
    col_span = max(right - left, 1)
    dense_rows = _quantile_span(rows, row_span)
    dense_cols = _quantile_span(cols, col_span)
    if dense_rows < row_span * 0.8 or dense_cols < col_span * 0.8:
        return ("The silhouette in %s has outlying pixels: 99%% of it fits in "
                "%d x %d pixels but its bounding box is %d x %d. The fit aligns "
                "on the bounding box, so a speck or a watermark in a corner will "
                "pull the whole reference — crop it or clean the background."
                % (os.path.basename(path), dense_cols + 1, dense_rows + 1,
                   col_span + 1, row_span + 1))
    return None


# ---------------------------------------------------------------------------
# radial profiles
# ---------------------------------------------------------------------------

def _fill_gaps(profile):
    """Fill NaN bins by interpolating around the circle. ``None`` if all NaN."""
    valid = ~_np.isnan(profile)
    if not valid.any():
        return None
    if valid.all():
        return profile
    bins = profile.shape[0]
    index = _np.arange(bins, dtype="f8")
    known = index[valid]
    values = profile[valid]
    xp = _np.concatenate([known - bins, known, known + bins])
    fp = _np.concatenate([values, values, values])
    return _np.interp(index, xp, fp)


def _smooth_profile(profile, window=PROFILE_SMOOTH_BINS):
    """Circular moving average — the stair-steps off a pixel outline."""
    if window <= 1:
        return profile
    bins = profile.shape[0]
    kernel = _np.ones(window, dtype="f8") / float(window)
    tiled = _np.concatenate([profile, profile, profile])
    smoothed = _np.convolve(tiled, kernel, mode="same")
    return smoothed[bins:2 * bins]


def _sample_profile(profile, angles):
    """The profile at arbitrary angles, linearly interpolated, wrapping."""
    bins = profile.shape[0]
    step = 2.0 * math.pi / bins
    centres = (_np.arange(bins, dtype="f8") + 0.5) * step
    xp = _np.concatenate([centres - 2.0 * math.pi, centres,
                          centres + 2.0 * math.pi])
    fp = _np.concatenate([profile, profile, profile])
    return _np.interp(_np.mod(angles, 2.0 * math.pi), xp, fp)


def _march_profile(mask, centre_col, centre_row, scale_h, scale_v, t_max, steps,
                   bins=ANGLE_BINS):
    """Outline radius per angular bin, in world units, by marching rays.

    ONE implementation, two callers: the reference picture and the mesh's own
    rasterised projection are measured by exactly the same ruler, which is what
    makes the ratio between them meaningful.  Pixel ``(row, col)`` sits at world
    offset ``((col - centre_col) * scale_h, (centre_row - row) * scale_v)`` from
    the shared centre, so both callers only have to say where their centre is
    and how big a pixel is.

    The **outermost** mask pixel a ray touches is the outline — outermost rather
    than "the first gap", so a hole in the middle of a silhouette (a handle, a
    thresholding speck, a coarse raster) does not become the outline.
    """
    height, width = mask.shape
    t = _np.linspace(0.0, t_max, max(steps, 8), dtype="f8")
    angles = (_np.arange(bins, dtype="f8") + 0.5) * (2.0 * math.pi / bins)
    dh = _np.cos(angles)[:, None] * t[None, :]
    dv = _np.sin(angles)[:, None] * t[None, :]

    col = _np.rint(dh / scale_h + centre_col).astype("i8")
    row = _np.rint(centre_row - dv / scale_v).astype("i8")
    inside = (col >= 0) & (col < width) & (row >= 0) & (row < height)
    hit = _np.zeros(dh.shape, dtype=bool)
    hit[inside] = mask[row[inside], col[inside]]

    any_hit = hit.any(axis=1)
    last = (t.shape[0] - 1) - hit[:, ::-1].argmax(axis=1)
    profile = _np.full(bins, _np.nan, dtype="f8")
    profile[any_hit] = t[last[any_hit]]
    return profile, int(_np.count_nonzero(~any_hit))


# ---------------------------------------------------------------------------
# rasterising a projection, for the IoU
# ---------------------------------------------------------------------------

def _splat_mask(points, tris, origin, cell, shape, notes):
    """Rasterise a projected mesh into a boolean grid by area sampling.

    Barycentric samples are spread over the triangles in proportion to their
    projected area, enough of them that a grid cell inside the silhouette gets
    at least a few — which is a real rasterisation of the *surface*, not a splat
    of the vertices, so a coarse mesh does not read as a colander.  The RNG is
    seeded, so the number this produces is the same number tomorrow.
    """
    rows, cols = shape
    grid = _np.zeros(shape, dtype=bool)
    if tris is None or not len(tris):
        col = ((points[:, 0] - origin[0]) / cell).astype("i8")
        row = ((origin[1] - points[:, 1]) / cell).astype("i8")
        inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
        grid[row[inside], col[inside]] = True
        notes.append("the mesh has no faces, so its projection was rasterised "
                     "from its vertices alone and may read thinner than it is")
        return grid

    if len(tris) > MAX_RASTER_TRIANGLES:
        stride = int(math.ceil(len(tris) / float(MAX_RASTER_TRIANGLES)))
        tris = tris[::stride]
        notes.append("the IoU rasteriser sampled every %dth triangle (%d faces "
                     "is past its %d cap); the fit itself used every vertex"
                     % (stride, len(tris) * stride, MAX_RASTER_TRIANGLES))

    p0 = points[tris[:, 0]]
    e1 = points[tris[:, 1]] - p0
    e2 = points[tris[:, 2]] - p0
    area = 0.5 * _np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    total = float(area.sum())
    if total <= 0.0:
        return grid

    wanted = total / (cell * cell) * SPLAT_PER_CELL
    wanted = int(min(max(wanted, MIN_SPLAT_SAMPLES), MAX_SPLAT_SAMPLES))
    per_triangle = _np.maximum(
        1, _np.ceil(area / total * wanted).astype("i8"))
    index = _np.repeat(_np.arange(len(tris), dtype="i8"), per_triangle)

    rng = _np.random.default_rng(SPLAT_SEED)
    u = rng.random(index.shape[0])
    v = rng.random(index.shape[0])
    flip = (u + v) > 1.0
    u[flip] = 1.0 - u[flip]
    v[flip] = 1.0 - v[flip]
    sampled = p0[index] + u[:, None] * e1[index] + v[:, None] * e2[index]

    col = ((sampled[:, 0] - origin[0]) / cell).astype("i8")
    row = ((origin[1] - sampled[:, 1]) / cell).astype("i8")
    inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
    grid[row[inside], col[inside]] = True
    return grid


def _target_grid(mask, centre_px, scale_h, scale_v, origin, cell, shape):
    """The reference mask resampled onto the same grid, nearest neighbour."""
    rows, cols = shape
    hs = origin[0] + (_np.arange(cols, dtype="f8") + 0.5) * cell
    vs = origin[1] - (_np.arange(rows, dtype="f8") + 0.5) * cell
    col = _np.rint(hs / scale_h + centre_px[0]).astype("i8")
    row = _np.rint(centre_px[1] - vs / scale_v).astype("i8")
    height, width = mask.shape
    grid = _np.zeros(shape, dtype=bool)
    valid_c = (col >= 0) & (col < width)
    valid_r = (row >= 0) & (row < height)
    if not valid_c.any() or not valid_r.any():
        return grid
    sub = mask[_np.clip(row, 0, height - 1)][:, _np.clip(col, 0, width - 1)]
    grid[_np.ix_(valid_r, valid_c)] = sub[_np.ix_(valid_r, valid_c)]
    return grid


def _iou(a, b):
    union = float(_np.count_nonzero(a | b))
    if union <= 0.0:
        return 0.0
    return float(_np.count_nonzero(a & b)) / union


def _shape_iou(a, b):
    """IoU after both masks are cropped and fitted to one square.

    ``verify._normalise_mask``, verbatim: the framing-independent number, which
    answers "is it the right shape" separately from "is it in the right place at
    the right size".
    """
    left, _ = verify._normalise_mask(a)
    right, _ = verify._normalise_mask(b)
    if left is None or right is None:
        return None
    return _iou(left, right)


# ---------------------------------------------------------------------------
# parsing — everything, before anything is applied
# ---------------------------------------------------------------------------

def _view_requests(params):
    """``[{"axis", "image", "threshold"}]`` from ``views`` or from the sugar."""
    sugar = [(key, params[key]) for key in IMAGE_SUGAR if params.get(key) is not None]
    raw = params.get("views")
    if raw is not None and sugar:
        raise ForgeError(
            "Give either 'views' or the per-side shorthand (%s), not both — "
            "they are the same list said twice."
            % ", ".join(sorted(name for name, _ in sugar)))
    if raw is None:
        if not sugar:
            raise ForgeError(
                "fit_to_silhouette needs at least one reference picture: pass "
                '\'views\': [{"image": "<path>", "axis": "front"}], or the '
                "shorthand 'front_image' / 'side_image'.")
        return [{"axis": IMAGE_SUGAR[key], "image": value, "threshold": None,
                 "given": IMAGE_SUGAR[key]}
                for key, value in sorted(sugar, key=lambda pair: pair[0])]

    if not isinstance(raw, (list, tuple)) or not raw:
        raise ForgeError(
            "'views' must be a non-empty list of "
            '{"image": "<path>", "axis": "front"|"side"|...} objects.')
    if len(raw) > MAX_VIEWS:
        raise ForgeError("'views' takes at most %d entries (one per orthographic "
                         "side); %d were given." % (MAX_VIEWS, len(raw)))
    requests = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ForgeError("views[%d] must be an object, got %s."
                             % (index, type(entry).__name__))
        image = entry.get("image", entry.get("path"))
        if image is None:
            raise ForgeError("views[%d] is missing 'image' — the path to the "
                             "reference picture." % index)
        if not isinstance(image, str):
            raise ForgeError("views[%d].image must be a string path, got %s."
                             % (index, type(image).__name__))
        threshold = None
        if entry.get("threshold") is not None:
            threshold = get_float(entry, "threshold", minimum=0.0, maximum=1.0)
        requests.append({"axis": entry.get("axis"), "image": image,
                         "threshold": threshold, "given": entry.get("axis")})
    return requests


def _plan_view(request, world, index):
    """Everything about one view that never changes during the fit.

    The target outline is frozen **here**, against the mesh as it was before the
    first iteration: the alignment, the scale, the shared centre and the
    reference's radial profile are all computed once and reused, so the thing
    the fit is measured against at the end is the same thing it was aimed at at
    the start.  Recomputing the alignment from the deformed mesh would move the
    goalposts and make the before/after IoU meaningless.
    """
    warnings = []
    axis_key = resolve_view(request["axis"])
    right_index, right_sign, up_index, up_sign, depth_index = view_frame(axis_key)
    image = mask_from_image(request["image"], request["threshold"])

    bbox = _mask_bbox(image["mask"])
    if bbox is None:
        raise ForgeError(
            "Nothing could be separated from the background in %s: every pixel "
            "reads as background. Use a picture of the subject on a plain, "
            "contrasting background, or one with a transparent background."
            % os.path.basename(image["path"]))
    speck = _speck_warning(image["mask"], bbox, image["path"])
    if speck:
        warnings.append(speck)
    left, top, right, bottom = bbox
    mask_w = float(right - left + 1)
    mask_h = float(bottom - top + 1)

    h = right_sign * world[:, right_index]
    v = up_sign * world[:, up_index]
    h_lo, h_hi = float(h.min()), float(h.max())
    v_lo, v_hi = float(v.min()), float(v.max())
    mesh_w = h_hi - h_lo
    mesh_h = v_hi - v_lo
    if mesh_w <= 1e-9 and mesh_h <= 1e-9:
        raise ForgeError(
            "Seen from the %s the mesh projects to a single point, so there is "
            "no outline to fit. Check the object is not zero-scaled." % axis_key)

    fit = request["fit"]
    if fit == "height" and mesh_h > 1e-9:
        scale_v = mesh_h / mask_h
        scale_h = scale_v
    elif fit == "width" and mesh_w > 1e-9:
        scale_h = mesh_w / mask_w
        scale_v = scale_h
    elif fit == "bbox":
        scale_h = (mesh_w / mask_w) if mesh_w > 1e-9 else (mesh_h / mask_h)
        scale_v = (mesh_h / mask_h) if mesh_h > 1e-9 else scale_h
    else:  # the anchor extent is degenerate — fall back to the other one
        scale_h = scale_v = (mesh_w / mask_w) if mesh_w > 1e-9 else (mesh_h / mask_h)
        warnings.append(
            "the %s projection has no %s extent, so the reference was scaled on "
            "the other axis instead"
            % (axis_key, "height" if fit == "height" else "width"))

    centre = (0.5 * (h_lo + h_hi), 0.5 * (v_lo + v_hi))
    centre_px = (left + 0.5 * mask_w - 0.5, top + 0.5 * mask_h - 0.5)

    target_w = mask_w * scale_h
    target_h = mask_h * scale_v
    t_max = 0.5 * math.hypot(target_w, target_h) * 1.05
    reference, unresolved = _march_profile(
        image["mask"], centre_px[0], centre_px[1], scale_h, scale_v, t_max,
        int(max(mask_w, mask_h) * 2))
    reference = _fill_gaps(reference)
    if reference is None:
        raise ForgeError(
            "No ray out of the centre of %s found any silhouette, which should "
            "be impossible for a non-empty mask — the picture may be a single "
            "ring around its own middle." % os.path.basename(image["path"]))
    reference = _smooth_profile(reference)
    if unresolved:
        warnings.append(
            "%d of %d rays out of the centre of the %s reference left the mask "
            "immediately (a concave outline); their radius was interpolated from "
            "the neighbouring angles" % (unresolved, ANGLE_BINS, axis_key))

    # Two fixed frames, both computed once against the mesh as it is now: the
    # IoU frame (the before and the after have to be measured in the same
    # place), and the coarser-padded frame the mesh's own outline is read off
    # each iteration, which is roomier because the mesh moves inside it.
    lo_h = min(h_lo, centre[0] - 0.5 * target_w)
    hi_h = max(h_hi, centre[0] + 0.5 * target_w)
    lo_v = min(v_lo, centre[1] - 0.5 * target_h)
    hi_v = max(v_hi, centre[1] + 0.5 * target_h)
    span = max(hi_h - lo_h, hi_v - lo_v, 1e-9)
    origin, cell, shape = _frame(lo_h, hi_h, lo_v, hi_v, span, IOU_PAD, IOU_GRID)
    p_origin, p_cell, p_shape = _frame(lo_h, hi_h, lo_v, hi_v, span,
                                       PROFILE_PAD, PROFILE_GRID)

    return {
        "axis": request["given"] if isinstance(request["given"], str) else axis_key,
        "resolved_axis": axis_key,
        "index": index,
        "image": image,
        "bbox_px": [left, top, right, bottom],
        "right": (right_index, right_sign),
        "up": (up_index, up_sign),
        "depth_index": depth_index,
        "plane": _plane_name(right_index, up_index),
        "scale": (scale_h, scale_v),
        "centre": centre,
        "centre_px": centre_px,
        "reference": reference,
        "unresolved": unresolved,
        "target_size": (target_w, target_h),
        "mesh_size": (mesh_w, mesh_h),
        "origin": origin,
        "cell": cell,
        "shape": shape,
        "profile_origin": p_origin,
        "profile_cell": p_cell,
        "profile_shape": p_shape,
        "fit": fit,
        "warnings": warnings,
    }


def _frame(lo_h, hi_h, lo_v, hi_v, span, pad_fraction, grid):
    """``(origin, cell, shape)`` for a padded raster over a plane bounding box.

    ``origin`` is the TOP-LEFT corner in plane coordinates (h right, v up), so a
    row index counts downward exactly the way an image's does.
    """
    pad = pad_fraction * span
    cell = (span + 2.0 * pad) / float(grid)
    shape = (max(1, int(math.ceil((hi_v - lo_v + 2.0 * pad) / cell))),
             max(1, int(math.ceil((hi_h - lo_h + 2.0 * pad) / cell))))
    return (lo_h - pad, hi_v + pad), cell, shape


def _raster_profile(points, tris, plan, notes):
    """The mesh's own outline radius per angular bin, off a rasterised silhouette.

    Rasterise the projected FACES, then march the same rays the reference was
    measured with. See :data:`PROFILE_GRID` for why this is not done by binning
    the projected vertices.
    """
    origin, cell, shape = (plan["profile_origin"], plan["profile_cell"],
                           plan["profile_shape"])
    grid = _splat_mask(points, tris, origin, cell, shape, notes)
    centre_col = (plan["centre"][0] - origin[0]) / cell - 0.5
    centre_row = (origin[1] - plan["centre"][1]) / cell - 0.5
    t_max = math.hypot(shape[1] * cell, shape[0] * cell) * 0.5
    profile, unresolved = _march_profile(
        grid, centre_col, centre_row, cell, cell, t_max, 2 * max(shape))
    return _fill_gaps(profile), unresolved, grid


def _plane_points(plan, world):
    """The mesh's vertices as ``(n, 2)`` coordinates in this view's plane."""
    right_index, right_sign = plan["right"]
    up_index, up_sign = plan["up"]
    return _np.stack([right_sign * world[:, right_index],
                      up_sign * world[:, up_index]], axis=1)


def _measure(plan, world, tris, notes):
    """``(iou, shape_iou, outline_error)`` of the mesh against a frozen target."""
    points = _plane_points(plan, world)
    mesh_grid = _splat_mask(points, tris, plan["origin"], plan["cell"],
                            plan["shape"], notes)
    target_grid = _target_grid(plan["image"]["mask"], plan["centre_px"],
                               plan["scale"][0], plan["scale"][1],
                               plan["origin"], plan["cell"], plan["shape"])
    profile, _unresolved, _grid = _raster_profile(points, tris, plan, notes)
    if profile is None:  # pragma: no cover - a mesh with vertices always has one
        error = {"mean": None, "max": None}
    else:
        difference = _np.abs(_smooth_profile(profile) - plan["reference"])
        error = {"mean": _round(float(difference.mean()) * M_TO_MM, 3),
                 "max": _round(float(difference.max()) * M_TO_MM, 3)}
    return (_iou(mesh_grid, target_grid),
            _shape_iou(mesh_grid, target_grid),
            error)


# ---------------------------------------------------------------------------
# the warp
# ---------------------------------------------------------------------------

def _view_displacement(plan, world, tris, strength, falloff, notes):
    """The in-plane movement this one view asks of every vertex.

    Returns an ``(n, 3)`` world-space array whose depth-axis column is exactly
    zero — a front reference has no opinion about Y and does not get one.
    """
    right_index, right_sign = plan["right"]
    up_index, up_sign = plan["up"]
    points = _plane_points(plan, world)
    h = points[:, 0] - plan["centre"][0]
    v = points[:, 1] - plan["centre"][1]
    radius = _np.hypot(h, v)
    angle = _np.arctan2(v, h)

    profile, _unresolved, _grid = _raster_profile(points, tris, plan, notes)
    if profile is None:  # pragma: no cover - guarded upstream
        return _np.zeros_like(world)
    profile = _smooth_profile(profile)

    mesh_radius = _np.maximum(_sample_profile(profile, angle), 1e-9)
    target_radius = _np.maximum(_sample_profile(plan["reference"], angle), 0.0)
    scale = target_radius / mesh_radius

    weight = 1.0
    if falloff > 0.0:
        weight = _np.clip(radius / mesh_radius, 0.0, 1.0) ** falloff
    move = (scale - 1.0) * radius * weight * strength

    unit = _np.maximum(radius, 1e-12)
    delta = _np.zeros_like(world)
    delta[:, right_index] = right_sign * move * (h / unit)
    delta[:, up_index] = up_sign * move * (v / unit)
    return delta


def _smooth_field(delta, edges, factor):
    """Laplacian passes over the DISPLACEMENT, never over the mesh."""
    if factor <= 0.0 or not len(edges):
        return delta
    a, b = edges[:, 0], edges[:, 1]
    for _pass in range(SMOOTH_PASSES):
        total = _np.zeros_like(delta)
        count = _np.zeros(delta.shape[0], dtype="f8")
        _np.add.at(total, a, delta[b])
        _np.add.at(total, b, delta[a])
        _np.add.at(count, a, 1.0)
        _np.add.at(count, b, 1.0)
        has = count > 0
        average = delta.copy()
        average[has] = total[has] / count[has][:, None]
        delta = (1.0 - factor) * delta + factor * average
    return delta


def _symmetry_partners(world, axis_index, plane):
    """For each vertex, the index of the vertex nearest its own mirror."""
    from mathutils.kdtree import KDTree

    count = world.shape[0]
    tree = KDTree(count)
    for index in range(count):
        tree.insert((float(world[index, 0]), float(world[index, 1]),
                     float(world[index, 2])), index)
    tree.balance()
    partners = _np.empty(count, dtype="i8")
    for index in range(count):
        point = [float(world[index, 0]), float(world[index, 1]),
                 float(world[index, 2])]
        point[axis_index] = 2.0 * plane - point[axis_index]
        _co, found, _dist = tree.find(point)
        partners[index] = index if found is None else found
    return partners


def _apply_symmetry(delta, partners, axis_index):
    """Average each vertex's movement with its mirror's, mirrored back."""
    mirrored = delta[partners].copy()
    mirrored[:, axis_index] *= -1.0
    return 0.5 * (delta + mirrored)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

@command("fit_to_silhouette")
def cmd_fit_to_silhouette(params):
    """Deform a mesh until its outline matches one or more reference pictures.

    Parameters
    ----------
    ``object``
        The mesh to fit. Omitted = the active object; a near-miss name comes
        back with a suggestion rather than a traceback.
    ``views``
        ``[{"image": <path>, "axis": "front"|"back"|"side"|"left"|"right"|
        "top"|"bottom", "threshold"?: 0-1}]`` — one entry per reference. The
        shorthand ``front_image`` / ``side_image`` / ... is the same list said
        shorter, and giving both is an error rather than a merge.
    ``strength``
        0-1, default 1. The global lerp: how much of the movement each iteration
        actually applies. **0 is an exact no-op** — the mesh is measured and put
        back untouched, which is how you get the before/after numbers without
        committing to the fit.
    ``iterations``
        1-12, default 3. Each pass re-reads the mesh's own outline, so the
        passes after the first are what recover the accuracy the smoothing pass
        gives away.
    ``smooth``
        0-1, default 0.5. Laplacian fairing of the displacement field over the
        mesh's edges. The MESH is never smoothed; sculpted detail is untouched.
    ``falloff``
        0-4, default 0. ``0`` scales the whole cross-section together (relative
        interior proportions preserved exactly); above 0 the movement is
        weighted by ``(r / R_mesh) ** falloff`` so the rim moves and the core
        stays.
    ``symmetry``
        ``false`` (default), ``true`` (= ``"X"``) or an axis name. Averages each
        vertex's movement with its mirror's through the mesh's own bounding-box
        centre plane, so a symmetric sculpt stays symmetric even when the
        reference photograph was not.
    ``fit``
        ``"height"`` (default), ``"width"`` or ``"bbox"`` — which extent of the
        mesh's projection the reference is scaled to match. ``height`` is the
        default because the vertical axis is the one front and side views share,
        which is what makes two references agree with each other.

    Returns a report whose load-bearing numbers are the per-view ``iou_before``
    and ``iou_after``, measured against the same frozen target on the same fixed
    grid, and ``outline_error_*_mm``, the mean and worst radial gap between the
    two outlines in millimetres.
    """
    started = time.monotonic()
    _require_numpy()
    warnings = []
    notes = []

    obj = mechanism.resolve_target(params)
    if obj.type != "MESH":
        raise ForgeError(
            "%r is a %s. fit_to_silhouette moves vertices, so it needs a MESH — "
            "convert it first, or name the mesh explicitly."
            % (obj.name, obj.type))
    mesh = obj.data
    vertex_count = len(mesh.vertices)
    if vertex_count < 3:
        raise ForgeError(
            "%r has %d vertices. There is no outline to fit a silhouette to."
            % (obj.name, vertex_count))

    strength = get_float(params, "strength", 1.0, minimum=0.0, maximum=1.0)
    iterations = get_int(params, "iterations", 3, minimum=1,
                         maximum=MAX_ITERATIONS)
    smooth = get_float(params, "smooth", 0.5, minimum=0.0, maximum=1.0)
    falloff = get_float(params, "falloff", 0.0, minimum=0.0, maximum=4.0)
    raw_fit = params.get("fit", "height")
    fit = raw_fit.strip().lower() if isinstance(raw_fit, str) else None
    if fit not in ("height", "width", "bbox"):
        raise ForgeError(
            "'fit' must be 'height' (the default), 'width' or 'bbox' — which "
            "extent of the mesh's projection the reference is scaled to match; "
            "got %r." % (raw_fit,))

    raw_symmetry = params.get("symmetry")
    symmetry_axis = None
    if isinstance(raw_symmetry, str):
        letter = raw_symmetry.strip().upper()
        if letter not in AXIS_NAMES:
            raise ForgeError("'symmetry' must be false, true, or one of X, Y, Z; "
                             "got %r." % raw_symmetry)
        symmetry_axis = AXIS_NAMES.index(letter)
    elif raw_symmetry is not None:
        if get_bool(params, "symmetry", False):
            symmetry_axis = 0

    # --- parse every view before touching a single vertex -------------------
    requests = _view_requests(params)
    seen = {}
    for request in requests:
        request["fit"] = fit
        key = resolve_view(request["axis"])
        if key in seen:
            raise ForgeError(
                "Two views are both %r (%s and %s). One picture per side."
                % (key, os.path.basename(str(seen[key])),
                   os.path.basename(str(request["image"]))))
        seen[key] = request["image"]

    matrix = obj.matrix_world.copy()
    inverse = matrix.inverted_safe()
    world = _world_coords(mesh, matrix)
    original = world.copy()
    tris = _triangles(mesh)

    plans = [_plan_view(request, world, index)
             for index, request in enumerate(requests)]
    for plan in plans:
        warnings.extend(plan["warnings"])
        if plan["image"]["confidence"] == "low":
            for reason in plan["image"]["confidence_reasons"]:
                warnings.append("%s reference: %s" % (plan["resolved_axis"], reason))

    constrained = set()
    for plan in plans:
        constrained.add(plan["right"][0])
        constrained.add(plan["up"][0])
    untouched = [AXIS_NAMES[i] for i in range(3) if i not in constrained]

    shape_keys = getattr(mesh, "shape_keys", None)
    key_count = len(shape_keys.key_blocks) if shape_keys else 0
    if key_count:
        warnings.append(
            "%r has %d shape key(s). The fit was written to the base mesh "
            "coordinates; the shape keys still hold the positions they held, so "
            "the viewport shows the fit only while the active key is the basis."
            % (obj.name, key_count))
    if obj.modifiers:
        notes.append("modifiers (%s) still evaluate on top of the fitted base "
                     "mesh; the outline you see in the viewport is theirs, not "
                     "this command's"
                     % ", ".join(m.name for m in obj.modifiers))

    # --- before -------------------------------------------------------------
    before = [_measure(plan, world, tris, notes) for plan in plans]

    # --- the fit ------------------------------------------------------------
    edges = _edge_pairs(mesh) if smooth > 0.0 else _np.zeros((0, 2), dtype="i4")
    partners = None
    symmetry_plane = None
    if symmetry_axis is not None:
        if vertex_count > SYMMETRY_VERTEX_LIMIT:
            warnings.append(
                "symmetry was skipped: pairing %d vertices with their mirrors "
                "costs more than the symmetry is worth (the limit is %d). Fit "
                "without it, or decimate first."
                % (vertex_count, SYMMETRY_VERTEX_LIMIT))
            symmetry_axis = None
        else:
            symmetry_plane = float(0.5 * (world[:, symmetry_axis].min()
                                          + world[:, symmetry_axis].max()))
            partners = _symmetry_partners(world, symmetry_axis, symmetry_plane)

    axis_votes = _np.zeros(3, dtype="f8")
    for plan in plans:
        axis_votes[plan["right"][0]] += 1.0
        axis_votes[plan["up"][0]] += 1.0
    divisor = _np.maximum(axis_votes, 1.0)

    iteration_max_mm = []
    for _pass in range(iterations):
        accumulated = _np.zeros_like(world)
        for plan in plans:
            accumulated += _view_displacement(plan, world, tris, strength,
                                              falloff, notes)
        accumulated /= divisor
        accumulated = _smooth_field(accumulated, edges, smooth)
        if symmetry_axis is not None:
            accumulated = _apply_symmetry(accumulated, partners, symmetry_axis)
        step = float(_np.abs(accumulated).max()) if accumulated.size else 0.0
        iteration_max_mm.append(_round(step * M_TO_MM, 4))
        world = world + accumulated
        if step * M_TO_MM < MOVED_EPS_MM:
            break

    movement = world - original
    distance_mm = _np.linalg.norm(movement, axis=1) * M_TO_MM
    moved = int(_np.count_nonzero(distance_mm > MOVED_EPS_MM))
    applied = moved > 0

    if applied:
        with common.object_mode():
            local = verify._to_world(world, inverse)
            mesh.vertices.foreach_set("co", local.ravel().astype("f4"))
            mesh.update()
            try:
                mesh.calc_loop_triangles()
            except (AttributeError, RuntimeError):  # pragma: no cover
                pass
    else:
        notes.append("nothing moved, so the mesh was not written to at all — "
                     "the numbers below are a measurement, not an edit")

    # --- after --------------------------------------------------------------
    after = [_measure(plan, world, tris, notes) for plan in plans]

    view_reports = []
    for plan, (iou_b, shape_b, error_b), (iou_a, shape_a, error_a) in zip(
            plans, before, after):
        image = plan["image"]
        view_reports.append({
            "axis": plan["axis"],
            "resolved_axis": plan["resolved_axis"],
            "image": image["path"],
            "image_size": image["size"],
            "plane": plan["plane"],
            "depth_axis": AXIS_NAMES[plan["depth_index"]],
            "constrains": [AXIS_NAMES[plan["right"][0]],
                           AXIS_NAMES[plan["up"][0]]],
            "mask": {
                "source": image["source"],
                "threshold": image["threshold"],
                "coverage": image["coverage"],
                "pixels": image["pixels"],
                "bbox_px": plan["bbox_px"],
                "background_uniformity": image["background_uniformity"],
            },
            "confidence": image["confidence"],
            "confidence_reasons": image["confidence_reasons"],
            "fit": plan["fit"],
            "scale_mm_per_pixel": [_round(plan["scale"][0] * M_TO_MM, 5),
                                   _round(plan["scale"][1] * M_TO_MM, 5)],
            "centre_mm": [_round(plan["centre"][0] * M_TO_MM, 3),
                          _round(plan["centre"][1] * M_TO_MM, 3)],
            "target_size_mm": [_round(plan["target_size"][0] * M_TO_MM, 3),
                               _round(plan["target_size"][1] * M_TO_MM, 3)],
            "mesh_size_before_mm": [_round(plan["mesh_size"][0] * M_TO_MM, 3),
                                    _round(plan["mesh_size"][1] * M_TO_MM, 3)],
            "iou_before": claim(_round(iou_b, 4), MEASURED,
                                "overlap of the mesh's projected silhouette "
                                "with the reference, on a fixed %d-cell grid"
                                % IOU_GRID),
            "iou_after": claim(_round(iou_a, 4), MEASURED,
                               "the same measurement, same grid, same target, "
                               "after the fit"),
            "iou_gain": _round(iou_a - iou_b, 4),
            "shape_iou_before": claim(_round(shape_b, 4), MEASURED,
                                      "the same overlap with both silhouettes "
                                      "cropped and fitted to one square — shape "
                                      "without framing"),
            "shape_iou_after": claim(_round(shape_a, 4), MEASURED,
                                     "shape without framing, after the fit"),
            "outline_error_before_mm": error_b,
            "outline_error_after_mm": error_a,
            "rays_unresolved": plan["unresolved"],
            "grid": [plan["shape"][0], plan["shape"][1]],
            "grid_cell_mm": _round(plan["cell"] * M_TO_MM, 4),
        })

    # The rasteriser says the same thing once per measurement; the artist wants
    # to read it once.
    notes = list(dict.fromkeys(notes))
    warnings = list(dict.fromkeys(warnings))

    axis_max_mm = {
        AXIS_NAMES[i]: _round(float(_np.abs(movement[:, i]).max()) * M_TO_MM, 4)
        for i in range(3)
    }

    return {
        "object": obj.name,
        "vertices": vertex_count,
        "faces": len(mesh.polygons),
        "views": view_reports,
        "strength": _round(strength, 4),
        "iterations": iterations,
        "iterations_run": len(iteration_max_mm),
        "smooth": _round(smooth, 4),
        "falloff": _round(falloff, 4),
        "fit": fit,
        "symmetry": AXIS_NAMES[symmetry_axis] if symmetry_axis is not None else None,
        "symmetry_plane_mm": (_round(symmetry_plane * M_TO_MM, 3)
                              if symmetry_plane is not None else None),
        "applied": applied,
        "moved": moved,
        "moved_fraction": _round(moved / float(vertex_count), 4),
        "max_displacement_mm": _round(float(distance_mm.max()), 4),
        "mean_displacement_mm": _round(float(distance_mm.mean()), 4),
        "displacement_by_axis_mm": axis_max_mm,
        "constrained_axes": [AXIS_NAMES[i] for i in sorted(constrained)],
        "untouched_axes": untouched,
        "iteration_max_mm": iteration_max_mm,
        "dimensions_before_mm": _dimensions_mm(original),
        "dimensions_after_mm": _dimensions_mm(world),
        "bounds_before_mm": _bounds_mm(original),
        "bounds_after_mm": _bounds_mm(world),
        "shape_keys": key_count,
        "modifiers": [m.name for m in obj.modifiers],
        "method": (
            "per view: the reference is thresholded to a binary silhouette and "
            "scaled (fit=%s) onto the mesh's own projection; both outlines are "
            "measured as a radius per angular bin (%d bins) by marching rays "
            "out of the shared centre — the reference off its picture, the mesh "
            "off a %d-cell rasterisation of its projected faces; every vertex "
            "moves along its own radius by the ratio of the two radii at its "
            "angle, weighted by (r/R)^falloff; the displacement field (never "
            "the mesh) is Laplacian-smoothed over the edge graph and optionally "
            "mirrored, then applied. Repeated %d time(s)."
            % (fit, ANGLE_BINS, PROFILE_GRID, iterations)),
        "honesty": HONESTY,
        "notes": notes,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }
