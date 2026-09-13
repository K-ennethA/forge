"""Deterministic verifier-pick over a seed ensemble.  No VLM, no CLIP, ever.

Image-to-3D is a sampler, and a sampler's seed is a lottery ticket.  Published
oracle best-of-5 on this class of model is roughly a **26% Chamfer improvement**
over one draw (arXiv 2604.27106) - which says the good mesh is usually already in
a handful of samples, and that the whole problem is *picking* it.

Two things are settled about how to pick, and this module implements both:

* **Consensus beats a judge.**  A symmetric-Chamfer *medoid* - the candidate that
  agrees most with the others - outperformed a VLM verifier on CAD generation,
  and the benefit plateaus around N = 9 (arXiv 2608.09706).  Agreement is
  measurable; "which of these looks right" is not.
* **The one image-side signal that works is geometric.**  ShapeGen's picker
  compares a rendered normal map of the front view against the input image with
  DINO cosine (arXiv 2511.20624).  meshgen has no DINO to spare at pick time and
  will not add one, so it uses the geometric half of the same idea: the
  candidate's silhouette against the input's prepared silhouette.
* **Render-CLIP is chance** on this task and appears nowhere here, by name, so
  that nobody re-adds it.

Everything below is numpy over vertices, triangles and boolean grids.  Same
input, same answer, on any machine - which is the property that lets the picker
be believed without a human looking at the meshes.

``numpy`` is imported at module scope, and this module is imported **lazily** by
the backend (only when an ensemble is actually requested), so the service's
stdlib-only single-shot path is untouched.  Chamfer sampling uses
``numpy.random.default_rng`` with a fixed seed - the randomness is a fixed
quadrature rule, not a source of run-to-run variation.
"""

from __future__ import annotations

import numpy as np

#: Points sampled per mesh for the Chamfer medoid.  4096 is the knee: the
#: symmetric distance between two ~200k-triangle meshes is stable to about 1e-4
#: of the bbox diagonal at this count, and the pairwise cost is 4096**2 = 16.7M
#: distances per pair, which numpy does in well under a second.  It is a
#: constant, not a tunable, because a comparison run at two sample counts is not
#: a comparison.
CHAMFER_SAMPLES = 4096

#: Fixed seed for that sampling.  See the module docstring: a quadrature rule.
CHAMFER_SEED = 20260913

#: Raster the two silhouettes are compared on, after both are cropped to their
#: bounding box and squared up.  32-cube grids project to 32x32, so anything much
#: above this is interpolating detail the grid does not have.
SILHOUETTE_RASTER = 64


class EnsembleError(RuntimeError):
    """A candidate could not be scored, with the reason in the message."""


# ---------------------------------------------------------------------------
# the structure grid, recovered exactly from core's cube mesh
# ---------------------------------------------------------------------------
def occupancy_from_cube_mesh(verts, faces, resolution):
    """Invert ``VoxelToMeshBasic`` back to the dense boolean grid it drew.

    Core's ``voxel_to_mesh`` emits, for every solid voxel face that touches
    empty space, one unit quad (two triangles) whose corners are **integer**
    lattice points, then maps the lattice to ``[-1, 1]`` with
    ``(corner - R/2) / (R/2)`` and flips the axis order.  ``SaveGLB`` writes
    those vertices through with no node matrix and no rotation.  So the mesh is
    the grid, and this is a decode rather than an estimate.

    Two things make the inversion exact rather than approximate:

    * **Interior voxels have no exposed face**, so the face set alone under-reports
      a solid block.  The full solid is recovered by **parity**: along any axis,
      the faces perpendicular to it sit at exactly the solid/empty transitions,
      so voxel *i* is solid iff an odd number of transitions lie at or below it.
    * **Both triangles of a quad share the quad's minimum corner** (the split is
      corners 0-1-2 and 0-2-3, which both contain the diagonal pair 0 and 2), so
      a triangle's per-axis minimum *is* its cell index on the two axes it spans.

    The answer is computed independently from all three axis families and the
    three must agree; they cannot, unless the mesh really is a closed voxel
    boundary.  That is the self-check that keeps a silently wrong grid - which
    would produce a plausible consensus table - out of the report.
    """
    R = int(resolution)
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        raise EnsembleError("the structure probe produced an empty mesh - the "
                            "sampler decoded an entirely empty occupancy grid")

    lattice = (verts + 1.0) * (R / 2.0)
    index = np.rint(lattice)
    residual = float(np.abs(lattice - index).max())
    if residual > 1e-3:
        raise EnsembleError(
            f"structure mesh vertices are {residual:.4g} off the {R}-grid - this "
            "is not a VoxelToMeshBasic cube mesh at that resolution "
            "(wrong resolution, or the export path changed)")
    index = np.clip(index.astype(np.int64), 0, R)

    tri = index[faces]
    lo = tri.min(axis=1)
    hi = tri.max(axis=1)
    constant = lo == hi
    usable = constant.sum(axis=1) == 1
    if not usable.any():
        raise EnsembleError("no axis-aligned faces in the structure mesh")

    lo = lo[usable]
    axes = np.argmax(constant[usable], axis=1)

    grids = []
    for axis in range(3):
        pick = axes == axis
        if not pick.any():
            continue
        others = [k for k in range(3) if k != axis]
        rows = lo[pick]
        crossing = np.zeros((R + 1, R, R), dtype=bool)
        crossing[rows[:, axis], rows[:, others[0]], rows[:, others[1]]] = True
        parity = np.cumsum(crossing[:R].astype(np.int64), axis=0) % 2
        grids.append(np.moveaxis(parity.astype(bool), (0, 1, 2),
                                 (axis, others[0], others[1])))

    for other in grids[1:]:
        if not np.array_equal(other, grids[0]):
            raise EnsembleError(
                "the three axis families disagree about the structure grid - the "
                "saved mesh is not a closed voxel boundary")
    # core's last act before returning the mesh is ``torch.fliplr(vertices)``,
    # which reverses (z, y, x) into (x, y, z).  Undoing it here means the grid
    # that comes back is indexed the way ``VaeDecodeStructureTrellis2`` indexed
    # it, not the way the exporter happened to write it out.  Consensus IoU is
    # blind to the difference (every candidate is flipped identically); a reader
    # comparing this against core's source is not.
    return np.ascontiguousarray(np.transpose(grids[0], (2, 1, 0)))


# ---------------------------------------------------------------------------
# grid agreement
# ---------------------------------------------------------------------------
def grid_iou(a, b):
    union = int(np.count_nonzero(a | b))
    if union == 0:
        return 1.0 if not np.any(a) and not np.any(b) else 0.0
    return float(np.count_nonzero(a & b)) / float(union)


def pairwise_iou(grids):
    """Symmetric IoU matrix with 1.0 on the diagonal."""
    n = len(grids)
    matrix = np.eye(n, dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i, j] = matrix[j, i] = grid_iou(grids[i], grids[j])
    return matrix


def agreement(matrix):
    """Mean agreement of each candidate with the OTHERS (self excluded).

    Excluding the diagonal matters: with it, every candidate carries a free 1.0
    and an N of 2 cannot be separated at all.
    """
    matrix = np.asarray(matrix, dtype=np.float64)
    n = matrix.shape[0]
    if n < 2:
        return np.ones(n, dtype=np.float64)
    return (matrix.sum(axis=1) - np.diag(matrix)) / (n - 1)


# ---------------------------------------------------------------------------
# silhouette agreement with the drawing
# ---------------------------------------------------------------------------
def _square_crop(mask):
    """Crop to the content's bounding box, then pad to a square about its centre.

    Scale and position are thrown away (the grid fills its cube, the prepared
    image fills 1/1.1 of its frame) and **proportion is kept**, because
    proportion is the thing a wrong structure gets wrong.
    """
    mask = np.asarray(mask, dtype=bool)
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return np.zeros((1, 1), dtype=bool)
    sub = mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    side = max(sub.shape)
    out = np.zeros((side, side), dtype=bool)
    top = (side - sub.shape[0]) // 2
    left = (side - sub.shape[1]) // 2
    out[top:top + sub.shape[0], left:left + sub.shape[1]] = sub
    return out


def _resample(mask, size):
    """Nearest-neighbour resample of a square boolean mask.  No interpolation:
    a half-covered pixel has no meaning in an IoU and averaging invents one."""
    mask = np.asarray(mask, dtype=bool)
    if mask.shape[0] == size and mask.shape[1] == size:
        return mask
    ys = (np.arange(size) * mask.shape[0]) // size
    xs = (np.arange(size) * mask.shape[1]) // size
    return mask[np.ix_(ys, xs)]


def normalise_mask(mask, size=SILHOUETTE_RASTER):
    return _resample(_square_crop(mask), size)


#: The 24 axis-aligned labellings of a projection: 3 projection axes x 4
#: quarter-turns x 2 mirrors.  It is a search over *labelling*, not a fit -
#: the same discipline ``mesh_metrics.best_orientation`` uses, and for the same
#: reason: nothing is rotated by a free angle, so a genuinely wrong shape cannot
#: be aligned into looking right.
def projections(grid):
    grid = np.asarray(grid, dtype=bool)
    for axis in range(3):
        flat = grid.any(axis=axis)
        for turn in range(4):
            rotated = np.rot90(flat, turn)
            yield (axis, turn, 0), rotated
            yield (axis, turn, 1), rotated[:, ::-1]


def silhouette_scores(grid, mask, size=SILHOUETTE_RASTER):
    """``{labelling: iou}`` of every axis-aligned projection against ``mask``."""
    target = normalise_mask(mask, size)
    out = {}
    for label, view in projections(grid):
        candidate = _resample(_square_crop(view), size)
        union = int(np.count_nonzero(candidate | target))
        out[label] = (float(np.count_nonzero(candidate & target)) / union
                      if union else 0.0)
    return out


def silhouette_agreement(grids, mask, size=SILHOUETTE_RASTER):
    """Per-candidate silhouette IoU under ONE shared labelling.

    The camera behind a found image is unknown - a photo is perspective at an
    arbitrary azimuth, and the grid is orthographic and axis-aligned - so an
    absolute IoU here is not a quality score and is never treated as one.  What
    *is* meaningful is the comparison between candidates, and that only holds if
    they are all read the same way round.  So the labelling is chosen once, by
    the candidate that explains the drawing best under any labelling, and then
    every candidate is scored under that one.

    Returns ``(scores, labelling)``.
    """
    tables = [silhouette_scores(grid, mask, size) for grid in grids]
    best_label, best_score = None, -1.0
    for label in sorted(tables[0]):          # sorted + strict > : ties go to the
        score = max(table[label] for table in tables)   # first label, every time
        if score > best_score:
            best_label, best_score = label, score
    return ([float(table[best_label]) for table in tables],
            tuple(int(v) for v in best_label))


# ---------------------------------------------------------------------------
# Chamfer, on sampled point sets
# ---------------------------------------------------------------------------
def sample_surface(verts, faces, count=CHAMFER_SAMPLES, seed=CHAMFER_SEED):
    """Area-weighted uniform points on a triangle soup, deterministically.

    Area weighting is what makes the sample a sample *of the surface* rather
    than of the tessellation: without it a densely triangulated flat panel would
    dominate a sparsely triangulated curved one and Chamfer would score the
    remesher, not the shape.
    """
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    tris = verts[faces]
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = 0.5 * np.linalg.norm(cross, axis=1)
    total = float(area.sum())
    if not np.isfinite(total) or total <= 0.0:
        raise EnsembleError("mesh has no surface area to sample")

    rng = np.random.default_rng(seed)
    index = rng.choice(faces.shape[0], size=count, p=area / total)
    u = rng.random(count)
    v = rng.random(count)
    over = u + v > 1.0
    u[over] = 1.0 - u[over]
    v[over] = 1.0 - v[over]
    picked = tris[index]
    return (picked[:, 0]
            + u[:, None] * (picked[:, 1] - picked[:, 0])
            + v[:, None] * (picked[:, 2] - picked[:, 0]))


def _one_way(a, b, chunk=512):
    out = np.empty(a.shape[0], dtype=np.float64)
    for start in range(0, a.shape[0], chunk):
        block = a[start:start + chunk]
        d = ((block[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
        out[start:start + chunk] = d.min(axis=1)
    return out


def symmetric_chamfer(a, b):
    """Mean of the two one-way mean squared nearest-neighbour distances.

    Squared, not root-of-mean-squared: the medoid only ever compares these
    against each other, and a monotone transform of a ranking key is noise in
    the write-up.  The unit is (object diagonal)**2 because every point set is
    normalised before it gets here.
    """
    return 0.5 * (float(_one_way(a, b).mean()) + float(_one_way(b, a).mean()))


def chamfer_matrix(point_sets):
    n = len(point_sets)
    matrix = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i, j] = matrix[j, i] = symmetric_chamfer(point_sets[i], point_sets[j])
    return matrix


def chamfer_consensus(matrix):
    """Mean distance from each candidate to the others.  Lower is the medoid."""
    matrix = np.asarray(matrix, dtype=np.float64)
    n = matrix.shape[0]
    if n < 2:
        return np.zeros(n, dtype=np.float64)
    return (matrix.sum(axis=1) - np.diag(matrix)) / (n - 1)


# ---------------------------------------------------------------------------
# tier 1: pick a structure grid
# ---------------------------------------------------------------------------
#: How far below the best candidate's silhouette IoU a grid may sit before it is
#: rejected as disagreeing with the drawing.  RELATIVE, not absolute, and that is
#: forced by the physics: the camera behind a found image is unknown, so the
#: absolute number is not a quality score (see :func:`silhouette_agreement`).
#: 0.15 of IoU is a large disagreement between two decodes of the same picture.
SILHOUETTE_MARGIN = 0.15


def select_structure(candidates, mask=None, margin=SILHOUETTE_MARGIN):
    """Pick one structure grid out of N.  Returns a full, honest report.

    ``candidates`` is ``[{"seed": int, "grid": ndarray}, ...]``.

    Order, and why:

    1. **Silhouette gate first.**  A grid that does not explain the drawing must
       not get a vote on what the others should look like - otherwise two bad
       draws that agree with each other outvote three good ones.  The gate is
       relative and it **never leaves fewer than two survivors**: with one
       candidate standing there is no consensus left to take, and a gate that can
       empty the field is a gate that decides the answer by itself.
    2. **Medoid of the survivors' pairwise IoU.**  The grid most agreed-with.
    3. **Ties**: higher silhouette, then lowest seed - so the answer is the same
       on the next machine.
    """
    grids = [c["grid"] for c in candidates]
    seeds = [int(c["seed"]) for c in candidates]
    n = len(grids)
    if n == 0:
        raise EnsembleError("no structure candidates to choose between")

    report = {
        "candidates": [{"seed": seed,
                        "occupied": int(np.count_nonzero(grid)),
                        "resolution": int(grid.shape[-1])}
                       for seed, grid in zip(seeds, grids)],
        "silhouette_labelling": None,
        "rejected": [],
    }

    silhouette = [None] * n
    if mask is not None and n > 1:
        scores, label = silhouette_agreement(grids, mask)
        silhouette = scores
        report["silhouette_labelling"] = list(label)
        cutoff = max(scores) - margin
        ranked = sorted(range(n), key=lambda i: (-scores[i], seeds[i]))
        keep = [i for i in ranked if scores[i] >= cutoff]
        if len(keep) < 2:
            keep = ranked[:min(2, n)]
        keep.sort()          # report in candidate order, not in gate-rank order
        dropped = set(range(n)) - set(keep)
        report["rejected"] = [
            {"seed": seeds[i],
             "silhouette_iou": round(scores[i], 4),
             "reason": f"silhouette IoU {scores[i]:.3f} is more than {margin} "
                       f"below the best candidate's {max(scores):.3f} - this "
                       "grid does not explain the drawing"}
            for i in sorted(dropped, key=lambda k: seeds[k])]
    else:
        keep = list(range(n))

    for entry, value in zip(report["candidates"], silhouette):
        entry["silhouette_iou"] = None if value is None else round(float(value), 4)

    matrix = pairwise_iou([grids[i] for i in keep])
    means = agreement(matrix)
    for position, i in enumerate(keep):
        report["candidates"][i]["grid_agreement"] = round(float(means[position]), 4)
    for i in range(n):
        report["candidates"][i].setdefault("grid_agreement", None)

    order = sorted(
        range(len(keep)),
        key=lambda p: (-round(float(means[p]), 6),
                       -(silhouette[keep[p]] or 0.0),
                       seeds[keep[p]]))
    winner = keep[order[0]]

    report["pairwise_iou"] = [[round(float(v), 4) for v in row] for row in matrix]
    report["compared_seeds"] = [seeds[i] for i in keep]
    report["winner"] = {
        "seed": seeds[winner],
        "grid_agreement": round(float(means[order[0]]), 4),
        "silhouette_iou": (None if silhouette[winner] is None
                           else round(float(silhouette[winner]), 4)),
    }
    report["why"] = (
        f"seed {seeds[winner]} is the medoid: its occupancy grid agrees with the "
        f"other {len(keep) - 1} candidate(s) at mean IoU "
        f"{means[order[0]]:.3f}, the highest of the {len(keep)} compared"
        + (f" (of {n} generated; {n - len(keep)} rejected on silhouette)"
           if len(keep) != n else "")
        + "."
    ) if len(keep) > 1 else (
        f"seed {seeds[winner]} was the only candidate that could be compared.")
    return report


# ---------------------------------------------------------------------------
# tier 2: pick a finished mesh
# ---------------------------------------------------------------------------
#: Relative topology gates.  Absolute ones are useless here and meshgen has the
#: measurement that proves it: a GOOD run of this pipeline exports 9 boundary and
#: 210 non-manifold edges (meshgen/README.md, "What comes out is a *diagnosable*
#: mesh"), so "must be manifold" would reject every candidate every time.  These
#: fire only on a candidate that is much worse than its own batch's best.
GATE_SELF_INTERSECTION_FACTOR = 3.0
GATE_SELF_INTERSECTION_FLOOR = 50
GATE_SHELL_FACTOR = 3.0
GATE_SHELL_FLOOR = 8

#: Silhouette IoU is rounded to this many decimals before it is used as the
#: primary rank key.  Measured reason: across the entire A/B tuning sweep IoU
#: moved only from 0.9763 to 0.9801 while the meshes differed enormously
#: (meshgen/README.md) - it is a guard that the object was not eaten, not a
#: quality ordering.  Rounding hands the decision to the consensus medoid
#: wherever the silhouettes are indistinguishable, which is nearly always.
SILHOUETTE_RANK_DECIMALS = 3


def select_mesh(candidates):
    """Pick one finished mesh out of N.  ``candidates`` is a list of dicts:

    ``{"seed", "mesh_path", "metrics": <mesh_metrics.score output>,
       "points": ndarray}``

    Gates first (topology, relative), then rank.  A gate that would empty the
    field is dropped and said so in the report, because a picker that can refuse
    to pick is worse than one that picks the least bad.
    """
    n = len(candidates)
    if n == 0:
        raise EnsembleError("no mesh candidates to choose between")

    report = {"candidates": [], "rejected": []}
    for candidate in candidates:
        metrics = candidate.get("metrics") or {}
        report["candidates"].append({
            "seed": int(candidate["seed"]),
            "mesh_path": str(candidate.get("mesh_path") or ""),
            "silhouette_iou": _round(metrics.get("silhouette_iou")),
            "edge_sharpness": _round(metrics.get("edge_sharpness")),
            "crease_length": _round(metrics.get("crease_length"), 2),
            "self_intersections": metrics.get("self_intersections"),
            "components": metrics.get("components"),
            "nonmanifold_edges": metrics.get("nonmanifold_edges"),
            "faces": metrics.get("faces"),
        })

    alive = [i for i in range(n)
             if (candidates[i].get("metrics") or {}).get("faces")]
    for i in range(n):
        if i not in alive:
            report["rejected"].append(
                {"seed": int(candidates[i]["seed"]),
                 "reason": "the mesh could not be read or had no triangles"})
    if not alive:
        raise EnsembleError("every best_of candidate failed to produce a mesh")

    def gate(indices, key, factor, floor, label):
        """Reject candidates far worse than the best in THIS batch.

        The limit is anchored on ``min(batch)``, so the best candidate always
        survives and the gate can never empty the field - a picker that can
        refuse to pick is worse than one that picks the least bad.  It can
        legitimately cut to a single survivor, and when it does the report says
        there was no consensus left to take.
        """
        values = [(candidates[i].get("metrics") or {}).get(key) for i in indices]
        usable = [v for v in values if isinstance(v, (int, float))]
        if len(usable) < 2:
            return indices
        limit = max(float(min(usable)) * factor, float(floor))
        kept = [i for i, v in zip(indices, values)
                if not isinstance(v, (int, float)) or v <= limit]
        for i, v in zip(indices, values):
            if i not in kept:
                report["rejected"].append({
                    "seed": int(candidates[i]["seed"]),
                    "reason": f"{label} {v} is over the batch limit of {limit:g} "
                              f"({factor:g}x the best in this batch, floor {floor})"})
        return kept

    alive = gate(alive, "self_intersections", GATE_SELF_INTERSECTION_FACTOR,
                 GATE_SELF_INTERSECTION_FLOOR, "self-intersections")
    alive = gate(alive, "components", GATE_SHELL_FACTOR, GATE_SHELL_FLOOR,
                 "shell count")

    points = [candidates[i].get("points") for i in alive]
    consensus = None
    if len(alive) > 1 and all(p is not None for p in points):
        matrix = chamfer_matrix(points)
        consensus = chamfer_consensus(matrix)
        report["chamfer"] = [[float(f"{v:.6g}") for v in row] for row in matrix]
        for position, i in enumerate(alive):
            report["candidates"][i]["chamfer_consensus"] = float(
                f"{consensus[position]:.6g}")

    def key(position):
        i = alive[position]
        metrics = candidates[i].get("metrics") or {}
        iou = metrics.get("silhouette_iou")
        iou = 0.0 if iou is None else round(float(iou), SILHOUETTE_RANK_DECIMALS)
        chamfer = 0.0 if consensus is None else float(consensus[position])
        sharp = metrics.get("edge_sharpness") or 0.0
        return (-iou, chamfer, -float(sharp), int(candidates[i]["seed"]))

    order = sorted(range(len(alive)), key=key)
    winner = alive[order[0]]
    metrics = candidates[winner].get("metrics") or {}

    report["compared_seeds"] = [int(candidates[i]["seed"]) for i in alive]
    report["winner"] = {
        "seed": int(candidates[winner]["seed"]),
        "mesh_path": str(candidates[winner].get("mesh_path") or ""),
        "silhouette_iou": _round(metrics.get("silhouette_iou")),
        "chamfer_consensus": (None if consensus is None
                              else float(f"{consensus[order[0]]:.6g}")),
    }
    if consensus is not None and len(alive) > 1:
        report["why"] = (
            f"seed {candidates[winner]['seed']} survived the topology gates and is "
            f"the Chamfer medoid of the {len(alive)} survivors (mean symmetric "
            f"distance {consensus[order[0]]:.4g} to the others, the lowest), with "
            f"silhouette IoU {metrics.get('silhouette_iou', 0):.4f}.")
    else:
        report["why"] = (
            f"seed {candidates[winner]['seed']} was the only candidate left after "
            "the topology gates, so there was nothing to take a consensus over.")
    return report


def _round(value, places=4):
    return None if value is None else round(float(value), places)
